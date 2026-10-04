#!/usr/bin/env python3
"""Import DETE's official company workbooks, without publishing private individuals.

Uses only stdlib zip/XML for XLSX. Validates all raw monthly and annual totals
before filtering; writes aggregate drop counts, never dropped names, to metadata.
Raw workbooks stay in memory and are never committed or saved by this command.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

APP = Path(__file__).resolve().parents[2]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import yaml
from backend.paths import COUNTRIES
from backend.permits.employer_names import is_individual, normalize_ie
from backend.permits.history import FIELDS, encode_monthly

DEFAULT_OUTPUT = COUNTRIES / "ie/sponsors-dete.csv"
OFFICIAL_HOSTS = {"enterprise.gov.ie", "www.enterprise.gov.ie", "gov.ie", "www.gov.ie", "assets.gov.ie"}
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
FORMAT = ("One row per legal employer and year. monthly_permits lists only the months with permits, as MM:count "
          "pairs (04:1;05:2); each year's published months are in sources[].months_covered, so a covered month "
          "missing from a row had none. Matching keys are derived from the employer name by the app.")


def statistics_url(year: int) -> str:
    return f"https://enterprise.gov.ie/en/publications/employment-permit-statistics-{year}.html"


def _official(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in OFFICIAL_HOSTS and not parts.username and not parts.password


def fetch(url: str, *, limit: int = 10_000_000) -> bytes:
    if not _official(url):
        raise ValueError("The importer only downloads from official DETE/government hosts.")
    request = Request(url, headers={"User-Agent": "CareerWorkspace/1.0 (public employment permit data import)"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=20) as response:
                if not _official(response.geturl()):
                    raise ValueError("The official download redirected to a non-government host.")
                data = response.read(limit + 1)
            break
        except (URLError, TimeoutError, OSError) as error:
            if attempt == 2 or isinstance(error, HTTPError) and error.code not in {429, 500, 502, 503, 504}:
                raise
            time.sleep(attempt + 1)
    if len(data) > limit:
        raise ValueError("Official download exceeds the importer size limit.")
    return data


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() == "a":
            href = dict(attrs).get("href")
            if href:
                self.urls.append(href)


def workbook_url(html: str, year: int, page_url: str | None = None) -> str:
    parser = _Links()
    parser.feed(html)
    matches = []
    for href in parser.urls:
        url = urljoin(page_url or statistics_url(year), href)
        path = urlsplit(url).path.casefold()
        if _official(url) and path.endswith(".xlsx") and "companies" in path and "permit" in path and str(year) in path:
            matches.append(url)
    matches = list(dict.fromkeys(matches))
    if len(matches) != 1:
        raise ValueError(f"Expected one official companies workbook link for {year}; found {len(matches)}.")
    return matches[0]


def _column(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference or "")
    if not letters:
        raise ValueError("XLSX cell lacks a column reference.")
    number = 0
    for letter in letters[0]:
        number = number * 26 + ord(letter) - ord("A") + 1
    return number - 1


def _workbook_rows(data: bytes) -> tuple[str, list[dict[int, str]]]:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 2000 or sum(item.file_size for item in archive.infolist()) > 100_000_000:
                raise ValueError("XLSX expanded contents exceed the importer size limit.")
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            sheets = workbook.findall("s:sheets/s:sheet", NS)
            if not sheets:
                raise ValueError("Companies workbook has no worksheet.")
            sheet = next((item for item in sheets if item.get("name", "").casefold() == "export"), sheets[0])
            relationship = sheet.get(f"{{{NS['r']}}}id")
            relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            target = next((item.get("Target") for item in relations if item.get("Id") == relationship and item.get("TargetMode") != "External"), None)
            if not target:
                raise ValueError("Companies worksheet relationship is missing.")
            path = str(PurePosixPath(target.lstrip("/"))) if target.startswith("/") else str(PurePosixPath("xl") / target)
            if ".." in PurePosixPath(path).parts:
                raise ValueError("Unexpected worksheet path in companies workbook.")
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = ["".join(item.itertext()) for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
            rows = []
            for row in ET.fromstring(archive.read(path)).findall("s:sheetData/s:row", NS):
                cells = {}
                for cell in row.findall("s:c", NS):
                    value = cell.find("s:v", NS)
                    text = value.text or "" if value is not None else "".join(cell.find("s:is", NS).itertext()) if cell.find("s:is", NS) is not None else ""
                    if cell.get("t") == "s":
                        text = shared[int(text)]
                    cells[_column(cell.get("r"))] = text
                rows.append(cells)
            return sheet.get("name", ""), rows
    except (BadZipFile, ET.ParseError, KeyError, IndexError, TypeError) as error:
        raise ValueError("The official companies file is not a supported XLSX workbook.") from error


def _integer(value: str, row: int) -> int:
    if not str(value or "").strip():
        return 0
    try:
        number = Decimal(str(value).strip().replace(",", ""))
        if not number.is_finite() or number < 0 or number != number.to_integral_value():
            raise InvalidOperation
        return int(number)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid permit count in workbook row {row}.") from None


def _month(label: str) -> int | None:
    words = re.findall(r"[a-z]+", str(label).casefold())
    for number, name in enumerate(MONTHS, 1):
        if name.casefold() in words or name[:3].casefold() in words:
            return number
    return None


def parse_workbook(data: bytes, year: int) -> tuple[list[dict], dict]:
    """Validate the complete source before dropping private/ambiguous employer names."""
    sheet, rows = _workbook_rows(data)
    header = None
    total_column = None
    for position, row in enumerate(rows[:10]):
        for column, label in row.items():
            if re.sub(r"\s+", " ", label.casefold()).endswith("grand total"):
                total_column = column
        month_columns = {column: month for column, label in row.items() if (month := _month(label))}
        if month_columns:
            if row.get(0, "").strip().casefold() not in {"", "employer name", "employer", "row labels"}:
                raise ValueError("Companies workbook employer header changed.")
            header = position
            break
    if header is None or total_column is None or not month_columns or len(set(month_columns.values())) != len(month_columns):
        raise ValueError("Companies workbook month/Grand Total header changed.")
    months = sorted(month_columns.values())
    if months != list(range(1, max(months) + 1)):
        raise ValueError("Companies workbook does not cover contiguous months starting in January.")
    month_keys = [f"{year}-{month:02d}" for month in months]
    totals = []
    raw = []
    observed = {key: 0 for key in month_keys}
    for position, row in enumerate(rows[header + 1:], header + 2):
        employer = " ".join(row.get(0, "").split())
        if not employer:
            if any(str(value).strip() for value in row.values()):
                raise ValueError(f"Companies workbook row {position} lacks an employer name.")
            continue
        monthly = {f"{year}-{month:02d}": _integer(row.get(column, ""), position) for column, month in month_columns.items()}
        permits = _integer(row.get(total_column, ""), position)
        if sum(monthly.values()) != permits:
            raise ValueError(f"Monthly counts do not equal Grand Total in workbook row {position}.")
        if employer.casefold() in {"total", "grand total"}:
            totals.append((permits, monthly))
            continue
        raw.append({"employer_key": normalize_ie(employer), "employer": employer, "year": year,
                    "permits": permits, "months_covered": ";".join(month_keys), "monthly_permits": monthly})
        for key, count in monthly.items():
            observed[key] += count
    if len(totals) != 1 or sum(row["permits"] for row in raw) != totals[0][0] or observed != totals[0][1]:
        raise ValueError(f"Companies workbook {year} totals do not reconcile with its unique Total row.")
    kept_by_name, kept_raw_rows, dropped_permits = {}, 0, 0
    for row in raw:
        if not row["employer_key"] or is_individual(row["employer"]):
            dropped_permits += row["permits"]
        else:
            kept_raw_rows += 1
            # Excel can distinguish spellings containing repeated or nonbreaking
            # spaces. After whitespace normalization, preserve every source count
            # by combining only exactly identical employer names in this year.
            existing = kept_by_name.get(row["employer"])
            if existing is None:
                kept_by_name[row["employer"]] = row
            else:
                existing["permits"] += row["permits"]
                for month, count in row["monthly_permits"].items():
                    existing["monthly_permits"][month] += count
    kept = list(kept_by_name.values())
    return kept, {"year": year, "sheet": sheet, "months_covered": month_keys, "source_rows": len(raw),
                  "source_total": totals[0][0], "source_monthly_totals": observed,
                  "published_rows": len(kept), "published_raw_rows": kept_raw_rows,
                  "merged_whitespace_rows": kept_raw_rows - len(kept), "published_permits": sum(row["permits"] for row in kept),
                  "dropped_individual_or_ambiguous_rows": len(raw) - kept_raw_rows, "dropped_permits": dropped_permits,
                  "totals_validated": True}


def import_years(years: list[int], output: Path = DEFAULT_OUTPUT, *, downloader=fetch) -> dict:
    records, sources = [], []
    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for year in sorted(set(years)):
        page_url = statistics_url(year)
        page = downloader(page_url)
        url = workbook_url(page.decode("utf-8"), year, page_url)
        data = downloader(url)
        rows, meta = parse_workbook(data, year)
        records.extend(rows)
        sources.append({**meta, "statistics_url": page_url, "workbook_url": url,
                        "sha256": hashlib.sha256(data).hexdigest(), "retrieved_at": retrieved})
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    # Rows of one brand stay together (sorted by matching key), which keeps refresh diffs readable.
    for row in sorted(records, key=lambda item: (item["employer_key"], item["year"], item["employer"].casefold())):
        writer.writerow({"employer": row["employer"], "year": row["year"], "permits": row["permits"],
                         "monthly_permits": encode_monthly(row["monthly_permits"])})
    csv_data = stream.getvalue()
    metadata = {"schema_version": 2, "source": "DETE employment permits issued to companies", "format": FORMAT,
                "retrieved_at": retrieved, "privacy_policy": "Only identifiable corporate or institutional employers and curated organisations; individuals, households, sole traders and ambiguous names omitted.",
                "months_policy": "Published workbook months; no inferred counts. Rolling history uses complete calendar months.",
                "rows": len(records), "csv_sha256": hashlib.sha256(csv_data.encode()).hexdigest(), "sources": sources}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    csv_temp = output.with_suffix(output.suffix + ".tmp")
    meta_path = output.with_suffix(".meta.yml")
    meta_temp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    csv_temp.write_text(csv_data, encoding="utf-8", newline="")
    meta_temp.write_text(yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True), encoding="utf-8")
    csv_temp.replace(output)
    meta_temp.replace(meta_path)
    return metadata


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", default="2022-2026", help="inclusive range or comma-separated years")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if re.fullmatch(r"\d{4}-\d{4}", args.years):
        start, end = map(int, args.years.split("-"))
        years = list(range(start, end + 1))
    else:
        try:
            years = [int(year.strip()) for year in args.years.split(",")]
        except ValueError:
            parser.error("Use an inclusive year range or comma-separated years.")
    if not years or any(year < 2000 or year > datetime.now(timezone.utc).year for year in years):
        parser.error("Choose published years from 2000 to the current year.")
    try:
        result = import_years(years, args.output)
    except Exception as error:
        print(json.dumps({"imported": False, "error": str(error)}))
        return 1
    print(json.dumps({"imported": True, "csv": str(args.output), "rows": result["rows"], "sources": result["sources"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
