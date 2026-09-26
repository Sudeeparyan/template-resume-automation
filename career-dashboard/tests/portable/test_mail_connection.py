"""An optional mailbox belongs to one profile and is checked before searches."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "backend/scripts"))

from backend.dashboard.app import create_app  # noqa: E402
from backend.services.agents import AgentRunner  # noqa: E402
from backend.services.workspace_v2 import CareerServices  # noqa: E402
from career import Workspace  # noqa: E402


def profile(tmp_path: Path):
    root = tmp_path / "person"
    config = root / "data/config"
    context = root / "data/context"
    config.mkdir(parents=True)
    context.mkdir(parents=True)
    (config / "profile.yml").write_text("candidate: {}\ntarget_markets: [ie]\n", encoding="utf-8")
    (context / "evidence.yml").write_text("candidate_revision: 1\nclaims: []\nprojects: []\n", encoding="utf-8")
    return root


def test_mail_connection_is_profile_scoped_and_verified_before_ingest(tmp_path):
    root = profile(tmp_path)
    service = CareerServices(Workspace(root))
    assert service.mail()["available"] is False
    with pytest.raises(ValueError, match="both"):
        service.save_mail_connection("gmail", "")
    with pytest.raises(ValueError, match="unsupported"):
        service.save_mail_connection("gmail.unsafe", "person@example.org")
    service.save_mail_connection("gmail", "Person@Example.org")
    assert service.mail()["available"] is True
    assert service.mail()["connection"]["connected"] is False
    assert service.mail()["connection"]["expected_email"] == "person@example.org"
    batch = {"email": "someone@example.org", "connection_verified": True,
             "search_completed": True, "coverage": "Test fixture", "messages": []}
    with pytest.raises(ValueError, match="does not match"):
        service.ingest_mail(batch)
    assert service.mail()["connection"]["connected"] is False
    batch["email"] = "PERSON@example.org"
    assert service.ingest_mail(batch) == {"imported": 0}
    assert service.mail()["connection"]["connected"] is True
    second = profile(tmp_path / "other")
    assert CareerServices(Workspace(second)).mail()["available"] is False


def test_mailbox_change_with_saved_evidence_requires_another_profile(tmp_path):
    root = profile(tmp_path)
    service = CareerServices(Workspace(root))
    service.save_mail_connection("gmail", "person@example.org")
    service.ingest_mail({
        "email": "person@example.org", "connection_verified": True,
        "search_completed": True, "coverage": "Test fixture",
        "messages": [{
            "id": "fixture-message", "kind": "uncertain", "job_id": None,
            "company": "Example", "role": "Archivist", "subject": "Application update",
            "sender": "jobs@example.org", "received_at": datetime.now(timezone.utc).isoformat(),
            "submission_date": None, "excerpt": "Please review", "reason": "Uncertain",
            "confidence": "needs_review",
        }],
    })
    with pytest.raises(ValueError, match="separate profile"):
        service.save_mail_connection("gmail2", "other@example.org")
    service.save_mail_connection("", "")
    assert service.mail()["available"] is False
    assert len(service.mail()["messages"]) == 1


def test_preflight_uses_only_profile_tool_and_blocks_wrong_account(tmp_path):
    runner = object.__new__(AgentRunner)
    runner.w = SimpleNamespace(root=tmp_path)
    runner.s = SimpleNamespace(pref=lambda key, default: {
        "connector_id": "gmail", "expected_email": "person@example.org",
    })
    calls = []

    def cached(prompt, schema, **options):
        calls.append(options)
        return {"email": "other@example.org", "connection_verified": True}

    runner.cached = cached
    with pytest.raises(ValueError, match="No messages were read"):
        runner.verify_mailbox()
    assert calls == [{"cacheable": False, "apps": "profile", "web": False}]


def test_profile_api_saves_connection_without_crossing_to_other_profile(tmp_path):
    root = profile(tmp_path)
    with TestClient(create_app(root), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v2/mail/connection").json().get("connector_id") is None
        response = client.put("/api/v2/mail/connection", json={
            "connector_id": "gmail", "expected_email": "person@example.org",
        })
        assert response.status_code == 200, response.text
        assert client.get("/api/v2/mail").json()["available"] is True
