"""Root & Escape web app: a FastAPI API plus the static 8D board, served by one Cloud Run service.

Store, BigQuery and image work is blocking, so async handlers push it to worker threads (asyncio.to_thread);
plain `def` handlers already run in FastAPI's thread pool.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import settings
from .data.cases import SEED_CASES, get_seed
from .pipeline import run_investigation
from .report_html import render_report
from .store import get_store, load_plant, plant_source_label

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rae.main")

WEB = Path(__file__).resolve().parents[1] / "web"
SECTIONS = ["D2", "D3", "D4", "D5"]
DEFECTS = ["crack", "burr", "scratch", "short_shot"]
VISITOR_RE = re.compile(r"^[a-zA-Z0-9-]{8,40}$")
USER_CASE_RE = re.compile(r"^U-[a-f0-9]{16}$")
MAX_PIXELS = 40_000_000          # a 40 MP photo is plenty; anything bigger is refused before it is decoded
MAX_CASES_PER_VISITOR = 50

app = FastAPI(title="Root & Escape", version=__version__, docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=WEB), name="static")

_llm = None
_llm_error: str | None = None
_llm_lock = threading.Lock()
_bq_engine = None
_run_locks: dict[str, asyncio.Lock] = {}


def llm():
    """Gemini agents, created once. Returns None in deterministic mode or if Gemini can't be initialised."""
    global _llm, _llm_error
    if _llm is None and _llm_error is None and settings.use_llm:
        with _llm_lock:
            if _llm is None and _llm_error is None:
                try:
                    from .agents import GeminiAgents
                    _llm = GeminiAgents()
                except Exception as exc:  # noqa: BLE001
                    _llm_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                    log.error("Gemini unavailable, running deterministic: %s", _llm_error)
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
    if not USER_CASE_RE.match(case_id):
        raise HTTPException(404, "Case not found")
    c = get_store().get_case(case_id)
    if not c or c.get("visitor") != visitor:
        raise HTTPException(404, "Case not found")
    return c


# ------------------------------------------------------------------------------------------ abuse limits
# Per-IP speed bump, kept in memory per instance. The first X-Forwarded-For hop is client-supplied, so this only
# slows casual hammering; the hard cost cap is the global daily Gemini quota held in Firestore.
_ip_hits: dict[str, deque] = {}
_ip_lock = threading.Lock()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "unknown")


def ip_allows(ip: str, window_s: float = 600.0) -> bool:
    now = time.monotonic()
    with _ip_lock:
        q = _ip_hits.setdefault(ip, deque())
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= settings.runs_per_ip_per_10min:
            return False
        q.append(now)
        if len(_ip_hits) > 10_000:  # forget idle addresses so memory stays bounded
            for k in [k for k, d in _ip_hits.items() if not d or now - d[-1] > window_s]:
                _ip_hits.pop(k, None)
        return True


# ------------------------------------------------------------------------------------------ runs
async def _run_and_collect(case: dict, use_llm: bool) -> dict:
    plant, source = await asyncio.to_thread(load_plant)
    photos = await asyncio.to_thread(_photos, case)
    result = None
    async for e in run_investigation(case, plant, source, llm() if use_llm else None,
                                     photos, bq_engine() if use_llm else None):
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


def _visitor_key(case_id: str, visitor: str) -> str:
    return f"{case_id}__{visitor}"


def _result_for(case_id: str, visitor: str) -> dict | None:
    """A visitor sees their own latest live run of a seed case; otherwise the shared stored run."""
    store = get_store()
    if get_seed(case_id):
        mine = store.get_result(_visitor_key(case_id, visitor)) if visitor != "anonymous" else None
        return mine or store.get_result(case_id)
    return store.get_result(case_id)


def _same_finding(new: dict, old: dict) -> bool:
    a, b = new.get("analysis") or {}, old.get("analysis") or {}
    return (a.get("primary_segment") == b.get("primary_segment")
            and bool((new.get("draft") or {}).get("insufficient_evidence"))
            == bool((old.get("draft") or {}).get("insufficient_evidence")))


