"""Print a compact per-metric scorecard for the e2e eval (same app, dataset and metrics as test_rae_e2e.py).

    python tests/evals/summarize.py            # add --json to print machine-readable results
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from deepeval.test_case import LLMTestCase  # noqa: E402

import rae_app  # noqa: E402
from metrics import E2E_METRICS  # noqa: E402


def main() -> dict:
    goldens = json.loads((HERE / ".dataset.json").read_text(encoding="utf-8"))
    per_metric = {m.__name__: [0, 0] for m in E2E_METRICS}
    failures = []
    for g in goldens:
        run = rae_app.run_rae(g["input"], g["additional_metadata"])
        tc = LLMTestCase(input=g["input"], actual_output=rae_app.report_text(run["result"]),
                         expected_output=g["expected_output"], metadata=run, name=g["name"])
        for m in E2E_METRICS:
            m.measure(tc)
            per_metric[m.__name__][1] += 1
            if m.is_successful():
                per_metric[m.__name__][0] += 1
            else:
                failures.append((g["name"], m.__name__, m.reason))
    goldens_passed = len(goldens) - len({f[0] for f in failures})
    out = {"mode": rae_app.MODE, "goldens": len(goldens), "goldens_passed": goldens_passed,
           "metrics": {k: f"{p}/{n}" for k, (p, n) in per_metric.items()},
           "failures": [{"golden": a, "metric": b, "reason": c} for a, b, c in failures]}
    return out


if __name__ == "__main__":
    res = main()
    if "--json" in sys.argv:
        print(json.dumps(res, indent=1, ensure_ascii=False))
    else:
        print(f"mode={res['mode']}  goldens passing every metric: {res['goldens_passed']}/{res['goldens']}")
        for k, v in res["metrics"].items():
            print(f"  {v:>6}  {k}")
        for f in res["failures"]:
            print(f"  FAIL {f['golden']}: {f['metric']} -> {f['reason']}")
