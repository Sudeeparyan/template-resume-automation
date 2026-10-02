"""Job sources read without AI: employer career feeds and public job boards.

Discovery by AI web search is one narrow pass: a handful of searches, a dozen pages,
and a summary in place of the posting. This module reads postings straight from where
they are published, with the complete text, so the market, work-permit, duplicate,
legitimacy and requirement checks all see the employer's own words:

* employer feeds: Greenhouse, Lever and Ashby (services/portals.py), plus Workday and
  SmartRecruiters, for the profile's tracked companies (data/config/portals.yml) and
  the country's verified employer directory (backend/countries/<code>/employers.yml);
* Irish job boards whose pages publish schema.org JobPosting data: gradireland (its
  sitemap lists every live job), jobs.ie (its search pages), and the askmanavi
  graduate tracker (which links each role to the employer's own ATS page).

Only titles that name one of the profile's target roles are opened, so a board of
thousands costs a few dozen requests. The fetcher identifies itself honestly, obeys
each site's robots.txt (documented public job APIs excepted), waits between two
requests to one host and stops at a time budget. Sites that refuse automated reading
(LinkedIn, Indeed, IrishJobs.ie, Glassdoor) are left to the AI's own web search.

Every function returns postings in the same shape as ``portals.fetch_board``, plus
``source`` (where it was read), ``source_kind`` (employer_feed or job_board) and
``vouched`` (why the legitimacy check may accept the employer without a web search).
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import yaml

from backend.paths import COUNTRIES
from backend.services.portals import board_token, fetch_board, html_to_text, is_public_ats, official_posting

USER_AGENT = "Mozilla/5.0 (compatible; CareerWorkspace/1.0; personal job search on the candidate's own computer)"
TIMEOUT = 20
# Seconds between two requests to the same host.
MIN_INTERVAL = 1.0
# Documented public job APIs, built for programs to read; robots.txt is for crawlers of web pages.
PUBLIC_APIS = {"boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com", "api.smartrecruiters.com"}
# Largest body read from one page.
MAX_BYTES = 3_000_000

SOURCE_LABELS = {
    "tracked": "Your tracked companies",
    "directory": "Employer directory",
    "gradireland": "gradireland",
    "jobs_ie": "jobs.ie",
    "askmanavi": "askmanavi graduate tracker",
    "held": "Postings held for an AI requirement check",
}
# Where a person can see each job board for themselves.
BOARD_URLS = {"gradireland": "https://gradireland.com", "jobs_ie": "https://www.jobs.ie",
              "askmanavi": "https://askmanavi.com/graduate-tracker"}
# Sources that only make sense for one market.
MARKET_SOURCES = {"gradireland": "ie", "jobs_ie": "ie", "askmanavi": "ie"}
COUNTRY_NAMES = {"ie": "Ireland", "irl": "Ireland", "ireland": "Ireland", "us": "United States", "usa": "United States",
                 "gb": "United Kingdom", "uk": "United Kingdom", "gbr": "United Kingdom"}


# ----- fetching ------------------------------------------------------------------------------

class Fetcher:
    """Polite HTTP: honest user agent, robots.txt, a pause between requests to one host.

    ``opener(request, timeout)`` is urllib's urlopen; tests hand in a fake.
    """

    def __init__(self, *, min_interval: float = MIN_INTERVAL, timeout: int = TIMEOUT, opener=None,
                 respect_robots: bool = True, sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self.timeout = timeout
        self.opener = opener or urlopen
        self.respect_robots = respect_robots
        self.sleep = sleep
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._slots = threading.BoundedSemaphore(4)
        self.requests = 0
        self.refused: list[str] = []

    def _pace(self, host: str) -> None:
        with self._lock:
            wait = self._last.get(host, 0.0) + self.min_interval - time.monotonic()
            self._last[host] = time.monotonic() + max(0.0, wait)
        if wait > 0:
            self.sleep(wait)

    def _raw(self, url: str, data: bytes | None = None, accept: str = "*/*", content_type: str | None = None):
        """(status, body text, final url, error)."""
        host = (urlsplit(url).hostname or "").lower()
        with self._lock:
            lock = self._host_locks.setdefault(host, threading.Lock())
        with lock, self._slots:
            return self._request(url, data, accept, content_type)

    def _request(self, url, data, accept, content_type):
        host = (urlsplit(url).hostname or "").lower()
        self._pace(host)
        headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en-IE,en;q=0.8"}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        self.requests += 1
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read(MAX_BYTES)
                response_headers = getattr(response, "headers", None)
                charset = response_headers.get_content_charset() if hasattr(response_headers, "get_content_charset") else None
                return response.status, _decode(raw, charset), response.geturl(), None
        except HTTPError as exc:
            return exc.code, "", url, f"HTTP {exc.code}"
        except (URLError, TimeoutError, OSError) as exc:
            return None, "", url, f"unreachable ({type(exc).__name__})"

    def allowed(self, url: str) -> bool:
        """robots.txt says this user agent may read the URL (documented public APIs always may)."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not self.respect_robots or host in PUBLIC_APIS:
            return True
        if host not in self._robots:
            status, body, _, _ = self._raw(f"{parts.scheme}://{parts.netloc}/robots.txt", accept="text/plain")
            parser = urllib.robotparser.RobotFileParser()
            if status == 200:
                parser.parse(body.splitlines())
            elif status in (401, 403):
                parser.disallow_all = True
            elif status is not None and 400 <= status < 500:
                parser.allow_all = True
            else:
                parser = None  # unreachable or a server error: read nothing from this host this run
            self._robots[host] = parser
        parser = self._robots[host]
        ok = bool(parser and parser.can_fetch(USER_AGENT, url))
        if not ok:
            self.refused.append(host)
        return ok

    def text(self, url: str, accept: str = "text/html,application/xhtml+xml,application/xml") -> tuple[str | None, str | None]:
        if not self.allowed(url):
            return None, "the site's robots.txt does not allow automated reading"
        status, body, _, error = self._raw(url, accept=accept)
        if error or status != 200:
            return None, error or f"HTTP {status}"
        return body, None

    def json(self, url: str, payload: dict | None = None) -> tuple[Any, str | None]:
        if not self.allowed(url):
            return None, "the site's robots.txt does not allow automated reading"
        data = json.dumps(payload).encode() if payload is not None else None
        status, body, _, error = self._raw(url, data=data, accept="application/json",
                                           content_type="application/json" if data is not None else None)
        if error or status != 200:
            return None, error or f"HTTP {status}"
        try:
            return json.loads(body), None
        except ValueError:
            return None, "the response was not valid JSON"


