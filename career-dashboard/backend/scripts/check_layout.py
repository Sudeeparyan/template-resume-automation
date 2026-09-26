#!/usr/bin/env python3
"""Verify shared tracking, the mirrored layout (career-dashboard, daily-job-search, backup) and projections."""
import hashlib
import json
import sys
from pathlib import Path
from career import Workspace

ROOT = Path(__file__).resolve().parents[3]


def main():
    failures = []
    warnings = []
    for name in ["daily-job-search", "career-dashboard", "backup"]:
        if not (ROOT / name).is_dir():
            failures.append(f"Missing {name}")
    # Retired active folders must not reappear at the workspace root.
    for name in ["system", "dashboard", "output", "context"]:
        if (ROOT / name).exists():
            failures.append(f"Retired folder remains active: {name}")
    w = Workspace(ROOT / "career-dashboard")
    with w.connect() as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            failures.append("Database integrity check failed")
        if list(db.execute("PRAGMA foreign_key_check")):
            failures.append("Broken database relationships")
    packs = w.root / "data/historical-packs.json"
    historical = json.loads(packs.read_text(encoding="utf-8")) if packs.exists() else []
    for item in historical:
        source = ROOT / item["folder"] / "resume.tex"
        if not source.exists():
            # This checkout includes the index but not every archived PDF/TeX pack.
            # Report that coverage gap without pretending a present file is valid.
            warnings.append("Historical source not present in this checkout: " + item["folder"])
        elif hashlib.sha256(source.read_bytes()).hexdigest() != item["source_sha256"]:
            failures.append("Historical source changed: " + item["folder"])
    for job in w.jobs():
        if job["folder"] and not (w.root / job["folder"]).is_dir():
            failures.append("Missing draft: " + job["id"])
    for run in w.search_runs():
        target = ROOT / "daily-job-search" / run["date"] / "run.json"
        if not target.exists() or json.loads(target.read_text(encoding="utf-8")) != run:
            failures.append("Stale search projection: " + run["date"])
    for name, expected in [
        ("jobs.json", w.jobs()),
        ("activity.json", w.activity(limit=-1)),
    ]:
        target = w.root / "data" / name
        if not target.exists() or json.loads(target.read_text(encoding="utf-8")) != expected:
            failures.append("Stale tracking projection: " + name)
    print(
        json.dumps(
            {
                "status": "FAIL" if failures else "PASS",
                "historical_packs": len(historical),
                "jobs": len(w.jobs()),
                "runs": len(w.search_runs()),
                "failures": failures,
                "warnings": warnings,
            },
            indent=2,
        )
    )
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
