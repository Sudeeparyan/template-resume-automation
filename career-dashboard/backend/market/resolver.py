"""Find the careers boards of employers in DETE's permit data: the employer registry.

DETE publishes legal entities ("Kitman Labs Limited"); their boards are named after the brand
("kitmanlabs"). For each employer a few slug guesses (at most ``MAX_VARIANTS``) are tried on
the public job APIs of the common applicant-tracking systems, in ``GUESSED`` order. A board is
accepted only when both hold:

* it lists at least one current posting in Ireland, and
* it is the same employer: the board's own employer name matches the legal name (exactly, as
  the brand of it, or with a similarity of at least ``SIMILARITY``); for a board that states no
  name (Lever, Ashby), an Irish posting's own text must name the employer.

Everything else stays unresolved, with what was tried and why each near miss was refused. A
careers link found another way (by hand, or suggested by an AI web search) goes through the
same ``check_link`` before it is accepted, so a guess never enters the registry unverified.
Used by scripts/build_employer_registry.py; reads go through job_sources.Fetcher.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Callable
from urllib.parse import quote

from backend.permits.employer_names import (DESCRIPTORS, STOP_BRANDS, _plain, aliases, keys_for, normalize_ie,
                                            prefix_match)

ATS_ORDER = ("greenhouse", "lever", "lever_eu", "ashby", "smartrecruiters", "workable", "recruitee", "personio",
             "teamtailor")
# Systems whose boards are guessed from a name. Workable is checked only from a link: its API
# blocks a client (HTTP 429 for every request, for about half an hour) after some 70 guesses.
GUESSED = tuple(kind for kind in ATS_ORDER if kind != "workable")
SIMILARITY = 0.85
MAX_VARIANTS = 3
# Boards on per-company hosts, or that ask for a slow pace, get fewer guesses.
VARIANTS_PER_KIND = {"workable": 2, "recruitee": 2, "personio": 1, "teamtailor": 2}
# Words a board's title adds around the employer's name ("Careers at Kitman Labs", "Acme Jobs").
_BOARD_WORDS = re.compile(r"\b(careers?|jobs?|job board|vacancies|openings|join us|work(?:ing)? (?:at|with)|hiring|at)\b")


def _core(tokens: list[str]) -> list[str]:
    core = list(tokens)
    while len(core) > 1 and core[-1] in DESCRIPTORS:
        core.pop()
    return core


def slug_variants(legal_name: str) -> list[str]:
    """At most three board names to try: a known brand, the name without descriptor words, then the first word."""
    key = normalize_ie(legal_name)
    tokens = key.split()
    if not tokens or (len(tokens) == 1 and tokens[0] in STOP_BRANDS):
        return []
    brands = [brand for brand, legal_keys in aliases().items() if key in legal_keys]
    core = _core(tokens)
    if len(core) == 1 and (core[0] in STOP_BRANDS or len(core[0]) < 4) and len(tokens) > 1:
        core = tokens  # "global data services" is not the board "global"
    out = ["".join(brand.split()) for brand in brands]
    out += ["".join(core), "-".join(core) if len(core) > 1 else "", "".join(tokens) if tokens != core else ""]
    first = core[0]
    if len(core) > 1 and len(first) >= 4 and first not in STOP_BRANDS:
        out.append(first)
    return [slug for slug in dict.fromkeys(s for s in out if len(s) >= 3)][:MAX_VARIANTS]


def name_match(board_name: str, legal_name: str) -> str:
    """How a board's own employer name names this legal employer: "exact", "alias", "similar" or
    "brand" (the legal name is the board's name plus descriptor words, "Stripe" for "Stripe
    Technology Company"; the weakest, so the registry review lists it); "" when it does not."""
    text = _BOARD_WORDS.sub(" ", _plain(board_name)).strip()
    # Workday's hiring-company field can start with an internal company code
    # ("3100 Accenture Limited Company"); the name is tried with and without it.
    boards = [board for board in dict.fromkeys(normalize_ie(t) for t in (text, _COMPANY_CODE.sub("", text))) if board]
    if not boards:
        return ""
    keys = keys_for(legal_name)
    brands = [brand for brand, legal_keys in aliases().items() if set(keys) & set(legal_keys)]
    found = {_match(board, keys, brands) for board in boards}
    return next((strength for strength in ("exact", "alias", "similar", "brand") if strength in found), "")


_COMPANY_CODE = re.compile(r"^\d{3,6}\s+")


def _match(board: str, keys: list[str], brands: list[str]) -> str:
    if board in keys:
        return "exact"
    if board in brands:
        return "alias"
    if any(SequenceMatcher(None, board, key).ratio() >= SIMILARITY for key in keys + brands):
        return "similar"
    if any(prefix_match(board, key) or prefix_match(key, board) for key in keys):
        return "brand"
    return ""


def same_employer(board_name: str, legal_name: str) -> bool:
    """Whether a board's own employer name names this legal employer (or its known brand)."""
    return bool(name_match(board_name, legal_name))


def named_in_text(texts: list[str], legal_name: str) -> bool:
    """Whether one of these posting texts names the employer by its brand (a distinctive one only)."""
    core = _core(normalize_ie(legal_name).split())
    phrase = " ".join(core)
    if not phrase or (len(core) == 1 and (len(phrase) < 4 or phrase in STOP_BRANDS)):
        return False
    return any(f" {phrase} " in f" {_plain(text)} " for text in texts)


# ---- one probe: a board's postings (title, place, text) and its own employer name -------------

def _missing(error: str | None) -> bool:
    """No usable board: none by that name, or one its site's robots.txt does not let the app read."""
    return bool(error) and (error.startswith("HTTP 404") or error.startswith("the site's robots.txt"))


def probe(kind: str, slug: str, fetcher) -> dict | None:
    """{"ats", "token", "name", "postings": [{"title", "location", "text"}], "error"}; None when there is no board."""
    from backend.market.readers import ats
    from backend.services.portals import _ashby_location, _lever_text

    board = {"ats": kind, "token": slug, "name": "", "postings": [], "error": None}
    if kind == "greenhouse":
        data, error = fetcher.json(f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}/jobs")
        if not isinstance(data, dict):
            return None if _missing(error) or error is None else {**board, "error": error}
        board["postings"] = [{"title": str(j.get("title") or ""), "location": str((j.get("location") or {}).get("name") or ""),
                              "text": ""} for j in data.get("jobs") or [] if isinstance(j, dict)]
        if board["postings"]:
            about, _ = fetcher.json(f"https://boards-api.greenhouse.io/v1/boards/{quote(slug)}")
            board["name"] = " ".join(str((about or {}).get("name") or "").split()) if isinstance(about, dict) else ""
        return board
    if kind in ("lever", "lever_eu"):
        api = "api.eu.lever.co" if kind == "lever_eu" else "api.lever.co"
        data, error = fetcher.json(f"https://{api}/v0/postings/{quote(slug)}?mode=json")
        if not isinstance(data, list):
            return None if _missing(error) or error is None else {**board, "error": error}
        board["postings"] = [{"title": str(j.get("text") or ""),
                              "location": "; ".join(dict.fromkeys([str((j.get("categories") or {}).get("location") or "")]
                                                                  + [str(p) for p in (j.get("categories") or {}).get("allLocations") or []])),
                              "text": _lever_text(j)} for j in data if isinstance(j, dict)]
        return board
    if kind == "ashby":
        data, error = fetcher.json(f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}")
        if not isinstance(data, dict):
            return None if _missing(error) or error is None else {**board, "error": error}
        board["postings"] = [{"title": str(j.get("title") or ""), "location": _ashby_location(j),
                              "text": str(j.get("descriptionPlain") or "")} for j in data.get("jobs") or [] if isinstance(j, dict)]
        return board
    if kind == "smartrecruiters":
        data, error = fetcher.json(f"https://api.smartrecruiters.com/v1/companies/{quote(slug)}/postings?country=ie&limit=100")
        if not isinstance(data, dict):
            return None if _missing(error) or error is None else {**board, "error": error}
        content = [j for j in data.get("content") or [] if isinstance(j, dict)]
        if not content:
            return None
        names = [str((j.get("company") or {}).get("name") or "") for j in content]
        board["name"] = max(set(names), key=names.count)
        board["postings"] = [{"title": str(j.get("name") or ""),
                              "location": ", ".join(str((j.get("location") or {}).get(k) or "") for k in ("city", "region", "country")
                                                    if (j.get("location") or {}).get(k)) + ", Ireland", "text": ""}
                             for j in content]
        return board
    if kind == "workday":  # only from a link (host/site); Workday boards cannot be guessed from a name
        return _workday(slug, fetcher, board)
    if kind in ats.KINDS:
        found = ats.read_board({"ats": kind, "token": slug}, fetcher)
        if found["error"] and not found["postings"]:
            return None if _missing(found["error"]) or found["error"].startswith("Invalid") else {**board, "error": found["error"]}
        board.update(name=found["name"], postings=[{"title": p["title"], "location": p["location"], "text": p["description"]}
                                                   for p in found["postings"]])
        return board
    raise ValueError(f"Unknown applicant-tracking system {kind!r}")


def _workday(token: str, fetcher, board: dict) -> dict | None:
    """A Workday site ("host/site"): its Irish listing, and the employer named on its first postings."""
    from backend.services.job_sources import workday_detail

    host, _, site = token.partition("/")
    tenant = host.split(".")[0]
    data, error = fetcher.json(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                               {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "Ireland"})
    if not isinstance(data, dict):
        return None if _missing(error) or error is None else {**board, "error": error}
    names = []
    for item in [j for j in data.get("jobPostings") or [] if isinstance(j, dict)][:3]:
        full = workday_detail(host, tenant, site, str(item.get("externalPath") or ""), fetcher)
        if full:
            board["postings"].append({"title": full["title"], "location": full["location"], "text": full["description"]})
            names.append(full["company"])
    named = [n for n in names if n]
    board["name"] = max(set(named), key=named.count) if named else ""
    return board


def judge(board: dict, legal_name: str, location_ok: Callable[[str], bool]) -> dict:
    """Accept or refuse one board for this employer, with the reason either way."""
    irish = [p for p in board["postings"] if location_ok(p["location"])]
    result = {"ats": board["ats"], "token": board["token"], "board_name": board["name"], "postings": len(board["postings"]),
              "irish_postings": len(irish), "accepted": False, "reason": ""}
    if board.get("error"):
        return {**result, "reason": f"not read: {board['error']}"}
    if not irish:
        return {**result, "reason": "no current posting in Ireland"}
    if board["name"]:
        match = name_match(board["name"], legal_name)
        if match:
            return {**result, "accepted": True, "match": match, "reason": f"board name: {board['name']} ({match})"}
        return {**result, "reason": f"board name {board['name']!r} is another employer"}
    if named_in_text([p["text"] or p["title"] for p in irish], legal_name):
        return {**result, "accepted": True, "match": "text", "reason": "an Irish posting's own text names the employer"}
    return {**result, "reason": "the board states no employer name and no Irish posting names this employer"}


def resolve(legal_name: str, fetcher, *, location_ok: Callable[[str], bool] | None = None,
            kinds: tuple[str, ...] = GUESSED) -> dict:
    """The first accepted board for one DETE employer, or what was tried and refused."""
    if location_ok is None:
        from backend.countries import load_pack

        location_ok = load_pack("ie").location_ok
    tried, refused = [], []
    for number, slug in enumerate(slug_variants(legal_name)):
        for kind in kinds:
            if number >= VARIANTS_PER_KIND.get(kind, MAX_VARIANTS):
                continue
            if getattr(fetcher, "broken", None) and fetcher.broken(_host(kind, slug)):
                continue
            tried.append(f"{kind}:{slug}")
            board = probe(kind, slug, fetcher)
            if board is None:
                continue
            verdict = judge(board, legal_name, location_ok)
            if verdict["accepted"]:
                return {"status": "resolved", "legal_name": legal_name, **verdict, "tried": tried}
            refused.append(verdict)
    return {"status": "unresolved", "legal_name": legal_name, "tried": tried, "refused": refused}


def check_link(legal_name: str, careers_url: str, fetcher, *, location_ok: Callable[[str], bool] | None = None) -> dict:
    """Verify a careers link found another way (by hand or an AI web search) exactly as a guessed board."""
    from backend.services.job_sources import tracked_row

    try:
        row = tracked_row(legal_name, careers_url)
    except ValueError as error:
        return {"status": "unresolved", "legal_name": legal_name, "reason": str(error), "tried": [careers_url], "refused": []}
    kind, token = str(row.get("ats") or ""), str(row.get("token") or "")
    if kind == "workday":
        token = f"{row.get('host')}/{row.get('site')}"
    if kind not in (*ATS_ORDER, "workday") or not token:
        return {"status": "unresolved", "legal_name": legal_name, "tried": [careers_url], "refused": [],
                "reason": f"{kind or 'this'} careers pages cannot be read by this app"}
    if location_ok is None:
        from backend.countries import load_pack

        location_ok = load_pack("ie").location_ok
    board = probe(kind, token, fetcher)
    if board is None:
        return {"status": "unresolved", "legal_name": legal_name, "tried": [f"{kind}:{token}"], "refused": [],
                "reason": "no such board"}
    verdict = judge(board, legal_name, location_ok)
    return ({"status": "resolved", "legal_name": legal_name, **verdict, "tried": [f"{kind}:{token}"]} if verdict["accepted"]
            else {"status": "unresolved", "legal_name": legal_name, "tried": [f"{kind}:{token}"], "refused": [verdict]})


def _host(kind: str, slug: str) -> str:
    return {"greenhouse": "boards-api.greenhouse.io", "lever": "api.lever.co", "lever_eu": "api.eu.lever.co",
            "ashby": "api.ashbyhq.com", "smartrecruiters": "api.smartrecruiters.com", "workable": "apply.workable.com",
            "recruitee": f"{slug}.recruitee.com", "personio": f"{slug}.jobs.personio.de",
            "teamtailor": f"{slug}.teamtailor.com"}.get(kind, "")
