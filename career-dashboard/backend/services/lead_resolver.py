"""A pasted job link, resolved to the employer's own posting before anything is saved.

People paste links from anywhere: LinkedIn, an aggregator, a newsletter, a recruiter's email.
The app reads a posting only where it is published in full, so a pasted link is resolved in
this order:

1. a posting on an applicant-tracking system the app reads (Greenhouse, Lever, Ashby, Workday,
   SmartRecruiters, Workable, Recruitee, Personio) is read from that system's own feed;
2. any other page is opened, following redirects (a tracking link lands on its target), and
   read through its schema.org JobPosting data when the page publishes it;
3. a page that is not the posting itself but links to exactly one posting on such a system
   (an aggregator's "apply on the employer's site" page) is followed to that posting.

Nothing is guessed. Otherwise the answer says why and asks for the employer's own link. Sites
that refuse automated reading (LinkedIn, Indeed, Glassdoor, IrishJobs.ie) are never opened.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

REFUSING = ("linkedin.com", "indeed.com", "glassdoor.com", "glassdoor.ie", "irishjobs.ie")
_HREF = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*["']([^"'#]+)["']""", re.I)


def refused(url: str) -> str:
    """The site's name when it refuses automated reading ('' otherwise)."""
    host = (urlsplit(url or "").hostname or "").lower()
    return next((site for site in REFUSING if host == site or host.endswith("." + site)), "")


def posting_link(url: str) -> bool:
    """Whether a link is one posting (not a whole board) on an applicant-tracking system the app reads."""
    from backend.market.readers import ats
    from backend.services.job_sources import smartrecruiters_parts, workday_parts

    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    path = [segment for segment in parts.path.split("/") if segment]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        return "jobs" in path[:-1]
    if host in {"jobs.lever.co", "jobs.eu.lever.co", "jobs.ashbyhq.com"}:
        return len(path) >= 2
    if host == "apply.workable.com":
        return (len(path) >= 3 and path[1] == "j") or (len(path) >= 2 and path[0] == "j")
    if host.endswith(".recruitee.com"):
        return len(path) == 2 and path[0] == "o"
    return bool(workday_parts(url) or smartrecruiters_parts(url) or ats.personio_parts(url))


def posting_links(html: str, base: str) -> list[str]:
    """Distinct applicant-tracking-system posting links on a page, in page order."""
    links = []
    for href in _HREF.findall(html or ""):
        url = urljoin(base, href.strip()).split("#")[0]
        if url.startswith("https://") and posting_link(url):
            links.append(url.rstrip("/"))
    return list(dict.fromkeys(links))


def board_links(html: str, base: str) -> list[str]:
    """Distinct careers-board links on a page (the board itself, not one posting), in page order."""
    from backend.services.job_sources import row_url, tracked_row

    found = []
    for href in _HREF.findall(html or ""):
        url = urljoin(base, href.strip()).split("#")[0]
        if not url.startswith("https://"):
            continue
        try:
            row = tracked_row("board", url)
        except ValueError:
            continue
        found.append(row_url(row) or url)
    return list(dict.fromkeys(found))


def _needs(url: str, reason: str, **extra) -> dict:
    return {"status": "needs_employer_link", "url": url, "posting": None, "reason": reason, **extra}


def _read(url: str, found: dict, *, via: str = "") -> dict:
    return {"status": "read", "url": found.get("url") or url, "posting": {**found, "url": found.get("url") or url},
            "method": found.get("method", ""), "via": via if via and via != url else ""}


def _complete(found: dict, url: str, fetcher) -> dict:
    """Fill a feed-read posting's title and employer from its own page when the feed leaves them out."""
    from backend.services.job_sources import jsonld_posting

    if found.get("company") and found.get("title"):
        return found
    page, _ = fetcher.text(url)
    published = jsonld_posting(page or "") or {}
    return {**found, "title": found.get("title") or published.get("title") or "",
            "company": found.get("company") or published.get("company") or ""}


def resolve(url: str, *, fetcher=None) -> dict:
    """{"status": "read", "url", "posting", "method", "via"} or {"status": "needs_employer_link", "url", "reason"}."""
    from backend.services.job_sources import _default_fetcher, jsonld_posting, read_posting

    url = (url or "").strip()
    if not re.match(r"(?i)^https?://", url):
        return _needs(url, "That is not a web link. Paste the posting's full https:// link.")
    site = refused(url)
    if site:
        return _needs(url, f"{site} does not allow automated reading, so the app cannot open this link. Open the "
                           "posting there, then paste the employer's own application link (or the full job text).",
                      site=site)
    fetcher = fetcher or _default_fetcher()
    if posting_link(url):
        found = read_posting(url, fetcher)
        if found and found.get("description"):
            return _read(url, _complete({**found, "url": found.get("url") or url}, url, fetcher))
        return _needs(url, "The posting could not be read from the employer's careers feed; it may have closed. "
                           "Check the link, or paste the full job text.")
    body, final, error = fetcher.page(url)
    if final and final != url and not refused(final) and posting_link(final):
        found = read_posting(final, fetcher)
        if found and found.get("description"):
            return _read(final, _complete({**found, "url": found.get("url") or final}, final, fetcher), via=url)
    if body is None:
        return _needs(final or url, f"The page could not be opened ({error}). Paste the employer's own link or the full job text.")
    published = jsonld_posting(body)
    if published and published.get("description"):
        return _read(final, {**published, "url": final, "method": "structured_data"}, via=url)
    links = [link for link in posting_links(body, final) if not refused(link)]
    if len(links) == 1:
        found = read_posting(links[0], fetcher)
        if found and found.get("description"):
            return _read(links[0], _complete({**found, "url": found.get("url") or links[0]}, links[0], fetcher), via=url)
    if len(links) > 1:
        return _needs(final, f"The page links to {len(links)} different postings, so the app will not pick one. "
                             "Paste the link of the posting you mean.", candidates=links[:10])
    return _needs(final, "The page publishes no structured posting and no link to the employer's own posting. "
                         "Paste the employer's own application link, or the full job text.")


def to_posting(resolved: dict, *, company: str = "", title: str = "", location: str = "") -> dict:
    """A resolved link as the posting add_posting saves; the caller's own values fill only what is missing."""
    found = resolved.get("posting") or {}
    return {"company": found.get("company") or company, "title": found.get("title") or title,
            "location": found.get("location") or location, "url": resolved.get("url") or "",
            "description": found.get("description") or "", "requisition_id": str(found.get("requisition_id") or ""),
            "raw_salary": found.get("raw_salary"), "posted_at": found.get("posted_at") or "",
            "valid_through": found.get("valid_through") or "",
            "verification": f"Read from the posting's own source ({found.get('method') or 'page'})"
                            + (f", reached from the pasted link {resolved['via']}" if resolved.get("via") else "") + "."}