def upgrades_shared(new: dict, prev: dict | None) -> bool:
    """The shared result of a seed case changes at most once: from the deterministic baseline to an approved Gemini
    run that reached the same finding. Any other run is kept per visitor, so one judge never changes what another sees."""
    if new.get("verdict") != "APPROVED_FOR_REVIEW" or (new.get("modes") or {}).get("investigation") != "gemini":
        return False
    if prev is None:
        return True
    return (prev.get("modes") or {}).get("investigation") != "gemini" and _same_finding(new, prev)


def _save_run(case_id: str, visitor: str, r: dict) -> None:
    store = get_store()
    if not get_seed(case_id):
        store.save_result(case_id, r)
        return
    if upgrades_shared(r, store.get_result(case_id)):
        store.save_result(case_id, r)
    if visitor != "anonymous":
        store.save_result(_visitor_key(case_id, visitor), r)


def signed(approvals: dict, result: dict | None) -> dict:
    """Only sign-offs given to the result now on screen count. A new run needs new signatures."""
    at = (result or {}).get("created_at")
    return {s: rec for s, rec in (approvals or {}).items()
            if s in SECTIONS and at and isinstance(rec, dict) and rec.get("result_at") == at}


# -------------------------------------------------------------------------------------------- pages / meta
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__}


@app.get("/api/meta")
def meta(x_visitor: str | None = Header(default=None)):
    g = llm()
    return {
        "version": __version__, "backend": settings.backend, "data_source": plant_source_label(),
        "gemini": bool(g), "model": getattr(g, "model", None), "gemini_error": _llm_error if settings.use_llm else None,
        "bqml": bool(bq_engine()), "quota": get_store().quota(visitor_of(x_visitor)),
        "synthetic": True,
    }


# ------------------------------------------------------------------------------------------------- cases
@app.get("/api/cases")
def list_cases(x_visitor: str | None = Header(default=None)):
    store, visitor = get_store(), visitor_of(x_visitor)
    cases = SEED_CASES + store.list_user_cases(visitor)
    summ = store.summaries([c["case_id"] for c in cases])
    out = []
    for c in cases:
        s = summ.get(c["case_id"]) or {}
        out.append({"case_id": c["case_id"], "title": c["title"], "defect": c["defect"], "received": c["received"],
                    "seed": bool(get_seed(c["case_id"])), "verdict": s.get("verdict"),
                    "insufficient": bool(s.get("insufficient")), "modes": s.get("modes")})
    return out


@app.get("/api/cases/{case_id}")
async def get_case(case_id: str, x_visitor: str | None = Header(default=None)):
    visitor = visitor_of(x_visitor)
    case = await asyncio.to_thread(case_for, case_id, visitor)
    store = get_store()
    result = await asyncio.to_thread(_result_for, case_id, visitor)
    if result is None and get_seed(case_id):
        lock = _run_locks.setdefault(case_id, asyncio.Lock())
        async with lock:  # first visit: compute the deterministic baseline once
            result = await asyncio.to_thread(store.get_result, case_id)
            if result is None:
                result = await _run_and_collect(case, use_llm=False)
                await asyncio.to_thread(store.save_result, case_id, result)
    approvals = await asyncio.to_thread(store.approvals, visitor, case_id)
    public_case = {k: v for k, v in case.items() if k not in ("truth", "visitor")}
    return {"case": public_case, "result": result, "approvals": signed(approvals, result)}