def _decode(raw: bytes, charset: str | None) -> str:
    """The page's text: its declared charset, else UTF-8, else Windows-1252 (some boards mix them)."""
    for encoding in [c for c in (charset, "utf-8") if c]:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("cp1252", "replace")


def _default_fetcher() -> Fetcher:
    return Fetcher()


# ----- the shape every source returns --------------------------------------------------------

def today(tz: str | None = None) -> str:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(tz or "Europe/Dublin")).date().isoformat()


def make_posting(company: str, title: str, url: str, location: str, description: str, *, source: str,
                 source_kind: str, vouched: str = "", requisition_id: str = "", posted_at: str = "",
                 careers_url: str = "", tz: str | None = None, verification: str = "",
                 raw_salary=None, valid_through: str = "") -> dict:
    from backend.services import salary
    return {
        "company": " ".join(str(company or "").split()),
        "title": " ".join(str(title or "").split()),
        "location": " ".join(str(location or "").split()),
        "url": url or "",
        "requisition_id": str(requisition_id or ""),
        "description": (description or "").strip(),
        "raw_salary": raw_salary,
        "salary": salary.extract(description or "", raw_salary=raw_salary, url=url or "", observed_at=today(tz)),
        "valid_through": valid_through,
        "company_sources": ([{"title": f"{company} careers", "url": careers_url, "accessed_at": today(tz)}]
                            if careers_url else []),
        "legal_presence": vouched,
        "verification": verification or f"Read directly from {SOURCE_LABELS.get(source, source)} on {today(tz)}.",
        "red_flags": [],
        "size_category": "unknown",
        "employee_min": None,
        "employee_max": None,
        "sponsorship_state": "unknown",
        "sponsorship_evidence": [],
        "restriction_quote": "",
        "employer_type": "company",
        "applicant_count": None,
        "competition_signals": {"posted_within_72h": _within_hours(posted_at, 72), "limited_syndication": source_kind == "employer_feed",
                                "niche_match": False},
        "posted_at": posted_at or "",
        "source": source,
        "source_kind": source_kind,
        "vouched": vouched,
    }


def _within_hours(stamp: str, hours: int) -> bool:
    when = _parse_date(stamp)
    return bool(when and datetime.now(timezone.utc) - when <= timedelta(hours=hours))


def _parse_date(stamp: str) -> datetime | None:
    if not stamp:
        return None
    text = str(stamp).strip().replace("Z", "+00:00")
    for candidate in (text, text[:10]):
        try:
            when = datetime.fromisoformat(candidate)
            return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def age_days(stamp: str) -> int | None:
    when = _parse_date(stamp)
    return None if when is None else max(0, (datetime.now(timezone.utc) - when).days)


# ----- schema.org JobPosting (job boards and many career sites) ------------------------------

_LD_JSON = re.compile(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)


def _types(node: dict) -> set:
    kind = node.get("@type")
    return {str(k) for k in (kind if isinstance(kind, list) else [kind])}


def _walk_ld(data):
    if isinstance(data, list):
        for item in data:
            yield from _walk_ld(item)
    elif isinstance(data, dict):
        yield data
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in data:
                yield from _walk_ld(data[key])


def _place(node) -> str:
    """"Dublin, Leinster, Ireland" from a schema.org Place (or a list of them)."""
    if isinstance(node, list):
        return "; ".join(p for p in (_place(item) for item in node) if p)
    if not isinstance(node, dict):
        return str(node or "")
    address = node.get("address") if isinstance(node.get("address"), dict) else node
    country = address.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name") or country.get("@id") or ""
    country = COUNTRY_NAMES.get(str(country or "").strip().casefold(), str(country or "").strip())
    parts = [str(address.get(k) or "").strip() for k in ("addressLocality", "addressRegion")]
    parts = [p for p in parts if p]
    if country and country.casefold() not in " ".join(parts).casefold():
        parts.append(country)
    return ", ".join(parts)


