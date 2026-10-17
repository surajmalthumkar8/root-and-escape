"""Runs one investigation: Intake → Investigator → Challenger (up to 3 rounds) → Writer.

Yields events for the live UI. Each Gemini step has a deterministic fallback, and the result records which
mode actually produced each stage, so the UI never claims Gemini did something it didn't.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import AsyncIterator

from .challenger import _numbers_ok, check_draft, screen_hypotheses, verdict
from .data.cases import CANNED_INTAKE
from .report import build_report, scripted_investigation
from .tools import EvidenceLog, Toolbox, public_item

MAX_ROUNDS = 3


def ev(kind: str, **kw) -> dict:
    return {"type": kind, "t": round(time.time(), 2), **kw}


def _short(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def deterministic_intake(case: dict) -> dict:
    canned = CANNED_INTAKE.get(case["case_id"])
    if canned:
        return {**canned, "source": "stored reading (deterministic mode)"}
    text = case.get("complaint", "")
    m = re.search(r"(\d[\d,]*)\s*(?:of|/|個中|中)\s*(\d[\d,]*)", text)
    how_many = f"{m.group(1)} of {m.group(2)}" if m else "not stated"
    return {
        "translation_en": None, "defect_type": case["defect"], "box_2d": None,
        "photo_observation": "Photo not read in deterministic mode.",
        "d2": {"what": case["defect"], "where": case.get("customer", "customer"), "how_many": how_many,
               "when": f"date codes {case['date_code_start']} to {case['date_code_end']}"},
        "source": "parsed from the complaint (deterministic mode)",
    }


def valid_box(box) -> list[int] | None:
    try:
        b = [int(round(float(x))) for x in box]
    except Exception:
        return None
    if len(b) != 4 or not all(0 <= x <= 1000 for x in b) or b[0] >= b[2] or b[1] >= b[3]:
        return None
    return b


def _report_ok(sections: dict, log: EvidenceLog) -> bool:
    texts = [v for k, v in sections.items() if k in ("D3", "D4", "D4_escape") and isinstance(v, str)]
    ok, _ = _numbers_ok(texts, log)
    return ok and all(sections.get(k) for k in ("D2", "D3", "D4", "D5"))


async def run_investigation(case: dict, plant, plant_source: str, llm=None, photos: dict | None = None,
                            contribution_engine=None) -> AsyncIterator[dict]:
    t0 = time.time()
    notes: list[str] = []
    modes = {"intake": "deterministic", "investigation": "deterministic", "writer": "deterministic"}
    usage_before = dict(getattr(llm, "usage", {}) or {})

    # ---- Intake --------------------------------------------------------------------------------------
    yield ev("stage", stage="intake", status="running", label="Intake agent reads the complaint and photos")
    intake = None
    if llm:
        try:
            intake = await llm.intake(case, photos or {})
            modes["intake"] = "gemini"
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Intake fell back to the stored reading ({_short(exc)}).")
    if intake is None:
        intake = deterministic_intake(case)
    intake["box_2d"] = valid_box(intake.get("box_2d")) if intake.get("box_2d") is not None else None
    yield ev("intake", intake=intake)

    # ---- Investigation -------------------------------------------------------------------------------
    log = EvidenceLog()
    tb = Toolbox(plant, case, log, contribution_engine)
    sent = 0
    yield ev("stage", stage="investigate", status="running",
             label="Investigator agent tests hypotheses against plant data")
    draft = None
    if llm:
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        log.listener = lambda item: loop.call_soon_threadsafe(queue.put_nowait, item)
        task = asyncio.create_task(llm.investigate(tb, case, intake))
        while True:  # stream each tool result while the agent is still thinking
            getter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield ev("evidence", item=public_item(getter.result()))
                sent += 1
            else:
                getter.cancel()
            if task.done() and queue.empty():
                break
        log.listener = None
        try:
            draft = task.result()
            modes["investigation"] = "gemini"
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Investigator fell back to the scripted policy ({_short(exc)}).")
    if draft is None:
        draft = scripted_investigation(tb)
    for item in log.public()[sent:]:
        yield ev("evidence", item=item)
    sent = len(log.items)

    # ---- Challenger ----------------------------------------------------------------------------------
    rounds = []
    for rnd in range(1, MAX_ROUNDS + 1):
        res = check_draft(draft, tb)
        v = verdict(res)
        rounds.append({"round": rnd, "verdict": v, "rules": res, "by": modes["investigation"],
                       "occurrence": (draft.get("occurrence") or {}).get("statement"),
                       "escape": (draft.get("escape") or {}).get("statement"),
                       "insufficient": bool(draft.get("insufficient_evidence"))})
        yield ev("challenger", round=rounds[-1])
        if v == "APPROVED_FOR_REVIEW":
            break
        revised = None
        if llm and modes["investigation"] == "gemini" and rnd < MAX_ROUNDS - 1:
            try:
                revised = await llm.revise(tb, case, intake, draft, res)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"Revision failed ({_short(exc)}).")
        if revised is None:
            notes.append(f"Round {rnd}: draft rejected; the scripted policy wrote the next draft.")
            revised = scripted_investigation(tb)
            modes["investigation"] = "deterministic" if modes["investigation"] == "deterministic" else "gemini+scripted"
        draft = revised
        for item in log.public()[sent:]:
            yield ev("evidence", item=item)
        sent = len(log.items)

    hyps = draft.get("hypotheses") or (screen_hypotheses(tb) if tb.last_is_is_not and tb.last_is_is_not["significant"] else [])
    containment = None
    if not draft.get("insufficient_evidence"):
        seg = (draft.get("occurrence") or {}).get("segment") or (tb.last_is_is_not or {}).get("primary_segment")
        existing = [i for i in log.items if i["tool"] == "containment" and i["args"].get("segment") == seg]
        if not existing and seg:
            tb.containment(seg)
            existing = [log.items[-1]]
        containment = existing[-1]["data"] if existing else None
    for item in log.public()[sent:]:
        yield ev("evidence", item=item)
    sent = len(log.items)
    yield ev("hypotheses", hypotheses=hyps)

    # ---- Writer --------------------------------------------------------------------------------------
    yield ev("stage", stage="write", status="running", label="Writer drafts the 8D in English and Japanese")
    report = None
    if llm:
        try:
            report = await llm.write(case, intake, draft, containment, log)
            if not (_report_ok(report.get("en", {}), log)):
                notes.append("The Writer's text quoted numbers that are not in the evidence, so the template text is shown.")
                report = None
            else:
                modes["writer"] = "gemini"
        except Exception as exc:  # noqa: BLE001
            notes.append(f"Writer fell back to templates ({_short(exc)}).")
    if report is None:
        report = {"en": build_report(case, intake, draft, containment, "en"),
                  "ja": build_report(case, intake, draft, containment, "ja")}

    final_verdict = rounds[-1]["verdict"]
    usage = dict(getattr(llm, "usage", {}) or {})
    if usage and usage_before:
        usage = {k: usage.get(k, 0) - usage_before.get(k, 0) for k in usage}
    result = {
        "case_id": case["case_id"], "created_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(time.time() - t0, 1), "modes": modes, "notes": notes,
        "data_source": plant_source, "model": getattr(llm, "model", None),
        "intake": intake, "evidence": log.public(), "analysis": _analysis_view(tb),
        "draft": {k: v for k, v in draft.items() if k != "hypotheses"}, "hypotheses": hyps,
        "challenger": rounds, "verdict": final_verdict, "containment": _containment_view(containment),
        "report": report, "usage": usage or None,
    }
    yield ev("done", result=result)


def _analysis_view(tb: Toolbox) -> dict | None:
    r = tb.last_is_is_not
    if not r:
        return None
    keep = ("defect", "test_window", "control_window", "overall", "significant", "primary_segment", "primary_label",
            "refined_by", "heatmap", "method")
    out = {k: r.get(k) for k in keep}
    out["segments"] = [{k: s[k] for k in ("label", "filters", "rate_test", "rate_control", "ratio", "z",
                                          "excess_defects", "significant")} for s in r["top_segments"][:10]]
    if r.get("bqml_insights"):
        out["bqml_insights"] = r["bqml_insights"][:10]
    if r.get("bqml_error"):
        out["bqml_error"] = r["bqml_error"]
    return out


def _containment_view(c: dict | None) -> dict | None:
    if not c:
        return None
    return {k: c[k] for k in ("segment", "segment_label", "suspect_window", "targeted_lots", "blanket_lots",
                              "by_status", "lots", "note") if k in c}
