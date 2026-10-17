"""Promptfoo provider: send one root-cause claim to the "Try to break it" endpoint and return the Challenger's ruling.

Local (default): runs the FastAPI app in-process, deterministic mode, temporary storage. No network.
Live: set RAE_EVAL_BASE_URL=https://<your-service>.run.app to send the same claims to the deployed app.
The prompt is the occurrence claim; vars `case` and `escape` complete the draft.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# A fresh ID per run: the live app allows each visitor 30 Gemini critic checks a day, so a re-run would hit the cap.
VISITOR = os.getenv("RAE_EVAL_VISITOR") or f"promptfoo-{int(time.time())}"
_client = None


def _local():
    global _client
    if _client is None:
        os.environ.setdefault("RAE_BACKEND", "local")
        os.environ.setdefault("RAE_USE_LLM", "false")
        os.environ.setdefault("RAE_LOCAL_DIR", tempfile.mkdtemp(prefix="rae-promptfoo-"))
        os.environ.setdefault("RAE_RUNS_PER_IP_PER_10MIN", "100000")
        sys.path.insert(0, str(ROOT))
        from fastapi.testclient import TestClient
        for name in ("httpx", "httpx2", "rae"):
            logging.getLogger(name).setLevel(logging.WARNING)  # keep the eval log readable

        from rae.main import app
        _client = TestClient(app)
    return _client


def _post_live(base: str, case: str, body: dict) -> dict:
    req = urllib.request.Request(f"{base}/api/cases/{case}", headers={"X-Visitor": VISITOR})
    urllib.request.urlopen(req, timeout=60).read()  # make sure the case has a stored result
    req = urllib.request.Request(f"{base}/api/cases/{case}/challenge", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Visitor": VISITOR}, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=60))


def call_api(prompt, options, context):
    v = context.get("vars", {})
    case, body = str(v.get("case", "118")), {"occurrence": prompt, "escape": v.get("escape", "")}
    base = os.getenv("RAE_EVAL_BASE_URL", "").rstrip("/")
    try:
        if base:
            res = _post_live(base, case, body)
        else:
            c = _local()
            c.get(f"/api/cases/{case}", headers={"X-Visitor": VISITOR})
            r = c.post(f"/api/cases/{case}/challenge", headers={"X-Visitor": VISITOR}, json=body)
            r.raise_for_status()
            res = r.json()
    except Exception as exc:  # noqa: BLE001  (a transport error must show as an error, not as a pass)
        return {"error": f"{type(exc).__name__}: {exc}"}
    failed = [x["rule"] for x in res["rules"] if not x["passed"]]
    return {"output": json.dumps({"verdict": res["verdict"], "failed": failed}, ensure_ascii=False)}