def jsonld_posting(html: str) -> dict | None:
    """The page's JobPosting: title, company, location, full description, dates, id."""
    for match in _LD_JSON.finditer(html or ""):
        try:
            data = json.loads(match.group(1).strip(), strict=False)
        except ValueError:
            continue
        for node in _walk_ld(data):
            if "JobPosting" not in _types(node):
                continue
            organisation = node.get("hiringOrganization") or {}
            company = organisation.get("name") if isinstance(organisation, dict) else str(organisation)
            location = _place(node.get("jobLocation"))
            if str(node.get("jobLocationType") or "").upper() == "TELECOMMUTE":
                allowed = _place(node.get("applicantLocationRequirements"))
                location = ", ".join(p for p in ("Remote", allowed or location) if p)
            identifier = node.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value")
            description = html_to_text(unescape(str(node.get("description") or "")))
            if not description:
                continue
            return {
                "title": " ".join(unescape(str(node.get("title") or "")).split()),
                "company": " ".join(unescape(str(company or "")).split()),
                "location": location,
                "description": description,
                "posted_at": str(node.get("datePosted") or ""),
                "valid_through": str(node.get("validThrough") or ""),
                "raw_salary": node.get("baseSalary"),
                "requisition_id": str(identifier or ""),
                "company_url": str((organisation or {}).get("sameAs") or (organisation or {}).get("url") or "")
                if isinstance(organisation, dict) else "",
            }
    return None


# ----- Workday -------------------------------------------------------------------------------

