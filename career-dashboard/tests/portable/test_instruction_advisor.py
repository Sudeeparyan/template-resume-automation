"""Resume instruction durability and candidate-blind advisor behavior."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(APP), str(APP / "backend/scripts")]

from backend.services.agents import AgentRunner  # noqa: E402
from backend.dashboard.app import create_app  # noqa: E402
from backend.services.instruction_tracker import InstructionTracker  # noqa: E402
from backend.services.workspace_v2 import CareerServices  # noqa: E402
from career import Workspace  # noqa: E402


def service(tmp_path):
    root = tmp_path / "person"
    (root / "data/config").mkdir(parents=True)
    (root / "data/context").mkdir(parents=True)
    (root / "data/config/profile.yml").write_text(
        "candidate: {full_name: Example Person}\ntarget_markets: [ie]\n", encoding="utf-8",
    )
    (root / "data/context/evidence.yml").write_text(
        "candidate_revision: 1\nclaims: []\nprojects: []\n", encoding="utf-8",
    )
    career = CareerServices(Workspace(root))
    job = career.w.add_job(
        "Example Company", "Analyst", "Dublin, Ireland", "https://example.org/jobs/analyst",
        "The analyst will maintain SQL reporting dashboards and explain findings to the business team. "
        "This is a documented test posting for a local profile.",
    )
    return career, job


class DraftStub:
    def __init__(self):
        self.lock = threading.RLock()
        self.revision = 1
        self.source = "original"

    def save(self, job_id, revision, *, fields=None, **kwargs):
        if revision != self.revision:
            raise ValueError("Draft changed elsewhere")
        if fields:
            self.source = next(iter(fields.values()))
        self.revision += 1
        return self.get(job_id)

    def get(self, job_id):
        return {"revision": self.revision, "source": self.source,
                "fields": {}, "project_library": []}

    def preview(self, job_id, revision):
        return self.get(job_id)

    def score(self, job_id):
        return {"score": 50}

    def contract(self, job_id):
        return SimpleNamespace(min_body_pt=10, max_body_pt=11)


def test_instruction_history_is_scoped_idempotent_and_revision_bound(tmp_path):
    career, job = service(tmp_path)
    studio = DraftStub()
    tracker = InstructionTracker(career, studio)
    first = tracker.send("summary: SQL analyst", job["id"], 1, "request-1")
    assert first["state"] == "applied"
    assert studio.get(job["id"])["revision"] == 2
    assert tracker.send("summary: SQL analyst", job["id"], 1, "request-1") == first
    assert tracker.send("summary: stale", job["id"], 1)["state"] == "needs_attention"
    assert tracker.send("Please add an unsupported metric", job["id"], 2)["state"] == "needs_clarification"
    assert studio.get(job["id"])["source"] == "SQL analyst"
    assert len(InstructionTracker(career, studio).history(job["id"])) == 3
    with pytest.raises(ValueError, match="another request"):
        tracker.send("summary: another", job["id"], 2, "request-1")
    second, _ = service(tmp_path / "other")
    assert InstructionTracker(second, DraftStub()).history() == []


def test_instruction_api_records_a_thread_in_the_selected_profile(tmp_path):
    career, _ = service(tmp_path)
    with TestClient(create_app(career.w.root), base_url="http://127.0.0.1") as client:
        response = client.post("/api/v2/instructions", json={"message": "help", "request_id": "help-1"})
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "answered"
        assert [item["id"] for item in client.get("/api/v2/instructions").json()] == ["help-1"]


def test_advisor_only_receives_job_and_public_research(tmp_path):
    career, job = service(tmp_path)
    career.save_knowledge({"kind": "fact", "title": "PRIVATE PROFILE", "summary": "Secret detail"})
    prompts = []
    runner = AgentRunner(career)
    runner.gateway.resolve = lambda action, provider=None, model=None: (SimpleNamespace(id="test"), "test-model")
    runner.cached = lambda prompt, schema, **options: (
        prompts.append(prompt) or {"summary": "Checked", "report": "Public role advice", "sources": [], "limitations": []}
    )
    try:
        queued = runner.enqueue("resume_advisor", job["id"])
        runner.pool.shutdown(wait=True)
        run = next(item for item in career.runs() if item["id"] == queued["id"])
        assert run["state"] == "completed", run.get("error")
        assert run["result"]["profile_access"] is False
        assert len(prompts) == 2
        assert all("PRIVATE PROFILE" not in prompt and "Secret detail" not in prompt for prompt in prompts)
        assert all("Example Company" in prompt for prompt in prompts)
        career.set_agent_enabled("resume_advisor", False)
        with pytest.raises(ValueError, match="paused"):
            runner.enqueue("resume_advisor", job["id"])
    finally:
        runner.pool.shutdown(wait=True)


def test_ai_instruction_worker_rejects_new_candidate_claims(tmp_path):
    career, job = service(tmp_path)
    studio = DraftStub()
    studio.source = r"\newcommand{\CoreSkills}{SQL, Python}"
    tracker = InstructionTracker(career, studio)
    tracker.send("Put SQL first in skills", job["id"], 1)
    prompts = []
    runner = AgentRunner(career)
    runner.studio = studio
    runner.gateway.resolve = lambda action, provider=None, model=None: (SimpleNamespace(id="test"), "test-model")
    runner.cached = lambda prompt, schema, **options: (
        prompts.append(prompt) or {
            "summary": "One safe edit", "commands": ["skills: SQL", "experience: invented role"],
            "clarifications": [],
        }
    )
    try:
        queued = runner.enqueue("instruction_interpret", job["id"])
        runner.pool.shutdown(wait=True)
        run = next(item for item in career.runs() if item["id"] == queued["id"])
        assert run["state"] == "completed", run.get("error")
        assert run["result"]["applied_commands"] == ["skills: SQL"]
        assert "experience: invented role" in run["result"]["clarifications"][0]
        assert studio.get(job["id"])["source"] == "SQL"
        assert "invented role" not in str(career.knowledge())
        assert "Put SQL first" in prompts[0]
    finally:
        runner.pool.shutdown(wait=True)
