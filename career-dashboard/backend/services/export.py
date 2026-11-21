"""Download everything: one JSON backup of this profile, and every tailored document as a zip.

The backup is an explicit allowlist of the person's own records: applications (removed ones
too, with the reason), their history, interviews, follow-up drafts, cover letters, goals,
email evidence the person reviewed (the excerpt and dates, never a full mailbox), profile
entries, the evidence registry and the profile settings. Keys, tokens, passwords and other
credentials are never included, even if a settings file holds one. Both files go to the
person's browser; nothing is uploaded anywhere.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "career-workspace-backup"
VERSION = 1
SECRET_KEY = re.compile(r"(api[_-]?key|secret|token|password|passwd|credential|cookie|session|private[_-]?key|auth[_-]?code)", re.I)


def scrub(value):
    """A copy without any field whose name says it is a secret."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if not SECRET_KEY.search(str(k))}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def _rows(db, sql: str) -> list[dict]:
    table = re.search(r"FROM (\w+)", sql).group(1)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return []
    return [dict(row) for row in db.execute(sql)]


def backup(services) -> dict:
    w = services.w
    with w.connect() as db:
        tables = {
            "interviews": _rows(db, "SELECT * FROM interviews WHERE deleted_at IS NULL ORDER BY starts_utc"),
            "follow_ups": _rows(db, "SELECT * FROM follow_ups ORDER BY id"),
            "cover_letters": _rows(db, "SELECT job_id,version,content,created_at FROM cover_letters ORDER BY job_id,version"),
            "application_evidence": _rows(db, "SELECT job_id,source,confirmed_at,submission_date FROM application_evidence"),
            "email_evidence": _rows(db, "SELECT job_id,company,role,kind,subject,sender,received_at,submission_date,excerpt,state "
                                        "FROM mail_evidence WHERE state != 'dismissed' ORDER BY received_at"),
            "profile_items": _rows(db, "SELECT id,kind,title,summary,data,source,review_state,deleted,updated_at FROM knowledge ORDER BY kind,title"),
        }
    return scrub({
        "format": FORMAT,
        "version": VERSION,
        "profile_id": Path(w.root).name,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Your own records from this career workspace. Credentials are never included.",
        "applications": w.jobs(include_deleted=True),
        "history": w.activity(limit=-1),
        "goals": services.goals(),
        "evidence_registry": w.evidence(),
        "configuration": w.profile(),
        **tables,
    })


def _part(text: str) -> str:
    """A folder-name-safe piece of a company or role name."""
    cleaned = re.sub(r"[^\w\- ]+", "", str(text or "")).strip().replace(" ", "-")
    return cleaned[:50] or "untitled"


def documents_zip(services) -> tuple[bytes, int]:
    """Every listed resume and cover letter, as <Company>_<Role>/<file>. Returns (zip, count)."""
    from career import safe_child

    output = Path(services.w.root) / "data/output"
    buffer = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        seen = set()
        for item in services.documents():
            folder = f"{_part(item.get('company'))}_{_part(item.get('title'))}"
            paths = [r["path"] for r in item.get("resumes") or []]
            if item.get("cover_letter"):
                paths.append(item["cover_letter"]["path"])
            for relative in paths:
                try:
                    source = safe_child(output, relative)
                except ValueError:
                    continue  # a path outside the output folder is never read
                if not source.is_file():
                    continue
                name = f"{folder}/{source.name}"
                if name in seen:
                    name = f"{folder}/{source.parent.name}-{source.name}"
                seen.add(name)
                archive.write(source, name)
                count += 1
    return buffer.getvalue(), count
