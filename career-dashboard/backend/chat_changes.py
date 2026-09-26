"""Revision-bound, idempotent resume and Profile change sets."""

from __future__ import annotations

import difflib
import json
import re
import uuid

from backend.resume_rules import canonicalize_skill_list


class ChatChangeService:
    def __init__(self, service, studio):
        self.s, self.w, self.studio = service, service.w, studio

    def _existing(self, scope: str, request_id: str):
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM chat_change_sets WHERE scope=? AND request_id=?", (scope, request_id)).fetchone()
        return self._decode(row) if row else None

    @staticmethod
    def _decode(row):
        return {**dict(row), "proposed_changes": json.loads(row["proposed_changes"]), "evidence_ids": json.loads(row["evidence_ids"])}


    FIELD_TO_CHANGE = {
        "summary": "ResumeSummary",
        "skills": "CoreSkills",
        "project": "project_id",
        "second_project": "second_project_id",
        "font": "font",
    }

    def _eligible_projects(self) -> dict:
        return {
            item["id"]: item["title"]
            for item in self.s.knowledge()
            if item["kind"] == "project" and item["review_state"] == "registered" and not item["deleted"]
        }

    def _registered_evidence(self) -> list:
        return [
            {"id": item["id"], "kind": item["kind"], "title": item["title"], "summary": item["summary"]}
            for item in self.s.knowledge()
            if item["review_state"] == "registered" and not item["deleted"]
        ]

    def _tailor_with_ai(self, job_id: str, draft: dict, message: str) -> dict:
        """Ask the resume_tailor specialist for an evidence-bound change set.

        Returns the same shape the regex path produces, plus the reasons any part
        of the request could not be applied. A request that needs a fact the
        evidence does not hold is reported, never silently dropped.
        """
        from backend.ai import any_provider_configured, team_for
        from backend.ai.agents.graph import AgentError

        if not any_provider_configured(self.w.root):
            return {"changes": {}, "unsupported": [], "note":
                    "No AI provider is configured, so only the exact commands are understood. "
                    "Add an API key in Settings to use plain-English requests."}

        job = self.w.get_job(job_id)
        projects = self._eligible_projects()
        try:
            result = team_for(self.s).run("resume_tailor", {
                "request": message,
                "role": {"title": job["title"], "company": job["company"],
                         "description": (job["description"] or "")[:12000]},
                "current_resume_fields": draft["fields"],
                "registered_evidence": self._registered_evidence(),
                "selectable_project_ids": projects,
            })
        except AgentError as error:
            return {"changes": {}, "unsupported": [],
                    "note": "The AI assistant could not be reached, so nothing was changed. " + str(error)}

        changes, unsupported = {}, list(result.unsupported_requests)
        for edit in result.edits:
            target = self.FIELD_TO_CHANGE.get(edit.field)
            if not target:
                continue
            if target in {"project_id", "second_project_id"}:
                if edit.value not in projects:
                    unsupported.append(f"{edit.value} is not a registered, resume-ready project")
                    continue
                changes[target] = edit.value
            elif target == "font":
                try:
                    size = float(edit.value)
                except ValueError:
                    continue
                if 10 <= size <= 12:
                    changes["font"] = size
            elif target in {"CoreSkills", "SkillsLanguages"}:
                changes[target] = canonicalize_skill_list(edit.value)
            else:
                if not edit.evidence_ids:
                    # Rewritten prose with nothing behind it is a claim, not an edit.
                    unsupported.append(f"{edit.field}: proposed wording cites no registered evidence")
                    continue
                changes[target] = edit.value.strip()
        return {"changes": changes, "unsupported": unsupported, "note": result.summary}

    def resume_preview(self, job_id: str, message: str, request_id: str, expected_revision: int) -> dict:
        existing = self._existing("resume", request_id)
        if existing:
            return existing
        draft = self.studio.get(job_id)
        if draft["revision"] != expected_revision:
            raise ValueError("This resume changed elsewhere. Reload before previewing the request.")
        text = message.strip()
        changes, pending = {}, []
        patterns = {
            "ResumeSummary": r"^(?:set\s+)?summary\s*:\s*(.+)$",
            "CoreSkills": r"^(?:set\s+)?skills\s*:\s*(.+)$",
        }
        for field, pattern in patterns.items():
            match = re.match(pattern, text, re.I | re.S)
            if match:
                changes[field] = canonicalize_skill_list(match[1]) if field in {"CoreSkills", "SkillsLanguages"} else match[1].strip()
        project = re.match(r"^(second project|project)\s*:\s*([\w-]+)\s*$", text, re.I)
        if project:
            changes["second_project_id" if project[1].casefold().startswith("second") else "project_id"] = project[2]
        font = re.match(r"^(?:set\s+)?(?:font|font size|body font)\s*:?\s*(10(?:\.\d)?|11(?:\.\d)?|12(?:\.0)?)\s*$", text, re.I)
        if font:
            changes["font"] = float(font[1])
        ai_note = None
        if not changes:
            # Plain English: ask the resume_tailor specialist for an evidence-bound edit.
            tailored = self._tailor_with_ai(job_id, draft, text)
            changes.update(tailored["changes"])
            ai_note = tailored["note"]
            for reason in tailored["unsupported"]:
                pending.append({"kind": "fact", "title": "Resume chat proposal", "summary": reason,
                                "reason": "This needs a candidate fact that the evidence registry does not hold."})
        if not changes and not pending:
            pending.append({"kind": "fact", "title": "Resume chat proposal", "summary": text, "reason": "The request is not a supported evidence-safe document edit and needs Profile review."})
        proposed = {"message": text, "fields": {k: v for k, v in changes.items() if k in {"ResumeSummary", "CoreSkills"}}, "project_id": changes.get("project_id"), "second_project_id": changes.get("second_project_id"), "font": changes.get("font"), "pending_profile_proposals": pending, "ai_note": ai_note}
        preview_lines = [f"{key}: {draft['fields'].get(key, '')} -> {value}" for key, value in proposed["fields"].items()]
        if proposed.get("project_id"):
            preview_lines.append("First project -> " + proposed["project_id"])
        if proposed.get("second_project_id"):
            preview_lines.append("Second project -> " + proposed["second_project_id"])
        if proposed.get("font"):
            preview_lines.append("Body font -> " + str(proposed["font"]))
        if preview_lines:
            proposed["diff"] = "\n".join(preview_lines)
        else:
            reasons = [item["summary"] for item in pending]
            proposed["diff"] = "No resume change could be made from this request.\n" + (
                ai_note + "\n" if ai_note else ""
            ) + ("\n".join("- " + reason for reason in reasons) if reasons else "")
        proposed["applies_resume_change"] = bool(preview_lines)
        stamp = self.s.now()
        change_id = uuid.uuid4().hex
        with self.w.connect() as db:
            db.execute("INSERT INTO chat_change_sets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (change_id, request_id, "resume", job_id, expected_revision, "preview", json.dumps(proposed), None, None, "[]", stamp, stamp))
        return self._existing("resume", request_id)

    def resume_apply(self, job_id: str, change_id: str, request_id: str, expected_revision: int) -> dict:
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM chat_change_sets WHERE id=? AND scope='resume' AND request_id=?", (change_id, request_id)).fetchone()
        if not row:
            raise ValueError("Resume change preview not found")
        change = self._decode(row)
        if change["state"] == "applied":
            return change
        if change["source_revision"] != expected_revision or self.studio.get(job_id)["revision"] != expected_revision:
            raise ValueError("This resume changed after the preview. Reload and preview the request again.")
        proposal = change["proposed_changes"]
        if proposal["pending_profile_proposals"]:
            ids = []
            for item in proposal["pending_profile_proposals"]:
                saved = self.s.save_knowledge({**item, "data": {"origin": "resume_chat", "pending": True}})
                ids.append(saved["id"])
            with self.w.connect() as db:
                db.execute("UPDATE chat_change_sets SET state='applied',evidence_ids=?,updated_at=? WHERE id=?", (json.dumps(ids), self.s.now(), change_id))
            return self._existing("resume", request_id)
        if proposal.get("font") is not None:
            self.s.set_pref("resume_font:" + job_id, proposal["font"])
        updated = self.studio.save(job_id, expected_revision, fields=proposal["fields"] or None, project_id=proposal.get("project_id"), second_project_id=proposal.get("second_project_id"))
        try:
            compiled = self.studio.preview(job_id, updated["revision"])
            assessment = self.studio.score(job_id)
        except ValueError as exc:
            current = self.studio.get(job_id)
            self.studio.save(job_id, current["revision"], restore_revision=expected_revision)
            with self.w.connect() as db:
                db.execute("UPDATE chat_change_sets SET state='rejected',undo_revision=?,updated_at=? WHERE id=?", (self.studio.get(job_id)["revision"], self.s.now(), change_id))
            raise ValueError("The requested change did not compile; the prior valid source was restored. " + str(exc)) from None
        with self.w.connect() as db:
            db.execute("UPDATE chat_change_sets SET state='applied',applied_revision=?,updated_at=? WHERE id=?", (compiled["revision"], self.s.now(), change_id))
            self.w.record_event(db, "resume_chat_applied", job_id, change_set_id=change_id, revision=compiled["revision"])
        self.s.sync_projections()
        return {**self._existing("resume", request_id), "assessment": assessment, "draft": compiled}

    def resume_undo(self, job_id: str, change_id: str, request_id: str, expected_revision: int) -> dict:
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM chat_change_sets WHERE id=? AND request_id=? AND state='applied'", (change_id, request_id)).fetchone()
        if not row:
            raise ValueError("Applied change set not found")
        change = self._decode(row)
        restored = self.studio.save(job_id, expected_revision, restore_revision=change["source_revision"])
        with self.w.connect() as db:
            db.execute("UPDATE chat_change_sets SET state='undone',undo_revision=?,updated_at=? WHERE id=?", (restored["revision"], self.s.now(), change_id))
            self.w.record_event(db, "resume_chat_undone", job_id, change_set_id=change_id, revision=restored["revision"])
        self.s.sync_projections()
        return self._existing("resume", request_id)


    def _curate_with_ai(self, message: str) -> dict:
        """Ask the profile_curator specialist to turn plain English into proposals.

        Profile changes govern every future resume, so these stay proposals: the
        apply step still requires confirmation and marks entries pending review.
        """
        from backend.ai import any_provider_configured, team_for
        from backend.ai.agents.graph import AgentError

        if not any_provider_configured(self.w.root):
            return {"changes": [], "note":
                    "No AI provider is configured, so only the exact commands are understood. "
                    "Add an API key in Settings to use plain-English requests."}
        known = {item["id"]: item for item in self.s.knowledge() if not item["deleted"]}
        try:
            result = team_for(self.s).run("profile_curator", {
                "request": message,
                "existing_profile": [
                    {"id": item["id"], "kind": item["kind"], "title": item["title"],
                     "summary": item["summary"], "review_state": item["review_state"]}
                    for item in known.values()
                ],
            })
        except AgentError as error:
            return {"changes": [], "note": "The AI assistant could not be reached, so nothing was proposed. " + str(error)}

        changes = []
        for proposal in result.proposals:
            if proposal.action in {"correct", "merge", "remove"} and proposal.target_id not in known:
                # Never act on an entry the model invented.
                continue
            if proposal.action == "remove":
                changes.append({"operation": "remove", "id": proposal.target_id,
                                "rationale": proposal.rationale})
            elif proposal.action in {"correct", "merge"}:
                changes.append({"operation": "update", "id": proposal.target_id,
                                "summary": (proposal.summary or "").strip(),
                                "rationale": proposal.rationale})
            else:
                changes.append({"operation": "add", "kind": (proposal.kind or "fact").casefold(),
                                "title": (proposal.title or message[:80]).strip(),
                                "summary": (proposal.summary or "").strip(),
                                "rationale": proposal.rationale})
        return {"changes": changes, "note": result.summary}

    def profile_preview(self, message: str, request_id: str, expected_revision: int) -> dict:
        existing = self._existing("profile", request_id)
        if existing:
            return existing
        if self.s.profile_revision() != expected_revision:
            raise ValueError("Profile changed elsewhere. Reload before previewing this request.")
        text = message.strip()
        proposed = []
        add = re.match(r"^add\s+(skill|project|experience|education|certification|fact)\s*:\s*([^|]+)(?:\|(.+))?$", text, re.I | re.S)
        remove = re.match(r"^remove\s+([\w:.-]+)\s*$", text, re.I)
        update = re.match(r"^(?:update|correct)\s+([\w:.-]+)\s*:\s*(.+)$", text, re.I | re.S)
        if add:
            proposed.append({"operation": "add", "kind": add[1].casefold(), "title": add[2].strip(), "summary": (add[3] or "").strip()})
        elif remove:
            proposed.append({"operation": "remove", "id": remove[1]})
        elif update:
            proposed.append({"operation": "update", "id": update[1], "summary": update[2].strip()})
        note = None
        if not proposed:
            curated = self._curate_with_ai(text)
            proposed, note = curated["changes"], curated["note"]
        if not proposed:
            proposed.append({"operation": "proposal", "kind": "fact", "title": "Profile chat proposal", "summary": text})
        payload = {"message": text, "changes": proposed, "confirmation_required": True, "ai_note": note}
        stamp, change_id = self.s.now(), uuid.uuid4().hex
        with self.w.connect() as db:
            db.execute("INSERT INTO chat_change_sets VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (change_id, request_id, "profile", None, expected_revision, "preview", json.dumps(payload), None, None, "[]", stamp, stamp))
        return self._existing("profile", request_id)

    def profile_apply(self, change_id: str, request_id: str, expected_revision: int) -> dict:
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM chat_change_sets WHERE id=? AND scope='profile' AND request_id=?", (change_id, request_id)).fetchone()
        if not row:
            raise ValueError("Profile change preview not found")
        change = self._decode(row)
        if change["state"] == "applied":
            return change
        if change["source_revision"] != expected_revision or self.s.profile_revision() != expected_revision:
            raise ValueError("Profile changed after the preview. Reload and preview again.")
        ids = []
        for item in change["proposed_changes"]["changes"]:
            if item["operation"] in {"add", "proposal"}:
                saved = self.s.save_knowledge({"kind": item["kind"], "title": item["title"], "summary": item["summary"], "data": {"origin": "profile_chat", "pending": True}})
                ids.append(saved["id"])
            elif item["operation"] == "remove":
                self.s.delete_knowledge(item["id"])
                ids.append(item["id"])
            elif item["operation"] == "update":
                old = next((entry for entry in self.s.knowledge() if entry["id"] == item["id"]), None)
                if not old:
                    raise ValueError("Profile entry not found")
                saved = self.s.save_knowledge({**old, "summary": item["summary"]}, item["id"])
                ids.append(saved["id"])
        revision = self.s.profile_revision()
        with self.w.connect() as db:
            db.execute("UPDATE chat_change_sets SET state='applied',applied_revision=?,evidence_ids=?,updated_at=? WHERE id=?", (revision, json.dumps(ids), self.s.now(), change_id))
        return self._existing("profile", request_id)
