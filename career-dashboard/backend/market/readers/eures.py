"""EURES: Irish vacancies from the European Commission's job mobility portal.

Irish vacancies on EURES come from JobsIreland, the public employment service of the
Department of Social Protection. An employer who wants a General Employment Permit must
advertise the role on JobsIreland and EURES first (the Labour Market Needs Test), so a EURES
listing is a useful signal for permit-route roles. It does not show that the test was completed.

The portal's own public search API is read (no key; europa.eu's robots.txt allows it and asks
for 10 seconds between requests, which the fetcher keeps). The search matches the person's
target roles in job titles, newest first; a cursor per search remembers the newest posting
already read, so the next pass stops there. Each EURES id carries the JobsIreland job number
("2473309 18" in base64: connection point 18 is JobsIreland), which gives the posting's own
JobsIreland page: the application link people use. A few new matches per pass are read in
detail for their closing date.
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from typing import Callable

from backend.market import normalize

SEARCH_URL = "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search"
DETAIL_URL = "https://europa.eu/eures/api/jv-searchengine/public/jv/id/{id}?requestLang=en"
PAGE_URL = "https://europa.eu/eures/portal/jv-se/jv-details/{id}?lang=en"
JOBSIRELAND_URL = "https://jobsireland.ie/en-US/job-Details?id={number}"
JOBSIRELAND_POINT = "18"  # EURES connection point of Ireland's public employment service (JobsIreland)
PER_PAGE = 50
MAX_PAGES = 4        # per search and pass: 200 newest titles
DETAILS_PER_PASS = 8  # detail reads (closing dates) per pass; each costs the 10-second crawl delay
SOURCE = "eures"


def search_body(keyword: str, page: int) -> dict:
    """One page of the portal's search, Ireland only, newest first, matching the keyword in titles."""
    return {"resultsPerPage": PER_PAGE, "page": page, "sortSearch": "MOST_RECENT",
            "keywords": [{"keyword": keyword, "specificSearchCode": "TITLE"}] if keyword else [],
            "publicationPeriod": None, "occupationUris": [], "skillUris": [], "requiredExperienceCodes": [],
            "positionScheduleCodes": [], "sectorCodes": [], "educationAndQualificationLevelCodes": [],
            "positionOfferingCodes": [], "locationCodes": ["ie"], "euresFlagCodes": [], "otherBenefitsCodes": [],
            "requiredLanguages": [], "minNumberPost": None, "sessionId": "career-workspace"}


def jobsireland_number(eures_id: str) -> str:
    """The JobsIreland job number inside a EURES id ("MjQ3MzMwOSAxOA" -> "2473309"), or ''."""
    try:
        decoded = base64.b64decode(str(eures_id) + "=" * (-len(str(eures_id)) % 4), validate=True).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return ""
    match = re.fullmatch(r"(\d{4,12}) (\d{1,4})", decoded)
    return match[1] if match and match[2] == JOBSIRELAND_POINT else ""


def location_label(jv: dict) -> tuple[str, list[str]]:
    """'Dublin, Ireland' (or the NUTS region in words) and the NUTS codes EURES gave."""
    codes = [str(code).upper() for code in ((jv.get("locationMap") or {}).get("IE") or [])]
    places = normalize.counties("", codes)
    label = ", ".join(places) if places else normalize.region(codes)
    return (f"{label}, Ireland" if label else "Ireland"), codes


