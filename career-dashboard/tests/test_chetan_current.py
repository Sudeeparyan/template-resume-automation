"""Chetan-specific integration checks; all mutations use a disposable workspace."""

from __future__ import annotations

import shutil
import sys
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from backend.dashboard.shell import create_shell
from backend.profiles import ProfileStore
from backend.services.reapply import check, due_for_ghosting
from backend.services.workspace_v2 import CareerServices

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "backend" / "scripts"))
from career import Workspace  # noqa: E402


def temporary_workspace(tmp_path):
    root = tmp_path / "career-dashboard"
    for name in ("config", "context", "templates"):
        shutil.copytree(SOURCE / "data" / name, root / "data" / name)
    return root, Workspace(root)


def test_chetan_profile_and_no_inferred_ghosting(tmp_path):
    _, workspace = temporary_workspace(tmp_path)
    profile = workspace.profile()
    assert profile["candidate"]["full_name"] == "Chetan Babu M"
    assert profile["country_pack"] == "ie"
    assert profile["candidate"]["timezone"] == "Europe/Dublin"
    assert profile["reapply"]["auto_ghost"] is False
    old_application = {
        "id": "old-application",
        "company": "Example Co",
        "title": "Data Analyst",
        "status": "applied",
        "application_date": "2024-01-01",
        "updated_at": "2024-01-01",
    }
    assert due_for_ghosting([old_application], profile, today=date(2026, 9, 25)) == []
    assert not check("Example Co", "Data Analyst", [old_application], profile=profile)["blocked"]


def test_no_application_without_date_and_shared_activity(tmp_path):
    _, workspace = temporary_workspace(tmp_path)
    services = CareerServices(workspace)
    job = workspace.add_job(
        "Example Co",
        "Data Analyst",
        "Ireland",
        "https://example.com/jobs/123",
        "Analyze reports and build dashboards with SQL and Power BI for stakeholders. "
        "Collaborate with business teams and document decisions and results.",
    )
    try:
        workspace.update_job(job["id"], "applied")
    except ValueError as exc:
        assert "actual application date" in str(exc)
    else:
        raise AssertionError("An application without a confirmed date was accepted")
    assert services.age_applications() == []
    assert workspace.get_job(job["id"])["status"] == "saved"
    assert any(event["action"] == "job_saved" for event in workspace.activity(limit=-1))


def test_default_profile_and_summary_route_are_chetan(tmp_path):
    root, workspace = temporary_workspace(tmp_path)
    CareerServices(workspace)
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=root)
    app = create_shell(profiles=store, schedule=False)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        profiles = client.get("/api/profiles")
        assert profiles.status_code == 200
        assert profiles.json()["profiles"][0]["name"] == "Chetan Babu M"
        health = client.get("/p/chetan/api/health")
        summary = client.get("/p/chetan/api/v2/summary")
        assert health.status_code == 200
        assert summary.status_code == 200
