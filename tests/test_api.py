import json

from fastapi.testclient import TestClient

from rae.main import app

client = TestClient(app)
V = {"X-Visitor": "test-visitor-0001"}


def sse(path):
    with client.stream("GET", path) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def test_health_and_meta():
    assert client.get("/healthz").json()["ok"]
    m = client.get("/api/meta", headers=V).json()
    assert m["gemini"] is False and m["synthetic"] is True


def test_seed_case_loads_with_baseline():
    cases = client.get("/api/cases", headers=V).json()
    assert {c["case_id"] for c in cases} >= {"118", "121", "124"}
    d = client.get("/api/cases/118", headers=V).json()
    assert d["result"]["verdict"] == "APPROVED_FOR_REVIEW"
    assert d["result"]["containment"]["targeted_lots"] < d["result"]["containment"]["blanket_lots"]
    assert "truth" not in d["case"]


def test_live_run_streams_and_finishes():
    events = sse("/api/cases/124/live?v=test-visitor-0001")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "quota" and "evidence" in kinds and kinds[-1] == "done"


def test_export_locked_until_signed():
    assert client.get("/api/cases/118/report?lang=en&v=test-visitor-0002").status_code == 409
    for s in ("D2", "D3", "D4", "D5"):
        r = client.post("/api/cases/118/approve", headers={"X-Visitor": "test-visitor-0002"}, json={"section": s, "name": "Tester"})
        assert r.status_code == 200
    r = client.get("/api/cases/118/report?lang=ja&v=test-visitor-0002")
    assert r.status_code == 200 and "8D レポート" in r.text
    # approvals are per visitor: someone else still sees an unsigned report
    assert client.get("/api/cases/118/report?lang=en&v=test-visitor-0003").status_code == 409


def test_user_case_create_and_run():
    r = client.post("/api/cases", headers=V, data={"complaint": "Clips arrived with burrs on the edge, 12 of 900 parts.",
                                                   "defect": "burr", "date_code_start": "2026-09-29",
                                                   "date_code_end": "2026-10-09", "title": "Burr test"})
    assert r.status_code == 200, r.text
    cid = r.json()["case_id"]
    assert client.get(f"/api/cases/{cid}", headers={"X-Visitor": "someone-else-0001"}).status_code == 404
    events = sse(f"/api/cases/{cid}/live?v=test-visitor-0001")
    assert events[-1]["type"] in ("done", "quota")


def test_input_validation():
    bad = client.post("/api/cases", headers=V, data={"complaint": "short", "defect": "burr",
                                                     "date_code_start": "2026-09-29", "date_code_end": "2026-10-09"})
    assert bad.status_code == 422
    out_of_range = client.post("/api/cases", headers=V, data={"complaint": "x" * 30, "defect": "crack",
                                                              "date_code_start": "2025-01-01", "date_code_end": "2025-01-02"})
    assert out_of_range.status_code == 422
    assert client.get("/api/cases/../../etc", headers=V).status_code == 404
    assert client.post("/api/cases/118/approve", headers=V, json={"section": "D9", "name": "x"}).status_code == 422
