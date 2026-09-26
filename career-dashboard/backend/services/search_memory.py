"""What every search has already looked at, so the next pass spends its effort on new postings.

A night of searching reads the same employer feeds and boards again and again. Each
posting a hunt pass evaluates is remembered here under its canonical identity (the
same key the jobs table uses) with the outcome: saved, rejected at a named gate,
excluded by the work-permit gate, a duplicate, or held for an AI requirement check.
A later pass skips a posting already decided, unless the candidate's evidence has
changed since a fit decision (then the fit is worth checking again). Held postings
keep their full text here until a free AI plan can check them.

The memory belongs to the profile's database and never changes a job's status.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from backend.services.postings import posting_key

# A decided posting is skipped for this long; after it, a still-open posting is looked at again.
REMEMBER_DAYS = 21
FINAL = ("saved", "rejected", "excluded", "duplicate")


def ensure(db) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS search_memory(
        key TEXT PRIMARY KEY, company TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL,
        stage TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', fit_score INTEGER,
        evidence_hash TEXT NOT NULL DEFAULT '', posting TEXT, first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL, times INTEGER NOT NULL DEFAULT 1);
    CREATE INDEX IF NOT EXISTS idx_search_memory_outcome ON search_memory(outcome, last_seen);
    """)


def keys_for(posting: dict) -> list[str]:
    """Every identity of a posting: its URL and, when shown, company + requisition."""
    url = str(posting.get("url") or "")
    keys = []
    try:
        keys.append(posting_key(url))
        if str(posting.get("requisition_id") or "").strip():
            keys.append(posting_key(url, str(posting.get("company") or ""), str(posting["requisition_id"])))
    except ValueError:
        pass
    return list(dict.fromkeys(keys))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def decided(db, posting: dict, evidence_hash: str, days: int = REMEMBER_DAYS) -> dict | None:
    """The remembered decision for this posting when it still stands, else None."""
    ensure(db)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    for key in keys_for(posting):
        row = db.execute("SELECT * FROM search_memory WHERE key=?", (key,)).fetchone()
        if not row or row["outcome"] not in FINAL or row["last_seen"] < since:
            continue
        # A fit decision made on different evidence is stale: the candidate added skills or projects.
        if row["stage"] == "fit" and row["evidence_hash"] and row["evidence_hash"] != evidence_hash:
            continue
        return dict(row)
    return None


def remember(db, posting: dict, outcome: str, *, stage: str = "", reason: str = "", fit_score=None,
             evidence_hash: str = "", keep_posting: bool = False) -> None:
    """Record (or update) what happened to one posting."""
    ensure(db)
    now = _now()
    body = None
    if keep_posting:
        body = json.dumps({k: v for k, v in posting.items() if k != "relevance"}, ensure_ascii=False, default=str)
    for key in keys_for(posting):
        db.execute(
            """INSERT INTO search_memory(key,company,title,url,source,outcome,stage,reason,fit_score,evidence_hash,posting,first_seen,last_seen)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET outcome=excluded.outcome, stage=excluded.stage, reason=excluded.reason,
            fit_score=COALESCE(excluded.fit_score, search_memory.fit_score), evidence_hash=excluded.evidence_hash,
            posting=excluded.posting, source=excluded.source, last_seen=excluded.last_seen, times=search_memory.times+1""",
            (key, str(posting.get("company") or "")[:200], str(posting.get("title") or "")[:300],
             str(posting.get("url") or "")[:1000], str(posting.get("source") or "")[:40], outcome, stage,
             str(reason or "")[:500], fit_score, evidence_hash, body, now, now))


def held(db, limit: int = 60) -> list[dict]:
    """Postings waiting for an AI requirement check, oldest first."""
    ensure(db)
    rows = db.execute("SELECT posting FROM search_memory WHERE outcome='held' AND posting IS NOT NULL "
                      "ORDER BY last_seen LIMIT ?", (limit * 2,)).fetchall()
    out, seen = [], set()
    for row in rows:
        try:
            posting = json.loads(row["posting"])
        except (TypeError, ValueError):
            continue
        if posting.get("url") in seen:
            continue
        seen.add(posting.get("url"))
        out.append({**posting, "source": "held", "source_kind": posting.get("source_kind") or "employer_feed"})
        if len(out) >= limit:
            break
    return out


def held_count(db) -> int:
    ensure(db)
    return db.execute("SELECT COUNT(DISTINCT url) FROM search_memory WHERE outcome='held'").fetchone()[0]


def summary(db, since: str | None = None) -> dict:
    """Counts by outcome (and by rejecting gate), optionally since a timestamp."""
    ensure(db)
    where, args = ("WHERE last_seen >= ?", (since,)) if since else ("", ())
    outcomes = dict(db.execute(f"SELECT outcome, COUNT(DISTINCT url) FROM search_memory {where} GROUP BY outcome", args).fetchall())
    stages = dict(db.execute(
        f"SELECT stage, COUNT(DISTINCT url) FROM search_memory {where + (' AND' if where else 'WHERE')} outcome='rejected' GROUP BY stage",
        args).fetchall())
    return {"outcomes": outcomes, "rejected_by_stage": stages}
