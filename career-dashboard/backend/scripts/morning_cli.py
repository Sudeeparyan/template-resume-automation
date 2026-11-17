#!/usr/bin/env python3
"""Morning jobs for one profile, from chat: the dashboard's Settings switch as a command.

  career morning --profile <id>                       show the switch, the ready-by time and the job count
  career morning --profile <id> --on --ready-by 08:00 --jobs 3
                                                      switch it on: the PC's "Career Morning Jobs" task
                                                      searches overnight and the list is ready by 08:00
  career morning --profile <id> --off                 switch it off for this profile

--jobs and --min-fit are the overnight hunt's own settings (Daily Search -> Overnight hunt).
The task is a Windows scheduled task; elsewhere the reply says which command an AI app's
scheduler should run instead. It prints JSON and never starts a search by itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(APP), str(APP / "backend/scripts")]


def clock(value: str) -> str:
    """08:00 from '08:00' or '8:00'; argparse reports anything else."""
    try:
        return datetime.strptime(value.strip(), "%H:%M").strftime("%H:%M")
    except ValueError:
        raise argparse.ArgumentTypeError("use a 24-hour time such as 08:00") from None


def main(argv=None, *, profiles=None) -> int:
    parser = argparse.ArgumentParser(prog="career morning", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True, help="exact profile ID (see career doctor)")
    switch = parser.add_mutually_exclusive_group()
    switch.add_argument("--on", action="store_true", help="switch Morning jobs on for this profile")
    switch.add_argument("--off", action="store_true", help="switch Morning jobs off for this profile")
    parser.add_argument("--ready-by", type=clock, help="local time the list is ready, such as 08:00")
    parser.add_argument("--jobs", type=int, help="how many new jobs the overnight hunt prepares (1-40)")
    parser.add_argument("--min-fit", type=int, help="the lowest fit score a job may have (50-95)")
    args = parser.parse_args(argv)

    from backend.profiles import ProfileError, store
    from backend.services import schedule_tasks
    from backend.services.hunt import DEFAULTS, MAX_TARGET

    if args.jobs is not None and not 1 <= args.jobs <= MAX_TARGET:
        parser.error(f"--jobs must be between 1 and {MAX_TARGET}")
    if args.min_fit is not None and not 50 <= args.min_fit <= 95:
        parser.error("--min-fit must be between 50 and 95")
    profiles = profiles or store()
    hunt = {key: value for key, value in (("target", args.jobs), ("min_fit", args.min_fit)) if value is not None}
    changing = bool(hunt or args.on or args.off or args.ready_by)
    try:
        profile = profiles.get(args.profile)
        ready = profile.get("state") == "ready"
        if changing and not ready:
            raise ProfileError("Build this profile first (career setup); its morning jobs start after that.")
        stored = {}
        if ready:
            from career import Workspace
            from backend.services.workspace_v2 import CareerServices

            services = CareerServices(Workspace(profiles.root_for(args.profile)))
            stored = services.pref("hunt_preferences", {}) or {}
            if hunt:
                stored = {**stored, **hunt}
                services.set_pref("hunt_preferences", stored)
        if args.on or args.off or args.ready_by:
            enabled = args.on or (not args.off and bool((profile.get("schedule") or {}).get("enabled")))
            status = schedule_tasks.set_profile(profiles, args.profile, enabled=enabled, ready_by=args.ready_by)
        else:
            status = schedule_tasks.profile_status(profiles, args.profile)
    except (ProfileError, ValueError) as error:
        print(json.dumps({"ok": False, "profile": args.profile, "error": str(error)}, indent=2))
        return 2
    list_path = profiles.root_for(args.profile) / "daily-job-search" / "MORNING-JOBS.md"
    result = {
        "ok": True, "profile": args.profile,
        "morning_jobs": status,
        "jobs_per_morning": stored.get("target", DEFAULTS["target"]),
        "min_fit": stored.get("min_fit", DEFAULTS["min_fit"]),
        "list": str(list_path),
    }
    if status.get("supported") is False:
        result["next"] = ("No Windows task here. Schedule `bash daily-job-search/morning-jobs.command --background "
                          f"--profile {args.profile}` in the evening with an AI app's or the OS's scheduler.")
    elif status.get("enabled") and not status.get("exists"):
        result["next"] = "The switch is on but Task Scheduler has no task: read morning_jobs.note and fix it."
    elif status.get("enabled"):
        result["next"] = (f"The search starts at {status['night_start']} and the list is ready by "
                          f"{status['ready_by']}. Keep the PC on or asleep (not shut down) and plugged in.")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