def posting(jv: dict, *, tz: str | None = None) -> dict | None:
    """One EURES search result in the shape every job source returns (None when it is unusable)."""
    from backend.services.job_sources import make_posting
    from backend.services.portals import html_to_text

    eures_id = str(jv.get("id") or "")
    company = " ".join(str((jv.get("employer") or {}).get("name") or "").split())
    title = " ".join(str(jv.get("title") or "").split())
    if not (eures_id and company and title):
        return None
    number = jobsireland_number(eures_id)
    page = PAGE_URL.format(id=eures_id)
    url = JOBSIRELAND_URL.format(number=number) if number else page
    location, codes = location_label(jv)
    board = "JobsIreland and EURES" if number else "EURES"
    found = make_posting(
        company, title, url, location, html_to_text(str(jv.get("description") or "")),
        source=SOURCE, source_kind="official_board", requisition_id=eures_id,
        posted_at=normalize.epoch_ms(jv.get("creationDate")), tz=tz,
        vouched=(f"{company} advertised this role on {board}: Ireland's public employment service, which "
                 "publishes registered employers' vacancies (read from the European Commission's EURES portal)."),
        verification=f"Read from the EURES portal's public search on {_today(tz)}; the role is published by JobsIreland."
        if number else f"Read from the EURES portal's public search on {_today(tz)}.")
    found.update(nuts=codes, on_eures=True, source_urls=[page], eures_id=eures_id)
    return found


def _today(tz: str | None) -> str:
    from backend.services.job_sources import today

    return today(tz)


def add_detail(found: dict, fetcher) -> dict:
    """The closing date (and remuneration wording, when given) from the posting's EURES record."""
    data, _ = fetcher.json(DETAIL_URL.format(id=found["eures_id"]))
    profiles = (data or {}).get("jvProfiles") if isinstance(data, dict) else None
    profile = next(iter(profiles.values()), {}) if isinstance(profiles, dict) and profiles else {}
    closing = normalize.epoch_ms(profile.get("lastApplicationDate")) if profile.get("lastApplicationDate") else ""
    if closing:
        found["valid_through"] = closing[:10]
    return found


def eures_jobs(fetcher, keywords: list[str], title_ok: Callable[[str], bool], place_ok: Callable[[str], bool], *,
               tz: str | None = None, deadline: float | None = None, coverage=None,
               skip_posting: Callable[[dict], bool] | None = None) -> tuple[list[dict], str | None]:
    """New Irish EURES postings whose titles match, for each search keyword; (postings, error).

    ``coverage`` is the profile's own checkpoint registry: each search's cursor (the newest
    posting this profile has read) belongs to the profile, so another profile's reading never
    hides a posting from it.
    """
    out: list[dict] = []
    errors: list[str] = []
    details = 0
    for keyword in dict.fromkeys(k for k in keywords if k):
        key = f"eures:{keyword.casefold()}"
        cursor = (coverage.get(key).get("cursor") or {}) if coverage else {}
        newest_seen = int(cursor.get("newest") or 0)
        newest_now, finished, failed, before = newest_seen, False, "", len(out)
        for page in range(1, MAX_PAGES + 1):
            if deadline is not None and time.monotonic() > deadline:
                errors.append("the time budget ran out")
                break
            data, error = fetcher.json(SEARCH_URL, search_body(keyword, page))
            if error or not isinstance(data, dict):
                failed = error or "the search returned no data"
                errors.append(failed)
                break
            jvs = [jv for jv in data.get("jvs") or [] if isinstance(jv, dict)]
            for jv in jvs:
                created = int(jv.get("creationDate") or 0)
                newest_now = max(newest_now, created)
                if newest_seen and created and created <= newest_seen:
                    finished = True  # read on an earlier pass; everything after it is older still
                    break
                found = posting(jv, tz=tz)
                if not found or not title_ok(found["title"]) or not place_ok(found["location"]):
                    continue
                if skip_posting and skip_posting(found):
                    continue
                if details < DETAILS_PER_PASS and (deadline is None or time.monotonic() < deadline):
                    found = add_detail(found, fetcher)
                    details += 1
                out.append(found)
            if finished or len(jvs) < PER_PAGE:
                finished = True
                break
        if coverage:
            # The cursor moves only when every newer page was read; otherwise the next pass starts over.
            coverage.checkpoint(key, {"newest": newest_now if finished else newest_seen},
                                "failed" if failed else "complete" if finished else "partial",
                                found=len(out) - before, error=failed)
    unique = list({item["url"]: item for item in out}.values())
    return unique, ("; ".join(dict.fromkeys(errors)) if errors and not unique else None)
