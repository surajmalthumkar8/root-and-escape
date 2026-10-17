"""The Challenger: deterministic rules that every draft 8D must pass before a person can approve it.

Two jobs:
1. Screen hypotheses. Every segment that looks significant on its own is ruled SUPPORTED, EXPLAINED,
   REJECTED or OPEN. People-related hypotheses (crew, shift) are always stratified first.
2. Check the draft against the 8D rules below. Every rejection is shown in the UI.
"""
from __future__ import annotations

import re
import unicodedata

from . import analytics as A
from .tools import EvidenceLog, Toolbox

PEOPLE_RE = re.compile(
    r"\b(operators?|crews?|human|humans|people|persons?|personnel|employees?|workers?|staff|inspectors?|inspector error"
    r"|teams?|setters?|technicians?|line leaders?|supervisors?|foreman|foremen|new hires?|trainees?"
    r"|mistakes?|forgot|forgotten|forgetting|negligen\w*|careless\w*|retrain\w*|re-train\w*|training"
    r"|night[- ]?shifts?|nights|shifts?\s+[DNS]|crews?\s+[A-Z]|temps?|agency|contractors?|management|managers?"
    r"|engineers?|techs?|op error|reckless|incompetent|lazy|sloppy|poor judge?ment|guys|workforce|manpower|labou?r"
    r"|(?:overnight|late|evening|graveyard|swing|early|morning|afternoon)[- ]?(?:shifts?|rotations?)|[A-Z]-shifts?|Man\s*[:\-–]"
    r"|new (?:guys|people|hires?|starters?|joiners?)|rotations?)\b"
    r"|作業者|作業員|担当者|検査員|オペレーター|人為|夜勤|班|教育不足|ミス|うっかり|不注意|見落とし|さん|様|氏|派遣|人員|作業ミス",
    re.I,
)
_NOT_A_NAME = (r"(?:Inspection|Sampling|Press|Dryer|Mould|Mold|Machine|Gauge|Line|Plant|Process|Production|System|Control"
               r"|The|It|This|That|Resin|Escape|Occurrence|Cavity|Tool|Quality|Containment|Customer|Data|Evidence|Root)")
# blame without a role word: a pronoun or a name doing something wrong ("He left the dryer off", "Tanaka loaded wet resin")
PRONOUN_BLAME_RE = re.compile(
    r"\b(?:he|she|they|someone|somebody|whoever)\b(?:\s+\w+){0,3}?\s+(?:left|forgot|skipped|ignored|loaded|turned|"
    r"failed to|didn't|did not|neglected|bypassed|switched|set|changed|overrode|disabled)\b", re.I)
ACTOR_BLAME_RE = re.compile(
    r"\b(?!" + _NOT_A_NAME + r"\b)[A-Z][a-z]{2,}(?:-san|-sama)?\s+(?:in|from)\s+(?:maintenance|quality|production|engineering)\b"
    r"|\b(?!" + _NOT_A_NAME + r"\b)[A-Z][a-z]{2,}(?:-san|-sama)?\s+(?:loaded|left|forgot|skipped|ignored|bypassed|neglected"
    r"|disabled|overrode|didn't|did not)\b"
    r"|\b[A-Z][a-z]+-(?:san|sama|kun)\b")
DEVICE_AFTER_RE = re.compile(r"^\s*(?:panel|interface|screen|hmi|console|display|terminal|station|alarm)\b", re.I)
# A people word is not blame when the clause clears them: "not a people cause", "the crew difference disappears
# inside every press", "班の差は消える". Blame stays blame: "crew B did not follow the instruction".
EXONERATE_BEFORE_RE = re.compile(r"(\bnot\s+(?:(?:a|an|the|due to|caused by|from|by|about|related to)\s+)*|\bunrelated to\s+|\bno\s+|\brather than\s+"
                                 r"|\b(?:isn't|is not|wasn't|was not|aren't|are not|weren't|were not)\s+(?:(?:a|an|the|due to|caused by)\s+)*"
                                 r"|\bruled out\s+(?:the\s+)?|\bunlike\s+(?:the\s+)?|\bexcept\s+(?:the\s+)?)$", re.I)
