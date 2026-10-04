"""A shared public DETE permit-history cache; contains no candidate information.

The bundled CSV (``countries/ie/sponsors-dete.csv``) has one row per legal employer and
year: ``employer, year, permits, monthly_permits``. ``monthly_permits`` lists only the months
with permits, as ``MM:count`` pairs (``04:1;05:1``); the months each year's workbook covered
are listed once, in ``sponsors-dete.meta.yml``, so a covered month missing from a row had no
permits. Matching keys are derived from the legal name when the cache is built
(``permits/employer_names.py``), so they always follow the current matching rules.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import sqlite3
import threading
from calendar import monthrange
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from backend.paths import COUNTRIES, DATA
from backend.permits import employer_names

DETE_CSV = COUNTRIES / "ie/sponsors-dete.csv"
DETE_DB = DATA / "sponsors/dete.db"
DETE_HUB_URL = "https://enterprise.gov.ie/en/what-we-do/workplace-and-skills/employment-permits/statistics/"
SOURCE = "DETE employment permits issued to companies"
FIELDS = ["employer", "year", "permits", "monthly_permits"]
_SCHEMA = """
CREATE TABLE IF NOT EXISTS permit_employers(id INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL, employer TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS permit_employer_keys(employer_key TEXT NOT NULL, employer_id INTEGER NOT NULL,
    PRIMARY KEY(employer_key,employer_id));
CREATE INDEX IF NOT EXISTS ix_permit_keys ON permit_employer_keys(employer_key);
CREATE TABLE IF NOT EXISTS permit_history(employer_id INTEGER NOT NULL, year INTEGER NOT NULL,
    permits INTEGER NOT NULL, months_covered TEXT NOT NULL, monthly_permits TEXT NOT NULL,
    PRIMARY KEY(employer_id,year));
CREATE TABLE IF NOT EXISTS permit_history_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
_BUILD_LOCK = threading.RLock()
# A change to the matching rules or this reader rebuilds the cache.
_CODE = (Path(__file__), Path(employer_names.__file__))
_MONTH_PAIR = re.compile(r"(0[1-9]|1[0-2]):([1-9]\d*)")
_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
log = logging.getLogger(__name__)


def encode_monthly(monthly: dict[str, int]) -> str:
    """``{"2026-04": 1, "2026-05": 0}`` as ``"04:1"``: months without permits are left out."""
    return ";".join(f"{month[5:]}:{count}" for month, count in sorted(monthly.items()) if count)


def decode_monthly(text: str, year: int) -> dict[str, int]:
    """``"04:1;05:2"`` for ``year`` as ``{"YYYY-04": 1, "YYYY-05": 2}``; refuses anything else."""
    monthly: dict[str, int] = {}
    for part in filter(None, str(text or "").split(";")):
        match = _MONTH_PAIR.fullmatch(part.strip())
        month = f"{year}-{match[1]}" if match else ""
        if not match or month in monthly:
            raise ValueError("A DETE CSV record has invalid monthly permit counts.")
        monthly[month] = int(match[2])
    return monthly


def describe(record: dict) -> str:
    """Counts by year for people: ``2025: 12; 2026 (Jan–Sep): 9``."""
    covered = record.get("year_months") or {}
    parts = []
    for year, count in (record.get("by_year") or {}).items():
        months = sorted(covered.get(str(year)) or [])
        label = str(year)
        if 0 < len(months) < 12:
            first, last = (_MONTH_NAMES[int(m[5:7]) - 1] for m in (months[0], months[-1]))
            label += f" ({first}–{last})" if first != last else f" ({first})"
        parts.append(f"{label}: {count:,}")
    return "; ".join(parts)


def _identity(name: str) -> str:
    # DETE supplies names, not registration identifiers. Preserve source spelling
    # instead of merging two legal employers on punctuation or geography alone.
    return name.strip()


def window_for(on: date) -> tuple[date, date]:
    """The 24 complete calendar months before on, never an inferred current month."""
    end_year, end_month = (on.year - 1, 12) if on.month == 1 else (on.year, on.month - 1)
    start_year, start_month = (end_year - 2, end_month + 1) if end_month < 12 else (end_year - 1, 1)
    return date(start_year, start_month, 1), date(end_year, end_month, monthrange(end_year, end_month)[1])


class PermitHistoryIndex:
    """SQLite cache over the bundled DETE CSV; conservative legal-name matching.

    USCIS's SponsorIndex subclasses this public-history interface while preserving
    its existing normalization and return fields. Defaults are resolved at runtime
    so portable tests can redirect DETE_DB without writing to real app data.
    """
    kind = "dete"

    def __init__(self, csv_path: Path | None = None, db_path: Path | None = None, *, aliases: dict | None = None):
        self.csv_path = Path(csv_path) if csv_path is not None else DETE_CSV
        self.db_path = Path(db_path) if db_path is not None else DETE_DB
        self.aliases = employer_names.aliases() if aliases is None else aliases

    def _conn(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _stamp(self):
        if not self.csv_path.is_file():
            return None
        stat = self.csv_path.stat()
        aliases = json.dumps(self.aliases, sort_keys=True, separators=(",", ":"))
        meta = self.csv_path.with_suffix(".meta.yml")
        meta_stat = meta.stat() if meta.is_file() else None
        code = ":".join(str(path.stat().st_mtime_ns) for path in _CODE if path.is_file())
        return (f"{stat.st_mtime_ns}:{stat.st_size}:{meta_stat.st_mtime_ns if meta_stat else 0}:{code}:"
                f"{hashlib.sha256(aliases.encode()).hexdigest()}")

    def version(self) -> str | None:
        """What the index is built from (data, metadata, code, aliases); None when there is no DETE CSV."""
        return self._stamp()

    def is_current(self) -> bool:
        stamp = self._stamp()
        if stamp is None or not self.db_path.is_file():
            return False
        try:
            with closing(self._conn()) as db:
                row = db.execute("SELECT value FROM permit_history_meta WHERE key='stamp'").fetchone()
            return bool(row and row[0] == stamp)
        except sqlite3.Error:
            return False

    def _records(self, metadata: dict) -> list[tuple]:
        """Every CSV row, checked against its year's published months and its own total."""
        months_by_year = {}
        for source in metadata.get("sources") or []:
            if isinstance(source, dict) and str(source.get("year", "")).isdigit():
                months_by_year[int(source["year"])] = [str(m) for m in source.get("months_covered") or []]
        records = []
        with self.csv_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != FIELDS:
                raise ValueError("The bundled DETE CSV has unexpected columns. Re-run the importer.")
            for row in reader:
                employer = (row.get("employer") or "").strip()
                if not employer or employer_names.is_individual(employer):
                    raise ValueError("The DETE index refuses individual or ambiguous employer records.")
                year, permits = int(row["year"]), int(row["permits"])
                monthly = decode_monthly(row.get("monthly_permits") or "", year)
                covered = months_by_year.get(year) or sorted(monthly)
                if permits < 0 or sum(monthly.values()) != permits or not set(monthly) <= set(covered):
                    raise ValueError("A DETE CSV record does not reconcile with its monthly counts.")
                records.append((employer, year, permits, covered, monthly))
        return records

    def build(self, force: bool = False) -> dict[str, Any]:
        with _BUILD_LOCK:
            stamp = self._stamp()
            if stamp is None:
                return {"built": False, "rows": 0, "reason": "sponsors-dete.csv is not present"}
            if not force and self.is_current():
                return {"built": False, "rows": self.stats()["rows"], "reason": "already current"}
            meta_path = self.csv_path.with_suffix(".meta.yml")
            metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
            metadata = metadata if isinstance(metadata, dict) else {}
            if metadata.get("csv_sha256") and hashlib.sha256(self.csv_path.read_bytes()).hexdigest() != metadata["csv_sha256"]:
                raise ValueError("The bundled DETE CSV does not match its provenance hash. Refresh public data.")
            records = self._records(metadata)
            with closing(self._conn()) as db, db:
                db.executescript(_SCHEMA)
                for table in ("permit_history", "permit_employer_keys", "permit_employers", "permit_history_meta"):
                    db.execute(f"DELETE FROM {table}")
                for employer, year, permits, months, monthly in records:
                    identity = _identity(employer)
                    db.execute("INSERT OR IGNORE INTO permit_employers(identity,employer) VALUES(?,?)", (identity, employer))
                    employer_id = db.execute("SELECT id FROM permit_employers WHERE identity=?", (identity,)).fetchone()[0]
                    for key in employer_names.keys_for(employer):
                        db.execute("INSERT OR IGNORE INTO permit_employer_keys VALUES(?,?)", (key, employer_id))
                    # Reject accidental duplicated source rows rather than double-count permits.
                    db.execute("INSERT INTO permit_history VALUES(?,?,?,?,?)", (employer_id, year, permits, json.dumps(months), json.dumps(monthly)))
                meta = {"stamp": stamp, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "rows": len(records), "sources": metadata.get("sources") or []}
                db.executemany("INSERT INTO permit_history_meta VALUES(?,?)", [(key, json.dumps(value)) if key != "stamp" else (key, value) for key, value in meta.items()])
            return {"built": True, "rows": len(records), "reason": "rebuilt from public DETE CSV"}

    def stats(self) -> dict[str, Any]:
        if not self.is_current():
            if not self.csv_path.is_file():
                return {"employers": 0, "rows": 0, "built_at": None, "csv": str(self.csv_path)}
            self.build()
        with closing(self._conn()) as db:
            count = db.execute("SELECT COUNT(*) FROM permit_employers").fetchone()[0]
            meta = {row[0]: json.loads(row[1]) for row in db.execute("SELECT key,value FROM permit_history_meta WHERE key!='stamp'")}
        return {"employers": count, "rows": meta.get("rows", 0), "built_at": meta.get("built_at"), "csv": str(self.csv_path)}

    def lookup(self, company: str, state: str = "", *, on: date | str | None = None) -> dict[str, Any]:
        on = date.fromisoformat(on) if isinstance(on, str) else on or date.today()
        start, end = window_for(on)
        empty = {"found": False, "match_type": "none", "matched_name": None, "matched_names": [], "matched_legal_names": [],
                 "by_year": {}, "per_year_counts": {}, "year_months": {}, "permits_24_months": 0, "permits": 0,
                 "window_start": start.isoformat(), "window_end": end.isoformat(), "months_covered": [],
                 "covered_months": 0, "coverage_complete": False, "source": SOURCE, "source_urls": []}
        keys = employer_names.keys_for(company)
        if not keys or not self.csv_path.is_file():
            return empty
        try:
            if not self.is_current():
                self.build()
        except (ValueError, OSError, sqlite3.Error, csv.Error, yaml.YAMLError) as error:
            # History only ranks postings, so bad public data must never stop a job page or a search.
            log.warning("DETE permit history is unavailable: %s", error)
            return {**empty, "match_type": "unavailable"}
        with closing(self._conn()) as db:
            matches = {}
            for key in keys:
                for row in db.execute("SELECT e.id,e.employer FROM permit_employer_keys k JOIN permit_employers e ON e.id=k.employer_id WHERE k.employer_key=?", (key,)):
                    matches[row["id"]] = (row["employer"], "exact")
            if not matches:
                for key in keys:
                    for alias in self.aliases.get(key, []):
                        for row in db.execute("SELECT e.id,e.employer FROM permit_employer_keys k JOIN permit_employers e ON e.id=k.employer_id WHERE k.employer_key=?", (alias,)):
                            matches[row["id"]] = (row["employer"], "alias")
            ambiguous_prefix = False
            if not matches:
                for key in keys:
                    candidates = db.execute("SELECT DISTINCT k.employer_key,e.id,e.employer FROM permit_employer_keys k JOIN permit_employers e ON e.id=k.employer_id WHERE k.employer_key LIKE ?", (key + " %",)).fetchall()
                    candidate_count = len({row["id"] for row in candidates})
                    if candidate_count > employer_names.MAX_ENTITIES and any(employer_names.prefix_match(key, row["employer_key"]) for row in candidates):
                        ambiguous_prefix = True
                    for row in candidates:
                        if employer_names.prefix_match(key, row["employer_key"], entity_count=candidate_count):
                            matches[row["id"]] = (row["employer"], "prefix")
                if ambiguous_prefix and not matches:
                    return {**empty, "match_type": "ambiguous"}
            if len(matches) > employer_names.MAX_ENTITIES:
                return {**empty, "match_type": "ambiguous"}
            if not matches:
                return empty
            placeholders = ",".join("?" for _ in matches)
            history = db.execute(f"SELECT * FROM permit_history WHERE employer_id IN ({placeholders})", list(matches)).fetchall()
            sources = json.loads(db.execute("SELECT value FROM permit_history_meta WHERE key='sources'").fetchone()[0])
        by_year, covered, recent, year_months = {}, set(), 0, {}
        for source in sources:
            months = [str(m) for m in source.get("months_covered") or []]
            year_months[str(source.get("year"))] = months
            for month in months:
                try:
                    day = date.fromisoformat(month + "-01")
                except (TypeError, ValueError):
                    continue
                if start <= day <= end:
                    covered.add(month)
        for row in history:
            year = str(row["year"])
            by_year[year] = by_year.get(year, 0) + row["permits"]
            monthly = json.loads(row["monthly_permits"])
            for month in json.loads(row["months_covered"]):
                try:
                    day = date.fromisoformat(month + "-01")
                except ValueError:
                    continue
                if start <= day <= end:
                    covered.add(month)
                    recent += monthly.get(month, 0)
        matched_names = sorted({value[0] for value in matches.values()}, key=str.casefold)
        source_urls = list(dict.fromkeys(source.get("workbook_url") or source.get("statistics_url") for source in sources if str(source.get("year")) in by_year and (source.get("workbook_url") or source.get("statistics_url"))))
        if not source_urls:
            source_urls = [f"https://enterprise.gov.ie/en/publications/employment-permit-statistics-{year}.html" for year in sorted(by_year)]
        types = {value[1] for value in matches.values()}
        return {**empty, "found": True, "match_type": next(iter(types)) if len(types) == 1 else "mixed", "matched_name": matched_names[0],
                "matched_names": matched_names, "matched_legal_names": matched_names, "by_year": dict(sorted(by_year.items())),
                "per_year_counts": dict(sorted(by_year.items())), "permits": sum(by_year.values()), "permits_24_months": recent,
                "year_months": {year: months for year, months in year_months.items() if year in by_year},
                "months_covered": sorted(covered), "covered_months": len(covered), "coverage_complete": len(covered) == 24,
                "source_urls": source_urls, "years": sorted(by_year)}


def index() -> PermitHistoryIndex:
    # Lightweight instance construction also honours dynamically redirected test paths.
    return PermitHistoryIndex()
