"""Source-backed pay observations. A salary preference is not a permit decision."""
from __future__ import annotations

import math
import re
from datetime import date
from urllib.parse import urlsplit

VERSION = "ie-salary-2"
DEFAULT_FLOOR = 36605
AMOUNT = r"\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?\s*[kK]?"
MONEY = re.compile(r"(?P<currency>€|EUR\b|£|GBP\b|\$|USD\b)\s*(?P<low>" + AMOUNT + r")(?:\s*(?:-|–|—|to)\s*(?:€|EUR\b|£|GBP\b|\$|USD\b)?\s*(?P<high>" + AMOUNT + r"))?", re.I)
# The least anyone is paid for each period. A smaller figure is a slip at the source (HubSpot's
# Greenhouse range once read "€73,85 – €105,60" a year): unreadable, never rescaled into a guess.
PLAUSIBLE_MINIMUM = {"YEAR": 1000, "MONTH": 100, "WEEK": 25, "HOUR": 1}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        text = str(value).strip().lower().replace(",", "").replace(" ", "")
        number = float(text[:-1]) * 1000 if text.endswith("k") else float(text)
        return number if math.isfinite(number) and 0 < number < 10000000 else None
    except (TypeError, ValueError):
        return None


def _currency(value):
    return {"€": "EUR", "£": "GBP", "$": "USD"}.get(str(value).upper(), str(value).upper())


def _period(text):
    for pattern, period in ((r"\b(year|yearly|annual|annually|annum|pa)\b|p\.a\.", "YEAR"),
                            (r"\b(month|monthly)\b", "MONTH"),
                            (r"\b(hourly|per hour|an hour|/hr|per hr)\b", "HOUR"),
                            (r"\b(week|weekly)\b", "WEEK")):
        if re.search(pattern, text, re.I):
            return period
    return "UNKNOWN"


_INTERVALS = {"YEAR": "YEAR", "YEARLY": "YEAR", "ANNUAL": "YEAR", "ANNUALLY": "YEAR", "MONTH": "MONTH", "MONTHLY": "MONTH",
              "WEEK": "WEEK", "WEEKLY": "WEEK", "HOUR": "HOUR", "HOURLY": "HOUR", "Y": "YEAR", "M": "MONTH", "W": "WEEK",
              "H": "HOUR", "PER-YEAR-SALARY": "YEAR", "PER-MONTH-SALARY": "MONTH", "PER-WEEK-SALARY": "WEEK",
              "PER-HOUR-WAGE": "HOUR", "1 YEAR": "YEAR", "1 MONTH": "MONTH", "1 WEEK": "WEEK", "1 HOUR": "HOUR"}


def structured(raw_salary):
    """(currency, low, high, period, quote) from an ATS or aggregator pay field, or None.

    Greenhouse ``pay_input_ranges``, Lever ``salaryRange``, Ashby ``compensation``, Recruitee
    ``salary`` and Careerjet's salary fields. The period is the source's own (an unstated one stays UNKNOWN, so no annual
    figure is derived); only base salary counts, never equity, bonus or commission.
    """
    if isinstance(raw_salary, list):  # Greenhouse: one range per location or currency
        ranges = [r for r in raw_salary if isinstance(r, dict) and (r.get("min_cents") or r.get("max_cents"))]
        chosen = next((r for r in ranges if str(r.get("currency_type") or "").upper() == "EUR"), ranges[0] if ranges else None)
        if not chosen:
            return None
        words = f"{chosen.get('title') or ''} {_plain_text(chosen.get('blurb'))}"
        low, high = (_number(chosen.get(k)) for k in ("min_cents", "max_cents"))
        return (str(chosen.get("currency_type") or "").upper(), low / 100 if low else None, high / 100 if high else None,
                _period(words), f"Greenhouse pay range: {words.strip() or 'base pay'} {chosen.get('min_cents')}-{chosen.get('max_cents')} cents")
    if not isinstance(raw_salary, dict):
        return None
    if "interval" in raw_salary and ("min" in raw_salary or "max" in raw_salary):  # Lever salaryRange
        interval = _INTERVALS.get(str(raw_salary.get("interval") or "").upper(), "UNKNOWN")
        return (str(raw_salary.get("currency") or "").upper(), _number(raw_salary.get("min")), _number(raw_salary.get("max")),
                interval, f"Lever salary range: {raw_salary}")
    components = raw_salary.get("summaryComponents") or [c for tier in raw_salary.get("compensationTiers") or []
                                                          if isinstance(tier, dict) for c in tier.get("components") or []]
    salaried = [c for c in components if isinstance(c, dict) and str(c.get("compensationType") or "").casefold() == "salary"]
    if salaried:  # Ashby compensation
        part = salaried[0]
        return (str(part.get("currencyCode") or "").upper(), _number(part.get("minValue")), _number(part.get("maxValue")),
                _INTERVALS.get(str(part.get("interval") or "").upper(), "UNKNOWN"),
                f"Ashby compensation: {part.get('summary') or raw_salary.get('compensationTierSummary') or part}")
    if "period" in raw_salary and "currency" in raw_salary and ("min" in raw_salary or "max" in raw_salary):  # Recruitee
        return (str(raw_salary.get("currency") or "").upper(), _number(raw_salary.get("min")), _number(raw_salary.get("max")),
                _INTERVALS.get(str(raw_salary.get("period") or "").upper(), "UNKNOWN"), f"Recruitee salary: {raw_salary}")
    if "salary_min" in raw_salary or "salary_max" in raw_salary:  # Careerjet
        return (str(raw_salary.get("salary_currency_code") or "").upper(), _number(raw_salary.get("salary_min")),
                _number(raw_salary.get("salary_max")), _INTERVALS.get(str(raw_salary.get("salary_type") or "").upper(), "UNKNOWN"),
                f"Careerjet salary: {raw_salary.get('salary') or raw_salary}")
    return None


