"""Spans in local SQLite files, and the reads the Agents tab and ``career trace`` need.

A profile's spans go to profiles/<id>/data/traces.db; work outside a profile goes to
data/traces/app.db. Spans older than RETENTION_DAYS are removed as new ones arrive.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from backend.paths import DATA

try:
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
except ImportError:  # pragma: no cover - tracing is off without the packages
    SpanExporter = object  # type: ignore[assignment,misc]
    SpanExportResult = None  # type: ignore[assignment]

APP_DB = DATA / "traces" / "app.db"
RETENTION_DAYS = 14
SCHEMA = """
CREATE TABLE IF NOT EXISTS spans(
    trace_id TEXT NOT NULL, span_id TEXT NOT NULL, parent_span_id TEXT, name TEXT NOT NULL, kind TEXT,
    start_ns INTEGER NOT NULL, end_ns INTEGER, status TEXT, status_message TEXT,
    attributes TEXT NOT NULL, events TEXT NOT NULL, run_id TEXT,
    PRIMARY KEY(trace_id, span_id));
CREATE INDEX IF NOT EXISTS ix_spans_run ON spans(run_id, start_ns);
CREATE INDEX IF NOT EXISTS ix_spans_start ON spans(start_ns);
"""


def db_for(profile_root=None) -> Path:
    return Path(profile_root) / "data" / "traces.db" if profile_root else APP_DB


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def _hex(value: int | None, width: int) -> str | None:
    return format(value, f"0{width}x") if value else None


def _row(span, attributes: dict) -> tuple:
    run_id = attributes.get("career.run_id")
    events = [
        {"name": e.name, "time_ns": e.timestamp, "attributes": dict(e.attributes or {})}
        for e in span.events
    ]
    return (
        _hex(span.context.trace_id, 32),
        _hex(span.context.span_id, 16),
        _hex(span.parent.span_id if span.parent else None, 16),
        span.name,
        span.kind.name if span.kind is not None else None,
        span.start_time,
        span.end_time,
        span.status.status_code.name,
        span.status.description or "",
        json.dumps(attributes, default=str),
        json.dumps(events, default=str),
        str(run_id) if run_id not in (None, "") else None,
    )


class SQLiteSpanExporter(SpanExporter):
    """Writes each span to its profile's traces.db (or the app's own file)."""

    def __init__(
        self, app_db: Path | None = None, retention_days: int = RETENTION_DAYS
    ):
        self.app_db = Path(app_db) if app_db else APP_DB
        self.retention_days = retention_days

    def export(self, spans):
        groups: dict[Path, list] = defaultdict(list)
        for span in spans:
            attributes = dict(span.attributes or {})
            root = attributes.pop("career.profile_root", None)
            if root and not Path(root).is_dir():
                continue  # the profile was deleted: never recreate its folder for a trace
            groups[Path(root) / "data" / "traces.db" if root else self.app_db].append(
                _row(span, attributes)
            )
        cutoff = time.time_ns() - self.retention_days * 86400 * 10**9
        for path, rows in groups.items():
            try:
                with closing(_connect(path)) as db, db:
                    db.executemany(
                        "INSERT OR REPLACE INTO spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        rows,
                    )
                    db.execute("DELETE FROM spans WHERE start_ns < ?", (cutoff,))
            except (sqlite3.Error, OSError):
                continue  # tracing never breaks the work it describes
        return SpanExportResult.SUCCESS if SpanExportResult else None

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _public(row: sqlite3.Row) -> dict:
    start, end = row["start_ns"], row["end_ns"]
    return {
        "trace_id": row["trace_id"],
        "span_id": row["span_id"],
        "parent_span_id": row["parent_span_id"],
        "name": row["name"],
        "kind": row["kind"],
        "start_ns": start,
        "end_ns": end,
        "duration_ms": round((end - start) / 1e6, 1) if end else None,
        "status": row["status"],
        "status_message": row["status_message"],
        "attributes": json.loads(row["attributes"] or "{}"),
        "events": json.loads(row["events"] or "[]"),
    }


def _read(path: Path, sql: str, params: tuple) -> list[sqlite3.Row]:
    if not path.is_file():
        return []
    try:
        with closing(sqlite3.connect(path, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            return db.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def run_spans(profile_root, run_id) -> list[dict]:
    """Every span of one run (all its traces), oldest first."""
    # A parent ends after its children, so it comes first when clock ticks make their starts equal.
    rows = _read(
        db_for(profile_root),
        "SELECT * FROM spans WHERE run_id=? ORDER BY start_ns, end_ns DESC",
        (str(run_id),),
    )
    return [_public(row) for row in rows]


def trace_spans(profile_root, trace_id: str) -> list[dict]:
    rows = _read(
        db_for(profile_root),
        "SELECT * FROM spans WHERE trace_id=? ORDER BY start_ns, end_ns DESC",
        (trace_id,),
    )
    return [_public(row) for row in rows]


def recent_runs(profile_root, limit: int = 20) -> list[dict]:
    """The latest traced runs: when, how long, how many AI and web calls, and how many failed."""
    rows = _read(
        db_for(profile_root),
        """
        SELECT run_id, MIN(start_ns) AS started, MAX(end_ns) AS ended, COUNT(*) AS spans,
               SUM(status = 'ERROR') AS errors,
               SUM(name LIKE 'chat %') AS ai_calls,
               SUM(name LIKE 'GET %' OR name LIKE 'POST %') AS http_calls,
               MAX(json_extract(attributes, '$."career.run_kind"')) AS kind
        FROM spans WHERE run_id IS NOT NULL GROUP BY run_id ORDER BY started DESC LIMIT ?""",
        (int(limit),),
    )
    return [
        {
            "run_id": r["run_id"],
            "kind": r["kind"],
            "started_ns": r["started"],
            "ended_ns": r["ended"],
            "duration_ms": (
                round((r["ended"] - r["started"]) / 1e6, 1) if r["ended"] else None
            ),
            "spans": r["spans"],
            "errors": r["errors"] or 0,
            "ai_calls": r["ai_calls"] or 0,
            "http_calls": r["http_calls"] or 0,
        }
        for r in rows
    ]
