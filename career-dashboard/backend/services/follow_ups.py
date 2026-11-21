"""Follow-up and thank-you notes: when one is due, and a draft for the person to send.

Drafts only. The app never sends an email or contacts an employer: the person copies the
draft, sends it themselves, then may record "I sent it". Silence never changes an
application's status; a due follow-up is a suggestion, nothing more.

* A follow-up is due once an application has been quiet for QUIET_DAYS (anchored on the real
  application date), and again QUIET_DAYS after the last one they sent, at most
  MAX_FOLLOW_UPS times per application.
* A thank-you note is due for THANK_YOU_DAYS after a recorded interview ends.

The drafts use only the posting's company and title, the recorded dates and the person's
own name from their profile, so they can never state anything the person did not record.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from backend.services import reapply

QUIET_DAYS = 10
MAX_FOLLOW_UPS = 2
THANK_YOU_DAYS = 3
KINDS = ("follow_up", "thank_you")


def _human(day: str | None) -> str:
    try:
        value = date.fromisoformat(str(day)[:10])
    except ValueError:
        return str(day or "")
    return f"{value.day} {value.strftime('%B %Y')}"


def _table_ready(db) -> bool:
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='follow_ups'").fetchone())


class FollowUps:
    def __init__(self, services):
        self.s = services
        self.w = services.w

    def due(self, jobs: list[dict] | None = None, today: date | None = None, now: datetime | None = None) -> list[dict]:
        """What is due now, most overdue first. `jobs` lets the summary reuse its job list."""
        today = today or datetime.now(timezone.utc).date()
        jobs = jobs if jobs is not None else self.w.jobs()
        with self.w.connect() as db:
            if not _table_ready(db):
                return []
            rows = [dict(r) for r in db.execute(
                "SELECT id,job_id,kind,text,drafted_at,marked_sent_at FROM follow_ups ORDER BY id")]
        sent: dict[tuple[str, str], list[str]] = {}
        drafts: dict[tuple[str, str], dict] = {}
        for row in rows:
            key = (row["job_id"], row["kind"])
            if row["marked_sent_at"]:
                sent.setdefault(key, []).append(row["marked_sent_at"])
            else:
                drafts[key] = row
        out = []
        for job in jobs:
            if job.get("status") != "applied" or job.get("deleted_at"):
                continue
            quiet = reapply.quiet_days(job, today)
            done = sent.get((job["id"], "follow_up"), [])
            if quiet is None or quiet < QUIET_DAYS or len(done) >= MAX_FOLLOW_UPS:
                continue
            if done and (today - reapply._day(done[-1])).days < QUIET_DAYS:
                continue
            out.append({
                "job_id": job["id"], "company": job["company"], "title": job["title"], "kind": "follow_up",
                "days_quiet": quiet, "sent": len(done),
                "reason": (f"Applied {quiet} days ago, no reply recorded." if not done
                           else f"No reply recorded since your follow-up on {_human(done[-1])}."),
                "draft": drafts.get((job["id"], "follow_up")),
            })
        from backend.services.interviews import Interviews

        seen = set()
        for interview in Interviews(self.s).recently_ended(days=THANK_YOU_DAYS, now=now):
            job_id = interview["job_id"]
            thanked = sent.get((job_id, "thank_you"), [])
            if job_id in seen or any(stamp >= interview["starts_utc"] for stamp in thanked):
                continue
            seen.add(job_id)
            out.append({
                "job_id": job_id, "company": interview["company"], "title": interview["title"], "kind": "thank_you",
                "days_quiet": 0, "sent": len(thanked), "interview_id": interview["id"],
                "reason": f"{interview['stage_label']} on {_human(interview['starts_at'])}. A short thank-you is usual within a day or two.",
                "draft": drafts.get((job_id, "thank_you")),
            })
        return sorted(out, key=lambda item: (item["kind"] != "thank_you", -item["days_quiet"]))

    def draft(self, job_id: str, kind: str) -> dict:
        """Write (or rewrite) the draft for one job; it is stored until the person sends it."""
        if kind not in KINDS:
            raise ValueError("Unknown note kind")
        job = self.w.get_job(job_id)
        if job.get("deleted_at"):
            raise ValueError("Restore this removed role first")
        name = str((self.w.profile().get("candidate") or {}).get("full_name") or "").strip()
        with self.w.connect() as db:
            sent = db.execute(
                "SELECT COUNT(*) FROM follow_ups WHERE job_id=? AND kind=? AND marked_sent_at IS NOT NULL",
                (job_id, kind),
            ).fetchone()[0]
        text = self._text(job, kind, name, sent)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.w.connect() as db:
            db.execute("DELETE FROM follow_ups WHERE job_id=? AND kind=? AND marked_sent_at IS NULL", (job_id, kind))
            cursor = db.execute(
                "INSERT INTO follow_ups(job_id,kind,text,method,drafted_at) VALUES(?,?,?,'template',?)",
                (job_id, kind, text, stamp),
            )
            self.w.record_event(db, "follow_up_drafted", job_id, kind=kind, follow_up_id=cursor.lastrowid)
            draft_id = cursor.lastrowid
        return self.get(draft_id)

    def mark_sent(self, follow_up_id: int) -> dict:
        """The person's own record that they sent it. Never changes the application's status."""
        row = self.get(follow_up_id)
        if row["marked_sent_at"]:
            return row
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.w.connect() as db:
            db.execute("UPDATE follow_ups SET marked_sent_at=? WHERE id=?", (stamp, follow_up_id))
            self.w.record_event(db, "follow_up_marked_sent", row["job_id"], kind=row["kind"], follow_up_id=follow_up_id)
        self.s.export_state()
        return self.get(follow_up_id)

    def get(self, follow_up_id: int) -> dict:
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM follow_ups WHERE id=?", (follow_up_id,)).fetchone()
        if row is None:
            raise ValueError("Draft not found")
        return dict(row)

    def _text(self, job: dict, kind: str, name: str, sent_before: int) -> str:
        company, title = job["company"], job["title"]
        sign = f"Kind regards,\n{name}" if name else "Kind regards,"
        if kind == "thank_you":
            return (
                f"Subject: Thank you: {title} interview\n\n"
                "Dear Hiring Team,\n\n"
                f"Thank you for taking the time to speak with me about the {title} role at {company}. "
                "I enjoyed learning more about the team and the work, and our conversation confirmed my interest in the position.\n\n"
                "Please let me know if there is anything further I can provide.\n\n"
                f"{sign}"
            )
        when = f" on {_human(job.get('application_date'))}" if job.get("application_date") else ""
        if sent_before:
            return (
                f"Subject: {title} application\n\n"
                "Dear Hiring Team,\n\n"
                f"I am following up once more on my application for the {title} role at {company}, submitted{when}. "
                "I would still welcome the chance to discuss it. If the position has been filled, I would appreciate a short note so I can update my records.\n\n"
                "Thank you for your time.\n\n"
                f"{sign}"
            )
        return (
            f"Subject: Following up on my application for {title}\n\n"
            "Dear Hiring Team,\n\n"
            f"I applied for the {title} role at {company}{when}. I wanted to ask whether there is any update on the "
            "hiring timeline, and to say that I remain very interested in the position.\n\n"
            "Please let me know if I can share anything else to help with your review.\n\n"
            f"{sign}"
        )
