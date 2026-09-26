"""The resume and Profile chats must act on plain English, or say why they cannot."""

import shutil
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from test_career_workspace import add, workspace  # noqa: E402,F401
from backend.services.workspace_v2 import CareerServices  # noqa: E402
from backend.services.resume_studio import ResumeStudio  # noqa: E402
import backend.ai  # noqa: E402
import backend.chat_changes as chat_changes  # noqa: E402
from backend.ai.agents import schemas  # noqa: E402
from backend.ai.agents.graph import AgentError  # noqa: E402


@pytest.fixture
def service(workspace, monkeypatch):
    monkeypatch.setattr(CareerServices, "today", staticmethod(lambda: "2026-09-12"))
    shutil.copytree(ROOT / "backend/workflows", workspace.root / "backend/workflows")
    return CareerServices(workspace)


@pytest.fixture
def chat(service):
    return chat_changes.ChatChangeService(service, ResumeStudio(service))


@pytest.fixture
def job(service):
    """A real saved job: chat_change_sets.job_id has a foreign key to jobs."""
    return add(service.w)


class StubTeam:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def run(self, name, payload, **_options):
        if self.error:
            raise AgentError(self.error)
        return self.result


def use_team(monkeypatch, team):
    monkeypatch.setattr(backend.ai, "any_provider_configured", lambda root: True)
    monkeypatch.setattr(backend.ai, "team_for", lambda services, on_usage=None: team)


def no_provider(monkeypatch):
    monkeypatch.setattr(backend.ai, "any_provider_configured", lambda root: False)


def draft_stub(chat, monkeypatch, fields=None, revision=1):
    """Stand in for a compiled Studio draft so these tests need no LaTeX run."""
    monkeypatch.setattr(chat.studio, "get", lambda job_id: {
        "revision": revision,
        "fields": fields or {"ResumeSummary": "Existing summary.", "CoreSkills": "Power BI; SQL"},
    })


def job_stub(chat, monkeypatch):
    monkeypatch.setattr(chat.w, "get_job", lambda job_id: {
        "id": job_id, "title": "Data Analyst", "company": "Example Ltd",
        "description": "Power BI and SQL reporting for finance stakeholders.",
    })


def test_an_exact_command_never_reaches_the_model(chat, job, monkeypatch):
    """The deterministic grammar stays the fast path."""
    draft_stub(chat, monkeypatch)
    use_team(monkeypatch, StubTeam(error="the model must not have been called"))
    preview = chat.resume_preview(job["id"], "summary: A focused Power BI summary.",
                                  uuid.uuid4().hex, 1)
    proposed = preview["proposed_changes"]
    assert proposed["fields"]["ResumeSummary"] == "A focused Power BI summary."
    assert proposed["applies_resume_change"] is True


def test_plain_english_becomes_an_evidence_cited_edit(chat, job, monkeypatch):
    draft_stub(chat, monkeypatch)
    job_stub(chat, monkeypatch)
    use_team(monkeypatch, StubTeam(schemas.ResumeChangeSet(
        edits=[schemas.ResumeEdit(field="summary", value="Power BI focused summary.",
                                  rationale="The role is Power BI heavy.",
                                  evidence_ids=["SKILL-BI-001"])],
        unsupported_requests=[],
        summary="Refocused the summary on Power BI.",
    )))
    preview = chat.resume_preview(job["id"], "make the summary more focused on Power BI",
                                  uuid.uuid4().hex, 1)
    proposed = preview["proposed_changes"]
    assert proposed["fields"]["ResumeSummary"] == "Power BI focused summary."
    assert proposed["applies_resume_change"] is True
    assert "Power BI" in proposed["diff"]


def test_an_edit_citing_no_evidence_is_refused(chat, job, monkeypatch):
    """Rewritten prose with nothing behind it is a new claim, not an edit."""
    draft_stub(chat, monkeypatch)
    job_stub(chat, monkeypatch)
    use_team(monkeypatch, StubTeam(schemas.ResumeChangeSet(
        edits=[schemas.ResumeEdit(field="summary", value="Led a team of twelve engineers.",
                                  rationale="Sounds senior.", evidence_ids=[])],
        unsupported_requests=[],
        summary="Rewrote the summary.",
    )))
    preview = chat.resume_preview(job["id"], "say I led a big team", uuid.uuid4().hex, 1)
    proposed = preview["proposed_changes"]
    assert proposed["fields"] == {}
    assert proposed["applies_resume_change"] is False
    assert any("evidence" in item["summary"] for item in proposed["pending_profile_proposals"])


