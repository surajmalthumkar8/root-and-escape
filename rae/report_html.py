"""Printable 8D report (EN / JA). Opened only after D2-D5 are signed."""
from __future__ import annotations

import html

T = {
    "en": {"head": "8D Report", "D1": "D1 Team", "D2": "D2 Problem description", "D3": "D3 Containment",
           "D4": "D4 Root cause: occurrence", "D4_escape": "D4 Root cause: escape", "D5": "D5 Corrective actions",
           "D6": "D6 Implement and verify", "D7": "D7 Prevent recurrence", "D8": "D8 Recognise the team",
           "syn": "Synthetic demo data (Google Cloud AI Builder Cup 2026 prototype). Do not use for real decisions.",
           "case": "Case", "gen": "generated", "photos": "Photos", "defect": "Defect", "golden": "Golden sample",
           "actions": "Actions, owners and due dates", "action": "Action", "owner": "Owner", "due": "Due",
           "lots": "Lots on hold", "lot": "Lot", "made": "Made", "ship": "Shipment", "status": "Status",
           "sign": "Sign-off", "section": "Section", "by": "Approved by", "at": "At (UTC)",
           "ev": "Evidence (tool results; original language)", "print": "Print / save as PDF",
           "status_map": {"at_customer": "at customer", "in_transit": "in transit", "in_plant": "in plant"},
           "owners": {"Quality": "Quality", "Maintenance": "Maintenance", "Process engineering": "Process engineering"}},
    "ja": {"head": "8D レポート", "D1": "D1 チーム", "D2": "D2 問題の記述", "D3": "D3 暫定処置",
           "D4": "D4 発生原因", "D4_escape": "D4 流出原因", "D5": "D5 是正処置",
           "D6": "D6 実施と効果確認", "D7": "D7 再発防止", "D8": "D8 チームの称賛",
           "syn": "合成デモデータ（Google Cloud AI Builder Cup 2026 プロトタイプ）。実際の判断には使用しないでください。",
           "case": "事例", "gen": "作成", "photos": "写真", "defect": "不良品", "golden": "良品サンプル",
           "actions": "是正処置・担当・期限", "action": "処置", "owner": "担当", "due": "期限",
           "lots": "保留ロット", "lot": "ロット", "made": "製造日", "ship": "出荷", "status": "状態",
           "sign": "承認", "section": "項目", "by": "承認者", "at": "日時（UTC）",
           "ev": "証拠（ツール結果・原文）", "print": "印刷 / PDF 保存",
           "status_map": {"at_customer": "顧客先", "in_transit": "輸送中", "in_plant": "社内"},
           "owners": {"Quality": "品質保証", "Maintenance": "保全", "Process engineering": "生産技術"}},
}


def _photo_url(name: str | None) -> str:
    if not name:
        return ""
    return f"/api/photos/{name}" if name.startswith("U-") else f"/static/photos/{name}"


