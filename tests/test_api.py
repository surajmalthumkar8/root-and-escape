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


def test_try_to_break_it_rejects_operator_error():
    client.get("/api/cases/118", headers=V)
    r = client.post("/api/cases/118/challenge", headers=V,
                    json={"occurrence": "Operator error: night crew B did not follow the work instruction. Retrain crew B."})
    body = r.json()
    assert r.status_code == 200 and body["verdict"] == "REJECTED"
    failed = {x["rule"] for x in body["rules"] if not x["passed"]}
    assert "no_people_blame_without_stratified_effect" in failed and "requires_occurrence_and_escape" in failed
    ok = client.post("/api/cases/118/challenge", headers=V, json={
        "occurrence": "Occurrence: the resin dryer on press P4 failed, so the resin was wet.",
        "escape": "Escape: inspection sampling on press P4 was cut, so the rise was not caught."}).json()
    assert ok["verdict"] == "APPROVED_FOR_REVIEW"
    fake = client.post("/api/cases/118/challenge", headers=V, json={
        "occurrence": "Occurrence: the dryer failed and the crack rate was 12.34%.",
        "escape": "Escape: inspection sampling was cut."}).json()
    assert any(x["rule"] == "numbers_come_from_evidence" and not x["passed"] for x in fake["rules"])


def test_deterministic_runs_do_not_use_quota_and_never_dead_end():
    before = client.get("/api/meta", headers={"X-Visitor": "quota-visitor-01"}).json()["quota"]["used_by_you"]
    for _ in range(7):  # more than the per-visitor limit
        events = sse("/api/cases/121/live?v=quota-visitor-01")
        assert events[-1]["type"] == "done"
    after = client.get("/api/meta", headers={"X-Visitor": "quota-visitor-01"}).json()["quota"]["used_by_you"]
    assert after == before


def test_visitor_runs_do_not_change_what_others_see():
    shared = client.get("/api/cases/124", headers={"X-Visitor": "viewer-a-000001"}).json()["result"]["created_at"]
    sse("/api/cases/124/live?v=viewer-b-000001")
    assert client.get("/api/cases/124", headers={"X-Visitor": "viewer-a-000001"}).json()["result"]["created_at"] == shared
    mine = client.get("/api/cases/124", headers={"X-Visitor": "viewer-b-000001"}).json()["result"]["created_at"]
    assert mine != shared
