"""How each job source may be used: what it is, its key, attribution, terms and request budget.

One table, so the readers, the hunt plan, the Tracker and (later) a hosted service apply the same
rules:

* a keyed source runs only once its key is saved (Settings, or career-dashboard/.env);
* an aggregator's results carry its attribution wherever they are shown, and its links are left
  for the person to open: the app never follows an aggregator's tracking link by itself (that
  would register clicks on the person's own publisher account);
* a ``personal_use_only`` source is read for the person's own job search on their own computer,
  never republished;
* ``budget`` caps requests per day and, for Jooble's free key, for the key's lifetime;
* every source is read through the polite fetcher (robots.txt, pacing, backing off on errors);
  documented public APIs (``robots: "api"``) are read without a robots.txt check.
"""

from __future__ import annotations

from pathlib import Path

SOURCE_POLICY: dict[str, dict] = {
    "tracked": {"kind": "employer_feed", "label": "Your tracked companies", "robots": "api",
                "terms": "The employer's own public careers feed."},
    "directory": {"kind": "employer_feed", "label": "Employer directory", "robots": "api",
                  "terms": "The employer's own public careers feed."},
    "registry": {"kind": "employer_feed", "label": "Permit employers (DETE registry)", "robots": "api",
                 "terms": "The employer's own public careers feed; the registry itself is built from DETE's published "
                          "employment permit statistics."},
    "eures": {"kind": "official_board", "label": "EURES / JobsIreland", "robots": "obeyed (Crawl-delay 10)",
              "terms": "The European Commission's EURES portal; vacancies published by Ireland's public employment "
                       "service. Confirm the portal's reuse terms before any hosted use."},
    "gradireland": {"kind": "job_board", "label": "gradireland", "robots": "obeyed", "personal_use_only": True,
                    "terms": "Read for the person's own job search only; never republished."},
    "jobs_ie": {"kind": "job_board", "label": "jobs.ie", "robots": "obeyed", "personal_use_only": True,
                "terms": "Read for the person's own job search only; never republished."},
    "careerjet": {"kind": "aggregator", "label": "Careerjet", "robots": "api", "personal_use_only": True,
                  "key_env": "CAREERJET_API_KEY", "requires": ["CAREERJET_USER_IP"],
                  "attribution": "Jobs by Careerjet", "attribution_url": "https://www.careerjet.ie",
                  "key_page": "https://www.careerjet.com/partners/api",
                  "budget": {"per_day": 40}, "follow_links": False,
                  "terms": "A Careerjet publisher key, registered by the person, with the public address of this "
                           "computer's connection whitelisted; results shown with Careerjet's attribution; links "
                           "opened only by the person."},
    "jooble": {"kind": "aggregator", "label": "Jooble", "robots": "api", "personal_use_only": True,
               "key_env": "JOOBLE_API_KEY", "attribution": "Jobs by Jooble", "attribution_url": "https://ie.jooble.org",
               "key_page": "https://ie.jooble.org/api/about",
               "budget": {"per_day": 10, "lifetime": 500}, "follow_links": False,
               "terms": "A free Jooble key for the Irish site (500 requests for the key's whole life); links opened "
                        "only by the person."},
}


def policy(source: str) -> dict:
    return SOURCE_POLICY.get(source, {})


def ready(source: str, root: Path) -> tuple[bool, str]:
    """(True, "") when the source can run; otherwise (False, what is missing, in words for the person)."""
    from backend.ai import keys

    rules = policy(source)
    if not rules.get("key_env"):
        return True, ""
    found = keys.load(root)
    missing = [name for name in [rules["key_env"], *rules.get("requires", [])] if not found.get(name)]
    if missing:
        return False, f"{rules['label']} is optional and needs " + " and ".join(missing) + " (Settings, Job sources)."
    return True, ""


def status(root: Path, store=None) -> list[dict]:
    """Every source with its rules, readiness and (for budgeted ones) requests used; never a key's value."""
    from backend.ai import keys

    found = keys.load(root)
    out = []
    for source, rules in SOURCE_POLICY.items():
        ok, missing = ready(source, root)
        row = {"id": source, **{k: v for k, v in rules.items()}, "ready": ok, "missing": missing,
               "keys": [{"name": name, "saved": bool(found.get(name)), "saved_in": keys.source(root, name) or ""}
                        for name in [rules.get("key_env"), *rules.get("requires", [])] if name]}
        if rules.get("budget") and store is not None:
            try:
                row["used"] = store.budget_used(source)
            except Exception:  # noqa: BLE001 - a missing market store shows no usage, not an error
                row["used"] = {"today": 0, "lifetime": 0}
        out.append(row)
    return out
