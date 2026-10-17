"""The Challenger: deterministic rules that every draft 8D must pass before a person can approve it.

Two jobs:
1. Screen hypotheses. Every segment that looks significant on its own is ruled SUPPORTED, EXPLAINED,
   REJECTED or OPEN. People-related hypotheses (crew, shift) are always stratified first.
2. Check the draft against the 8D rules below. Every rejection is shown in the UI.
"""
from __future__ import annotations

import re

from . import analytics as A
from .tools import EvidenceLog, Toolbox

PEOPLE_RE = re.compile(
    r"\b(operator|operators|crew|human error|retrain|re-train|night shift|worker|careless|training|staff|inspector error)\b"
    r"|作業者|オペレーター|人為|夜勤|班|教育不足",
    re.I,
)
DETECTION_RE = re.compile(
    r"sampl|inspect|gauge|calibrat|control plan|check|detect|test|audit|検査|ゲージ|校正|抜き取り|管理計画|検出",
    re.I,
)
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
LOTS_RE = re.compile(r"(\d[\d,]*)\s*(?:lots?\b|ロット)", re.I)


def _subset(a: dict, b: dict) -> bool:
    return all(b.get(k) == v for k, v in a.items())


def _explained_by(tb: Toolbox, seg: dict, primary: dict) -> dict:
    """Recompute `seg` with the primary segment's rows removed. If the jump vanishes, `seg` was a side effect."""
    prod = tb.plant.production
    mask = None
    for k, v in primary.items():
        m = prod[k] == v
        mask = m if mask is None else (mask & m)
    rest = prod[~mask]
    r = A.is_is_not(rest, tb.defect, tb.start, tb.end)
    match = [s for s in r["top_segments"] if s["filters"] == seg]
    if not match:
        return {"ratio": None, "explained": True}
    s = match[0]
    return {"ratio": s["ratio"], "rate_test": s["rate_test"], "rate_control": s["rate_control"],
            "explained": s["ratio"] < 1.8 or not s["significant"]}


def screen_hypotheses(tb: Toolbox) -> list[dict]:
    r = tb.last_is_is_not
    if not r:
        return []
    primary = r["primary_segment"]
    src = tb.log.items[0]["id"] if tb.log.items else None
    out = []
    for s in r["significant_singles"]:
        f = s["filters"]
        (dim, val), = f.items()
        base = {"hypothesis": f"{dim} {val}", "looks": f"×{s['ratio']} vs before ({A.pct(s['rate_test'])} vs {A.pct(s['rate_control'])})"}
        if primary and _subset(f, primary):
            out.append(base | {"ruling": "SUPPORTED", "reason": f"part of the primary segment {A.seg_label(primary)}",
                               "evidence": [src]})
            continue
        if dim in A.PEOPLE_DIMS and primary:
            within = next((k for k in primary if k not in A.PEOPLE_DIMS), None)
            if within:
                st = tb.stratify(dim, within)
                if st["confounded"]:
                    out.append(base | {"ruling": "REJECTED",
                                       "reason": f"confounded: inside each {within} there is no {dim} effect. "
                                                 f"{dim} {val} just works {A.seg_label(primary)} more often.",
                                       "evidence": [st["evidence_id"]]})
                elif st["effect_persists_within"]:
                    out.append(base | {"ruling": "OPEN", "reason": f"the {dim} effect persists inside some {within}s; investigate",
                                       "evidence": [st["evidence_id"]]})
                else:
                    out.append(base | {"ruling": "REJECTED",
                                       "reason": f"every {dim} rose together; inside each {within}, no {dim} stands out",
                                       "evidence": [st["evidence_id"]]})
                continue
        if primary:
            ex = _explained_by(tb, f, primary)
            if ex["explained"]:
                out.append(base | {"ruling": "EXPLAINED",
                                   "reason": f"with {A.seg_label(primary)} removed, {dim} {val} is normal"
                                             + (f" (×{ex['ratio']})" if ex.get("ratio") else ""),
                                   "evidence": [src]})
            else:
                out.append(base | {"ruling": "OPEN", "reason": f"still elevated with {A.seg_label(primary)} removed (×{ex['ratio']})",
                                   "evidence": [src]})
    return out