EXONERATE_AFTER_RE = re.compile(r"\b(differences?|effects?|gaps?)\s+(disappears?|vanish(?:es)?|goes away|is explained)\b"
                                r"|\bdisappears?\b|\bruled out\b|\brejected\b|\bconfounded\b|\bnot (?:the|a) cause\b"
                                r"|\bnot responsible\b|\bnot to blame\b|\bwas cleared\b|\bnot at fault\b|\bblameless\b|\bno fault\b"
                                r"|\b(?:were|was|are|is)\s+not\s+(?:at fault|to blame|the cause|a cause|responsible)\b"
                                r"|\b(?:is|are|was|were)\s+not\s+significant\b|\bnot significant\b"
                                r"|\bonly looked worse\b|\blooked worse (?:only )?because\b|\bjust works\b|\bworks (?:on )?press\b"
                                r"|\bis confounded\b|\bwas confounded\b|\balike\b|\bequally\b|\bacross (?:all )?shifts\b"
                                r"|浮かび上がらない|見られない|有意でない|有意差なし"
                                r"|消え|ではない|ではなく|否定|目立たず|目立たない|差はない|差がない", re.I)
DETECTION_RE = re.compile(
    r"sampl|inspect|gauge|calibrat|control plan|check|detect|test|audit|\bQC\b|quality control|\bAQL\b"
    r"|検査|ゲージ|校正|抜き取り|管理計画|検出|品質管理",
    re.I,
)
# What kind of detection weakness an escape statement claims, so it can be checked against the inspection records.
ESC_SAMPLING_RE = re.compile(r"sampl|frequency|per hour|an hour|/\s?h\b|control plan|抜き取り|頻度|管理計画", re.I)
ESC_GAUGE_RE = re.compile(r"gauge|calibrat|\bG-\d+|ゲージ|校正", re.I)
ESC_OPEN_RE = re.compile(r"not yet identified|not identified|not yet confirmed|not confirmed|未特定|未確定|\bunknown\b"
                         r"|not yet known|\bTBD\b|to be determined|pending|under investigation|調査中", re.I)
GAUGE_ID_RE = re.compile(r"\bG-\d+\b", re.I)
# How an occurrence statement names a mechanism, keyed like A.MECHANISM_EFFECTS.
MECHANISM_RE = {
    "dryer_dew_point_c": re.compile(r"dryer|dew[- ]?point|moisture|wet resin|乾燥|露点|吸湿", re.I),
    "mold_shots_since_pm": re.compile(r"\bmou?lds?\b[^.;]*\b(wear|wore|worn|maintenance|shots?)\b"
                                      r"|\b(worn|wearing|worn-out|damaged)\s+mou?lds?\b|maintenance limit|parting[- ]lines?"
                                      r"|\btool(?:ing)?\b[^.;]*\b(wear|wore|worn|PM|shot count|maintenance)\b|\bPM\b[^.;]*\bshot"
                                      r"|摩耗|保全上限|パーティング", re.I),
    "melt_temp_c": re.compile(r"melt temp|溶融温度|樹脂温度", re.I),
    "hold_pressure_bar": re.compile(r"hold(ing)? pressure|packing pressure|保圧", re.I),
    "cycle_time_s": re.compile(r"cycle time|cooling time|サイクルタイム|冷却時間", re.I),
}
# causes the plant data doesn't log at all: they can't be confirmed from the evidence, so they can't be the stated cause
UNLOGGED_RE = re.compile(r"hydraulic|(?<!dryer )heater|\bleak(?:s|ed|ing)?\b|contaminat|off[- ]spec|foreign (?:object|material|matter)"
                         r"|wrench|dropped|supplier|grade change|material change|power (?:cut|outage|failure)|static|humidity"
                         r"|conveyor|guide rails?|handling damage|transit damage|injection (?:pressure|speed)|screw|nozzle"
                         r"|mou?ld temp(?:erature)?|regrind|masterbatch|colou?rant|cooling water"
                         r"|油漏れ|異物|ヒーター|コンベア|ガイド|射出圧", re.I)
LEAD_RE = re.compile(r"\b(check|verify|investigate|rule out|next|collect|ask|confirm whether|to be checked)\b|確認|調査", re.I)
MECHANISM_NAMES = {"dryer_dew_point_c": "wet resin (dryer)", "mold_shots_since_pm": "a worn mould",
                   "melt_temp_c": "melt temperature", "hold_pressure_bar": "hold pressure", "cycle_time_s": "cycle time"}