def _plain_text(value) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value or ""))).strip()


def unknown(url="", observed_at=""):
    return {"kind": "unknown", "currency": "", "minimum": None, "maximum": None,
            "annual_min": None, "annual_max": None, "period": "UNKNOWN", "hours_per_week": None,
            "quote": "", "url": url, "observed_at": observed_at, "sources": [], "components": "unknown"}


def extract(description, raw_salary=None, url="", observed_at=""):
    """Parse JobPosting.baseSalary or explicit pay text; never guess a currency or pay period.

    `observed_at` is when the pay was read from its source; without one the result has no date,
    so a re-read of the same job gives the same answer and nothing claims a check that never ran.
    """
    stamp = observed_at or ""
    text = str(description or "")
    result = unknown(url, stamp)
    hours = re.search(r"\b(\d{1,2}(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:per|a|each|/)\s*week", text, re.I)
    hours = float(hours[1]) if hours and 0 < float(hours[1]) <= 80 else None
    fields = structured(raw_salary)
    if fields and fields[0] and (fields[1] or fields[2]):
        currency, low, high, period, quote = fields
        result.update(kind="advertised", currency=_currency(currency), minimum=low, maximum=high,
                      period=period, quote=quote, components="base")
    elif isinstance(raw_salary, dict) and raw_salary.get("currency") and raw_salary.get("value") is not None:
        value = raw_salary["value"]
        data = value if isinstance(value, dict) else {"value": value}
        low = _number(data.get("minValue", data.get("value")))
        high = _number(data.get("maxValue", data.get("value")))
        period = str(data.get("unitText") or "UNKNOWN").upper()
        period = {"ANNUAL": "YEAR", "YEARLY": "YEAR", "MONTHLY": "MONTH", "HOURLY": "HOUR", "WEEKLY": "WEEK"}.get(period, period)
        quote = f"JobPosting.baseSalary: {raw_salary}"
        result.update(kind="advertised", currency=_currency(raw_salary["currency"]), minimum=low,
                      maximum=high, period=period, quote=quote, components="base")
    else:
        for match in MONEY.finditer(text):
            start = max(text.rfind("\n", 0, match.start()), text.rfind(". ", 0, match.start()) + 1, 0)
            ends = [x for x in (text.find("\n", match.end()), text.find(". ", match.end())) if x >= 0]
            end = min(ends) if ends else min(len(text), match.end() + 100)
            context = text[start:end].strip()
            if len(context) > 300:
                context = text[max(start, match.start() - 60):min(end, match.end() + 100)].strip()
            # Expenses, grants and standalone bonus amounts cannot become base salary.
            pay_context = re.split(r"\s+(?:plus|\+)\s+", context, maxsplit=1, flags=re.I)[0]
            if re.search(r"\b(bonus|commission|equity|relocation|allowance|training budget|grant|OTE|total compensation)\b", pay_context, re.I) and not re.search(r"\b(base|basic)\b", pay_context, re.I):
                continue
            low, high = _number(match["low"]), _number(match["high"]) if match["high"] else _number(match["low"])
            if match["high"] and "k" in match["high"].lower() and low and low < 1000:
                low *= 1000
            prefix = text[max(start, match.start() - 20):match.start()]
            if re.search(r"up to|maximum", prefix, re.I):
                low = None
            if re.search(r"from|starting at|minimum", prefix, re.I) and not match["high"]:
                high = None
            period = _period(pay_context)
            if period == "UNKNOWN" and not re.search(r"salary|pay|remuneration", context, re.I):
                continue
            result.update(kind="advertised", currency=_currency(match["currency"]), minimum=low,
                          maximum=high, period=period, quote=context, components="base")
            break
    result["hours_per_week"] = hours
    if result["kind"] == "unknown":
        return result
    if result["minimum"] and result["maximum"] and result["minimum"] > result["maximum"]:
        return unknown(url, stamp)
    smallest = PLAUSIBLE_MINIMUM.get(result["period"])
    if smallest and any(value is not None and value < smallest for value in (result["minimum"], result["maximum"])):
        return unknown(url, stamp)
    # FTE figures are not actual annual earnings without an explicit fraction.
    fte = bool(re.search(r"\b(pro[- ]?rata|full[- ]time equivalent|FTE)\b", result["quote"], re.I))
    factors = {"YEAR": 1, "MONTH": 12, "WEEK": 52, "HOUR": hours * 52 if hours else None}
    factor = factors.get(result["period"]) if not fte else None
    for bound in ("min", "max"):
        value = result["minimum" if bound == "min" else "maximum"]
        result["annual_" + bound] = round(value * factor, 2) if value is not None and factor else None
    result["sources"] = [{"url": url, "title": "Vacancy salary", "quote": result["quote"], "observed_at": stamp}]
    return result


