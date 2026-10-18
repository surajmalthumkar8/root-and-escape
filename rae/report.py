"""Deterministic investigation policy and 8D report templates (EN + JA).

This is the fallback when Gemini is unavailable (labelled "deterministic mode" in the UI). It is also the
reference the benchmark checks against. In Gemini mode the agents plan and write; these templates are only
used when a Gemini step fails.
"""
from __future__ import annotations

from . import analytics as A
from .challenger import screen_hypotheses
from .tools import Toolbox


def _when(ts: str | None) -> str:
    return (ts or "")[:16].replace("T", " ")


def mechanism(pe: dict | None, defect: str | None = None) -> dict:
    """Map flagged process data to a physical mechanism, a corrective action and past-8D keywords.
    A flagged parameter is only named as the mechanism when it is a typical cause of this defect type
    (A.MECHANISM_DEFECTS); otherwise the mechanism is reported as not yet confirmed."""
    if not pe:
        return {"kind": "unknown", "text": "", "action": "", "keywords": []}
    data = {p["param"]: p for p in pe["data"]["params"]}
    flagged = pe["data"]["flagged"]
    press = pe["data"]["press"]

    def fits(param: str) -> bool:  # a typical cause of this defect, so it can be named as the mechanism
        return defect is None or defect in A.MECHANISM_EFFECTS.get(param, {}).get("typical", set())

    words, words_ja = A.DEFECT_WORDS.get(defect, "defects"), A.DEFECT_JA.get(defect, "不良")
    if "dryer_dew_point_c" in flagged and fits("dryer_dew_point_c"):
        d = data["dryer_dew_point_c"]
        over = d.get("spec_max") is not None and d["test_mean"] > d["spec_max"]
        spec = f", above the resin maker's limit of {d['spec_max']} °C" if over else ""
        spec_ja = f"、樹脂メーカー上限 {d['spec_max']} °C 超" if over else ""
        return {"kind": "dryer", "keywords": ["dryer", "moisture", "dew"],
                "text": (f"the resin dryer on press {press} failed: dew point averaged {d['test_mean']} °C against a normal "
                         f"{d['control_mean']} °C ({d['shift_sigma']}σ{spec}) from {_when(d['first_out'])} to "
                         f"{_when(d['last_out'])}, so the resin was wet, degraded in the barrel, and the parts became brittle"),
                "text_ja": (f"プレス {press} の樹脂乾燥機が故障し、露点が平常 {d['control_mean']} °C に対し平均 {d['test_mean']} °C"
                            f"（{d['shift_sigma']}σ{spec_ja}）となった（{_when(d['first_out'])}〜{_when(d['last_out'])}）。"
                            f"吸湿した樹脂が成形中に劣化し、成形品が脆くなった"),
                "action": f"Fit a dew-point interlock on the press {press} dryer (stop resin feed above -35 °C) and repair the dryer",
                "action_ja": f"プレス {press} の乾燥機に露点インターロックを設置し（-35 °C 超で樹脂供給を停止）、乾燥機を修理する"}
    if "mold_shots_since_pm" in flagged and fits("mold_shots_since_pm"):
        pm = pe["data"]["pm"]
        return {"kind": "mould_wear", "keywords": ["pm", "wear", "cavity", "parting"],
                "text": (f"the press {press} mould ran past its {pm['limit']:,}-shot maintenance limit from "
                         f"{_when(pm['first_over_limit'])} (up to {pm['max_shots']:,} shots), so the cavity wore and flashed"),
                "text_ja": (f"プレス {press} の金型が {_when(pm['first_over_limit'])} から保全上限 {pm['limit']:,} ショットを超えて稼働し"
                            f"（最大 {pm['max_shots']:,} ショット）、キャビティが摩耗してバリが発生した"),
                "action": f"Repair the worn cavity, then add a maintenance interlock that stops press {press} at {pm['limit']:,} shots",
                "action_ja": f"摩耗したキャビティを修理し、プレス {press} を {pm['limit']:,} ショットで停止する保全インターロックを追加する"}
    for p in pe["data"]["params"]:
        if p["flag"] and fits(p["param"]):
            s = p["shift_sigma"]
            way, way_ja = ("above", "高い") if s > 0 else ("below", "低い")
            name, _, unit = p["label"].partition(" (")
            unit = unit.rstrip(")")
            return {"kind": "param", "keywords": [p["param"].split("_")[0]],
                    "text": (f"{name} on press {press} averaged {p['test_mean']} {unit} against a normal "
                             f"{p['control_mean']} {unit}, {abs(s)}σ {way} normal, from {_when(p['first_out'])} to {_when(p['last_out'])}"),
                    "text_ja": (f"プレス {press} の {name} が平均 {p['test_mean']} {unit}（平常 {p['control_mean']} {unit}）で、"
                                f"平常より {abs(s)}σ {way_ja}（{_when(p['first_out'])}〜{_when(p['last_out'])}）"),
                    "action": f"Restore {p['label']} on press {press} to its validated window and add an alarm",
                    "action_ja": f"プレス {press} の {p['label']} を検証済みの範囲に戻し、アラームを追加する"}
    possible = [f for f in flagged if defect in A.MECHANISM_EFFECTS.get(f, {}).get("possible", set())]
    if possible:  # a logged change that can cause this defect, but not typically: a lead, not a conclusion
        f = possible[0]
        what = ("the mould is past its maintenance limit" if f == "mold_shots_since_pm"
                else f"{A.PARAMS.get(f, f)} moved")
        what_ja = "金型が保全上限を超えている" if f == "mold_shots_since_pm" else f"{A.PARAMS.get(f, f)} が変動した"
        return {"kind": "unknown", "keywords": [f.split("_")[0]],
                "text": (f"mechanism not yet confirmed on press {press}; {what}, which can cause {words} but usually "
                         f"doesn't, so check that first"),
                "text_ja": f"プレス {press} の発生メカニズムは未確定（{what_ja}。{words_ja}の原因になり得るため、まずこれを確認する）",
                "action": f"Check whether this is the cause on press {press} ({what}) before closing D4",
                "action_ja": f"D4 を閉じる前に、プレス {press} で（{what_ja}）が原因かを確認する"}
    if flagged:  # something moved, but nothing that typically causes this defect
        return {"kind": "unknown", "keywords": [],
                "text": (f"mechanism not yet confirmed on press {press}; the process changes logged there do not "
                         f"usually cause {words}"),
                "text_ja": f"プレス {press} の発生メカニズムは未確定（記録された工程変化は通常{words_ja}の原因にならない）",
                "action": f"Find what on press {press} can cause {words} (tooling, handling, material) before closing D4",
                "action_ja": f"D4 を閉じる前に、プレス {press} で{words_ja}を起こし得る要因（金型・取り扱い・材料）を特定する"}
    return {"kind": "unknown", "keywords": [],
            "text": f"mechanism not yet confirmed on press {press}; no logged process parameter moved",
            "text_ja": f"プレス {press} の発生メカニズムは未確定（工程パラメータに変動なし）",
            "action": f"Collect more data on press {press} (material, maintenance logs, tooling) before closing D4",
            "action_ja": f"D4 を閉じる前に、プレス {press} の材料・保全記録・金型のデータを追加収集する"}


