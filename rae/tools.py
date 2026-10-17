"""Tools the agents call. Each call is logged as evidence (E-01, E-02, ...), and every claim in a report
must cite one of these IDs."""
from __future__ import annotations

import concurrent.futures as cf
import re
import time
from datetime import date

from . import analytics as A
from .data.generator import END, PlantData

_BQ_POOL = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="bqml")
BQML_WAIT_S = 75.0  # the longest a run waits for BigQuery ML before going on with the local test alone


class EvidenceLog:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.listener = None  # called with each new item (used to stream evidence live)

    def add(self, tool: str, args: dict, data: dict, summary: str, source: str) -> dict:
        item = {"id": f"E-{len(self.items) + 1:02d}", "tool": tool, "args": args, "summary": summary,
                "source": source, "data": data}
        self.items.append(item)
        if self.listener:
            try:
                self.listener(item)
            except Exception:  # noqa: BLE001  (streaming is best-effort)
                pass
        return item

    def get(self, eid: str) -> dict | None:
        return next((i for i in self.items if i["id"] == eid), None)

    def ids(self) -> set[str]:
        return {i["id"] for i in self.items}

    def percentages(self) -> list[float]:
        """Every percentage quoted in evidence (the report may only quote these)."""
        return [float(m.group(1)) for i in self.items for m in re.finditer(r"(\d+(?:\.\d+)?)\s?%", i["summary"])]

    def lot_counts(self) -> list[int]:
        """Every lot count quoted in evidence."""
        return [int(m.group(1).replace(",", "")) for i in self.items
                for m in re.finditer(r"(\d[\d,]*)\s+lots?\b", i["summary"], re.I)]

    def public(self) -> list[dict]:
        return [public_item(i) for i in self.items]


def public_item(i: dict) -> dict:
    return {k: v for k, v in i.items() if k != "data"} | {"data": _trim(i["data"])}


def _trim(d):
    """Keep evidence payloads small for the UI and Firestore."""
    if isinstance(d, dict):
        return {k: _trim(v) for k, v in d.items() if k not in ("top_segments_full",)}
    if isinstance(d, list):
        return [_trim(x) for x in d[:60]]
    return d


def _p(x: float) -> str:
    return A.pct(x, 2)


