#!/usr/bin/env python3
"""One-time recovery of the tracked job/activity projections into an empty database."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/scripts"))
sys.path.insert(0, str(ROOT))

from career import Workspace, posting_key  # noqa: E402


def main() -> int:
    workspace = Workspace(ROOT)
    jobs = json.loads((ROOT / "data/jobs.json").read_text(encoding="utf-8"))
    activity = json.loads((ROOT / "data/activity.json").read_text(encoding="utf-8"))
    with workspace.connect() as db:
        if db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] or db.execute("SELECT COUNT(*) FROM activity").fetchone()[0]:
            print("Refusing to import: the shared database is not empty.")
            return 2
        table_columns = [row[1] for row in db.execute("PRAGMA table_info(jobs)")]
        for job in jobs:
            row = dict(job)
            # The projection referenced old folders that are not present in this
            # clone. Keep the job/history and let a future prepare create a valid
            # folder instead of exposing a broken file route.
            folder = row.get("folder")
            if folder:
                candidate = ROOT / folder
                if not candidate.exists():
                    row["folder"] = None
            # Company records were not part of the projection. They are rebuilt
            # lazily by the current app; do not create unsupported metadata.
            row["company_id"] = None
            columns = [column for column in table_columns if column in row]
            placeholders = ",".join("?" for _ in columns)
            db.execute(
                f"INSERT INTO jobs({','.join(columns)}) VALUES({placeholders})",
                [row.get(column) for column in columns],
            )
            db.execute(
                "INSERT OR IGNORE INTO posting_identities(identity,job_id) VALUES(?,?)",
                (posting_key(row["url"]), row["id"]),
            )
        for event in sorted(activity, key=lambda item: int(item["id"])):
            db.execute(
                "INSERT INTO activity(id,occurred_at,action,job_id,details) VALUES(?,?,?,?,?)",
                (
                    event["id"],
                    event["occurred_at"],
                    event["action"],
                    event.get("job_id"),
                    json.dumps(event.get("details") or {}, ensure_ascii=False),
                ),
            )
        workspace.record_event(
            db,
            "legacy_projections_imported",
            jobs=len(jobs),
            activity_events=len(activity),
            missing_folders=sum(bool(job.get("folder")) for job in jobs),
        )
    workspace.export_tracking()
    print(f"Imported {len(jobs)} jobs and {len(activity)} activity events into {workspace.db_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
