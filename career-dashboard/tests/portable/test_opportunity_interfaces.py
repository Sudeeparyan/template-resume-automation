"""The dashboard, CLI and Assistant expose the same profile-scoped opportunity evidence."""
import json
import sys

import yaml
from fastapi.testclient import TestClient

import backend.profiles
import workspace
from backend.dashboard.api_v2 import PostingInput
from backend.dashboard.shell import create_shell
from backend.profiles import ProfileStore
from backend.services.source_coverage import Coverage
from career import Workspace


def make_profile(store, name):
    pid = store.create(name)["id"]
    root = store.root_for(pid)
    (root / "data/config/profile.yml").write_text(yaml.safe_dump({
        "country_pack": "ie", "target_markets": ["ie"], "candidate_revision": "test-1",
        "candidate": {"full_name": name, "timezone": "Europe/Dublin"},
        "work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "citizen"}},
    }), encoding="utf-8")
    (root / "data/context/evidence.yml").write_text(yaml.safe_dump({
        "candidate_revision": "test-1", "claims": [], "projects": [],
    }), encoding="utf-8")
    store.update(pid, state="ready")
    return pid, root


def test_coverage_and_salary_are_consistent_across_profile_interfaces(tmp_path, monkeypatch, capsys):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    pid, root = make_profile(store, "Example Person")
    other_id, other_root = make_profile(store, "Other Example")
    Coverage(root).checkpoint("ai:ie:1:county:kerry", {"page": 2}, "partial", found=3)
    Coverage(other_root).checkpoint("employer:https://other.example.org", {}, "blocked", error="Access blocked")
    job = Workspace(root).add_job("Example Employer", "Data Analyst", "Dublin, Ireland",
        "https://example.org/jobs/analyst", "Salary EUR 40,000 per year. Analyze sample data, maintain reports, "
        "document findings and support reporting across the team in Dublin, Ireland.")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    monkeypatch.setattr(backend.profiles, "store", lambda: store)
    with TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1") as client:
        response = client.get(f"/p/{pid}/api/v2/search/coverage")
        assert response.status_code == 200, response.text
        expected = response.json()
        assert [s["key"] for s in expected["sources"]] == ["ai:ie:1:county:kerry"]
        assert client.get(f"/p/{other_id}/api/v2/search/coverage").json()["sources"][0]["state"] == "blocked"
        monkeypatch.setattr(sys, "argv", ["workspace.py", "coverage", "--profile", pid])
        workspace.main()
        assert json.loads(capsys.readouterr().out) == expected

        monkeypatch.setattr(sys, "argv", ["workspace.py", "salary", "--profile", pid, "--job-id", job["id"]])
        workspace.main()
        salary = json.loads(capsys.readouterr().out)["opportunity"]
        current = client.get(f"/p/{pid}/api/v2/summary").json()["jobs"][0]["opportunity"]
        assert salary["salary_state"] == current["salary_state"] == "meets_floor"
        assert salary["salary"]["quote"] == current["salary"]["quote"]
        assert salary["salary"]["annual_min"] == 40000


def test_posting_input_retains_structured_salary_and_closing_date():
    structured = {"currency": "EUR", "value": {"minValue": 40000, "maxValue": 48000, "unitText": "YEAR"}}
    posting = PostingInput(company="Example Employer", title="Analyst", location="Ireland",
        url="https://example.org/jobs/analyst", description="Analyze data and maintain reports. " * 4,
        raw_salary=structured, valid_through="2026-10-31")
    assert posting.model_dump()["raw_salary"] == structured
    assert posting.model_dump()["valid_through"] == "2026-10-31"
