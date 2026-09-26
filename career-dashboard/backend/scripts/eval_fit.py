#!/usr/bin/env python3
"""Read-only calibration of the requirement check (backend/services/fit.py).

Scores every saved job, every job she removed as not suitable, and the postings the
sponsorship gate excluded, then prints each one's score, must-haves met and whether it
would pass FIT_THRESHOLD. Nothing is written: no job_fit rows, no fit_score, no activity.

    python backend/scripts/eval_fit.py [--profile ID] [--ai] [--show 3]

By default only the rules path runs (no AI call at all). --ai also asks a free plan
(Kimi, Codex or Claude through Auto, never a paid key), one call per posting.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]
from career import Workspace  # noqa: E402
from backend.services import fit  # noqa: E402
from backend.services.workspace_v2 import CareerServices  # noqa: E402


def must_line(analysis: dict) -> str:
    must = [r for r in analysis["matrix"]["requirements"] if r["category"] == "required" and r["status"] != "unknown"]
    met = sum(r["status"] == "met" for r in must)
    return f"{met}/{len(must)}" if must else "-"


def row(label: str, posting: dict, analysis: dict, extra: str = "") -> str:
    passes = analysis["score"] >= fit.FIT_THRESHOLD and analysis["must_have_ok"] and not analysis["matrix"]["hard_blockers"]
    name = f"{posting.get('company', '')} - {posting.get('title', '')}"[:52]
    return (f"{label:<9} {name:<52} {analysis['method']:<5} {analysis['score']:>3}  must {must_line(analysis):>5}  "
            f"{'PASS' if passes else 'fail'}{extra}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", help="profile ID (default: last opened profile)")
    parser.add_argument("--ai", action="store_true", help="also check each posting on a free AI plan (never a paid key)")
    parser.add_argument("--show", type=int, default=0, help="print the full matrix for the first N postings")
    args = parser.parse_args()

    from backend.profiles import store

    profiles = store()
    profile_id = args.profile or profiles.last_used()
    if not profile_id:
        parser.error("No profile exists yet. Create one in the dashboard first.")
    services = CareerServices(Workspace(profiles.root_for(profile_id)))
    cat = fit.catalogue(services)
    team = fit.fit_team(services) if args.ai else None
    if args.ai and team is None:
        print("No free AI plan is ready, so only the rules path runs.")

    samples = [("saved", job) for job in services.w.jobs()]
    samples += [("removed", job) for job in services.w.jobs(include_deleted=True) if job.get("deleted_at")]
    with services.w.connect() as db:
        samples += [("excluded", dict(r)) for r in db.execute(
            "SELECT company, title, location, url, description FROM excluded_postings WHERE restored_at IS NULL "
            "ORDER BY excluded_at DESC LIMIT 25")]
    samples = [(label, p) for label, p in samples if str(p.get("description") or "").strip()]

    print(f"Threshold {fit.FIT_THRESHOLD}, must-haves at least {int(fit.MUST_HAVE_FLOOR * 100)}%, {fit.FIT_VERSION}. "
          f"{len(samples)} postings with a description.\n")
    tally: dict[str, list[int]] = {}
    for n, (label, posting) in enumerate(samples):
        rules = fit.analyse(services, posting, cat=cat)
        print(row(label, posting, rules))
        chosen = rules
        if team is not None:
            ai = fit.analyse(services, posting, team=team, cat=cat)
            print(row("  by AI", posting, ai, f"  ({ai['provider_label']})" if ai["method"] == "ai" else f"  (fell back: {ai['ai_error'][:60]})"))
            chosen = ai
        passed = chosen["score"] >= fit.FIT_THRESHOLD and chosen["must_have_ok"] and not chosen["matrix"]["hard_blockers"]
        tally.setdefault(label, [0, 0])[0 if passed else 1] += 1
        if n < args.show:
            for r in chosen["matrix"]["requirements"]:
                ids = ", ".join(r["evidence_ids"])
                print(f"      {r['category'][:4]:<5} {r['status']:<8} {r['text'][:60]}{'  <- ' + ids if ids else ''}")
            print()
    print("\nWould pass / fail:", ", ".join(f"{label} {p}/{f}" for label, (p, f) in tally.items()))
    print("Good fits you saved should mostly pass; roles you removed as unsuitable should mostly fail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
