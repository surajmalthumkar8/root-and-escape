"""BigQuery ML contribution analysis: the 8D is / is-not analysis done in SQL.

This uses the same session pattern as ADK's own `analyze_contribution` tool (CREATE TEMP MODEL inside a
BigQuery session), but with fixed arguments: the model never writes SQL. Results are cached per window,
because CREATE MODEL bills at 50x the bytes scanned (about $0.003 per run on this dataset).
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import date, timedelta
from functools import lru_cache

from .config import settings

DEFECTS = {"crack", "burr", "scratch", "short_shot"}
DIMS = ["press", "cavity", "shift", "crew", "resin_lot"]
PART_RE = re.compile(r"^[A-Z]{2}-\d{2,3}$")
LAST: dict = {}  # the latest contribution analysis this instance ran, shown on the live service check


@lru_cache(maxsize=64)
def _run(defect: str, start: str, end: str, part: str = "CL-204", control_days: int = 28) -> tuple:
    from google.cloud import bigquery

    if defect not in DEFECTS:
        raise ValueError("unknown defect type")
    if not PART_RE.match(part):
        raise ValueError("unknown part number")
    t0 = time.monotonic()
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    cstart = s - timedelta(days=control_days)
    col = f"defects_{defect}"
    table = f"`{settings.project}.{settings.dataset}.production_lots`"
    client = bigquery.Client(project=settings.project, location=settings.bq_location)
    sess = client.query("SELECT 1", job_config=bigquery.QueryJobConfig(dry_run=True, create_session=True), timeout=30)
    sid = sess.session_info.session_id
    cfg = bigquery.QueryJobConfig(connection_properties=[bigquery.ConnectionProperty("session_id", sid)],
                                  maximum_bytes_billed=500 * 1024 * 1024, job_timeout_ms=60_000)
    name = f"ca_{uuid.uuid4().hex}"
    dims = ", ".join(f"'{d}'" for d in DIMS)
    job = client.query(f"""
CREATE TEMP MODEL {name}
OPTIONS (MODEL_TYPE = 'CONTRIBUTION_ANALYSIS',
         CONTRIBUTION_METRIC = 'SUM({col})/SUM(units)',
         IS_TEST_COL = 'is_test',
         DIMENSION_ID_COLS = [{dims}],
         MIN_APRIORI_SUPPORT = 0.05,
         PRUNING_METHOD = 'PRUNE_REDUNDANT_INSIGHTS')
AS SELECT {', '.join(DIMS)}, {col}, units, (prod_date >= DATE '{s.isoformat()}') AS is_test
   FROM {table}
   WHERE prod_date BETWEEN DATE '{cstart.isoformat()}' AND DATE '{e.isoformat()}' AND part_no = '{part}'""",
                       job_config=cfg)
    job.result(timeout=70)
    rows = client.query(f"SELECT * FROM ML.GET_INSIGHTS(MODEL {name}) ORDER BY contribution DESC LIMIT 12",
                        job_config=cfg).result(timeout=40)
    out = []
    for r in rows:
        d = dict(r.items())
        contributors = d.get("contributors") or []
        out.append({
            "segment": ", ".join(contributors) if contributors else "all",
            "metric_test": d.get("metric_test"), "metric_control": d.get("metric_control"),
            "test_over_control": d.get("metric_test_over_metric_control"),
            "test_over_complement": d.get("metric_test_over_complement"),
            "attribution": d.get("aumann_shapley_attribution"),
            "apriori_support": d.get("apriori_support"), "contribution": d.get("contribution"),
        })
    LAST.update(defect=defect, part=part, window=[start, end], insights=len(out), job_id=job.job_id,
                seconds=round(time.monotonic() - t0, 1), at=time.time())
    try:
        client.query("CALL BQ.ABORT_SESSION()", job_config=cfg).result(timeout=10)
    except Exception:  # noqa: BLE001  (the session expires on its own)
        pass
    return tuple(out)


def contribution_analysis(defect: str, start: date, end: date, part: str = "CL-204") -> list[dict]:
    return list(_run(defect, start.isoformat(), end.isoformat(), part))
