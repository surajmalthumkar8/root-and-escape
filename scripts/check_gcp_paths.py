"""Exercise the GCP code paths (GcpStore + the API in gcp mode) against the local Firestore emulator.

Stubbed: BigQuery (plant data comes from the local generator) and Cloud Storage (in-memory bucket).
Real: every Firestore call, including the quota transaction under concurrency, get_all with field_paths,
merge writes, TTL field, and the FastAPI app end to end.
Run:  npx firebase-tools emulators:start --only firestore --project demo-rae
      FIRESTORE_EMULATOR_HOST=127.0.0.1:8085 python scripts/check_gcp_paths.py .
"""
import io
import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

REPO = sys.argv[1]
sys.path.insert(0, REPO)
os.environ.update(RAE_BACKEND="gcp", GOOGLE_CLOUD_PROJECT="demo-rae", RAE_USE_LLM="false", RAE_BUCKET="fake-bucket",
                  RAE_RUNS_PER_IP_PER_10MIN="100000", RAE_LIVE_RUNS_PER_DAY="30", RAE_RUNS_PER_VISITOR_PER_DAY="5")
assert os.environ.get("FIRESTORE_EMULATOR_HOST"), "set FIRESTORE_EMULATOR_HOST"


class FakeBlob:
    store: dict = {}

    def __init__(self, name):
        self.name, self.content_type = name, None

    def upload_from_string(self, data, content_type=None):
        FakeBlob.store[self.name] = (data, content_type)

    def exists(self):
        return self.name in FakeBlob.store

    def download_as_bytes(self):
        data, ct = FakeBlob.store[self.name]
        self.content_type = ct
        return data


class FakeBucket:
    def blob(self, name):
        b = FakeBlob(name)
        if name in FakeBlob.store:
            b.content_type = FakeBlob.store[name][1]
        return b


class FakeStorageClient:
    def __init__(self, *a, **k):
        pass

    def bucket(self, name):
        return FakeBucket()


import google.cloud.storage as gcs  # noqa: E402
gcs.Client = FakeStorageClient

import rae.store as S  # noqa: E402
from rae.data.generator import generate  # noqa: E402
S._read_plant = lambda: generate(42)  # BigQuery stand-in (same data the loader puts in BigQuery)

problems = []


def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        problems.append(msg)


store = S.get_store()
check(type(store).__name__ == "GcpStore", "GcpStore in use")

# ---- quota: exact when runs arrive one at a time, never over the cap when they arrive together
day_doc = store.db.collection("quota").document(S.utc_day())
visitors = [f"v-{i:02d}-{uuid.uuid4().hex[:6]}" for i in range(10)]
day_doc.delete()
seq = [store.take_quota(v)[0] for v in visitors for _ in range(7)]
check(sum(seq) == 30, f"sequential: exactly 30 granted ({sum(seq)})")
ok, view = store.take_quota("late-visitor-01")
check(not ok and view["used_today"] == 30, "sequential: the 31st run is refused with a correct view")
day_doc.delete()
with ThreadPoolExecutor(max_workers=16) as ex:
    oks = list(ex.map(lambda v: store.take_quota(v)[0], [v for v in visitors for _ in range(7)]))
q = day_doc.get().to_dict()
check(sum(oks) <= 30 and q["total"] == sum(oks), f"16 at once: never over the cap, counter matches ({sum(oks)} granted)")
check(max(q["visitors"].values()) <= 5, f"16 at once: per-visitor cap held (max {max(q['visitors'].values())})")
det_ok, _ = store.take_quota("late-visitor-01", "det")
check(det_ok, "deterministic quota is separate from the Gemini quota")
check(store.db.collection("quota").document(S.utc_day() + "-det").get().exists, "det quota stored under its own day key")

