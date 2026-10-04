"""Deterministic Irish permit evidence checks, with unknown conditions preserved."""
from __future__ import annotations
import math
import re
from datetime import date
from urllib.parse import urlsplit
from backend.permits.rules import freshness, load_rules, today
from backend.permits.timeline import confirmed_date, graduate_window, timeline


def mapping(value):
    return value if isinstance(value, dict) else {}


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None


def degree_conditions(profile, requires, on, job=None):
    education, checks = mapping(profile.get("education_for_permits")), []
    for key in ("relevant_degree", "irish_institution"):
        if requires.get(key):
            value = mapping(job).get("permit_relevant_degree", education.get(key)) if key == "relevant_degree" else education.get(key)
            checks.append((key, value if isinstance(value, bool) else None))
    if requires.get("min_nfq_level"):
        nfq = education.get("nfq_level")
        checks.append(("nfq_level", nfq >= requires["min_nfq_level"] if isinstance(nfq, int) and not isinstance(nfq, bool) and 1 <= nfq <= 10 else None))
    if requires.get("degree_within_months"):
        window = graduate_window(profile, on=on)
        checks.append(("graduate_window", None if window["state"] == "unknown" else window["state"] == "active"))
    return checks


def personal_floor(profile, *, on=None, rules=None):
    """Default search preference from person facts; job/employer conditions remain separate."""
    on, profile = on or today(), mapping(profile)
    rules = load_rules() if rules is None else rules
    standard = rules["routes"]["gep"]["annual"]
    if freshness(on=on, rules=rules)["state"] == "stale":
        return standard
    possible = [standard]
    for route in rules["routes"].values():
        for variant in route.get("thresholds") or []:
            checks = degree_conditions(profile, variant.get("requires") or {}, on)
            if checks and all(value is True for _, value in checks):
                possible.append(variant["annual"])
    return min(possible)


def conditions(variant, profile, occupation, on, job):
    requires = variant.get("requires") or {}
    checks = degree_conditions(profile, requires, on, job)
    classification = occupation.get("classification") or occupation.get("status") or "unknown"
    for key in ("occupation_list", "occupation_not"):
        if requires.get(key):
            value = None if classification == "unknown" else classification == requires[key]
            checks.append(("occupation", not value if value is not None and key == "occupation_not" else value))
    state = "not_met" if any(v is False for _, v in checks) else "unknown" if any(v is None for _, v in checks) else "met"
    return state, [{"id": k, "state": "unknown" if v is None else "pass" if v else "fail"} for k, v in checks]


def salary_state(pay, threshold, stale, strict=False):
    if stale or pay.get("kind") != "advertised" or pay.get("currency") != "EUR":
        return "unknown"
    if pay.get("components") in {"bonus", "total", "total_compensation", "ote"}:
        return "unknown"
    hours = number(pay.get("hours_per_week"))
    if hours is not None and hours > 39:
        threshold = threshold * hours / 39
    low, high = number(pay.get("annual_min")), number(pay.get("annual_max"))
    if low is not None and (low > threshold if strict else low >= threshold):
        return "pass"
    return "fail" if high is not None and high < threshold else "unknown"


def contract(job):
    value = job.get("contract_duration_months")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value, f"Recorded offer duration: {value} months."
    choices = []
    pattern = r"\b(\d+|one|two|three)\s*[- ]\s*(months?|years?)\s+(?:(?:fixed[- ]term|employment)\s+)?(?:contract|offer)\b"
    for match in re.finditer(pattern, str(job.get("description") or ""), re.I):
        amount = {"one": 1, "two": 2, "three": 3}.get(match[1].lower())
        amount = amount if amount is not None else int(match[1])
        choices.append((amount * (12 if match[2].lower().startswith("year") else 1), match[0]))
    if len({months for months, _ in choices}) == 1:
        return choices[0][0], 'Posting states: "' + choices[0][1] + '".'
    return None, "Confirm the offer duration; a permanent-job label does not confirm a minimum duration."


def on_eures(job):
    """Advertised on EURES or JobsIreland: the channels the Labour Market Needs Test uses."""
    meta = mapping(job.get("posting_metadata"))
    if job.get("on_eures") in (True, 1) or meta.get("on_eures") in (True, 1) or "eures" in (job.get("source"), meta.get("source")):
        return True
    url = urlsplit(str(job.get("url") or ""))
    host = (url.hostname or "").lower()
    return (host == "europa.eu" and url.path.startswith("/eures/")) or host in {"jobsireland.ie", "www.jobsireland.ie"}