@app.post("/api/cases")
async def create_case(request: Request, complaint: str = Form(..., max_length=4000), defect: str = Form(...),
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
    if not ip_allows(client_ip(request)):
        raise HTTPException(429, "Too many requests from your network. Try again in a few minutes.")
    store = get_store()
    if len(await asyncio.to_thread(store.list_user_cases, visitor)) >= MAX_CASES_PER_VISITOR:
        raise HTTPException(429, f"This demo keeps up to {MAX_CASES_PER_VISITOR} cases per visitor.")
    case_id = f"U-{uuid.uuid4().hex[:16]}"
    photo_name = None
    if photo is not None and photo.filename:
        limit = settings.max_upload_mb * 1024 * 1024
        data = await photo.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"Photo is larger than {settings.max_upload_mb} MB")
        jpeg = await asyncio.to_thread(normalise_image, data)
        photo_name = f"{case_id}.jpg"
        await asyncio.to_thread(store.put_photo, photo_name, jpeg, "image/jpeg")
    case = {"case_id": case_id, "title": (title.strip() or f"{defect.capitalize()} complaint")[:120],
            "customer": "Your test customer", "defect": defect,
            "received": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "date_code_start": s.isoformat(), "date_code_end": e.isoformat(), "language": "auto",
            "complaint": complaint.strip(), "photo": photo_name, "golden": "golden_cl204.png", "visitor": visitor}
    await asyncio.to_thread(store.save_case, case)
    return {"case_id": case_id}


def normalise_image(data: bytes) -> bytes:
    """Re-encode uploads as JPEG: only JPEG/PNG/WebP, at most MAX_PIXELS, downscaled to 1600 px *before* full
    decoding, orientation fixed, metadata stripped."""
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow raises DecompressionBombError above 2x this
    try:
        img = Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP"])
    except Image.DecompressionBombError:
        raise HTTPException(413, "That photo has too many pixels. Upload one under 40 megapixels.")
    except Exception:
        raise HTTPException(415, "Upload a JPEG, PNG or WebP photo")
    if img.width * img.height > MAX_PIXELS:
        raise HTTPException(413, "That photo has too many pixels. Upload one under 40 megapixels.")
    try:
        img.thumbnail((1600, 1600))  # uses the JPEG decoder's draft mode, so a big JPEG is never fully decoded
        img = ImageOps.exif_transpose(img).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
    except Exception:
        raise HTTPException(415, "That photo could not be read. Try another JPEG, PNG or WebP file.")
    return buf.getvalue()


@app.get("/api/photos/{name}")
def photo(name: str, v: str | None = Query(default=None)):
    m = re.match(r"^(U-[a-f0-9]{16})\.jpg$", name)
    if not m:
        raise HTTPException(404)
    store = get_store()
    c = store.get_case(m.group(1))
    if not c or c.get("visitor") != visitor_of(v):  # photos are only shown to the visitor who uploaded them
        raise HTTPException(404)
    got = store.get_photo(name)
    if not got:
        raise HTTPException(404)
    return Response(got[0], media_type=got[1], headers={"Cache-Control": "private, max-age=3600"})


def _take_quota(store, visitor: str, kind: str) -> tuple[bool, dict | None]:
    """If the quota store itself fails, fail safe: no Gemini run (it costs money), but the free engine still runs."""
    try:
        return store.take_quota(visitor, kind)
    except Exception:  # noqa: BLE001
        log.exception("quota check failed (%s)", kind)
        return kind == "det", None


def _sse(e: dict) -> str:
    return f"data: {json.dumps(e, ensure_ascii=False, default=str)}\n\n"


HEARTBEAT_S = 15.0


async def _with_heartbeat(agen, every: float | None = None):
    """Yield the generator's items, plus None whenever nothing arrived for `every` seconds. A quiet stretch (one long
    Gemini call) then still sends bytes, so no proxy drops the stream as idle."""
    every = every or HEARTBEAT_S
    nxt = None
    try:
        while True:
            if nxt is None:
                nxt = asyncio.ensure_future(agen.__anext__())
            done, _ = await asyncio.wait({nxt}, timeout=every)
            if not done:
                yield None
                continue
            step, nxt = nxt, None
            try:
                item = step.result()
            except StopAsyncIteration:
                return
            yield item
    finally:
        if nxt is not None and not nxt.done():
            nxt.cancel()  # the client left: cancelling the pending step lets the pipeline stop its Gemini work


