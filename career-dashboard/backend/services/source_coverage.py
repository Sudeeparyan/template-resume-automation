"""Profile-local crawl checkpoints and verified employer discoveries.

Coverage describes the configured sources attempted, never every employer in a country.
Reads do not create a database; writes use short SQLite transactions so concurrent source
workers and a restarted hunt share durable progress without a shared in-memory cursor.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class Coverage:
    def __init__(self, root):
        self.path = Path(root) / "data" / "career.db"

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS source_checkpoints (key TEXT PRIMARY KEY, cursor TEXT NOT NULL, "
                   "state TEXT NOT NULL, checked_at TEXT NOT NULL, found INTEGER NOT NULL, error TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS discovered_employers (key TEXT PRIMARY KEY, market TEXT NOT NULL, "
                   "row_json TEXT NOT NULL, posting_url TEXT NOT NULL, verified_at TEXT NOT NULL)")
        return db

    def get(self, key):
        if not self.path.exists():
            return {}
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='source_checkpoints'").fetchone():
                return {}
            row = db.execute("SELECT * FROM source_checkpoints WHERE key=?", (key,)).fetchone()
        return {**dict(row), "cursor": json.loads(row["cursor"])} if row else {}

    def checkpoint(self, key, cursor=None, state="partial", *, found=0, error=""):
        if state not in {"partial", "complete", "failed", "blocked", "attempted"}:
            raise ValueError("Unknown source coverage state")
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO source_checkpoints VALUES (?,?,?,?,?,?)",
                       (str(key), json.dumps(cursor or {}), state, now(), int(found), str(error)[:500]))

    def order(self, rows, key=lambda row: row["id"]):
        """Unattempted first, then least recently attempted; stable when equally fresh."""
        checked = {row["key"]: row["checked_at"] for row in self.summary()["sources"]}
        return sorted(rows, key=lambda row: checked.get(key(row), ""))

    def summary(self):
        if not self.path.exists():
            return {"sources": [], "complete": 0, "partial": 0, "failed": 0,
                    "scope": "Configured sources only; coverage is not exhaustive."}
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='source_checkpoints'").fetchone():
                return {"sources": [], "complete": 0, "partial": 0, "failed": 0,
                        "scope": "Configured sources only; coverage is not exhaustive."}
            rows = [{**dict(r), "cursor": json.loads(r["cursor"])}
                    for r in db.execute("SELECT * FROM source_checkpoints ORDER BY checked_at DESC")]
        return {"sources": rows, **{s: sum(r["state"] == s for r in rows)
                                    for s in ("complete", "partial", "failed")},
                "scope": "Configured sources only; coverage is not exhaustive."}

    def register_employer(self, posting, *, verified=False):
        """Only a caller that verified the exact direct ATS posting may register its board."""
        if not verified or posting.get("market") not in {"ie", "us"}:
            return False
        from backend.services.job_sources import tracked_row, row_url
        from backend.countries import load_pack

        url = str(posting.get("url") or "")
        if not posting.get("description") or not load_pack(posting["market"]).location_ok(str(posting.get("location") or "")):
            return False
        try:
            row = tracked_row(str(posting.get("company") or ""), url)
        except ValueError:
            return False
        # Save the board root rather than a single role URL.
        row.pop("careers_url", None)
        row["careers_url"] = row_url(row)
        row.update(_market=posting["market"], _source="directory",
                   _vouched="Employer discovered through a verified direct ATS posting.")
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO discovered_employers VALUES (?,?,?,?,?)",
                       (posting["market"] + ":" + row["careers_url"], posting["market"],
                        json.dumps(row), url, now()))
        return True

    def employers(self, market):
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(self.path, timeout=30)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='discovered_employers'").fetchone():
                return []
            return [json.loads(r[0]) for r in db.execute(
                "SELECT row_json FROM discovered_employers WHERE market=? ORDER BY verified_at", (market,))]