UNCONFIRMED_RE = re.compile(r"not yet confirmed|not confirmed|未確定", re.I)
CERTAINTY_RE = re.compile(r"\b(certain|definitely|clearly|obviously|we know|no doubt|sure)\b|確実|間違いなく", re.I)
INSUFFICIENT_RE = re.compile(
    r"insufficient (evidence|data)|not enough (evidence|data)|inconclusive|no (clear )?signal|no (root )?cause "
    r"(found|stands out|is supported|can be (identified|found|named)|could be (identified|found))|root cause (is )?unknown"
    r"|cannot (be )?(identified|determined)|can't (be )?(identified|determined)|can(?:no|')t name a (root )?cause"
    r"|unable to (identify|name|determine)|not (yet )?(possible|able) to (identify|name)|原因不明"
    r"|(?:root )?cause (?:is )?not (?:yet )?determined|can(?:no|')t pin|cannot pin|no segment passes"
    r"|証拠不十分|根拠不十分|データ不足|特定できない|特定に至らず", re.I)
CAUSAL_CLAIM_RE = re.compile(r"(?<!not )(?<!n't )\b(likely|probably|caused by|due to|because of|the cause is|root cause is"
                             r"|responsible for|(?:is|are) (?:scratching|damaging|causing|cracking))\b"
                             r"|が原因|のせい|原因である|可能性が高い|と思われる|と考えられる", re.I)

# ---- numbers. Every number a statement quotes with a unit must appear in a tool result (or in the complaint).
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?(?:%|percent\b|per cent\b|パーセント)", re.I)
LOTS_RE = re.compile(r"(\d[\d,]*)\s*(?:lots?\b|ロット)", re.I)
_Q = r"(?<![A-Za-z0-9.])([-−+]?\d[\d,]*(?:\.\d+)?)[\s-]?"  # "P4 parts" is not a count of 4
UNIT_RES = [  # (pattern in a statement, which pool of evidence numbers it must match)
    (re.compile(_Q + r"(?:°\s?C|℃)", re.I), "process"),
    (re.compile(_Q + r"(?:°\s?F|℉)", re.I), "never"),  # the plant logs °C only
    (re.compile(_Q + r"σ"), "sigma"),
    (re.compile(_Q + r"(?:/\s?h\b|per hour\b|an hour\b)", re.I), "per_h"),
    (re.compile(_Q + r"shots?\b", re.I), "shots"),
    (re.compile(_Q + r"days?\b", re.I), "days"),
    (re.compile(_Q + r"(?:weeks?|hours?|minutes?|months?)\b", re.I), "never"),
    (re.compile(_Q + r"(?:per\s+shift|/\s?shift|/\s?min\b|per\s+minute|/\s?day\b|per\s+day)", re.I), "never"),
    (re.compile(_Q + r"(?:[A-Za-z]+\s+){0,2}?(?:parts?|pieces?|pcs|units?|clips?)\b", re.I), "parts"),
    (re.compile(_Q + r"(?:bar|mm|ppm|per\s+thousand|‰)\b|" + _Q + r"‰", re.I), "process"),
    (re.compile(_Q + r"(?:fold|times)\b|(?:×|(?<![A-Za-z0-9])x)\s?(\d+(?:\.\d+)?)\b", re.I), "ratio"),
    (re.compile(r"\bz\s?=?\s?(\d+(?:\.\d+)?)"), "z"),
]
POOL_RES = {
    "sigma": re.compile(r"(\d+(?:\.\d+)?)σ"),
    "per_h": re.compile(r"(\d+)/h\b"),
    "shots": re.compile(r"(\d[\d,]*)(?:-shot|\s+shots?)\b"),
    "days": re.compile(r"(\d+)\s+days?\b"),
    "parts": re.compile(r"(\d[\d,]*)\s+parts?\b"),
    "ratio": re.compile(r"×\s?(\d+(?:\.\d+)?)"),
    "z": re.compile(r"\bz\s(\d+(?:\.\d+)?)"),
}
OF_RE = re.compile(r"(\d[\d,]*)\s+(?:of|out of)\s+(\d[\d,]*)", re.I)
NUM_RE = re.compile(r"[-−]?\d[\d,]*(?:\.\d+)?")
# dates, times and IDs inside evidence are not quantities ("2026-10-02" must not supply a "-2 °C")
NOT_A_QUANTITY_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}:\d{2}(?::\d{2})?\b|\b\d*[A-Z]{1,3}-\d[\d-]*\b")
_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_WORDS.update({w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())})
_NUMWORD = r"(?:" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r"|hundred|thousand|point|half|and|a)"
SPELLED_RE = re.compile(rf"\b({_NUMWORD}(?:[\s-]+{_NUMWORD})*)[\s-]*(percent|per cent|per hour|an hour|fold|times)\b", re.I)


