"""Runtime settings. Everything comes from environment variables, and no secrets are needed.
On Cloud Run the service account authenticates Gemini, BigQuery, Firestore and Cloud Storage."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    backend: str = os.getenv("RAE_BACKEND", "local")            # local | gcp
    project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location: str = os.getenv("GOOGLE_CLOUD_LOCATION", "global")  # Gemini endpoint
    dataset: str = os.getenv("RAE_DATASET", "plant_quality")
    bq_location: str = os.getenv("RAE_BQ_LOCATION", "US")
    bucket: str = os.getenv("RAE_BUCKET", "")
    model: str = os.getenv("RAE_MODEL", "gemini-3.8-flash")
    model_lite: str = os.getenv("RAE_MODEL_LITE", "gemini-3.5-flash-lite")
    use_llm: bool = _bool("RAE_USE_LLM", False)                    # off = deterministic mode
    use_bqml: bool = _bool("RAE_USE_BQML", True)
    live_runs_per_day: int = int(os.getenv("RAE_LIVE_RUNS_PER_DAY", "30"))
    runs_per_visitor_per_day: int = int(os.getenv("RAE_RUNS_PER_VISITOR_PER_DAY", "5"))
    max_upload_mb: int = int(os.getenv("RAE_MAX_UPLOAD_MB", "8"))
    local_dir: str = os.getenv("RAE_LOCAL_DIR", "var")

    @property
    def gcp(self) -> bool:
        return self.backend == "gcp"


settings = Settings()
