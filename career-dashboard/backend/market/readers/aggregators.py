"""Careerjet and Jooble: aggregator searches, used as leads to the employer's own posting.

Both are optional and need the person's own free key (market/policy.py). An aggregator result is
a short excerpt and a tracking link, not the posting, and the app never follows those links by
itself (that would register clicks on the person's own publisher account). So each result is a
lead:

* when its employer has a careers board the app reads (the profile's tracked companies, the
  employer directory or the permit registry), that board is read and the same posting looked
  up there by its title: the employer's own posting then enters the search like any feed posting;
* otherwise the lead is kept in the shared market store for the Tracker, marked as a lead, with
  the aggregator's attribution and link for the person to open.

Each search costs one request from the source's budget (Jooble's free key allows 500 in its
whole life; the policy spreads them at about ten a day).
"""

from __future__ import annotations

import base64
import time
from typing import Callable
from urllib.parse import urlencode

from backend.market import normalize, policy

CAREERJET_URL = "https://search.api.careerjet.net/v4/query"
JOOBLE_URL = "https://ie.jooble.org/api/{key}"
PAGE_SIZE = 50


def careerjet_url(keyword: str, *, user_ip: str, page: int = 1) -> str:
    from backend.services.job_sources import USER_AGENT

    return CAREERJET_URL + "?" + urlencode({"locale_code": "en_IE", "keywords": keyword, "location": "Ireland",
                                            "sort": "date", "page": page, "page_size": PAGE_SIZE,
                                            "fragment_size": 400, "user_ip": user_ip, "user_agent": USER_AGENT})


def careerjet_leads(fetcher, keyword: str, *, key: str, user_ip: str, page: int = 1) -> tuple[list[dict], str | None]:
    token = base64.b64encode(f"{key}:".encode()).decode()
    data, error = fetcher.json(careerjet_url(keyword, user_ip=user_ip, page=page), headers={"Authorization": f"Basic {token}"})
    if not isinstance(data, dict):
        return [], error or "Careerjet returned no data"
    if data.get("type") not in (None, "JOBS"):
        return [], str(data.get("message") or "Careerjet did not return jobs")
    leads = []
    for job in data.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        pay = {k: job.get(k) for k in ("salary", "salary_min", "salary_max", "salary_currency_code", "salary_type")}
        leads.append({"source": "careerjet", "company": str(job.get("company") or ""), "title": str(job.get("title") or ""),
                      "location": str(job.get("locations") or ""), "snippet": str(job.get("description") or ""),
                      "url": str(job.get("url") or ""), "posted_at": str(job.get("date") or ""),
                      "raw_salary": pay if (pay["salary_min"] or pay["salary_max"]) else None, "lead_id": ""})
    return leads, None


def jooble_leads(fetcher, keyword: str, *, key: str, page: int = 1) -> tuple[list[dict], str | None]:
    data, error = fetcher.json(JOOBLE_URL.format(key=key), {"keywords": keyword, "location": "Ireland", "page": page})
    if not isinstance(data, dict):
        return [], error or "Jooble returned no data"
    leads = []
    for job in data.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        snippet = str(job.get("snippet") or "")
        if job.get("salary"):
            snippet += f"\n\nSalary as listed on Jooble: {job['salary']}"
        leads.append({"source": "jooble", "company": str(job.get("company") or ""), "title": str(job.get("title") or ""),
                      "location": str(job.get("location") or ""), "snippet": snippet, "url": str(job.get("link") or ""),
                      "posted_at": str(job.get("updated") or ""), "raw_salary": None, "lead_id": str(job.get("id") or "")})
    return leads, None


def _plain(text: str) -> str:
    from backend.services.portals import html_to_text

    return html_to_text(text)


def as_lead_posting(lead: dict, *, tz: str | None = None) -> dict:
    """A lead in the market store's posting shape, marked as a lead with its source's attribution."""
    from backend.services.job_sources import make_posting

    rules = policy.policy(lead["source"])
    found = make_posting(lead["company"], _plain(lead["title"]), lead["url"], lead["location"], _plain(lead["snippet"]),
                         source=lead["source"], source_kind="aggregator", requisition_id=lead.get("lead_id") or "",
                         posted_at=lead.get("posted_at") or "", tz=tz, raw_salary=lead.get("raw_salary"),
                         verification=f"A lead from {rules.get('label', lead['source'])}: an excerpt, not the posting.")
    found.update(lead=True, attribution=rules.get("attribution", ""))
    return found


def board_index(rows: list[dict]) -> dict[str, dict]:
    """Employer rows by every matching key of their names (brand and, for registry rows, legal name)."""
    from backend.permits.employer_names import keys_for

    index: dict[str, dict] = {}
    for row in rows:
        for name in (row.get("name"), row.get("legal_name")):
            for key in keys_for(str(name or "")):
                index.setdefault(key, row)
    return index


