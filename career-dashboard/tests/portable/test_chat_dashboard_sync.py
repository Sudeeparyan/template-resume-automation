"""The external chat CLI and the dashboard read the same selected profile state."""

from __future__ import annotations

import json
import sys

import yaml
from fastapi.testclient import TestClient

from backend.dashboard.shell import create_shell
from backend.profiles import ProfileStore
from career import Workspace
import backend.profiles
import career
import workspace


def test_dashboard_changes_reach_chat_cli_and_chat_changes_reach_dashboard(tmp_path, monkeypatch, capsys):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    pid = store.create("Example Person")["id"]
    root = store.root_for(pid)
    (root / "data/config/profile.yml").write_text(yaml.safe_dump({
        "country_pack": "ie", "target_markets": ["ie"], "candidate_revision": "2026-09-27.1",
        "candidate": {"full_name": "Example Person", "timezone": "Europe/Dublin"},
        "work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "citizen"}},
    }), encoding="utf-8")
    (root / "data/context/evidence.yml").write_text(yaml.safe_dump({
        "candidate_revision": "2026-09-27.1",
        "claims": [{"id": "FACT-EXAMPLE", "category": "other",
                    "approved_external_use": "Example fixture fact."}], "projects": [],
    }), encoding="utf-8")
    store.update(pid, state="ready")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    monkeypatch.setattr(backend.profiles, "store", lambda: store)

    with TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1") as client:
        base = f"/p/{pid}/api"
        # A user changes their goal in the dashboard. A subsequent chat command reads it fresh.
        update = client.put(base + "/v2/goals", json={
            "weekly_target": 3, "workdays": [0, 1, 2, 3, 4], "start_date": "2026-01-01",
        })
        assert update.status_code == 200, update.text
        monkeypatch.setattr(sys, "argv", ["workspace.py", "summary", "--profile", pid])
        workspace.main()
        summary = json.loads(capsys.readouterr().out)
        assert summary["goals"]["weekly_target"] == 3

        fact = client.post(base + "/v2/profile/items", json={
            "kind": "fact", "title": "Volunteer work", "fields": {
                "title": "Volunteer work", "details": "I volunteer on weekends."
            },
        })
        assert fact.status_code == 201, fact.text
        monkeypatch.setattr(sys, "argv", ["workspace.py", "profile", "--profile", pid])
        workspace.main()
        knowledge = json.loads(capsys.readouterr().out)
        assert any(item["id"] == fact.json()["id"] and
                   item["summary"] == "I volunteer on weekends." for item in knowledge)
        evidence = yaml.safe_load((root / "data/context/evidence.yml").read_text(encoding="utf-8"))
        assert any("I volunteer on weekends." in json.dumps(claim)
                   for claim in evidence["claims"])
        assert evidence["candidate_revision"] != "2026-09-27.1"
        assert client.get(base + "/v2/summary").json()["profile_dirty"] is False

        job = Workspace(root).add_job("Example Company", "Data Analyst", "Dublin, Ireland",
                                      "https://example.org/jobs/data-analyst",
                                      "Analyze sample data in Dublin, document findings, maintain reports, "
                                      "review source records, and share findings with the team.")
        # A status set through the dashboard must appear in the external chat command.
        changed = client.patch(base + f"/jobs/{job['id']}", json={
            "status": "applied", "application_date": "2026-09-26",
        })
        assert changed.status_code == 200, changed.text
        monkeypatch.setattr(sys, "argv", ["career.py", "--profile", pid, "jobs"])
        career.main()
        jobs = json.loads(capsys.readouterr().out)
        assert next(j for j in jobs if j["id"] == job["id"])["status"] == "applied"

        # A later chat status update is immediately visible to the dashboard's profile API.
        monkeypatch.setattr(sys, "argv", ["career.py", "--profile", pid, "update", job["id"],
                                             "--status", "interview"])
        career.main()
        capsys.readouterr()
        dashboard = client.get(base + "/v2/summary")
        assert dashboard.status_code == 200, dashboard.text
        current = next(j for j in dashboard.json()["jobs"] if j["id"] == job["id"])
        assert current["status"] == "interview"
        assert dashboard.json()["counts"]["interviews"] == 1
