"""Storage. Two interchangeable backends:
- local: JSON files under ./var. Used for development and tests; no cloud needed.
- gcp: BigQuery holds the plant data, Firestore the cases, results, approvals and quotas, and
  Cloud Storage the photos.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .config import settings
from .data.generator import PlantData, generate_plant, past_8d_table

_lock = threading.Lock()


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


# ------------------------------------------------------------------------------------------------ plant data
_plant_cache: dict = {}
_plant_lock = threading.Lock()


def plant_source_label() -> str:
    if settings.gcp:
        return f"BigQuery dataset {settings.dataset} (synthetic plant data, seed 42)"
    return "local generator (synthetic plant data, seed 42)"


def load_plant() -> tuple[PlantData, str]:
    """Return (plant data, human-readable source). Cached per instance; the data is small (~4k rows).
    Blocking: call it from a worker thread. The lock makes concurrent first requests share one BigQuery read."""
    if "plant" in _plant_cache:
        return _plant_cache["plant"], _plant_cache["source"]
    with _plant_lock:
        if "plant" not in _plant_cache:
            _plant_cache.update(plant=_read_plant(), source=plant_source_label())
    return _plant_cache["plant"], _plant_cache["source"]


def _read_plant() -> PlantData:
    if settings.gcp:
        from google.cloud import bigquery  # lazy import keeps cold starts fast in local mode

        client = bigquery.Client(project=settings.project, location=settings.bq_location)
        ds = f"{settings.project}.{settings.dataset}"

        def q(table: str) -> pd.DataFrame:
            return client.query(f"SELECT * FROM `{ds}.{table}`").to_dataframe(create_bqstorage_client=False)

        prod, proc, insp, ship = q("production_lots"), q("process_params"), q("inspection_log"), q("shipments")
        past = q("past_8d")
        for df, cols in ((prod, ["prod_date"]), (insp, ["insp_date", "gauge_cal_due"]), (ship, ["prod_date"])):
            for c in cols:
                df[c] = pd.to_datetime(df[c]).dt.date
        prod["prod_ts"] = pd.to_datetime(prod["prod_ts"]).dt.tz_localize(None)
        proc["prod_ts"] = pd.to_datetime(proc["prod_ts"]).dt.tz_localize(None)
        return PlantData(prod, proc, insp, ship, past if len(past) else past_8d_table(),
                         meta={"synthetic": True, "source": "bigquery"})
    return generate_plant(42)


def result_summary(result: dict | None) -> dict | None:
    """The few fields the case list needs, so listing cases never downloads whole results."""
    if not result:
        return None
    return {"verdict": result.get("verdict"), "modes": result.get("modes"),
            "insufficient": bool((result.get("draft") or {}).get("insufficient_evidence"))}


# ------------------------------------------------------------------------------------------------ cases / results
class LocalStore:
    def __init__(self, root: str):
        self.root = Path(root)
        (self.root / "results").mkdir(parents=True, exist_ok=True)
        (self.root / "cases").mkdir(parents=True, exist_ok=True)
        (self.root / "photos").mkdir(parents=True, exist_ok=True)
        self.meta = self.root / "meta.json"

    def _read(self, p: Path, default):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write(self, p: Path, data) -> None:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(p)

    def save_case(self, case: dict) -> None:
        self._write(self.root / "cases" / f"{case['case_id']}.json", case)

    def get_case(self, case_id: str) -> dict | None:
        return self._read(self.root / "cases" / f"{case_id}.json", None)

    def list_user_cases(self, visitor: str) -> list[dict]:
        out = []
        for p in (self.root / "cases").glob("*.json"):
            c = self._read(p, None)
            if c and c.get("visitor") == visitor:
                out.append(c)
        return sorted(out, key=lambda c: c.get("received", ""), reverse=True)

    def save_result(self, case_id: str, result: dict) -> None:
        self._write(self.root / "results" / f"{case_id}.json", result)

    def get_result(self, case_id: str) -> dict | None:
        return self._read(self.root / "results" / f"{case_id}.json", None)

    def summaries(self, case_ids: list[str]) -> dict:
        return {cid: result_summary(self.get_result(cid)) for cid in case_ids}

    def approvals(self, visitor: str, case_id: str) -> dict:
        return self._read(self.meta, {}).get("approvals", {}).get(visitor, {}).get(case_id, {})

    def approve(self, visitor: str, case_id: str, section: str, record: dict) -> dict:
        with _lock:
            m = self._read(self.meta, {})
            m.setdefault("approvals", {}).setdefault(visitor, {}).setdefault(case_id, {})[section] = record
            self._write(self.meta, m)
            return m["approvals"][visitor][case_id]

    def reset_approvals(self, visitor: str, case_id: str) -> None:
        with _lock:
            m = self._read(self.meta, {})
            m.get("approvals", {}).get(visitor, {}).pop(case_id, None)
            self._write(self.meta, m)

    def take_quota(self, visitor: str, kind: str = "gemini") -> tuple[bool, dict]:
        """kind='gemini' (costs money, tight cap) or 'det' (deterministic runs, loose cap against abuse)."""
        day_key = utc_day() if kind == "gemini" else f"{utc_day()}-{kind}"
        lim_total, lim_v = _limits(kind)
        with _lock:
            m = self._read(self.meta, {})
            q = m.setdefault("quota", {}).setdefault(day_key, {"total": 0, "visitors": {}})
            used_v = q["visitors"].get(visitor, 0)
            ok = q["total"] < lim_total and used_v < lim_v
            if ok:
                q["total"] += 1
                q["visitors"][visitor] = used_v + 1
                self._write(self.meta, m)
            gq = m["quota"].get(utc_day(), {"total": 0, "visitors": {}})
            return ok, self._quota_view(gq, visitor)

    def quota(self, visitor: str) -> dict:
        q = self._read(self.meta, {}).get("quota", {}).get(utc_day(), {"total": 0, "visitors": {}})
        return self._quota_view(q, visitor)

    @staticmethod
    def _quota_view(q: dict, visitor: str) -> dict:
        return {"day": utc_day(), "used_today": q["total"], "limit_today": settings.live_runs_per_day,
                "used_by_you": q["visitors"].get(visitor, 0), "limit_per_visitor": settings.runs_per_visitor_per_day}

    def put_photo(self, name: str, data: bytes, content_type: str) -> str:
        (self.root / "photos" / name).write_bytes(data)
        return name

    def get_photo(self, name: str) -> tuple[bytes, str] | None:
        p = self.root / "photos" / name
        if not p.exists():
            return None
        return p.read_bytes(), "image/jpeg" if name.endswith(".jpg") else "image/png"


class GcpStore(LocalStore):
    """Firestore (cases, results, approvals, quotas) + Cloud Storage (photos)."""

    def __init__(self):
        from google.cloud import firestore, storage

        self.db = firestore.Client(project=settings.project)
        self.fs = firestore
        self.bucket = storage.Client(project=settings.project).bucket(settings.bucket)

    def save_case(self, case):
        self.db.collection("cases").document(case["case_id"]).set(case)

    def get_case(self, case_id):
        d = self.db.collection("cases").document(case_id).get()
        return d.to_dict() if d.exists else None

    def list_user_cases(self, visitor):
        docs = self.db.collection("cases").where("visitor", "==", visitor).limit(50).stream()
        return sorted([d.to_dict() for d in docs], key=lambda c: c.get("received", ""), reverse=True)

    def save_result(self, case_id, result):
        # Firestore documents max out at 1 MiB; results are ~100-300 KB, stored as a JSON string to keep keys flexible.
        doc = {"json": json.dumps(result, ensure_ascii=False, default=str), "saved": time.time(),
               "summary": result_summary(result)}
        if "__" in case_id:  # per-visitor runs expire (Firestore TTL policy on `expires_at`, set by deploy.sh)
            doc["expires_at"] = datetime.now(timezone.utc) + timedelta(days=21)
        self.db.collection("results").document(case_id).set(doc)

    def get_result(self, case_id):
        d = self.db.collection("results").document(case_id).get()
        return json.loads(d.to_dict()["json"]) if d.exists else None

    def summaries(self, case_ids):
        refs = [self.db.collection("results").document(c) for c in case_ids]
        out = {c: None for c in case_ids}
        for snap in self.db.get_all(refs, field_paths=["summary"]):  # one round trip, summary field only
            if snap.exists:
                out[snap.id] = (snap.to_dict() or {}).get("summary")
        return out

    def _appr_ref(self, visitor, case_id):
        return self.db.collection("approvals").document(f"{visitor}__{case_id}")

    def approvals(self, visitor, case_id):
        d = self._appr_ref(visitor, case_id).get()
        return d.to_dict() if d.exists else {}

    def approve(self, visitor, case_id, section, record):
        ref = self._appr_ref(visitor, case_id)
        ref.set({section: record}, merge=True)
        return ref.get().to_dict()

    def reset_approvals(self, visitor, case_id):
        self._appr_ref(visitor, case_id).delete()

    def take_quota(self, visitor, kind="gemini"):
        """Lock-free: count the run first, then check, and give the slot back if it went over the cap.
        It can never grant more than the cap (every granted run stays counted) and never waits on a lock.
        A transaction was tried first; on the Firestore emulator it hit lock timeouts with only 3 runs at once."""
        day_key = utc_day() if kind == "gemini" else f"{utc_day()}-{kind}"
        lim_total, lim_v = _limits(kind)
        ref = self.db.collection("quota").document(day_key)
        inc = self.fs.Increment
        ref.set({"total": inc(1), "visitors": {visitor: inc(1)}}, merge=True)
        q = ref.get().to_dict() or {}
        q.setdefault("visitors", {})
        used_v = q["visitors"].get(visitor, 0)
        ok = q.get("total", 0) <= lim_total and used_v <= lim_v
        if not ok:
            ref.set({"total": inc(-1), "visitors": {visitor: inc(-1)}}, merge=True)
            q["total"] = q.get("total", 1) - 1
            q["visitors"][visitor] = used_v - 1
        return ok, (self._quota_view(q, visitor) if kind == "gemini" else self.quota(visitor))

    def quota(self, visitor):
        d = self.db.collection("quota").document(utc_day()).get()
        q = d.to_dict() if d.exists else {"total": 0, "visitors": {}}
        return self._quota_view(q, visitor)

    def put_photo(self, name, data, content_type):
        blob = self.bucket.blob(f"photos/{name}")
        blob.upload_from_string(data, content_type=content_type)
        return name

    def get_photo(self, name):
        blob = self.bucket.blob(f"photos/{name}")
        if not blob.exists():
            return None
        return blob.download_as_bytes(), blob.content_type or "image/jpeg"


def _limits(kind: str) -> tuple[int, int]:
    if kind == "gemini":
        return settings.live_runs_per_day, settings.runs_per_visitor_per_day
    if kind == "critic":  # one short Gemini call per "Try to break it" claim that passes the code rules
        return settings.critic_checks_per_day, settings.critic_checks_per_visitor_per_day
    return settings.det_runs_per_day, settings.det_runs_per_visitor_per_day


_store = None


def get_store():
    global _store
    if _store is None:
        _store = GcpStore() if settings.gcp else LocalStore(settings.local_dir)
    return _store