def assess(salary, floor=DEFAULT_FLOOR):
    if salary.get("kind") == "unknown" or salary.get("currency") != "EUR":
        return "unknown"
    low, high = salary.get("annual_min"), salary.get("annual_max")
    if high is not None and high < floor:
        return "below_floor"
    if low is not None and low >= floor:
        return "meets_floor"
    return "needs_confirmation"


def floor_for(profile=None, *, on=None):
    """Keep a person's preference; refresh a default when its graduate window ends."""
    profile = profile if isinstance(profile, dict) else {}
    search = profile.get("job_search") or {}
    search = search if isinstance(search, dict) else {}
    value = search.get("salary_floor_eur")
    if search.get("salary_floor_source") != "permit_rules" and isinstance(value, (int, float)) \
            and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        return value
    from backend.permits.assessment import personal_floor

    return personal_floor(profile, on=on)


POLICIES = ("include_unstated", "confirmed_or_estimated", "confirmed_only")


def policy_for(profile=None):
    """Which pay evidence lets a job be prepared: advertised only; advertised or a labelled estimate; or
    (the default) those, plus jobs that state no pay, each flagged for the person to confirm the salary.
    Advertised or researched pay below the floor is never prepared."""
    search = (profile or {}).get("job_search") if isinstance(profile, dict) else None
    policy = search.get("salary_policy") if isinstance(search, dict) else None
    return policy if policy in POLICIES else POLICIES[0]


def research(observations, *, on=None):
    """Accept retrieved comparable evidence only. LLM-returned URLs alone are not observations.

    Callers must fetch sources and validate quotes before setting retrieved/comparable.
    Company bands or two independent publisher domains are required, no older than a year.
    """
    on = on or date.today()
    valid = []
    for row in observations:
        try:
            published = date.fromisoformat(str(row.get("published_at") or "")[:10])
        except ValueError:
            continue
        if not 0 <= (on - published).days <= 366 or not row.get("retrieved") or not row.get("comparable"):
            continue
        quote, body = str(row.get("quote") or ""), str(row.get("source_text") or "")
        host = urlsplit(str(row.get("url") or "")).hostname
        if not quote or quote not in body or not host or row.get("market") != "ie":
            continue
        pay = extract(quote, url=row["url"], observed_at=row.get("observed_at", ""))
        if pay["currency"] == "EUR" and pay["annual_min"] is not None:
            valid.append((row, pay, host.removeprefix("www.")))
    # Sibling subdomains are a single publisher, not independent corroboration.
    def publisher(host):
        parts = host.split('.')
        return '.'.join(parts[-3:] if len(parts) > 2 and '.'.join(parts[-2:]) in {'co.uk', 'com.au', 'co.ie'} else parts[-2:])
    if not valid or (not any(r.get("employer_band") for r, _, _ in valid) and len({publisher(h) for _, _, h in valid}) < 2):
        return unknown()
    # Conservative range across the observations; disagreement is kept, not averaged away.
    result = {**valid[0][1], "kind": "researched", "minimum": None, "maximum": None, "period": "YEAR",
              "annual_min": min(p["annual_min"] for _, p, _ in valid),
              "annual_max": max(p["annual_max"] or p["annual_min"] for _, p, _ in valid),
              "quote": "Comparable published pay; this vacancy's salary is unconfirmed."}
    result["sources"] = [{k: r.get(k, "") for k in ("url", "title", "quote", "observed_at", "published_at")} for r, _, _ in valid]
    return result
