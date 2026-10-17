from datetime import date

import pytest

from rae import analytics as A
from rae.benchmark import run as run_benchmark
from rae.challenger import check_draft, verdict
from rae.data.cases import SEED_CASES, get_seed
from rae.data.generator import generate
from rae.report import build_report, scripted_investigation
from rae.tools import EvidenceLog, Toolbox


@pytest.fixture(scope="module")
def plant():
    return generate(42)


def toolbox(plant, cid):
    return Toolbox(plant, get_seed(cid), EvidenceLog())


def test_generator_is_deterministic():
    a, b = generate(7), generate(7)
    assert a.production.equals(b.production)
    assert len(a.production) == 3960  # 55 days x 6 presses x 3 shifts x 4 cavities


def test_case_118_finds_press4_and_rejects_crew(plant):
    tb = toolbox(plant, "118")
    draft = scripted_investigation(tb)
    assert draft["occurrence"]["segment"] == {"press": "P4"}
    assert "dryer" in draft["occurrence"]["statement"]
    assert "sampling" in draft["escape"]["statement"]
    crew = [h for h in draft["hypotheses"] if h["hypothesis"] == "crew B"]
    assert crew and crew[0]["ruling"] == "REJECTED"
    assert verdict(check_draft(draft, tb)) == "APPROVED_FOR_REVIEW"
    cont = next(i for i in tb.log.items if i["tool"] == "containment")["data"]
    assert cont["targeted_lots"] * 4 < cont["blanket_lots"]


def test_case_124_cavity_and_gauge(plant):
    tb = toolbox(plant, "124")
    draft = scripted_investigation(tb)
    assert draft["occurrence"]["segment"] == {"press": "P2", "cavity": "C3"}
    assert "maintenance limit" in draft["occurrence"]["statement"]
    assert "G-02" in draft["escape"]["statement"]
    assert verdict(check_draft(draft, tb)) == "APPROVED_FOR_REVIEW"


def test_case_121_abstains(plant):
    tb = toolbox(plant, "121")
    draft = scripted_investigation(tb)
    assert draft["insufficient_evidence"] is True
    res = check_draft(draft, tb)
    assert verdict(res) == "APPROVED_FOR_REVIEW"
    assert {r["rule"] for r in res} == {"analysis_was_run", "no_claim_without_signal"}


def test_abstaining_when_there_is_a_signal_is_rejected(plant):
    tb = toolbox(plant, "118")
    scripted_investigation(tb)
    res = check_draft({"insufficient_evidence": True}, tb)
    assert verdict(res) == "REJECTED"
    assert any(r["rule"] == "no_abstain_with_signal" and not r["passed"] for r in res)


def test_draft_without_analysis_is_rejected(plant):
    tb = toolbox(plant, "118")  # no tool has been called, so there is nothing to ground a claim in
    for draft in ({"insufficient_evidence": True},
                  {"insufficient_evidence": False, "occurrence": {"statement": "x", "evidence": []}, "escape": {}}):
        res = check_draft(draft, tb)
        assert verdict(res) == "REJECTED" and res[0]["rule"] == "analysis_was_run" and not res[0]["passed"]


def test_challenger_rejects_operator_error(plant):
    tb = toolbox(plant, "118")
    good = scripted_investigation(tb)
    bad = {**good, "occurrence": {**good["occurrence"],
                                  "statement": "Occurrence: operator error by the night crew B on press P4."}}
    res = check_draft(bad, tb)
    failed = {r["rule"] for r in res if not r["passed"]}
    assert "no_people_blame_without_stratified_effect" in failed


def test_challenger_rejects_missing_escape_and_fake_numbers(plant):
    tb = toolbox(plant, "118")
    good = scripted_investigation(tb)
    bad = {**good, "escape": {"statement": "", "evidence": []},
           "occurrence": {**good["occurrence"], "statement": good["occurrence"]["statement"] + " Crack rate was 12.34%.",
                          "evidence": good["occurrence"]["evidence"] + ["E-99"]}}
    failed = {r["rule"] for r in check_draft(bad, tb) if not r["passed"]}
    assert {"requires_occurrence_and_escape", "evidence_must_resolve", "numbers_come_from_evidence"} <= failed


def test_claim_without_signal_is_rejected(plant):
    tb = toolbox(plant, "121")
    scripted_investigation(tb)
    fake = {"insufficient_evidence": False,
            "occurrence": {"statement": "Occurrence: press P1 scratches.", "segment": {"press": "P1"}, "evidence": ["E-01"]},
            "escape": {"statement": "Escape: inspection missed it.", "evidence": ["E-01"]}}
    failed = {r["rule"] for r in check_draft(fake, tb) if not r["passed"]}
    assert "no_claim_without_signal" in failed


