"""The search plan: where and how to look for this profile's roles, derived from the profile.

Every search strategy a hunt runs (services/hunt.py) comes from here, so the plan adapts
to whoever built the profile: their target roles (and the titles employers use for the
same work, backend/role_titles.py), their markets and cities, and whether they are early
in their career. Nothing here reads candidate evidence; it only decides where to look.

Two kinds of strategy:

* ``feeds``: read without AI (services/job_sources.py): the tracked companies, the
  country's employer directory and, for Ireland, EURES / JobsIreland, gradireland and jobs.ie;
* ``ai``: one focused web-search pass by the AI, with exact queries for one group of
  sites (employer careers and ATS pages; Irish job boards; LinkedIn and the public
  sector; graduate programmes). Aggregator hits are leads: discovery re-reads each
  posting from its own page before any gate sees it.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import yaml

from backend.role_titles import IGNORED, tokens

# Places searched when the profile names none.
DEFAULT_CITIES = {"ie": ["Dublin", "Cork", "Galway", "Limerick"], "us": ["New York", "Boston", "Chicago", "Austin"]}
IRELAND_COUNTIES = ("Carlow", "Cavan", "Clare", "Cork", "Donegal", "Dublin", "Galway", "Kerry", "Kildare",
                   "Kilkenny", "Laois", "Leitrim", "Limerick", "Longford", "Louth", "Mayo", "Meath", "Monaghan",
                   "Offaly", "Roscommon", "Sligo", "Tipperary", "Waterford", "Westmeath", "Wexford", "Wicklow")
AI_ROLES = 4          # target roles that get their own AI passes
BOARD_KEYWORDS = 6    # search words sent to job boards
PAGES_PER_PASS = 15   # posting pages one AI pass may open
# The county AI passes when the person will work anywhere in Ireland (feeds cover every county).
LARGEST_MARKETS = ("Dublin", "Cork", "Galway", "Limerick", "Kildare", "Waterford")
FEED_SOURCES = ("tracked", "directory", "registry", "eures", "gradireland", "jobs_ie", "careerjet", "jooble")

SITE_GROUPS = {
    "ie": [
        ("careers", "company careers pages and ATS", [
            '"{role}" Ireland (site:myworkdayjobs.com OR site:smartrecruiters.com OR site:greenhouse.io OR site:lever.co OR site:ashbyhq.com)',
            '"{role}" Ireland (site:workable.com OR site:teamtailor.com OR site:personio.com OR site:bamboohr.com OR site:recruitee.com)',
            '"{role}" {city} careers apply',
        ]),
        ("boards", "Irish job boards", [
            '"{role}" site:irishjobs.ie',
            '"{role}" site:jobs.ie',
            '"{role}" site:gradireland.com',
            '"{role}" Ireland site:ie.indeed.com',
        ]),
        ("network", "LinkedIn, public sector and Irish recruiters", [
            '"{role}" Ireland site:linkedin.com/jobs/view',
            '"{role}" site:publicjobs.ie OR site:jobsireland.ie OR site:rezoomo.com',
            '"{role}" Ireland (site:morganmckinley.com OR site:hays.ie OR site:sigmarrecruitment.com OR site:cpl.com)',
        ]),
    ],
    "us": [
        ("careers", "company careers pages and ATS", [
            '"{role}" "United States" (site:myworkdayjobs.com OR site:greenhouse.io OR site:lever.co OR site:ashbyhq.com)',
            '"{role}" {city} careers apply',
        ]),
        ("boards", "US job boards", [
            '"{role}" site:linkedin.com/jobs/view United States',
            '"{role}" (site:builtin.com OR site:wellfound.com OR site:indeed.com)',
        ]),
    ],
}
GRADUATE_QUERIES = {
    "ie": ['"graduate programme" {next_year} Ireland "{role}"', '"{role}" graduate Ireland {year} OR {next_year} apply',
           'site:gradireland.com "{role}"'],
    "us": ['"new grad" "{role}" {year} OR {next_year}', '"{role}" "entry level" United States apply'],
}


def _profile(root) -> dict:
    try:
        return yaml.safe_load((Path(root) / "data/config/profile.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _plain(role: str) -> str:
    """"Junior Data Analyst (Power BI)" -> "data analyst": the words a search box needs."""
    words = [w for w in tokens(re.sub(r"\([^)]*\)", " ", role)) if w not in IGNORED]
    return " ".join(words)


def early_career(profile: dict) -> bool:
    targets = profile.get("target_roles") or {}
    limit = targets.get("max_years_required")
    words = " ".join(str(r) for r in (targets.get("primary") or []) + (targets.get("seniority") or [])).casefold()
    return bool(re.search(r"\b(graduate|junior|entry|intern|trainee|new grad|associate)\b", words)) or (
        isinstance(limit, int) and 0 < limit <= 3) or bool((profile.get("scoring") or {}).get("block_seniority"))


def plan_for(root) -> dict:
    """Roles, related titles, markets, cities and board keywords for this profile."""
    from backend.countries import target_markets_for
    from backend.job_quality import ProfileRules

    profile = _profile(root)
    targets = profile.get("target_roles") or {}
    roles = [str(r) for group in ("primary", "secondary") for r in targets.get(group) or [] if str(r).strip()]
    matcher = ProfileRules.of(root).roles
    related = [r for r in getattr(matcher, "related", []) if r]
    markets = target_markets_for(root)
    preferred = [str(c) for c in ((profile.get("location_preferences") or {}).get("preferred") or []) if str(c).strip()]
    cities = {market: [c for c in preferred if c.casefold() not in {"ireland", "united states", "remote"}]
              or DEFAULT_CITIES.get(market, []) for market in markets}
    plain = list(dict.fromkeys(p for p in (_plain(r) for r in roles) if p))
    early = early_career(profile)
    # The target roles first, then the graduate search, then related titles: the cap keeps the best.
    keywords = plain + (["graduate " + plain[0]] if early and plain else []) + [_plain(r) for r in related]
    return {
        "roles": roles, "plain_roles": plain, "related_titles": related, "markets": markets, "cities": cities,
        "early_career": early, "board_keywords": list(dict.fromkeys(k for k in keywords if k))[:BOARD_KEYWORDS],
        "counties": {"ie": preferred_counties(preferred)} if "ie" in markets else {},
        "excluded_titles": list(getattr(matcher, "excluded", [])),
    }


def preferred_counties(places: list[str]) -> list[str]:
    """The counties the person's places name; with none ("anywhere in Ireland"), the largest markets.

    County passes are AI web searches, the scarcest budget on a free plan. The feeds (EURES,
    the employer directory, the boards) already cover every county, so "anywhere" searches the
    six largest markets rather than rotating through all 26.
    """
    from backend.market.normalize import counties

    named = list(dict.fromkeys(county for place in places for county in counties(place)))
    return named or list(LARGEST_MARKETS)


def strategies(root, *, sources: str = "all", plan: dict | None = None) -> list[dict]:
    """The passes one hunt cycle runs, in order: every no-AI feed first, then focused AI passes.

    ``sources`` is "all", "feeds" (no AI searching at all) or "ai" (AI passes only).
    """
    from backend.services.job_sources import MARKET_SOURCES, SOURCE_LABELS

    plan = plan or plan_for(root)
    from backend.services.source_coverage import Coverage
    coverage = Coverage(root)
    year = datetime.now(ZoneInfo("Europe/Dublin")).year
    out = []
    if sources in ("all", "feeds"):
        from backend.market import policy

        for source in FEED_SOURCES:
            if source in MARKET_SOURCES and MARKET_SOURCES[source] not in plan["markets"]:
                continue
            if not policy.ready(source, root)[0]:
                continue  # an optional keyed source the person has not set up
            out.append({"id": f"feeds:{source}", "kind": "feeds", "label": SOURCE_LABELS[source], "sources": [source]})
    if sources in ("all", "ai") and plan["plain_roles"]:
        passes = []
        for market in plan["markets"]:
            groups = SITE_GROUPS.get(market, [])
            city = (plan["cities"].get(market) or [""])[0]
            for number, role in enumerate(plan["plain_roles"][:AI_ROLES], 1):
                for group, label, templates in groups:
                    passes.append((group, {
                        "id": f"ai:{market}:{number}:{group}", "kind": "ai", "market": market,
                        "label": f"{role.title()} — {label}",
                        "queries": [t.format(role=role, city=city) for t in templates],
                        "max_age_days": 30,
                    }))
                if market == "ie":
                    for county in (plan.get("counties") or {}).get("ie") or preferred_counties([]):
                        passes.append(("county", {
                            "id": f"ai:ie:{number}:county:{county.lower()}", "kind": "ai", "market": "ie",
                            "county": county, "label": f"{role.title()} — County {county}",
                            "queries": [f'"{role}" "{county}" Ireland careers apply',
                                        f'"{role}" "{county}" (site:jobs.ie OR site:irishjobs.ie OR site:jobsireland.ie)'],
                            "max_age_days": 30,
                        }))
            if plan["early_career"]:
                role = plan["plain_roles"][0]
                passes.append(("graduate", {
                    "id": f"ai:{market}:graduate", "kind": "ai", "market": market,
                    "label": f"Graduate programmes ({market.upper()})",
                    "queries": [t.format(role=role, year=year, next_year=year + 1) for t in GRADUATE_QUERIES.get(market, [])],
                    "max_age_days": 60,
                }))
        # Employer pages before boards, boards before networks: the most direct evidence first.
        order = {"careers": 0, "boards": 1, "graduate": 2, "network": 3}
        out += [p for _, p in sorted(passes, key=lambda item: order.get(item[0], 9))]
    return coverage.order(out)


def focus_instructions(focus: dict) -> str:
    """What the discovery AI is told for one focused pass (appended to its guide)."""
    queries = "\n".join(f"  {n}. {q}" for n, q in enumerate(focus.get("queries") or [], 1))
    budget = len(focus.get("queries") or []) + 2
    return (
        f"FOCUS FOR THIS PASS: {focus.get('label') or 'targeted search'}.\n"
        f"This pass replaces the search budget above with its own: run these searches first, in order "
        f"(each is one use of the search tool), then at most two more like them, and open at most "
        f"{PAGES_PER_PASS} specific posting pages (about {budget} searches in all):\n{queries}\n"
        f"Prefer postings from the last {focus.get('max_age_days') or 30} days. Skip every URL in INPUT.skip_urls "
        "and INPUT.seen_jobs: they are already decided.\n"
        "LEADS, NOT ONLY FULLY READ POSTINGS: in this pass, return every real, open, relevant role you find, up to "
        "return_up_to, even when you could not read its whole posting within the page budget. The application "
        "re-reads each URL itself (the employer's ATS feed, Workday, SmartRecruiters, or the page's schema.org "
        "JobPosting data on gradireland, jobs.ie and most career sites) and discards any it cannot verify, so an "
        "unverifiable lead costs nothing while a withheld one is lost. For each lead give the most direct URL you "
        "found: the employer's own careers or ATS page first; when you only saw it on LinkedIn, Indeed, IrishJobs or "
        "a recruiter, spend one search looking for the same title on the employer's careers site. Put in description "
        "everything you did read, and say plainly in verification what you could and could not read. A role whose "
        "closing date has passed, or that is clearly outside the target roles or market, is still not a lead. "
        "Everything else in the instructions above still applies: quote restriction sentences verbatim, never invent "
        "a job or a URL, and return the schema."
    )


def describe(root) -> dict:
    """The plan in words for the page and the chat: what will be searched and where."""
    plan = plan_for(root)
    return {**plan, "strategies": [{k: s[k] for k in ("id", "kind", "label") if k in s} | (
        {"queries": s["queries"]} if s.get("queries") else {}) for s in strategies(root, plan=plan)]}
