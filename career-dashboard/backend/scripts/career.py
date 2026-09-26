#!/usr/bin/env python3
"""Local, evidence-first workspace operations. No applications or messages are sent."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo
import yaml
from tracking import Tracking, today

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))
from backend.ai_marks import clean_text
from backend.migrations import migrate
from backend.paths import TIMEZONE, app_root_for
from backend.services.postings import canonical_url, posting_key

STATUSES = {
    "saved",
    "prepared",
    "applied",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "ghosted",   # applied, then 21 days of silence (reapply.age flips it)
}


PROJECT_DOMAINS = ("medical", "healthcare", "clinical", "device", "streaming", "real-time", "vision", "insurance", "research", "embedded")


def company_key(name):
    """Normalised company name used for never-re-apply and signature-project bookkeeping."""
    return re.sub(r"[^a-z0-9]+", "", str(name).casefold())


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_yaml(path):
    result = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"{path.name} must contain a mapping")
    return result


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as f:
        f.write(text)
        tmp = Path(f.name)
    # Windows: a scanner or another open handle makes the first replace fail
    # with WinError 5; it almost always clears within a second.
    for attempt in range(6):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 5:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.3 * (attempt + 1))


def safe_child(root, value):
    path = (root / value).resolve()
    if path == root.resolve() or root.resolve() not in path.parents:
        raise ValueError("Path must stay inside the workspace directory")
    return path


def job_url(value):
    return canonical_url(value)


# Tectonic is XeTeX with the template's T1 fonts: a non-ASCII character is dropped from the
# page ("0–255" printed as "0255"), so punctuation and accented letters become LaTeX.
TEX_UNICODE = {
    "–": "--", "—": "---", "‒": "-", "‐": "-", "‑": "-", "−": "-",
    "‘": "`", "’": "'", "“": "``", "”": "''", "…": "...", " ": " ",
    "•": r"\textbullet{}", "·": r"\textperiodcentered{}", "°": r"\textdegree{}",
    "×": r"$\times$", "→": r"$\rightarrow$", "≤": r"$\le$", "≥": r"$\ge$",
    "€": r"\texteuro{}", "£": r"\pounds{}",
}
TEX_ACCENTS = {"́": "'", "̀": "`", "̂": "^", "̈": '"', "̃": "~", "̧": "c", "̌": "v"}


def _tex_unicode(c):
    if c in TEX_UNICODE:
        return TEX_UNICODE[c]
    import unicodedata

    decomposed = unicodedata.normalize("NFD", c)
    if len(decomposed) == 2 and decomposed[0].isascii() and decomposed[1] in TEX_ACCENTS:
        return "\\" + TEX_ACCENTS[decomposed[1]] + "{" + decomposed[0] + "}"
    return c


def tex_escape(text):
    # Hidden characters and look-alike letters (AI marks) never reach resume.tex.
    return "".join(
        {
            "\\": r"\textbackslash{}",
            "&": r"\&",
            "%": r"\%",
            "$": r"\$",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
            "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
            "|": r"\textbar{}",
            # R² and m³: the T1 fonts have no superscript digits (they print as "Rš").
            "²": r"\textsuperscript{2}",
            "³": r"\textsuperscript{3}",
        }.get(c) or _tex_unicode(c)
        for c in clean_text(text)
    )


def tokens(text):
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "will",
        "that",
        "this",
        "have",
        "your",
        "our",
        "you",
        "into",
        "using",
        "work",
        "data",
        "project",
        "experience",
    }
    return {
        w for w in re.findall(r"[a-z][a-z0-9+#.-]{2,}", text.lower()) if w not in stop
    }


class Workspace(Tracking):
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        # A profile folder holds only data; code and machine-local secrets live
        # at the app root (backend/paths.py).
        self.app_root = app_root_for(self.root)
        # Each profile keeps its own dated search history.
        self.daily_dir = (self.root.parent if self.app_root == self.root else self.root) / "daily-job-search"
        self._timezone = (None, None)
        self.db_path = self.root / "data/career.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, company TEXT NOT NULL, title TEXT NOT NULL,
                location TEXT NOT NULL, url TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'saved', created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                application_date TEXT, selected_project_id TEXT, folder TEXT,
                verification TEXT NOT NULL DEFAULT 'not_verified')"""
            )
            self.init_tracking(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS posting_identities(identity TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id))"
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "record_source" not in columns:
                db.execute(
                    "ALTER TABLE jobs ADD COLUMN record_source TEXT NOT NULL DEFAULT 'posting'"
                )
            if "market" not in columns:
                from backend.countries import market_for_location

                db.execute("ALTER TABLE jobs ADD COLUMN market TEXT NOT NULL DEFAULT 'ie'")
                for row in db.execute("SELECT id,location FROM jobs").fetchall():
                    db.execute("UPDATE jobs SET market=? WHERE id=?",
                               (market_for_location(self.root, row["location"]), row["id"]))
            if "deleted_at" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN deleted_at TEXT")
            if "deletion_reason" not in columns:
                db.execute(
                    "ALTER TABLE jobs ADD COLUMN deletion_reason TEXT NOT NULL DEFAULT ''"
                )
            migrate(self.root, self.db_path, db)
            for row in db.execute("SELECT id,url FROM jobs").fetchall():
                db.execute(
                    "INSERT OR IGNORE INTO posting_identities VALUES(?,?)",
                    (posting_key(row["url"]), row["id"]),
                )

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def profile(self):
        return read_yaml(self.root / "data/config/profile.yml")

    def evidence(self):
        return read_yaml(self.root / "data/context/evidence.yml")

    @property
    def timezone(self):
        """The candidate's own time zone (profile.yml candidate.timezone), else the default."""
        path = self.root / "data/config/profile.yml"
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return TIMEZONE
        if self._timezone[0] != stamp:
            try:
                value = str((self.profile().get("candidate") or {}).get("timezone") or "")
                ZoneInfo(value)
            except Exception:  # noqa: BLE001 - a missing or unknown zone falls back
                value = TIMEZONE
            self._timezone = (stamp, value)
        return self._timezone[1]

    def today(self):
        return today(self.timezone)

    def candidate_name(self):
        try:
            return str(self.profile()["candidate"]["full_name"]).strip() or "the candidate"
        except Exception:  # noqa: BLE001 - a blank profile has no name yet
            return "the candidate"

    # Employer verification lives in `companies`; the UI needs it beside each job.
    JOB_SELECT = (
        "SELECT jobs.*, companies.legitimacy_state, companies.size_category, "
        "companies.sponsorship_state FROM jobs "
        "LEFT JOIN companies ON companies.id = jobs.company_id "
    )

    @staticmethod
    def _job_row(row):
        """Decode the JSON sponsorship evidence so callers and the UI get a dict."""
        job = dict(row)
        raw = job.get("sponsor_evidence")
        if isinstance(raw, str):
            try:
                job["sponsor_evidence"] = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                job["sponsor_evidence"] = {}
        return job

    def jobs(self, include_deleted=False):
        with self.connect() as db:
            return [
                self._job_row(r)
                for r in db.execute(
                    self.JOB_SELECT
                    + (
                        ""
                        if include_deleted
                        else "WHERE jobs.deleted_at IS NULL AND (jobs.posting_state <> 'expired' OR jobs.status NOT IN ('saved','prepared')) "
                    )
                    + "ORDER BY jobs.created_at DESC, jobs.id"
                )
            ]

    def get_job(self, job_id):
        with self.connect() as db:
            row = db.execute(self.JOB_SELECT + "WHERE jobs.id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Opportunity not found")
        return self._job_row(row)

    def add_job(self, company, title, location, url, description, requisition_id="", market=""):
        values = [str(v).strip() for v in (company, title, location, url, description)]
        if not all(values):
            raise ValueError("Complete all job fields")
        company, title, location, url, description = values
        if len(description) < 80:
            raise ValueError(
                "Paste the actual job description (at least 80 characters)"
            )
        url = job_url(url)
        job_id = hashlib.sha256(url.encode()).hexdigest()[:16]
        from backend.countries import market_for_location

        market = market_for_location(self.root, location, market)
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                identities = {
                    posting_key(url),
                    posting_key(url, company, requisition_id),
                }
                if any(
                    db.execute(
                        "SELECT 1 FROM posting_identities WHERE identity=?", (key,)
                    ).fetchone()
                    for key in identities
                ):
                    raise ValueError("This posting is already saved")
                db.execute(
                    "INSERT INTO jobs(id,company,title,location,url,description,created_at,updated_at,market) VALUES(?,?,?,?,?,?,?,?,?)",
                    (job_id, company, title, location, url, description, now(), now(), market),
                )
                for key in identities:
                    db.execute(
                        "INSERT INTO posting_identities VALUES(?,?)", (key, job_id)
                    )
                self.record_event(
                    db, "job_saved", job_id, company=company, title=title, url=url
                )
        except sqlite3.IntegrityError:
            raise ValueError("This posting is already saved") from None
        self.export_tracking()
        return self.get_job(job_id)

    def update_job(self, job_id, status, notes=None, application_date=None):
        old = self.get_job(job_id)
        if old.get("deleted_at"):
            raise ValueError("Restore this removed role before updating it")
        if status not in STATUSES:
            raise ValueError("Unknown application status")
        with self.connect() as db:
            has_evidence = bool(
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='application_evidence'"
                ).fetchone()
            ) and bool(
                db.execute(
                    "SELECT 1 FROM application_evidence WHERE job_id=?", (job_id,)
                ).fetchone()
            )
        if status == "applied" and not (
            application_date or old["application_date"] or has_evidence
        ):
            raise ValueError(
                "Record the actual application date; preparing a resume is not an application"
            )
        notes = old["notes"] if notes is None else notes
        if application_date:
            try:
                date = datetime.strptime(application_date, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("Application date must be YYYY-MM-DD") from None
            if date > datetime.now().date():
                raise ValueError("Application date cannot be in the future")
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?,notes=?,application_date=?,updated_at=? WHERE id=?",
                (
                    status,
                    notes,
                    application_date or old["application_date"],
                    now(),
                    job_id,
                ),
            )
            self.record_event(
                db,
                "progress_updated",
                job_id,
                before=old,
                after=dict(
                    db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                ),
            )
        self.export_tracking()
        return self.get_job(job_id)

    def remove_job(self, job_id, reason="Not suitable"):
        old = self.get_job(job_id)
        if old.get("deleted_at"):
            return old
        if old["status"] not in {"saved", "prepared"}:
            raise ValueError(
                "Only saved or prepared roles can be removed. Keep applied, interview, offer, rejected and withdrawn records in application history."
            )
        reason = str(reason or "Not suitable").strip()[:500]
        stamp = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET deleted_at=?,deletion_reason=?,updated_at=? WHERE id=?",
                (stamp, reason, stamp, job_id),
            )
            self.record_event(
                db,
                "job_removed",
                job_id,
                company=old["company"],
                title=old["title"],
                reason=reason,
                recoverable=True,
            )
        self.export_tracking()
        return self.get_job(job_id)

    def restore_job(self, job_id):
        old = self.get_job(job_id)
        if not old.get("deleted_at"):
            return old
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE jobs SET deleted_at=NULL,deletion_reason='',updated_at=? WHERE id=?",
                (now(), job_id),
            )
            self.record_event(
                db,
                "job_restored",
                job_id,
                company=old["company"],
                title=old["title"],
            )
        self.export_tracking()
        return self.get_job(job_id)

    def export_tracking(self):
        jobs = self.jobs()

        def cell(value):
            return str(value or "").replace("|", "/").replace("\n", " ")

        header = "# Active opportunity pipeline\n\nGenerated from data/career.db. Do not edit this projection directly.\n\n"
        rows = [
            "| Company | Role | Location | Status | Posting |",
            "|---|---|---|---|---|",
        ]
        rows += [
            "| "
            + " | ".join(
                cell(j[k]) for k in ("company", "title", "location", "status", "url")
            )
            + " |"
            for j in jobs
        ]
        atomic_write(self.root / "data/pipeline.md", header + "\n".join(rows) + "\n")
        with self.connect() as db:
            has_mail = bool(
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='mail_evidence'"
                ).fetchone()
            )
            confirmed = (
                {
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT job_id FROM mail_evidence WHERE state='confirmed'"
                    )
                }
                if has_mail
                else set()
            )
        applied = [j for j in jobs if j["application_date"] or j["id"] in confirmed]
        lines = [
            "# Application tracker — " + self.candidate_name(),
            "",
            "Generated from data/career.db; user-confirmed submissions and linked email evidence establish submission. Blank dates are unknown, not inferred.",
            "",
            "| Company | Role | Applied | Status |",
            "|---|---|---|---|",
        ]
        lines += [
            "| "
            + " | ".join(
                cell(j[k]) for k in ("company", "title", "application_date", "status")
            )
            + " |"
            for j in applied
        ]
        atomic_write(self.root / "data/application-tracker.md", "\n".join(lines) + "\n")
        self.export_history()
        companies = sorted({j["company"] for j in applied}, key=str.lower)
        atomic_write(
            self.root / "data/applied-companies.md",
            "# Applied companies — " + self.candidate_name() + "\n\nGenerated from recorded dates and confirmed email evidence in data/career.db. No preparation implies an application.\n\n"
            + "\n".join("- " + cell(c) for c in companies)
            + "\n",
        )
        try:
            self.export_summary(jobs)
            self.export_signature_projects()
        except Exception:  # noqa: BLE001 - the front page must never block a save
            pass

    def export_signature_projects(self):
        """data/signature-projects.md: who owns which signature project. Generated from signature_assignments."""
        def cell(value):
            return str(value or "").replace("|", "/").replace("\n", " ")

        with self.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='signature_assignments'").fetchone():
                return
            rows = [dict(r) for r in db.execute(
                "SELECT s.company_key, s.project_id, s.assigned_at, j.company, j.title FROM signature_assignments s "
                "LEFT JOIN jobs j ON j.id = s.job_id ORDER BY s.assigned_at"
            )]
        titles = {p["id"]: (p.get("resume_content") or {}).get("title", p["id"]) for p in self.evidence().get("projects", [])}
        owner = {r["project_id"]: r for r in rows}
        pools = {}
        for track in self.profile().get("role_tracks", []):
            for project_id in track.get("signature_pool") or []:
                pools.setdefault(project_id, []).append(str(track.get("code")))
        lines = [
            "# Signature project registry",
            "",
            "Generated from data/career.db (signature_assignments). Do not edit; Resume Studio and prepare keep it current.",
            "",
            "Every resume leads Projects with exactly one **signature project**, chosen for that employer alone.",
            "A signature project is never reused across companies; it may still appear as the supporting project.",
            "If nothing unused fits, the strongest real project ships and a build-now spec goes into that company's study-plan.md.",
            "",
            "## Assigned",
            "",
            "| # | Company | Role | Signature project | Assigned |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {n} | {cell(r['company'] or r['company_key'])} | {cell(r['title'] or '(cleared by a fresh start)')} | "
            f"{cell(r['project_id'])} — {cell(titles.get(r['project_id']))} | {cell(str(r['assigned_at'])[:10])} |"
            for n, r in enumerate(rows, 1)
        ] or ["| – | none yet | | | |"]
        lines += ["", "## Pool", "", "| Project | Title | Tracks (signature pool) | Status |", "|---|---|---|---|"]
        for project_id, title in titles.items():
            used = owner.get(project_id)
            status = f"signature for {cell(used['company'] or used['company_key'])}" if used else "free"
            lines.append(f"| {project_id} | {cell(title)} | {', '.join(pools.get(project_id, [])) or 'supporting only'} | {status} |")
        atomic_write(self.root / "data/signature-projects.md", "\n".join(lines) + "\n")

    def export_summary(self, jobs=None):
        """data/output/SUMMARY.md: the one page the candidate reads. Generated; never hand-edited."""
        jobs = jobs if jobs is not None else self.jobs()
        sys.path.insert(0, str(self.app_root))
        from backend.services import reapply
        from backend.services.sponsorship import TIER_RANK

        def cell(value):
            return str(value or "").replace("|", "/").replace("\n", " ")

        with self.connect() as db:
            has_excluded = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='excluded_postings'").fetchone())
            excluded = [dict(r) for r in db.execute("SELECT company,title,url,reason_label,sentence,source,excluded_at FROM excluded_postings WHERE restored_at IS NULL ORDER BY excluded_at DESC")] if has_excluded else []
            has_assess = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='resume_assessments'").fetchone())
            # Latest assessment per job; its "missing_unsupported" list is the honest gap list.
            assessments = [dict(r) for r in db.execute(
                "SELECT job_id, details FROM resume_assessments WHERE id IN (SELECT MAX(id) FROM resume_assessments GROUP BY job_id)"
            )] if has_assess else []
            last = db.execute("SELECT action AS kind, occurred_at AS created_at FROM activity ORDER BY occurred_at DESC LIMIT 1").fetchone()
        open_states = {"saved", "prepared", "applied", "interview", "offer"}
        ranked = sorted(jobs, key=lambda j: (TIER_RANK.get(j.get("sponsor_tier") or "C", 4), j["created_at"]))
        lines = [f"# {self.candidate_name()}'s job search — front page", "",
                 f"Last run: {cell(last['kind']) if last else 'nothing yet'} at {cell(last['created_at']) if last else ''}. Generated from data/career.db; open the dashboard to act on anything here.", ""]
        lines += ["## Your resumes", "", "| # | Company | Role | Folder | Tier | Status | Apply link |", "|---|---|---|---|---|---|---|"]
        prepared = [j for j in ranked if j.get("folder")]
        lines += [f"| {n} | {cell(j['company'])} | {cell(j['title'])} | {cell(Path(j['folder']).name)} | {cell(j.get('sponsor_tier') or 'C')} | {cell(j['status'])} | {cell(j['url'])} |" for n, j in enumerate(prepared, 1)] or ["| – | none yet | | | | | |"]
        lines += ["", "## Pipeline", "", "| Company | Role | Status | Applied | Days quiet |", "|---|---|---|---|---|"]
        pipeline = [j for j in ranked if j["status"] in {"applied", "interview", "offer", "ghosted"}]
        lines += [f"| {cell(j['company'])} | {cell(j['title'])} | {cell(j['status'])} | {cell(j.get('application_date'))} | {reapply.quiet_days(j) if j['status'] == 'applied' else ''} |" for j in pipeline] or ["| – | nothing out yet | | | |"]
        lines += ["", "## Companies found, no resume yet", "", "| Company | Role | Location | Tier | Why this tier | Link |", "|---|---|---|---|---|---|"]
        found = [j for j in ranked if not j.get("folder") and j["status"] in open_states]
        lines += [f"| {cell(j['company'])} | {cell(j['title'])} | {cell(j['location'])} | {cell(j.get('sponsor_tier') or 'C')} | {cell((j.get('sponsor_evidence') or {}).get('label'))} | {cell(j['url'])} |" for j in found] or ["| – | press Start search on Daily Search | | | | |"]
        lines += ["", "## Excluded", "", "Cut by the sponsorship gate. The sentence that triggered each one is shown so a wrong call can be restored from the dashboard.", "",
                  "| Company | Role | Why | The sentence | Found by |", "|---|---|---|---|---|"]
        lines += [f"| {cell(e['company'])} | {cell(e['title'])} | {cell(e['reason_label'])} | \"{cell(e['sentence'])}\" | {cell(e['source'])} |" for e in excluded] or ["| – | nothing excluded | | | |"]
        lines += ["", "## What to study this week", ""]
        open_ids = {j["id"] for j in jobs if j["status"] in open_states}
        counts = {}
        for a in assessments:
            if a["job_id"] not in open_ids:
                continue
            try:
                details = json.loads(a["details"]) if isinstance(a["details"], str) else (a["details"] or {})
            except json.JSONDecodeError:
                continue
            for gap in details.get("missing_unsupported") or []:
                key = cell(gap)[:80]
                counts[key] = counts.get(key, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        lines += [f"- {text} — needed by {n} open application{'s' if n != 1 else ''}" for text, n in top] or ["- Run Build & score on a resume; the gaps it finds appear here, ranked by how many open applications need them."]
        lines += ["", "> The wall: anything listed under study never goes on a resume until it is learned and written into data/context/."]
        (self.root / "data/output").mkdir(parents=True, exist_ok=True)
        atomic_write(self.root / "data/output/SUMMARY.md", "\n".join(lines) + "\n")

    def rank_projects(self, description):
        target = tokens(description)
        target_text = description.casefold()
        # Domains worth a bonus when the JD and a project share them; a profile may name its own.
        domains = tuple((self.profile().get("scoring") or {}).get("project_domains") or PROJECT_DOMAINS)
        out = []
        for p in self.evidence()["projects"]:
            if not p.get("resume_content") or p.get("status") in {"hold", "missing"}:
                continue
            content = p.get("resume_content", {})
            evidence_text = " ".join(
                [
                    content.get("title", ""),
                    content.get("context", ""),
                    *content.get("bullets", []),
                ]
            )
            overlap = sorted(target & tokens(evidence_text))
            problems = set().union(*(tokens(value) for value in p.get("problem_patterns", [])))
            technologies = set().union(*(tokens(value) for value in p.get("technologies", [])))
            roles = " ".join(p.get("role_tracks", []))
            problem_score = min(35, round(35 * len(target & problems) / max(1, min(8, len(problems)))))
            skill_score = min(30, round(30 * len(target & technologies) / max(1, min(8, len(technologies)))))
            evidence_score = 20 if p.get("status") == "confirmed" else 15 if p.get("status") == "user_reported" else 8
            domain_score = 10 if any(
                term in target_text and term in evidence_text.casefold()
                for term in domains
            ) else 5 if target & tokens(roles) else 0
            recent_score = 5 if re.search(r"202[4-6]|current|recent", str(p.get("date_context", "")), re.I) else 3
            weighted = problem_score + skill_score + evidence_score + domain_score + recent_score
            out.append(
                {
                    "id": p["id"],
                    "title": content.get("title", p.get("name", p["id"])),
                    "matched_terms": overlap,
                    "match_count": weighted,
                    "weighted_score": weighted,
                    "score_components": {
                        "problem_similarity": problem_score,
                        "required_skill_evidence": skill_score,
                        "evidence_strength": evidence_score,
                        "domain_stakeholder_similarity": domain_score,
                        "recency": recent_score,
                    },
                    "context": content.get("context", ""),
                    "bullets": content.get("bullets", []),
                    "caveats": p.get("prohibited", []),
                    "status": p.get("status"),
                }
            )
        return sorted(out, key=lambda p: (-p["weighted_score"], p["id"]))

    def screen(self, job):
        """Title/location screen. Not an eligibility decision; the sponsorship gate owns that."""
        sys.path.insert(0, str(self.app_root))
        from backend.countries import market_for_location, pack_for
        from backend.job_quality import SENIORITY_BLOCK, ProfileRules, years_required
        title = job["title"]
        issues = []
        rules = ProfileRules.of(self.root)
        if rules.block_seniority and SENIORITY_BLOCK.search(title):
            issues.append("Seniority in the title is outside this profile's configured target level.")
        years = years_required(job.get("description", ""))
        cap = rules.max_years
        if years and cap and years > cap:
            issues.append(f"The posting asks for {years}+ years; the profile caps at {cap}.")
        market = market_for_location(self.root, job.get("location", ""), requested=job.get("market"))
        pack = pack_for(self.root, market)
        if not pack.location_ok(job.get("location", "")):
            issues.append(pack.text("location_screen"))
        return {
            "decision": "review_required",
            "concerns": issues,
            "note": "A title/location screen is not an eligibility decision. The sponsorship gate and the full requirements decide.",
        }

    def prepare(self, job_id, project_id=None):
        with self.connect() as db:
            has_knowledge = bool(
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='knowledge'"
                ).fetchone()
            )
            if (
                has_knowledge
                and db.execute(
                    "SELECT 1 FROM knowledge WHERE review_state='user_updated' LIMIT 1"
                ).fetchone()
            ):
                raise ValueError(
                    "Reconcile edited profile entries with the resume evidence registry before preparing a draft."
                )
        job = self.get_job(job_id)
        if job.get("deleted_at"):
            raise ValueError("Restore this removed role before preparing documents")
        if job.get("record_source") == "gmail":
            raise ValueError(
                "Add the full job description and posting URL before preparing a resume"
            )
        from backend.resume_contract import contract_for
        contract = contract_for(self.root, job.get("market") or None)
        required_projects = contract.required_selected_projects
        ranked = self.rank_projects(job["title"] + " " + job["description"])
        if len(ranked) < required_projects:
            raise ValueError(f"This resume contract needs {required_projects} registered, resume-ready project(s)")
        warnings = []
        pid = (project_id or self.choose_signature_project(job, ranked, warnings)) if required_projects else None
        project = next(
            (
                p
                for p in self.evidence()["projects"]
                if p["id"] == pid
                and bool(p.get("resume_content"))
                and p.get("status") not in {"hold", "missing"}
            ),
            None,
        ) if pid else None
        if required_projects and not project:
            raise ValueError("Select a registered, resume-ready project")
        # A fresh version preserves previous edits and reviews.
        out = self.root / "data/output/applications"
        out.mkdir(parents=True, exist_ok=True)
        folder = self.new_application_folder(job)
        source = (self.root / "data/templates/resume-base.tex").read_text(encoding="utf-8")
        if required_projects:
            source = self._replace_first_project(source, project)
        from backend.services.resume_projects import install_project
        second = next((p for p in ranked if p['id'] != pid), None) if required_projects >= 2 else None
        if required_projects >= 2:
            if not second:
                raise ValueError('Two distinct resume-ready projects are required')
            source = install_project(source, second, second=True)
        snapshot = f"# {job['company']} — {job['title']}\n\nLocation: {job['location']}\nSource: {job['url']}\nSaved: {now()}\nVerification: not verified; user-supplied snapshot.\n\n{job['description']}\n"
        snapshot_path = folder / "job-description.md"
        snapshot_path.write_text(snapshot, encoding="utf-8")
        (folder / "resume.tex").write_text(source, encoding="utf-8")
        ids = sorted({i for m in re.finditer(r"(?m)^\s*% EVIDENCE:\s*(.+)$", source) for i in m[1].split()})
        mapping = {
            "candidate_revision": self.evidence()["candidate_revision"],
            "job_id": job_id,
            "role_eligible": False,
            "job_snapshot_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
            "supported_requirement_coverage": 0,
            "requirements": [],
            "company_problem": {
                "statement": "Pending review of the saved job description.",
                "source_url": job["url"],
                "published_or_accessed": now()[:10],
                "evidence_class": "explicit",
                "confidence": "low",
            },
            "selected_project_id": pid,
            "selected_project_ids": [value for value in (pid, second["id"] if second else None) if value],
            "selected_project_reason": "Draft selection from approved evidence; review the business problem and full job requirements.",
            "resume_claim_ids": ids,
            "held_claims_used": [],
        }
        (folder / "evidence-map.yml").write_text(yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True), encoding="utf-8")
        (folder / "evaluation.md").write_text(
            "# Review required\n\n" + json.dumps(self.screen(job), indent=2)
            + "\n\nThis draft uses the registered evidence available for this profile. Review the full JD, candidate fit and evidence map before release.\n"
            + ("".join("\n> " + w for w in warnings) + "\n" if warnings else ""), encoding="utf-8")
        (folder / "company-research.md").write_text(
            "# Research pending\n\nVerify the original posting and public employer sources. No live-job claim has been generated.\n",
            encoding="utf-8")
        (folder / "study-plan.md").write_text(
            "# Study plan pending\n\nRun the study-plan agent after the match check. Unlearned skills stay off the resume until evidenced in the profile.\n",
            encoding="utf-8")
        relative = str(folder.relative_to(self.root))
        with self.connect() as db:
            db.execute("UPDATE jobs SET folder=?, selected_project_id=?, status=CASE WHEN status='saved' THEN 'prepared' ELSE status END, updated_at=? WHERE id=?",
                       (relative, pid, now(), job_id))
            if pid:
                db.execute("INSERT OR REPLACE INTO signature_assignments(company_key, project_id, job_id, assigned_at) VALUES(?,?,?,?)",
                           (company_key(job["company"]), pid, job_id, now()))
            self.record_event(db, "draft_prepared", job_id, folder=relative, project_id=pid,
                              review_required=True, warnings=warnings)
        self.export_tracking()
        return {"folder": relative, "project_id": pid, "review_required": True, "warnings": warnings}

    @staticmethod
    def _replace_first_project(source, project):
        pid = project["id"]
        content = project["resume_content"]
        values = {
            "SelectedProjectID": pid,
            "SelectedProjectTitle": content["title"],
            "SelectedProjectContext": content["context"],
        }
        bullets = content["bullets"]
        values.update(
            {
                f"SelectedProjectBullet{n}": b
                for n, b in zip(("One", "Two", "Three"), bullets)
            }
        )
        for name, value in values.items():
            pattern = (
                r"(% EVIDENCE: [^\n]+\n)(\\newcommand\{\\" + name + r"\}\{)[^\n]*(\})"
            )
            source, count = re.subn(
                pattern,
                lambda m: "% EVIDENCE: " + pid + "\n" + m[2] + tex_escape(value) + m[3],
                source,
                count=1,
            )
            if count != 1:
                raise ValueError("Base template content slot is missing: " + name)
        if len(bullets) == 2:
            source = re.sub(
                r"% EVIDENCE: [^\n]+\n\\newcommand\{\\SelectedProjectBulletThree\}\{[^\n]*\}\n",
                "",
                source,
            )
            source = re.sub(
                r"\s*% EVIDENCE: [^\n]+\n\s*\\item \\SelectedProjectBulletThree\n",
                "\n",
                source,
            )
        a = source.index("% SELECTED_PROJECT_BLOCK_START")
        b = source.index("% SELECTED_PROJECT_BLOCK_END")
        source = (
            source[:a]
            + re.sub(r"% EVIDENCE: [^\n]+", "% EVIDENCE: " + pid, source[a:b])
            + source[b:]
        )
        return source

    def choose_signature_project(self, job, ranked, warnings):
        """Choose the strongest project allowed to lead the Projects section.

        Some profiles reserve each lead project for one company; others permit
        reuse when it is the strongest truthful match for multiple employers.
        """
        unique = bool((self.profile().get("project_selection") or {}).get("signature_unique_per_company", False))
        with self.connect() as db:
            taken = {
                row["project_id"]: row["company_key"]
                for row in db.execute("SELECT company_key, project_id FROM signature_assignments")
            } if unique else {}
        mine = company_key(job["company"])
        # Coursework-style projects the registry marks signature_eligible: false may only support.
        barred = {p["id"] for p in self.evidence().get("projects", []) if p.get("signature_eligible") is False}
        for project in ranked:
            if project["id"] in barred:
                continue
            owner = taken.get(project["id"])
            if owner is None or owner == mine:
                return project["id"]
        # Everything that fits is already someone else's signature: ship the strongest real project and say so.
        warnings.append(
            "No unused signature project fits this company; the strongest real project was reused. "
            "Write a build-now spec into study-plan.md."
        )
        return next((p["id"] for p in ranked if p["id"] not in barred), ranked[0]["id"])

    def new_application_folder(self, job):
        """data/output/applications/<First>_<Last>_<Company>_<NN>, NN counting per company."""
        out = self.root / "data/output/applications"
        out.mkdir(parents=True, exist_ok=True)
        first = self.profile()["candidate"]["full_name"].split()[0]
        last = self.profile()["candidate"]["full_name"].split()[-1]
        company = re.sub(r"[^A-Za-z0-9]+", "", job["company"]) or "Company"
        prefix = f"{first}_{last}_{company}_"
        existing = [p.name for p in out.glob(prefix + "*") if p.is_dir()]
        numbers = [int(m[1]) for name in existing if (m := re.match(re.escape(prefix) + r"(\d+)", name))]
        folder = out / f"{prefix}{(max(numbers) + 1 if numbers else 1):02d}"
        folder.mkdir()
        return folder

    def current_folder(self, job_id):
        job = self.get_job(job_id)
        if not job["folder"]:
            raise ValueError("Prepare a draft first")
        return safe_child(
            self.root / "data/output", str(Path(job["folder"]).relative_to("data/output"))
        )

    def compile_preview(self, job_id):
        # Compile in a temporary directory; only publish a preview with the contract's page count.
        folder = self.current_folder(job_id)
        sys.path.insert(0, str(self.app_root / "backend/scripts"))
        from validate_resume import compile_latex, inspect_pdf

        with tempfile.TemporaryDirectory(prefix="career-compile-") as temp:
            pdf, log, error = compile_latex(folder / "resume.tex", Path(temp))
            if pdf is None:
                raise ValueError((error or log)[-5000:])
            report = inspect_pdf(pdf, folder / "resume-preview")
            from backend.resume_contract import contract_for
            expected_pages = contract_for(self.root, self.get_job(job_id).get("market") or None).pages
            if report["page_count"] != expected_pages:
                raise ValueError(
                    f"Expected {expected_pages} page(s); found {report['page_count']}. Cut content before release; never shrink fonts or margins."
                )
            (folder / "resume.pdf").write_bytes(pdf.read_bytes())
        atomic_write(
            folder / "preview.json",
            json.dumps(
                {
                    "review_required": True,
                    "page_count": report["page_count"],
                    "source_sha256": hashlib.sha256(
                        (folder / "resume.tex").read_bytes()
                    ).hexdigest(),
                    "created_at": now(),
                },
                indent=2,
            )
            + "\n",
        )
        return {
            "folder": str(folder.relative_to(self.root)),
            "page_count": report["page_count"],
            "review_required": True,
        }

    def validate(self, job_id):
        folder = self.current_folder(job_id)
        result = subprocess.run(
            [
                sys.executable,
                str(self.app_root / "backend/scripts/validate_resume.py"),
                "--workspace",
                str(self.root),
                "--market",
                self.get_job(job_id).get("market") or "",
                str(folder / "resume.tex"),
                "--compile",
                "--output",
                str(folder / "resume.pdf"),
                "--render-dir",
                str(folder / "resume-preview"),
                "--qa-json",
                str(folder / "qa.json"),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        qa = (
            json.loads((folder / "qa.json").read_text(encoding="utf-8"))
            if (folder / "qa.json").exists()
            else {"status": "FAIL", "failures": [result.stderr or result.stdout]}
        )
        return qa

    def artifacts(self):
        items = []
        for p in sorted((self.root / "data/output").glob("**/*.pdf")):
            qa_path = p.parent / "qa.json"
            qa = {}
            if qa_path.exists():
                try:
                    qa = json.loads(qa_path.read_text(encoding="utf-8"))
                except ValueError:
                    pass
            source = (
                self.root / "data/templates/resume-base.tex"
                if p.parent.name == "base"
                else p.parent / "resume.tex"
            )
            stale = (
                not source.exists()
                or qa.get("source_sha256")
                != hashlib.sha256(source.read_bytes()).hexdigest()
                or qa.get("pdf_sha256") != hashlib.sha256(p.read_bytes()).hexdigest()
                or qa.get("candidate_revision") != self.evidence()["candidate_revision"]
            )
            stale = (
                stale
                or qa.get("registry_sha256")
                != hashlib.sha256(
                    (self.root / "data/context/evidence.yml").read_bytes()
                ).hexdigest()
                or qa.get("profile_sha256")
                != hashlib.sha256(
                    (self.root / "data/config/profile.yml").read_bytes()
                ).hexdigest()
            )
            for filename, expected in qa.get("preview_sha256", {}).items():
                preview = p.parent / "resume-preview" / filename
                stale = (
                    stale
                    or not preview.is_file()
                    or hashlib.sha256(preview.read_bytes()).hexdigest() != expected
                )
            if p.parent.name != "base":
                mapping = p.parent / "evidence-map.yml"
                jd = p.parent / "job-description.md"
                stale = (
                    stale
                    or not mapping.is_file()
                    or qa.get("evidence_map", {}).get("sha256")
                    != hashlib.sha256(mapping.read_bytes()).hexdigest()
                )
                stale = (
                    stale
                    or not jd.is_file()
                    or qa.get("evidence_map", {}).get("job_snapshot_sha256")
                    != hashlib.sha256(jd.read_bytes()).hexdigest()
                )
            items.append(
                {
                    "path": str(p.relative_to(self.root / "data/output")),
                    "name": p.name,
                    "status": (
                        "REVIEW_REQUIRED"
                        if stale
                        else qa.get("status", "REVIEW_REQUIRED")
                    ),
                    "release_ready": bool(qa.get("release_ready")) and not stale,
                }
            )
        return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="profile ID (default: last opened profile)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("projects")
    sub.add_parser("jobs")
    sub.add_parser("activity")
    sub.add_parser("export")
    notes = sub.add_parser("notes")
    notes.add_argument("--file", type=Path, required=True)
    add = sub.add_parser("add")
    add.add_argument("--file", type=Path, required=True)
    update = sub.add_parser("update")
    update.add_argument("job_id")
    update.add_argument("--status", choices=sorted(STATUSES), required=True)
    update.add_argument("--notes")
    update.add_argument("--application-date")
    prep = sub.add_parser("prepare")
    prep.add_argument("job_id")
    prep.add_argument("--project")
    for name in ("preview", "validate"):
        sub.add_parser(name).add_argument("job_id")
    args = parser.parse_args()
    from backend.profiles import store
    profiles = store()
    profile_id = args.profile or profiles.last_used()
    if not profile_id:
        parser.error("No profile exists yet. Run  career setup --name \"Full Name\"  or create one in the dashboard.")
    w = Workspace(profiles.root_for(profile_id))
    if args.command == "status":
        result = {
            "candidate": w.profile()["candidate"]["full_name"],
            "jobs": len(w.jobs()),
            "artifacts": w.artifacts(),
        }
    elif args.command == "jobs":
        result = w.jobs()
    elif args.command == "activity":
        result = w.activity()
    elif args.command == "export":
        w.export_tracking()
        result = {"exported": True}
    elif args.command == "notes":
        result = w.save_profile_notes(args.file.read_text(encoding="utf-8"))
    elif args.command == "add":
        # Same path as the dashboard: sponsorship gate, then never-re-apply, then save.
        from backend.services.workspace_v2 import CareerServices
        result = CareerServices(w).add_posting(json.loads(args.file.read_text(encoding="utf-8")), source="cli")
    elif args.command == "update":
        result = w.update_job(
            args.job_id, args.status, args.notes, args.application_date
        )
    elif args.command == "projects":
        result = w.rank_projects("")
    elif args.command == "prepare":
        result = w.prepare(args.job_id, args.project)
    elif args.command == "preview":
        result = w.compile_preview(args.job_id)
    else:
        result = w.validate(args.job_id)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
