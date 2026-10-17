"""Generate the SYNTHETIC plant data (seed 42) and load it into BigQuery.

Usage (Cloud Shell): python scripts/load_bigquery.py --project MY_PROJECT --dataset plant_quality
Idempotent: every table is replaced (WRITE_TRUNCATE).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rae.data.generator import generate  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--dataset", default="plant_quality")
    ap.add_argument("--location", default="US")
    args = ap.parse_args()

    from google.cloud import bigquery

    client = bigquery.Client(project=args.project, location=args.location)
    ds = bigquery.Dataset(f"{args.project}.{args.dataset}")
    ds.location = args.location
    ds.description = "Root & Escape demo: SYNTHETIC injection-moulding plant data (seed 42). Not real plant data."
    client.create_dataset(ds, exists_ok=True)

    plant = generate(42)
    for name, df in plant.tables().items():
        df = df.copy()
        for c in df.columns:
            if c.endswith("_date") or c in ("gauge_cal_due", "insp_date", "closed_date"):
                df[c] = pd.to_datetime(df[c]).dt.date
            if c == "prod_ts":
                df[c] = pd.to_datetime(df[c])
        job = client.load_table_from_dataframe(
            df, f"{args.project}.{args.dataset}.{name}",
            job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"))
        job.result()
        print(f"loaded {name}: {len(df)} rows")


if __name__ == "__main__":
    main()
