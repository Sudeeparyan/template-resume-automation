#!/usr/bin/env python3
"""Build countries/ie/employer-registry.csv: careers boards of employers in DETE's permit data.

A maintainer runs this when DETE publishes new figures (the importer refreshes the DETE CSV):

    backend/.venv/Scripts/python.exe backend/scripts/build_employer_registry.py --top 400

1. Candidates: the employers with the most permits in the last 24 complete months (one per
   matching key), leaving out those already in countries/ie/employers.yml.
2. market/resolver.py tries a few board names on each applicant-tracking system's public API
   and accepts a board only when it lists a current Irish posting and names the same employer.
3. Optional ``--ai kimi_cli`` (or claude_code, codex): for the largest employers still unresolved,
   a local AI app's web search suggests each one's job board. Every suggestion is then checked
   exactly like a guessed board (or, for a careers page, the one board it links to); the AI
   never adds a row by itself. ``--links FILE`` checks hand-found links (CSV: legal_name,url).

Writes the registry CSV and its .meta.yml (public data, committed), and a review file of what
was tried and refused (data/market/registry-review.json, not committed). Requests go through
the app's polite fetcher: robots.txt, one request a second per host, backing off on errors.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import yaml  # noqa: E402

from backend import paths  # noqa: E402
from backend.market import registry, resolver  # noqa: E402
from backend.permits import employer_names  # noqa: E402
from backend.permits.history import DETE_CSV, decode_monthly, window_for  # noqa: E402

REVIEW = paths.MARKET / "registry-review.json"
AI_BATCH = 8
# Employers whose vacancies are on public-sector or care-sector sites, not an applicant-tracking
# system: not sent to the AI step (they stay unresolved, which the review file records).
NOT_ATS = ("hospital", "nursing home", "university", "health service", "hse", "council", "college", "home care",
           "homecare", "care centre", "carechoice", "school", "hospice")


def candidates(top: int, on: date) -> list[dict]:
    """The ``top`` employers by permits in the 24 complete months before ``on``, one per matching key."""
    start, end = window_for(on)
    months = {f"{y}-{m:02d}" for y in range(start.year, end.year + 1) for m in range(1, 13)
              if start <= date(y, m, 1) <= end}
    by_name: dict[str, int] = defaultdict(int)
    with DETE_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            for month, count in decode_monthly(row["monthly_permits"], int(row["year"])).items():
                if month in months:
                    by_name[row["employer"]] += count
    groups: dict[str, dict] = {}
    for name, count in by_name.items():
        key = (employer_names.keys_for(name) or [name.casefold()])[0]
        group = groups.setdefault(key, {"key": key, "legal_name": name, "best": 0, "permits_24m": 0})
        group["permits_24m"] += count
        if count > group["best"]:
            group.update(legal_name=name, best=count)
    ranked = sorted(groups.values(), key=lambda g: (-g["permits_24m"], g["key"]))
    return [{k: g[k] for k in ("key", "legal_name", "permits_24m")} for g in ranked[:top]]


def directory_keys() -> set[str]:
    """Matching keys of employers the directory already reads (and the legal names their aliases name)."""
    data = yaml.safe_load((paths.COUNTRIES / "ie" / "employers.yml").read_text(encoding="utf-8")) or {}
    keys = set()
    for row in data.get("employers") or []:
        key = employer_names.normalize_ie(str(row.get("name") or ""))
        if key:
            keys.add(key)
            keys.update(employer_names.aliases().get(key, []))
    return keys


def covered(key: str, directory: set[str]) -> bool:
    return key in directory or any(employer_names.prefix_match(brand, key) for brand in directory)


_BOARD_TITLE = re.compile(r"(?i)^(?:careers?|jobs?|vacancies)\s+(?:at|with)\s+|\s+(?:careers?|jobs?|job board|vacancies)$")


def display_name(legal_name: str, board_name: str, accepted_by_board_name: bool) -> str:
    """The board's own name when it matched, else the legal name; either without a Workday company
    code, board words, legal form or country ("Kaseya Careers" -> "Kaseya", "3100 Accenture Limited
    Company" -> "Accenture")."""
    name = " ".join(board_name.split()) if accepted_by_board_name and board_name else legal_name
    name = _BOARD_TITLE.sub("", resolver._COMPANY_CODE.sub("", name)).strip() or name
    words = employer_names._TRADING.split(f" {name} ")[0].split()
    while len(words) > 1 and employer_names._plain(words[-1]) in (employer_names.LEGAL | employer_names.TRAILING_PLACE | {""}):
        words.pop()
    return " ".join(words)


def registry_row(found: dict, candidate: dict, on: date) -> dict:
    kind, token = found["ats"], found["token"]
    host = site = ""
    if kind == "workday":
        host, _, site = token.partition("/")
        token = ""
    by_name = found["reason"].startswith("board name:")
    return {"employer": display_name(candidate["legal_name"], found.get("board_name", ""), by_name),
            "legal_name": candidate["legal_name"], "ats": kind, "token": token, "host": host, "site": site,
            "permits_24m": candidate["permits_24m"], "irish_postings": found["irish_postings"],
            "name_check": found["reason"], "verified_on": on.isoformat()}


def ai_links(provider: str, names: list[str]) -> dict[str, str]:
    """{legal name: job-board link} suggested by a local AI app's web search (checked afterwards)."""
    from importlib import import_module

    module = import_module(f"backend.ai.{provider}")
    schema = {"type": "object", "additionalProperties": False, "required": ["employers"], "properties": {"employers": {
        "type": "array", "items": {"type": "object", "additionalProperties": False,
                                   "required": ["legal_name", "job_board_url", "source_url"],
                                   "properties": {"legal_name": {"type": "string"}, "job_board_url": {"type": "string"},
                                                  "source_url": {"type": "string"}}}}}}
    prompt = ("These employers hold Irish employment permits (DETE's public records). For each one, find the web address "
              "of the job board where it publishes its current vacancies. Prefer its applicant-tracking-system board: "
              "boards.greenhouse.io/<name>, jobs.lever.co/<name>, jobs.ashbyhq.com/<name>, <name>.wd<N>.myworkdayjobs.com/"
              "<site>, jobs.smartrecruiters.com/<name>, apply.workable.com/<name>, <name>.recruitee.com, "
              "<name>.jobs.personio.de or <name>.teamtailor.com; otherwise its own careers page. Only give an address "
              "you opened yourself and that belongs to this employer (Irish entity or its group); leave job_board_url "
              "empty when you cannot find one. Never guess an address. source_url is the page where you found it.\n\n"
              + "\n".join(f"- {name}" for name in names))
    module_options = {"kimi_cli": {"model": "kimi-runtime"}, "claude_code": {"model": "sonnet"},
                      "codex": {"model": "codex-runtime"}}.get(provider, {})
    answer = module.invoke(prompt, schema, web=True, **module_options)
    out = {}
    for item in (answer or {}).get("employers") or []:
        name, link = str(item.get("legal_name") or "").strip(), str(item.get("job_board_url") or "").strip()
        if name in names and link.startswith("https://"):
            out[name] = link
    return out


def check_suggested(legal_name: str, link: str, fetcher) -> dict:
    """A suggested link checked like a guessed board; a careers page is followed to the one board it links to."""
    from backend.services.lead_resolver import board_links

    result = resolver.check_link(legal_name, link, fetcher)
    if result["status"] == "resolved" or result.get("refused"):
        return result
    page, final, _ = fetcher.page(link)
    boards = board_links(page or "", final)
    if len(boards) == 1:
        followed = resolver.check_link(legal_name, boards[0], fetcher)
        followed["via"] = link
        return followed
    return {**result, "reason": result.get("reason", "") + (f"; the page links to {len(boards)} boards" if boards else "")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=400, help="how many of the largest permit employers to try")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--only", action="append", default=[], help="try only this legal name (repeatable)")
    parser.add_argument("--ai", choices=["kimi_cli", "claude_code", "codex"], help="ask this local AI app for unresolved boards")
    parser.add_argument("--ai-limit", type=int, default=48, help="how many unresolved employers to ask the AI about")
    parser.add_argument("--links", type=Path, help="CSV of legal_name,url found by hand, checked the same way")
    parser.add_argument("--keep", action="store_true", help="keep existing registry rows not re-found this run")
    parser.add_argument("--output", type=Path, default=registry.path_for("ie"))
    args = parser.parse_args(argv)

    from backend.services.job_sources import Fetcher

    on = date.today()
    directory = directory_keys()
    pool_size = max(args.top, 1)
    chosen = [c for c in candidates(pool_size if not args.only else 20000, on)
              if (not args.only or c["legal_name"] in args.only) and not covered(c["key"], directory)]
    fetcher = Fetcher(min_interval=1.0)
    started = time.monotonic()
    results: dict[str, dict] = {}

    def work(candidate):
        return candidate, resolver.resolve(candidate["legal_name"], fetcher)

    print(f"Trying {len(chosen)} employers on {len(resolver.GUESSED)} applicant-tracking systems...", file=sys.stderr, flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for done, (candidate, found) in enumerate(executor.map(work, chosen), 1):
            results[candidate["legal_name"]] = {**found, "permits_24m": candidate["permits_24m"]}
            if found["status"] == "resolved":
                print(f"  + {candidate['legal_name']}: {found['ats']}:{found['token']} ({found['reason']})", file=sys.stderr, flush=True)
            if done % 25 == 0:
                print(f"  {done}/{len(chosen)} tried, {time.monotonic() - started:.0f}s", file=sys.stderr, flush=True)

    by_name = {c["legal_name"]: c for c in chosen}
    suggestions: dict[str, str] = {}
    if args.links:
        with args.links.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("legal_name") in by_name and str(row.get("url") or "").startswith("https://"):
                    suggestions[row["legal_name"]] = row["url"].strip()
    if args.ai:
        unresolved = [name for name, found in sorted(results.items(), key=lambda kv: -kv[1]["permits_24m"])
                      if found["status"] != "resolved" and name not in suggestions
                      and not any(word in name.casefold() for word in NOT_ATS)][:args.ai_limit]
        for offset in range(0, len(unresolved), AI_BATCH):
            batch = unresolved[offset:offset + AI_BATCH]
            print(f"  asking {args.ai} about {len(batch)} employers ({offset + len(batch)}/{len(unresolved)})...", file=sys.stderr, flush=True)
            try:
                suggestions.update({name: link for name, link in ai_links(args.ai, batch).items() if name not in suggestions})
            except Exception as error:  # noqa: BLE001 - a usage limit or a CLI failure ends the AI step, not the build
                print(f"  the AI step stopped: {error}", file=sys.stderr, flush=True)
                break
    for name, link in suggestions.items():
        found = check_suggested(name, link, fetcher)
        found["suggested"] = link
        if found["status"] == "resolved":
            print(f"  + {name}: {found['ats']}:{found['token']} (suggested link; {found['reason']})", file=sys.stderr, flush=True)
            results[name] = {**found, "permits_24m": by_name[name]["permits_24m"]}
        else:
            results.setdefault(name, {"status": "unresolved", "permits_24m": by_name[name]["permits_24m"]})["suggested"] = link
            results[name]["suggested_check"] = found

    rows = [registry_row(found, by_name[name], on) for name, found in results.items() if found["status"] == "resolved"]
    if args.keep:
        existing = registry.load(args.output)
        found_names = {row["legal_name"] for row in rows}
        rows += [row for row in existing if row["legal_name"] not in found_names]
    rows = list({(r["ats"], r["token"], r["host"], r["site"]): r for r in rows}.values())
    rows.sort(key=lambda r: (-int(r["permits_24m"] or 0), r["employer"].casefold()))
    registry.write(rows, args.output)
    start, end = window_for(on)
    meta = {"source": "Careers boards of employers in DETE's employment permit records",
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dete_csv_sha256": hashlib.sha256(DETE_CSV.read_bytes()).hexdigest(),
            "permit_window": f"{start.isoformat()}..{end.isoformat()}", "candidates": len(chosen), "rows": len(rows),
            "boards_by_ats": dict(Counter(r["ats"] for r in rows).most_common()),
            "acceptance": ("At least one current posting in Ireland, and the board names the same employer: its own "
                           "name equals the legal name (exact), a curated alias (alias), is at least 85% similar "
                           "(similar), or is the brand the legal name extends with descriptor words (brand: reviewed "
                           "by hand before committing); a board that states no name needs an Irish posting whose own "
                           "text names the employer (text). AI suggestions are checked the same way and never "
                           "accepted unchecked."),
            "csv_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
    args.output.with_suffix(".meta.yml").write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8")
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(json.dumps({"built_at": meta["built_at"], "results": results}, indent=1, default=str), encoding="utf-8")
    summary = {"candidates": len(chosen), "resolved": len(rows), "by_ats": meta["boards_by_ats"],
               "requests": fetcher.requests, "seconds": round(time.monotonic() - started), "output": str(args.output),
               "review": str(REVIEW)}
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
