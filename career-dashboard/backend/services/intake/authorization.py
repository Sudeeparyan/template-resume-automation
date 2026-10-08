"""Person-confirmed permit facts; AI extraction remains verbatim and separate.

Date proposals are deterministic suggestions, never permissions or inferred facts.
Only an explicit confirmation with an exact ISO date populates a normalized date.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime

PERMISSION_TYPES = ("unknown", "stamp_1g", "stamp_2", "stamp_4", "irish_or_eea_citizen",
                    "csep_holder", "gep_holder", "stamp_1", "stamp_3", "other")
POLICIES = ("include_unstated", "confirmed_or_estimated", "confirmed_only")


def iso_date(value, label="Date") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label} must be an exact date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"{label} must be a valid calendar date.") from None


def date_proposal(raw) -> dict:
    text = str(raw or "").strip()
    out = {"raw": text, "proposed_date": None, "precision": "unknown", "needs_confirmation": bool(text)}
    if not text:
        return out
    try:
        out.update(proposed_date=iso_date(text), precision="day")
        return out
    except ValueError:
        pass
    for fmt in ("%d %B %Y", "%d %b %Y", "%d-%b-%Y", "%d%b%Y"):
        try:
            out.update(proposed_date=datetime.strptime(text, fmt).date().isoformat(), precision="day")
            return out
        except ValueError:
            pass
    for fmt in ("%b%Y", "%B%Y", "%b %Y", "%B %Y", "%Y-%m"):
        try:
            parsed = datetime.strptime(text, fmt)
            last = calendar.monthrange(parsed.year, parsed.month)[1]
            out.update(proposed_date=date(parsed.year, parsed.month, last).isoformat(), precision="month",
                       explanation="Only a month was supplied. Month-end is a proposal; confirm the actual day from your permission or award record.")
            return out
        except ValueError:
            pass
    out["explanation"] = "The date is incomplete or ambiguous. Enter and confirm the actual day in YYYY-MM-DD format."
    return out


def optional_bool(value, label):
    if value is None or value in ("", "unknown"):
        return None
    if type(value) is bool:
        return value
    if value in ("true", "yes"):
        return True
    if value in ("false", "no"):
        return False
    raise ValueError(f"{label} must be yes, no or unknown.")


def validate_authorization(value: dict, market: str = "ie") -> dict:
    if not isinstance(value, dict):
        raise ValueError("Work authorization must be an object.")
    result = {}
    for key, allowed in (("status", ("authorized", "needs_sponsorship", "unknown")),
                         ("citizenship", ("citizen", "noncitizen", "unknown")),
                         ("needs_sponsorship_later", ("yes", "no", "unknown"))):
        choice = value.get(key, "unknown")
        if choice not in allowed:
            raise ValueError("Choose valid work-authorization, citizenship and future sponsorship answers, or leave them unknown.")
        result[key] = choice
    permission = value.get("permission_type", "unknown") or "unknown"
    if permission not in PERMISSION_TYPES:
        raise ValueError("Choose a supported permission type, or leave it unknown.")
    if "permission_type" in value:
        result["permission_type"] = permission
    raw = str(value.get("valid_until_raw") or value.get("valid_until") or "").strip()[:200]
    confirmed = value.get("valid_until_confirmed") is True
    if raw or "valid_until" in value or "valid_until_confirmed" in value:
        result.update(valid_until_raw=raw, valid_until_confirmed=confirmed,
                      valid_until=iso_date(value.get("valid_until"), "Permission expiry") if confirmed else "",
                      expiry_proposal=date_proposal(raw))
    wording = value.get("permission_wording")
    if wording is not None:
        result["permission_wording"] = str(wording).strip()[:500]
    return result


def validate_education(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Education for permit facts must be an object.")
    raw = str(value.get("award_date_raw") or value.get("award_date") or "").strip()[:200]
    confirmed = value.get("award_date_confirmed") is True
    level = value.get("nfq_level")
    if level in (None, "", "unknown"):
        level = None
    elif isinstance(level, bool) or not re.fullmatch(r"(?:[1-9]|10)", str(level)):
        raise ValueError("NFQ level must be 1 to 10, or unknown.")
    else:
        level = int(level)
    return {"award_date": iso_date(value.get("award_date"), "Degree award date") if confirmed else "",
            "award_date_raw": raw, "award_date_confirmed": confirmed, "award_date_proposal": date_proposal(raw),
            "nfq_level": level,
            "irish_institution": optional_bool(value.get("irish_institution"), "Irish institution"),
            "relevant_degree": optional_bool(value.get("relevant_degree"), "Degree relevance")}


def validate_job_search(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Job-search preferences must be an object.")
    result = {}
    policy = value.get("salary_policy", POLICIES[0])
    if policy not in POLICIES:
        raise ValueError("Choose include_unstated, confirmed_or_estimated or confirmed_only for the salary policy.")
    result["salary_policy"] = policy
    if value.get("salary_floor_eur") not in (None, ""):
        floor = value["salary_floor_eur"]
        if type(floor) not in (int, float) or not 0 < floor <= 1000000:
            raise ValueError("Salary floor must be a positive euro amount.")
        result["salary_floor_eur"] = floor
        result["salary_floor_source"] = "person"
    graduate = value.get("graduate_search_confirmed") is True
    result["graduate_search_confirmed"] = graduate
    seniority = value.get("seniority", ["graduate", "junior", "entry"] if graduate else [])
    if not isinstance(seniority, list) or any(not isinstance(s, str) or not s.strip() or len(s) > 50 for s in seniority):
        raise ValueError("Seniority must be a list of levels you want to search for.")
    result["seniority"] = list(dict.fromkeys(s.strip() for s in seniority))
    maximum = value.get("max_years_required", 3 if graduate else None)
    if maximum not in (None, "") and (type(maximum) is not int or not 0 <= maximum <= 50):
        raise ValueError("Maximum required experience must be 0 to 50 whole years, or unknown.")
    result["max_years_required"] = None if maximum == "" else maximum
    return result
