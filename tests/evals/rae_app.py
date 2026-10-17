"""The eval entry point: run one complete investigation for a golden and return the stored result.

Modes (environment variable RAE_EVAL_MODE):
- deterministic (default): no model, no network. Scores the engine every judge sees when Gemini is off.
- gemini: the real Gemini agents on Agent Platform (Intake, ADK Investigator, Writer). Needs Application Default
  Credentials and GOOGLE_CLOUD_PROJECT, so run it in Cloud Shell. About $0.03 per golden.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rae.data.cases import get_seed  # noqa: E402
from rae.data.generator import Planted, generate  # noqa: E402
from rae.pipeline import run_investigation  # noqa: E402

MODE = os.getenv("RAE_EVAL_MODE", "deterministic")


@lru_cache(maxsize=None)
def _agents():
    from rae.agents import GeminiAgents
    return GeminiAgents()


def _plant(meta: dict):
    if meta["kind"] == "story":
        return generate(42)
    p = meta["planted"]
    extra = [Planted(meta["defect"], p, date.fromisoformat(meta["start"]), date.fromisoformat(meta["end"]), meta["rate"])] if p else []
    return generate(seed=meta["seed"], planted_story=False, extra=extra)


def _case(complaint: str, meta: dict) -> dict:
    if meta["kind"] == "story":
        return get_seed(meta["case_id"])
    return {"case_id": f"EVAL-{meta['case_id']}-{meta['seed']}", "title": meta["case_id"], "customer": "Eval customer",
            "defect": meta["defect"], "received": "2026-10-16T09:00:00+00:00", "date_code_start": meta["start"],
            "date_code_end": meta["end"], "language": "en", "complaint": complaint, "photo": None, "golden": None}


def _rows(df, filters: dict | None) -> set:
    if not filters:
        return set()
    m = None
    for k, v in filters.items():
        x = df[k] == v
        m = x if m is None else (m & x)
    return set(df[m].index)


def _truth(plant, meta: dict, result: dict) -> dict:
    """Score against the planted cause, the same way rae/benchmark.py does."""
    p = meta["planted"]
    if not p:
        return {"overlap": None, "containment_recall": None}
    s, e = date.fromisoformat(meta["start"]), date.fromisoformat(meta["end"])
    prod = plant.production
    test = prod[(prod.prod_date >= s) & (prod.prod_date <= e)]
    found = ((result.get("analysis") or {}).get("primary_segment")) or {}
    truth, got = _rows(test, p), _rows(test, found)
    overlap = len(truth & got) / len(truth | got) if (truth | got) else 0.0
    ship = plant.shipments[(plant.shipments.prod_date >= s) & (plant.shipments.prod_date <= e)]
    for k, v in p.items():
        if k in ("press", "shift"):
            ship = ship[ship[k] == v]
    true_lots = set(ship.lot_id)
    held = {l["lot_id"] for l in ((result.get("containment") or {}).get("lots") or [])}
    recall = len(true_lots & held) / len(true_lots) if true_lots else None
    return {"overlap": round(overlap, 2), "containment_recall": None if recall is None else round(recall, 2)}


def run_rae(complaint: str, meta: dict) -> dict:
    """Run one investigation. Returns plain JSON: the stored result, the golden's metadata and the truth scores."""
    plant = _plant(meta)
    case = _case(complaint, meta)
    llm = _agents() if MODE == "gemini" else None

    async def go():
        res = None
        async for ev in run_investigation(case, plant, "eval", llm, {}, None):
            if ev["type"] == "done":
                res = ev["result"]
        return res

    result = json.loads(json.dumps(asyncio.run(go()), default=str))
    return {"result": result, "meta": meta, "mode": MODE, **_truth(plant, meta, result)}


def report_text(result: dict) -> str:
    """The text a judge or customer reads: the D2–D5 sections of the English 8D."""
    en = (result.get("report") or {}).get("en") or {}
    return "\n".join(f"{k}: {en[k]}" for k in ("D2", "D3", "D4", "D4_escape", "D5") if en.get(k))
