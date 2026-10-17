"""Root & Escape web app: a FastAPI API plus the static 8D board, served by one Cloud Run service."""
from __future__ import annotations

import asyncio
import html
import io
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import settings
from .data.cases import SEED_CASES, get_seed
from .pipeline import run_investigation
from .store import get_store, load_plant

WEB = Path(__file__).resolve().parents[1] / "web"
SECTIONS = ["D2", "D3", "D4", "D5"]
DEFECTS = ["crack", "burr", "scratch", "short_shot"]
VISITOR_RE = re.compile(r"^[a-zA-Z0-9-]{8,40}$")

app = FastAPI(title="Root & Escape", version=__version__, docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB), name="static")

_llm = None
_llm_error: str | None = None
_bq_engine = None
_run_locks: dict[str, asyncio.Lock] = {}


def llm():
    """Gemini agents, created once. Returns None in deterministic mode or if Gemini can't be initialised."""
    global _llm, _llm_error
    if _llm is None and _llm_error is None and settings.use_llm:
        try:
            from .agents import GeminiAgents
            _llm = GeminiAgents()
        except Exception as exc:  # noqa: BLE001
            _llm_error = f"{type(exc).__name__}: {str(exc)[:200]}"
    return _llm


def bq_engine():
    global _bq_engine
    if _bq_engine is None and settings.gcp and settings.use_bqml:
        from .bq import contribution_analysis
        _bq_engine = contribution_analysis
    return _bq_engine


def visitor_of(v: str | None) -> str:
    return v if v and VISITOR_RE.match(v) else "anonymous"


def case_for(case_id: str, visitor: str) -> dict:
    seed = get_seed(case_id)
    if seed:
        return seed
    if not re.match(r"^U-[a-f0-9]{8}$", case_id):
        raise HTTPException(404, "Case not found")
    c = get_store().get_case(case_id)
    if not c or c.get("visitor") != visitor:
        raise HTTPException(404, "Case not found")
    return c


async def _run_and_collect(case: dict, use_llm: bool) -> dict:
    plant, source = load_plant()
    result = None
    async for e in run_investigation(case, plant, source, llm() if use_llm else None,
                                     _photos(case), bq_engine() if use_llm else None):
        if e["type"] == "done":
            result = e["result"]
    return result


def _photos(case: dict) -> dict:
    out = {}
    for key in ("photo", "golden"):
        name = case.get(key)
        if not name:
            continue
        p = WEB / "photos" / name
        if p.exists():
            out[key] = (p.read_bytes(), "image/png")
        else:
            got = get_store().get_photo(name)
            if got:
                out[key] = got
    return out


# -------------------------------------------------------------------------------------------- pages / meta
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__}


@app.get("/api/meta")
def meta(x_visitor: str | None = Header(default=None)):
    _, source = load_plant()
    g = llm()
    return {
        "version": __version__, "backend": settings.backend, "data_source": source,
        "gemini": bool(g), "model": getattr(g, "model", None), "gemini_error": _llm_error if settings.use_llm else None,
        "bqml": bool(bq_engine()), "quota": get_store().quota(visitor_of(x_visitor)),
        "synthetic": True,
    }


# ------------------------------------------------------------------------------------------------- cases
@app.get("/api/cases")
def list_cases(x_visitor: str | None = Header(default=None)):
    store, visitor = get_store(), visitor_of(x_visitor)
    out = []
    for c in SEED_CASES + store.list_user_cases(visitor):
        r = store.get_result(c["case_id"])
        out.append({"case_id": c["case_id"], "title": c["title"], "defect": c["defect"], "received": c["received"],
                    "seed": bool(get_seed(c["case_id"])), "verdict": r and r.get("verdict"),
                    "insufficient": bool(r and r.get("draft", {}).get("insufficient_evidence")),
                    "modes": r and r.get("modes")})
    return out


@app.get("/api/cases/{case_id}")
async def get_case(case_id: str, x_visitor: str | None = Header(default=None)):
    visitor = visitor_of(x_visitor)
    case = case_for(case_id, visitor)
    store = get_store()
    result = store.get_result(case_id)
    if result is None and get_seed(case_id):
        lock = _run_locks.setdefault(case_id, asyncio.Lock())
        async with lock:  # first visit: compute the deterministic baseline once
            result = store.get_result(case_id)
            if result is None:
                result = await _run_and_collect(case, use_llm=False)
                store.save_result(case_id, result)
    public_case = {k: v for k, v in case.items() if k not in ("truth", "visitor")}
    return {"case": public_case, "result": result, "approvals": store.approvals(visitor, case_id)}


