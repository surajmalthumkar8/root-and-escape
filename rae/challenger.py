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
    r"\b(operators?|crews?|human|humans|people|persons?|personnel|employees?|workers?|staff|inspectors?|inspector error"
    r"|teams?|setters?|technicians?|line leaders?|supervisors?|foreman|foremen|new hires?|trainees?"
    r"|mistakes?|forgot|forgotten|forgetting|negligen\w*|careless\w*|retrain\w*|re-train\w*|training"
    r"|night[- ]?shifts?|nights|shifts?\s+[DNS]|crews?\s+[A-Z])\b"
    r"|作業者|作業員|担当者|検査員|オペレーター|人為|夜勤|班|教育不足|ミス|うっかり",
    re.I,
)
DETECTION_RE = re.compile(
    r"sampl|inspect|gauge|calibrat|control plan|check|detect|test|audit|\bQC\b|quality control|\bAQL\b"
    r"|検査|ゲージ|校正|抜き取り|管理計画|検出|品質管理",
    re.I,
)
# How an occurrence statement names a mechanism, keyed like A.MECHANISM_EFFECTS.
MECHANISM_RE = {
    "dryer_dew_point_c": re.compile(r"dryer|dew[- ]?point|moisture|wet resin|乾燥|露点|吸湿", re.I),
    "mold_shots_since_pm": re.compile(r"\bmou?lds?\b[^.;]*\b(wear|wore|worn|maintenance|shots?)\b"
                                      r"|\b(worn|wearing|worn-out|damaged)\s+mou?lds?\b|maintenance limit|摩耗|保全上限", re.I),
    "melt_temp_c": re.compile(r"melt temp|溶融温度|樹脂温度", re.I),
    "hold_pressure_bar": re.compile(r"hold(ing)? pressure|packing pressure|保圧", re.I),
    "cycle_time_s": re.compile(r"cycle time|cooling time|サイクルタイム|冷却時間", re.I),
}
MECHANISM_NAMES = {"dryer_dew_point_c": "wet resin (dryer)", "mold_shots_since_pm": "a worn mould",
                   "melt_temp_c": "melt temperature", "hold_pressure_bar": "hold pressure", "cycle_time_s": "cycle time"}
UNCONFIRMED_RE = re.compile(r"not yet confirmed|not confirmed|未確定", re.I)
CERTAINTY_RE = re.compile(r"\b(certain|definitely|clearly|obviously|we know|no doubt|sure)\b|確実|間違いなく", re.I)
INSUFFICIENT_RE = re.compile(r"insufficient evidence|no (root )?cause (found|stands out|is supported)|証拠不十分", re.I)
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b|per cent\b|パーセント)", re.I)
LOTS_RE = re.compile(r"(\d[\d,]*)\s*(?:lots?\b|ロット)", re.I)
UNIT_RE = re.compile(r"([-−+]?\d[\d,]*(?:\.\d+)?)\s?(?:°\s?C|℃|σ|/\s?h\b)|×\s?(\d+(?:\.\d+)?)", re.I)
NUM_RE = re.compile(r"[-−]?\d[\d,]*(?:\.\d+)?")

# Ways a segment value is written: P4, press 4, プレス4, 4号機; C3, cavity 3, キャビティ3; shift N; crew B; R-2291.
SEGMENT_RES = {
    "press": re.compile(r"\bpress(?:es)?\s*#?\s*P?(\d)\b|\bP(\d)\b|プレス\s*P?(\d)|(\d)\s*号機", re.I),
    "cavity": re.compile(r"\bcavit(?:y|ies)\s*#?\s*C?(\d)\b|\bC(\d)\b|キャビティ\s*C?(\d)", re.I),
    "shift": re.compile(r"\bshifts?\s+([DNS])\b"),
    "crew": re.compile(r"\bcrews?\s+([A-Z])\b"),
    "resin_lot": re.compile(r"\b(R-\d{3,5})\b"),
}
SEGMENT_PREFIX = {"press": "P", "cavity": "C", "shift": "", "crew": "", "resin_lot": ""}
NEGATION_RE = re.compile(r"\b(not|no|never|unlike|except|excluding|other than|rather than)\b|n't\b|ではなく|以外", re.I)


def segment_mentions(text: str) -> dict[str, set]:
    """Segment values a statement names, ignoring negated ones ("did not come from P4", "unlike P1")."""
    text = text or ""
    out: dict[str, set] = {}
    for dim, rx in SEGMENT_RES.items():
        for m in rx.finditer(text):
            before = re.split(r"[,.;:!?。、]", text[max(0, m.start() - 30):m.start()])[-1]  # negation stops at a clause break
            before = UNCONFIRMED_RE.sub("", before)  # "not yet confirmed on press P4" still names P4
            if NEGATION_RE.search(before):
                continue
            val = next(g for g in m.groups() if g)
            out.setdefault(dim, set()).add(f"{SEGMENT_PREFIX[dim]}{val}" if not val.startswith(SEGMENT_PREFIX[dim]) else val)
    return out