def escape_text(ic: dict | None) -> dict:
    if not ic:
        return {"text": "", "text_ja": "", "action": "", "action_ja": ""}
    d = ic["data"]
    press = d["press"]
    if d["sampling_changes"]:
        c = d["sampling_changes"][-1]
        return {"text": (f"inspection sampling on press {press} was cut from {c['sampling_from']}/h to {c['sampling_to']}/h on "
                         f"{c['date']} (control plan rev {c['rev_from']} → {c['rev_to']}), so the rising defect rate was not caught"),
                "text_ja": (f"プレス {press} の検査抜き取り頻度が {c['date']} に {c['sampling_from']}/h から {c['sampling_to']}/h に削減され"
                            f"（管理計画 Rev {c['rev_from']} → {c['rev_to']}）、不良率の上昇を検出できなかった"),
                "action": f"Restore sampling to {c['sampling_from']}/h on press {press} and require quality sign-off on control-plan changes",
                "action_ja": f"プレス {press} の抜き取りを {c['sampling_from']}/h に戻し、管理計画の変更に品質承認を必須とする"}
    if d["gauge_overdue"]:
        g = d["gauge_overdue"]
        return {"text": (f"gauge {g['gauge_id']} on press {press} was overdue for calibration since {g['cal_due']}, "
                         f"so out-of-spec parts passed inspection"),
                "text_ja": f"プレス {press} のゲージ {g['gauge_id']} は {g['cal_due']} から校正期限切れで、規格外品が検査を通過した",
                "action": f"Calibrate {g['gauge_id']} now and add a calibration-due lockout to the inspection station",
                "action_ja": f"{g['gauge_id']} を直ちに校正し、校正期限切れで検査を止めるロックアウトを追加する"}
    return {"text": (f"not yet identified; inspection on press {press} shows no change in sampling or gauge calibration, "
                     f"so why it did not detect the rise still has to be found with a detection capability check"),
            "text_ja": f"未特定（プレス {press} の検査は抜き取り・ゲージ校正とも変更なし。検出能力の確認で、検出できなかった理由を特定する）",
            "action": f"Run a detection capability check on press {press} inspection (gauge R&R and sampling plan) before closing D4",
            "action_ja": f"D4 を閉じる前に、プレス {press} の検査の検出能力（ゲージ R&R・抜き取り計画）を確認する"}


