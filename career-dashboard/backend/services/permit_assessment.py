"""Dated Irish permit information with explicit unknowns, never an approval engine."""
from datetime import date
from pathlib import Path
import re
import yaml

RULES = Path(__file__).resolve().parents[1] / "countries/ie/permit-rules.yml"


def assess(job, salary, profile, sponsorship, *, on=None):
    on = on or date.today()
    rules = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    auth = (profile.get("work_authorization_by_market") or {}).get("ie") or {}
    checks = []
    def add(key, label, state, note):
        checks.append({"id": key, "label": label, "state": state, "note": note})
    stale = not rules["effective_from"] <= on.isoformat() <= rules["review_after"]
    add("rules", "Official information", "unknown" if stale else "pass",
        f"Version {rules['version']}; checked {rules['verified_at']}. " + ("Review current official information before relying on thresholds." if stale else "General routes shown; exceptions require confirmation."))
    needs = not (auth.get("status") == "authorized"
                 and (auth.get("needs_sponsorship_later") == "no" or auth.get("citizenship") == "citizen"))
    add("current_permission", "Current permission", "pass" if auth.get("status") == "authorized" else "unknown",
        "Recorded as authorized; check its conditions and validity for the job start date." if auth.get("status") == "authorized" else "Confirm current permission and any start-date conditions.")
    expiry = auth.get("valid_until")
    if expiry:
        try:
            expired = date.fromisoformat(expiry) < on
            add("validity", "Permission validity", "unknown" if expired else "pass",
                "The recorded permission has expired; update it before preparing this role." if expired else f"Recorded valid until {expiry}; start-date conditions still apply.")
        except (TypeError, ValueError):
            add("validity", "Permission validity", "unknown", "Confirm the permission expiry date.")
    support_state = sponsorship.get("state") or "unknown"
    if needs:
        add("employer_support", "Employer permit statement", "fail" if support_state == "refuses" else "unknown" if support_state != "supports" else "pass",
            sponsorship.get("quote") or "The posting is silent. Employer participation is unconfirmed.")
    else:
        add("employer_support", "Employer permit statement", "pass",
            "Profile records current authorization and no employer sponsorship need. "
            + (f"Posting says: {sponsorship['quote']}" if sponsorship.get("quote") else "Confirm current permission conditions."))
    routes = []
    for key, route in rules["routes"].items():
        low, high = salary.get("annual_min"), salary.get("annual_max")
        confirmed = salary.get("kind") == "advertised" and salary.get("currency") == "EUR"
        state = "unknown"
        if confirmed and not stale and low is not None and low >= route["annual"]:
            state = "pass"
        # Below standard is not ineligible: graduate or occupational exceptions may apply.
        note = f"Standard annual threshold EUR {route['annual']:,}; qualifying graduate threshold EUR {route['graduate_annual']:,}. {route['graduate_condition']}."
        if confirmed and high is not None and high < route["annual"]:
            note += " Advertised pay is below the standard threshold; confirm whether an exception applies."
        routes.append({"id": key, **route, "salary_state": state})
        add(key + "_salary", route["title"] + " remuneration", state, note)
    add("occupation", "Occupation and qualifications", "unknown",
        "Check the occupation duties against the official lists, exclusions and required qualifications; job titles alone do not establish eligibility.")
    months = re.search(r"\b(\d+)\s*[- ]?month\s+(?:fixed[- ]term\s+)?contract", job.get("description") or "", re.I)
    add("contract", "Contract duration", "unknown",
        f"Posting states {months[1]} months; Critical Skills normally requires a two-year offer." if months else "Confirm contract duration; Critical Skills normally requires a two-year offer.")
    add("employer_criteria", "Employer requirements", "unknown",
        "Confirm the employer relationship, registration, 50:50 rule or exception, and any required Labour Market Needs Test.")
    add("hours", "Hours and remuneration components", "unknown",
        "Confirm contracted weekly hours and qualifying remuneration; a salary estimate or bonus is not proof.")
    state = "obstacle" if any(c["state"] == "fail" for c in checks) else "needs_confirmation"
    return {"state": state, "summary": "Employer statement conflicts with the search policy." if state == "obstacle" else
            "Salary and permission facts are separate; employer and permit conditions need confirmation." if needs else
            "Profile records no future sponsorship need; verify current permission conditions.",
            "checks": checks, "routes": routes, "policy_version": rules["version"], "verified_at": rules["verified_at"],
            "sources": [{"title": r["title"], "url": r["url"]} for r in rules["routes"].values()] + rules["occupation_sources"]}
