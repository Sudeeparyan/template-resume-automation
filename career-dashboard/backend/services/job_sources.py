"""Job sources read without AI: employer career feeds and public job boards.

Discovery by AI web search is one narrow pass: a handful of searches, a dozen pages,
and a summary in place of the posting. This module reads postings straight from where
they are published, with the complete text, so the market, work-permit, duplicate,
legitimacy and requirement checks all see the employer's own words:

* employer feeds: Greenhouse, Lever and Ashby (services/portals.py), Workday and
  SmartRecruiters, and Workable, Recruitee, Personio and Teamtailor (market/readers/ats.py),
  for the profile's tracked companies (data/config/portals.yml), the country's verified
  employer directory (backend/countries/<code>/employers.yml) and the employer registry of
  permit employers (countries/ie/employer-registry.csv, built from DETE's permit data);
* EURES, the European Commission's portal, for vacancies Ireland's public employment
  service (JobsIreland) publishes: where employers advertise before a General Employment
  Permit (market/readers/eures.py);
* Irish job boards whose pages publish schema.org JobPosting data: gradireland (its
  sitemap lists every live job) and jobs.ie (its search pages). Both are read for the
  person's own job search only (personal use);
* optional aggregators with the person's own key, Careerjet and Jooble: their results are
  leads, looked up on the employer's own board (market/readers/aggregators.py). How each
  source may be used is in market/policy.py.

Every posting read is also recorded in the shared public market store (backend/market/),
which keeps advertised pay for market estimates and, later, the Tracker.

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
import random
import re
import threading
import time
import urllib.robotparser
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import yaml

from backend import telemetry
from backend.paths import COUNTRIES
from backend.services.portals import board_token, fetch_board, html_to_text, is_public_ats, official_posting

USER_AGENT = "Mozilla/5.0 (compatible; CareerWorkspace/1.0; personal job search on the candidate's own computer)"
TIMEOUT = 20
# Seconds between two requests to the same host.
MIN_INTERVAL = 1.0
# Documented public job APIs, built for programs to read; robots.txt is for crawlers of web pages.
PUBLIC_APIS = {"boards-api.greenhouse.io", "api.lever.co", "api.eu.lever.co", "api.ashbyhq.com", "api.smartrecruiters.com",
               "search.api.careerjet.net"}
# Keyed APIs served from a site's own host: only their API path counts as the API.
PUBLIC_API_PATHS = {"ie.jooble.org": "/api/"}
# Largest body read from one page. A larger one is that page's problem (TOO_LARGE), never the host's.
MAX_BYTES = 3_000_000
# A documented job API answers with a whole board at once: OpenAI's Ashby board is about 15 MB of
# JSON and Elastic's Greenhouse board about 8 MB, so the PUBLIC_APIS hosts get a larger limit.
API_MAX_BYTES = 25_000_000
TOO_LARGE = "too large to read"


def byte_limit(host: str) -> int:
    """The largest body read from ``host``: more for a documented job API than for a web page."""
    return API_MAX_BYTES if host in PUBLIC_APIS else MAX_BYTES


class PageTooLarge(Exception):
    """A response body over the byte limit: the page answered, so its host is not counted as failing."""


# Politeness towards a struggling host (Fetcher): the longest Retry-After waited out, the
# longest robots.txt Crawl-delay kept, the cap on backoff, and the failures in a row that
# make a run leave a host alone.
RETRY_CAP = 120
MAX_CRAWL_DELAY = 30.0
BACKOFF_CAP = 30.0
BREAKER_FAILURES = 3
# Hosts that answer "429 Too Many Requests" at one request a second, and the pace they accept.
HOST_INTERVALS = {"apply.workable.com": 3.0}

SOURCE_LABELS = {
    "tracked": "Your tracked companies",
    "directory": "Employer directory",
    "registry": "Permit employers (DETE registry)",
    "eures": "EURES / JobsIreland",
    "gradireland": "gradireland",
    "jobs_ie": "jobs.ie",
    "careerjet": "Careerjet (leads)",
    "jooble": "Jooble (leads)",
    "held": "Postings held for an AI requirement check",
}
# Where a person can see each job board for themselves.
BOARD_URLS = {"gradireland": "https://gradireland.com", "jobs_ie": "https://www.jobs.ie",
              "eures": "https://europa.eu/eures/portal/jv-se/home?lang=en", "careerjet": "https://www.careerjet.ie",
              "jooble": "https://ie.jooble.org"}
# Sources that only make sense for one market.
MARKET_SOURCES = {"gradireland": "ie", "jobs_ie": "ie", "eures": "ie", "registry": "ie", "careerjet": "ie", "jooble": "ie"}
COUNTRY_NAMES = {"ie": "Ireland", "irl": "Ireland", "ireland": "Ireland", "us": "United States", "usa": "United States",
                 "gb": "United Kingdom", "uk": "United Kingdom", "gbr": "United Kingdom"}


# ----- fetching ------------------------------------------------------------------------------

class Fetcher:
    """Polite HTTP: honest user agent, robots.txt (and its Crawl-delay), a pause between requests
    to one host, conditional re-reads, and backing off from a host that is struggling.

    * A 429 or 503 with a Retry-After of up to two minutes is waited out and tried once more.
    * Each failure (a 5xx, a timeout) lengthens the next pause to that host; after
      ``BREAKER_FAILURES`` in a row the host is left alone for the rest of this fetcher's run.
    * With a ``cache`` (market/http_cache.py) a GET sends the page's last ETag or Last-Modified,
      so an unchanged page costs the site a "304 Not Modified".

    ``opener(request, timeout)`` is urllib's urlopen; tests hand in a fake.
    """

    def __init__(self, *, min_interval: float = MIN_INTERVAL, timeout: int = TIMEOUT, opener=None,
                 respect_robots: bool = True, sleep: Callable[[float], None] = time.sleep, cache=None):
        self.min_interval = min_interval
        self.timeout = timeout
        self.opener = opener or urlopen
        self.respect_robots = respect_robots
        self.sleep = sleep
        self.cache = cache
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._delays: dict[str, float] = {}     # robots.txt Crawl-delay, seconds
        self._failures: dict[str, int] = {}     # consecutive failures per host
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._slots = threading.BoundedSemaphore(4)
        self.requests = 0
        self.refused: list[str] = []

    def _pace(self, host: str) -> None:
        with self._lock:
            interval = max(self.min_interval, self._delays.get(host, 0.0), HOST_INTERVALS.get(host, 0.0))
            failures = self._failures.get(host, 0)
            if failures:
                # Exponential backoff with jitter, so a struggling host is not hit in step.
                interval += min(BACKOFF_CAP, 2.0 ** failures) * (0.75 + random.random() / 2)
            wait = self._last.get(host, 0.0) + interval - time.monotonic()
            self._last[host] = time.monotonic() + max(0.0, wait)
        if wait > 0:
            telemetry.event("paced", host=host, seconds=round(wait, 2))
            self.sleep(wait)

    def _succeeded(self, host: str) -> None:
        with self._lock:
            self._failures.pop(host, None)

    def _failed(self, host: str) -> None:
        with self._lock:
            self._failures[host] = self._failures.get(host, 0) + 1

    def broken(self, host: str) -> bool:
        """True once a host failed ``BREAKER_FAILURES`` times in a row: it is not asked again this run."""
        return self._failures.get(host, 0) >= BREAKER_FAILURES

    def _raw(self, url: str, data: bytes | None = None, accept: str = "*/*", content_type: str | None = None,
             headers: dict | None = None):
        """(status, body text, final url, error)."""
        host = (urlsplit(url).hostname or "").lower()
        with self._lock:
            lock = self._host_locks.setdefault(host, threading.Lock())
        with lock, self._slots:
            return self._request(url, data, accept, content_type, headers)

    def _request(self, url, data, accept, content_type, headers=None):
        host = (urlsplit(url).hostname or "").lower()
        method = "POST" if data is not None else "GET"
        with telemetry.span(f"{method} {host}", **{"http.request.method": method, "url.full": telemetry.safe_url(url),
                                                   "server.address": host, "career.http.accept": accept}) as current:
            result = self._send(url, host, method, data, accept, content_type, headers)
            status, body, _final, error = result
            telemetry.set_attributes(current, **{"http.response.status_code": status, "career.http.chars": len(body or ""),
                                                 "error.type": error})
            if error or (status is not None and status >= 400):
                telemetry.mark_failed(current, error or f"HTTP {status}")
            return result

    def _send(self, url, host, method, data, accept, content_type, extra_headers=None):
        if self.broken(host):
            telemetry.event("circuit_open", host=host)
            return None, "", url, f"skipped ({host} failed {BREAKER_FAILURES} times in a row on this pass)"
        # A request with its own credentials is never answered from (or saved to) the shared page cache.
        use_cache = self.cache is not None and method == "GET" and not extra_headers
        cached = self.cache.get(url) if use_cache else None
        limit = byte_limit(host)
        for attempt in range(2):
            self._pace(host)
            headers = {"User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en-IE,en;q=0.8",
                       "Accept-Encoding": "gzip", **(extra_headers or {})}
            if content_type:
                headers["Content-Type"] = content_type
            if cached:
                if cached.get("etag"):
                    headers["If-None-Match"] = cached["etag"]
                if cached.get("last_modified"):
                    headers["If-Modified-Since"] = cached["last_modified"]
            request = Request(url, data=data, headers=headers, method=method)
            self.requests += 1
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    raw = response.read(limit + 1)
                    if len(raw) > limit:
                        raise PageTooLarge()
                    response_headers = getattr(response, "headers", None)
                    getter = getattr(response_headers, "get", lambda *_: None)
                    if str(getter("Content-Encoding") or "").lower() == "gzip":
                        raw = _gunzip(raw, limit)
                    charset = response_headers.get_content_charset() if hasattr(response_headers, "get_content_charset") else None
                    body = _decode(raw, charset)
                    self._succeeded(host)
                    if use_cache and response.status == 200:
                        self.cache.put(url, etag=str(getter("ETag") or ""), last_modified=str(getter("Last-Modified") or ""),
                                       body=body)
                    return response.status, body, response.geturl(), None
            except PageTooLarge:
                self._succeeded(host)  # the host answered; only this page is too big to read
                telemetry.event("page_too_large", host=host, limit=limit)
                return None, "", url, f"{TOO_LARGE} (over {limit / 1_000_000:g} MB)"
            except HTTPError as exc:
                if exc.code == 304 and cached:
                    self._succeeded(host)
                    telemetry.event("http_cache_hit", host=host)
                    return 200, cached["body"], url, None
                if exc.code in (429, 503) and attempt == 0:
                    wait = _retry_after(exc.headers.get("Retry-After") if exc.headers else None)
                    if wait is not None:
                        telemetry.event("retry_after", host=host, status=exc.code, seconds=wait)
                        self.sleep(wait + random.random())
                        continue
                if exc.code == 429 or exc.code >= 500:
                    self._failed(host)
                return exc.code, "", url, f"HTTP {exc.code}"
            except (URLError, TimeoutError, OSError, EOFError, zlib.error) as exc:
                self._failed(host)
                return None, "", url, f"unreachable ({type(exc).__name__})"
        self._failed(host)
        return None, "", url, "unreachable (the site asked to wait longer than two minutes)"

    def allowed(self, url: str) -> bool:
        """robots.txt says this user agent may read the URL (documented public APIs always may)."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not self.respect_robots or host in PUBLIC_APIS or (
                host in PUBLIC_API_PATHS and parts.path.startswith(PUBLIC_API_PATHS[host])):
            return True
        if host not in self._robots:
            status, body, _, _ = self._raw(f"{parts.scheme}://{parts.netloc}/robots.txt", accept="text/plain")
            parser = urllib.robotparser.RobotFileParser()
            if status == 200:
                # Python's parser ends a group at a blank line; major crawlers (and RFC 9309) end it
                # only at the next User-agent line. europa.eu puts "Crawl-delay: 10" and later
                # Disallow rules after blank lines inside its "User-agent: *" group.
                parser.parse([line for line in body.splitlines() if line.strip()])
            elif status in (401, 403):
                parser.disallow_all = True
            elif status is not None and 400 <= status < 500:
                parser.allow_all = True
            else:
                parser = None  # unreachable or a server error: read nothing from this host this run
            delay = parser.crawl_delay(USER_AGENT) if parser else None
            if delay:
                # The site's own pace (europa.eu asks for 10 seconds), capped so one page cannot stall a run.
                self._delays[host] = min(float(delay), MAX_CRAWL_DELAY)
            self._robots[host] = parser
        parser = self._robots[host]
        ok = bool(parser and parser.can_fetch(USER_AGENT, url))
        if not ok:
            self.refused.append(host)
            telemetry.event("robots_refused", host=host, url=telemetry.safe_url(url),
                            reason="robots.txt disallows it" if parser else "robots.txt unreachable")
        return ok

    def text(self, url: str, accept: str = "text/html,application/xhtml+xml,application/xml") -> tuple[str | None, str | None]:
        if not self.allowed(url):
            return None, "the site's robots.txt does not allow automated reading"
        status, body, _, error = self._raw(url, accept=accept)
        if error or status != 200:
            return None, error or f"HTTP {status}"
        return body, None

    def page(self, url: str, accept: str = "text/html,application/xhtml+xml") -> tuple[str | None, str, str | None]:
        """(body, the URL it ended at after redirects, error): for a pasted link that may redirect."""
        if not self.allowed(url):
            return None, url, "the site's robots.txt does not allow automated reading"
        status, body, final, error = self._raw(url, accept=accept)
        if error or status != 200:
            return None, final or url, error or f"HTTP {status}"
        return body, final or url, None

    def json(self, url: str, payload: dict | None = None, *, headers: dict | None = None) -> tuple[Any, str | None]:
        """(parsed JSON, error); ``headers`` adds request headers (an API's Authorization), never cached."""
        if not self.allowed(url):
            return None, "the site's robots.txt does not allow automated reading"
        data = json.dumps(payload).encode() if payload is not None else None
        status, body, _, error = self._raw(url, data=data, accept="application/json",
                                           content_type="application/json" if data is not None else None,
                                           headers=headers)
        if error or status != 200:
            return None, error or f"HTTP {status}"
        try:
            return json.loads(body), None
        except ValueError:
            return None, "the response was not valid JSON"