def test_an_unregistered_project_cannot_be_selected(chat, job, monkeypatch):
    draft_stub(chat, monkeypatch)
    job_stub(chat, monkeypatch)
    monkeypatch.setattr(chat, "_eligible_projects", lambda: {"PROJ-REAL": "Real project"})
    use_team(monkeypatch, StubTeam(schemas.ResumeChangeSet(
        edits=[schemas.ResumeEdit(field="project", value="PROJ-INVENTED",
                                  rationale="Looks relevant.", evidence_ids=["PROJ-INVENTED"])],
        unsupported_requests=[], summary="Swapped the project.",
    )))
    preview = chat.resume_preview(job["id"], "use a more relevant project", uuid.uuid4().hex, 1)
    proposed = preview["proposed_changes"]
    assert proposed["project_id"] is None
    assert any("PROJ-INVENTED" in item["summary"] for item in proposed["pending_profile_proposals"])


def test_an_unreachable_provider_is_reported_not_swallowed(chat, job, monkeypatch):
    """The original defect: the box cleared and nothing said why."""
    draft_stub(chat, monkeypatch)
    job_stub(chat, monkeypatch)
    use_team(monkeypatch, StubTeam(error="resume_tailor could not run: the account is out of credits"))
    preview = chat.resume_preview(job["id"], "make it punchier", uuid.uuid4().hex, 1)
    proposed = preview["proposed_changes"]
    assert proposed["applies_resume_change"] is False
    assert "out of credits" in proposed["diff"]
    assert "No resume change could be made" in proposed["diff"]


def test_without_a_key_the_chat_explains_the_limit(chat, job, monkeypatch):
    draft_stub(chat, monkeypatch)
    job_stub(chat, monkeypatch)
    no_provider(monkeypatch)
    preview = chat.resume_preview(job["id"], "make it punchier", uuid.uuid4().hex, 1)
    assert "No AI provider is configured" in preview["proposed_changes"]["diff"]


def test_a_failure_message_carries_no_provider_url(chat, job, monkeypatch):
    """Provider errors quote key-management links; those must not reach the user."""
    draft_stub(chat, monkeypatch)
    job_stub(chat, monkeypatch)
    use_team(monkeypatch, StubTeam(error="the account is out of credits"))
    diff = chat.resume_preview(job["id"], "make it punchier", uuid.uuid4().hex, 1)["proposed_changes"]["diff"]
    assert "http" not in diff


def test_profile_chat_turns_a_statement_into_a_confirmable_proposal(chat, service, monkeypatch):
    use_team(monkeypatch, StubTeam(schemas.ProfileChangeSet(
        proposals=[schemas.ProfileProposal(
            action="add", kind="certification",
            title="Google Data Analytics Professional Certificate",
            summary="Completed August 2026.", rationale="The candidate stated they finished it.")],
        summary="Added one certification.",
    )))
    preview = chat.profile_preview("I finished the Google Data Analytics certificate",
                                   uuid.uuid4().hex, service.profile_revision())
    proposed = preview["proposed_changes"]
    assert proposed["confirmation_required"] is True
    assert proposed["changes"][0]["operation"] == "add"
    assert proposed["changes"][0]["kind"] == "certification"


def test_profile_chat_ignores_a_proposal_against_an_unknown_entry(chat, service, monkeypatch):
    """A correction must name an entry that exists, or it is dropped."""
    use_team(monkeypatch, StubTeam(schemas.ProfileChangeSet(
        proposals=[schemas.ProfileProposal(action="correct", target_id="ENTRY-THAT-DOES-NOT-EXIST",
                                           summary="New wording.", rationale="Model invented this.")],
        summary="One correction.",
    )))
    preview = chat.profile_preview("fix my job title", uuid.uuid4().hex, service.profile_revision())
    changes = preview["proposed_changes"]["changes"]
    assert all(change.get("id") != "ENTRY-THAT-DOES-NOT-EXIST" for change in changes)
