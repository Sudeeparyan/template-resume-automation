"""Durable, fingerprinted stages and bounded local AI execution.

Stages may run again after a crash: callers must publish artifacts atomically and
make mutations idempotent. A lease owner alone may commit a stage's result.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class TaskBusy(ValueError):
    """Another worker owns this stage, or its retry is not due."""


class LeaseLost(ValueError):
    """The worker no longer owns the stage and cannot publish its result."""


_slots = threading.Condition()
_providers: set[str] = set()
_held_provider: ContextVar[str | None] = ContextVar("career_invocation_provider", default=None)
_job_locks_guard = threading.Lock()
_job_locks = {}


@contextmanager
def job_write_slot(root, job_id):
    """Serialize mutations of one job across in-process service instances.

Reentrant for direct Studio calls from an already locked worker. Do not hold
this while waiting for a queued agent: the worker needs the same job slot.
"""
    key = (str(Path(root).resolve()), str(job_id))
    with _job_locks_guard:
        entry = _job_locks.setdefault(key, [threading.RLock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _job_locks_guard:
            entry[1] -= 1
            if not entry[1]:
                _job_locks.pop(key, None)


SLOT_TIMEOUT = 960  # one web call's own limit (900 s, backend/ai/*_cli.py) and a minute


def slot_timeout() -> float:
    """How long a call waits for its provider's slot: ``CAREER_AI_SLOT_TIMEOUT`` seconds, else SLOT_TIMEOUT."""
    try:
        value = float(os.environ.get("CAREER_AI_SLOT_TIMEOUT") or SLOT_TIMEOUT)
    except ValueError:
        return SLOT_TIMEOUT
    return value if value > 0 else SLOT_TIMEOUT


@contextmanager
def invocation_slot(provider: str, *, timeout: float | None = None):
    """At most two AI calls per process, and one per actual provider.

The router and specialist adapter can both wrap a call; same-provider nesting
does not reserve twice. Acquire only around an actual endpoint invocation. A call
queued behind another on the same provider waits for it (``slot_timeout()``).
"""
    timeout = slot_timeout() if timeout is None else timeout
    held = _held_provider.get()
    if held == provider:
        yield
        return
    if held is not None:
        raise RuntimeError("Release the current provider before invoking a fallback")
    until = time.monotonic() + timeout
    with _slots:
        while provider in _providers or len(_providers) >= 2:
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Waiting for an AI execution slot timed out")
            _slots.wait(remaining)
        _providers.add(provider)
    token = _held_provider.set(provider)
    try:
        yield
    finally:
        _held_provider.reset(token)
        with _slots:
            _providers.remove(provider)
            _slots.notify_all()


