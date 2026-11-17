"""Date arithmetic over confirmed facts; no immigration recommendations."""
import calendar
from datetime import date, timedelta
from backend.permits.rules import load_rules, today


def add_months(value, months):
    index = value.year * 12 + value.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def confirmed_date(facts, field):
    if not isinstance(facts, dict) or facts.get(field + "_confirmed") is not True:
        return None
    value = facts.get(field)
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except ValueError:
        return None


def graduate_window(profile, *, on=None):
    on = on or today()
    award = confirmed_date(profile.get("education_for_permits"), "award_date")
    if award is None:
        return {"state": "unknown", "award_date": None, "ends_on": None, "days_left": None,
                "note": "Confirm the exact award date before evaluating a graduate threshold."}
    end = add_months(award, 12)
    return {"state": "active" if award <= on < end else "future" if on < award else "ended",
            "award_date": str(award), "ends_on": str(end), "days_left": (end - on).days,
            "note": "Recorded award date plus 12 calendar months; other graduate-rate conditions remain separate."}


def timeline(profile, job=None, *, on=None, rules=None):
    on, job = on or today(), job or {}
    rules = load_rules() if rules is None else rules
    by_market = profile.get("work_authorization_by_market") or {}
    auth = by_market.get("ie") or {} if isinstance(by_market, dict) else {}
    auth = auth if isinstance(auth, dict) else {}
    items = []
    expiry = confirmed_date(auth, "valid_until")
    if auth.get("permission_type") == "stamp_1g":
        items.append({"id": "stamp_1g_expiry", "label": "Recorded Stamp 1G expiry",
                      "date": str(expiry) if expiry else None, "days_left": (expiry - on).days if expiry else None,
                      "note": "Expiry confirmed by the person; job-start conditions remain separate." if expiry else "Confirm an exact expiry date.",
                      "url": rules["stamp_1g"]["url"]})
    window = graduate_window(profile, on=on)
    if window["ends_on"]:
        items.append({"id": "graduate_window_end", "label": "Recorded graduate-rate window ends",
                      "date": window["ends_on"], "days_left": window["days_left"], "note": window["note"],
                      "url": rules["routes"]["gep"]["url"]})
    gep = rules["routes"]["gep"]
    received_by = None
    try:
        if isinstance(job.get("start_date"), str):
            received_by = date.fromisoformat(job["start_date"]) - timedelta(weeks=gep["application_lead_weeks"])
    except ValueError:
        pass
    items.append({"id": "gep_lead_time", "label": "Published GEP application lead time",
                  "date": str(received_by) if received_by else None, "days_left": (received_by - on).days if received_by else None,
                  "weeks": gep["application_lead_weeks"], "note": gep["application_lead_text"], "url": gep["url"]})
    return {"as_of": str(on), "events": items, "graduate_window": window, "disclaimer": "Not immigration advice"}
