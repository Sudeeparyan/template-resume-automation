#!/usr/bin/env python3
"""Refresh the public Irish occupation tables from DETE's official DOCX documents.

Only zip/XML in the standard library parses Word tables. An unknown header, orphaned
merge, invalid SOC code or duplicate row stops the import before the saved data changes.
Downloaded originals are optional and belong in an ignored runtime folder.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

import yaml

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO / "career-dashboard/backend/countries/ie/occupations.yml"
SOURCES = {
    "critical": {
        "title": "Critical Skills Occupations List",
        "page_url": "https://enterprise.gov.ie/en/what-we-do/workplace-and-skills/economic-migration-policy/occupations-lists-and-reviews/csol.html",
    },
    "ineligible": {
        "title": "Ineligible Occupations List",
        "page_url": "https://enterprise.gov.ie/en/what-we-do/workplace-and-skills/economic-migration-policy/occupations-lists-and-reviews/iol.html",
    },
}
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{" + NS["w"] + "}"
MAX_BYTES = 5_000_000
# Keep complete clauses rather than trying to turn prose into legal conclusions.
CONDITION = re.compile(
    r"specialis(?:ing|ed)|\bin manufacturing\b|\bwith (?:at least|a minimum|fluency|required|skills)\b"
    r"|\bwho (?:hold|are|has|have)\b|\bwithin the\b|\bemployed by\b|\bregistered\b"
    r"|\bqualified\b|\brelevant specialist\b|\bdomain knowledge\b|\bin [23]D\b",
    re.I,
)
EXCEPTION = re.compile(r"\b(?:with the exception of|except(?:ing)?|excluding)\b", re.I)


def _official(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in {"enterprise.gov.ie", "www.enterprise.gov.ie"} \
        and not parts.username and not parts.password


def fetch(url: str) -> bytes:
    if not _official(url):
        raise ValueError("Occupation sources must use DETE's official HTTPS website.")
    request = Request(url, headers={"User-Agent": "CareerWorkspace/1.0 (public Irish occupation data refresh)"})
    with urlopen(request, timeout=45) as response:
        if not _official(response.geturl()):
            raise ValueError("DETE source redirected outside the official website.")
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Occupation source exceeds the 5 MB import limit.")
    return data


class DocumentLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if urlsplit(href).path.casefold().endswith(".docx"):
                self.links.append(href)


def find_document(page: bytes, page_url: str, kind: str) -> str:
    parser = DocumentLinks()
    parser.feed(page.decode("utf-8"))
    candidates = {urljoin(page_url, link) for link in parser.links
                  if kind in urlsplit(link).path.casefold() and "occupation" in urlsplit(link).path.casefold()}
    if len(candidates) != 1:
        raise ValueError(f"Expected one {kind} occupation DOCX link on the official page; found {len(candidates)}.")
    url = candidates.pop()
    if not _official(url):
        raise ValueError("The occupation DOCX link is not an official HTTPS source.")
    return url


def _paragraph(p: ET.Element) -> str:
    chunks = []
    for node in p.iter():
        if node.tag == W + "t":
            chunks.append(node.text or "")
        elif node.tag in {W + "br", W + "cr"}:
            chunks.append("\n")
        elif node.tag == W + "tab":
            chunks.append("\t")
    return "".join(chunks).strip()


def _cell_text(cell: ET.Element) -> str:
    return "\n".join(text for p in cell.findall("w:p", NS) if (text := _paragraph(p)))


def _clauses(text: str, pattern: re.Pattern) -> list[str]:
    # Qualifiers can continue over later paragraphs. Preserve the entire tail verbatim.
    match = pattern.search(text)
    return [text[match.start():].strip()] if match else []


def parse_document(data: bytes, kind: str, *, source_url: str) -> list[dict]:
    if kind not in SOURCES:
        raise ValueError("Unknown occupation list.")
    if not _official(source_url):
        raise ValueError("Occupation table provenance must use DETE's official HTTPS website.")
    if len(data) > MAX_BYTES:
        raise ValueError("Occupation source exceeds the 5 MB import limit.")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_BYTES:
                raise ValueError("Word table XML exceeds the 5 MB import limit.")
            document = ET.fromstring(archive.read(info))
    except (BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError("The occupation source is not a readable Word DOCX table.") from exc
    tables = document.findall("w:body/w:tbl", NS)
    if not tables:
        raise ValueError("The occupation document contains no tables.")
    sha256 = hashlib.sha256(data).hexdigest()
    entries, seen, header_count = [], set(), 0
    for table in tables:
        previous = ["", "", "", ""]
        for row_number, row in enumerate(table.findall("w:tr", NS), 1):
            raw_cells = row.findall("w:tc", NS)
            if len(raw_cells) != 4 or any(cell.find("w:tcPr/w:gridSpan", NS) is not None for cell in raw_cells):
                raise ValueError(f"Unexpected occupation table layout at row {row_number}.")
            cells = []
            for column, cell in enumerate(raw_cells):
                value = _cell_text(cell)
                merge = cell.find("w:tcPr/w:vMerge", NS)
                if merge is not None and merge.get(W + "val", "continue") != "restart" and not value:
                    if not previous[column]:
                        raise ValueError(f"Orphaned vertical merge at row {row_number}.")
                    value = previous[column]
                cells.append(value)
            if cells[0].upper() == "SOC-3" and cells[2].upper() == "SOC-4":
                if "employment" not in cells[1].casefold() or "employment" not in cells[3].casefold():
                    raise ValueError("Unexpected occupation table header.")
                header_count += 1
                previous = ["", "", "", ""]
                continue
            if not header_count:
                raise ValueError("Occupation table has no recognised SOC-3 / SOC-4 header.")
            soc3, category, soc4, title = cells
            if soc4.casefold() == "all" and kind == "ineligible":
                soc3, soc4 = "all", "all"
            elif not re.fullmatch(r"\d{4}", soc4):
                raise ValueError(f"Invalid SOC-4 at row {row_number}: {soc4!r}.")
            else:
                # One publisher row (3417) leaves the repeated category blank without a
                # Word vMerge marker. Carry only inside the same SOC-3 prefix.
                if not soc3 and previous[0] == soc4[:3]:
                    soc3, category = previous[:2]
                if not re.fullmatch(r"\d{3}", soc3) or soc3 != soc4[:3]:
                    raise ValueError(f"Invalid or inconsistent SOC-3 at row {row_number}.")
            if not category or not title:
                raise ValueError(f"Missing occupation category or employment wording at row {row_number}.")
            identity = (kind, soc4)
            if identity in seen:
                raise ValueError(f"Duplicate {kind} SOC-4 {soc4}.")
            seen.add(identity)
            qualifiers = _clauses(title, CONDITION)
            exceptions = _clauses(title, EXCEPTION)
            entries.append({"id": f"{kind}:{soc4}", "list": kind, "soc3": soc3, "soc4": soc4,
                            "category": category, "title": title, "qualifiers": qualifiers,
                            "exceptions": exceptions, "conditional": bool(qualifiers or exceptions),
                            "source_url": source_url, "source_sha256": sha256})
            previous = [soc3, category, soc4, title]
    if not entries:
        raise ValueError("The occupation document contains no occupation rows.")
    return entries


def build_dataset(documents: dict[str, bytes], urls: dict[str, str], *, verified_at: date) -> dict:
    entries, sources = [], {}
    for kind, source in SOURCES.items():
        rows = parse_document(documents[kind], kind, source_url=urls[kind])
        sources[kind] = {**source, "url": urls[kind], "sha256": hashlib.sha256(documents[kind]).hexdigest(),
                         "bytes": len(documents[kind]), "row_count": len(rows),
                         "soc4_count": len({r["soc4"] for r in rows if r["soc4"] != "all"})}
        entries.extend(rows)
    return {"schema_version": 1, "verified_at": verified_at.isoformat(),
            "review_after": (verified_at + timedelta(days=90)).isoformat(),
            "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "notice": "Dated occupation-list facts only. Duties, qualifications and exceptions require confirmation. Not immigration advice.",
            "sources": sources, "entries": entries}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-dir", type=Path, help="optional local official DOCX files for an offline re-import")
    parser.add_argument("--save-originals", type=Path, help="optional ignored runtime folder for the downloaded DOCX originals")
    parser.add_argument("--verified-at", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    if args.source_dir and args.verified_at is None:
        parser.error("An offline re-import requires --verified-at with the date the official sources were checked.")
    verified_at = args.verified_at or date.today()
    documents, urls = {}, {}
    for kind, source in SOURCES.items():
        filename = "critical-skills-occupations-list.docx" if kind == "critical" else "ineligible-occupations-list.docx"
        if args.source_dir:
            urls[kind] = "https://enterprise.gov.ie/en/publications/publication-files/" + filename
            documents[kind] = (args.source_dir / filename).read_bytes()
        else:
            urls[kind] = find_document(fetch(source["page_url"]), source["page_url"], kind)
            documents[kind] = fetch(urls[kind])
    dataset = build_dataset(documents, urls, verified_at=verified_at)
    # Validate both tables before replacing anything. An interrupted write leaves the old
    # published file intact; the temporary file carries public data only.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.output.parent,
                                         prefix=args.output.stem + "-", suffix=".yml", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(yaml.safe_dump(dataset, sort_keys=False, allow_unicode=True, width=110))
        temporary.replace(args.output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if args.save_originals:
        args.save_originals.mkdir(parents=True, exist_ok=True)
        for kind, data in documents.items():
            filename = Path(urlsplit(urls[kind]).path).name
            (args.save_originals / filename).write_bytes(data)
    print(json.dumps({"output": str(args.output), "verified_at": dataset["verified_at"], "sources": dataset["sources"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