class TaskRepository:
    """One profile's durable stage records, shared by CLI and dashboard workers."""

    def __init__(self, services, *, clock=time.time):
        self.s, self.w, self.clock = services, services.w, clock
        with self.w.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS execution_tasks(
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, stage TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL, input TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL, owner TEXT, lease_until REAL,
                    retry_at REAL, result TEXT, error TEXT, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(job_id,stage,input_fingerprint));
                CREATE INDEX IF NOT EXISTS execution_tasks_pending
                    ON execution_tasks(state,retry_at,lease_until);
            """)

    @staticmethod
    def _public(row):
        if row is None:
            return None
        item = dict(row)
        item["input"] = json.loads(item["input"])
        item["result"] = json.loads(item["result"]) if item["result"] is not None else None
        return item

    def get(self, task_id):
        with self.w.connect() as db:
            return self._public(db.execute("SELECT * FROM execution_tasks WHERE id=?", (task_id,)).fetchone())

    def enqueue(self, stage, payload, *, job_id=None, max_attempts=3):
        if not stage or max_attempts < 1:
            raise ValueError("A stage name and positive attempt limit are required")
        digest, stamp = fingerprint(payload), self.clock()
        with self.w.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT OR IGNORE INTO execution_tasks
                (id,job_id,stage,input_fingerprint,input,max_attempts,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""", (uuid.uuid4().hex, job_id or "", stage, digest,
                json.dumps(payload, ensure_ascii=False, allow_nan=False), max_attempts, stamp, stamp))
            return self._public(db.execute("""SELECT * FROM execution_tasks
                WHERE job_id=? AND stage=? AND input_fingerprint=?""", (job_id or "", stage, digest)).fetchone())

    def claim(self, task_id, owner, *, lease_seconds=90):
        if not owner or lease_seconds <= 0:
            raise ValueError("An owner and positive lease duration are required")
        stamp = self.clock()
        with self.w.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE execution_tasks SET state='running', owner=?,
                lease_until=?, attempts=attempts+1, retry_at=NULL, updated_at=?
                WHERE id=? AND attempts<max_attempts AND (
                    (state IN ('queued','retry') AND COALESCE(retry_at,0)<=?) OR
                    (state='running' AND lease_until<=?))""",
                (owner, stamp + lease_seconds, stamp, task_id, stamp, stamp)).rowcount
            return self._public(db.execute("SELECT * FROM execution_tasks WHERE id=?", (task_id,)).fetchone()) if changed else None

    def heartbeat(self, task_id, owner, *, lease_seconds=90):
        stamp = self.clock()
        with self.w.connect() as db:
            changed = db.execute("""UPDATE execution_tasks SET lease_until=?,updated_at=?
                WHERE id=? AND owner=? AND state='running' AND lease_until>?""",
                (stamp + lease_seconds, stamp, task_id, owner, stamp)).rowcount
        return bool(changed)

    def complete(self, task_id, owner, result):
        encoded, stamp = json.dumps(result, ensure_ascii=False, allow_nan=False), self.clock()
        with self.w.connect() as db:
            changed = db.execute("""UPDATE execution_tasks SET state='completed', result=?,
                error=NULL,owner=NULL,lease_until=NULL,updated_at=?
                WHERE id=? AND owner=? AND state='running' AND lease_until>?""",
                (encoded, stamp, task_id, owner, stamp)).rowcount
        if not changed:
            raise LeaseLost("This stage's lease expired before its result could be saved")
        return self.get(task_id)

    def fail(self, task_id, owner, error, *, retryable=False, retry_after=0):
        stamp = self.clock()
        with self.w.connect() as db:
            changed = db.execute("""UPDATE execution_tasks SET
                state=CASE WHEN ? AND attempts<max_attempts THEN 'retry' ELSE 'failed' END,
                error=?,retry_at=?,owner=NULL,lease_until=NULL,updated_at=?
                WHERE id=? AND owner=? AND state='running' AND lease_until>?""",
                (bool(retryable), str(error)[:1500], stamp + max(0, retry_after),
                 stamp, task_id, owner, stamp)).rowcount
        if not changed:
            raise LeaseLost("This stage is no longer owned by this worker")
        return self.get(task_id)

    def recover_expired(self):
        """Requeue expired leases only; another process's live work remains owned."""
        stamp = self.clock()
        with self.w.connect() as db:
            return db.execute("""UPDATE execution_tasks SET
                state=CASE WHEN attempts<max_attempts THEN 'retry' ELSE 'failed' END,
                owner=NULL,lease_until=NULL,retry_at=?,updated_at=?,
                error='Worker lease expired; retrying from saved inputs'
                WHERE state='running' AND lease_until<=?""", (stamp, stamp, stamp)).rowcount

    def invalidate(self, task_id):
        """Rebuild a completed stage whose referenced artifact failed validation."""
        with self.w.connect() as db:
            return bool(db.execute("""UPDATE execution_tasks SET state='queued',result=NULL,
                attempts=0,error=NULL,retry_at=NULL,updated_at=? WHERE id=? AND state='completed'""",
                (self.clock(), task_id)).rowcount)

    def run(self, stage, payload, fn, *, job_id=None, max_attempts=3,
            lease_seconds=90, validate_result=None, retryable=False, retry_after=0):
        """Reuse a valid result or run once with a background lease heartbeat.

Include all dependency hashes and policy versions in payload. ``fn`` has no
arguments. Retries are persisted, not slept through; the orchestrator schedules
the next invocation. ``validate_result`` checks referenced artifacts still exist.
"""
        task = self.enqueue(stage, payload, job_id=job_id, max_attempts=max_attempts)
        if task["state"] == "completed":
            if validate_result is None or validate_result(task["result"]):
                return task["result"]
            self.invalidate(task["id"])
        owner = uuid.uuid4().hex
        if not self.claim(task["id"], owner, lease_seconds=lease_seconds):
            current = self.get(task["id"])
            if current["state"] == "failed":
                raise ValueError(current["error"] or "This stage exhausted its attempts")
            raise TaskBusy("This stage is already running or waiting for its retry")
        finished = threading.Event()

        def pulse():
            while not finished.wait(min(30, lease_seconds / 3)):
                try:
                    if not self.heartbeat(task["id"], owner, lease_seconds=lease_seconds):
                        return
                except Exception:
                    return  # complete() still refuses a lost lease

        worker = threading.Thread(target=pulse, name="career-stage-lease", daemon=True)
        worker.start()
        try:
            result = fn()
            self.complete(task["id"], owner, result)
            return result
        except Exception as error:
            retry = retryable(error) if callable(retryable) else retryable
            try:
                self.fail(task["id"], owner, error, retryable=retry, retry_after=retry_after)
            except LeaseLost:
                pass
            raise
        finally:
            finished.set()
            worker.join(timeout=1)
