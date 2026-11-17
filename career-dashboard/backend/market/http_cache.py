"""A conditional-request cache: re-read a public page only when the site says it changed.

The fetcher sends a page's last ETag or Last-Modified back to the site; a "304 Not Modified"
answer reuses the stored body and costs the site almost nothing. Public pages only (no
cookies, credentials or POST bodies are ever cached); entries older than a week are dropped.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend import paths

MAX_AGE_DAYS = 7
_LOCK = threading.Lock()


class HttpCache:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else Path(paths.HTTP_CACHE_DB)
        self._purged = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS http_cache(url TEXT PRIMARY KEY, etag TEXT NOT NULL DEFAULT '', "
                   "last_modified TEXT NOT NULL DEFAULT '', body TEXT NOT NULL, fetched_at TEXT NOT NULL)")
        return db

    def get(self, url: str) -> dict | None:
        try:
            with closing(self._connect()) as db:
                row = db.execute("SELECT * FROM http_cache WHERE url=?", (url,)).fetchone()
        except sqlite3.Error:
            return None
        return dict(row) if row else None

    def put(self, url: str, *, etag: str, last_modified: str, body: str) -> None:
        if not (etag or last_modified):
            return
        now = datetime.now(timezone.utc)
        try:
            with _LOCK, closing(self._connect()) as db, db:
                db.execute("INSERT OR REPLACE INTO http_cache VALUES(?,?,?,?,?)",
                           (url, etag, last_modified, body, now.isoformat(timespec="seconds")))
                if not self._purged:
                    db.execute("DELETE FROM http_cache WHERE fetched_at<?",
                               ((now - timedelta(days=MAX_AGE_DAYS)).isoformat(timespec="seconds"),))
                    self._purged = True
        except sqlite3.Error:
            pass  # a cache that cannot be written only costs a full re-read next time