def assess(job, salary, profile, sponsorship, *, on=None, rules=None, occupation=None, history=None):
    on = on or today()
    job, salary, profile, sponsorship = map(mapping, (job, salary, profile, sponsorship))
    rules = load_rules() if rules is None else rules
    fresh = freshness(on=on, rules=rules)
    stale = fresh["state"] == "stale"
    auth = mapping(mapping(profile.get("work_authorization_by_market")).get("ie"))
    needs = not (auth.get("status") == "authorized" and (auth.get("needs_sponsorship_later") == "no" or auth.get("citizenship") == "citizen"))
    if occupation is None:
        from backend.permits.occupations import classify
        occupation = classify(job, on=on)
    if history is None:
        from backend.permits.history import PermitHistoryIndex
        history = PermitHistoryIndex().lookup(str(job.get("company") or ""), on=on)
    occupation, history = mapping(occupation), mapping(history)
    checks, routes = [], []

    def add(key, label, state, note, url=None):
        checks.append({"id": key, "label": label, "state": state, "note": note, **({"url": url} if url else {})})

    add("rules", "Official information", "unknown" if stale else "pass", f"Version {rules['version']}; checked {rules['verified_at']}. " + (fresh["message"] if stale else "Published facts are current; other conditions remain separate."))
    add("current_permission", "Current permission", "pass" if auth.get("status") == "authorized" else "unknown", "Recorded as authorized; check conditions and validity for the job start date." if auth.get("status") == "authorized" else "Confirm current permission and start-date conditions.")
    expiry = auth.get("valid_until")
    if expiry or auth.get("permission_type") == "stamp_1g":
        confirmed = confirmed_date(auth, "valid_until")
        if confirmed is None and auth.get("permission_type") != "stamp_1g" and isinstance(expiry, str):
            try:
                confirmed = date.fromisoformat(expiry)
            except ValueError:
                pass
        add("validity", "Permission validity", "pass" if confirmed and confirmed >= on else "unknown", f"Recorded valid until {confirmed}; job-start conditions remain separate." if confirmed and confirmed >= on else "The recorded permission has expired; update it before preparing this role." if confirmed else "Confirm the permission expiry date.")
    support = sponsorship.get("state") or "unknown"
    add("employer_support", "Employer permit statement", "fail" if needs and support == "refuses" else "pass" if not needs or support == "supports" else "unknown", sponsorship.get("quote") or ("The posting is silent. Employer participation is unconfirmed." if needs else "Profile records no employer sponsorship need; confirm permission conditions."))
    classification = occupation.get("classification") or occupation.get("status") or "unknown"
    quotes = [str(row.get("quote")) for row in occupation.get("matches") or [] if row.get("quote")]
    add("occupation", "Occupation list evidence", "fail" if needs and classification == "ineligible" else "pass" if classification in {"critical", "neither"} else "unknown", str(occupation.get("reason") or "Duties and qualifiers require confirmation.") + (" Official list entry: " + " | ".join(quotes) if quotes else ""))
    months, note = contract(job)
    add("contract", "Critical Skills offer duration", "unknown" if months is None else "pass" if months >= 24 else "fail", note + " Critical Skills requires a minimum 24-month offer.", rules["routes"]["csep"]["url"])
    for key, route in rules["routes"].items():
        variants = []
        for variant in route.get("thresholds") or []:
            state, details = conditions(variant, profile, occupation, on, job)
            variants.append({**variant, "conditions_state": state, "conditions": details, "salary_state": salary_state(salary, variant["annual"], stale, key == "csep" and variant["id"] == "other_occupation")})
        possible = [v for v in variants if v["conditions_state"] != "not_met"]
        met = [v for v in variants if v["conditions_state"] == "met"]
        selected = min(met or possible or variants, key=lambda v: v["annual"])
        threshold = selected["annual"] if selected["conditions_state"] == "met" or selected["id"] == "other_occupation" else route["annual"]
        pay_state = salary_state(salary, threshold, stale, selected["id"] == "other_occupation")
        if not possible or pay_state == "fail":
            pay_state = "unknown"  # Other published occupational exceptions may need confirmation.
        note = f"Dated annual threshold EUR {threshold:,}. " if stale else f"Applicable evidenced annual threshold EUR {threshold:,}. " if selected["conditions_state"] == "met" else f"Published annual threshold EUR {threshold:,}; variant conditions remain unconfirmed. "
        note += f"Graduate figure EUR {route['graduate_annual']:,}: {route['graduate_condition']}. " + route.get("remuneration_note", "")
        if salary.get("kind") != "advertised":
            note += " An estimate is not this vacancy's advertised remuneration."
        if selected.get("note"):
            note += " " + selected["note"]
        routes.append({"id": key, **route, "variants": variants, "applied_annual": None if stale else threshold, "selected_variant": selected["id"] if selected["conditions_state"] == "met" and not stale else None, "salary_state": pay_state})
        add(key + "_salary", route["title"] + " remuneration", pay_state, note, route["url"])
    gep, exemptions = rules["routes"]["gep"], []
    if classification == "critical":
        exemptions.append("Occupation appears to match a Critical Skills entry.")
    if salary_state(salary, 68911, stale) == "pass":
        exemptions.append("Advertised annual remuneration reaches the published high-pay exemption figure.")
    if job.get("lmnt_recommendation_by") in ("IDA Ireland", "Enterprise Ireland"):
        exemptions.append("Recorded agency recommendation; confirm its application to this offer.")
    add("lmnt", "GEP Labour Market Needs Test", "pass" if exemptions and not stale else "unknown", "Published exemption condition evidenced: " + " ".join(exemptions) if exemptions and not stale else gep["lmnt"]["text"] + " Completion and other exemptions remain unconfirmed.", gep["lmnt"]["url"])
    add("eures", "LMNT advertising-channel signal", "pass" if on_eures(job) else "unknown", "A EURES listing is recorded. This indicates a channel, not 28 continuous days or completion of the test." if on_eures(job) else "No EURES listing is recorded; this is not proof that required advertising is missing.", gep["lmnt"]["url"])
    names = history.get("matched_names") or ([history["matched_name"]] if history.get("matched_name") else [])
    if history.get("found"):
        from backend.permits.history import describe

        note = (f"DETE lists permits issued to {'; '.join(names)}: {describe(history)}, "
                f"{history.get('permits_24_months', 0):,} in the 24 complete months to {history.get('window_end')}. "
                "Past permits are not a promise of support for this vacancy.")
    elif history.get("match_type") == "ambiguous":
        note = "The employer name matches several legal entities in DETE's data, so no history is attributed to it."
    else:
        note = "No DETE permit record matched this employer. Absence is not a refusal or proof of an unlawful employer."
    add("dete_history", "Employer permit history", "pass" if history.get("found") else "unknown", note, rules["sources"]["statistics"])
    add("employer_criteria", "Employer relationship and registration", "unknown", "Confirm the direct employer relationship, registration and current trading status; permit history alone does not establish compliance.")
    add("eea_ratio", "Employer 50:50 staffing rule", "unknown", rules["employer_rules"]["eea_ratio"]["text"], rules["employer_rules"]["eea_ratio"]["url"])
    add("hours", "Hours and remuneration components", "unknown", "Annual figures use a 39-hour week. Confirm contracted weekly hours and qualifying remuneration; bonuses and estimates are not proof.")
    obstacles = [c for c in checks if c["state"] == "fail" and c["id"] in {"employer_support", "occupation"}]
    sources = [{"title": r["title"], "url": r["url"]} for r in rules["routes"].values()] + rules["occupation_sources"]
    sources += [{"title": "DETE permit statistics", "url": rules["sources"]["statistics"]}, {"title": "Labour Market Needs Test", "url": gep["lmnt"]["url"]}]
    return {"state": "obstacle" if obstacles else "needs_confirmation", "summary": "Recorded posting or occupation evidence presents a permit obstacle; exceptions require confirmation." if obstacles else "Salary, permission, occupation and employer facts are separate; outstanding conditions need confirmation.",
            "checks": checks, "routes": routes, "occupation": occupation, "permit_record": history, "timeline": timeline(profile, job, on=on, rules=rules), "personal_floor_eur": personal_floor(profile, on=on, rules=rules),
            "policy_version": rules["version"], "verified_at": rules["verified_at"], "sources": sources, "disclaimer": "Not immigration advice"}
