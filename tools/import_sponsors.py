#!/usr/bin/env python3
"""Import the raw H-1B sponsor spreadsheet into data/companies.csv.

The raw file is never edited; this script normalizes it (English column names,
URLs with a scheme, split AI-relevance column). Re-running is safe: derived
columns (career_url, ats, slug, status, ...) already in data/companies.csv are
carried over by employer name, so detector results and manual fixes survive.

Usage: python tools/import_sponsors.py [raw_csv]
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = ROOT / "data" / "raw" / "h1b_sponsor_company_49cities.csv"
OUT_PATH = ROOT / "data" / "companies.csv"

RAW_COLUMNS = {
    "Employer": "employer",
    "覆盖城市数": "cities",
    "FY2025 LCA 合计": "lca_fy2025",
    "公司官网": "homepage",
    "行业/类型": "sector",
    "主要 Sponsor 岗位": "top_roles",
}
RELEVANCE = {"高": "high", "中": "medium", "低": "low"}
# Filled by later stages or by hand; preserved across re-imports.
DERIVED = ["career_url", "ats", "slug", "status", "checked_at", "notes"]
FIELDS = ["employer", "cities", "lca_fy2025", "ai_relevance", "ai_share",
          "homepage", "domain", "sector", "top_roles"] + DERIVED


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value or value == "未确认":
        return ""
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    return value


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.") if url else ""


def parse_relevance(value: str) -> tuple[str, str]:
    """'高 (30.0%)' -> ('high', '0.300')."""
    m = re.match(r"\s*(\S+)\s*\(([\d.]+)%\)", value)
    if not m or m.group(1) not in RELEVANCE:
        raise ValueError(f"unexpected Data/AI 相关度 value: {value!r}")
    return RELEVANCE[m.group(1)], f"{float(m.group(2)) / 100:.3f}"


def normalize_row(raw: dict) -> dict:
    row = {new: (raw[old] or "").strip() for old, new in RAW_COLUMNS.items()}
    row["homepage"] = normalize_url(row["homepage"])
    row["domain"] = domain_of(row["homepage"])
    row["ai_relevance"], row["ai_share"] = parse_relevance(raw["Data/AI 相关度"])
    row.update({k: "" for k in DERIVED})
    return row


def main() -> None:
    raw_path = Path(sys.argv[1]) if len(sys.argv) > 1 else RAW_PATH
    with raw_path.open(encoding="utf-8-sig", newline="") as f:
        rows = [normalize_row(r) for r in csv.DictReader(f)]

    previous = {}
    if OUT_PATH.exists():
        with OUT_PATH.open(encoding="utf-8", newline="") as f:
            previous = {r["employer"]: r for r in csv.DictReader(f)}
    kept = 0
    for row in rows:
        old = previous.get(row["employer"])
        if old and any(old.get(k) for k in DERIVED):
            row.update({k: old.get(k, "") for k in DERIVED})
            kept += 1
        # Recomputed every run, so a URL added to the raw data clears it.
        if not row["homepage"] and not row["career_url"]:
            row["status"] = row["status"] or "needs_url"
        elif row["status"] == "needs_url":
            row["status"] = ""

    rows.sort(key=lambda r: -int(r["lca_fy2025"]))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    dropped = set(previous) - {r["employer"] for r in rows}
    print(f"wrote {len(rows)} employers to {OUT_PATH.relative_to(ROOT)} "
          f"({kept} kept derived fields; {len(dropped)} no longer in raw data)")


if __name__ == "__main__":
    main()
