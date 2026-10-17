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
    assert res[0]["rule"] == "no_claim_without_signal"


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


def test_benchmark_meets_bar():
    b = run_benchmark()
    assert b["controls_abstained"] == b["controls_total"]
    assert b["caused_found"] >= 7
    assert b["mean_containment_recall"] >= 0.9