def _words_to_number(words: str) -> float | None:
    toks = [t for t in re.split(r"[\s-]+", words.lower()) if t and t not in ("and", "a")]
    if not toks:
        return None
    if toks == ["half"]:
        return 0.5
    total, cur, frac, in_frac = 0.0, 0.0, "", False
    for t in toks:
        if t == "point":
            in_frac = True
        elif in_frac and t in _WORDS and _WORDS[t] < 10:
            frac += str(_WORDS[t])
        elif t in _WORDS:
            cur += _WORDS[t]
        elif t == "hundred":
            cur = (cur or 1) * 100
        elif t == "thousand":
            total, cur = total + (cur or 1) * 1000, 0.0
        elif t == "half":
            cur += 0.5
        else:
            return None
    return total + cur + (float("0." + frac) if frac else 0.0)


# Ways a segment value is written: P4, press 4, プレス4, 4号機; C3, cavity 3, キャビティ3; shift N; crew B; R-2291.
SEGMENT_RES = {
    "press": re.compile(r"\bpress(?:es)?[\s-]*(?:no\.?|number|#)?\s*P?-?(\d)\b|(?<![A-Za-z0-9])P-?(\d)(?![0-9])"
                        r"|\bmachine\s*#?\s*(\d)\b|(?<![\w#])(?:no\.?|number)\s*(\d)\s+press\b|#\s*(\d)\s+press\b"
                        r"|プレス\s*P?(\d)|(\d)\s*号機|\bpress\s+(one|two|three|four|five|six)\b", re.I),
    "cavity": re.compile(r"\bcav(?:ity|ities|\.)?[\s-]*(?:no\.?|number|#)?\s*C?-?(\d)\b|(?<![A-Za-z0-9°])C-?(\d)(?![0-9])|キャビティ\s*C?(\d)"
                         r"|\bcavity\s+(one|two|three|four)\b", re.I),
    "shift": re.compile(r"\bshifts?\s+([DNS])\b"),
    "crew": re.compile(r"\bcrews?\s+([A-Z])\b"),
    "resin_lot": re.compile(r"\b(R-\d{3,5})\b"),
}
SEGMENT_PREFIX = {"press": "P", "cavity": "C", "shift": "", "crew": "", "resin_lot": ""}
NEGATION_RE = re.compile(r"\b(not|no|never|unlike|except|excluding|other than|rather than)\b|n't\b|ではなく|以外", re.I)
CLAUSE_RE = r"[,;:!?。、]|\.(?!\d)"  # a decimal point (6.75) is not a clause break
SENTENCE_RE = r"[!?。]|\.(?!\d)"


_HOMOGLYPHS = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ѕ": "s",
                             "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "Т": "T", "Н": "H", "К": "K", "М": "M",
                             "ο": "o", "α": "a", "ε": "e", "ρ": "p", "τ": "t", "ν": "v", "Ο": "O", "Α": "A"})


def normalize(text: str) -> str:
    """Undo disguises before any rule reads the text: full-width digits (NFKC), zero-width characters, Cyrillic or
    Greek look-alike letters, "0perat0r", and "o-p-e-r-a-t-o-r"."""
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"[\u200b-\u200f\u2060\u00ad\ufeff]", "", t).translate(_HOMOGLYPHS)
    t = re.sub(r"\b[A-Za-z0]*0[A-Za-z0]*\b", lambda m: m.group(0).replace("0", "o")
               if sum(c.isalpha() for c in m.group(0)) >= 3 else m.group(0), t)
    t = re.sub(r"\b(?:[A-Za-z][-.\s]){3,}[A-Za-z]\b", lambda m: re.sub(r"[-.\s]", "", m.group(0)), t)
    return t


def segment_mentions(text: str) -> dict[str, set]:
    """Segment values a statement names, ignoring negated ones ("did not come from P4", "unlike P1")."""
    text = text or ""
    out: dict[str, set] = {}
    for dim, rx in SEGMENT_RES.items():
        for m in rx.finditer(text):
            before = re.split(CLAUSE_RE, text[max(0, m.start() - 30):m.start()])[-1]  # negation stops at a clause break
            before = UNCONFIRMED_RE.sub("", before)  # "not yet confirmed on press P4" still names P4
            before = " ".join(before.split()[-3:])  # "did not come from P4" negates; "wasn't dried properly on press P4" doesn't
            if NEGATION_RE.search(before):
                continue
            val = next(g for g in m.groups() if g)
            val = str(_WORDS.get(val.lower(), val)) if val.isalpha() and dim in ("press", "cavity") else val
            out.setdefault(dim, set()).add(f"{SEGMENT_PREFIX[dim]}{val}" if not val.startswith(SEGMENT_PREFIX[dim]) else val)
    return out


