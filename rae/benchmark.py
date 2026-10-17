"""14-case benchmark: 12 planted causes (8 strong, 4 weak) + 2 controls with no cause. Seeds were not tuned.

Scoring
- Caused cases: the primary segment found must cover the same rows as the planted segment in the complaint
  window (Jaccard overlap >= 0.8). Containment recall is the share of truly affected lots inside the hold list.
- Control cases: the analysis must abstain ("insufficient evidence").
"""
from __future__ import annotations

import json
from datetime import date

from . import analytics as A
from .data.generator import END, Planted, generate

CASES = [
    ("B01", "crack", {"press": "P3"}, "2026-10-01", "2026-10-08", 0.035),
    ("B02", "burr", {"press": "P5", "cavity": "C2"}, "2026-09-25", "2026-10-06", 0.03),
    ("B03", "short_shot", {"press": "P1"}, "2026-10-03", "2026-10-10", 0.03),
    ("B04", "scratch", {"shift": "N"}, "2026-10-04", "2026-10-12", 0.02),
    ("B05", "crack", {"press": "P6", "cavity": "C1"}, "2026-09-30", "2026-10-09", 0.04),
    ("B06", "short_shot", {"cavity": "C4"}, "2026-10-02", "2026-10-11", 0.015),
    ("B07", "burr", {"press": "P3", "cavity": "C4"}, "2026-10-05", "2026-10-13", 0.025),
    ("B08", "scratch", {"press": "P2"}, "2026-10-06", "2026-10-12", 0.02),
    ("B09", "crack", None, "2026-10-06", "2026-10-12", None),
    ("B10", "short_shot", None, "2026-09-29", "2026-10-06", None),
    # harder: weaker signals (3-7x baseline) and small segments
    ("B11", "burr", {"press": "P4"}, "2026-10-02", "2026-10-09", 0.008),
    ("B12", "crack", {"cavity": "C2"}, "2026-09-26", "2026-10-05", 0.010),
    ("B13", "short_shot", {"press": "P6", "shift": "S"}, "2026-10-01", "2026-10-10", 0.015),
    ("B14", "scratch", {"press": "P1", "cavity": "C4"}, "2026-10-03", "2026-10-12", 0.012),
]


def _rows(df, filters):
    m = None
    for k, v in (filters or {}).items():
        x = df[k] == v
        m = x if m is None else (m & x)
    return set(df[m].index) if m is not None else set()


def run(seed_base: int = 100) -> dict:
    results = []
    for i, (cid, defect, filters, s, e, rate) in enumerate(CASES):
        start, end = date.fromisoformat(s), date.fromisoformat(e)
        extra = [Planted(defect, filters, start, end, rate)] if filters else []
        plant = generate(seed=seed_base + i, planted_story=False, extra=extra)
        r = A.is_is_not(plant.production, defect, start, end)
        row = {"case": cid, "defect": defect, "planted": A.seg_label(filters) if filters else "none",
               "found": r["primary_label"] or "insufficient evidence"}
        if filters is None:
            row["pass"] = not r["significant"]
            row["containment_recall"] = None
        else:
            test = plant.production[(plant.production.prod_date >= start) & (plant.production.prod_date <= end)]
            truth, found = _rows(test, filters), _rows(test, r["primary_segment"] or {})
            jac = len(truth & found) / len(truth | found) if (truth | found) else 0.0
            row["overlap"] = round(jac, 2)
            row["pass"] = bool(r["significant"] and jac >= 0.8)
            if r["significant"]:
                c = A.containment(plant.production, plant.shipments, defect, r["primary_segment"], start, end, END)
                held = {l["lot_id"] for l in c["lots"]}
                lot_filters = {k: v for k, v in filters.items() if k in ("press", "shift")}
                truth_lots = plant.shipments[(plant.shipments.prod_date >= start) & (plant.shipments.prod_date <= end)]
                for k, v in lot_filters.items():
                    truth_lots = truth_lots[truth_lots[k] == v]
                tl = set(truth_lots.lot_id)
                row["containment_recall"] = round(len(tl & held) / len(tl), 2) if tl else None
                row["held_lots"] = c["targeted_lots"]
                row["blanket_lots"] = c["blanket_lots"]
            else:
                row["containment_recall"] = 0.0
        results.append(row)
    caused = [r for r in results if r["planted"] != "none"]
    controls = [r for r in results if r["planted"] == "none"]
    return {
        "cases": results,
        "caused_found": sum(r["pass"] for r in caused), "caused_total": len(caused),
        "controls_abstained": sum(r["pass"] for r in controls), "controls_total": len(controls),
        "mean_containment_recall": round(sum(r["containment_recall"] or 0 for r in caused) / len(caused), 2),
        "note": "Synthetic data. Deterministic analysis layer only; Gemini runs are scored separately in the live app.",
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path
    out = json.dumps(run(), indent=2, default=str)
    if "--write" in sys.argv:
        (Path(__file__).resolve().parent / "data" / "benchmark.json").write_text(out + chr(10), encoding="utf-8")
    print(out)
