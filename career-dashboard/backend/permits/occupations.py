"""Conservative occupation-list matching from dated official entries and posting quotes.

This describes the occupation evidence only. It cannot establish a person's degree,
registration, experience, work permission or a permit outcome. Titles alone do not
decide list membership; specialisms and exceptions are left for confirmation.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
import re

import yaml

from backend.paths import COUNTRIES

LISTS_FILE = COUNTRIES / "ie/occupations.yml"
KEYWORDS_FILE = COUNTRIES / "ie/occupation-keywords.yml"


@lru_cache(maxsize=8)
def _read(path: str, modified: int, size: int) -> dict:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Occupation data must be a version 1 YAML mapping.")
    return value


def _load(path: Path) -> dict:
    stat = path.stat()
    return _read(str(path), stat.st_mtime_ns, stat.st_size)


def load_lists() -> dict:
    data = _load(LISTS_FILE)
    if not isinstance(data.get("entries"), list) or not data["entries"]:
        raise ValueError("Occupation data has no entries.")
    seen = set()
    for entry in data["entries"]:
        if not isinstance(entry, dict) or any(not isinstance(entry.get(field), str) or not entry[field]
                                            for field in ("id", "list", "soc3", "soc4", "category", "title", "source_url", "source_sha256")):
            raise ValueError("Occupation entry fields are missing or malformed.")
        if entry["id"] in seen or entry["list"] not in {"critical", "ineligible"} \
                or not re.fullmatch(r"[a-f0-9]{64}", entry["source_sha256"]):
            raise ValueError("Occupation entries have invalid identities or source hashes.")
        seen.add(entry["id"])
    return data


def load_keywords() -> list[dict]:
    rows = _load(KEYWORDS_FILE).get("rows")
    if not isinstance(rows, list):
        raise ValueError("Occupation keyword rows must be a list.")
    return rows


def _contains(text: str, phrase: str) -> re.Match | None:
    # Escape all curator text; there is no regex supplied by a posting or model.
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.I)


def _duty_quote(job: dict, phrases: list[str]) -> str:
    description = str(job.get("description") or "")
    # Proposed excerpts are usable only if they really occur verbatim in the posting.
    excerpts = job.get("duty_excerpts")
    if not isinstance(excerpts, list):
        excerpts = []
    candidates = [excerpt for excerpt in excerpts if isinstance(excerpt, str) and excerpt in description]
    candidates += [part for part in re.split(r"(?<=[.!?])\s+|\n+", description) if part.strip()]
    for excerpt in candidates:
        for phrase in phrases:
            match = _contains(excerpt, phrase)
            if match and not re.search(r"\b(?:not|never|no|without)\b.{0,40}$", excerpt[:match.start()], re.I):
                return excerpt.strip()
    return ""


def _match(entry: dict, *, method: str, duty_quote: str = "") -> dict:
    return {"id": entry["id"], "list": entry["list"], "soc3": entry["soc3"], "soc4": entry["soc4"],
            "category": entry["category"], "quote": entry["title"], "title": entry["title"],
            "qualifiers": list(entry.get("qualifiers") or []), "exceptions": list(entry.get("exceptions") or []),
            "conditional": bool(entry.get("conditional")), "source_url": entry["source_url"],
            "source_sha256": entry["source_sha256"], "match_type": method, "duty_quote": duty_quote}


def classify(job: dict, *, on: date | None = None) -> dict:
    """Return critical | ineligible | neither | unknown, with source and duty quotations.

    ``soc4`` (or ``occupation_soc4``) is a caller-supplied SOC 2010 code. With
    ``soc4_verified=True``, a curated whole-code entry can be matched directly;
    narrower or conditional entries need more evidence. Verification is also required
    before an absent code is reported as neither. Curated title aliases require a
    verbatim duty excerpt when no verified code mapping is supplied.
    All specialism/exception clauses still return unknown in this deterministic stage.
    """
    result = {"classification": "unknown", "status": "unknown", "matches": [], "soc4": None,
              "reason": "No occupation mapping has been confirmed from the posting's duties.",
              "verified_at": None, "review_after": None, "notice": "Not immigration advice."}
    if not isinstance(job, dict):
        return result
    try:
        data, keywords = load_lists(), load_keywords()
        verified = date.fromisoformat(str(data["verified_at"]))
        review = date.fromisoformat(str(data["review_after"]))
        if review < verified:
            raise ValueError("Occupation data dates are inconsistent.")
        result.update(verified_at=verified.isoformat(), review_after=review.isoformat())
        if not verified <= (on or date.today()) <= review:
            result["reason"] = "The bundled occupation lists need review for the requested date. Check the official documents."
            return result
        by_id = {entry["id"]: entry for entry in data["entries"]}
        for row in keywords:
            identity = f"{row['list']}:{row['soc4']}"
            if identity not in by_id or not isinstance(row.get("title_aliases"), list) \
                    or not isinstance(row.get("duty_phrases"), list) \
                    or not all(isinstance(value, str) and value for value in row["title_aliases"] + row["duty_phrases"]):
                raise ValueError("An occupation keyword row references missing or malformed source data.")
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, yaml.YAMLError):
        result["reason"] = "The official occupation data cannot be read or validated; no list classification is available."
        return result

    if job.get("employment_context") == "private_home":
        generic = by_id.get("ineligible:all")
        result["matches"] = [_match(generic, method="employment_context")] if generic else []
        result["reason"] = "The list has a generic private-home employment entry. Confirm the employment context and duties before relying on an ordinary SOC-code match."
        return result

    code = job.get("soc4", job.get("occupation_soc4"))
    explicit = code is not None and code != ""
    if explicit:
        code = str(code)
        if not re.fullmatch(r"[1-9]\d{3}", code):
            result["reason"] = "Confirm a four-digit SOC 2010 occupation code from the posting's duties."
            return result
        result["soc4"] = code
        matches = [_match(entry, method="explicit_soc4") for entry in data["entries"] if entry["soc4"] == code]
        result["matches"] = matches
        if not matches:
            if job.get("soc4_verified") is True:
                result.update(classification="neither", status="neither",
                              reason="The supplied verified SOC 2010 code appears on neither bundled list. Other permit conditions still require confirmation.")
            else:
                result["reason"] = "The supplied code is absent from both lists; confirm the SOC 2010 mapping before calling it neither."
            return result
        if job.get("soc4_verified") is not True:
            for match in matches:
                for row in keywords:
                    if row["list"] != match["list"] or str(row["soc4"]) != code:
                        continue
                    if any(_contains(str(job.get("title") or ""), alias) for alias in row["title_aliases"]):
                        match["duty_quote"] = _duty_quote(job, row["duty_phrases"])
                        if match["duty_quote"]:
                            match["match_type"] = "title_and_duties"
    else:
        title = str(job.get("title") or "")
        matches = []
        for row in keywords:
            if not any(_contains(title, alias) for alias in row["title_aliases"]):
                continue
            quote = _duty_quote(job, row["duty_phrases"])
            entry = by_id[f"{row['list']}:{row['soc4']}"]
            # Keep candidate entries visible even if a title lacks corroborating duties.
            matches.append(_match(entry, method="title_and_duties" if quote else "title_only", duty_quote=quote))
        result["matches"] = matches
        if not matches:
            return result

    if any(match["conditional"] for match in matches):
        result["reason"] = "The official wording contains a specialism, qualification or exception. Confirm the complete clause from duties and evidence; the title or SOC code alone is insufficient."
        return result
    full_codes = {str(row["soc4"]) for row in keywords if row.get("full_soc") is True}
    decisive = [match for match in matches if (explicit and job.get("soc4_verified") is True
                and (match["list"] == "ineligible" or match["soc4"] in full_codes)) or match["duty_quote"]]
    lists = {match["list"] for match in decisive}
    codes = {match["soc4"] for match in decisive}
    if len(lists) != 1 or len(codes) != 1:
        result["reason"] = "The occupation mapping is incomplete or ambiguous; confirm it from the posting's actual duties."
        return result
    classification = lists.pop()
    result.update(classification=classification, status=classification, soc4=codes.pop(),
                  reason=f"Appears to match the {classification} occupation list entry shown. Qualifications and the other permit conditions still require confirmation.")
    return result
