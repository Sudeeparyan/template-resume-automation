#!/usr/bin/env python3
"""Manage daily searches in one selected local profile."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "career-dashboard"
sys.path[:0] = [str(APP / "backend" / "scripts"), str(APP)]
from career import Workspace  # noqa: E402
from backend.profiles import store  # noqa: E402
from backend.services.workspace_v2 import CareerServices  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="Local profile ID; defaults to the last opened profile")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    start = sub.add_parser("start")
    start.add_argument("--date")
    add = sub.add_parser("add")
    add.add_argument("--date")
    add.add_argument("--file", type=Path, required=True)
    link = sub.add_parser("link")
    link.add_argument("job_id")
    link.add_argument("--date")
    notes = sub.add_parser("notes")
    notes.add_argument("--date")
    notes.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    profiles = store()
    profile_id = args.profile or profiles.last_used()
    if not profile_id:
        parser.error("Create and open a profile in the dashboard, or pass --profile <id>.")
    entry = profiles.get(profile_id)
    if entry["state"] != "ready":
        parser.error("Build this profile's agents before managing its search history.")
    workspace = Workspace(profiles.root_for(profile_id))
    run_date = (args.date or workspace.today()) if args.command != "list" else None
    if args.command == "list":
        result = workspace.search_runs()
    elif args.command == "start":
        result = workspace.start_search(run_date)
    elif args.command == "link":
        result = workspace.track_search_job(args.job_id, run_date)
    elif args.command == "notes":
        result = workspace.update_search(run_date, args.file.read_text(encoding="utf-8"))
    else:
        workspace.start_search(run_date)  # Validate the run date before saving a posting.
        values = json.loads(args.file.read_text(encoding="utf-8"))
        job = CareerServices(workspace).add_posting(values)["job"]
        if job is None:
            parser.error("The posting did not pass this profile's eligibility and reapply checks.")
        result = workspace.track_search_job(job["id"], run_date)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
