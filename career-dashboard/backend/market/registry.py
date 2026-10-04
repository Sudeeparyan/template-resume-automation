"""The employer registry: careers boards of employers in DETE's employment permit records.

``countries/<code>/employer-registry.csv`` is built by scripts/build_employer_registry.py, which
a maintainer runs when DETE publishes new figures. Each row is a board that, on the day it was
checked, listed at least one current posting in Ireland and named the same employer as DETE's
legal name (market/resolver.py). The file holds public data only: employer names, the board's
address on its applicant-tracking system and the counts it was chosen on.

At search time the "registry" source reads these boards like the employer directory, least
recently read first. A board that has gone (HTTP 404) is set aside for ``GONE_DAYS`` and then
tried again; the next registry build drops it for good.
"""

from __future__ import annotations

import csv
from pathlib import Path

from backend.paths import COUNTRIES

FIELDS = ["employer", "legal_name", "ats", "token", "host", "site", "permits_24m", "irish_postings", "name_check",
          "verified_on"]
GONE_DAYS = 30
ATS_LABELS = {"greenhouse": "Greenhouse", "lever": "Lever", "lever_eu": "Lever", "ashby": "Ashby",
              "smartrecruiters": "SmartRecruiters", "workable": "Workable", "recruitee": "Recruitee",
              "personio": "Personio", "teamtailor": "Teamtailor", "workday": "Workday"}


def path_for(market: str) -> Path:
    return COUNTRIES / market / "employer-registry.csv"


def load(path: Path) -> list[dict]:
    """The registry's rows (empty when the file is absent); a malformed file is an error, not an empty list."""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise ValueError(f"{path.name} has unexpected columns; rebuild it with build_employer_registry.py")
        return [dict(row) for row in reader]


def write(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDS})


def employer_rows(market: str, *, gone: set[tuple[str, str, str, str]] | None = None) -> list[dict]:
    """Registry boards as employer-feed rows (job_sources.employer_feed), boards set aside left out."""
    try:
        rows = load(path_for(market))
    except (OSError, ValueError, csv.Error):
        return []
    out = []
    for row in rows:
        identity = (row["ats"], row["token"], row["host"], row["site"])
        if gone and identity in gone:
            continue
        label = ATS_LABELS.get(row["ats"], row["ats"])
        out.append({"name": row["employer"], "ats": row["ats"], "token": row["token"],
                    **({"host": row["host"]} if row["host"] else {}), **({"site": row["site"]} if row["site"] else {}),
                    "legal_name": row["legal_name"], "_source": "registry", "_market": market,
                    "_vouched": (f"{row['legal_name']} appears in DETE's employment permit records, and this posting was "
                                 f"read from the employer's own careers board ({label}), which the registry matched to "
                                 f"that employer ({row['name_check']}).")})
    return out