def names_segment(text: str, seg: dict) -> tuple[bool, str]:
    """True if the statement names every part of `seg`, no other value of those dimensions, and no press, cavity or
    resin lot that the analysis didn't find ("only cavity C2 of press P4" narrows to something unsupported)."""
    m = segment_mentions(text)
    missing = [f"{k} {v}" for k, v in seg.items() if v not in m.get(k, set())]
    other = sorted(f"{k} {x}" for k, vals in m.items() for x in vals
                   if (k in seg and x != seg[k]) or (k not in seg and k not in A.PEOPLE_DIMS))
    if missing:
        return False, f"it doesn't name {', '.join(missing)}"
    if other:
        return False, f"it also names {', '.join(other)}, which the analysis did not find"
    return True, ""


def blames_people(text: str) -> bool:
    """True if any people word in the text is used as a cause, not cleared ("not a people cause")."""
    text = normalize(text)
    if ACTOR_BLAME_RE.search(text) or PRONOUN_BLAME_RE.search(text):
        return True
    for m in PEOPLE_RE.finditer(text):
        if DEVICE_AFTER_RE.search(text[m.end():m.end() + 20]):
            continue  # "the operator panel" is a device, not a person
        before = re.split(CLAUSE_RE, text[max(0, m.start() - 40):m.start()])[-1]
        after = re.split(CLAUSE_RE, text[m.end():m.end() + 70])[0]
        sent_before = re.split(SENTENCE_RE, text[:m.start()])[-1]
        sent_after = re.split(SENTENCE_RE, text[m.end():])[0]
        if EXONERATE_BEFORE_RE.search(before) or EXONERATE_AFTER_RE.search(after) or EXONERATE_AFTER_RE.search(sent_after):
            continue
        if re.search(r"\b(nothing|none|no \w+)\b[^.]*\b(stand(?:s|ing)? out|stood out|differs?|is elevated)\b", sent_before + " " + sent_after, re.I):
            continue  # "nothing stands out by press, crew or shift"
        if re.search(r"\b(no|none|neither|nor)\b", sent_before, re.I) and \
                re.search(r"\bstands? out\b|\bstood out\b|\bis elevated\b|\bdiffers?\b|\bexplains?\b|目立", sent_after, re.I):
            continue  # "no press, cavity, shift or crew stands out"
        return True
    return False


def mechanisms_named(text: str) -> list[str]:
    return [k for k, rx in MECHANISM_RE.items() if rx.search(text or "")]


def unlogged_causes(text: str) -> list[str]:
    """Causes the plant data doesn't log (a hydraulic leak, a supplier change...), unless the clause only proposes
    checking them next ("next, check the resin supplier")."""
    out = []
    for m in UNLOGGED_RE.finditer(text or ""):
        clause = re.split(CLAUSE_RE, (text or "")[max(0, m.start() - 50):m.start()])[-1]
        if not LEAD_RE.search(clause):
            out.append(m.group(0).lower())
    return sorted(set(out))


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


def _quantities(text: str) -> list[float]:
    clean = NOT_A_QUANTITY_RE.sub(" ", text or "")
    return [float(x.replace("−", "-").replace(",", "")) for x in NUM_RE.findall(clean)]


DEFECT_COUNT_RE = re.compile(r"(\d[\d,]*)\s+(?:lots?|parts?|pieces?)\s+(?:were|are|was|got)\s+"
                             r"(?:burred|cracked|scratched|defective|bad|damaged|affected|faulty)\b", re.I)
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
REV_RE = re.compile(r"\brev(?:ision)?\.?\s*([A-Z])(?:\s*(?:→|->|to)\s*([A-Z]))?\b")
CAPABILITY_RE = re.compile(r"\b(?:Cpk|Ppk|Cp|Pp)\b[^;]{0,60}?\d")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
KANJI_PCT_RE = re.compile(r"([〇一二三四五六七八九十百]+)\s*(?:パーセント|%)")
_KANJI = {c: i for i, c in enumerate("〇一二三四五六七八九")}