@app.post("/api/cases")
async def create_case(complaint: str = Form(..., max_length=4000), defect: str = Form(...),
                      date_code_start: str = Form(...), date_code_end: str = Form(...),
                      title: str = Form("", max_length=120), photo: UploadFile | None = File(default=None),
                      x_visitor: str | None = Header(default=None)):
    visitor = visitor_of(x_visitor)
    if visitor == "anonymous":
        raise HTTPException(400, "Missing visitor id. Reload the page.")
    if defect not in DEFECTS:
        raise HTTPException(422, f"defect must be one of {DEFECTS}")
    try:
        s = datetime.fromisoformat(date_code_start).date()
        e = datetime.fromisoformat(date_code_end).date()
    except ValueError:
        raise HTTPException(422, "Dates must be YYYY-MM-DD")
    if not (datetime(2026, 8, 30).date() <= s <= e <= datetime(2026, 10, 16).date()):
        raise HTTPException(422, "Date codes must fall between 2026-08-30 and 2026-10-16 (the synthetic plant's history)")
    if (e - s).days > 21:
        raise HTTPException(422, "Keep the date-code range to 21 days or less")
    if len(complaint.strip()) < 20:
        raise HTTPException(422, "Describe the complaint in at least 20 characters")
    case_id = f"U-{uuid.uuid4().hex[:8]}"
    photo_name = None
    if photo is not None and photo.filename:
        data = await photo.read()
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"Photo is larger than {settings.max_upload_mb} MB")
        photo_name = f"{case_id}.jpg"
        get_store().put_photo(photo_name, _normalise_image(data), "image/jpeg")
    case = {"case_id": case_id, "title": (title.strip() or f"{defect.capitalize()} complaint")[:120],
            "customer": "Your test customer", "defect": defect,
            "received": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "date_code_start": s.isoformat(), "date_code_end": e.isoformat(), "language": "auto",
            "complaint": complaint.strip(), "photo": photo_name, "golden": "golden_cl204.png", "visitor": visitor}
    get_store().save_case(case)
    return {"case_id": case_id}


def _normalise_image(data: bytes) -> bytes:
    """Re-encode uploads: strips metadata, fixes orientation, caps size at 1600 px."""
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        raise HTTPException(415, "Upload a JPEG, PNG or WebP photo")
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