def names_segment(text: str, seg: dict) -> tuple[bool, str]:
    """True if the statement names every part of `seg` and no other value of the same dimension."""
    m = segment_mentions(text)
    missing = [f"{k} {v}" for k, v in seg.items() if v not in m.get(k, set())]
    other = sorted(f"{k} {x}" for k, v in seg.items() for x in m.get(k, set()) if x != v)
    if missing:
        return False, f"it doesn't name {', '.join(missing)}"
    if other:
        return False, f"it also names {', '.join(other)}"
    return True, ""


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
    """Every percentage, lot count, °C, σ, per-hour rate and × ratio in the texts must appear in a tool result."""
    pcts, lots = log.percentages(), set(log.lot_counts())
    evid = [float(x.replace("−", "-").replace(",", "")) for i in log.items for x in NUM_RE.findall(i.get("summary") or "")]
    bad = []
    for t in texts:
        t = t or ""
        for m in PCT_RE.finditer(t):
            x = float(m.group(1))
            if not any(abs(x - e) <= 0.05 for e in pcts):
                bad.append(m.group(0))
        for m in LOTS_RE.finditer(t):
            if int(m.group(1).replace(",", "")) not in lots:
                bad.append(m.group(0))
        for m in UNIT_RE.finditer(t):
            x = float((m.group(1) or m.group(2)).replace("−", "-").replace("+", "").replace(",", ""))
            if not any(abs(x - e) <= max(0.051, abs(e) * 0.001) for e in evid):
                bad.append(m.group(0).strip())
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
    occ_text, esc_text = occ.get("statement") or "", esc.get("statement") or ""
    if PEOPLE_RE.search(occ_text) or PEOPLE_RE.search(esc_text):
        support = [i for i in log.items if i["tool"] == "stratify" and i["args"].get("by") in A.PEOPLE_DIMS
                   and i["data"].get("effect_persists_within") and not i["data"].get("confounded")
                   and i["id"] in cited]
        rule("no_people_blame_without_stratified_effect", bool(support),
             "People blamed without evidence: REJECTED. A cause about people (in either the occurrence or the escape) "
             "needs a stratified effect that survives inside each press, and the evidence doesn't show one.",
             "The people-related cause is backed by an effect that persists after stratification.")
    if r["primary_segment"]:
        seg = occ.get("segment") or {}
        ok_fields = bool(seg) and (_subset(r["primary_segment"], seg) or _subset(seg, r["primary_segment"]))
        ok_text, why = names_segment(occ_text, r["primary_segment"])
        rule("segment_matches_is_is_not", ok_fields and ok_text,
             f"The occurrence must name the segment the is / is-not analysis found ({r['primary_label']})"
             + (f", but {why}." if why else "."),
             f"The occurrence names the analysed segment ({r['primary_label']}).")
    named = [k for k, rx in MECHANISM_RE.items() if rx.search(occ_text)]
    is_open = bool(UNCONFIRMED_RE.search(occ_text)) and not CERTAINTY_RE.search(occ_text)  # "not confirmed, but certain"
    rule("mechanism_named_or_open", bool(named) or is_open,
         "Name the physical mechanism the process data shows, or say the mechanism is not yet confirmed. "
         "A cause with no mechanism can't be checked.",
         "The occurrence names a mechanism or marks it as not yet confirmed.")
    if named:
        press = (r["primary_segment"] or {}).get("press")
        flagged = {f for i in log.items if i["tool"] == "param_excursions"
                   and (press is None or i["args"].get("press") == press) for f in (i["data"].get("flagged") or [])}
        absent = [k for k in named if k not in flagged]
        names = " and ".join(MECHANISM_NAMES[k] for k in absent or named)
        rule("mechanism_in_evidence", not absent,
             f"The process data{(' for press ' + press) if press else ''} does not show {names}. "
             "Name only a mechanism that the evidence flags.",
             f"The named mechanism ({names}) is flagged in the process data.")
        defect = getattr(tb, "defect", None)
        if defect:
            words = A.DEFECT_WORDS.get(defect, defect)
            unfit = [k for k in named if defect not in A.MECHANISM_DEFECTS[k]]
            fnames = " and ".join(MECHANISM_NAMES[k] for k in unfit or named)
            rule("mechanism_fits_defect", not unfit,
                 f"{fnames[0].upper() + fnames[1:]} does not usually cause {words}. Name a mechanism that can produce "
                 f"{words}, or say the mechanism is not yet confirmed.",
                 f"The named mechanism ({fnames}) can cause {words}.")
    ok, bad = _numbers_ok([occ_text, esc_text], log)
    rule("numbers_come_from_evidence", ok,
         f"These numbers do not appear in any evidence: {bad}. Quote tool results only.",
         "Every percentage, lot count, °C, σ and rate matches a tool result.")
    return res


def verdict(results: list[dict]) -> str:
    return "APPROVED_FOR_REVIEW" if all(r["passed"] for r in results) else "REJECTED"
