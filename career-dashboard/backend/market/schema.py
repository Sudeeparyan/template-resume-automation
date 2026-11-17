"""The market database's tables, created and upgraded by numbered migrations (PRAGMA user_version)."""

from __future__ import annotations

import sqlite3

MIGRATIONS: tuple[str, ...] = (
    # 1: postings and where each was read, employers, pay observations, source runs and budgets.
    """
    CREATE TABLE IF NOT EXISTS employers(
        key TEXT PRIMARY KEY, name TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS employer_boards(
        employer_key TEXT NOT NULL, ats TEXT NOT NULL, token TEXT NOT NULL DEFAULT '', host TEXT NOT NULL DEFAULT '',
        site TEXT NOT NULL DEFAULT '', careers_url TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'active',
        checked_at TEXT, source TEXT NOT NULL DEFAULT '', PRIMARY KEY(ats, token, host, site));
    CREATE TABLE IF NOT EXISTS postings(
        key TEXT PRIMARY KEY, url TEXT NOT NULL, company TEXT NOT NULL, employer_key TEXT NOT NULL,
        title TEXT NOT NULL, title_key TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
        counties TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '',
        posting_type TEXT NOT NULL DEFAULT 'unspecified', level TEXT NOT NULL DEFAULT '',
        role_family TEXT NOT NULL DEFAULT '', years_required INTEGER, closing_date TEXT NOT NULL DEFAULT '',
        posted_at TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', description_hash TEXT NOT NULL DEFAULT '',
        salary TEXT NOT NULL DEFAULT '{}', salary_kind TEXT NOT NULL DEFAULT 'unknown', salary_min REAL, salary_max REAL,
        statement TEXT NOT NULL DEFAULT 'silent', statement_quote TEXT NOT NULL DEFAULT '',
        on_eures INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL, source_kind TEXT NOT NULL, source_rank INTEGER NOT NULL,
        cluster TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'open', closed_at TEXT);
    CREATE INDEX IF NOT EXISTS ix_postings_cluster ON postings(cluster);
    CREATE INDEX IF NOT EXISTS ix_postings_state_seen ON postings(state, last_seen);
    CREATE INDEX IF NOT EXISTS ix_postings_employer ON postings(employer_key);
    CREATE TABLE IF NOT EXISTS posting_sources(
        posting_key TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL, first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL, PRIMARY KEY(posting_key, source, url));
    CREATE TABLE IF NOT EXISTS salary_observations(
        posting_key TEXT PRIMARY KEY, employer_key TEXT NOT NULL, role_family TEXT NOT NULL, level TEXT NOT NULL,
        annual_min REAL, annual_max REAL, county TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
        observed_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_salary_family ON salary_observations(role_family, level, observed_at);
    CREATE TABLE IF NOT EXISTS company_dossiers(
        employer_key TEXT PRIMARY KEY, depth TEXT NOT NULL, dossier TEXT NOT NULL, created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS source_runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
        state TEXT NOT NULL DEFAULT 'running', found INTEGER NOT NULL DEFAULT 0, new INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS source_cursors(
        source TEXT NOT NULL, query TEXT NOT NULL, cursor TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
        PRIMARY KEY(source, query));
    CREATE TABLE IF NOT EXISTS source_budgets(
        source TEXT NOT NULL, day TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(source, day));
    """,
    # 2: aggregator leads (an excerpt and the aggregator's link, shown with its attribution), and each
    # employer's DETE permit history, cached for the Tracker against the DETE data it was read from.
    """
    ALTER TABLE postings ADD COLUMN lead INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE postings ADD COLUMN attribution TEXT NOT NULL DEFAULT '';
    ALTER TABLE employers ADD COLUMN permits_24m INTEGER;
    ALTER TABLE employers ADD COLUMN permit_names TEXT NOT NULL DEFAULT '';
    ALTER TABLE employers ADD COLUMN permit_years TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE employers ADD COLUMN permits_checked TEXT NOT NULL DEFAULT '';
    CREATE INDEX IF NOT EXISTS ix_postings_first_seen ON postings(first_seen);
    """,
)


def migrate(db: sqlite3.Connection) -> int:
    """Apply the migrations this database has not had; returns its version."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS, 1):
        if number > version:
            db.executescript(script)
            db.execute(f"PRAGMA user_version={number}")
            version = number
    return version