def _gunzip(raw: bytes, limit: int) -> bytes:
    """A gzip body, inflated to at most ``limit`` bytes (an oversized one raises PageTooLarge, a damaged one zlib.error)."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    body = inflater.decompress(raw, limit)
    if inflater.unconsumed_tail:
        raise PageTooLarge()
    return body


def _retry_after(value) -> float | None:
    """Seconds from a Retry-After header (seconds or an HTTP date); None when absent or over RETRY_CAP."""
    from email.utils import parsedate_to_datetime

    text = str(value or "").strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(text) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, IndexError):
            return None
    return max(0.0, seconds) if seconds <= RETRY_CAP else None


def _decode(raw: bytes, charset: str | None) -> str:
    """The page's text: its declared charset, else UTF-8, else Windows-1252 (some boards mix them)."""
    for encoding in [c for c in (charset, "utf-8") if c]:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("cp1252", "replace")


def _default_fetcher() -> Fetcher:
    from backend.market.http_cache import HttpCache

    return Fetcher(cache=HttpCache())


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

    Greenhouse, Lever and Ashby through their feeds (portals.official_posting), Workday,
    SmartRecruiters, Workable, Recruitee and Personio through theirs, anything else
    (Teamtailor included) through the page's schema.org JobPosting.
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
    from backend.market.readers import ats

    full = ats.read_posting(url, fetcher)
    if full:
        return full
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
    from backend.market.readers import ats as boards

    if ats in boards.KINDS:
        return boards.careers_url(row)
    base = {"greenhouse": "https://boards.greenhouse.io/", "lever": "https://jobs.lever.co/",
            "lever_eu": "https://jobs.eu.lever.co/", "ashby": "https://jobs.ashbyhq.com/",
            "smartrecruiters": "https://jobs.smartrecruiters.com/"}.get(ats)
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
    if host == "jobs.eu.lever.co" and path:
        return {**row, "ats": "lever_eu", "token": path[0]}
    if host == "jobs.ashbyhq.com" and path:
        return {**row, "ats": "ashby", "token": path[0]}
    if host in {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"} and path:
        return {**row, "ats": "smartrecruiters", "token": path[0]}
    if host.endswith(".myworkdayjobs.com"):
        sites = [segment for segment in path if not _LOCALE.match(segment)]
        if sites:
            return {**row, "ats": "workday", "host": host, "site": sites[0]}
    from backend.market.readers import ats

    board = ats.board_row(url)
    if board:
        return {**row, **board}
    raise ValueError("That link is not a Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Recruitee, "
                     "Personio or Teamtailor careers page, so it cannot be read directly. Open the company's careers "
                     "page and use the link of its job list.")


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
    if source == "registry":
        # Careers boards of employers in DETE's permit records (market/registry.py), minus boards found gone.
        from backend.market import registry as permit_registry
        from backend.market.store import MarketStore

        try:
            gone = MarketStore().gone_boards(days=permit_registry.GONE_DAYS)
        except Exception as error:  # noqa: BLE001 - the market store is a cache; read every board instead
            telemetry.event("market.boards_unavailable", error=telemetry.safe_error(error))
            gone = set()
        return [row for market in target_markets_for(root) for row in permit_registry.employer_rows(market, gone=gone)]
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
    from backend.market.readers import ats as boards

    if ats in boards.KINDS:
        board = boards.read_board(row, fetcher, tz=tz)
        return ([posting for posting in board["postings"] if title_ok(posting["title"]) and place_ok(posting["location"])
                 and not (skip_posting and skip_posting(posting))], board["error"])
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
        if source in ("tracked", "directory", "registry"):
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
                    for row, found, error, attempted in pool.map(telemetry.carry(read_employer), batch):
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
                        if source == "registry":
                            note_board(row, error)
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
        if source == "eures":
            from backend.market.readers.eures import eures_jobs

            found, error = eures_jobs(fetcher, keywords, title_ok, place_ok, tz=tz, deadline=deadline,
                                      coverage=registry, skip_posting=skip_posting)
        elif source == "gradireland":
            found, error = gradireland_jobs(fetcher, title_ok, place_ok, tz=tz, deadline=deadline, coverage=registry, skip_posting=skip_posting)
        elif source == "jobs_ie":
            found, error = jobs_ie_jobs(fetcher, keywords, title_ok, place_ok, tz=tz, deadline=deadline, coverage=registry, skip_posting=skip_posting)
        elif source in ("careerjet", "jooble"):
            # Optional keyed aggregators: leads looked up on the employer's own board (market/readers/aggregators.py).
            from backend.market.readers.aggregators import aggregator_jobs

            found, error, note = aggregator_jobs(source, fetcher, keywords, title_ok, place_ok, root=root, tz=tz,
                                                 deadline=deadline, coverage=registry, skip_posting=skip_posting)
            added = keep(found)
            registry.checkpoint("feeds:" + source, {}, "failed" if error else "complete", found=added, error=error or "")
            coverage.append(f"{SOURCE_LABELS[source]}: " + (f"FETCH FAILED ({error})" if error else note))
            progress(SOURCE_LABELS[source], url=BOARD_URLS.get(source, ""), found=added, error=str(error or ""))
            continue
        else:
            coverage.append(f"{source}: unknown source")
            continue
        added = keep(found)
        if source in ("jobs_ie", "eures"):
            prefix = "jobs_ie:" if source == "jobs_ie" else "eures:"
            states = [registry.get(prefix + (k if source == "jobs_ie" else k.casefold())).get("state", "partial") for k in keywords]
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
    rechecked = {item.get("url") for item in held or []}  # held from an earlier pass, not read again now
    record_in_market([item for item in postings if item.get("url") not in rechecked])
    return postings, coverage


def note_board(row: dict, error: str | None) -> None:
    """A registry board that answered "not found" is set aside (market/registry.py GONE_DAYS); one read again is active."""
    from backend.market.store import MarketStore

    try:
        if error and error.startswith("HTTP 404"):
            MarketStore().mark_board({**row, "careers_url": row_url(row)}, "gone")
        elif not error:
            MarketStore().mark_board({**row, "careers_url": row_url(row)}, "active")
    except Exception as failure:  # noqa: BLE001 - board states are a cache of public data
        telemetry.event("market.board_state_failed", error=telemetry.safe_error(failure))


def record_in_market(postings: list[dict]) -> dict | None:
    """Add what was read to the shared public market store; a store problem never stops a search."""
    from backend import features

    if not postings or not features.enabled("market_store"):
        return None
    from backend.market.store import MarketStore

    try:
        counts = MarketStore().record_postings(postings)
    except Exception as error:  # noqa: BLE001 - the market store is a cache of public data
        telemetry.event("market.record_failed", error=telemetry.safe_error(error))
        return None
    telemetry.event("market.recorded", **counts)
    return counts
