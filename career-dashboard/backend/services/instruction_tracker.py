"""Profile-scoped, durable Resume Studio instructions.

Explicit edit commands use no AI. Other requests remain in the thread for
clarification or optional AI interpretation; they never silently change facts.
"""

from __future__ import annotations

import re
import uuid

from backend.services.resume_layout import set_density


class InstructionTracker:
    def __init__(self, service, studio):
        self.s, self.w, self.studio = service, service.w, studio
        with self.w.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS instruction_messages(
                id TEXT PRIMARY KEY,
                job_id TEXT REFERENCES jobs(id),
                message TEXT NOT NULL,
                response TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL)"""
            )

    def history(self, job_id=None):
        if job_id is not None:
            self.w.get_job(job_id)
        with self.w.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM instruction_messages WHERE job_id IS NULL OR job_id=? ORDER BY created_at,rowid",
                    (job_id,),
                )
            ]

    def send(self, message, job_id=None, revision=None, request_id=None):
        message = message.strip()
        if not message or len(message) > 10000:
            raise ValueError("Enter 1 to 10,000 characters")
        key = request_id or uuid.uuid4().hex
        if len(key) > 100:
            raise ValueError("Invalid message ID")
        if job_id:
            self.w.get_job(job_id)
        with self.studio.lock:
            with self.w.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                old = db.execute("SELECT * FROM instruction_messages WHERE id=?", (key,)).fetchone()
                if old:
                    if old["message"] != message or old["job_id"] != job_id:
                        raise ValueError("Message ID already belongs to another request")
                    return dict(old)
                db.execute(
                    "INSERT INTO instruction_messages VALUES(?,?,?,?,?,?)",
                    (key, job_id, message, "Processing this instruction.", "processing", self.s.now()),
                )

            state = "needs_clarification"
            response = (
                "Saved your request. It has not changed the resume. You can give an explicit "
                "command such as 'summary: ...', 'skills: ...', 'project: ID', "
                "'font: 10.5', or 'note: ...', or use AI interpretation below."
            )
            field = re.fullmatch(
                r"(?:set\s+)?(summary|skills(?:\s+(?:languages|data|ml|cloud|test))?|coursework)\s*:\s*([\s\S]+)",
                message, re.I,
            )
            project = re.fullmatch(r"(second project|project)\s*:\s*([\w:-]+)", message, re.I)
            font = re.fullmatch(
                r"(?:set\s+)?(?:font|font size|body font)\s*:?\s*(auto|\d+(?:\.\d+)?)\s*(?:pt)?",
                message, re.I,
            )
            note = re.fullmatch(r"(experience|note)\s*:\s*([\s\S]+)", message, re.I)
            if field or project or font:
                if not job_id or revision is None:
                    response = "Saved your request. Select a job and open its draft before applying a resume instruction."
                else:
                    try:
                        if field:
                            target = re.sub(r"\s+", " ", field[1].lower())
                            from validate_resume import extract_zero_argument_macros
                            present = extract_zero_argument_macros(self.studio.get(job_id)["source"])
                            name = {
                                "summary": "ResumeSummary", "skills": "CoreSkills" if "CoreSkills" in present else "SkillsLanguages",
                                "skills languages": "SkillsLanguages",
                                "skills data": "SkillsData", "skills ml": "SkillsML", "skills cloud": "SkillsCloud",
                                "skills test": "SkillsTest", "coursework": "Coursework",
                            }[target]
                            draft = self.studio.save(job_id, revision, fields={name: field[2]})
                        elif project:
                            name = "second_project_id" if project[1].lower().startswith("second") else "project_id"
                            draft = self.studio.save(job_id, revision, **{name: project[2]})
                        else:
                            size = None if font[1].lower() == "auto" else float(font[1])
                            contract = self.studio.contract(job_id)
                            if size is not None and not contract.min_body_pt <= size <= contract.max_body_pt:
                                raise ValueError(
                                    f"Body font must be {contract.min_body_pt:g}–{contract.max_body_pt:g}pt "
                                    "for this profile's resume contract"
                                )
                            current = self.studio.get(job_id)
                            draft = self.studio.save(
                                job_id, revision,
                                source=set_density(current["source"], size) if size is not None else current["source"],
                            )
                            with self.w.connect() as db:
                                self.s.set_pref("resume_font:" + job_id, size, db)
                        state = "applied"
                        response = (
                            f"Applied to saved draft version {draft['revision']}. "
                            "Build the PDF to refresh it. The instruction and result are retained."
                        )
                    except ValueError as exc:
                        state, response = "needs_attention", "Request saved; not applied: " + str(exc)
            elif note:
                entry = self.s.save_knowledge({
                    "kind": "experience" if note[1].lower() == "experience" else "fact",
                    "title": "Experience correction" if note[1].lower() == "experience" else "Resume chat note",
                    "summary": note[2],
                    "data": {"origin": "instruction_chat", "message_id": key, "job_id": job_id},
                })
                state = "pending_evidence"
                response = (
                    f"Saved in Profile ({entry['id']}). Confirm its evidence before a new resume uses it."
                )
            elif re.fullmatch(r"(help|what can you do)\??", message, re.I):
                state = "answered"
                response = (
                    "I keep the instruction history. Use summary, skills, coursework, project, "
                    "second project, font, experience or note commands. Resume edits apply only "
                    "to the selected job. Profile notes wait for evidence review."
                )
            with self.w.connect() as db:
                db.execute(
                    "UPDATE instruction_messages SET response=?,state=? WHERE id=?",
                    (response, state, key),
                )
                self.w.record_event(db, "instruction_recorded", job_id, message_id=key, state=state)
            self.w.export_tracking()
            return next(item for item in self.history(job_id) if item["id"] == key)
