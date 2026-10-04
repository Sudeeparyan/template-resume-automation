"""The market repository: every read and write of ``data/market/market.db`` goes through here.

Callers hand in postings in the shape ``services/job_sources.make_posting`` returns and read
plain dictionaries back, so the storage can move to Postgres for hosting without changing them.
Only public posting data is stored; a profile's own state never enters this database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from backend import paths
from backend.market import normalize, schema

# How directly a source shows the employer's own posting; the lowest rank represents a cluster.
SOURCE_RANK = {"employer_feed": 1, "official_board": 2, "aggregator": 3, "job_board": 4}
STALE_DAYS = 21  # an open posting no source has shown for this long is marked stale
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MarketStore:
    def __init__(self, path: Path | str | None = None):
        # Resolved now, not at import: tests and tools point paths.MARKET_DB elsewhere.
        self.path = Path(path) if path is not None else Path(paths.MARKET_DB)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        schema.migrate(db)
        return db

    # ---- postings -----------------------------------------------------------------------------
    def record_postings(self, postings: Iterable[dict], *, source: str | None = None) -> dict[str, int]:
        """Add or refresh public postings; returns counts of new, refreshed and unusable ones."""
        from backend.permits.employer_names import normalize_ie
        from backend.services.opportunities import permit_statement
        from backend.services.postings import posting_key

        counts = {"new": 0, "refreshed": 0, "skipped": 0}
        now = _now()
        rows = []
        for posting in postings:
            if not isinstance(posting, dict):
                counts["skipped"] += 1
                continue
            company, title, url = (str(posting.get(k) or "").strip() for k in ("company", "title", "url"))
            try:
                key = posting_key(url, company, str(posting.get("requisition_id") or ""))
            except (ValueError, AttributeError):
                key = ""
            if not (key and company and title):
                counts["skipped"] += 1
                continue
            description = str(posting.get("description") or "")
            location = str(posting.get("location") or "")
            nuts = posting.get("nuts") if isinstance(posting.get("nuts"), list) else []
            places = normalize.counties(location, nuts)
            pay = posting.get("salary") if isinstance(posting.get("salary"), dict) else {}
            statement = permit_statement(description)
            kind = str(posting.get("source_kind") or "job_board")
            employer_key = normalize_ie(company) or " ".join(company.casefold().split())
            rows.append({
                "key": key, "url": url, "company": company, "employer_key": employer_key, "title": title,
                "title_key": normalize.title_key(title), "location": location, "counties": ";".join(places),
                "region": normalize.region(nuts), "posting_type": normalize.posting_type(title, description),
                "level": normalize.level(title, description), "role_family": normalize.role_family(title),
                "years_required": normalize.years_required(description),
                "closing_date": normalize.closing_date(description, str(posting.get("valid_through") or "")),
                "posted_at": str(posting.get("posted_at") or ""), "description": description,
                "description_hash": hashlib.sha256(description.encode()).hexdigest(),
                "salary": json.dumps(pay, ensure_ascii=False, default=str), "salary_kind": str(pay.get("kind") or "unknown"),
                "salary_min": pay.get("annual_min") if pay.get("currency") == "EUR" else None,
                "salary_max": pay.get("annual_max") if pay.get("currency") == "EUR" else None,
                "statement": statement["state"], "statement_quote": statement["quote"],
                "on_eures": int(bool(posting.get("on_eures")) or posting.get("source") == "eures"),
                "source": str(source or posting.get("source") or "unknown"), "source_kind": kind,
                "source_rank": SOURCE_RANK.get(kind, 5),
                "cluster": f"{employer_key}|{normalize.title_key(title)}|{places[0] if places else ''}",
                "lead": int(bool(posting.get("lead"))), "attribution": str(posting.get("attribution") or ""),
                "extra_urls": [str(u) for u in posting.get("source_urls") or [] if u],
            })
        if not rows:
            return counts
        with _LOCK, closing(self.connect()) as db, db:
            for row in rows:
                extra_urls = row.pop("extra_urls")
                before = db.execute("SELECT description_hash FROM postings WHERE key=?", (row["key"],)).fetchone()
                if before is None:
                    db.execute(f"INSERT INTO postings({','.join(row)},first_seen,last_seen) "
                               f"VALUES({','.join('?' * len(row))},?,?)", (*row.values(), now, now))
                    counts["new"] += 1
                else:
                    fields = (row if before["description_hash"] != row["description_hash"]
                              else ("url", "statement", "statement_quote"))
                    changes = {k: row[k] for k in fields if k not in ("key", "on_eures")}
                    sets = ",".join(f"{k}=?" for k in changes)
                    # on_eures only ever turns on: another source's copy does not unlist it from EURES.
                    db.execute(f"UPDATE postings SET {sets},on_eures=max(on_eures,?),last_seen=?,state='open',closed_at=NULL "
                               "WHERE key=?", (*changes.values(), row["on_eures"], now, row["key"]))
                    counts["refreshed"] += 1
                for url in dict.fromkeys([row["url"], *extra_urls]):
                    db.execute("INSERT INTO posting_sources(posting_key,source,url,first_seen,last_seen) VALUES(?,?,?,?,?) "
                               "ON CONFLICT(posting_key,source,url) DO UPDATE SET last_seen=excluded.last_seen",
                               (row["key"], row["source"], url, now, now))
                db.execute("INSERT INTO employers(key,name,first_seen,last_seen) VALUES(?,?,?,?) "
                           "ON CONFLICT(key) DO UPDATE SET last_seen=excluded.last_seen", (row["employer_key"], row["company"], now, now))
                # Market estimates use pay read from a whole posting, never an aggregator's excerpt.
                if (row["salary_kind"] == "advertised" and row["salary_min"] and row["role_family"] and row["level"]
                        and not row["lead"]):
                    db.execute("INSERT INTO salary_observations(posting_key,employer_key,role_family,level,annual_min,annual_max,"
                               "county,source,observed_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(posting_key) DO UPDATE SET "
                               "annual_min=excluded.annual_min,annual_max=excluded.annual_max,observed_at=excluded.observed_at",
                               (row["key"], row["employer_key"], row["role_family"], row["level"], row["salary_min"],
                                row["salary_max"], row["counties"].split(";")[0], row["source"], now))
        return counts

    def posting(self, key: str) -> dict[str, Any] | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM postings WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            sources = db.execute("SELECT source,url,first_seen,last_seen FROM posting_sources WHERE posting_key=? "
                                 "ORDER BY first_seen", (key,)).fetchall()
        return {**_plain(row), "sources": [dict(s) for s in sources]}

    def key_for(self, url: str, company: str = "", requisition_id: str = "") -> str:
        """The market key of a posting: the same identity a profile stores with a saved job."""
        from backend.services.postings import posting_key

        try:
            return posting_key(url, company, requisition_id)
        except (ValueError, AttributeError):
            return ""

    def refresh_states(self, *, on: date | None = None) -> dict[str, int]:
        """Close postings past their stated closing date; mark long-unseen ones stale."""
        on = on or date.today()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).isoformat(timespec="seconds")
        with _LOCK, closing(self.connect()) as db, db:
            closed = db.execute("UPDATE postings SET state='closed',closed_at=? WHERE state!='closed' AND closing_date!='' "
                                "AND closing_date<?", (_now(), on.isoformat())).rowcount
            stale = db.execute("UPDATE postings SET state='stale' WHERE state='open' AND last_seen<?", (cutoff,)).rowcount
        return {"closed": closed, "stale": stale}

    def counts(self) -> dict[str, int]:
        with closing(self.connect()) as db:
            rows = db.execute("SELECT state, COUNT(*) FROM postings GROUP BY state").fetchall()
            observations = db.execute("SELECT COUNT(*) FROM salary_observations").fetchone()[0]
        return {**{row[0]: row[1] for row in rows}, "salary_observations": observations}

    # ---- employer boards ------------------------------------------------------------------------
    def mark_board(self, row: dict, state: str, *, source: str = "registry") -> None:
        """Record a board's state after a read: "active", or "gone" when its system says it no longer exists."""
        from backend.permits.employer_names import normalize_ie

        identity = tuple(str(row.get(k) or "") for k in ("ats", "token", "host", "site"))
        with _LOCK, closing(self.connect()) as db, db:
            db.execute("INSERT INTO employer_boards(employer_key,ats,token,host,site,careers_url,state,checked_at,source) "
                       "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(ats,token,host,site) DO UPDATE SET state=excluded.state,"
                       "checked_at=excluded.checked_at",
                       (normalize_ie(str(row.get("name") or "")), *identity, str(row.get("careers_url") or ""), state,
                        _now(), source))

    def gone_boards(self, *, days: int) -> set[tuple[str, str, str, str]]:
        """Boards found gone within the last ``days``: (ats, token, host, site)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with closing(self.connect()) as db:
            rows = db.execute("SELECT ats,token,host,site FROM employer_boards WHERE state='gone' AND checked_at>=?",
                              (cutoff,)).fetchall()
        return {tuple(row) for row in rows}

    # ---- company dossiers (public research, shared by every profile) ---------------------------
    def dossier(self, employer_key: str) -> dict | None:
        """A company dossier that has not expired yet, or None."""
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM company_dossiers WHERE employer_key=? AND expires_at>?",
                             (employer_key, _now())).fetchone()
        if row is None:
            return None
        try:
            return {**json.loads(row["dossier"]), "depth": row["depth"], "created_at": row["created_at"],
                    "expires_at": row["expires_at"]}
        except ValueError:
            return None

    def save_dossier(self, employer_key: str, depth: str, dossier: dict, *, days: int = 30) -> None:
        now = datetime.now(timezone.utc)
        with _LOCK, closing(self.connect()) as db, db:
            db.execute("INSERT INTO company_dossiers(employer_key,depth,dossier,created_at,expires_at) VALUES(?,?,?,?,?) "
                       "ON CONFLICT(employer_key) DO UPDATE SET depth=excluded.depth,dossier=excluded.dossier,"
                       "created_at=excluded.created_at,expires_at=excluded.expires_at",
                       (employer_key, depth, json.dumps(dossier, ensure_ascii=False),
                        now.isoformat(timespec="seconds"), (now + timedelta(days=days)).isoformat(timespec="seconds")))

    def eures_count(self, employer_key: str, *, days: int = 90) -> int:
        """Vacancies this employer advertised on EURES / JobsIreland that the app first saw in the last ``days``."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with closing(self.connect()) as db:
            return int(db.execute("SELECT COUNT(*) FROM postings WHERE employer_key=? AND on_eures=1 AND first_seen>=?",
                                  (employer_key, since)).fetchone()[0])

    # ---- pay ------------------------------------------------------------------------------------
    def salary_observations(self, role_family: str, level: str, *, since: str) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute("SELECT * FROM salary_observations WHERE role_family=? AND level=? AND observed_at>=? "
                              "ORDER BY observed_at DESC", (role_family, level, since)).fetchall()
        return [dict(row) for row in rows]

    # ---- sources: runs, paging cursors and request budgets -------------------------------------
    def start_run(self, source: str) -> int:
        with _LOCK, closing(self.connect()) as db, db:
            return int(db.execute("INSERT INTO source_runs(source,started_at) VALUES(?,?)", (source, _now())).lastrowid)

    def finish_run(self, run_id: int, *, state: str, found: int = 0, new: int = 0, error: str = "") -> None:
        with _LOCK, closing(self.connect()) as db, db:
            db.execute("UPDATE source_runs SET finished_at=?,state=?,found=?,new=?,error=? WHERE id=?",
                       (_now(), state, found, new, error[:500], run_id))

    def claim_run(self, source: str, *, fresh_hours: float, lease_hours: float = 2.0) -> int | None:
        """Start a run of ``source`` unless one finished in the last ``fresh_hours`` or another is still
        running (started within ``lease_hours``); the check and the start are one transaction, so two
        apps on one computer never both start it. Returns the run id, or None."""
        now = datetime.now(timezone.utc)
        with _LOCK, closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM source_runs WHERE source=? ORDER BY id DESC LIMIT 1", (source,)).fetchone()
                if row:
                    started = datetime.fromisoformat(row["started_at"])
                    if row["state"] == "running" and now - started < timedelta(hours=lease_hours):
                        db.execute("ROLLBACK")
                        return None
                    if row["finished_at"] and row["state"] == "completed" and \
                            now - datetime.fromisoformat(row["finished_at"]) < timedelta(hours=fresh_hours):
                        db.execute("ROLLBACK")
                        return None
                    if row["state"] == "running":  # an abandoned lease (the app stopped): close it
                        db.execute("UPDATE source_runs SET state='abandoned',finished_at=? WHERE id=?", (_now(), row["id"]))
                run_id = int(db.execute("INSERT INTO source_runs(source,started_at) VALUES(?,?)", (source, _now())).lastrowid)
                db.execute("COMMIT")
                return run_id
            except Exception:
                db.execute("ROLLBACK")
                raise

    def last_run(self, source: str) -> dict | None:
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM source_runs WHERE source=? ORDER BY id DESC LIMIT 1", (source,)).fetchone()
        return dict(row) if row else None

    def cursor(self, source: str, query: str) -> dict:
        with closing(self.connect()) as db:
            row = db.execute("SELECT cursor FROM source_cursors WHERE source=? AND query=?", (source, query)).fetchone()
        try:
            return json.loads(row[0]) if row else {}
        except ValueError:
            return {}

    def save_cursor(self, source: str, query: str, cursor: dict) -> None:
        with _LOCK, closing(self.connect()) as db, db:
            db.execute("INSERT INTO source_cursors(source,query,cursor,updated_at) VALUES(?,?,?,?) ON CONFLICT(source,query) "
                       "DO UPDATE SET cursor=excluded.cursor,updated_at=excluded.updated_at",
                       (source, query, json.dumps(cursor), _now()))

    def take_budget(self, source: str, *, per_day: int, lifetime: int | None = None, n: int = 1) -> bool:
        """Spend ``n`` requests of a source's allowance; False (nothing spent) when a cap would be passed."""
        today = date.today().isoformat()
        with _LOCK, closing(self.connect()) as db, db:
            used_today = (db.execute("SELECT used FROM source_budgets WHERE source=? AND day=?", (source, today)).fetchone() or [0])[0]
            used_ever = db.execute("SELECT COALESCE(SUM(used),0) FROM source_budgets WHERE source=?", (source,)).fetchone()[0]
            if used_today + n > per_day or (lifetime is not None and used_ever + n > lifetime):
                return False
            db.execute("INSERT INTO source_budgets(source,day,used) VALUES(?,?,?) ON CONFLICT(source,day) "
                       "DO UPDATE SET used=used+excluded.used", (source, today, n))
            return True

    def budget_used(self, source: str) -> dict[str, int]:
        today = date.today().isoformat()
        with closing(self.connect()) as db:
            used_today = (db.execute("SELECT used FROM source_budgets WHERE source=? AND day=?", (source, today)).fetchone() or [0])[0]
            used_ever = db.execute("SELECT COALESCE(SUM(used),0) FROM source_budgets WHERE source=?", (source,)).fetchone()[0]
        return {"today": int(used_today), "lifetime": int(used_ever)}


def _plain(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    try:
        out["salary"] = json.loads(out.get("salary") or "{}")
    except ValueError:
        out["salary"] = {}
    out["counties"] = [c for c in str(out.get("counties") or "").split(";") if c]
    return out