def _kanji_to_number(k: str) -> float | None:
    total, cur = 0, 0
    for c in k:
        if c in _KANJI:
            cur = _KANJI[c]
        elif c == "十":
            total, cur = total + (cur or 1) * 10, 0
        elif c == "百":
            total, cur = total + (cur or 1) * 100, 0
        else:
            return None
    return float(total + cur)


def _numbers_ok(texts: list[str], log: EvidenceLog, sources: tuple[str, ...] = ()) -> tuple[bool, list[str]]:
    """Every number a statement quotes with a unit must match a tool result that quotes the same kind of number:
    %, lots, °C (logged process values), σ, per-hour rates, shots, days, part counts, × ratios, z scores, "N of M",
    and spelled-out forms such as "nine point five percent". Part counts and "N of M" may also come from the
    complaint itself (`sources`). °F, weeks, hours and months never appear in the evidence, so they never match."""
    pcts, lots = log.percentages(), set(log.lot_counts())
    summaries = [NOT_A_QUANTITY_RE.sub(" ", i.get("summary") or "") for i in log.items]
    num = lambda x: float(str(x).replace("−", "-").replace("+", "").replace(",", ""))  # noqa: E731
    pools = {k: [num(x) for t in summaries for x in rx.findall(t)] for k, rx in POOL_RES.items()}
    pools["process"] = [num(x) for i, t in zip(log.items, summaries) if i["tool"] == "param_excursions" for x in NUM_RE.findall(t)]
    pools["never"] = []
    told = [x for t in sources for x in _quantities(t)]
    pools["parts"] += told
    every = [x for t in summaries for x in _quantities(t)]

    def known(x: float, pool: list[float], raw: str = "") -> bool:
        """'4.3' matches 4.33 (rounded to what was quoted); '4.38' or '0.35' does not match 4.33 or 0.39."""
        d = len(raw.split(".")[1]) if "." in raw else 0
        return any(round(e, d) == round(x, d) for e in pool) if raw else any(abs(x - e) <= 0.005 for e in pool)

    dates_known = " ".join([i.get("summary") or "" for i in log.items] + list(sources))

    bad = []
    for t in texts:
        t = normalize(t)
        for m in DEFECT_COUNT_RE.finditer(t):  # the evidence counts lots held, never lots found defective
            bad.append(m.group(0))
        for tm in TIME_RE.findall(t):  # a clock time must appear in the evidence
            if tm not in dates_known:
                bad.append(tm)
        known_revs = {m2.groups() for m2 in REV_RE.finditer(dates_known)}
        for m2 in REV_RE.finditer(t):  # control-plan revisions must match the records ("rev B → C")
            a2, b2 = m2.groups()
            if (b2 and (a2, b2) not in known_revs) or (not b2 and not any(a2 in r2 for r2 in known_revs)):
                bad.append(m2.group(0))
        for m2 in CAPABILITY_RE.finditer(t):  # no capability study is in the evidence
            bad.append(m2.group(0))
        for d in DATE_RE.findall(t):  # a date must be one the evidence, the complaint or the date codes contain
            if d not in dates_known:
                bad.append(d)
        for m in KANJI_PCT_RE.finditer(t):
            x = _kanji_to_number(m.group(1))
            if x is not None and not known(x, pcts, str(x)):
                bad.append(m.group(0))
        t = NOT_A_QUANTITY_RE.sub(" ", t)
        for m in PCT_RE.finditer(t):
            if not known(float(m.group(1)), pcts, m.group(1)):
                bad.append(m.group(0))
        for m in LOTS_RE.finditer(t):
            if int(m.group(1).replace(",", "")) not in lots:
                bad.append(m.group(0))
        for rx, pool in UNIT_RES:
            for m in rx.finditer(t):
                raw = next(g for g in m.groups() if g)
                x = num(raw)
                if raw.lstrip().startswith(("-", "−")):
                    x = -abs(x)
                elif pool in ("sigma", "ratio", "z"):
                    x = abs(x)
                if not known(x, pools[pool], raw.replace(",", "")):
                    bad.append(m.group(0).strip())
        for m in OF_RE.finditer(t):
            if not all(known(num(g), every + told, g.replace(",", "")) for g in m.groups()):
                bad.append(m.group(0))
        for m in SPELLED_RE.finditer(t):
            x = _words_to_number(m.group(1))
            if x is None:
                continue
            unit = m.group(2).lower()
            pool = pcts if "cent" in unit else pools["per_h"] if "hour" in unit else pools["ratio"]
            if not known(x, pool, f"{x:g}"):
                bad.append(m.group(0))
    return (not bad), bad


