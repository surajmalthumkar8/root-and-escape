"""Seed investigation cases. Complaint texts and photos are SYNTHETIC; `truth` is only used by tests and the benchmark."""
from __future__ import annotations

from datetime import date

SEED_CASES = [
    {
        "case_id": "118",
        "title": "CL-204 snap clip cracked at OEM line 3",
        "customer": "OEM-J (Japan)",
        "defect": "crack",
        "received": "2026-10-17T09:12:00+07:00",
        "date_code_start": "2026-10-06",
        "date_code_end": "2026-10-11",
        "language": "ja",
        "complaint": (
            "件名：【不具合報告】CL-204 スナップクリップ 割れ発生のご連絡\n\n"
            "品質保証ご担当者様\n\n"
            "いつもお世話になっております。本日、弊社第3組立ラインにて、貴社納入品 CL-204 スナップクリップの"
            "フック根元部に割れが確認されました。受入品 2,400 個のうち 37 個で同様の割れを確認しております。"
            "該当品の日付コードは 2026/10/06 〜 2026/10/11 です。写真を添付いたします。\n\n"
            "つきましては、24時間以内の暫定処置（在庫・輸送中品の封じ込め）および 8D 報告書のご提出をお願いいたします。\n\n"
            "OEM-J 品質管理部"
        ),
        "photo": "case118_defect.png",
        "golden": "golden_cl204.png",
        "truth": {"segment": {"press": "P4"}, "kind": "caused", "confounder": "crew",
                  "occurrence": "dryer dew point", "escape": "sampling"},
    },
    {
        "case_id": "124",
        "title": "CL-204 burrs on the snap edge",
        "customer": "OEM-J (Japan)",
        "defect": "burr",
        "received": "2026-10-16T15:40:00+07:00",
        "date_code_start": "2026-09-29",
        "date_code_end": "2026-10-09",
        "language": "en",
        "complaint": (
            "Subject: Burrs on CL-204 snap clips\n\n"
            "Our incoming inspection found sharp burrs on the snap edge of CL-204 clips. "
            "The burrs scratch our wire harness insulation during assembly. 19 of 1,600 parts sampled are affected. "
            "Date codes 2026-09-29 to 2026-10-09. Please contain suspect stock and send an 8D within 5 days.\n\n"
            "OEM-J Supplier Quality"
        ),
        "photo": "case124_defect.png",
        "golden": "golden_cl204.png",
        "truth": {"segment": {"press": "P2", "cavity": "C3"}, "kind": "caused",
                  "occurrence": "mould wear", "escape": "gauge calibration"},
    },
    {
        "case_id": "121",
        "title": "CL-204 light scratches reported",
        "customer": "OEM-J (Japan)",
        "defect": "scratch",
        "received": "2026-10-15T11:05:00+07:00",
        "date_code_start": "2026-10-07",
        "date_code_end": "2026-10-13",
        "language": "ja",
        "complaint": (
            "件名：CL-204 表面キズについて\n\n"
            "CL-204 の一部に軽微な表面キズが見られました。3,000 個中 11 個です。日付コード 2026/10/07 〜 2026/10/13。"
            "原因調査をお願いいたします。\n\nOEM-J 品質管理部"
        ),
        "photo": "case121_defect.png",
        "golden": "golden_cl204.png",
        "truth": {"segment": None, "kind": "no_cause"},
    },
]


def get_seed(case_id: str) -> dict | None:
    for c in SEED_CASES:
        if c["case_id"] == case_id:
            return c
    return None


def parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])