@app.get("/api/cases/{case_id}/live")
async def live(case_id: str, request: Request, v: str | None = Query(default=None)):
    """Re-run the investigation and stream every step (Server-Sent Events). Rate-limited to protect cost."""
    visitor = visitor_of(v)
    case = await asyncio.to_thread(case_for, case_id, visitor)
    store = get_store()
    g = await asyncio.to_thread(llm)
    ip_ok = ip_allows(client_ip(request))
    ok = det_ok = False
    quota = None
    # Only Gemini runs cost money, so they have the tight quota. When it is used up the run still happens,
    # deterministically, under a much looser cap that only exists to stop abuse.
    if ip_ok and g is not None:
        ok, quota = await asyncio.to_thread(_take_quota, store, visitor, "gemini")
    if ip_ok and not ok:
        det_ok, _ = await asyncio.to_thread(_take_quota, store, visitor, "det")
    if quota is None:
        try:
            quota = await asyncio.to_thread(store.quota, visitor)
        except Exception:  # noqa: BLE001
            log.exception("quota read failed")
            quota = {"used_today": None, "limit_today": settings.live_runs_per_day,
                     "used_by_you": None, "limit_per_visitor": settings.runs_per_visitor_per_day}

    async def stream():
        if not (ok or det_ok):
            why = ("Too many runs from your network in the last 10 minutes" if not ip_ok
                   else "You have used today's runs for this demo")
            yield _sse({"type": "limit", "message": f"{why}. The stored result is still shown; try again later.",
                        "quota": quota})
            return
        use = g if ok else None
        if g is not None and not ok:
            msg = (f"Today's Gemini runs are used up ({quota['limit_today']} a day for everyone, "
                   f"{quota['limit_per_visitor']} per visitor), so this run uses the deterministic engine.")
            yield _sse({"type": "quota", "message": msg, "quota": quota})
        else:
            yield _sse({"type": "quota", "quota": quota})
        try:
            plant, source = await asyncio.to_thread(load_plant)
            photos = await asyncio.to_thread(_photos, case)
            async for e in _with_heartbeat(run_investigation(case, plant, source, use, photos, bq_engine() if use else None)):
                if e is None:
                    yield ": ping\n\n"  # an SSE comment: browsers ignore it, proxies see traffic
                    continue
                if e["type"] == "done":
                    r = e["result"]
                    if g is not None and not ok:
                        r["notes"] = ["Gemini quota for today was used up; deterministic engine used."] + r["notes"]
                    try:
                        await asyncio.to_thread(_save_run, case_id, visitor, r)
                    except Exception:  # noqa: BLE001  (still show the run; it just won't survive a reload)
                        log.exception("case %s: could not save the run", case_id)
                        r["notes"] = r["notes"] + ["This run could not be saved, so it will not be there after a reload."]
                yield _sse(e)
        except Exception as exc:  # noqa: BLE001
            log.exception("case %s: live run failed", case_id)
            yield _sse({"type": "error",
                        "message": f"The run stopped: {type(exc).__name__}. The stored result is still shown."})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


class Approval(BaseModel):
    section: str = Field(pattern=r"^D[2-5]$")
    name: str = Field(min_length=2, max_length=60)
    result_at: str | None = Field(default=None, max_length=64)  # the run the signer was looking at