def scripted_investigation(tb: Toolbox) -> dict:
    """The investigation a careful quality engineer would run, as fixed code. Returns a draft plus hypothesis rulings.
    When the data locates the defect but doesn't show why it happened or why it escaped, the draft says so and
    lists what to check next, instead of inventing a cause."""
    r = tb.is_is_not()
    if not r["significant"]:
        tb.stratify("crew", "press")
        tb.past_8d([])
        return {
            "insufficient_evidence": True,
            "summary": (f"No press, cavity, shift, crew or resin lot explains the {tb.defect} reports: the rate in the "
                        f"complaint date codes is in line with the previous 28 days. Root cause: insufficient evidence."),
            "next_data": ["Ask the customer for part serials and photos of every affected part",
                          "Check packaging and transport handling records for the date codes",
                          "Inspect retained samples from the complaint date codes"],
            "hypotheses": [], "evidence_used": [i["id"] for i in tb.log.items],
        }
    seg = r["primary_segment"]
    is_id = tb.log.items[-1]["id"]
    press = seg.get("press")
    pe = ic = None
    if press:
        tb.param_excursions(press)
        pe = tb.log.items[-1]
        tb.inspection_changes(press)
        ic = tb.log.items[-1]
    # a segment that is a shift or crew must show its effect survives a split by press before it may be named
    people_ids = [tb.stratify(d, "press")["evidence_id"] for d in seg if d in A.PEOPLE_DIMS]
    label, label_ja = A.seg_label(seg), A.seg_label_ja(seg)
    if press:
        mech, esc = mechanism(pe, tb.defect), escape_text(ic)
    else:  # e.g. one shift or one cavity number across every press: no single press log can explain it
        mech = {"kind": "unknown", "keywords": [],
                "text": (f"mechanism not yet confirmed on {label}; it is not tied to one press, so no single press's "
                         f"process log explains it"),
                "text_ja": f"{label_ja} の発生メカニズムは未確定（特定のプレスに限定されないため、単一プレスの工程記録では説明できない）",
                "action": f"Collect more data on {label} (material lots, tooling, handling) before closing D4",
                "action_ja": f"D4 を閉じる前に、{label_ja} の材料ロット・金型・取り扱いのデータを追加収集する"}
        esc = {"text": (f"not yet identified; the defect on {label} is not tied to one press, so no single inspection "
                        f"station's records explain why it was not detected"),
               "text_ja": f"未特定（{label_ja} の不良は特定のプレスに限定されず、単一の検査工程の記録では検出できなかった理由を説明できない）",
               "action": "Run a detection capability check at final inspection (gauge R&R and sampling plan) before closing D4",
               "action_ja": "D4 を閉じる前に、最終検査の検出能力（ゲージ R&R・抜き取り計画）を確認する"}
    tb.past_8d(mech["keywords"] + (["sampling"] if ic and ic["data"]["sampling_changes"] else []))
    hyps = screen_hypotheses(tb)
    full = tb.last_is_is_not or {}
    top = next((s for s in full.get("top_segments", []) if s["filters"] == seg), None)
    rate_txt = (f" The {tb.defect.replace('_', ' ')} rate on {label} was {A.pct(top['rate_test'])} in the "
                f"complaint date codes against {A.pct(top['rate_control'])} before.") if top else ""
    rate_ja = (f"{label_ja} の不良率は日付コード範囲で {A.pct(top['rate_test'])}、それ以前は {A.pct(top['rate_control'])}。"
               if top else "")
    cont = tb.containment(seg)
    open_points = []
    if mech["kind"] == "unknown":
        open_points.append("Mechanism not yet confirmed")
    if esc["text"].startswith("not yet identified"):
        open_points.append("Escape point not yet identified")
    return {
        "insufficient_evidence": False,
        "occurrence": {"statement": f"Occurrence: {mech['text']}.{rate_txt}", "segment": seg,
                       "evidence": [is_id] + ([pe["id"]] if pe else []) + people_ids},
        "escape": {"statement": f"Escape: {esc['text']}.", "evidence": [ic["id"]] if ic else [is_id]},
        "occurrence_ja": f"発生原因：{mech.get('text_ja', '')}。{rate_ja}",
        "escape_ja": f"流出原因：{esc['text_ja']}。",
        "actions_ja": [a for a in (mech.get("action_ja"), esc.get("action_ja")) if a],
        "actions": [a for a in (mech["action"], esc["action"]) if a],
        "open_points": open_points,
        "containment_evidence": cont["evidence_id"],
        "hypotheses": hyps,
        "evidence_used": [i["id"] for i in tb.log.items],
    }