# ---- results, summaries, TTL
store.save_result("118__someone", {"verdict": "APPROVED_FOR_REVIEW", "modes": {"investigation": "gemini"}, "draft": {}})
raw = store.db.collection("results").document("118__someone").get().to_dict()
check("expires_at" in raw and raw["summary"]["verdict"] == "APPROVED_FOR_REVIEW", "per-visitor result has expires_at + summary")
summ = store.summaries(["118__someone", "does-not-exist"])
check(summ["118__someone"]["modes"]["investigation"] == "gemini" and summ["does-not-exist"] is None, "summaries() via get_all(field_paths)")

# ---- the API end to end in gcp mode
from fastapi.testclient import TestClient  # noqa: E402
from rae.main import app  # noqa: E402

c = TestClient(app)
V = {"X-Visitor": f"emu-visitor-{uuid.uuid4().hex[:8]}"}
m = c.get("/api/meta", headers=V).json()
check(m["backend"] == "gcp" and "BigQuery dataset" in m["data_source"], f"meta in gcp mode: {m['data_source']}")
for cid in ("118", "124", "121"):
    store.db.collection("results").document(cid).delete()
d = c.get("/api/cases/118", headers=V).json()
check(d["result"]["verdict"] == "APPROVED_FOR_REVIEW", "seed baseline computed and stored in Firestore")
check(store.get_result("118")["created_at"] == d["result"]["created_at"], "stored baseline matches what was served")
cases = c.get("/api/cases", headers=V).json()
check(any(x["case_id"] == "118" and x["verdict"] for x in cases), "case list reads summaries from Firestore")


def sse(path, headers=None):
    with c.stream("GET", path, headers=headers or {}) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


ev = sse(f"/api/cases/124/live?v={V['X-Visitor']}")
check(ev[-1]["type"] == "done", f"live run streams to done ({len(ev)} events)")
mine = c.get("/api/cases/124", headers=V).json()["result"]["created_at"]
check(mine == ev[-1]["result"]["created_at"], "visitor's own run saved and served back")
for s in ("D2", "D3", "D4", "D5"):
    r = c.post("/api/cases/124/approve", headers=V, json={"section": s, "name": "Emu Tester", "result_at": mine})
    assert r.status_code == 200, r.text
rep = c.get(f"/api/cases/124/report?lang=ja&v={V['X-Visitor']}")
check(rep.status_code == 200 and "8D" in rep.text, "signed export works with Firestore approvals")
c.post("/api/cases/124/reset", headers=V)
check(c.get(f"/api/cases/124/report?lang=en&v={V['X-Visitor']}").status_code == 409, "reset clears Firestore approvals")

from PIL import Image  # noqa: E402
buf = io.BytesIO()
Image.new("RGB", (1200, 800), (100, 110, 120)).save(buf, "PNG")
r = c.post("/api/cases", headers=V, files={"photo": ("p.png", buf.getvalue(), "image/png")},
           data={"complaint": "Clips cracked near the hinge, 5 of 400 parts.", "defect": "crack",
                 "date_code_start": "2026-10-06", "date_code_end": "2026-10-11"})
check(r.status_code == 200, f"user case created in Firestore ({r.status_code})")
uid = r.json()["case_id"]
ev = sse(f"/api/cases/{uid}/live?v={V['X-Visitor']}")
check(ev[-1]["type"] == "done", "user case live run done")
case = c.get(f"/api/cases/{uid}", headers=V).json()
check(case["result"] and case["case"]["photo"], "user case result + photo stored")
ph = c.get(f"/api/photos/{case['case']['photo']}?v={V['X-Visitor']}")
check(ph.status_code == 200 and ph.headers["content-type"] == "image/jpeg", "photo served to its owner")
check(c.get(f"/api/photos/{case['case']['photo']}?v=other-visitor-01").status_code == 404, "photo hidden from others")
check(any(x["case_id"] == uid for x in c.get("/api/cases", headers=V).json()), "user case listed (where + limit query)")

print("PROBLEMS:", problems or "none")
sys.exit(1 if problems else 0)
