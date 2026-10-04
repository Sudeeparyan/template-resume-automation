"""One SQLite checkpoint store per profile: ``profiles/<id>/data/agents.db``.

A checkpoint is written after every step (``durability="sync"``), so a run stopped by a restart
resumes from its last finished node instead of repeating it. Deserialization is strict: only
LangGraph's safe built-in types are rebuilt from the database, never arbitrary Python objects.
Checkpoints older than ``RETENTION_DAYS`` are deleted.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver

RETENTION_DAYS = 30
_SAVERS: dict[str, SqliteSaver] = {}
_LOCK = threading.Lock()


def path_for(root) -> Path:
    return Path(root) / "data" / "agents.db"


def saver(root) -> SqliteSaver:
    """The profile's checkpoint store (one connection per database, shared by its runs)."""
    path = path_for(root)
    key = str(path.resolve())
    with _LOCK:
        if key not in _SAVERS:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=30000")
            store = SqliteSaver(connection, serde=JsonPlusSerializer(allowed_msgpack_modules=None))
            store.setup()
            _SAVERS[key] = store
        return _SAVERS[key]


def close(root) -> None:
    """Close a profile's store (tests, and before a profile folder is removed)."""
    key = str(path_for(root).resolve())
    with _LOCK:
        store = _SAVERS.pop(key, None)
    if store is not None:
        store.conn.close()


def threads(root) -> list[str]:
    store = saver(root)
    with store.cursor(transaction=False) as cursor:
        cursor.execute("SELECT DISTINCT thread_id FROM checkpoints")
        return [row[0] for row in cursor.fetchall()]


def prune_old(root, *, days: int = RETENTION_DAYS) -> int:
    """Delete every thread whose latest checkpoint is older than ``days``; returns how many."""
    store = saver(root)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed = 0
    for thread_id in threads(root):
        latest = store.get_tuple({"configurable": {"thread_id": thread_id}})
        stamp = (latest.checkpoint or {}).get("ts") if latest else None
        try:
            when = datetime.fromisoformat(str(stamp))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < cutoff:
            store.delete_thread(thread_id)
            removed += 1
    return removed


def copy_thread(root, source: str, target: str) -> int:
    """Every checkpoint and pending write of thread ``source``, copied to the empty thread ``target``
    (SqliteSaver leaves LangGraph's copy_thread unimplemented). Returns how many checkpoints."""
    store = saver(root)
    with store.cursor() as cursor:
        cursor.execute("SELECT 1 FROM checkpoints WHERE thread_id=? LIMIT 1", (target,))
        if cursor.fetchone():
            raise ValueError(f"Checkpoint thread {target} already exists.")
        cursor.execute("INSERT INTO checkpoints(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, "
                       "checkpoint, metadata) SELECT ?, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, "
                       "checkpoint, metadata FROM checkpoints WHERE thread_id=?", (target, source))
        copied = cursor.rowcount
        cursor.execute("INSERT INTO writes(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value) "
                       "SELECT ?, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value FROM writes "
                       "WHERE thread_id=?", (target, source))
    return copied
