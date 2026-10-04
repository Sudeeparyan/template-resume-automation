#!/usr/bin/env python3
"""Measure the app's honesty checks and, optionally, an AI agent against them.

    python evals/run.py                   the checks themselves, no AI: every planted fabrication
                                          in evals/cases.yml must be caught and every honest change
                                          accepted
    python evals/run.py --live kimi_cli   also run the cover-letter writer with that local AI app on
                                          a synthetic demo profile, and record its answers in
                                          evals/cassettes/ (never a real profile)
    python evals/run.py --replay          the same AI measures from the recorded answers, offline

Run from career-dashboard/ with the backend Python. The report gives, per suite, the honest
changes accepted, the fabrications caught by kind and anything that slipped through, and the
occupation-list top-1 accuracy. Exit status 1 when a fabrication slips through, an honest change
is refused or an occupation is classified on the wrong list.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP / "backend/scripts")]

import yaml  # noqa: E402

CASES = APP / "evals" / "cases.yml"
CASSETTES = APP / "evals" / "cassettes"


def _score(name: str, results: list[tuple[dict, bool]]) -> dict:
    """results: (case, accepted). A 'pass' case must be accepted, a 'fail' case refused."""
    honest = [(case, ok) for case, ok in results if case["expect"] == "pass"]
    planted = [(case, ok) for case, ok in results if case["expect"] == "fail"]
    slipped = [case for case, ok in planted if ok]
    refused = [case for case, ok in honest if not ok]
    caught = Counter(case.get("kind", "?") for case, ok in planted if not ok)
    return {"suite": name, "honest_accepted": f"{len(honest) - len(refused)}/{len(honest)}",
            "fabrications_caught": f"{len(planted) - len(slipped)}/{len(planted)}", "caught_by_kind": dict(caught),
            "slipped": [case.get("kind") or case.get("text") for case in slipped],
            "refused_honest": [case.get("text") or case.get("append") or "base" for case in refused],
            "ok": not slipped and not refused}


def rewrite_suite(spec: dict) -> dict:
    from backend.services.rewrite_guard import check

    results = []
    for case in spec["cases"]:
        problems = check(spec["original"], case["text"], sources=case.get("sources", []), skills=spec.get("skills", []),
                         never=case.get("never", []), posting_terms=case.get("posting_terms", []))
        results.append((case, not problems))
    return _score("rewrite_guard", results)


def letter_suite(spec: dict) -> dict:
    from backend.services.cover_letters import check

    cat = {"entries": [{**entry, "terms": []} for entry in spec["evidence"]], "never": spec["never"], "hash": "eval"}
    matrix = {"requirements": [{"text": text, "category": "required", "status": "missing", "evidence_ids": []}
                               for text in spec["missing"]]}
    results = []
    for case in spec["cases"]:
        body = spec["base"]
        if case.get("replace"):
            body = body.replace(*case["replace"])
        if case.get("append"):
            body = body + " " + case["append"]
        draft = {"body": body, "evidence_ids": case.get("ids", spec["ids"]), "unsupported_claims": case.get("unsupported", [])}
        results.append((case, not check(draft, job=spec["job"], cat=cat, matrix=matrix, facts=spec["facts"])))
    return _score("letter_check", results)


def claim_suite(spec: dict) -> dict:
    from backend.graphs.dossier import check_claim

    today = date.fromisoformat(spec["today"])
    results = []
    for case in spec["cases"]:
        page = case["page"] if "page" in case else spec["page"]
        claim = {"facet": case["facet"], "quote": case["quote"], "published_at": case.get("published_at", "")}
        reason = check_claim(claim, page, None if page is not None else "HTTP 404", today=today)
        results.append((case, not reason))
    return _score("claim_check", results)


def occupation_suite(spec: dict) -> dict:
    """Occupation-list classification (permits/occupations.py): top-1 accuracy, and never a confident wrong list.

    'unknown' is the safe answer (the person confirms the duties); a case that expects a list entry and
    gets 'unknown' lowers the accuracy, while a confident answer on the wrong list or code fails the suite."""
    from backend.permits.occupations import classify

    today = date.fromisoformat(spec["today"])
    right, wrong, cautious = [], [], []
    for case in spec["cases"]:
        found = classify({"title": case["title"], "description": case["description"]}, on=today)
        got = (found["classification"], found["soc4"] if found["classification"] in {"critical", "ineligible"} else None)
        want = (case["expect"], str(case["soc4"]) if case.get("soc4") else None)
        if got == want:
            right.append(case)
        elif got[0] == "unknown":
            cautious.append(f"{case['title']}: expected {want[0]} {want[1] or ''}".strip())
        else:
            wrong.append(f"{case['title']}: expected {want[0]} {want[1] or ''}, got {got[0]} {got[1] or ''}".strip())
    return {"suite": "occupation_lists", "top1_accuracy": f"{len(right)}/{len(spec['cases'])}",
            "unknown_instead": cautious, "confidently_wrong": wrong, "ok": not wrong and not cautious}


# ---- the AI writer, live or replayed ------------------------------------------------------------

POSTINGS = [
    ("Example Analytics", "Graduate Data Analyst", "Dublin, Ireland",
     "Graduate Data Analyst. You will write SQL queries, clean data in Python and build reports for "
     "the operations team. A degree in computer science or a related field is required. Base salary EUR 38,000."),
    ("Sample Software", "Junior Software Engineer", "Cork, Ireland",
     "Junior Software Engineer. Build and test Python services, write SQL, use Git and review code with "
     "the team. A relevant master's degree is welcome. Salary EUR 40,000 per year."),
]


class Recorder:
    """A team wrapper that keeps every cover-letter answer, keyed by its request's hash."""

    def __init__(self, team, store: dict):
        self.team, self.store = team, store
        self.served = getattr(team, "served", None)

    def run(self, name, payload):
        answer = self.team.run(name, payload)
        self.served = getattr(self.team, "served", None)
        data = answer.model_dump() if hasattr(answer, "model_dump") else dict(answer)
        self.store.setdefault(_key(payload), []).append(data)
        return data


