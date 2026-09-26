"""Through the HTTP API: a posting that refuses sponsorship is never listed, but stays visible and restorable."""
from fastapi.testclient import TestClient

from backend.dashboard.app import create_app
from test_career_workspace import workspace, JD  # noqa: F401 - fixtures
from test_workspace_v2 import service  # noqa: F401

REFUSAL = "Must be authorized to work in the United States without sponsorship now or in the future."


def posting(company="Acme Analytics", title="Data Engineer", url="https://careers.acme.test/jobs/1", extra=""):
    return {"company": company, "title": title, "location": "Austin, TX", "url": url, "description": JD + " " + extra}


def test_refusal_is_excluded_listed_with_its_sentence_and_restorable(service):
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        refused = client.post("/api/v2/jobs", json=posting(extra=REFUSAL))
        assert refused.status_code == 200
        body = refused.json()
        assert body["excluded"] is True and body["job"] is None
        assert body["reason"] == "no_sponsorship" and "without sponsorship" in body["sentence"]

        summary = client.get("/api/v2/summary").json()
        assert summary["jobs"] == []
        assert summary["counts"]["excluded"] == 1
        [row] = summary["excluded_jobs"]
        assert row["company"] == "Acme Analytics" and "without sponsorship" in row["sentence"] and row["source"] == "manual"

        listed = client.get("/api/v2/excluded").json()["items"]
        assert [item["id"] for item in listed] == [row["id"]]

        # Posting it again is still excluded (the posting's own words decide), never saved.
        assert client.post("/api/v2/jobs", json=posting(extra=REFUSAL)).json()["excluded"] is True
        assert client.get("/api/v2/summary").json()["jobs"] == []

        # A wrong call is correctable: restore saves it as an ordinary job and keeps the audit row.
        restored = client.post(f"/api/v2/excluded/{row['id']}/restore")
        assert restored.status_code == 200
        job = restored.json()["job"]
        assert job["company"] == "Acme Analytics" and job["sponsor_tier"] == "C"
        after = client.get("/api/v2/summary").json()
        assert [j["id"] for j in after["jobs"]] == [job["id"]]
        assert client.get("/api/v2/excluded", params={"include_restored": True}).json()["items"][0]["restored_at"]
        assert client.post(f"/api/v2/excluded/{row['id']}/restore").status_code == 400


def test_silent_posting_is_saved_with_a_tier_and_the_same_role_is_blocked(service):
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        saved = client.post("/api/v2/jobs", json=posting()).json()
        assert not saved.get("excluded") and saved["job"]["sponsor_tier"] in {"S", "A", "B", "C"}
        # The same URL is a duplicate; the same company and role at a new URL is a re-apply.
        assert client.post("/api/v2/jobs", json=posting()).json()["duplicate"] is True
        again = client.post("/api/v2/jobs", json=posting(url="https://careers.acme.test/jobs/2")).json()
        assert again["blocked"] is True and again["rule"] == "same_role"
        # A different role at the same company is fine.
        other = client.post("/api/v2/jobs", json=posting(title="Machine Learning Engineer", url="https://careers.acme.test/jobs/3")).json()
        assert other["job"]["title"] == "Machine Learning Engineer"
        recheck = client.post(f"/api/v2/jobs/{saved['job']['id']}/sponsorship")
        assert recheck.status_code == 200


def test_excluded_postings_never_reach_the_tracker_or_summary_markdown(service):
    service.add_posting(posting(extra=REFUSAL))
    service.w.export_tracking()
    for projection in ("data/pipeline.md", "data/application-tracker.md", "data/applied-companies.md"):
        assert "Acme Analytics" not in (service.w.root / projection).read_text(), projection
    summary = (service.w.root / "data/output/SUMMARY.md").read_text()
    assert "Acme Analytics" in summary and "without sponsorship" in summary
    excluded_section = summary.split("Excluded", 1)[1]
    assert "Acme Analytics" in excluded_section


def test_posting_sweep_moves_a_changed_posting_to_excluded_and_restore_brings_it_back(service):
    from backend.job_quality import JobQualityService
    job = service.add_posting(posting())["job"]
    page = {"status": 200, "final_url": job["url"], "text": "Data Engineer. " + JD + " " + REFUSAL}
    result = JobQualityService(service, fetcher=lambda url: page).verify_posting(job["id"])
    assert result["excluded"] and any("moved to Excluded" in line for line in result["evidence"])
    assert service.w.jobs() == []
    [row] = service.excluded()
    assert row["source"] == "sweep" and "without sponsorship" in row["sentence"]
    restored = service.restore_excluded(row["id"])
    assert restored["job"]["id"] == job["id"] and not restored["job"]["deleted_at"]
    assert [j["id"] for j in service.w.jobs()] == [job["id"]]
    # Chetan's restore sticks: the next sweep and a manual re-check see the same sentence and leave it.
    assert not JobQualityService(service, fetcher=lambda url: page).verify_posting(job["id"])["excluded"]
    assert service.reevaluate_sponsorship(job["id"])["excluded"] is False
    assert [j["id"] for j in service.w.jobs()] == [job["id"]]
    # A silent re-check leaves a job alone.
    quiet = JobQualityService(service, fetcher=lambda url: {"status": 200, "final_url": job["url"], "text": "Data Engineer. " + JD})
    assert not quiet.verify_posting(job["id"])["excluded"]


def test_cli_add_goes_through_the_gate(service, tmp_path, monkeypatch, capsys):
    """career.py add used to call add_job directly and skipped the sponsorship gate."""
    import json
    import sys
    import career
    monkeypatch.setattr(career, "Workspace", lambda *a, **k: service.w)
    refused = tmp_path / "refused.json"
    refused.write_text(json.dumps(posting(extra=REFUSAL)))
    monkeypatch.setattr(sys, "argv", ["career.py", "add", "--file", str(refused)])
    career.main()
    assert json.loads(capsys.readouterr().out)["excluded"] is True
    assert service.w.jobs() == [] and service.excluded()[0]["source"] == "cli"
    silent = tmp_path / "silent.json"
    silent.write_text(json.dumps(posting(title="Analytics Engineer", url="https://careers.acme.test/jobs/9")))
    monkeypatch.setattr(sys, "argv", ["career.py", "add", "--file", str(silent)])
    career.main()
    assert json.loads(capsys.readouterr().out)["job"]["sponsor_tier"] in {"S", "A", "B", "C"}


def test_a_restored_posting_keeps_its_real_tier(service):
    """Johns Hopkins was restored as tier C in the live test (24 Sep 2026); a university is S."""
    note = ("No visa-sponsorship or citizenship sentence was visible, but the posting says: " + REFUSAL)
    service.add_posting(posting(company="Hopkins State University", url="https://jobs.hopkins-state.edu/1", extra=note))
    [row] = service.excluded()
    restored = service.restore_excluded(row["id"])["job"]
    assert restored["sponsor_tier"] == "S"
