"""Tracked companies' own career pages, read through their public ATS JSON feeds.

Greenhouse, Lever and Ashby publish every open posting as JSON with the full job
description. No AI call is needed to find, gate and save these, so this is the
cheapest and most reliable discovery mode: it reads data/config/portals.yml,
pulls each company's board, and hands the postings to the same relevance,
sponsorship and never-re-apply gates as the AI search.

Standard library only (urllib + json). A board that cannot be read is reported as
FETCH FAILED in the coverage notes; it never stops the pass and never looks empty.
"""
from __future__ import annotations

import html as _html
import json
import re
from typing import Any
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from backend.paths import CONFIG, TIMEZONE


def _today(tz: str | None = None) -> str:
    """The access date in the candidate's time zone, for the source records on each posting."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(tz or TIMEZONE)).date().isoformat()

# Each profile reads its own data/config/portals.yml (tracked_companies(root=...)).
PORTALS_YML = CONFIG / "portals.yml"
TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (portable-career-workspace; local job search)"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NL_RE = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    """Greenhouse returns HTML-escaped HTML, so unescape then strip, repeatedly."""
    if not raw:
        return ""
    text = raw
    for _ in range(3):
        before = text
        text = _html.unescape(text)
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|ul|ol)\s*>", "\n", text)
        text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
        text = _TAG_RE.sub(" ", text)
        if text == before:
            break
    text = text.replace("\xa0", " ").replace("​", "")
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _NL_RE.sub("\n\n", text).strip()


def tracked_companies(enabled_only: bool = True, root=None) -> list[dict[str, Any]]:
    """Read the workspace's portals.yml; cap-exempt employers first (no lottery for them)."""
    path = Path(root) / "data/config/portals.yml" if root else PORTALS_YML
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a missing or broken file means no tracked boards
        return []
    rows = [r for r in data.get("tracked_companies") or [] if isinstance(r, dict) and (not enabled_only or r.get("enabled", True))]
    rows.sort(key=lambda r: (not r.get("cap_exempt", False), r.get("name", "")))
    return rows


