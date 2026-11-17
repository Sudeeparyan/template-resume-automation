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


def test_url_only_import_and_saved_job_preparation_use_shared_services(tmp_path, monkeypatch):
    from backend.services import lead_resolver
    from backend.services.pipeline import Pipeline

    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    pid, root = make_profile(store, "Example Person")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    url = "https://careers.example.org/jobs/data-1"
    monkeypatch.setattr(lead_resolver, "resolve", lambda value: {
        "status": "read", "url": value, "method": "structured_data",
        "posting": {"company": "Example Employer", "title": "Data Analyst", "location": "Dublin, Ireland",
                    "description": "Use SQL and Python to analyse data and maintain reports. " * 4},
    })
    with TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1") as client:
        response = client.post(f"/p/{pid}/api/v2/jobs/import", json={"url": url})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "saved" and result["job"]["url"] == url
        assert result["job"]["company"] == "Example Employer"
        job_id = result["job"]["id"]

        called = {}
        def prepare(self, value, config):
            called.update(job_id=value, config=config)
            return {"id": "practice-run", "state": "queued"}
        monkeypatch.setattr(Pipeline, "prepare_existing", prepare)
        response = client.post(f"/p/{pid}/api/v2/jobs/{job_id}/prepare-application", json={
            "count": 1, "source": "default", "steps": {"cover_letter": True},
        })
        assert response.status_code == 202, response.text
        assert response.json()["id"] == "practice-run"
        assert called["job_id"] == job_id
        assert called["config"]["count"] == 1
        assert called["config"]["steps"]["cover_letter"] is True


def test_url_import_asks_for_details_without_guessing(tmp_path, monkeypatch):
    from backend.services import lead_resolver

    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    pid, _root = make_profile(store, "Example Person")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    monkeypatch.setattr(lead_resolver, "resolve", lambda value: {
        "status": "needs_employer_link", "url": value, "reason": "Paste the full posting text.",
    })
    with TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1") as client:
        response = client.post(f"/p/{pid}/api/v2/jobs/import", json={"url": "https://example.org/jobs/unknown"})
        assert response.status_code == 200
        assert response.json()["status"] == "needs_employer_link"
        assert Workspace(store.root_for(pid)).jobs() == []


def test_application_funnel_uses_recorded_dates_and_next_actions(tmp_path):
    from datetime import date, timedelta
    from backend.services.workspace_v2 import CareerServices

    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    _pid, root = make_profile(store, "Example Person")
    workspace = Workspace(root)
    today = date.today().isoformat()
    recent = workspace.add_job("Recent Example", "Data Analyst", "Dublin, Ireland",
        "https://example.org/jobs/recent", "Use SQL and Python to analyse data and write reports. " * 4)
    old = workspace.add_job("Earlier Example", "Data Analyst", "Dublin, Ireland",
        "https://example.org/jobs/earlier", "Use SQL and Python to analyse data and write reports. " * 4)
    workspace.update_job(recent["id"], "applied", application_date=today)
    with workspace.connect() as db:
        db.execute("UPDATE jobs SET created_at=?,updated_at=? WHERE id=?",
                   ((date.today() - timedelta(days=60)).isoformat(),
                    (date.today() - timedelta(days=60)).isoformat(), old["id"]))
    summary = CareerServices(workspace).summary()
    assert summary["funnel"]["periods"]["30"]["saved"] == 1
    assert summary["funnel"]["periods"]["90"]["saved"] == 2
    assert summary["funnel"]["periods"]["all"] == {"saved": 2, "applied": 1, "interviews": 0, "offers": 0}
    assert summary["funnel"]["next_actions"][0]["job_id"] == old["id"]


def test_loopback_practice_urls_keep_scheme_and_port_and_demo_is_not_paid():
    from backend.ai.router import is_paid
    from backend.services.postings import canonical_url

    assert canonical_url("http://127.0.0.1:8010/demo/postings/software") == "http://127.0.0.1:8010/demo/postings/software"
    assert canonical_url("http://localhost:8010/demo/postings/analytics") == "http://localhost:8010/demo/postings/analytics"
    assert canonical_url("http://[::1]:8010/demo/postings/software") == "http://[::1]:8010/demo/postings/software"
    assert canonical_url("http://example.org:8080/jobs/1") == "https://example.org:8080/jobs/1"
    assert canonical_url("https://example.org/jobs/1") == "https://example.org/jobs/1"
    assert is_paid("demo") is False