def check_draft(draft: dict, tb: Toolbox) -> list[dict]:
    log, r = tb.log, tb.last_is_is_not
    res: list[dict] = []

    def rule(name: str, ok: bool, msg_fail: str, msg_ok: str = "") -> None:
        res.append({"rule": name, "passed": bool(ok), "message": msg_ok if ok else msg_fail})

    case = getattr(tb, "case", None) or {}
    complaint = (case.get("complaint") or "", f"{case.get('date_code_start', '')} {case.get('date_code_end', '')}")
    insufficient = bool(draft.get("insufficient_evidence"))
    occ, esc = draft.get("occurrence") or {}, draft.get("escape") or {}
    occ_text, esc_text = normalize(occ.get("statement") or ""), normalize(esc.get("statement") or "")
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
        if occ_text:  # an abstention may not smuggle in a cause
            named = sorted(f"{k} {v}" for k, vs in segment_mentions(occ_text).items() for v in vs if k not in A.PEOPLE_DIMS)
            mech = [MECHANISM_NAMES[k] for k in mechanisms_named(occ_text)] + unlogged_causes(f"{occ_text} {esc_text}")
            people = blames_people(occ_text) or blames_people(esc_text)
            causal = bool(CAUSAL_CLAIM_RE.search(f"{occ_text} {esc_text}"))
            found = named + mech + (["people"] if people else []) + (["a cause stated as likely"] if causal else [])
            rule("abstention_names_no_cause", not found,
                 "An 'insufficient evidence' answer can't also name a cause" + (f" ({', '.join(found)})." if found else "."),
                 "The draft abstains without naming a cause.")
            ok, bad = _numbers_ok([occ_text, esc_text], log, complaint)
            rule("numbers_come_from_evidence", ok, f"These numbers do not appear in any evidence: {bad}.",
                 "Every quoted number matches a tool result.")
        return res

    rule("requires_occurrence_and_escape", bool(occ_text) and bool(esc_text),
         "An 8D needs two root causes: why the defect happened (occurrence) and why it was not caught (escape).",
         "Both occurrence and escape causes are present.")
    cited = set(occ.get("evidence") or []) | set(esc.get("evidence") or [])
    missing = sorted(cited - log.ids())
    rule("evidence_must_resolve", bool(occ.get("evidence")) and bool(esc.get("evidence")) and not missing,
         f"Every cause must cite evidence that exists. Missing or unknown: {missing or 'no citations'}.",
         "Every cited evidence ID resolves to a stored tool result.")
    rule("escape_must_explain_detection", bool(DETECTION_RE.search(esc_text)) or bool(ESC_OPEN_RE.search(esc_text)),
         "The escape cause must explain why inspection or detection did not catch the defect.",
         "The escape cause names a detection weakness.")
    press = (r["primary_segment"] or {}).get("press")
    if esc_text:
        insp = [i for i in log.items if i["tool"] == "inspection_changes" and (press is None or i["args"].get("press") == press)]
        has_sampling = any((i["data"] or {}).get("sampling_changes") for i in insp)
        gauges = {((i["data"] or {}).get("gauge_overdue") or {}).get("gauge_id") for i in insp} - {None}
        claims_sampling, claims_gauge = bool(ESC_SAMPLING_RE.search(esc_text)), bool(ESC_GAUGE_RE.search(esc_text))
        is_open = bool(ESC_OPEN_RE.search(esc_text))
        if is_open:  # "not yet identified; the records show no sampling or gauge change" describes the records
            claims_sampling = claims_gauge = False
        other_press = sorted(segment_mentions(esc_text).get("press", set()) - ({press} if press else set()))
        wrong_gauge = sorted({g.upper() for g in GAUGE_ID_RE.findall(esc_text)} - gauges)
        problems = []
        if other_press:
            problems.append(f"it names {', '.join(other_press)} instead of the analysed press")
        if claims_sampling and not has_sampling:
            problems.append("the inspection records show no sampling change there")
        changes = [c for i in insp for c in ((i["data"] or {}).get("sampling_changes") or [])]
        mdir = re.search(r"from\s+(\d+)\s*/\s*h\s+to\s+(\d+)\s*/\s*h", esc_text, re.I)
        if claims_sampling and changes and ((mdir and not any((int(mdir.group(1)), int(mdir.group(2))) ==
                                                              (c["sampling_from"], c["sampling_to"]) for c in changes))
                                            or re.search(r"\b(increased|raised|doubled|tightened)\b", esc_text, re.I)):
            problems.append("the records show sampling was cut, not changed that way (more sampling is not a reason it escaped)")
        if claims_gauge and (not gauges or wrong_gauge):
            problems.append(f"the inspection records show no overdue gauge {' '.join(wrong_gauge)}".rstrip())
        if not (claims_sampling or claims_gauge or is_open):
            problems.append("it names no detection weakness the records could confirm (a sampling change or an "
                            "overdue gauge), and doesn't say the escape point is not yet identified")
        rule("escape_in_evidence", not problems,
             "The escape cause isn't backed by the inspection records: " + "; ".join(problems) + ".",
             "The escape cause matches the inspection records, or is honestly marked as not yet identified.")
    if blames_people(occ_text) or blames_people(esc_text):
        support = [i for i in log.items if i["tool"] == "stratify" and i["args"].get("by") in A.PEOPLE_DIMS
                   and i["data"].get("effect_persists_within") and not i["data"].get("confounded")
                   and i["id"] in cited]
        rule("no_people_blame_without_stratified_effect", bool(support),
             "This blames people, but the split by press doesn't back it. A cause about people, in the occurrence "
             "or the escape, needs an effect that survives inside each press, and the evidence doesn't show one.",
             "The people-related cause is backed by an effect that persists after stratification.")
    if r["primary_segment"]:
        seg = occ.get("segment") or {}
        ok_fields = bool(seg) and (_subset(r["primary_segment"], seg) or _subset(seg, r["primary_segment"]))
        ok_text, why = names_segment(occ_text, r["primary_segment"])
        rule("segment_matches_is_is_not", ok_fields and ok_text,
             f"The occurrence must name the segment the is / is-not analysis found ({r['primary_label']})"
             + (f", but {why}." if why else "."),
             f"The occurrence names the analysed segment ({r['primary_label']}).")
    named = mechanisms_named(occ_text)
    is_open = bool(UNCONFIRMED_RE.search(occ_text)) and not CERTAINTY_RE.search(occ_text)  # "not confirmed, but certain"
    rule("mechanism_named_or_open", bool(named) or is_open,
         "Name the physical mechanism the process data shows, or say the mechanism is not yet confirmed. "
         "A cause with no mechanism can't be checked.",
         "The occurrence names a mechanism or marks it as not yet confirmed.")
    unlogged = unlogged_causes(occ_text)
    if unlogged:
        rule("cause_is_in_the_plant_data", False,
             f"The claim names {', '.join(unlogged)}, which the plant data doesn't record, so it can't be confirmed. "
             "State it as something to check next, not as the cause.")
    if named:
        flagged = {f for i in log.items if i["tool"] == "param_excursions"
                   and (press is None or i["args"].get("press") == press) for f in (i["data"].get("flagged") or [])}
        absent = [k for k in named if k not in flagged]
        names = " and ".join(MECHANISM_NAMES[k] for k in absent or named)
        rule("mechanism_in_evidence", not absent,
             f"The process data{(' for press ' + press) if press else ''} does not show {names}. "
             "Name only a mechanism that the evidence flags.",
             f"The process data flags the named mechanism: {names}.")
        defect = getattr(tb, "defect", None)
        if defect:
            words = A.DEFECT_WORDS.get(defect, defect)
            unfit = [k for k in named if defect not in A.MECHANISM_DEFECTS[k]]
            fnames = " and ".join(MECHANISM_NAMES[k] for k in unfit or named)
            rule("mechanism_fits_defect", not unfit,
                 f"{fnames[0].upper() + fnames[1:]} does not usually cause {words}. Name a mechanism that can produce "
                 f"{words}, or say the mechanism is not yet confirmed.",
                 f"The named mechanism can cause {words}: {fnames}.")
    ok, bad = _numbers_ok([occ_text, esc_text], log, complaint)
    rule("numbers_come_from_evidence", ok,
         f"These numbers do not appear in any evidence: {bad}. Quote tool results only.",
         "Every quoted number (%, lots, °C, σ, rates, counts) matches a tool result.")
    return res


def verdict(results: list[dict]) -> str:
    return "APPROVED_FOR_REVIEW" if all(r["passed"] for r in results) else "REJECTED"
