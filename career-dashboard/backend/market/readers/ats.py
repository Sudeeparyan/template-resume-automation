"""Employer careers boards on Workable, Recruitee, Personio and Teamtailor.

Each is the employer's own careers feed, published for programs and job boards to read, and
one request lists the whole board with every posting's full text:

* Workable: the careers-widget API, ``apply.workable.com/api/v1/widget/accounts/<account>``
  (with ``details=true`` each job carries its description, requirements and benefits);
* Recruitee: the careers-site API, ``<company>.recruitee.com/api/offers/`` (pay when given);
* Personio: the XML job feed, ``<company>.jobs.personio.de/xml`` (some companies' robots.txt
  refuses all reading; the fetcher obeys and the board reports why it was not read);
* Teamtailor: the RSS job feed, ``<company>.teamtailor.com/jobs.rss`` (or a custom domain).

Lever's EU data centre (``jobs.eu.lever.co``) is read with Lever's other boards in
services/portals.py. Every reader returns postings in the shape job_sources.make_posting
builds, plus the board's own employer name where the feed states one (the employer
registry compares it with DETE's legal names).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import quote, urlsplit

KINDS = ("workable", "recruitee", "personio", "teamtailor")
WORKABLE_BOARD = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
WORKABLE_JOB = "https://apply.workable.com/api/v2/accounts/{token}/jobs/{shortcode}"
RECRUITEE_BOARD = "https://{host}/api/offers/"
RECRUITEE_OFFER = "https://{host}/api/offers/{slug}"
PERSONIO_BOARD = "https://{host}/xml?language=en"
TEAMTAILOR_BOARD = "https://{host}/jobs.rss"
TEAMTAILOR_NS = "{https://teamtailor.com/locations}"
_PERSONIO_HOST = re.compile(r"^([a-z0-9-]+)\.jobs\.personio\.(de|com)$")
_DTD = re.compile(r"<!(DOCTYPE|ENTITY)", re.I)


def host_for(row: dict) -> str:
    """The careers host one board is read from (a custom domain when the row names one)."""
    kind = str(row.get("ats") or "").casefold()
    host = str(row.get("host") or "").strip().lower()
    token = str(row.get("token") or row.get("ats_token") or "").strip()
    if host:
        return host
    if kind == "recruitee":
        return f"{token.lower()}.recruitee.com"
    if kind == "personio":
        return f"{token.lower()}.jobs.personio.de"
    if kind == "teamtailor":
        return f"{token.lower()}.teamtailor.com"
    return "apply.workable.com" if kind == "workable" else ""


def careers_url(row: dict) -> str:
    kind, token = str(row.get("ats") or "").casefold(), str(row.get("token") or row.get("ats_token") or "")
    if kind == "workable":
        return f"https://apply.workable.com/{token}/" if token else ""
    host = host_for(row)
    if kind == "teamtailor":
        return f"https://{host}/jobs" if host else ""
    return f"https://{host}/" if host else ""


def _text(value) -> str:
    from backend.services.portals import html_to_text

    return html_to_text(str(value or ""))


def _join(*parts) -> str:
    return ", ".join(dict.fromkeys(str(p).strip() for p in parts if p and str(p).strip()))


def _places(*places: str, remote: bool = False) -> str:
    joined = "; ".join(dict.fromkeys(p for p in places if p))
    return _join("Remote", joined) if remote else joined


def _xml(text: str):
    """A parsed feed, or None; a feed declaring a DTD is refused (no entity expansion)."""
    if not text or _DTD.search(text[:2000]):
        return None
    try:
        return ElementTree.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ElementTree.ParseError:
        return None


def _posting(row: dict, *, company: str, title: str, url: str, location: str, description: str, source_id: str,
             posted_at: str = "", valid_through: str = "", raw_salary=None, tz: str | None = None) -> dict:
    from backend.services.job_sources import make_posting

    return make_posting(company or row.get("name") or "", title, url, location, description,
                        source=row.get("_source", "directory"), source_kind="employer_feed",
                        vouched=row.get("_vouched", ""), requisition_id=source_id, posted_at=posted_at,
                        careers_url=careers_url(row), tz=tz, raw_salary=raw_salary, valid_through=valid_through)


# ---- Workable ---------------------------------------------------------------------------------

def _workable_place(job: dict) -> str:
    places = [_join(p.get("city"), p.get("region"), p.get("country")) for p in job.get("locations") or [] if isinstance(p, dict)]
    if not places:
        places = [_join(job.get("city"), job.get("state") or job.get("region"), job.get("country"))]
    return _places(*places, remote=bool(job.get("telecommuting") or job.get("remote")))


def workable_board(row: dict, fetcher, *, tz: str | None = None) -> dict:
    token = str(row.get("token") or "")
    data, error = fetcher.json(WORKABLE_BOARD.format(token=quote(token)))
    if not isinstance(data, dict):
        return {"postings": [], "name": "", "error": error or "Invalid Workable board"}
    company = " ".join(str(data.get("name") or "").split())
    out = []
    for job in data.get("jobs") or []:
        if not isinstance(job, dict) or not job.get("shortcode") or str(job.get("state") or "published") != "published":
            continue
        description = _text(job.get("description"))
        if not description:
            continue
        out.append(_posting(row, company=row.get("name") or company, title=str(job.get("title") or ""),
                            url=f"https://apply.workable.com/{token}/j/{job['shortcode']}/",
                            location=_workable_place(job), description=description, source_id=str(job["shortcode"]),
                            posted_at=str(job.get("published_on") or job.get("created_at") or ""), tz=tz))
    return {"postings": out, "name": company, "error": None}


def workable_job(token: str, shortcode: str, fetcher) -> dict | None:
    """One Workable posting in full (description, requirements and benefits)."""
    data, _ = fetcher.json(WORKABLE_JOB.format(token=quote(token), shortcode=quote(shortcode)))
    if not isinstance(data, dict):
        return None
    sections = [(heading, _text(data.get(key))) for heading, key in
                (("", "description"), ("Requirements", "requirements"), ("Benefits", "benefits"))]
    description = "\n\n".join((f"{heading}\n{body}" if heading else body) for heading, body in sections if body)
    if not description:
        return None
    places = [_join(p.get("city"), p.get("region"), p.get("country")) for p in data.get("locations") or [] if isinstance(p, dict)]
    location = _places(*(places or [_join(*((data.get("location") or {}).get(k) for k in ("city", "region", "country")))]),
                       remote=bool(data.get("remote")))
    return {"title": str(data.get("title") or ""), "description": description, "location": location,
            "url": f"https://apply.workable.com/{token}/j/{shortcode}/", "requisition_id": str(shortcode),
            "posted_at": str(data.get("published") or ""), "valid_through": "", "raw_salary": None, "company": ""}


_WORKABLE_ACCOUNT = re.compile(r'href="https://apply\.workable\.com/([A-Za-z0-9_-]+)/j/([A-Za-z0-9]+)')


def workable_parts(url: str, fetcher=None) -> tuple[str, str] | None:
    """(account, shortcode) of a Workable posting link; a bare /j/<code> link names its account in its page."""
    parts = urlsplit(url or "")
    if (parts.hostname or "").lower() != "apply.workable.com":
        return None
    path = [segment for segment in parts.path.split("/") if segment]
    if len(path) >= 3 and path[1] == "j":
        return path[0], path[2]
    if len(path) >= 2 and path[0] == "j" and fetcher is not None:
        html, _ = fetcher.text(url)
        match = _WORKABLE_ACCOUNT.search(html or "")
        if match and match[2] == path[1]:
            return match[1], match[2]
    return None


# ---- Recruitee --------------------------------------------------------------------------------

def _recruitee_place(offer: dict) -> str:
    places = [_join(p.get("city") or p.get("name"), p.get("state"), p.get("country"))
              for p in offer.get("locations") or [] if isinstance(p, dict)]
    return _places(*(places or [str(offer.get("location") or "")]), remote=bool(offer.get("remote")))


def _recruitee_posting(row: dict, offer: dict, host: str, *, tz: str | None) -> dict | None:
    description = "\n\n".join(p for p in (_text(offer.get("description")), _text(offer.get("requirements"))) if p)
    if not description or not offer.get("title"):
        return None
    pay = offer.get("salary") if isinstance(offer.get("salary"), dict) else None
    pay = pay if pay and (pay.get("min") or pay.get("max")) else None
    return _posting(row, company=row.get("name") or str(offer.get("company_name") or ""), title=str(offer["title"]),
                    url=str(offer.get("careers_url") or f"https://{host}/o/{offer.get('slug') or ''}"),
                    location=_recruitee_place(offer), description=description, source_id=str(offer.get("id") or ""),
                    posted_at=str(offer.get("published_at") or offer.get("created_at") or ""),
                    valid_through=str(offer.get("close_at") or ""), raw_salary=pay, tz=tz)


def recruitee_board(row: dict, fetcher, *, tz: str | None = None) -> dict:
    host = host_for(row)
    data, error = fetcher.json(RECRUITEE_BOARD.format(host=host))
    if not isinstance(data, dict):
        return {"postings": [], "name": "", "error": error or "Invalid Recruitee board"}
    offers = [o for o in data.get("offers") or [] if isinstance(o, dict) and str(o.get("status") or "published") == "published"]
    names = [str(o.get("company_name") or "").strip() for o in offers if o.get("company_name")]
    out = [p for p in (_recruitee_posting(row, offer, host, tz=tz) for offer in offers) if p]
    return {"postings": out, "name": max(set(names), key=names.count) if names else "", "error": None}


def recruitee_offer(url: str, fetcher) -> dict | None:
    """One Recruitee posting from its /o/<slug> page link (its careers host serves the same API)."""
    parts = urlsplit(url or "")
    path = [segment for segment in parts.path.split("/") if segment]
    if len(path) < 2 or path[0] != "o" or not parts.hostname:
        return None
    data, _ = fetcher.json(RECRUITEE_OFFER.format(host=parts.hostname.lower(), slug=quote(path[1])))
    offer = (data or {}).get("offer") if isinstance(data, dict) else None
    if not isinstance(offer, dict):
        return None
    found = _recruitee_posting({"ats": "recruitee", "host": parts.hostname.lower()}, offer, parts.hostname.lower(), tz=None)
    if not found:
        return None
    return {**{k: found[k] for k in ("title", "description", "location", "url", "requisition_id", "posted_at",
                                     "valid_through", "raw_salary")}, "company": str(offer.get("company_name") or "")}


# ---- Personio ---------------------------------------------------------------------------------

def _child(node, tag: str) -> str:
    found = node.find(tag)
    return " ".join((found.text or "").split()) if found is not None and found.text else ""


def personio_board(row: dict, fetcher, *, tz: str | None = None) -> dict:
    host = host_for(row)
    body, error = fetcher.text(PERSONIO_BOARD.format(host=host), accept="application/xml,text/xml")
    tree = _xml(body or "")
    if tree is None or tree.tag != "workzag-jobs":
        return {"postings": [], "name": "", "error": error or "Invalid Personio feed"}
    out, names = [], []
    for position in tree.findall("position"):
        position_id, title = _child(position, "id"), _child(position, "name")
        sections = []
        for part in position.findall("jobDescriptions/jobDescription"):
            heading, body_text = _child(part, "name"), _text(part.findtext("value") or "")
            if body_text:
                sections.append(f"{heading}\n{body_text}" if heading else body_text)
        if not (position_id and title and sections):
            continue
        offices = [_child(position, "office")] + [" ".join((o.text or "").split()) for o in position.findall("additionalOffices/office")]
        company = _child(position, "subcompany")
        if company:
            names.append(company)
        out.append(_posting(row, company=row.get("name") or company, title=title,
                            url=f"https://{host}/job/{position_id}", location=_places(*offices),
                            description="\n\n".join(sections), source_id=position_id,
                            posted_at=_child(position, "createdAt"), tz=tz))
    return {"postings": out, "name": max(set(names), key=names.count) if names else "", "error": None}


def personio_parts(url: str) -> tuple[str, str] | None:
    """(host, position id) of a Personio posting link, else None."""
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    path = [segment for segment in parts.path.split("/") if segment]
    if not _PERSONIO_HOST.match(host) or len(path) < 2 or path[0] != "job" or not path[1].isdigit():
        return None
    return host, path[1]


# ---- Teamtailor -------------------------------------------------------------------------------

def _rss_date(value: str) -> str:
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, IndexError):
        return ""


def teamtailor_board(row: dict, fetcher, *, tz: str | None = None) -> dict:
    host = host_for(row)
    body, error = fetcher.text(TEAMTAILOR_BOARD.format(host=host), accept="application/rss+xml,application/xml,text/xml")
    tree = _xml(body or "")
    channel = tree.find("channel") if tree is not None else None
    if channel is None:
        return {"postings": [], "name": "", "error": error or "Invalid Teamtailor feed"}
    out = []
    for item in channel.findall("item"):
        title, link = _child(item, "title"), _child(item, "link")
        description = _text(item.findtext("description") or "")
        if not (title and link and description):
            continue
        places = [_join(_child(place, TEAMTAILOR_NS + "city") or _child(place, TEAMTAILOR_NS + "name"),
                        _child(place, TEAMTAILOR_NS + "country"))
                  for place in item.findall(f"{TEAMTAILOR_NS}locations/{TEAMTAILOR_NS}location")]
        remote = _child(item, "remoteStatus").casefold() in {"fully", "remote", "temporary"}
        match = re.search(r"/jobs/(\d+)", link)
        out.append(_posting(row, company=row.get("name") or "", title=title, url=link,
                            location=_places(*places, remote=remote), description=description,
                            source_id=match[1] if match else _child(item, "guid"),
                            posted_at=_rss_date(_child(item, "pubDate")), tz=tz))
    return {"postings": out, "name": _child(channel, "title"), "error": None}


# ---- one board, whichever of these it is ------------------------------------------------------

READERS: dict[str, Callable[..., dict]] = {"workable": workable_board, "recruitee": recruitee_board,
                                           "personio": personio_board, "teamtailor": teamtailor_board}


def read_board(row: dict, fetcher, *, tz: str | None = None) -> dict:
    """{"postings": [...], "name": the board's own employer name or "", "error": None or why it failed}."""
    reader = READERS.get(str(row.get("ats") or "").casefold())
    if reader is None:
        return {"postings": [], "name": "", "error": f"Unknown board type {row.get('ats')!r}"}
    return reader(row, fetcher, tz=tz)


def board_row(url: str) -> dict | None:
    """The ats/token/host of a careers link on one of these boards, else None."""
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    path = [segment for segment in parts.path.split("/") if segment]
    if host == "apply.workable.com" and path and path[0] not in {"j", "api"}:
        return {"ats": "workable", "token": path[0]}
    if host.endswith(".recruitee.com") and host.count(".") == 2:
        return {"ats": "recruitee", "token": host.split(".")[0]}
    match = _PERSONIO_HOST.match(host)
    if match:
        return {"ats": "personio", "token": match[1], "host": host}
    if host.endswith(".teamtailor.com") and host.count(".") == 2:
        return {"ats": "teamtailor", "token": host.split(".")[0]}
    return None


def read_posting(url: str, fetcher) -> dict | None:
    """One posting from a Workable, Recruitee or Personio link (Teamtailor pages carry schema.org data)."""
    parts = workable_parts(url, fetcher)
    if parts:
        found = workable_job(*parts, fetcher)
        return {**found, "method": "workable_feed"} if found else None
    split = urlsplit(url or "")
    host, path = (split.hostname or "").lower(), [segment for segment in split.path.split("/") if segment]
    if host and len(path) == 2 and path[0] == "o":
        # Recruitee's posting links are /o/<slug>, on <company>.recruitee.com or a custom careers domain.
        found = recruitee_offer(url, fetcher)
        if found:
            return {**found, "method": "recruitee_feed"}
    if host.endswith(".recruitee.com"):
        return None
    parts = personio_parts(url)
    if parts:
        board = personio_board({"ats": "personio", "host": parts[0]}, fetcher)
        for found in board["postings"]:
            if found["requisition_id"] == parts[1]:
                return {**{k: found[k] for k in ("title", "description", "location", "url", "requisition_id",
                                                 "posted_at", "valid_through", "raw_salary")},
                        "company": board["name"], "method": "personio_feed"}
        return None
    return None