def match_on_boards(leads: list[dict], rows: list[dict], fetcher, *, place_ok: Callable[[str], bool],
                    tz: str | None = None, deadline: float | None = None,
                    skip_posting: Callable[[dict], bool] | None = None) -> tuple[list[dict], list[dict]]:
    """(the employers' own postings for leads whose employer has a readable board, the leads left unmatched)."""
    from backend.permits.employer_names import keys_for
    from backend.services.job_sources import employer_feed

    index = board_index(rows)
    wanted: dict[int, tuple[dict, set[str], list[dict]]] = {}
    unmatched = []
    for lead in leads:
        row = next((index[key] for key in keys_for(lead["company"]) if key in index), None)
        if row is None:
            unmatched.append(lead)
            continue
        entry = wanted.setdefault(id(row), (row, set(), []))
        entry[1].add(normalize.title_key(_plain(lead["title"])))
        entry[2].append(lead)
    postings = []
    for row, titles, row_leads in wanted.values():
        if deadline is not None and time.monotonic() > deadline:
            unmatched.extend(row_leads)
            continue
        found, _ = employer_feed(row, fetcher, lambda title: normalize.title_key(title) in titles, place_ok,
                                 country=row.get("_market") or "ie", search_text="Ireland", tz=tz, skip_posting=skip_posting)
        seen = {normalize.title_key(posting["title"]) for posting in found}
        for posting in found:
            posting["found_via"] = row_leads[0]["source"]
        postings.extend(found)
        unmatched.extend(lead for lead in row_leads if normalize.title_key(_plain(lead["title"])) not in seen)
    return postings, unmatched


def aggregator_jobs(source: str, fetcher, keywords: list[str], title_ok: Callable[[str], bool],
                    place_ok: Callable[[str], bool], *, root, tz: str | None = None, deadline: float | None = None,
                    coverage=None, skip_posting: Callable[[dict], bool] | None = None,
                    rows: list[dict] | None = None, store=None) -> tuple[list[dict], str | None, str]:
    """(employer postings found through this aggregator's leads, error, a coverage note)."""
    from backend.ai import keys
    from backend.market.store import MarketStore

    rules = policy.policy(source)
    ok, missing = policy.ready(source, root)
    if not ok:
        return [], None, f"not set up ({missing})"
    found_keys = keys.load(root)
    store = store or MarketStore()
    budget = rules.get("budget") or {}
    leads, errors, searched = [], [], 0
    ordered = coverage.order(list(keywords), key=lambda k: f"{source}:{k.casefold()}") if coverage else list(keywords)
    for keyword in ordered:
        if deadline is not None and time.monotonic() > deadline:
            break
        if not store.take_budget(source, per_day=int(budget.get("per_day") or 10), lifetime=budget.get("lifetime")):
            errors.append("its request budget for today is used")
            break
        searched += 1
        if source == "careerjet":
            found, error = careerjet_leads(fetcher, keyword, key=found_keys[rules["key_env"]],
                                           user_ip=found_keys["CAREERJET_USER_IP"])
        else:
            found, error = jooble_leads(fetcher, keyword, key=found_keys[rules["key_env"]])
        if coverage:
            coverage.checkpoint(f"{source}:{keyword.casefold()}", {}, "failed" if error else "complete",
                                found=len(found), error=error or "")
        if error:
            errors.append(error)
            continue
        leads.extend(found)
    leads = [lead for lead in {(lead["company"], lead["title"], lead["location"]): lead for lead in leads}.values()
             if lead["company"] and title_ok(_plain(lead["title"])) and place_ok(lead["location"] or "Ireland")]
    if rows is None:
        from backend.services.job_sources import employer_rows

        rows = [row for source_name in ("tracked", "directory", "registry") for row in employer_rows(root, source_name)]
    postings, unmatched = match_on_boards(leads, rows, fetcher, place_ok=place_ok, tz=tz, deadline=deadline,
                                          skip_posting=skip_posting)
    if unmatched:
        try:
            store.record_postings([as_lead_posting(lead, tz=tz) for lead in unmatched])
        except Exception:  # noqa: BLE001 - leads are a convenience for the Tracker; the search goes on
            pass
    note = (f"{searched} searches, {len(leads)} matching leads: {len(postings)} read from the employer's own board, "
            f"{len(unmatched)} kept as leads for the Tracker ({rules.get('attribution', '')})")
    error = "; ".join(dict.fromkeys(errors)) if errors and not leads else None
    return postings, error, note
