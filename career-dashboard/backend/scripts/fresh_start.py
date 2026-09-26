#!/usr/bin/env python3
"""Start the job search again from an empty list.

Every saved job and everything derived from it (search runs, agent runs,
drafts, scores, requirements, email evidence, employer checks, the AI cache)
is cleared. The profile, goals and settings stay. Nothing is lost: the database
is snapshotted with SQLite's backup API and the application folders are moved
into the profile's backup/<date>-fresh-start/ first, with a README saying what was there.

The profile's reapplication history outlives a fresh start: every cleared job's company,
role, status and dates are archived into reapply_history first, and the
excluded-postings log and the one-signature-project-per-company assignments
are kept. A new search can still consult the saved history.

Run from career-dashboard/:
    python backend/scripts/fresh_start.py --profile ID --yes
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path[:0] = [str(HERE.parents[2]), str(HERE.parent)]

from career import Workspace  # noqa: E402
from backend.services.workspace_v2 import CareerServices  # noqa: E402

# Children before parents, so foreign keys never block a delete.
JOB_TABLES = (
    "application_evidence", "chat_change_sets", "cover_letters", "instruction_messages",
    "job_requirements", "mail_evidence", "posting_checks", "posting_identities",
    "resume_assessments", "resume_scores", "search_jobs", "studio_captures",
    "studio_drafts", "studio_versions", "agent_run_events", "agent_runs", "search_runs",
    "company_checks", "jobs", "companies", "ai_cache", "ai_calls",
)

PROJECTIONS = ("jobs.json", "application-tracker.md", "applied-companies.md",
               "pipeline.md", "workspace-state.json", "activity.json")


def counts(db) -> dict:
    return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in JOB_TABLES
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone()}


def snapshot(root: Path, target: Path, before: dict) -> None:
    target.mkdir(parents=True, exist_ok=False)
    source = sqlite3.connect(root / "data/career.db")
    copy = sqlite3.connect(target / "career.db")
    with copy:
        source.backup(copy)
    copy.close()
    source.close()
    for name in PROJECTIONS:
        path = root / "data" / name
        if path.exists():
            shutil.copy2(path, target / name)
    applications = root / "data/output/applications"
    kept = sorted(p.name for p in applications.iterdir()) if applications.exists() else []
    if kept:
        shutil.move(str(applications), str(target / "applications"))
    applications.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {target.name}",
        "",
        "Snapshot taken before the job list was cleared for a new search.",
        "`career.db` is a consistent SQLite backup of the whole database at that",
        "moment; the projections beside it are the readable copies from the same",
        "time. `applications/` holds the prepared resume folders that were moved",
        "out of data/output/applications/.",
        "",
        "Rows that were cleared:",
        "",
        *(f"- {table}: {n}" for table, n in before.items() if n),
        "",
        f"Application folders moved: {len(kept)}",
        "",
        "To go back, stop the dashboard, copy `career.db` over data/career.db and",
        "move `applications/` back to data/output/applications/, then run",
        "`backend/scripts/workspace.py export`.",
        "",
        "Kept on purpose: reapply_history (every cleared company and role, for the",
        "never-re-apply rules), excluded_postings and signature_assignments.",
    ]
    (target / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def fresh_start(root: Path, today: str | None = None) -> dict:
    root = Path(root).resolve()
    today = today or date.today().isoformat()
    workspace = Workspace(root)
    services = CareerServices(workspace)
    target = root / "backup" / f"{today}-fresh-start"
    suffix = 2
    while target.exists():
        target = root / "backup" / f"{today}-fresh-start-{suffix}"
        suffix += 1
    with workspace.connect() as db:
        before = counts(db)
    snapshot(root, target, before)
    with workspace.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        stamp = services.now()
        db.execute(
            "INSERT INTO reapply_history(company, title, status, url, application_date, updated_at, archived_at) "
            "SELECT company, title, status, url, application_date, updated_at, ? FROM jobs",
            (stamp,),
        )
        db.execute("UPDATE signature_assignments SET job_id=NULL")
        for table in before:
            db.execute(f"DELETE FROM {table}")
        goals = services.pref("goals") or {}
        if goals:
            services.set_pref("goals", {**goals, "start_date": today}, db)
        workspace.record_event(db, "workspace_reset", backup=str(target.relative_to(root)),
                               cleared={k: v for k, v in before.items() if v}, goal_start=today)
    services.sync_projections()
    return {"backup": str(target), "cleared": {k: v for k, v in before.items() if v},
            "jobs_now": len(workspace.jobs()), "goal_start": today}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="actually do it; without this only the counts are shown")
    parser.add_argument("--root", type=Path, help="explicit profile workspace root")
    parser.add_argument("--profile", help="profile ID (default: last opened profile)")
    args = parser.parse_args()
    if args.root:
        root = args.root
    else:
        from backend.profiles import store
        profiles = store()
        profile_id = args.profile or profiles.last_used()
        if not profile_id:
            parser.error("No profile exists yet. Create one in the dashboard first.")
        root = profiles.root_for(profile_id)
    if not args.yes:
        with Workspace(root).connect() as db:
            print(json.dumps({"would_clear": {k: v for k, v in counts(db).items() if v}}, indent=2))
        print("Add --yes to back up and clear.", file=sys.stderr)
        return
    print(json.dumps(fresh_start(root), indent=2))


if __name__ == "__main__":
    main()