_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def workday_parts(url: str) -> tuple[str, str, str, str] | None:
    """(host, tenant, site, external_path) from a myworkdayjobs.com posting URL, else None."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if not host.endswith(".myworkdayjobs.com"):
        return None
    path = [segment for segment in parts.path.split("/") if segment]
    if path and _LOCALE.match(path[0]):
        path = path[1:]
    if len(path) < 3 or "job" not in path[1:]:
        return None
    site = path[0]
    index = path.index("job", 1)
    return host, host.split(".")[0], site, "/" + "/".join(path[index:])


def workday_detail(host: str, tenant: str, site: str, external_path: str, fetcher: Fetcher) -> dict | None:
    data, _ = fetcher.json(f"https://{host}/wday/cxs/{tenant}/{site}{external_path}")
    info = (data or {}).get("jobPostingInfo") if isinstance(data, dict) else None
    if not isinstance(info, dict) or not info.get("jobDescription"):
        return None
    places = [str(info.get("location") or "")] + [str(p) for p in info.get("additionalLocations") or []]
    country = ((info.get("country") or {}).get("descriptor") or "") if isinstance(info.get("country"), dict) else ""
    location = "; ".join(p for p in places if p)
    if country and country.casefold() not in location.casefold():
        location = f"{location}, {country}" if location else country
    organisation = ((data or {}).get("hiringOrganization") or {}).get("name") or ""
    return {
        "title": str(info.get("title") or ""),
        "description": html_to_text(info.get("jobDescription") or ""),
        "location": location,
        "requisition_id": str(info.get("jobReqId") or ""),
        "posted_at": str(info.get("startDate") or ""),
        "valid_through": str(info.get("endDate") or ""),
        "raw_salary": info.get("baseSalary") or info.get("salaryRange"),
        "url": str(info.get("externalUrl") or f"https://{host}/{site}{external_path}"),
        "company": str(organisation),
    }


def _checkpoint(coverage, key, cursor, *, complete=False, found=0, error=""):
    if coverage:
        coverage.checkpoint(key, cursor, "failed" if error else "complete" if complete else "partial",
                            found=found, error=error)


def workday_jobs(row: dict, fetcher: Fetcher, title_ok: Callable[[str], bool], place_ok: Callable[[str], bool], *,
                 search_text: str, pages: int = 5, details: int = 15, tz: str | None = None,
                 deadline: float | None = None, coverage=None, skip_posting=None) -> tuple[list[dict], str | None]:
    """Resume pages and details; already-decided postings consume no detail budget."""
    host, site = str(row.get("host") or ""), str(row.get("site") or "")
    tenant = host.split(".")[0]
    key = f"workday:{host}/{site}:{search_text}"
    cursor = (coverage.get(key).get("cursor") or {}) if coverage else {}
    offset, index = int(cursor.get("offset", 0)), int(cursor.get("index", 0))
    out, attempts = [], 0
    for _ in range(pages):
        if deadline and time.monotonic() > deadline:
            break
        data, error = fetcher.json(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                                  {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": search_text})
        if not isinstance(data, dict):
            _checkpoint(coverage, key, {"offset": offset, "index": index}, found=len(out), error=error or "Invalid listing")
            return out, error or "Invalid listing"
        batch = data.get("jobPostings") or []
        for position, item in enumerate(batch):
            if position < index:
                continue
            if attempts >= details or (deadline and time.monotonic() > deadline):
                _checkpoint(coverage, key, {"offset": offset, "index": position}, found=len(out))
                return out, None
            title, where = str(item.get("title") or ""), str(item.get("locationsText") or "")
            path = str(item.get("externalPath") or "")
            preview = {"url": f"https://{host}/{site}{path}", "company": row.get("name") or "", "title": title}
            if title_ok(title) and (place_ok(where) or re.match(r"^\d+ Locations?$", where)) and not (skip_posting and skip_posting(preview)):
                attempts += 1
                full = workday_detail(host, tenant, site, path, fetcher)
                if full and place_ok(full["location"]):
                    out.append(make_posting(row.get("name") or full["company"], full["title"] or title, full["url"], full["location"],
                                            full["description"], source=row.get("_source", "directory"), source_kind="employer_feed",
                                            vouched=row.get("_vouched", ""), requisition_id=full["requisition_id"],
                                            posted_at=full["posted_at"], careers_url=f"https://{host}/{site}", tz=tz,
                                            raw_salary=full.get("raw_salary"), valid_through=full.get("valid_through", "")))
            _checkpoint(coverage, key, {"offset": offset, "index": position + 1}, found=len(out))
        offset += 20
        index = 0
        if len(batch) < 20 or offset >= int(data.get("total") or 0):
            _checkpoint(coverage, key, {}, complete=True, found=len(out))
            return out, None
    _checkpoint(coverage, key, {"offset": offset, "index": index}, found=len(out))
    return out, None


# ----- SmartRecruiters -----------------------------------------------------------------------

def smartrecruiters_detail(company: str, posting_id: str, fetcher: Fetcher) -> dict | None:
    data, _ = fetcher.json(f"https://api.smartrecruiters.com/v1/companies/{quote(company)}/postings/{quote(posting_id)}")
    if not isinstance(data, dict):
        return None
    sections = ((data.get("jobAd") or {}).get("sections") or {})
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
        section = sections.get(key) or {}
        if section.get("text"):
            parts.append((section.get("title") or "") + "\n" + html_to_text(section["text"]))
    description = "\n\n".join(p.strip() for p in parts if p.strip())
    if not description:
        return None
    location = (data.get("location") or {})
    where = str(location.get("fullLocation") or ", ".join(
        str(location.get(k) or "") for k in ("city", "region", "country") if location.get(k)))
    where = re.sub(r"(,\s*)+", ", ", where).strip(", ")
    if location.get("remote"):
        where = "Remote, " + where
    return {"title": str(data.get("name") or "").strip(), "description": description, "location": where,
            "url": str(data.get("postingUrl") or ""), "requisition_id": str(data.get("refNumber") or data.get("id") or ""),
            "posted_at": str(data.get("releasedDate") or ""),
            "raw_salary": data.get("baseSalary") or data.get("salary"),
            "valid_through": str(data.get("validThrough") or ""),
            "company": str((data.get("company") or {}).get("name") or company)}


def smartrecruiters_jobs(row: dict, fetcher: Fetcher, title_ok: Callable[[str], bool], *, country: str,
                         details: int = 15, tz: str | None = None, deadline: float | None = None,
                         coverage=None, skip_posting=None) -> tuple[list[dict], str | None]:
    company = str(row.get("token") or "")
    key = f"smartrecruiters:{company}:{country}"
    cursor = (coverage.get(key).get("cursor") or {}) if coverage else {}
    offset, index = int(cursor.get("offset", 0)), int(cursor.get("index", 0))
    out, attempts = [], 0
    for _ in range(4):
        if deadline and time.monotonic() > deadline:
            break
        data, error = fetcher.json(f"https://api.smartrecruiters.com/v1/companies/{quote(company)}/postings"
                                  f"?country={quote(country)}&limit=100&offset={offset}")
        if not isinstance(data, dict):
            _checkpoint(coverage, key, {"offset": offset, "index": index}, found=len(out), error=error or "Invalid listing")
            return out, error or "Invalid listing"
        batch = data.get("content") or []
        for position, item in enumerate(batch):
            if position < index:
                continue
            if attempts >= details or (deadline and time.monotonic() > deadline):
                _checkpoint(coverage, key, {"offset": offset, "index": position}, found=len(out))
                return out, None
            posting_id = str(item.get("id") or "")
            preview = {"url": f"https://jobs.smartrecruiters.com/{company}/{posting_id}", "company": row.get("name") or company,
                       "title": str(item.get("name") or ""), "requisition_id": str(item.get("refNumber") or posting_id)}
            if title_ok(preview["title"]) and not (skip_posting and skip_posting(preview)):
                attempts += 1
                full = smartrecruiters_detail(company, posting_id, fetcher)
                if full:
                    out.append(make_posting(row.get("name") or full["company"], full["title"], full["url"], full["location"],
                                            full["description"], source=row.get("_source", "directory"), source_kind="employer_feed",
                                            vouched=row.get("_vouched", ""), requisition_id=full["requisition_id"],
                                            posted_at=full["posted_at"], careers_url=f"https://jobs.smartrecruiters.com/{company}", tz=tz,
                                            raw_salary=full.get("raw_salary"), valid_through=full.get("valid_through", "")))
            _checkpoint(coverage, key, {"offset": offset, "index": position + 1}, found=len(out))
        offset += 100
        index = 0
        if len(batch) < 100 or offset >= int(data.get("totalFound") or 0):
            _checkpoint(coverage, key, {}, complete=True, found=len(out))
            return out, None
    _checkpoint(coverage, key, {"offset": offset, "index": index}, found=len(out))
    return out, None


def smartrecruiters_parts(url: str) -> tuple[str, str] | None:
    parts = urlsplit(url or "")
    if (parts.hostname or "").lower() not in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}:
        return None
    path = [segment for segment in parts.path.split("/") if segment]
    if len(path) < 2:
        return None
    match = re.match(r"(\d{6,})", path[1])
    return (path[0], match.group(1)) if match else None


# ----- reading one posting from its URL ------------------------------------------------------

def read_posting(url: str, fetcher: Fetcher | None = None) -> dict | None:
    """The complete text and location of one posting, read from where it is published.

    Greenhouse, Lever and Ashby through their feeds (portals.official_posting), Workday and
    SmartRecruiters through theirs, anything else through the page's schema.org JobPosting.
    None when the page cannot be read or publishes no structured posting.
    """
    official = official_posting(url, fetcher=fetcher) if fetcher else official_posting(url)
    if official:
        return {**official, "method": "ats_feed"}
    if is_public_ats(url):
        return None
    fetcher = fetcher or _default_fetcher()
    parts = workday_parts(url)
    if parts:
        full = workday_detail(*parts, fetcher)
        return {**full, "method": "workday_feed"} if full else None
    parts = smartrecruiters_parts(url)
    if parts:
        full = smartrecruiters_detail(*parts, fetcher)
        return {**full, "method": "smartrecruiters_feed"} if full else None
    host = (urlsplit(url or "").hostname or "").lower()
    if not host or host.endswith("linkedin.com") or host.endswith("indeed.com") or host.endswith("glassdoor.com") \
            or host.endswith("glassdoor.ie") or host.endswith("irishjobs.ie"):
        return None  # these refuse automated reading; the AI's own web reading stands
    html, _ = fetcher.text(url)
    found = jsonld_posting(html or "")
    return {**found, "method": "structured_data"} if found else None


# ----- job boards ----------------------------------------------------------------------------

_SITEMAP_URL = re.compile(r"<url>\s*<loc>([^<]+)</loc>(?:\s*<lastmod>([^<]+)</lastmod>)?", re.S)


def gradireland_jobs(fetcher: Fetcher, title_ok: Callable[[str], bool], place_ok: Callable[[str], bool], *,
                     max_age_days: int | None = None, details: int = 40, tz: str | None = None,
                     deadline: float | None = None, coverage=None, skip_posting=None) -> tuple[list[dict], str | None]:
    """Read still-open graduate programmes; publication age alone never closes a role."""
    key = "board:gradireland"
    body, error = fetcher.text("https://gradireland.com/sitemap-0.xml", accept="application/xml,text/xml")
    if body is None:
        _checkpoint(coverage, key, (coverage.get(key).get("cursor") or {}) if coverage else {}, error=error or "Unreadable sitemap")
        return [], error
    candidates = []
    for url, stamp in _SITEMAP_URL.findall(body):
        if "/jobs/" not in url:
            continue
        words = re.sub(r"-\d+$", "", url.rstrip("/").rsplit("/", 1)[-1]).replace("-", " ")
        age = age_days(stamp)
        if max_age_days is not None and age is not None and age > max_age_days:
            continue
        if title_ok(words):
            candidates.append(url)
    candidates = sorted(set(candidates))
    index = int((coverage.get(key).get("cursor") or {}).get("index", 0)) if coverage else 0
    out, attempts = [], 0
    for position in range(index, len(candidates)):
        url = candidates[position]
        if attempts >= details or (deadline and time.monotonic() > deadline):
            _checkpoint(coverage, key, {"index": position}, found=len(out))
            return out, None
        if not (skip_posting and skip_posting({"url": url})):
            attempts += 1
            html, _ = fetcher.text(url)
            found = jsonld_posting(html or "")
            if found and title_ok(found["title"]) and place_ok(found["location"]):
                valid = _parse_date(found.get("valid_through") or "")
                if not valid or valid >= datetime.now(timezone.utc):
                    out.append(make_posting(found["company"], found["title"], url, found["location"], found["description"],
                                            source="gradireland", source_kind="job_board", vouched=_board_vouch("gradireland", found),
                                            requisition_id=found["requisition_id"], posted_at=found["posted_at"],
                                            careers_url=found.get("company_url") or "", tz=tz,
                                            raw_salary=found.get("raw_salary"), valid_through=found.get("valid_through", "")))
        _checkpoint(coverage, key, {"index": position + 1}, found=len(out))
    _checkpoint(coverage, key, {}, complete=True, found=len(out))
    return out, None


def _preloaded_state(html: str, key: str):
    marker = re.search(r'window\.__PRELOADED_STATE__\[\s*["\']' + re.escape(key) + r'["\']\s*\]\s*=\s*', html or "")
    if not marker:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(html[marker.end():])
        return value
    except ValueError:
        return None


def jobs_ie_jobs(fetcher: Fetcher, keywords: list[str], title_ok: Callable[[str], bool], place_ok: Callable[[str], bool], *,
                 max_age_days: int | None = None, details: int = 30, pages: int = 2, tz: str | None = None,
                 deadline: float | None = None, coverage=None, skip_posting=None) -> tuple[list[dict], str | None]:
    """Fair keyword rotation with persistent page/detail positions."""
    out, attempts, seen = [], 0, set()
    keywords = coverage.order(keywords, key=lambda k: "jobs_ie:" + k) if coverage else keywords
    for keyword in keywords:
        key = "jobs_ie:" + keyword
        cursor = (coverage.get(key).get("cursor") or {}) if coverage else {}
        page, index = max(1, int(cursor.get("page", 1))), int(cursor.get("index", 0))
        slug = re.sub(r"[^a-z0-9]+", "-", keyword.casefold()).strip("-")
        for _ in range(pages):
            if attempts >= details or (deadline and time.monotonic() > deadline):
                return out, None
            url = f"https://www.jobs.ie/jobs/{slug}" + (f"?page={page}" if page > 1 else "")
            html, error = fetcher.text(url)
            state = _preloaded_state(html or "", "app-unifiedResultlist")
            if not isinstance(state, dict):
                _checkpoint(coverage, key, {"page": page, "index": index}, found=len(out), error=error or "Unreadable listing")
                return out, error or "Unreadable listing"
            items = (state.get("searchResults") or {}).get("items") or []
            for position, item in enumerate(items):
                if position < index:
                    continue
                if attempts >= details or (deadline and time.monotonic() > deadline):
                    _checkpoint(coverage, key, {"page": page, "index": position}, found=len(out))
                    return out, None
                title = str(item.get("title") or "")
                age = age_days(str(item.get("datePosted") or ""))
                if max_age_days is not None and age is not None and age > max_age_days:
                    _checkpoint(coverage, key, {"page": page, "index": position + 1}, found=len(out))
                    continue
                posting_url = "https://www.jobs.ie" + str(item.get("url") or "")
                preview = {"url": posting_url, "company": str(item.get("companyName") or ""), "title": title,
                           "requisition_id": str(item.get("id") or "")}
                if posting_url not in seen and title_ok(title) and place_ok(str(item.get("location") or "")) and not (skip_posting and skip_posting(preview)):
                    seen.add(posting_url)
                    attempts += 1
                    detail, _ = fetcher.text(posting_url)
                    found = jsonld_posting(detail or "")
                    if found:
                        valid = _parse_date(found.get("valid_through") or "")
                        if not valid or valid >= datetime.now(timezone.utc):
                            out.append(make_posting(found["company"] or preview["company"], found["title"] or title,
                                                    posting_url, found["location"] or str(item.get("location") or ""),
                                                    found["description"], source="jobs_ie", source_kind="job_board",
                                                    vouched=_board_vouch("jobs.ie", found), requisition_id=preview["requisition_id"],
                                                    posted_at=found["posted_at"] or str(item.get("datePosted") or ""), tz=tz,
                                                    raw_salary=found.get("raw_salary"), valid_through=found.get("valid_through", "")))
                _checkpoint(coverage, key, {"page": page, "index": position + 1}, found=len(out))
            if len(items) < 10:
                _checkpoint(coverage, key, {}, complete=True, found=len(out))
                break
            page, index = page + 1, 0
            _checkpoint(coverage, key, {"page": page, "index": 0}, found=len(out))
    return out, None


def _rsc_objects(html: str, marker: str) -> list[dict]:
    """JSON objects embedded in a Next.js page payload that start with ``marker``."""
    text = (html or "").replace('\\"', '"').replace("\\\\", "\\")
    found, decoder = [], json.JSONDecoder()
    for match in re.finditer(re.escape(marker), text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def askmanavi_jobs(fetcher: Fetcher, title_ok: Callable[[str], bool], place_ok: Callable[[str], bool], *,
                   details: int = 25, max_age_days: int | None = None, tz: str | None = None,
                   deadline: float | None = None, coverage=None, skip_posting=None) -> tuple[list[dict], str | None]:
    """The askmanavi graduate tracker's roles, each read in full from the employer's ATS page it links to."""
    html, error = fetcher.text("https://askmanavi.com/graduate-tracker")
    if html is None:
        return [], error
    roles = [o for o in _rsc_objects(html, '{"id":"') if o.get("applyUrl") and o.get("title") and o.get("company")]
    unique = {}
    for role in roles:
        url = str(role["applyUrl"])
        # Greenhouse roles often link to the employer's own site (stripe.com/jobs/search?gh_jid=N);
        # the tracker's id names the board ("gh-stripe-N"), whose feed has the full posting.
        board = re.match(r"^gh-([a-z0-9_-]+?)-(\d+)$", str(role.get("id") or ""))
        if board:
            url = f"https://job-boards.greenhouse.io/{board.group(1)}/jobs/{board.group(2)}"
            # The same requisition id the employer's own feed gives it, so the two are one posting.
            role = {**role, "_requisition": board.group(2)}
        unique.setdefault(url, role)
    out, attempts = [], 0
    key = "board:askmanavi"
    index = int((coverage.get(key).get("cursor") or {}).get("index", 0)) if coverage else 0
    for position, (url, role) in enumerate(sorted(unique.items())):
        if position < index:
            continue
        if attempts >= details or (deadline and time.monotonic() > deadline):
            _checkpoint(coverage, key, {"index": position}, found=len(out))
            return out, None
        _checkpoint(coverage, key, {"index": position + 1}, found=len(out))
        age = age_days(str(role.get("postedDate") or ""))
        if max_age_days is not None and age is not None and age > max_age_days:
            continue
        if not title_ok(str(role["title"])) or (skip_posting and skip_posting({"url": url, "company": role["company"], "title": role["title"], "requisition_id": role.get("_requisition", "")})):
            continue
        if not place_ok(str(role.get("location") or "")):
            continue
        attempts += 1
        full = read_posting(url, fetcher)
        if not full or not place_ok(str(full.get("location") or role.get("location") or "")):
            continue
        valid = _parse_date(full.get("valid_through") or "")
        if valid and valid < datetime.now(timezone.utc):
            continue
        sponsorship = str(role.get("visaSponsorship") or "")
        note = f" askmanavi lists visa sponsorship as {sponsorship}." if sponsorship and sponsorship != "Unknown" else ""
        out.append(make_posting(role["company"], full.get("title") or role["title"], full.get("url") or url,
                                full.get("location") or role.get("location") or "", full["description"],
                                source="askmanavi", source_kind="employer_feed",
                                vouched=(f"{role['company']} posting read from the employer's own careers system "
                                         f"({urlsplit(url).hostname}), linked from the askmanavi graduate tracker."),
                                requisition_id=str(full.get("requisition_id") or role.get("_requisition") or ""),
                                posted_at=str(role.get("postedDate") or full.get("posted_at") or ""), tz=tz,
                                raw_salary=full.get("raw_salary"), valid_through=full.get("valid_through", ""),
                                verification=f"Found on the askmanavi graduate tracker; full text read from the employer's "
                                             f"own careers page ({full.get('method', 'feed')}).{note}"))
    _checkpoint(coverage, key, {}, complete=True, found=len(out))
    return out, None


# ----- the employer lists --------------------------------------------------------------------

def directory(code: str) -> dict:
    """backend/countries/<code>/employers.yml: {"employers": [...], "agencies": [...]} (empty when absent)."""
    path = COUNTRIES / code / "employers.yml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"employers": [], "agencies": []}
    return {"employers": [e for e in data.get("employers") or [] if isinstance(e, dict) and e.get("name")],
            "agencies": [str(a).casefold() for a in data.get("agencies") or []]}


def is_agency(company: str, code: str) -> bool:
    name = (company or "").casefold()
    return any(re.search(r"(?<![a-z])" + re.escape(agency) + r"(?![a-z])", name) for agency in directory(code)["agencies"])


def row_url(row: dict) -> str:
    """The public careers page an employer row is read from."""
    if row.get("careers_url"):
        return str(row["careers_url"])
    ats, token = str(row.get("ats") or "").casefold(), str(row.get("token") or row.get("ats_token") or "")
    if ats == "workday" and row.get("host"):
        return f"https://{row['host']}/{row.get('site') or ''}".rstrip("/")
    base = {"greenhouse": "https://boards.greenhouse.io/", "lever": "https://jobs.lever.co/",
            "ashby": "https://jobs.ashbyhq.com/", "smartrecruiters": "https://jobs.smartrecruiters.com/"}.get(ats)
    return base + token if base and token else ""


def tracked_row(name: str, url: str) -> dict:
    """A portals.yml tracked_companies row for a careers link on a feed this module can read."""
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    path = [segment for segment in parts.path.split("/") if segment]
    row = {"name": " ".join(str(name or "").split()), "careers_url": url.strip(), "enabled": True}
    if not row["name"]:
        raise ValueError("Name the company to track")
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and path:
        return {**row, "ats": "greenhouse", "token": path[0]}
    if host == "jobs.lever.co" and path:
        return {**row, "ats": "lever", "token": path[0]}
    if host == "jobs.ashbyhq.com" and path:
        return {**row, "ats": "ashby", "token": path[0]}
    if host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"} and path:
        return {**row, "ats": "smartrecruiters", "token": path[0]}
    if host.endswith(".myworkdayjobs.com"):
        sites = [segment for segment in path if not _LOCALE.match(segment)]
        if sites:
            return {**row, "ats": "workday", "host": host, "site": sites[0]}
    raise ValueError("That link is not a Greenhouse, Lever, Ashby, Workday or SmartRecruiters careers page, so it "
                     "cannot be read directly. Open the company's careers page and use the link of its job list.")


def _board_vouch(board: str, found: dict) -> str:
    company = found.get("company") or "The advertiser"
    return (f"{company} advertised this role on {board}, an established Irish job board that publishes the employer "
            f"with each posting (schema.org JobPosting read on the board's own page).")


def employer_rows(root, source: str) -> list[dict]:
    """Rows for the tracked companies (profile) or the directory (country), with how each is vouched for."""
    from backend.countries import target_markets_for
    from backend.services.portals import tracked_companies

    if source == "tracked":
        rows = []
        for row in tracked_companies(root=root):
            rows.append({**row, "_source": "tracked",
                         "_vouched": f"Tracked employer: {row.get('name')} is listed in data/config/portals.yml and this "
                                     "posting was read from its own careers feed."})
        return rows
    from backend.services.source_coverage import Coverage
    registry = Coverage(root)
    rows = []
    for market in target_markets_for(root):
        for row in directory(market)["employers"]:
            rows.append({**row, "_source": "directory", "_market": market,
                         "_vouched": f"{row['name']} is in the verified {market.upper()} employer directory and this "
                                     "posting was read from its own careers feed."})
        rows.extend(registry.employers(market))
    return list({(row.get("_market"), row_url(row)): row for row in rows}.values())


def employer_feed(row: dict, fetcher: Fetcher, title_ok, place_ok, *, country: str, search_text: str,
                  tz: str | None = None, deadline: float | None = None, coverage=None, skip_posting=None) -> tuple[list[dict], str | None]:
    """One employer's current postings whose title matches, from whichever ATS it uses."""
    ats = str(row.get("ats") or "").strip().casefold()
    if ats == "workday":
        return workday_jobs(row, fetcher, title_ok, place_ok, search_text=search_text, tz=tz, deadline=deadline,
                            coverage=coverage, skip_posting=skip_posting)
    if ats == "smartrecruiters":
        return smartrecruiters_jobs(row, fetcher, title_ok, country=country, tz=tz, deadline=deadline,
                                    coverage=coverage, skip_posting=skip_posting)
    kind, token = board_token(row)
    if not kind or not token:
        return [], None
    postings, error = fetch_board({**row, "ats": kind, "ats_token": token}, tz, fetcher=fetcher)
    out = []
    for posting in postings:
        if not title_ok(posting["title"]) or not place_ok(posting["location"]) or (skip_posting and skip_posting(posting)):
            continue
        out.append({**posting, "legal_presence": row.get("_vouched", ""), "source": row.get("_source", "directory"),
                    "source_kind": "employer_feed", "vouched": row.get("_vouched", "") })
    return out, error


