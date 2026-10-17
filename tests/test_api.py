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


def test_signatures_belong_to_the_run_they_were_given_to():
    who = {"X-Visitor": "signer-visitor-01"}
    first = client.get("/api/cases/124", headers=who).json()["result"]["created_at"]
    for s in ("D2", "D3", "D4", "D5"):
        assert client.post("/api/cases/124/approve", headers=who, json={"section": s, "name": "Tester", "result_at": first}).status_code == 200
    assert client.get("/api/cases/124/report?lang=en&v=signer-visitor-01").status_code == 200
    sse("/api/cases/124/live?v=signer-visitor-01")  # a new run replaces what this visitor sees
    d = client.get("/api/cases/124", headers=who).json()
    assert d["result"]["created_at"] != first and d["approvals"] == {}
    assert client.get("/api/cases/124/report?lang=en&v=signer-visitor-01").status_code == 409
    stale = client.post("/api/cases/124/approve", headers=who, json={"section": "D2", "name": "Tester", "result_at": first})
    assert stale.status_code == 409


def test_deterministic_runs_have_an_abuse_cap(monkeypatch):
    import rae.store as store_mod
    monkeypatch.setattr(store_mod, "_limits", lambda kind: (10_000, 2) if kind == "det" else (30, 5))
    kinds = [sse("/api/cases/121/live?v=det-cap-visitor-01")[-1]["type"] for _ in range(3)]
    assert kinds == ["done", "done", "limit"]


def test_per_ip_limit(monkeypatch):
    import dataclasses
    import rae.main as main_mod
    monkeypatch.setattr(main_mod, "settings", dataclasses.replace(main_mod.settings, runs_per_ip_per_10min=2))
    ip = {"X-Forwarded-For": "203.0.113.77, 10.0.0.1"}

    def run():
        with client.stream("GET", "/api/cases/121/live?v=ip-limit-visitor", headers=ip) as r:
            return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")][-1]

    assert [run()["type"] for _ in range(3)] == ["done", "done", "limit"]


def _png(w, h, raw=False):
    import struct
    import zlib
    if raw:  # header only: claims a size without carrying the pixels
        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 130, 140)).save(buf, "PNG")
    return buf.getvalue()


def _new_case(photo_bytes, name="p.png"):
    return client.post("/api/cases", headers=V, files={"photo": (name, photo_bytes, "image/png")},
                       data={"complaint": "Clips arrived cracked near the hinge, 5 of 400 parts.", "defect": "crack",
                             "date_code_start": "2026-10-06", "date_code_end": "2026-10-11"})


def test_photo_upload_limits_and_ownership():
    ok = _new_case(_png(2400, 1800))
    assert ok.status_code == 200, ok.text
    cid = ok.json()["case_id"]
    case = client.get(f"/api/cases/{cid}", headers=V).json()["case"]
    got = client.get(f"/api/photos/{case['photo']}?v=test-visitor-0001")
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    from PIL import Image
    import io
    assert max(Image.open(io.BytesIO(got.content)).size) == 1600
    assert client.get(f"/api/photos/{case['photo']}?v=someone-else-0001").status_code == 404
    assert client.get(f"/api/photos/{case['photo']}").status_code == 404
    assert _new_case(_png(7000, 7000, raw=True)).status_code == 413     # over 40 MP: refused before decoding
    assert _new_case(_png(30000, 30000, raw=True)).status_code == 413   # decompression bomb
    assert _new_case(b"not an image at all", "x.png").status_code == 415
    assert _new_case(b"GIF89a" + b"\x00" * 40, "x.gif").status_code == 415


def test_shared_seed_result_only_upgrades_to_the_same_finding():
    from rae.main import upgrades_shared
    base = {"modes": {"investigation": "deterministic"}, "analysis": {"primary_segment": {"press": "P4"}},
            "draft": {"insufficient_evidence": False}, "verdict": "APPROVED_FOR_REVIEW"}
    gem = {**base, "modes": {"investigation": "gemini"}}
    assert upgrades_shared(gem, base)
    assert not upgrades_shared(base, base)                                                  # deterministic never upgrades
    assert not upgrades_shared({**gem, "verdict": "REJECTED"}, base)
    assert not upgrades_shared({**gem, "analysis": {"primary_segment": {"press": "P1"}}}, base)  # different finding
    assert not upgrades_shared({**gem, "draft": {"insufficient_evidence": True}}, base)
    assert not upgrades_shared(gem, gem)                                                    # only once


def test_stream_sends_heartbeats_during_quiet_stretches(monkeypatch):
    import asyncio
    import rae.main as main_mod

    async def slow_run(case, *a, **k):
        yield {"type": "stage", "stage": "intake"}
        await asyncio.sleep(0.35)  # a long Gemini call with nothing to report
        yield {"type": "done", "result": {"case_id": case["case_id"], "created_at": "2026-10-17T00:00:00+00:00",
                                          "verdict": "REJECTED", "modes": {"investigation": "deterministic"}, "notes": []}}

    monkeypatch.setattr(main_mod, "run_investigation", slow_run)
    monkeypatch.setattr(main_mod, "HEARTBEAT_S", 0.05)
    with client.stream("GET", "/api/cases/121/live?v=heartbeat-visitor-1") as r:
        lines = [x for x in r.iter_lines() if x]
    assert ": ping" in lines
    assert json.loads([x for x in lines if x.startswith("data: ")][-1][6:])["type"] == "done"


def test_try_to_break_it_rejects_the_judges_bypasses():
    client.get("/api/cases/118", headers=V)
    esc_ok = "Escape: inspection sampling on press P4 was cut, so the rise was not caught."
    tries = {
        "Gremlins sabotaged the machine at midnight.": "segment_matches_is_is_not",
        "Occurrence: the press P1 mould wore out.": "segment_matches_is_is_not",
        "Occurrence: on press P4 the person running the machine on nights made a mistake.": "no_people_blame_without_stratified_effect",
        "Occurrence: press P4 night-shift people forgot to check the dryer.": "no_people_blame_without_stratified_effect",
    }
    for claim, rule in tries.items():
        body = client.post("/api/cases/118/challenge", headers=V, json={"occurrence": claim, "escape": esc_ok}).json()
        failed = {x["rule"] for x in body["rules"] if not x["passed"]}
        assert body["verdict"] == "REJECTED" and rule in failed, (claim, failed)
    # naming both parts of a two-part segment is required (case 124: press P2 x cavity C3)
    client.get("/api/cases/124", headers=V)
    half = client.post("/api/cases/124/challenge", headers=V, json={
        "occurrence": "Occurrence: the press P2 mould ran past its maintenance limit.",
        "escape": "Escape: gauge G-02 was overdue for calibration, so inspection missed the burrs."}).json()
    full = client.post("/api/cases/124/challenge", headers=V, json={
        "occurrence": "Occurrence: the press P2 mould ran past its maintenance limit, and cavity C3 makes the burrs.",
        "escape": "Escape: gauge G-02 was overdue for calibration, so inspection missed the burrs."}).json()
    assert half["verdict"] == "REJECTED" and full["verdict"] == "APPROVED_FOR_REVIEW", (half["rules"], full["rules"])