def board_token(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """(ats, token) from explicit fields, else inferred from the careers URL."""
    ats = (row.get("ats") or "").strip().lower() or None
    # portals.yml documents `token`; older files wrote `ats_token`. Both are read.
    token = str(row.get("ats_token") or row.get("token") or "").strip() or None
    if ats and token:
        return ats, token
    url = (row.get("careers_url") or "").lower()
    for key, marker in (("greenhouse", "boards.greenhouse.io/"), ("greenhouse", "job-boards.greenhouse.io/"),
                        ("lever", "jobs.lever.co/"), ("ashby", "jobs.ashbyhq.com/")):
        if marker in url:
            tail = url.split(marker, 1)[1].strip("/").split("/")[0].split("?")[0]
            if tail:
                return key, tail
    return ats, token


def _get_json(url: str):
    """(data, error): a board that cannot be read reports why instead of looking empty."""
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            if response.status != 200:
                return None, f"HTTP {response.status}"
            return json.loads(response.read().decode("utf-8", errors="replace")), None
    except HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return None, f"unreachable ({type(exc).__name__})"
    except ValueError:
        return None, "the board returned invalid JSON"


def _posting(row, source_id, title, url, location, description, employer_type="company", tz=None):
    return {
        "company": row.get("name") or "",
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url or "",
        "requisition_id": str(source_id or ""),
        "description": (description or "").strip(),
        "company_sources": [{"title": f"{row.get('name')} careers", "url": row.get("careers_url", ""), "accessed_at": _today(tz)}],
        "legal_presence": "Posting read from the company's own ATS board (tracked in portals.yml).",
        "verification": "Read directly from the employer's ATS JSON feed.",
        "red_flags": [],
        "size_category": "unknown",
        "employee_min": None,
        "employee_max": None,
        "sponsorship_state": "unknown",
        "sponsorship_evidence": [],
        "restriction_quote": "",
        "employer_type": "university" if row.get("cap_exempt") else employer_type,
        "applicant_count": None,
        "competition_signals": {"posted_within_72h": False, "limited_syndication": True, "niche_match": False},
    }


def _lever_text(job: dict[str, Any]) -> str:
    body = job.get("descriptionPlain") or html_to_text(job.get("description") or "")
    for lst in job.get("lists") or []:
        body += "\n\n" + (lst.get("text") or "") + "\n" + html_to_text(lst.get("content") or "")
    # The closing section is where Lever postings usually state sponsorship and clearance.
    return (body + "\n\n" + (job.get("additionalPlain") or html_to_text(job.get("additional") or ""))).strip()


def is_public_ats(url: str) -> bool:
    """Whether a posting should have an employer-controlled public JSON record."""
    host = (urlsplit(url or "").hostname or "").lower()
    return host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"}


def official_posting(url: str) -> dict[str, str] | None:
    """Read the complete text and location from one employer-controlled ATS record.

    These pages are built by script, so some AI web tools (Azure's) read only part of them,
    and an AI summary can drop the sponsorship sentence; the gates need the employer's own
    words and location. None means this exact requisition could not be read from the feed.
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    path = [segment for segment in parts.path.split("/") if segment]
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and "jobs" in path[:-1]:
        job_id = path[path.index("jobs") + 1]
        data, _ = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{path[0]}/jobs/{job_id}")
        if isinstance(data, dict):
            description = html_to_text(data.get("content") or "")
            if description:
                return {"description": description, "location": str((data.get("location") or {}).get("name") or "")}
    if host == "jobs.lever.co" and len(path) >= 2:
        data, _ = _get_json(f"https://api.lever.co/v0/postings/{path[0]}/{path[1]}")
        if isinstance(data, dict):
            description = _lever_text(data)
            if description:
                return {"description": description, "location": str((data.get("categories") or {}).get("location") or "")}
    if host == "jobs.ashbyhq.com" and len(path) >= 2:
        data, _ = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{path[0]}")
        for job in (data or {}).get("jobs", []) or []:
            if path[1] in (job.get("id"), job.get("jobId")) or (job.get("jobUrl") or "").rstrip("/").endswith(path[1]):
                description = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or "")
                if description:
                    return {"description": description, "location": str(job.get("location") or "")}
    return None


def full_text(url: str) -> str | None:
    """Compatibility helper for callers needing only the official job description."""
    posting = official_posting(url)
    return posting["description"] if posting else None


def fetch_board(row: dict[str, Any], tz: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """(postings, error). ``error`` is None on success, even an empty board."""
    ats, token = board_token(row)
    if not ats or not token:
        return [], None
    out: list[dict[str, Any]] = []
    error: str | None = None
    if ats == "greenhouse":
        data, error = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
        for job in (data or {}).get("jobs", []) or []:
            out.append(_posting(row, job.get("id"), job.get("title"), job.get("absolute_url"),
                                ((job.get("location") or {}).get("name") or ""), html_to_text(job.get("content") or ""), tz=tz))
    elif ats == "lever":
        data, error = _get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
        for job in data or []:
            cats = job.get("categories") or {}
            out.append(_posting(row, job.get("id"), job.get("text"), job.get("hostedUrl") or job.get("applyUrl"),
                                cats.get("location") or "", _lever_text(job), tz=tz))
    elif ats == "ashby":
        data, error = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
        for job in (data or {}).get("jobs", []) or []:
            out.append(_posting(row, job.get("id") or job.get("jobId"), job.get("title"), job.get("jobUrl") or job.get("applyUrl"),
                                job.get("location") or "", job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or ""), tz=tz))
    return out, error


def fetch_all(limit_per_board: int | None = None, root=None, tz: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Every open posting on every tracked board of the workspace at `root`, plus a coverage note per board."""
    postings: list[dict[str, Any]] = []
    coverage: list[str] = []
    for row in tracked_companies(root=root):
        ats, token = board_token(row)
        if not ats or not token:
            coverage.append(f"{row.get('name')}: no public ATS feed configured (careers page only)")
            continue
        found, error = fetch_board(row, tz)
        postings.extend(found[:limit_per_board] if limit_per_board else found)
        if error:
            coverage.append(f"{row.get('name')}: FETCH FAILED ({error}) via {ats}")
        else:
            coverage.append(f"{row.get('name')}: {len(found)} open postings via {ats}")
    return postings, coverage