# --------------------------------------------------------------------------------------------- 8D report
def build_report(case: dict, intake: dict, draft: dict, containment: dict | None, lang: str = "en") -> dict:
    """Assemble the eight disciplines from the approved draft. Text only quotes stored evidence."""
    ja = lang == "ja"
    d2 = intake.get("d2_ja" if ja else "d2") or intake.get("d2") or {}
    sections = {}
    sections["D1"] = ("チーム：品質技術（リーダー）、製造、保全、品質保証マネージャー" if ja else
                      "Team: quality engineer (lead), production, maintenance, quality manager.")
    if ja:
        sections["D2"] = f"現象：{d2.get('what', '')}。発生場所：{d2.get('where', '')}。数量：{d2.get('how_many', '')}。日付コード：{case['date_code_start']}〜{case['date_code_end']}。"
    else:
        sections["D2"] = (f"What: {d2.get('what', '')}. Where: {d2.get('where', '')}. How many: {d2.get('how_many', '')}. "
                          f"Date codes {case['date_code_start']} to {case['date_code_end']}.")
    if draft.get("insufficient_evidence"):
        if ja:
            sections["D3"] = "暫定処置：顧客在庫の選別を依頼。社内データでは特定のロットに偏りがないため、全数保留は行わない。"
            sections["D4"] = "根本原因：証拠不十分。プレス・キャビティ・直・班・樹脂ロットのいずれも有意な偏りなし。"
            sections["D5"] = "追加調査：" + "／".join(["不具合品のシリアルと写真の提供依頼", "梱包・輸送記録の確認", "保管サンプルの検査"])
        else:
            sections["D3"] = ("Containment: ask the customer to sort stock on hand. Plant data shows no lot concentration, "
                              "so no blanket hold.")
            sections["D4"] = "Root cause: insufficient evidence. " + draft.get("summary", "")
            sections["D5"] = "Next data to collect: " + "; ".join(draft.get("next_data", [])) + "."
    else:
        c = containment or {}
        st = c.get("by_status", {})
        if ja:
            sections["D3"] = (f"暫定処置：{c.get('segment_label_ja') or c.get('segment_label', '')} の {c.get('suspect_window', ['', ''])[0]}〜{c.get('suspect_window', ['', ''])[1]} "
                              f"製造分 {c.get('targeted_lots')} ロットを保留（顧客先 {st.get('at_customer', 0)}、輸送中 {st.get('in_transit', 0)}、"
                              f"社内 {st.get('in_plant', 0)}）。全数保留 {c.get('blanket_lots')} ロットは不要。")
            sections["D4"] = draft.get("occurrence_ja") or "発生原因：" + draft["occurrence"]["statement"]
            sections["D4_escape"] = draft.get("escape_ja") or "流出原因：" + draft["escape"]["statement"]
            sections["D5"] = "是正処置：" + "／".join(draft.get("actions_ja") or draft.get("actions", []))
        else:
            sections["D3"] = (f"Containment: hold {c.get('targeted_lots')} lots of {c.get('segment_label', '')} made "
                              f"{c.get('suspect_window', ['', ''])[0]} to {c.get('suspect_window', ['', ''])[1]} "
                              f"({st.get('at_customer', 0)} at customer, {st.get('in_transit', 0)} in transit, "
                              f"{st.get('in_plant', 0)} in plant). A blanket hold would have stopped {c.get('blanket_lots')} lots.")
            sections["D4"] = draft["occurrence"]["statement"]
            sections["D4_escape"] = draft["escape"]["statement"]
            sections["D5"] = "Corrective actions: " + "; ".join(draft.get("actions", [])) + "."
    sections["D6"] = ("実施と効果確認：是正後2週間の不良率が過去28日平均以下であることを確認する。" if ja else
                      "Implement and verify: confirm the defect rate stays at or below the previous 28-day baseline for two weeks after the fix.")
    sections["D7"] = ("再発防止：FMEA・管理計画を改訂し、同型設備へ水平展開する。" if ja else
                      "Prevent recurrence: update the PFMEA and control plan; read across to presses with the same equipment.")
    sections["D8"] = ("チームの貢献を記録し、本 8D を承認済み事例として登録する。" if ja else
                      "Recognise the team and file this 8D as an approved case for future read-across.")
    return sections
