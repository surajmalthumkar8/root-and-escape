"""Deterministic investigation tools.

Every number that appears in a Root & Escape report comes from one of these functions, never from the
language model. Gemini decides which tools to call and writes the report; this module computes the facts.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from itertools import combinations

import pandas as pd

DIMS = ["press", "cavity", "shift", "crew", "resin_lot"]
PEOPLE_DIMS = {"crew", "shift"}
PARAMS = {
    "melt_temp_c": "melt temperature (°C)",
    "hold_pressure_bar": "hold pressure (bar)",
    "cycle_time_s": "cycle time (s)",
    "dryer_dew_point_c": "resin dryer dew point (°C)",
}
PM_LIMIT_SHOTS = 250_000

# significance thresholds (documented in the README "Guardrails" section)
Z_MIN = 3.5          # two-proportion z score
RATIO_MIN = 2.5      # test rate / control rate
EXCESS_MIN = 10      # extra defects vs control baseline
MIN_SUPPORT = 0.04   # share of test+control units in the segment
MIN_CELL_UNITS = 1_500


def _rate(d: float, n: float) -> float:
    return float(d) / float(n) if n else 0.0


def _z(d1: float, n1: float, d2: float, n2: float) -> float:
    if not n1 or not n2:
        return 0.0
    p = (d1 + d2) / (n1 + n2)
    if p <= 0 or p >= 1:
        return 0.0
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return ((d1 / n1) - (d2 / n2)) / se if se else 0.0


def pct(x: float, nd: int = 2) -> str:
    return f"{x * 100:.{nd}f}%"


def split_test_control(prod: pd.DataFrame, start: date, end: date, control_days: int = 28):
    test = prod[(prod.prod_date >= start) & (prod.prod_date <= end)]
    cstart = start - timedelta(days=control_days)
    control = prod[(prod.prod_date >= cstart) & (prod.prod_date < start)]
    return test, control, cstart


def _filter(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    for k, v in filters.items():
        df = df[df[k] == v]
    return df


def seg_label(filters: dict | None) -> str:
    if not filters:
        return "whole plant"
    return " × ".join(f"{k} {v}" for k, v in filters.items())


DIM_JA = {"press": "プレス", "cavity": "キャビティ", "shift": "直", "crew": "班", "resin_lot": "樹脂ロット"}


def seg_label_ja(filters: dict | None) -> str:
    if not filters:
        return "工場全体"
    return "×".join(f"{DIM_JA.get(k, k)} {v}" for k, v in filters.items())


def _seg(test, control, col, filters, base_c, n_all):
    t, c = _filter(test, filters), _filter(control, filters)
    dt, nt = int(t[col].sum()), int(t.units.sum())
    dc, nc = int(c[col].sum()), int(c.units.sum())
    rt, rc = _rate(dt, nt), _rate(dc, nc)
    ref = rc if nc >= MIN_CELL_UNITS else base_c
    ratio = rt / ref if ref > 0 else (float("inf") if rt > 0 else 1.0)
    return {
        "filters": dict(filters), "label": seg_label(filters),
        "defects_test": dt, "units_test": nt, "rate_test": rt,
        "defects_control": dc, "units_control": nc, "rate_control": rc,
        "ratio": round(min(ratio, 999.0), 2), "z": round(_z(dt, nt, dc, nc), 2),
        "excess_defects": round(dt - nt * ref, 1),
        "support": round((nt + nc) / n_all, 4) if n_all else 0.0,
    }


def _significant(s: dict) -> bool:
    return s["z"] >= Z_MIN and s["ratio"] >= RATIO_MIN and s["excess_defects"] >= EXCESS_MIN


def is_is_not(prod: pd.DataFrame, defect: str, start: date, end: date, control_days: int = 28,
              dims: list[str] | None = None) -> dict:
    """8D "is / is-not": which segments explain the jump in defect rate (test window vs prior control window)."""
    dims = dims or DIMS
    col = f"defects_{defect}"
    test, control, cstart = split_test_control(prod, start, end, control_days)
    Dt, Nt, Dc, Nc = int(test[col].sum()), int(test.units.sum()), int(control[col].sum()), int(control.units.sum())
    base_c = _rate(Dc, Nc)
    n_all = Nt + Nc
    overall = {
        "defects_test": Dt, "units_test": Nt, "rate_test": _rate(Dt, Nt),
        "defects_control": Dc, "units_control": Nc, "rate_control": base_c,
        "ratio": round(_rate(Dt, Nt) / base_c, 2) if base_c else None,
        "z": round(_z(Dt, Nt, Dc, Nc), 2), "excess_defects": round(Dt - Nt * base_c, 1),
    }

    singles = []
    for dim in dims:
        for v in sorted(test[dim].unique()):
            s = _seg(test, control, col, {dim: v}, base_c, n_all)
            if s["support"] >= MIN_SUPPORT:
                s["significant"] = _significant(s)
                singles.append(s)
    pairs = []
    for d1, d2 in combinations(dims, 2):
        for v1 in sorted(test[d1].unique()):
            for v2 in sorted(test[test[d1] == v1][d2].unique()):
                s = _seg(test, control, col, {d1: v1, d2: v2}, base_c, n_all)
                if s["units_test"] >= MIN_CELL_UNITS:
                    s["significant"] = _significant(s)
                    pairs.append(s)

    sig_singles = sorted([s for s in singles if s["significant"]], key=lambda s: s["z"], reverse=True)
    primary, refined_by = None, None
    if sig_singles:
        top = sig_singles[0]
        primary = dict(top["filters"])
        # refine when the defect sits in one sub-segment of the top segment (e.g. one cavity of press 2)
        best = None
        (dim0, val0), = top["filters"].items()
        for dim in dims:
            if dim == dim0:
                continue
            subs = [p for p in pairs if p["filters"].get(dim0) == val0 and dim in p["filters"] and len(p["filters"]) == 2]
            subs = [p for p in subs if p["units_test"] >= MIN_CELL_UNITS]
            if len(subs) < 2:
                continue
            hi = max(subs, key=lambda p: p["rate_test"])
            lo = min(subs, key=lambda p: p["rate_test"])
            spread = hi["rate_test"] / max(lo["rate_test"], base_c, 1e-6)
            if spread >= 3 and hi["significant"] and (best is None or spread > best[0]):
                best = (spread, hi, dim)
        if best:
            primary, refined_by = dict(best[1]["filters"]), best[2]

    significant = primary is not None and (overall["ratio"] or 0) >= 1.3
    ranked = sorted(singles + pairs, key=lambda s: (s["significant"], s["excess_defects"]), reverse=True)
    heat_dims = (list(primary.keys()) if primary and len(primary) == 2 else
                 [list(primary.keys())[0], "shift" if list(primary.keys())[0] != "shift" else "press"] if primary
                 else ["press", "shift"])
    return {
        "defect": defect, "test_window": [start.isoformat(), end.isoformat()],
        "control_window": [cstart.isoformat(), (start - timedelta(days=1)).isoformat()],
        "overall": overall, "significant": bool(significant),
        "primary_segment": primary if significant else None, "refined_by": refined_by,
        "primary_label": seg_label(primary) if significant else None,
        "top_segments": ranked[:12],
        "significant_singles": sig_singles,
        "heatmap": heatmap(test, control, col, heat_dims[0], heat_dims[1], base_c),
        "method": "two-proportion z test on segments (local engine)",
    }


def heatmap(test, control, col, row_dim, col_dim, base_c) -> dict:
    rows = sorted(test[row_dim].unique())
    cols = sorted(test[col_dim].unique())
    cells = []
    for r in rows:
        line = []
        for c in cols:
            t = test[(test[row_dim] == r) & (test[col_dim] == c)]
            n = int(t.units.sum())
            line.append({"rate": _rate(int(t[col].sum()), n) if n else None, "units": n})
        cells.append(line)
    return {"row_dim": row_dim, "col_dim": col_dim, "rows": rows, "cols": cols, "cells": cells,
            "baseline_rate": base_c}


def stratify(prod: pd.DataFrame, defect: str, by: str, within: str, start: date, end: date) -> dict:
    """Does the effect of `by` (e.g. crew) survive inside each value of `within` (e.g. press)?"""
    col = f"defects_{defect}"
    test = prod[(prod.prod_date >= start) & (prod.prod_date <= end)]

    def cells(df):
        g = df.groupby(by).agg(d=(col, "sum"), n=("units", "sum")).reset_index()
        g = g[g.n >= 600]
        return [{by: str(r[by]), "defects": int(r.d), "units": int(r.n), "rate": _rate(r.d, r.n)} for _, r in g.iterrows()]

    def contrast(cs):
        if len(cs) < 2:
            return None
        hi = max(cs, key=lambda c: c["rate"])
        lo = min(cs, key=lambda c: c["rate"])
        return {"high": hi[by], "low": lo[by], "ratio": round(hi["rate"] / lo["rate"], 2) if lo["rate"] else None,
                "z": round(_z(hi["defects"], hi["units"], lo["defects"], lo["units"]), 2)}

    overall = cells(test)
    oc = contrast(overall)
    strata = []
    for w in sorted(test[within].unique()):
        cs = cells(test[test[within] == w])
        strata.append({within: str(w), "cells": cs, "contrast": contrast(cs)})
    persists = [s for s in strata if s["contrast"] and s["contrast"]["z"] >= 3 and (s["contrast"]["ratio"] or 0) >= 2]
    overall_effect = bool(oc and oc["z"] >= 3 and (oc["ratio"] or 0) >= 1.8)
    return {
        "defect": defect, "by": by, "within": within, "window": [start.isoformat(), end.isoformat()],
        "overall": overall, "overall_contrast": oc, "strata": strata,
        "overall_effect": overall_effect,
        "effect_persists_within": bool(persists),
        "confounded": bool(overall_effect and not persists),
        "persisting_strata": [s[within] for s in persists],
    }


def param_excursions(process: pd.DataFrame, press: str, start: date, end: date, control_days: int = 28) -> dict:
    p = process[process.press == press].copy()
    p["d"] = pd.to_datetime(p.prod_ts).dt.date
    test = p[(p.d >= start - timedelta(days=2)) & (p.d <= end)]
    ctrl = p[(p.d >= start - timedelta(days=control_days)) & (p.d < start - timedelta(days=2))]
    out = []
    for col, label in PARAMS.items():
        mu, sd = float(ctrl[col].mean()), max(float(ctrl[col].std()), 1e-3)
        z = (test[col] - mu) / sd
        bad = test[z.abs() > 3]
        shift = (float(test[col].mean()) - mu) / sd
        out.append({
            "param": col, "label": label, "control_mean": round(mu, 2), "control_sd": round(sd, 2),
            "test_mean": round(float(test[col].mean()), 2), "shift_sigma": round(shift, 1),
            "share_out_of_limits": round(len(bad) / max(len(test), 1), 2),
            "first_out": str(bad.prod_ts.min()) if len(bad) else None,
            "last_out": str(bad.prod_ts.max()) if len(bad) else None,
            "flag": bool(abs(shift) >= 3 or len(bad) / max(len(test), 1) >= 0.25),
        })
    pm = test[test.mold_shots_since_pm >= PM_LIMIT_SHOTS]
    pm_info = {
        "limit": PM_LIMIT_SHOTS, "max_shots": int(test.mold_shots_since_pm.max()) if len(test) else 0,
        "first_over_limit": str(pm.prod_ts.min()) if len(pm) else None, "flag": bool(len(pm)),
    }
    return {"press": press, "window": [start.isoformat(), end.isoformat()], "params": out, "pm": pm_info,
            "flagged": [o["param"] for o in out if o["flag"]] + (["mold_shots_since_pm"] if pm_info["flag"] else [])}


def inspection_changes(inspection: pd.DataFrame, press: str, start: date, end: date, lookback_days: int = 14) -> dict:
    ins = inspection[(inspection.press == press) & (inspection.insp_date >= start - timedelta(days=lookback_days))
                     & (inspection.insp_date <= end)].sort_values("insp_date")
    changes, prev = [], None
    for _, r in ins.iterrows():
        cur = (int(r.sampling_per_hour), str(r.control_plan_rev))
        if prev and cur != prev:
            changes.append({"date": r.insp_date.isoformat(), "sampling_from": prev[0], "sampling_to": cur[0],
                            "rev_from": prev[1], "rev_to": cur[1]})
        prev = cur
    overdue = ins[ins.gauge_cal_due < ins.insp_date]
    gauge = None
    if len(overdue):
        first = overdue.iloc[0]
        gauge = {"gauge_id": str(first.gauge_id), "cal_due": first.gauge_cal_due.isoformat(),
                 "days_overdue_at_end": (end - first.gauge_cal_due).days}
    return {"press": press, "window": [(start - timedelta(days=lookback_days)).isoformat(), end.isoformat()],
            "sampling_changes": changes, "gauge_overdue": gauge,
            "detection_weakened": bool(changes and any(c["sampling_to"] < c["sampling_from"] for c in changes)) or bool(gauge)}


def containment(prod: pd.DataFrame, shipments: pd.DataFrame, defect: str, filters: dict, start: date, end: date,
                last_day: date, control_days: int = 28) -> dict:
    """Targeted hold: lots in the confirmed segment over the days it was actually elevated.
    Blanket hold: every lot of the part from the first suspect day to the last production day."""
    col = f"defects_{defect}"
    seg = _filter(prod, filters)
    ctrl = seg[(seg.prod_date >= start - timedelta(days=control_days)) & (seg.prod_date < start - timedelta(days=7))]
    base = max(_rate(ctrl[col].sum(), ctrl.units.sum()), 0.001)
    daily = seg[seg.prod_date >= start - timedelta(days=10)].groupby("prod_date").agg(d=(col, "sum"), n=("units", "sum"))
    daily["rate"] = daily.d / daily.n
    hot = daily[(daily.n >= 300) & (daily.rate >= max(3 * base, 0.01))]
    ws = min([*hot.index, start]) if len(hot) else start
    we = max(hot.index) if len(hot) else end
    lot_cols = [k for k in filters if k in ("press", "shift")]
    lots = shipments[(shipments.prod_date >= ws) & (shipments.prod_date <= we)]
    for k in lot_cols:
        lots = lots[lots[k] == filters[k]]
    blanket = shipments[(shipments.prod_date >= ws) & (shipments.prod_date <= last_day)]
    by_status = lots.groupby("status").size().to_dict()
    units = prod.groupby("lot_id").units.sum()
    return {
        "segment": dict(filters), "segment_label": seg_label(filters), "segment_label_ja": seg_label_ja(filters),
        "suspect_window": [ws.isoformat(), we.isoformat()],
        "targeted_lots": int(len(lots)), "blanket_lots": int(len(blanket)),
        "targeted_parts": int(units.reindex(lots.lot_id).fillna(0).sum()),
        "blanket_parts": int(units.reindex(blanket.lot_id).fillna(0).sum()),
        "by_status": {k: int(v) for k, v in by_status.items()},
        "lots": lots.sort_values(["prod_date", "shift"])[["lot_id", "prod_date", "press", "shift", "shipment_id", "status"]]
                    .assign(prod_date=lambda x: x.prod_date.astype(str)).to_dict("records"),
        "note": ("cavity-level cause: whole lots are held because lots mix cavities" if "cavity" in filters else None),
    }


def past_8d_lookup(past: pd.DataFrame, defect: str, keywords: list[str] | None = None) -> dict:
    keywords = [k.lower() for k in (keywords or [])]
    hits = []
    for _, r in past.iterrows():
        text = " ".join(str(r[c]) for c in ("occurrence_cause", "escape_cause", "lesson")).lower()
        score = (2 if r.defect == defect else 0) + sum(1 for k in keywords if k in text)
        if score >= 2:
            hits.append({**{c: str(r[c]) for c in past.columns}, "score": score})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return {"defect": defect, "keywords": keywords, "matches": hits[:3]}