class Replayer:
    served = ("cassette", "recorded")

    def __init__(self, store: dict):
        self.store = {key: list(values) for key, values in store.items()}

    def run(self, name, payload):
        answers = self.store.get(_key(payload)) or []
        if not answers:
            raise RuntimeError("no recorded answer for this request")
        return answers.pop(0)


def _key(payload) -> str:
    stable = {k: v for k, v in payload.items() if k != "previous_attempt_problem"}
    attempt = "retry" if "previous_attempt_problem" in payload else "first"
    return attempt + ":" + hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:16]


def writer_suite(*, provider: str | None, replay: bool) -> dict:
    """Cover letters on a synthetic demo profile: how many AI drafts pass the checks, and why the rest fail."""
    from career import Workspace
    from make_demo_profile import make_demo, require_synthetic_demo

    from backend import paths
    from backend.services.workspace_v2 import CareerServices

    scratch = Path(tempfile.mkdtemp(prefix="career-eval-"))
    paths.MARKET_DB, paths.HTTP_CACHE_DB = scratch / "market.db", scratch / "http_cache.db"
    demo = make_demo(scratch / "profiles", as_of=date(2026, 10, 4))
    root = Path(demo["root"])
    require_synthetic_demo(root)
    services = CareerServices(Workspace(root))
    cassette = CASSETTES / "cover_letter_writer.json"
    if replay:
        team = Replayer(json.loads(cassette.read_text(encoding="utf-8")))
    else:
        from backend.ai import team_for

        services.set_pref("ai_preferences", {"default": {"provider": provider, "model": "kimi-runtime" if provider == "kimi_cli" else ""},
                                             "tiers": {tier: {"provider": provider} for tier in ("strong", "cheap")}})
        recorded: dict = {}
        team = Recorder(team_for(services), recorded)
    outcomes = []
    for company, title, location, text in POSTINGS:
        job = services.w.add_job(company, title, location, f"https://jobs.example/{company.split()[0].lower()}/1", text)
        try:
            letter = services.generate_cover_letter(job["id"], team=team)
            outcomes.append({"job": f"{title} at {company}", "method": letter["method"], "note": letter["note"]})
        except Exception as error:  # noqa: BLE001 - reported, not fatal
            outcomes.append({"job": f"{title} at {company}", "method": "error", "note": str(error)[:300]})
    if not replay:
        CASSETTES.mkdir(parents=True, exist_ok=True)
        cassette.write_text(json.dumps(recorded, indent=1, ensure_ascii=False), encoding="utf-8")
    accepted = sum(1 for o in outcomes if o["method"] == "ai")
    return {"suite": "cover_letter_writer" + (" (replayed)" if replay else f" ({provider})"),
            "ai_drafts_accepted": f"{accepted}/{len(outcomes)}", "outcomes": outcomes,
            "ok": all(o["method"] != "error" for o in outcomes)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", choices=["kimi_cli", "codex", "claude_code"], help="also run the AI writer with this app")
    parser.add_argument("--replay", action="store_true", help="also run the AI measures from evals/cassettes/")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    spec = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    reports = [rewrite_suite(spec["rewrite_guard"]), letter_suite(spec["letter_check"]), claim_suite(spec["claim_check"]),
               occupation_suite(spec["occupation_check"])]
    if args.live or args.replay:
        reports.append(writer_suite(provider=args.live, replay=args.replay and not args.live))
    if args.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        for report in reports:
            print(f"{'PASS' if report['ok'] else 'FAIL'}  {report['suite']}")
            for key, value in report.items():
                if key not in {"suite", "ok"} and value not in ([], {}, ""):
                    print(f"      {key}: {value}")
    return 0 if all(report["ok"] for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
