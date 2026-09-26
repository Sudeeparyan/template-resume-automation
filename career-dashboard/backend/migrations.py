"""Transactional SQLite migrations and pre-migration recovery snapshots."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "reliability_entities",
        """
        CREATE TABLE IF NOT EXISTS companies(
            id TEXT PRIMARY KEY,
            normalized_name TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            website_domain TEXT NOT NULL DEFAULT '',
            size_category TEXT NOT NULL DEFAULT 'unknown'
                CHECK(size_category IN ('startup','mid','large','unknown')),
            employee_min INTEGER,
            employee_max INTEGER,
            legitimacy_state TEXT NOT NULL DEFAULT 'needs_review'
                CHECK(legitimacy_state IN ('verified','needs_review','blocked')),
            sponsorship_state TEXT NOT NULL DEFAULT 'unknown'
                CHECK(sponsorship_state IN ('verified','unknown','not_offered')),
            manual_override_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS company_checks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL REFERENCES companies(id),
            state TEXT NOT NULL CHECK(state IN ('verified','needs_review','blocked')),
            sources TEXT NOT NULL DEFAULT '[]',
            findings TEXT NOT NULL DEFAULT '[]',
            red_flags TEXT NOT NULL DEFAULT '[]',
            checked_at TEXT NOT NULL,
            manual_override INTEGER NOT NULL DEFAULT 0,
            override_reason TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS posting_checks(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            state TEXT NOT NULL CHECK(state IN ('active','expired','needs_review')),
            http_status INTEGER,
            final_url TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '[]',
            checked_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_posting_checks_job_time
            ON posting_checks(job_id, checked_at DESC);
        CREATE TABLE IF NOT EXISTS job_requirements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            jd_hash TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('required','responsibility','preferred')),
            requirement TEXT NOT NULL,
            excerpt TEXT NOT NULL,
            aliases TEXT NOT NULL DEFAULT '[]',
            extractor_provider TEXT NOT NULL DEFAULT 'deterministic',
            extractor_model TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(job_id, jd_hash, category, requirement)
        );
        CREATE TABLE IF NOT EXISTS resume_assessments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            draft_revision INTEGER NOT NULL,
            pdf_hash TEXT NOT NULL,
            jd_hash TEXT NOT NULL,
            scoring_version TEXT NOT NULL,
            ats_readiness INTEGER NOT NULL,
            resume_coverage INTEGER NOT NULL,
            opportunity_fit TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(job_id, draft_revision, pdf_hash, jd_hash, scoring_version)
        );
        CREATE TABLE IF NOT EXISTS chat_change_sets(
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            scope TEXT NOT NULL CHECK(scope IN ('resume','profile')),
            job_id TEXT REFERENCES jobs(id),
            source_revision INTEGER NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('preview','applied','undone','rejected')),
            proposed_changes TEXT NOT NULL,
            applied_revision INTEGER,
            undo_revision INTEGER,
            evidence_ids TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(scope, request_id)
        );
        """,
    ),
    (
        2,
        "ai_gateway_records",
        """
        CREATE INDEX IF NOT EXISTS idx_ai_calls_action_time
            ON ai_calls(action, created_at DESC);
        """,
    ),
    (3, "agent_provider_selection", ""),
    (4, "artifact_paths_under_data", ""),
    (
        5,
        "us_sponsorship_and_reapply",
        """
        CREATE TABLE IF NOT EXISTS excluded_postings(
            id TEXT PRIMARY KEY,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL,
            reason_label TEXT NOT NULL DEFAULT '',
            sentence TEXT NOT NULL DEFAULT '',
            pattern TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            excluded_at TEXT NOT NULL,
            restored_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_excluded_company ON excluded_postings(company);
        CREATE TABLE IF NOT EXISTS signature_assignments(
            company_key TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            job_id TEXT REFERENCES jobs(id),
            assigned_at TEXT NOT NULL
        )
        """,
    ),
    (
        6,
        "reapply_memory_survives_fresh_start",
        """
        CREATE TABLE IF NOT EXISTS reapply_history(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            application_date TEXT,
            updated_at TEXT NOT NULL,
            archived_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_reapply_history_company ON reapply_history(company)
        """,
    ),
    (
        7,
        "visibility_and_tailoring",
        """
        CREATE TABLE IF NOT EXISTS rejected_leads(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rejected_leads_time ON rejected_leads(created_at DESC);
        CREATE TABLE IF NOT EXISTS resume_items(
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id),
            section TEXT NOT NULL CHECK(section IN ('projects','skills')),
            content TEXT NOT NULL,
            origin TEXT NOT NULL CHECK(origin IN ('verified','predicted')),
            evidence_id TEXT NOT NULL DEFAULT '',
            decision TEXT NOT NULL DEFAULT 'pending' CHECK(decision IN ('pending','kept','removed')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_resume_items_job ON resume_items(job_id);
        ALTER TABLE jobs ADD COLUMN fit_score INTEGER;
        ALTER TABLE jobs ADD COLUMN fit_rationale TEXT NOT NULL DEFAULT '';
        ALTER TABLE jobs ADD COLUMN raw_jd TEXT NOT NULL DEFAULT '';
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_state_time ON agent_runs(state, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_ai_calls_day ON ai_calls(day)
        """,
    ),
    (
        8,
        "job_fit_matrix",
        # One verified requirement matrix per job (backend/services/fit.py): what the posting
        # asks for and which registered evidence shows it. Reused by the fit score, the tailor,
        # resume coverage and the study plan; recomputed when the posting or evidence changes.
        """
        CREATE TABLE IF NOT EXISTS job_fit(
            job_id TEXT PRIMARY KEY REFERENCES jobs(id),
            jd_hash TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            fit_version TEXT NOT NULL,
            method TEXT NOT NULL CHECK(method IN ('ai','rules')),
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            matrix TEXT NOT NULL,
            score INTEGER,
            created_at TEXT NOT NULL
        )
        """,
    ),
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_once(root: Path, db_path: Path) -> None:
    recovery = root / "data" / "migrations"
    database_copy = recovery / "pre-v1-career.db"
    manifest_path = recovery / "pre-v1-artifacts.sha256.json"
    if database_copy.exists() and manifest_path.exists():
        return
    recovery.mkdir(parents=True, exist_ok=True)
    if db_path.exists() and not database_copy.exists():
        shutil.copy2(db_path, database_copy)
    if not manifest_path.exists():
        artifact_root = root / "data/output" / "applications"
        files = []
        if artifact_root.exists():
            for path in sorted(p for p in artifact_root.rglob("*") if p.is_file()):
                files.append(
                    {
                        "path": str(path.relative_to(root)),
                        "size": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                )
        payload = {
            "created_at": _utcnow(),
            "database_copy": str(database_copy.relative_to(root)),
            "artifacts": files,
        }
        temp = manifest_path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp.replace(manifest_path)


def _add_column(db: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def migrate(root: Path, db_path: Path, db: sqlite3.Connection) -> list[int]:
    """Apply all unapplied migrations and return their version numbers."""
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        _snapshot_once(root, db_path)
    db.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations(
        version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"""
    )
    applied = {row[0] for row in db.execute("SELECT version FROM schema_migrations")}
    completed: list[int] = []
    for version, name, sql in MIGRATIONS:
        if version in applied:
            continue
        db.execute("SAVEPOINT schema_migration")
        try:
            if version == 2:
                # ``ai_calls`` predates the provider gateway. Keep its original
                # budget columns and enrich it in place so no usage history is lost.
                db.execute(
                    """CREATE TABLE IF NOT EXISTS ai_calls(
                    id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, day TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT,
                    provider TEXT NOT NULL DEFAULT 'codex',
                    model TEXT NOT NULL DEFAULT 'codex-runtime',
                    action TEXT NOT NULL DEFAULT 'unknown',
                    cache_version TEXT NOT NULL DEFAULT 'v1',
                    input_tokens INTEGER, output_tokens INTEGER,
                    error_class TEXT NOT NULL DEFAULT '')"""
                )
                _add_column(db, "ai_calls", "provider TEXT NOT NULL DEFAULT 'codex'")
                _add_column(db, "ai_calls", "model TEXT NOT NULL DEFAULT 'codex-runtime'")
                _add_column(db, "ai_calls", "action TEXT NOT NULL DEFAULT 'unknown'")
                _add_column(db, "ai_calls", "cache_version TEXT NOT NULL DEFAULT 'v1'")
                _add_column(db, "ai_calls", "input_tokens INTEGER")
                _add_column(db, "ai_calls", "output_tokens INTEGER")
                _add_column(db, "ai_calls", "error_class TEXT NOT NULL DEFAULT ''")
            if version == 4:
                # Generated artifacts moved from <app>/output to <app>/data/output.
                # Stored folders keep that prefix, so rewrite them in place; every
                # other stored path is already relative to the output root.
                for table, column in (("jobs", "folder"), ("studio_drafts", "folder")):
                    # studio_drafts is created by ResumeStudio, not by a migration,
                    # so a fresh workspace reaches here before the table exists.
                    if db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone():
                        db.execute(
                            f"UPDATE {table} SET {column} = 'data/' || {column} "
                            f"WHERE {column} LIKE 'output/%'"
                        )
            if version == 3:
                db.execute(
                    """CREATE TABLE IF NOT EXISTS agent_runs(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    job_id TEXT REFERENCES jobs(id), state TEXT NOT NULL,
                    input TEXT NOT NULL, result TEXT, error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
                )
                _add_column(db, "agent_runs", "provider TEXT")
                _add_column(db, "agent_runs", "model TEXT")
                _add_column(db, "agent_runs", "preset TEXT NOT NULL DEFAULT 'default'")
            # ``executescript`` performs an implicit commit, which would defeat the
            # migration savepoint. These migrations intentionally contain only
            # simple DDL statements, so execute each statement in the savepoint.
            for statement in (part.strip() for part in sql.split(";")):
                if statement:
                    db.execute(statement)
            if version == 5:
                # Sponsorship tier and its evidence live on the job, so every list can show them.
                _add_column(db, "jobs", "sponsor_tier TEXT CHECK(sponsor_tier IN ('S','A','B','C'))")
                _add_column(db, "jobs", "sponsor_evidence TEXT NOT NULL DEFAULT '{}'")
            if version == 1:
                _add_column(db, "jobs", "company_id TEXT REFERENCES companies(id)")
                _add_column(db, "jobs", "posting_state TEXT NOT NULL DEFAULT 'active'")
                _add_column(db, "jobs", "last_verified_at TEXT")
                _add_column(db, "jobs", "closed_at TEXT")
            db.execute(
                "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                (version, name, _utcnow()),
            )
            db.execute("RELEASE schema_migration")
        except Exception:
            db.execute("ROLLBACK TO schema_migration")
            db.execute("RELEASE schema_migration")
            raise
        completed.append(version)
    return completed
