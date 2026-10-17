"""End-to-end eval of the whole 8D investigation over planted-truth plants.

    RAE_EVALS=1 deepeval test run tests/evals/test_rae_e2e.py
    # Gemini agents + a Gemini judge (Cloud Shell, after deploy):
    RAE_EVALS=1 RAE_EVAL_MODE=gemini RAE_EVAL_JUDGE=gemini deepeval test run tests/evals/test_rae_e2e.py

The dataset is built by tests/evals/build_dataset.py from the project's seeded generator; no golden is hand-written.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from deepeval import assert_test
from deepeval.dataset import EvaluationDataset, Golden
from deepeval.test_case import LLMTestCase

import rae_app
from metrics import E2E_METRICS

dataset = EvaluationDataset()
dataset.add_goldens_from_json_file(file_path=str(Path(__file__).resolve().parent / ".dataset.json"))


@pytest.mark.parametrize("golden", dataset.goldens, ids=[g.name for g in dataset.goldens])
def test_rae_e2e(golden: Golden):
    run = rae_app.run_rae(golden.input, golden.additional_metadata)
    test_case = LLMTestCase(
        input=golden.input,
        actual_output=rae_app.report_text(run["result"]),
        expected_output=golden.expected_output,
        metadata=run,
        name=golden.name,
    )
    assert_test(test_case=test_case, metrics=E2E_METRICS)