def test_report_sections_en_ja(plant):
    tb = toolbox(plant, "118")
    draft = scripted_investigation(tb)
    cont = next(i for i in tb.log.items if i["tool"] == "containment")["data"]
    intake = {"d2": {"what": "crack", "where": "OEM line 3", "how_many": "37 of 2,400"}}
    en = build_report(get_seed("118"), intake, draft, cont, "en")
    ja = build_report(get_seed("118"), intake, draft, cont, "ja")
    assert set(en) >= {"D1", "D2", "D3", "D4", "D4_escape", "D5", "D6", "D7", "D8"}
    assert str(cont["targeted_lots"]) in en["D3"] and str(cont["targeted_lots"]) in ja["D3"]


def test_benchmark_meets_bar_and_published_file_is_honest():
    import json
    from pathlib import Path
    b = run_benchmark()
    assert b["controls_abstained"] == b["controls_total"]      # never invents a cause on the controls
    assert b["caused_found"] >= b["caused_total"] - 2
    assert b["mean_containment_recall"] >= 0.85
    published = json.loads((Path(__file__).resolve().parents[1] / "rae" / "data" / "benchmark.json").read_text(encoding="utf-8"))
    assert json.loads(json.dumps(b, default=str)) == published  # the UI shows exactly what the code produces


def test_bigquery_ml_is_prefetched_and_waited_for_at_most_once(plant, monkeypatch):
    import time
    import rae.tools as T
    calls = []

    def slow_engine(defect, start, end):
        calls.append(defect)
        time.sleep(1.5)
        return [{"segment": "press=P4"}]

    def two_runs(tb):
        t0 = time.monotonic()
        tb.is_is_not()
        tb.is_is_not()  # the second call must not wait again or start a second BigQuery job
        return time.monotonic() - t0

    baseline = two_runs(Toolbox(plant, get_seed("118"), EvidenceLog()))  # the pandas work alone, on this machine
    monkeypatch.setattr(T, "BQML_WAIT_S", 0.1)
    tb = Toolbox(plant, get_seed("118"), EvidenceLog(), slow_engine)
    tb.prefetch_contribution()
    extra = two_runs(tb) - baseline
    assert extra < 0.8 and calls == ["crack"]  # waited about 0.1 s once; waiting for the engine would add 1.5 s
    assert "did not answer" in tb.last_is_is_not["bqml_error"]
    fast = Toolbox(plant, get_seed("118"), EvidenceLog(), lambda d, s, e: [{"segment": "press=P4"}])
    fast.is_is_not()
    assert fast.last_is_is_not["bqml_insights"] and "BigQuery ML" in fast.last_is_is_not["method"]


def test_mechanism_must_fit_the_defect(plant):
    tb = toolbox(plant, "118")  # cracks
    good = scripted_investigation(tb)
    assert "resin maker's limit" in good["occurrence"]["statement"]
    for claim, ok in (("Occurrence: the press P4 mould wore past its maintenance limit, so it flashed.", False),
                      ("Occurrence: the resin dryer on press P4 failed, so the resin was wet.", True),
                      ("Occurrence: mechanism not yet confirmed on press P4; the mould is worn.", False),  # no loophole
                      ("Occurrence: mechanism not yet confirmed on press P4; no logged parameter explains it.", True)):
        res = check_draft({**good, "occurrence": {**good["occurrence"], "statement": claim}}, tb)
        rule = [r for r in res if r["rule"] == "mechanism_fits_defect"]
        assert (not rule or rule[0]["passed"]) == ok, (claim, rule)


def test_located_but_unexplained_cause_is_reported_as_open_not_invented():
    from rae.data.generator import Planted
    start, end = date(2026, 10, 1), date(2026, 10, 8)
    p = generate(seed=100, planted_story=False, extra=[Planted("crack", {"press": "P3"}, start, end, 0.035)])
    case = {**get_seed("118"), "case_id": "T-1", "date_code_start": "2026-10-01", "date_code_end": "2026-10-08"}
    tb = Toolbox(p, case, EvidenceLog())
    d = scripted_investigation(tb)
    assert d["occurrence"]["segment"] == {"press": "P3"}
    assert "not yet confirmed" in d["occurrence"]["statement"] and "not yet identified" in d["escape"]["statement"]
    assert d["open_points"] == ["Mechanism not yet confirmed", "Escape point not yet identified"]
    assert verdict(check_draft(d, tb)) == "APPROVED_FOR_REVIEW"


def test_pure_interaction_is_found_with_a_stricter_bar():
    from rae.data.generator import Planted
    start, end = date(2026, 10, 3), date(2026, 10, 12)
    seg = {"press": "P1", "cavity": "C4"}
    p = generate(seed=113, planted_story=False, extra=[Planted("scratch", seg, start, end, 0.012)])
    r = A.is_is_not(p.production, "scratch", start, end)
    assert r["significant"] and r["primary_segment"] == seg and r["refined_by"] == "interaction"