@app.get("/api/photos/{name}")
def photo(name: str):
    if not re.match(r"^U-[a-f0-9]{8}\.jpg$", name):
        raise HTTPException(404)
    got = get_store().get_photo(name)
    if not got:
        raise HTTPException(404)
    return Response(got[0], media_type=got[1], headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/cases/{case_id}/live")
async def live(case_id: str, v: str | None = Query(default=None)):
    """Re-run the investigation and stream every step (Server-Sent Events). Rate-limited to protect cost."""
    visitor = visitor_of(v)
    case = case_for(case_id, visitor)
    store = get_store()
    ok, quota = store.take_quota(visitor)

    async def stream():
        if not ok:
            msg = ("Today's live-run limit is used up, so the stored run is shown. "
                   f"Limits: {quota['limit_today']} a day for everyone, {quota['limit_per_visitor']} per visitor.")
            yield f"data: {json.dumps({'type': 'quota', 'message': msg, 'quota': quota})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'quota', 'quota': quota})}\n\n"
        plant, source = load_plant()
        g = llm()
        try:
            async for e in run_investigation(case, plant, source, g, _photos(case), bq_engine()):
                if e["type"] == "done":
                    r = e["result"]
                    prev = store.get_result(case_id)
                    better = (not get_seed(case_id)) or prev is None or r["verdict"] == "APPROVED_FOR_REVIEW"
                    if better:
                        store.save_result(case_id, r)
                yield f"data: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'message': f'The run stopped: {type(exc).__name__}. The stored result is still shown.'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Approval(BaseModel):
    section: str = Field(pattern=r"^D[2-5]$")
    name: str = Field(min_length=2, max_length=60)


@app.post("/api/cases/{case_id}/approve")
def approve(case_id: str, body: Approval, x_visitor: str | None = Header(default=None)):
    visitor = visitor_of(x_visitor)
    case_for(case_id, visitor)
    record = {"by": f"human:{body.name.strip()}", "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return get_store().approve(visitor, case_id, body.section, record)


@app.post("/api/cases/{case_id}/reset")
def reset(case_id: str, x_visitor: str | None = Header(default=None)):
    visitor = visitor_of(x_visitor)
    case_for(case_id, visitor)
    get_store().reset_approvals(visitor, case_id)
    return {"ok": True}


@app.get("/api/cases/{case_id}/report", response_class=HTMLResponse)
def report(case_id: str, lang: str = Query("en", pattern="^(en|ja)$"), v: str | None = Query(default=None)):
    visitor = visitor_of(v)
    case = case_for(case_id, visitor)
    store = get_store()
    result = store.get_result(case_id)
    if not result:
        raise HTTPException(404, "Run the investigation first")
    appr = store.approvals(visitor, case_id)
    missing = [s for s in SECTIONS if s not in appr]
    if missing:
        raise HTTPException(409, f"Nothing leaves unsigned. Approve {', '.join(missing)} first.")
    return HTMLResponse(render_report(case, result, appr, lang))


def render_report(case: dict, result: dict, appr: dict, lang: str) -> str:
    sec = result["report"].get(lang) or result["report"]["en"]
    e = html.escape
    titles = {"en": {"D1": "D1 Team", "D2": "D2 Problem description", "D3": "D3 Containment", "D4": "D4 Root cause: occurrence",
                     "D4_escape": "D4 Root cause: escape", "D5": "D5 Corrective actions", "D6": "D6 Implement and verify",
                     "D7": "D7 Prevent recurrence", "D8": "D8 Recognise the team"},
              "ja": {"D1": "D1 チーム", "D2": "D2 問題の記述", "D3": "D3 暫定処置", "D4": "D4 根本原因（発生）",
                     "D4_escape": "D4 根本原因（流出）", "D5": "D5 是正処置", "D6": "D6 実施と効果確認",
                     "D7": "D7 再発防止", "D8": "D8 チームの称賛"}}[lang]
    rows = []
    for k in ["D1", "D2", "D3", "D4", "D4_escape", "D5", "D6", "D7", "D8"]:
        if k not in sec:
            continue
        a = appr.get(k[:2]) if k[:2] in SECTIONS else None
        sign = f"<div class='sig'>✓ {e(a['by'].replace('human:', ''))} · {e(a['at'][:16].replace('T', ' '))} UTC</div>" if a else ""
        rows.append(f"<section><h2>{e(titles[k])}</h2><p>{e(sec[k])}</p>{sign}</section>")
    ev = "".join(f"<li><b>{e(i['id'])}</b> {e(i['summary'])} <i>({e(i['source'])})</i></li>" for i in result["evidence"])
    head = "8D レポート" if lang == "ja" else "8D Report"
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{head} {e(case['case_id'])}</title>
<style>body{{font:15px/1.6 system-ui,'Hiragino Sans','Noto Sans JP',sans-serif;max-width:820px;margin:32px auto;padding:0 20px;color:#17202e}}
h1{{font-size:24px;margin:0}} .meta{{color:#4a5466;font-size:13px;margin:4px 0 20px}} section{{border:1px solid #d3d8e0;border-radius:6px;padding:10px 14px;margin:10px 0}}
h2{{font-size:14px;margin:0 0 4px;text-transform:uppercase;letter-spacing:.04em;color:#2350b8}} .sig{{color:#1f7349;font-size:13px;margin-top:6px}}
ol{{font-size:13px;color:#4a5466}} .syn{{background:#fff4d6;border:1px solid #e3c25a;padding:8px 12px;border-radius:6px;font-size:13px}}
@media print{{.noprint{{display:none}}}}</style></head><body>
<h1>{head} · {e(case['title'])}</h1>
<div class="meta">Case {e(case['case_id'])} · {e(case.get('customer', ''))} · generated {e(result['created_at'][:16].replace('T', ' '))} UTC · Root &amp; Escape</div>
<p class="syn">Synthetic demo data (Google Cloud AI Builder Cup 2026 prototype). Do not use for real decisions.</p>
{''.join(rows)}<h2 style="margin-top:20px">Evidence</h2><ol>{ev}</ol>
<p class="noprint"><button onclick="window.print()">Print / save as PDF</button></p></body></html>"""


@app.get("/api/benchmark")
def benchmark():
    from .benchmark import run
    global _bench
    try:
        return _bench
    except NameError:
        _bench = run()
        return _bench


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception):
    return JSONResponse({"detail": "Something went wrong on our side. The stored results are still available."}, status_code=500)
