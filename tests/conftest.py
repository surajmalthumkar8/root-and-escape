import os
import sys
import tempfile
from pathlib import Path

# Isolated local storage for tests. This must be set before rae.config is imported.
os.environ.setdefault("RAE_LOCAL_DIR", tempfile.mkdtemp(prefix="rae-test-"))
os.environ.setdefault("RAE_BACKEND", "local")
os.environ.setdefault("RAE_USE_LLM", "false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
