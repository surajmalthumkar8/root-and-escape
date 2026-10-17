"""Runs one investigation: Intake → Investigator → Challenger (up to 3 rounds) → Writer.

Yields events for the live UI. Each Gemini step has a deterministic fallback, and the result records which mode
actually produced each stage, so the UI never claims Gemini did something it didn't.

Time budget: the whole run must finish well inside Cloud Run's request timeout (600 s in deploy.sh). Each Gemini stage gets what is
left of a 230 s budget; when a stage runs out of time it falls back to the deterministic engine.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from .challenger import _numbers_ok, check_draft, screen_hypotheses, verdict
from .data.cases import CANNED_INTAKE
from .report import build_report, scripted_investigation
from .tools import EvidenceLog, Toolbox, public_item

log_ = logging.getLogger("rae.pipeline")
MAX_ROUNDS = 3
RUN_BUDGET_S = 230


def ev(kind: str, **kw) -> dict:
    return {"type": kind, "t": round(time.time(), 2), **kw}


def _short(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timed out"
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def deterministic_intake(case: dict) -> dict:
    canned = CANNED_INTAKE.get(case["case_id"])
    if canned:
        return {**canned, "source": "stored reading (deterministic mode)"}
    text = case.get("complaint", "")
    m = re.search(r"(\d[\d,]*)\s*(?:of|/|個中|中)\s*(\d[\d,]*)", text)
    how_many = f"{m.group(1).strip(',')} of {m.group(2).strip(',')}" if m else "not stated"
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
    deadline = t0 + RUN_BUDGET_S
    left = lambda cap: max(1.0, min(cap, deadline - time.time()))  # noqa: E731
    notes: list[str] = []
    modes = {"intake": "deterministic", "investigation": "deterministic", "writer": "deterministic"}
    usage: dict = {}
    usage_token = None
    if llm is not None:
        from .agents import RUN_USAGE
        usage_token = RUN_USAGE.set(usage)

    def fell_back(stage: str, exc: BaseException) -> None:
        notes.append(f"{stage} fell back to the deterministic engine ({_short(exc)}).")
        log_.warning("case %s: %s fallback: %s", case.get("case_id"), stage, _short(exc))

    try:
        log = EvidenceLog()
        tb = Toolbox(plant, case, log, contribution_engine)
        tb.prefetch_contribution()  # BigQuery ML works in the background while intake runs

        # ---- Intake ----------------------------------------------------------------------------------
        yield ev("stage", stage="intake", status="running", label="Intake agent reads the complaint and photos")
        intake = None
        if llm:
            try:
                intake = await asyncio.wait_for(llm.intake(case, photos or {}), timeout=left(60))
                modes["intake"] = "gemini"
            except Exception as exc:  # noqa: BLE001
                fell_back("Intake", exc)
        if intake is None:
            intake = deterministic_intake(case)
        intake["box_2d"] = valid_box(intake.get("box_2d")) if intake.get("box_2d") is not None else None
        yield ev("intake", intake=intake)

        # ---- Investigation ---------------------------------------------------------------------------
        sent = 0
        yield ev("stage", stage="investigate", status="running", label="Investigator agent tests hypotheses against plant data")
        draft = None
        if llm:
            queue: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()
            log.listener = lambda item: loop.call_soon_threadsafe(queue.put_nowait, item)
            on_call = lambda name, args: loop.call_soon_threadsafe(queue.put_nowait, {"_call": name, "args": args})  # noqa: E731
            task = asyncio.create_task(llm.investigate(tb, case, intake, on_call=on_call))
            stop_at = time.time() + left(130)
            getter = None
            try:
                while True:  # stream each tool call and result while the agent is still thinking
                    getter = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait({task, getter}, timeout=max(0.1, stop_at - time.time()),
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if getter in done:
                        item = getter.result()
                        if "_call" in item:
                            yield ev("call", name=item["_call"], args=item["args"])
                        else:
                            yield ev("evidence", item=public_item(item))
                            sent += 1
                    else:
                        getter.cancel()
                    if task.done() and queue.empty():
                        break
                    if time.time() >= stop_at:
                        task.cancel()
                        raise asyncio.TimeoutError()
                draft = task.result()
                modes["investigation"] = "gemini"
            except Exception as exc:  # noqa: BLE001
                fell_back("Investigator", exc)
            finally:  # never leave the agent running after the client leaves or time runs out
                log.listener = None
                if getter is not None and not getter.done():
                    getter.cancel()
                if not task.done():
                    task.cancel()
        if draft is None:
            draft = await asyncio.to_thread(scripted_investigation, tb)
        for item in log.public()[sent:]:
            yield ev("evidence", item=item)
        sent = len(log.items)

        # ---- Challenger ------------------------------------------------------------------------------
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
            if llm and modes["investigation"] == "gemini" and rnd < MAX_ROUNDS - 1 and deadline - time.time() > 70:
                try:
                    revised = await asyncio.wait_for(llm.revise(tb, case, intake, draft, res), timeout=left(60))
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"Revision failed ({_short(exc)}).")
                    log_.warning("case %s: revision failed: %s", case.get("case_id"), _short(exc))
            if revised is None:
                notes.append(f"Round {rnd}: draft rejected; the scripted policy wrote the next draft.")
                revised = await asyncio.to_thread(scripted_investigation, tb)
                modes["investigation"] = "deterministic" if modes["investigation"] == "deterministic" else "gemini+scripted"
            draft = revised
            for item in log.public()[sent:]:
                yield ev("evidence", item=item)
            sent = len(log.items)

        hyps = draft.get("hypotheses")
        if not hyps and tb.last_is_is_not and tb.last_is_is_not["significant"]:
            hyps = await asyncio.to_thread(screen_hypotheses, tb)
        hyps = hyps or []
        containment = None
        if not draft.get("insufficient_evidence"):
            seg = (draft.get("occurrence") or {}).get("segment") or (tb.last_is_is_not or {}).get("primary_segment")
            existing = [i for i in log.items if i["tool"] == "containment" and i["args"].get("segment") == seg]
            if not existing and seg:
                await asyncio.to_thread(tb.containment, seg)
                existing = [log.items[-1]]
            containment = existing[-1]["data"] if existing else None
        for item in log.public()[sent:]:
            yield ev("evidence", item=item)
        sent = len(log.items)
        yield ev("hypotheses", hypotheses=hyps)

        # ---- Writer ----------------------------------------------------------------------------------
        yield ev("stage", stage="write", status="running", label="Writer drafts the 8D in English and Japanese")
        report = None
        if llm and deadline - time.time() > 15:
            try:
                report = await asyncio.wait_for(llm.write(case, intake, draft, containment, log), timeout=left(60))
                if not (_report_ok(report.get("en", {}), log) and _report_ok(report.get("ja", {}), log)):
                    notes.append("The Writer's text quoted numbers that are not in the evidence, so the template text is shown.")
                    report = None
                else:
                    modes["writer"] = "gemini"
            except Exception as exc:  # noqa: BLE001
                fell_back("Writer", exc)
        if report is None:
            report = {"en": build_report(case, intake, draft, containment, "en"),
                      "ja": build_report(case, intake, draft, containment, "ja")}

        if "open_points" not in draft and not draft.get("insufficient_evidence"):  # Gemini drafts: derive from the text
            occ_s = ((draft.get("occurrence") or {}).get("statement") or "").lower()
            esc_s = ((draft.get("escape") or {}).get("statement") or "").lower()
            draft["open_points"] = (["Mechanism not yet confirmed"] if "not yet confirmed" in occ_s else []) +                 (["Escape point not yet identified"] if "not yet identified" in esc_s else [])
        actions = assign_owners(draft.get("actions") or [], case)
        result = {
            "case_id": case["case_id"], "created_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": round(time.time() - t0, 1), "modes": modes, "notes": notes,
            "data_source": plant_source, "model": getattr(llm, "model", None),
            "intake": intake, "evidence": log.public(), "analysis": _analysis_view(tb),
            "draft": {k: v for k, v in draft.items() if k != "hypotheses"}, "hypotheses": hyps,
            "challenger": rounds, "verdict": rounds[-1]["verdict"], "containment": _containment_view(containment),
            "report": report, "usage": dict(usage) or None, "actions": actions,
        }
        yield ev("done", result=result)
    finally:
        if usage_token is not None:
            from .agents import RUN_USAGE
            RUN_USAGE.reset(usage_token)


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
    return {k: c[k] for k in ("segment", "segment_label", "segment_label_ja", "suspect_window", "targeted_lots",
                              "blanket_lots", "targeted_parts", "blanket_parts", "by_status", "lots", "note") if k in c}


OWNER_RULES = [
    (re.compile(r"sampl|inspect|gauge|calibrat|control plan|check", re.I), "Quality", 1),
    (re.compile(r"dryer|mould|mold|cavity|maintenance|interlock|repair|heater|tool", re.I), "Maintenance", 5),
]


def assign_owners(actions: list[str], case: dict) -> list[dict]:
    """Owner and due date for each action. Detection fixes are due in 1 day (containment), and equipment fixes in
    5 days (root-cause deadline). Anything else follows the 20-day corrective-action clock."""
    try:
        received = datetime.fromisoformat(case.get("received", ""))
    except ValueError:
        received = datetime.now(timezone.utc)
    out = []
    for a in actions:
        owner, days = "Process engineering", 20
        for rx, o, d in OWNER_RULES:
            if rx.search(a):
                owner, days = o, d
                break
        out.append({"action": a, "owner": owner, "due": (received + timedelta(days=days)).date().isoformat()})
    return out