def _numbers_ok(texts: list[str], log: EvidenceLog) -> tuple[bool, list[str]]:
    pcts, lots = log.percentages(), set(log.lot_counts())
    bad = []
    for t in texts:
        for m in PCT_RE.finditer(t or ""):
            x = float(m.group(1))
            if not any(abs(x - e) <= 0.05 for e in pcts):
                bad.append(m.group(0))
        for m in LOTS_RE.finditer(t or ""):
            if int(m.group(1).replace(",", "")) not in lots:
                bad.append(m.group(0))
    return (not bad), bad


def check_draft(draft: dict, tb: Toolbox) -> list[dict]:
    log, r = tb.log, tb.last_is_is_not
    res: list[dict] = []

    def rule(name: str, ok: bool, msg_fail: str, msg_ok: str = "") -> None:
        res.append({"rule": name, "passed": bool(ok), "message": msg_ok if ok else msg_fail})

    insufficient = bool(draft.get("insufficient_evidence"))
    rule("analysis_was_run", r is not None,
         "No is / is-not analysis was run, so nothing in this draft is grounded in plant data.",
         "The is / is-not analysis was run on plant data.")
    if r is None:
        return res
    if not r["significant"]:
        rule("no_claim_without_signal", insufficient,
             "The is / is-not analysis found no significant segment, so a root cause cannot be claimed. "
             "Write 'insufficient evidence' and list the data to collect.",
             "No significant segment, and the draft correctly says 'insufficient evidence'.")
    elif insufficient:
        rule("no_abstain_with_signal", False,
             f"The is / is-not analysis found a significant segment ({r['primary_label']}). "
             "Investigate it instead of answering 'insufficient evidence'.")
    if insufficient:
        return res

    occ, esc = draft.get("occurrence") or {}, draft.get("escape") or {}
    rule("requires_occurrence_and_escape", bool(occ.get("statement")) and bool(esc.get("statement")),
         "An 8D needs two root causes: why the defect happened (occurrence) and why it was not caught (escape).",
         "Both occurrence and escape causes are present.")
    cited = set(occ.get("evidence") or []) | set(esc.get("evidence") or [])
    missing = sorted(cited - log.ids())
    rule("evidence_must_resolve", bool(occ.get("evidence")) and bool(esc.get("evidence")) and not missing,
         f"Every cause must cite evidence that exists. Missing or unknown: {missing or 'no citations'}.",
         "Every cited evidence ID resolves to a stored tool result.")
    rule("escape_must_explain_detection", bool(DETECTION_RE.search(esc.get("statement") or "")),
         "The escape cause must explain why inspection or detection did not catch the defect.",
         "The escape cause names a detection weakness.")
    if PEOPLE_RE.search(occ.get("statement") or ""):
        support = [i for i in log.items if i["tool"] == "stratify" and i["args"].get("by") in A.PEOPLE_DIMS
                   and i["data"].get("effect_persists_within") and not i["data"].get("confounded")
                   and i["id"] in cited]
        rule("no_people_blame_without_stratified_effect", bool(support),
             "Operator error: REJECTED. A people-related cause needs a stratified effect that survives inside each "
             "press. The evidence shows the crew difference disappears once split by press.",
             "The people-related cause is backed by an effect that persists after stratification.")
    if r is not None and r["primary_segment"]:
        seg = occ.get("segment") or {}
        rule("segment_matches_is_is_not", bool(seg) and (_subset(r["primary_segment"], seg) or _subset(seg, r["primary_segment"])),
             f"The occurrence must name the segment the is / is-not analysis found ({r['primary_label']}).",
             f"The occurrence matches the analysed segment ({r['primary_label']}).")
    ok, bad = _numbers_ok([occ.get("statement"), esc.get("statement")], log)
    rule("numbers_come_from_evidence", ok,
         f"These numbers do not appear in any evidence: {bad}. Quote tool results only.",
         "Every percentage and lot count matches a tool result.")
    return res


def verdict(results: list[dict]) -> str:
    return "APPROVED_FOR_REVIEW" if all(r["passed"] for r in results) else "REJECTED"
