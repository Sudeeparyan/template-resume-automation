"""Interviews the person records for an application, and calendar files for them.

Only what the person enters is stored: when, which stage, where, their notes. Times are read
in the profile's own time zone (Europe/Dublin by default), kept with their UTC offset as
entered, and also as UTC so ordering and the .ics files stay right across daylight-saving
changes. The app never writes to anyone's calendar: the .ics file is for the person to open.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STAGES = ("screen", "technical", "onsite", "final", "other")
STAGE_LABEL = {
    "screen": "Phone or video screen",
    "technical": "Technical interview",
    "onsite": "On-site interview",
    "final": "Final interview",
    "other": "Interview",
}
COLUMNS = "i.id,i.job_id,i.stage,i.starts_at,i.starts_utc,i.duration_min,i.where_text,i.notes,i.created_at,i.updated_at"


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_start(value: str, zone: str) -> datetime:
    """A start time from the form ("2026-11-25T14:00", local) or with an explicit offset."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Give the interview's date and time")
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Interview time must look like 2026-11-25T14:00") from None
    tz = ZoneInfo(zone)
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)


class Interviews:
    def __init__(self, services):
        self.s = services
        self.w = services.w

    # ---- reading ---------------------------------------------------------------------------
    def list(self, job_id: str | None = None, start: str | None = None, end: str | None = None) -> list[dict]:
        """Recorded interviews, soonest first, each with its company and role."""
        where, args = ["i.deleted_at IS NULL", "j.deleted_at IS NULL"], []
        if job_id:
            where.append("i.job_id=?")
            args.append(job_id)
        if start:
            where.append("i.starts_utc>=?")
            args.append(_utc(parse_start(start, self.w.timezone)))
        if end:
            where.append("i.starts_utc<?")
            args.append(_utc(parse_start(end, self.w.timezone)))
        with self.w.connect() as db:
            rows = db.execute(
                f"SELECT {COLUMNS}, j.company, j.title FROM interviews i JOIN jobs j ON j.id=i.job_id "
                f"WHERE {' AND '.join(where)} ORDER BY i.starts_utc",
                args,
            ).fetchall()
        return [self._view(dict(row)) for row in rows]

    def upcoming(self, days: int = 14, now: datetime | None = None) -> list[dict]:
        """Interviews from an hour ago to `days` ahead: one indexed query, cheap to poll."""
        now = now or datetime.now(timezone.utc)
        with self.w.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='interviews'").fetchone():
                return []
            rows = db.execute(
                f"SELECT {COLUMNS}, j.company, j.title FROM interviews i JOIN jobs j ON j.id=i.job_id "
                "WHERE i.deleted_at IS NULL AND j.deleted_at IS NULL AND i.starts_utc>=? AND i.starts_utc<? "
                "ORDER BY i.starts_utc LIMIT 50",
                (_utc(now - timedelta(hours=1)), _utc(now + timedelta(days=days))),
            ).fetchall()
        return [self._view(dict(row)) for row in rows]

    def recently_ended(self, days: int = 3, now: datetime | None = None) -> list[dict]:
        """Interviews that ended in the last `days` days (for thank-you notes)."""
        now = now or datetime.now(timezone.utc)
        with self.w.connect() as db:
            rows = db.execute(
                f"SELECT {COLUMNS}, j.company, j.title FROM interviews i JOIN jobs j ON j.id=i.job_id "
                "WHERE i.deleted_at IS NULL AND j.deleted_at IS NULL AND i.starts_utc>=? AND i.starts_utc<? "
                "ORDER BY i.starts_utc DESC",
                (_utc(now - timedelta(days=days)), _utc(now)),
            ).fetchall()
        out = []
        for row in rows:
            view = self._view(dict(row))
            ends = datetime.fromisoformat(view["starts_utc"]) + timedelta(minutes=view["duration_min"])
            if ends <= now:
                out.append(view)
        return out

    def get(self, interview_id: str) -> dict:
        with self.w.connect() as db:
            row = db.execute(
                f"SELECT {COLUMNS}, j.company, j.title FROM interviews i JOIN jobs j ON j.id=i.job_id "
                "WHERE i.id=? AND i.deleted_at IS NULL",
                (interview_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Interview not found")
        return self._view(dict(row))

    # ---- writing ---------------------------------------------------------------------------
    def add(self, job_id: str, data: dict) -> dict:
        job = self.w.get_job(job_id)
        if job.get("deleted_at"):
            raise ValueError("Restore this removed role before adding an interview")
        fields = self._fields(data)
        interview_id = uuid.uuid4().hex[:12]
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.w.connect() as db:
            db.execute(
                "INSERT INTO interviews(id,job_id,stage,starts_at,starts_utc,duration_min,where_text,notes,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (interview_id, job_id, fields["stage"], fields["starts_at"], fields["starts_utc"],
                 fields["duration_min"], fields["where_text"], fields["notes"], stamp, stamp),
            )
            self.w.record_event(db, "interview_added", job_id, interview_id=interview_id,
                                stage=fields["stage"], starts_at=fields["starts_at"])
        self.s.export_state()
        return self.get(interview_id)

    def update(self, interview_id: str, data: dict) -> dict:
        old = self.get(interview_id)
        fields = self._fields(data)
        with self.w.connect() as db:
            db.execute(
                "UPDATE interviews SET stage=?,starts_at=?,starts_utc=?,duration_min=?,where_text=?,notes=?,updated_at=? WHERE id=?",
                (fields["stage"], fields["starts_at"], fields["starts_utc"], fields["duration_min"], fields["where_text"],
                 fields["notes"], datetime.now(timezone.utc).isoformat(timespec="seconds"), interview_id),
            )
            self.w.record_event(db, "interview_updated", old["job_id"], interview_id=interview_id,
                                before={k: old[k] for k in ("stage", "starts_at")},
                                after={k: fields[k] for k in ("stage", "starts_at")})
        self.s.export_state()
        return self.get(interview_id)

    def delete(self, interview_id: str) -> dict:
        old = self.get(interview_id)
        with self.w.connect() as db:
            db.execute("UPDATE interviews SET deleted_at=? WHERE id=?",
                       (datetime.now(timezone.utc).isoformat(timespec="seconds"), interview_id))
            self.w.record_event(db, "interview_removed", old["job_id"], interview_id=interview_id,
                                starts_at=old["starts_at"])
        self.s.export_state()
        return {"deleted": interview_id}

    def _fields(self, data: dict) -> dict:
        stage = str(data.get("stage") or "other")
        if stage not in STAGES:
            raise ValueError("Unknown interview stage")
        moment = parse_start(data.get("starts_at"), self.w.timezone)
        duration = int(data.get("duration_min") or 45)
        if not 5 <= duration <= 600:
            raise ValueError("Length must be between 5 minutes and 10 hours")
        return {
            "stage": stage,
            "starts_at": moment.isoformat(timespec="minutes"),
            "starts_utc": _utc(moment),
            "duration_min": duration,
            "where_text": str(data.get("where_text") or "").strip()[:500],
            "notes": str(data.get("notes") or "").strip()[:5000],
        }

    def _view(self, row: dict) -> dict:
        return {**row, "stage_label": STAGE_LABEL.get(row["stage"], "Interview")}

    # ---- calendar files --------------------------------------------------------------------
    def ics(self, interviews: list[dict]) -> str:
        """An RFC 5545 calendar with one event per interview, times in UTC."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Career Workspace//Interviews//EN",
                 "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
        for item in interviews:
            start = datetime.fromisoformat(item["starts_utc"])
            end = start + timedelta(minutes=item["duration_min"])
            description = f"{item['stage_label']} for {item['title']} at {item['company']}."
            if item.get("notes"):
                description += "\n\n" + item["notes"]
            lines += [
                "BEGIN:VEVENT",
                f"UID:{item['id']}@career-workspace.local",
                f"DTSTAMP:{stamp}",
                f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}",
                f"DTEND:{end.strftime('%Y%m%dT%H%M%SZ')}",
                "SUMMARY:" + escape(f"{item['stage_label']}: {item['title']} at {item['company']}"),
                "DESCRIPTION:" + escape(description),
            ]
            if item.get("where_text"):
                lines.append("LOCATION:" + escape(item["where_text"]))
            lines.append("END:VEVENT")
        lines.append("END:VCALENDAR")
        return "".join(fold(line) + "\r\n" for line in lines)


def escape(text: str) -> str:
    """RFC 5545 TEXT escaping: backslash, semicolon, comma and line breaks."""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))


def fold(line: str, limit: int = 75) -> str:
    """Fold a content line at 75 octets, never inside a UTF-8 character."""
    data = line.encode("utf-8")
    if len(data) <= limit:
        return line
    parts, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > (limit if not parts else limit - 1):
            parts.append(current)
            current, size = "", 0
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)
