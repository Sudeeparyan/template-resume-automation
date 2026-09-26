#!/usr/bin/env python3
"""The overnight hunt for one profile, started by hand or from a scheduled task.

    career-dashboard/backend/.venv/Scripts/python.exe daily-job-search/night_hunt.py \
        [--profile <id>] [--target 10] [--hours 8] [--min-fit 70] [--feeds-only] [--allow-paid] [--no-prepare]

It is the same engine as Daily Search -> Overnight hunt and the Assistant's "overnight hunt":
it keeps searching job boards, employer career feeds and the web until it has saved
--target jobs that fit at --min-fit or better, or --hours pass; it waits for free AI plans
to reset instead of failing (and never uses a paid AI unless --allow-paid); then it prepares
each job (company research, tailored resume, study plan, PDF) and writes HUNT-REPORT.md in
the profile's daily-job-search/<date>/ folder.

This script only starts the dashboard when needed, starts the hunt and waits for it. Left
out, a choice keeps the last hunt's value. Nothing is ever submitted and no one is contacted.
"""
import argparse
import sys
import time

import morning_run as shared  # the profile, the log and the dashboard API helpers

POLL_SECONDS = 30


def parse():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", help="Local profile ID; defaults to the last opened profile")
    parser.add_argument("--base-url", help="A test copy of the dashboard")
    parser.add_argument("--target", type=int, help="Jobs to save (1-40)")
    parser.add_argument("--hours", type=float, help="How long it may run (0.25-12)")
    parser.add_argument("--min-fit", type=int, help="Fit bar (50-95)")
    parser.add_argument("--feeds-only", action="store_true", help="No AI web searching (AI still checks requirements)")
    parser.add_argument("--allow-paid", action="store_true", help="Use a paid AI when every free plan is resting")
    parser.add_argument("--no-prepare", action="store_true", help="Only find and save jobs")
    return parser.parse_args()


def main():
    args = parse()
    shared.log(f"=== Overnight hunt for profile {shared.PROFILE} ===")
    shared.ensure_server()
    body = {"target": args.target, "hours": args.hours, "min_fit": args.min_fit}
    if args.feeds_only:
        body["sources"] = "feeds"
    if args.allow_paid:
        body["allow_paid"] = True
    if args.no_prepare:
        body["steps"] = {"research": False, "tailor": False, "study_plan": False, "pdf": False}
    body = {k: v for k, v in body.items() if v is not None}
    status = shared.api("GET", "/api/v2/hunt/status")
    run = status.get("current")
    if run:
        shared.log("A hunt is already running; following it instead of starting another.")
    else:
        run = shared.api("POST", "/api/v2/hunt/run", body)
        config = run["config"]
        shared.log(f"Started: {config['target']} job(s) at fit {config['min_fit']}+ within {config['hours']} h "
                   f"({config['sources']}; paid AI {'allowed' if config['allow_paid'] else 'never'}).")
    stage, saved = "", 0
    deadline = time.monotonic() + (float(run["config"]["hours"]) + 1.5) * 3600
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        try:
            status = shared.api("GET", "/api/v2/hunt/status")
        except shared.ApiError as error:
            shared.log(f"Could not read the hunt's progress ({error}); retrying.")
            continue
        current = status.get("current")
        if current and current["id"] == run["id"]:
            progress = current.get("progress") or {}
            if progress.get("stage") and progress["stage"] != stage:
                stage = progress["stage"]
                shared.log("  " + stage)
            if len(progress.get("saved") or []) != saved:
                for job in (progress.get("saved") or [])[saved:]:
                    shared.log(f"  Saved: {job['company']} - {job['title']} (fit {job.get('fit')})")
                saved = len(progress.get("saved") or [])
            continue
        last = status.get("last") or {}
        if last.get("id") == run["id"]:
            progress = last.get("progress") or {}
            shared.log(f"=== Hunt {last['state']}: {len(progress.get('saved') or [])} of {last['config']['target']} saved"
                       + (f" ({last['error']})" if last.get("error") else "") + " ===")
            if progress.get("report"):
                shared.log("Report: " + str(shared.WORKSPACE_ROOT / progress["report"]))
            return 0 if last["state"] == "completed" else 1
    shared.log("The hunt did not report an end in time; open Daily Search to see where it stands.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as error:  # never leave a silent scheduled task
        shared.log("Overnight hunt stopped unexpectedly: " + repr(error))
        sys.exit(1)