# ----- one harvest ---------------------------------------------------------------------------

def harvest(root, sources: list[str], *, title_ok, place_ok, keywords: list[str], tz: str | None = None,
            fetcher: Fetcher | None = None, seconds: float = 900, held: list[dict] | None = None,
            skip: Callable[[dict], bool] | None = None, skip_posting: Callable[[dict], bool] | None = None,
            progress: Callable[..., None] | None = None) -> tuple[list[dict], list[str]]:
    """Postings from each named source, with one coverage line per source (or per employer).

    ``skip(row)`` lets the caller pass over an employer it has read recently; ``held``
    are postings kept from an earlier pass for an AI requirement check. ``progress(label,
    url=, found=, error=)`` hears about each site as it is read (the live trace).
    """
    progress = progress or (lambda label, **detail: None)
    from backend.countries import target_markets_for

    from backend.services.source_coverage import Coverage
    registry = Coverage(root)
    fetcher = fetcher or _default_fetcher()
    deadline = time.monotonic() + seconds
    markets = target_markets_for(root)
    postings: list[dict] = []
    coverage: list[str] = []
    seen_urls: set[str] = set()

    def keep(found: list[dict]) -> int:
        added = 0
        for posting in found:
            if posting["url"] and posting["url"] not in seen_urls:
                seen_urls.add(posting["url"])
                postings.append(posting)
                added += 1
        return added

    for source in registry.order(sources, key=lambda name: "feeds:" + name):
        if time.monotonic() > deadline:
            coverage.append(f"{SOURCE_LABELS.get(source, source)}: not read (the time budget for this pass ran out)")
            continue
        if source in MARKET_SOURCES and MARKET_SOURCES[source] not in markets:
            continue
        if source == "held":
            added = keep(list(held or []))
            coverage.append(f"{SOURCE_LABELS['held']}: {added} re-checked")
            progress(SOURCE_LABELS["held"], found=added)
            continue
        if source in ("tracked", "directory"):
            rows = employer_rows(root, source)
            if not rows:
                coverage.append(f"{SOURCE_LABELS[source]}: none listed")
                continue
            rows = registry.order(rows, key=lambda row: "employer:" + row_url(row))
            read = matched = failed = skipped = 0
            partial = False

            def read_employer(row):
                if time.monotonic() > deadline:
                    return row, [], "", False
                country = row.get("_market") or (markets[0] if markets else "ie")
                try:
                    found, error = employer_feed(row, fetcher, title_ok, place_ok, country=country,
                                                 search_text=COUNTRY_NAMES.get(country, country), tz=tz,
                                                 deadline=deadline, coverage=registry, skip_posting=skip_posting)
                except Exception as exc:
                    found, error = [], f"{type(exc).__name__}: {str(exc)[:200]}"
                return row, found, error, True

            # Submit one bounded batch at a time: no queue of requests outlives the budget.
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="career-source") as pool:
                for offset in range(0, len(rows), 4):
                    if time.monotonic() > deadline:
                        partial = True
                        break
                    batch = []
                    for row in rows[offset:offset + 4]:
                        if skip and skip(row):
                            skipped += 1
                        else:
                            batch.append(row)
                    for row, found, error, attempted in pool.map(read_employer, batch):
                        if not attempted:
                            partial = True
                            continue
                        read += 1
                        failed += bool(error)
                        added = keep(found)
                        matched += added
                        # Detailed ATS checkpoints say whether a paged board was exhausted.
                        ats = str(row.get("ats") or "").lower()
                        country = row.get("_market") or (markets[0] if markets else "ie")
                        detail_key = (f"workday:{row.get('host')}/{row.get('site')}:{COUNTRY_NAMES.get(country, country)}" if ats == "workday"
                                      else f"smartrecruiters:{row.get('token')}:{country}" if ats == "smartrecruiters" else "")
                        state = "failed" if error else (registry.get(detail_key).get("state", "partial") if detail_key else "complete")
                        partial = partial or state == "partial"
                        registry.checkpoint("employer:" + row_url(row), {}, state, found=added, error=error or "")
                        if error:
                            coverage.append(f"{row.get('name')}: FETCH FAILED ({error})")
                        progress(str(row.get("name") or "Employer"), url=row_url(row), found=added,
                                 error=str(error or ""), ats=str(row.get("ats") or ""))
            state = "failed" if failed else "partial" if partial or skipped or read < len(rows) else "complete"
            registry.checkpoint("feeds:" + source, {}, state, found=matched,
                                error=f"{failed} employer feeds failed" if failed else "")
            coverage.append(f"{SOURCE_LABELS[source]}: read {read} of {len(rows)} configured employer feeds; "
                            f"{matched} new matching postings; {state}"
                            + (f", {skipped} skipped" if skipped else ""))
            continue
        if source == "gradireland":
            found, error = gradireland_jobs(fetcher, title_ok, place_ok, tz=tz, deadline=deadline, coverage=registry, skip_posting=skip_posting)
        elif source == "jobs_ie":
            found, error = jobs_ie_jobs(fetcher, keywords, title_ok, place_ok, tz=tz, deadline=deadline, coverage=registry, skip_posting=skip_posting)
        elif source == "askmanavi":
            found, error = askmanavi_jobs(fetcher, title_ok, place_ok, tz=tz, deadline=deadline, coverage=registry, skip_posting=skip_posting)
        else:
            coverage.append(f"{source}: unknown source")
            continue
        added = keep(found)
        if source == "jobs_ie":
            states = [registry.get("jobs_ie:" + k).get("state", "partial") for k in keywords]
            state = "failed" if error else "complete" if states and all(s == "complete" for s in states) else "partial"
        else:
            state = "failed" if error else registry.get("board:" + source).get("state", "partial")
        registry.checkpoint("feeds:" + source, {}, state, found=added, error=error or "")
        coverage.append(f"{SOURCE_LABELS[source]}: " + (f"FETCH FAILED ({error})" if error and not found else
                                                         f"{added} postings matched your target roles"))
        progress(SOURCE_LABELS[source], url=BOARD_URLS.get(source, ""), found=added,
                 error=str(error or "") if not found else "")
    if fetcher.refused:
        coverage.append("Not read because robots.txt disallows it: " + ", ".join(sorted(set(fetcher.refused))))
    return postings, coverage