@app.post("/api/cases/{case_id}/approve")
def approve(case_id: str, body: Approval, x_visitor: str | None = Header(default=None)):
    visitor = visitor_of(x_visitor)
    case_for(case_id, visitor)
    result = _result_for(case_id, visitor)
    if not result:
        raise HTTPException(409, "Run the investigation first")
    if body.result_at and body.result_at != result.get("created_at"):
        raise HTTPException(409, "This case has a newer run. Reload it and review it before signing.")
    record = {"by": f"human:{body.name.strip()}", "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "result_at": result.get("created_at")}
    return signed(get_store().approve(visitor, case_id, body.section, record), result)


@app.post("/api/cases/{case_id}/reset")
def reset(case_id: str, x_visitor: str | None = Header(default=None)):
    visitor = visitor_of(x_visitor)
    case_for(case_id, visitor)
    get_store().reset_approvals(visitor, case_id)
    return {"ok": True}


class Challenge(BaseModel):
    occurrence: str = Field(min_length=5, max_length=600)
    escape: str = Field(default="", max_length=600)


class _StoredToolbox:
    """Rebuilds just enough of a run from the stored evidence for the Challenger rules. No model call, no cost."""

    def __init__(self, result: dict):
        from .tools import EvidenceLog
        self.log = EvidenceLog()
        self.log.items = [dict(i) for i in result.get("evidence", [])]
        a = result.get("analysis")
        self.last_is_is_not = None if not a else {
            "significant": bool(a.get("significant")), "primary_segment": a.get("primary_segment"),
            "primary_label": a.get("primary_label")}


@app.post("/api/cases/{case_id}/challenge")
def challenge(case_id: str, body: Challenge, x_visitor: str | None = Header(default=None)):
    """'Try to break it': run the Challenger's rules against a root-cause claim the visitor writes.
    The claim is given the run's evidence IDs, but it only gets the analysed segment if it names that segment
    (for example "P4"). A claim about the wrong press, or about nothing in the data, fails."""
    from .challenger import check_draft, verdict
    visitor = visitor_of(x_visitor)
    case_for(case_id, visitor)
    result = _result_for(case_id, visitor)
    if not result:
        raise HTTPException(404, "Run the investigation first")
    tb = _StoredToolbox(result)
    stored = result.get("draft") or {}
    cited = list((stored.get("occurrence") or {}).get("evidence") or []) + \
        [i["id"] for i in tb.log.items if i["tool"] == "stratify"]
    prim = (tb.last_is_is_not or {}).get("primary_segment") or {}
    names_it = bool(prim) and all(re.search(rf"(?<![A-Za-z0-9]){re.escape(str(v))}(?![A-Za-z0-9])", body.occurrence, re.I)
                                  for v in prim.values())
    seg = prim if names_it else {}
    draft = {"insufficient_evidence": False,
             "occurrence": {"statement": body.occurrence.strip(), "segment": seg, "evidence": cited},
             "escape": {"statement": body.escape.strip(), "evidence": list((stored.get("escape") or {}).get("evidence") or [])}}
    rules = check_draft(draft, tb)
    return {"verdict": verdict(rules), "rules": rules}


@app.get("/api/cases/{case_id}/report", response_class=HTMLResponse)
def report(case_id: str, lang: str = Query("en", pattern="^(en|ja)$"), v: str | None = Query(default=None)):
    visitor = visitor_of(v)
    case = case_for(case_id, visitor)
    store = get_store()
    result = _result_for(case_id, visitor)
    if not result:
        raise HTTPException(404, "Run the investigation first")
    appr = signed(store.approvals(visitor, case_id), result)
    missing = [s for s in SECTIONS if s not in appr]
    if missing:
        raise HTTPException(409, f"Nothing leaves unsigned. Approve {', '.join(missing)} first.")
    return HTMLResponse(render_report(case, result, appr, lang))


@app.get("/api/benchmark")
def benchmark():
    """Precomputed by `python -m rae.benchmark --write`; tests/test_core.py checks the file matches a fresh run."""
    p = Path(__file__).resolve().parent / "data" / "benchmark.json"
    return JSONResponse(json.loads(p.read_text(encoding="utf-8")), headers={"Cache-Control": "public, max-age=3600"})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": "Something went wrong on our side. The stored results are still available."}, status_code=500)
