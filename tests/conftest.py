import os
import sys
import tempfile
from pathlib import Path

# Isolated local storage for tests. This must be set before rae.config is imported.
os.environ.setdefault("RAE_LOCAL_DIR", tempfile.mkdtemp(prefix="rae-test-"))
os.environ.setdefault("RAE_BACKEND", "local")
os.environ.setdefault("RAE_USE_LLM", "false")
os.environ.setdefault("RAE_RUNS_PER_IP_PER_10MIN", "100000")  # every test request comes from one address
os.environ.setdefault("RAE_CORS_ORIGINS", "https://demo-rae.web.app")  # stands in for the Firebase Hosting origin
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The deepeval suite in tests/evals runs only on request: RAE_EVALS=1 deepeval test run tests/evals/test_rae_e2e.py
collect_ignore_glob = [] if os.getenv("RAE_EVALS") else ["evals/*"]