class Toolbox:
    """Bound to one case and one dataset. Methods return compact dicts that are safe to show to the model."""

    def __init__(self, plant: PlantData, case: dict, log: EvidenceLog, contribution_engine=None):
        self.plant, self.case, self.log = plant, case, log
        self.defect = case["defect"]
        self.start = date.fromisoformat(case["date_code_start"][:10])
        self.end = date.fromisoformat(case["date_code_end"][:10])
        self.contribution_engine = contribution_engine  # optional BigQuery ML engine
        self.last_is_is_not: dict | None = None
        self._bq_future: cf.Future | None = None
        self._bq_deadline = 0.0

    def prefetch_contribution(self) -> None:
        """Start BigQuery ML at the beginning of the run, because it takes tens of seconds. It then overlaps with
        intake instead of blocking the Investigator's first tool call. It runs at most once per run."""
        if self.contribution_engine is not None and self._bq_future is None:
            self._bq_future = _BQ_POOL.submit(self.contribution_engine, self.defect, self.start, self.end)
            self._bq_deadline = time.monotonic() + BQML_WAIT_S

    def _contribution(self):
        self.prefetch_contribution()
        return self._bq_future.result(timeout=max(0.0, self._bq_deadline - time.monotonic()))

    def _add(self, tool: str, args: dict, data: dict, summary: str, source: str) -> dict:
        """Log a tool result. Sources name BigQuery only when the data really came from BigQuery."""
        if (self.plant.meta or {}).get("source") != "bigquery":
            source = source.replace("BigQuery tables", "local synthetic tables").replace("BigQuery table", "local synthetic table")
        return self.log.add(tool, args, data, summary, source)

    # ---- tools -----------------------------------------------------------------------------------------
    def is_is_not(self) -> dict:
        r = A.is_is_not(self.plant.production, self.defect, self.start, self.end)
        source = "Python two-proportion test over BigQuery table plant_quality.production_lots"
        if self.contribution_engine is not None:
            try:
                bq = self._contribution()
                if bq:
                    r["bqml_insights"] = bq
                    r["method"] = "BigQuery ML contribution analysis + two-proportion test"
                    source = "BigQuery ML CONTRIBUTION_ANALYSIS (ML.GET_INSIGHTS) on plant_quality.production_lots"
            except cf.TimeoutError:
                r["bqml_error"] = f"BigQuery ML did not answer within {BQML_WAIT_S:.0f} s; the local test was used."
            except Exception as exc:  # BigQuery ML is optional: the local engine always runs
                r["bqml_error"] = f"{type(exc).__name__}: {str(exc)[:280]}"
        self.last_is_is_not = r
        o = r["overall"]
        if r["significant"]:
            top = next(s for s in r["top_segments"] if s["filters"] == r["primary_segment"]) if any(
                s["filters"] == r["primary_segment"] for s in r["top_segments"]) else r["significant_singles"][0]
            summary = (f"{self.defect} rate {_p(o['rate_test'])} in date codes {self.start}–{self.end} vs "
                       f"{_p(o['rate_control'])} in the prior 28 days. Strongest segment: {top['label']} at "
                       f"{_p(top['rate_test'])} vs {_p(top['rate_control'])} before (×{top['ratio']}, z {top['z']}).")
        else:
            summary = (f"{self.defect} rate {_p(o['rate_test'])} in date codes {self.start}–{self.end} vs "
                       f"{_p(o['rate_control'])} before (×{o['ratio']}). No segment passes the significance test.")
        item = self._add("is_is_not", {"defect": self.defect, "test": [str(self.start), str(self.end)]}, r, summary, source)
        return {"evidence_id": item["id"], "summary": summary, "significant": r["significant"],
                "primary_segment": r["primary_segment"],
                "significant_segments": [{"segment": s["label"], "rate_test": _p(s["rate_test"]),
                                          "rate_control": _p(s["rate_control"]), "ratio": s["ratio"], "z": s["z"]}
                                         for s in r["significant_singles"][:6]]}

    def stratify(self, by: str, within: str) -> dict:
        if by not in A.DIMS or within not in A.DIMS or by == within:
            raise ValueError(f"by/within must be two different values from {A.DIMS}")
        cached = next((i for i in self.log.items if i["tool"] == "stratify" and i["args"] == {"by": by, "within": within}), None)
        if cached:  # the same split twice adds no new evidence
            return {"evidence_id": cached["id"], "summary": cached["summary"], "confounded": cached["data"]["confounded"],
                    "effect_persists_within": cached["data"]["effect_persists_within"]}
        r = A.stratify(self.plant.production, self.defect, by, within, self.start, self.end)
        oc = r["overall_contrast"] or {}
        if r["confounded"]:
            verdict = f"the difference disappears inside every {within}: confounded"
        elif r["effect_persists_within"]:
            verdict = f"the difference persists inside {', '.join(r['persisting_strata'])}"
        else:
            verdict = f"no {by} stands out: all {by}s moved together"
        summary = (f"In the complaint window, {by} {oc.get('high')} vs {oc.get('low')}: ×{oc.get('ratio')} (z {oc.get('z')}); "
                   f"split by {within}, {verdict}.")
        item = self._add("stratify", {"by": by, "within": within}, r, summary,
                            "Python over BigQuery table plant_quality.production_lots")
        return {"evidence_id": item["id"], "summary": summary, "confounded": r["confounded"],
                "effect_persists_within": r["effect_persists_within"]}

    def param_excursions(self, press: str) -> dict:
        if press not in self.plant.production.press.unique():
            raise ValueError(f"unknown press {press}")
        r = A.param_excursions(self.plant.process, press, self.start, self.end)
        parts = []
        for p in r["params"]:
            if p["flag"]:
                parts.append(f"{p['label']} averaged {p['test_mean']} vs normal {p['control_mean']} ± {p['control_sd']} "
                             f"({p['shift_sigma']}σ), out of limits from {p['first_out']} to {p['last_out']}")
        if r["pm"]["flag"]:
            parts.append(f"mould ran past the {r['pm']['limit']:,}-shot maintenance limit from {r['pm']['first_over_limit']} "
                         f"(max {r['pm']['max_shots']:,} shots)")
        summary = f"Press {press}: " + ("; ".join(parts) if parts else "no process parameter moved outside its normal range.")
        item = self._add("param_excursions", {"press": press}, r, summary,
                            "Python over BigQuery table plant_quality.process_params")
        return {"evidence_id": item["id"], "summary": summary, "flagged": r["flagged"]}

    def inspection_changes(self, press: str) -> dict:
        r = A.inspection_changes(self.plant.inspection, press, self.start, self.end)
        parts = [f"sampling cut from {c['sampling_from']}/h to {c['sampling_to']}/h on {c['date']} "
                 f"(control plan rev {c['rev_from']} → {c['rev_to']})" for c in r["sampling_changes"]]
        if r["gauge_overdue"]:
            g = r["gauge_overdue"]
            parts.append(f"gauge {g['gauge_id']} overdue for calibration since {g['cal_due']} "
                         f"({g['days_overdue_at_end']} days by the end of the window)")
        summary = f"Press {press} inspection: " + ("; ".join(parts) if parts else "no change to sampling or gauges.")
        item = self._add("inspection_changes", {"press": press}, r, summary,
                            "Python over BigQuery table plant_quality.inspection_log")
        return {"evidence_id": item["id"], "summary": summary, "detection_weakened": r["detection_weakened"]}

    def containment(self, segment: dict) -> dict:
        segment = {k: v for k, v in (segment or {}).items() if k in A.DIMS}
        if not segment:
            raise ValueError("containment needs a segment such as {'press': 'P4'}")
        r = A.containment(self.plant.production, self.plant.shipments, self.defect, segment, self.start, self.end, END)
        st = r["by_status"]
        summary = (f"Hold {r['targeted_lots']} lots of {r['segment_label']} made {r['suspect_window'][0]} to "
                   f"{r['suspect_window'][1]} ({st.get('at_customer', 0)} at customer, {st.get('in_transit', 0)} in transit, "
                   f"{st.get('in_plant', 0)} in plant; {r['targeted_parts']:,} parts), instead of a blanket hold of "
                   f"{r['blanket_lots']} lots ({r['blanket_parts']:,} parts).")
        item = self._add("containment", {"segment": segment}, r, summary,
                            "Python over BigQuery tables production_lots + shipments")
        return {"evidence_id": item["id"], "summary": summary, "targeted_lots": r["targeted_lots"],
                "blanket_lots": r["blanket_lots"]}

    def past_8d(self, keywords: list[str] | None = None) -> dict:
        r = A.past_8d_lookup(self.plant.past_8d, self.defect, keywords)
        summary = ("Similar past 8Ds: " + "; ".join(f"{m['case_id']}: {m['lesson']}" for m in r["matches"])
                   if r["matches"] else "No similar past 8D found.")
        item = self._add("past_8d", {"keywords": keywords or []}, r, summary, "BigQuery table plant_quality.past_8d")
        return {"evidence_id": item["id"], "summary": summary}