def render_report(case: dict, result: dict, appr: dict, lang: str) -> str:
    t = T[lang]
    e = html.escape
    sec = result["report"].get(lang) or result["report"]["en"]
    rows = []
    for k in ["D1", "D2", "D3", "D4", "D4_escape", "D5", "D6", "D7", "D8"]:
        if k in sec:
            rows.append(f"<section><h2>{e(t[k])}</h2><p>{e(sec[k])}</p></section>")
    photos = ""
    if case.get("photo"):
        photos = (f"<section><h2>{e(t['photos'])}</h2><div class='ph'><figure><img src='{e(_photo_url(case['photo']))}' alt=''>"
                  f"<figcaption>{e(t['defect'])}</figcaption></figure><figure><img src='{e(_photo_url(case.get('golden')))}' alt=''>"
                  f"<figcaption>{e(t['golden'])}</figcaption></figure></div></section>")
    acts = result.get("actions") or []
    # the owners and dates are shared; the action text comes in the report's language when the draft has it
    ja = (result.get("draft") or {}).get("actions_ja") or []
    if lang == "ja" and len(ja) == len(acts):
        acts = [{**a, "action": j or a["action"]} for a, j in zip(acts, ja)]
    act_html = ""
    if acts:
        act_html = (f"<section><h2>{e(t['actions'])}</h2><table><tr><th>{e(t['action'])}</th><th>{e(t['owner'])}</th><th>{e(t['due'])}</th></tr>"
                    + "".join(f"<tr><td>{e(a['action'])}</td><td>{e(t['owners'].get(a['owner'], a['owner']))}</td><td>{e(a['due'])}</td></tr>" for a in acts)
                    + "</table></section>")
    c = result.get("containment") or {}
    lots_html = ""
    if c.get("lots"):
        lots_html = (f"<section><h2>{e(t['lots'])} ({c['targeted_lots']})</h2><table><tr><th>{e(t['lot'])}</th><th>{e(t['made'])}</th>"
                     f"<th>{e(t['ship'])}</th><th>{e(t['status'])}</th></tr>"
                     + "".join(f"<tr><td>{e(l['lot_id'])}</td><td>{e(l['prod_date'])}</td><td>{e(l['shipment_id'])}</td>"
                               f"<td>{e(t['status_map'].get(l['status'], l['status']))}</td></tr>" for l in c["lots"])
                     + "</table></section>")
    sign = (f"<section><h2>{e(t['sign'])}</h2><table><tr><th>{e(t['section'])}</th><th>{e(t['by'])}</th><th>{e(t['at'])}</th></tr>"
            + "".join(f"<tr><td>{e(k)}</td><td>{e(v['by'].replace('human:', ''))}</td><td>{e(v['at'][:16].replace('T', ' '))}</td></tr>"
                      for k, v in sorted(appr.items()))
            + "</table></section>")
    ev = "".join(f"<li><b>{e(i['id'])}</b> {e(i['summary'])} <i>({e(i['source'])})</i></li>" for i in result["evidence"])
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{e(t['head'])} {e(case['case_id'])}</title>
<style>body{{font:14.5px/1.6 system-ui,'Hiragino Sans','Noto Sans JP','Yu Gothic',sans-serif;max-width:860px;margin:28px auto;padding:0 20px;color:#17202e}}
h1{{font-size:22px;margin:0}} .meta{{color:#4a5466;font-size:13px;margin:4px 0 16px}} section{{border:1px solid #d3d8e0;border-radius:6px;padding:10px 14px;margin:10px 0;break-inside:avoid}}
h2{{font-size:13px;margin:0 0 6px;letter-spacing:.03em;color:#2350b8}} table{{width:100%;border-collapse:collapse;font-size:13px}} td,th{{border-bottom:1px solid #e3e7ee;padding:4px 6px;text-align:left;vertical-align:top}}
.ph{{display:grid;grid-template-columns:1fr 1fr;gap:10px}} .ph img{{width:100%;border:1px solid #d3d8e0;border-radius:4px}} figure{{margin:0}} figcaption{{font-size:12px;color:#4a5466}}
ol{{font-size:12.5px;color:#4a5466}} .syn{{background:#fff4d6;border:1px solid #e3c25a;padding:8px 12px;border-radius:6px;font-size:13px}}
@media print{{.noprint{{display:none}}}}</style></head><body>
<h1>{e(t['head'])} · {e(case['title'])}</h1>
<div class="meta">{e(t['case'])} {e(case['case_id'])} · {e(case.get('customer', ''))} · {e(t['gen'])} {e(result['created_at'][:16].replace('T', ' '))} UTC · Root &amp; Escape</div>
<p class="syn">{e(t['syn'])}</p>
{''.join(rows)}{photos}{act_html}{lots_html}{sign}
<section><h2>{e(t['ev'])}</h2><ol>{ev}</ol></section>
<p class="noprint"><button onclick="window.print()">{e(t['print'])}</button></p></body></html>"""
