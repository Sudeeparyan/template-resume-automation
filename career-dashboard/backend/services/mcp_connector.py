"""Profile-scoped local connector shared by the MCP stdio entry point and tests."""

from __future__ import annotations

import json
import uuid

from backend.chat_changes import ChatChangeService
from backend.services.resume_studio import ResumeStudio
from backend.services.workspace_v2 import CareerServices
from career import Workspace


class CareerConnector:
    def __init__(self, profile_id: str, *, profiles=None):
        from backend.profiles import store

        if not profile_id:
            raise ValueError("An exact profile ID is required")
        self.profiles = profiles or store()
        self.profile = self.profiles.get(profile_id)
        if self.profile["state"] != "ready":
            raise ValueError("Build this profile before connecting it")
        self.root = self.profiles.root_for(profile_id)
        self.s = CareerServices(Workspace(self.root))
        self.studio = ResumeStudio(self.s)
        self._runner = None
        self._pipeline = None
        self.chats = ChatChangeService(self.s, self.studio)

    @property
    def pipeline(self):
        if self._pipeline is None:
            from backend.services.agents import AgentRunner
            from backend.services.pipeline import Pipeline

            self._runner = AgentRunner(self.s)
            self._runner.studio = self.studio
            self._pipeline = Pipeline(self.s, self._runner, self.studio)
        return self._pipeline

    def close(self):
        if self._runner is not None:
            self._runner.pool.shutdown(wait=False)

    @staticmethod
    def _job(job: dict, description=False) -> dict:
        keys = ["id", "company", "title", "location", "url", "status", "application_date",
                "fit_score", "fit_rationale", "posting_state", "sponsor_tier", "sponsor_evidence"]
        result = {key: job.get(key) for key in keys}
        if description:
            result["description"] = job.get("description") or ""
        return result

    def summary(self) -> dict:
        current = self.s.summary()
        return {
            "profile_id": self.profile["id"],
            "name": self.profile["name"],
            "market": self.profile.get("country"),
            "jobs": [self._job(job) for job in current["jobs"]],
            "counts": current["counts"],
            "goals": current["goals"],
            "funnel": current["funnel"],
            "needs_you": current["needs_you"],
            "profile_dirty": current["profile_dirty"],
            "note": "Nothing is submitted or sent by this connector.",
        }

    def profile_context(self) -> dict:
        return {
            "profile_id": self.profile["id"],
            "configuration": self.s.w.profile(),
            "evidence": self.s.w.evidence(),
            "entries": self.s.knowledge(),
            "revision": self.s.profile_revision(),
            "note": "Candidate facts come only from this selected profile's registered sources.",
        }

    def list_jobs(self, status: str | None = None) -> dict:
        jobs = [self._job(job) for job in self.s.w.jobs() if not status or job.get("status") == status]
        return {"jobs": jobs, "count": len(jobs)}

    def get_job(self, job_id: str) -> dict:
        return self._job(self.s.w.get_job(job_id), description=True)

    def job_fit(self, job_id: str, refresh: bool = False) -> dict:
        from backend.services import fit

        self.s.w.get_job(job_id)
        return fit.for_job(self.s, job_id, refresh=refresh, use_ai=refresh)

    def resolve_posting(self, url: str) -> dict:
        from backend.services import lead_resolver

        return lead_resolver.resolve(url)

    def save_posting(self, company: str, title: str, location: str, url: str, description: str) -> dict:
        result = self.s.add_posting({
            "company": company, "title": title, "location": location, "url": url, "description": description,
        }, source="mcp")
        if result.get("job"):
            result["job"] = self._job(result["job"], description=True)
        return result

    def start_search(self, count: int = 5, source: str = "default", cover_letter: bool = False) -> dict:
        return self.pipeline.start({"count": count, "source": source, "steps": {"cover_letter": cover_letter}})

    def search_status(self, run_id: str | None = None) -> dict:
        if run_id:
            result = self.pipeline.get(run_id)
            if result is None:
                raise ValueError("No such search run in this profile")
            return result
        return self.pipeline.status()

    def prepare_job(self, job_id: str, cover_letter: bool = True) -> dict:
        return self.pipeline.prepare_existing(job_id, {"count": 1, "source": "default", "steps": {"cover_letter": cover_letter}})

    def tailor_job(self, job_id: str) -> dict:
        from backend.ai import team_for

        result = self.studio.tailor(job_id, team_for(self.s))
        return {
            "job_id": job_id,
            "revision": result.get("revision"),
            "tailored": result.get("tailored"),
            "preview": result.get("preview"),
            "warnings": result.get("warnings"),
            "note": "Saved as a draft for review. Nothing was submitted.",
        }

    def documents(self, job_id: str) -> dict:
        from backend.services import readiness

        self.s.w.get_job(job_id)
        documents = next((item for item in self.s.documents() if item["job_id"] == job_id), None)
        return {"job_id": job_id, "documents": documents, "readiness": readiness.check(self.s, self.studio, job_id)}

    def propose_profile_change(self, request: str) -> dict:
        request_id = "mcp-" + uuid.uuid4().hex[:16]
        preview = self.chats.profile_preview(request, request_id, self.s.profile_revision())
        return {
            "change_set_id": preview["id"], "source_revision": preview["source_revision"],
            "changes": preview["proposed_changes"]["changes"],
            "note": "Show these changes to the user. Apply only after the user explicitly confirms.",
        }

    def apply_profile_change(self, change_set_id: str, expected_revision: int, confirmed: bool = False) -> dict:
        if confirmed is not True:
            raise ValueError("The user must explicitly confirm this exact profile change first")
        with self.s.w.connect() as db:
            row = db.execute("SELECT request_id FROM chat_change_sets WHERE id=? AND scope='profile'", (change_set_id,)).fetchone()
        if not row:
            raise ValueError("No such profile proposal in this profile")
        result = self.chats.profile_apply(change_set_id, row["request_id"], expected_revision)
        return {"change_set_id": change_set_id, "state": result["state"], "entry_ids": result["evidence_ids"],
                "reconciled": result.get("reconciled"), "profile_dirty": self.s.profile_dirty()}


def configuration(host: str, profile_id: str, *, python: str, script: str, cwd: str, demo=False) -> dict:
    """Copyable host configuration; never writes the user's global host settings."""
    if host not in {"antigravity", "claude", "codex"}:
        raise ValueError("Choose Antigravity, Claude or Codex")
    if not profile_id:
        raise ValueError("An exact profile ID is required")
    args = [script, "--profile", profile_id, *(["--demo"] if demo else [])]
    if host == "codex":
        text = "[mcp_servers.career-workspace]\ncommand = " + json.dumps(python) + "\nargs = " + json.dumps(args, ensure_ascii=False) + "\ncwd = " + json.dumps(cwd) + "\n"
        return {"host": host, "format": "toml", "config": text}
    payload = {"mcpServers": {"career-workspace": {"command": python, "args": args, "cwd": cwd}}}
    return {"host": host, "format": "json", "config": json.dumps(payload, indent=2, ensure_ascii=False)}
