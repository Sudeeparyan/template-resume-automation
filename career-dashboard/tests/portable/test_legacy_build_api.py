"""Old intake and setup-chat Build actions use the shared guarded build service."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "backend/scripts"))

from backend.ai.agents.schemas import IntakeAudit, IntakeFacts, InterviewTurn  # noqa: E402
from backend.dashboard.shell import create_shell  # noqa: E402
from backend.pdf_compiler import tectonic_executable  # noqa: E402
from backend.profiles import ProfileStore  # noqa: E402
from backend.services.intake.interview import BUILD_NOW  # noqa: E402
import backend.services.intake.api as intake_api  # noqa: E402


SOURCE = (b"Example Person lives in Dublin, Ireland. Email: example@example.test. Phone: 555-0100.\n"
          b"I worked at Example Services as a Customer Support Specialist from January 2023 to Present.\n"
          b"I answered customers by phone and email and tracked their requests in Zendesk.\n"
          b"I am seeking customer support work in Ireland.\n")


class Team:
    def __init__(self):
        self.extractions = 0

    def run(self, name, _payload):
        if name == "intake_auditor":
            return IntakeAudit()
        if name == "intake_interviewer":
            return InterviewTurn(action="done")
        assert name == "profile_extractor"
        self.extractions += 1
        return IntakeFacts.model_validate({
            "contact": {"full_name": "Example Person", "city": "Dublin", "country": "Ireland",
                        "email": "example@example.test", "phone": "555-0100", "refs": ["P001"]},
            "authorization": {"status": "citizen", "work_country": "Ireland",
                              "needs_sponsorship_later": "no", "refs": ["P001"]},
            "targets": {"roles": ["Customer Support Specialist"], "countries": ["Ireland"],
                        "cities": ["Dublin"], "refs": ["P001"]},
            "experience": [{"employer": "Example Services", "title": "Customer Support Specialist",
                            "location": "Dublin", "start": "January 2023", "end": "Present",
                            "bullets": ["Answered customers by phone and email.",
                                        "Tracked their requests in Zendesk."], "refs": ["P001"]}],
        })


def _client(tmp_path, monkeypatch, team):
    monkeypatch.setattr(intake_api, "team_factory", lambda _profiles: lambda _on_usage: team)
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    return store, TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1")


def _wait_intake(client, base, state):
    until = time.monotonic() + 30
    while time.monotonic() < until:
        response = client.get(base + "/intake")
        assert response.status_code == 200, response.text
        current = response.json()
        if current["state"] == state:
            return current
        if current["state"] == "failed":
            pytest.fail(str(current.get("error")))
        time.sleep(.05)
    pytest.fail("Intake did not reach " + state)


@pytest.mark.skipif(not tectonic_executable(), reason="Tectonic is required for the PDF release gate")
def test_reviewed_intake_build_keeps_user_edits_and_validates_pdf(tmp_path, monkeypatch):
    team = Team()
    store, test_client = _client(tmp_path, monkeypatch, team)
    with test_client as client:
        pid = client.post("/api/profiles", json={"name": "Placeholder"}).json()["profile"]["id"]
        base = f"/api/profiles/{pid}"
        assert client.post(base + "/intake/files?name=resume.md", content=SOURCE).status_code == 200
        assert client.post(base + "/intake/start").status_code == 200
        _wait_intake(client, base, "review")
        extracted_before = team.extractions
        response = client.put(base + "/intake/draft", json={
            "roles": ["Customer Success Associate"], "target_markets": ["us"],
            "work_authorization_by_market": {"us": {"status": "authorized", "citizenship": "citizen"}},
        })
        assert response.status_code == 200, response.text
        built = client.post(base + "/intake/build")
        assert built.status_code == 200, built.text
        result = built.json()
        assert result["state"] == "built"
        assert result["build_run"]["status"] == "completed"
        assert result["build_run"]["reviewed_draft"] is True
        assert result["profile"]["target_markets"] == ["us"]
        assert team.extractions == extracted_before  # the reviewed corrections were not read away
        root = store.root_for(pid)
        assert list((root / "data/output/base").glob("*.pdf"))
        profile = client.get(f"/p/{pid}/api/v2/profile").json()["configuration"]
        assert profile["target_roles"]["primary"] == ["Customer Success Associate"]
        assert client.get(base + "/build-runs").json()["runs"][0]["id"] == result["build_run"]["id"]


@pytest.mark.skipif(not tectonic_executable(), reason="Tectonic is required for the retry release gate")
def test_old_build_route_rolls_back_when_pdf_fails_then_retries(tmp_path, monkeypatch):
    import backend.pdf_compiler as pdf_compiler

    team = Team()
    store, test_client = _client(tmp_path, monkeypatch, team)
    with test_client as client:
        pid = client.post("/api/profiles", json={"name": "Example Person"}).json()["profile"]["id"]
        base = f"/api/profiles/{pid}"
        assert client.post(base + "/intake/files?name=resume.md", content=SOURCE).status_code == 200
        assert client.post(base + "/intake/start").status_code == 200
        _wait_intake(client, base, "review")
        real_tectonic = pdf_compiler.tectonic_executable
        monkeypatch.setattr(pdf_compiler, "tectonic_executable", lambda: None)
        failed = client.post(base + "/intake/build")
        assert failed.status_code == 400
        [run] = client.get(base + "/build-runs").json()["runs"]
        assert run["status"] == "failed" and run["reviewed_draft"] is True
        assert store.get(pid)["state"] == "onboarding"
        assert not (store.root_for(pid) / "data/config/profile.yml").exists()
        assert client.get(base + "/intake").json()["state"] == "review"
        monkeypatch.setattr(pdf_compiler, "tectonic_executable", real_tectonic)
        retried = client.post(base + "/intake/build")
        assert retried.status_code == 200, retried.text
        assert retried.json()["build_run"]["status"] == "completed"
        assert store.get(pid)["state"] == "ready"
        assert list((store.root_for(pid) / "data/output/base").glob("*.pdf"))
        assert len(client.get(base + "/build-runs").json()["runs"]) == 2


@pytest.mark.skipif(not tectonic_executable(), reason="Tectonic is required for the PDF release gate")
def test_setup_chat_build_uses_reviewed_run_and_finishes_once(tmp_path, monkeypatch):
    team = Team()
    store, test_client = _client(tmp_path, monkeypatch, team)
    with test_client as client:
        pid = client.post("/api/profiles", json={"name": "Example Person"}).json()["profile"]["id"]
        base = f"/api/profiles/{pid}"
        assert client.post(base + "/intake/files?name=resume.md", content=SOURCE).status_code == 200
        assert client.post(base + "/intake/start").status_code == 200
        _wait_intake(client, base, "review")
        extracted_before = team.extractions
        # The setup chat sees the already reviewed draft and asks its remaining essentials.
        until = time.monotonic() + 30
        while time.monotonic() < until:
            response = client.get(base + "/intake/chat")
            assert response.status_code == 200, response.text
            chat = response.json()
            pending = chat.get("pending")
            if not pending:
                time.sleep(.05)
                continue
            key = pending["key"]
            if key == "build":
                choice, other = BUILD_NOW, ""
            elif key == "country":
                choice, other = "ie", ""
            elif key == "roles":
                choice, other = "Customer Support Specialist", ""
            elif key == "sponsor":
                choice, other = "no", ""
            else:
                pytest.fail("Unexpected setup question: " + key)
            answered = client.post(base + "/intake/chat/answer", json={"question_id": pending["id"],
                                                                     "choices": [choice], "other": other})
            assert answered.status_code == 200, answered.text
            if key == "build":
                break
        else:
            pytest.fail("Setup chat never offered to build")
        until = time.monotonic() + 60
        while time.monotonic() < until:
            chat = client.get(base + "/intake/chat").json()
            if any(message["kind"] == "built" for message in chat["messages"]):
                break
            time.sleep(.1)
        else:
            pytest.fail("Setup chat build did not finish")
        assert store.get(pid)["state"] == "ready"
        assert team.extractions == extracted_before
        assert len([m for m in chat["messages"] if m["kind"] == "built"]) == 1
        [run] = client.get(base + "/build-runs").json()["runs"]
        assert run["status"] == "completed" and run["reviewed_draft"] is True
