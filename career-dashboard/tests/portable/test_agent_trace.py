"""The live trace behind the Agents tab's side panel: what each run searched, read and decided."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from backend.services import job_sources, observability
from backend.services.agents import AgentRunner
from backend.services.workspace_v2 import CareerServices

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/scripts"))
from career import Workspace  # noqa: E402

JD = ("About the role: join the finance insights team in Dublin.\n"
      "Base salary: EUR 42,000 per year.\n"
      "Requirements:\n- Strong SQL for reporting queries\n- Power BI dashboards for stakeholders\n"
      "- Python for data cleaning\nResponsibilities: build weekly KPI reports and explain trends to managers.")


def ireland_profile(tmp_path):
    root = tmp_path / "profile"
    (root / "data/config").mkdir(parents=True)
    (root / "data/context").mkdir(parents=True)
    (root / "data/config/profile.yml").write_text(yaml.safe_dump({
        "country_pack": "ie", "target_markets": ["ie"],
        "work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "noncitizen",
                                                "needs_sponsorship_later": "yes"}},
        "candidate": {"full_name": "Sample Person", "preferred_name": "Sample", "timezone": "Europe/Dublin"},
        "target_roles": {"primary": ["Data Analyst"], "max_years_required": 3},
        "scoring": {"highest_degree": "a Master's"},
    }), encoding="utf-8")
    (root / "data/context/evidence.yml").write_text(yaml.safe_dump({
        "claims": [{"id": "SKILL-001", "category": "skill", "title": "Data skills", "status": "user_reported",
                    "approved_facts": ["SQL", "Power BI", "Python", "Excel"]}],
        "projects": [],
    }), encoding="utf-8")
    return CareerServices(Workspace(root))


def wait_for(services, run_id, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        with services.w.connect() as db:
            row = db.execute("SELECT state, result, error FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if row and row["state"] in ("completed", "failed"):
            assert row["state"] == "completed", row["error"]
            return json.loads(row["result"])
        time.sleep(0.05)
    raise AssertionError("the run did not finish")


@pytest.fixture(autouse=True)
def postings_stay_open(monkeypatch):
    from backend.job_quality import JobQualityService

    monkeypatch.setattr(JobQualityService, "_fetch", staticmethod(lambda url: {"status": 200, "final_url": url, "text": "Open"}))


def test_a_feeds_search_traces_each_site_read_and_each_posting_decided(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    runner = AgentRunner(services)
    found = job_sources.make_posting("Acme Analytics", "Data Analyst", "https://jobs.example/acme/1", "Dublin, Ireland", JD,
                                     source="directory", source_kind="employer_feed",
                                     vouched="Acme Analytics is in the verified IE employer directory.", requisition_id="1")
    elsewhere = job_sources.make_posting("Far Away Ltd", "Data Analyst", "https://jobs.example/far/2", "Lagos, Nigeria", JD,
                                         source="directory", source_kind="employer_feed", requisition_id="2")

    def harvest(root, sources, **options):
        options["progress"]("Acme Analytics", url="https://boards.greenhouse.io/acme", found=1, error="")
        options["progress"]("Broken Co", url="https://boards.greenhouse.io/broken", found=0, error="HTTP 500")
        return [found, elsewhere], ["stub coverage"]

    monkeypatch.setattr(job_sources, "harvest", harvest)
    try:
        queued = runner.enqueue("discovery", None, None, None, "feeds", count=2,
                                focus={"hunt": True, "sources": ["directory"]})
        result = wait_for(services, queued["id"])
        record = observability.trace(services, queued["id"])
    finally:
        runner.pool.shutdown(wait=True)

    kinds = [event["kind"] for event in record["events"]]
    assert kinds[-1] == "completed"
    stages = [event["label"] for event in record["events"] if event["kind"] == "stage"]
    assert "Checking each posting" in stages
    assert [(s["label"], s["url"], s["error"]) for s in record["sources"]] == [
        ("Acme Analytics", "https://boards.greenhouse.io/acme", ""),
        ("Broken Co", "https://boards.greenhouse.io/broken", "HTTP 500"),
    ]
    # Every posting has an outcome with its reason, and a saved one links to the saved job.
    decided = {event["label"]: event for event in record["events"] if event["kind"] == "check"}
    assert decided["Far Away Ltd — Data Analyst"]["outcome"] == "rejected"
    assert decided["Far Away Ltd — Data Analyst"]["stage"] == "market"
    saved = decided["Acme Analytics — Data Analyst"]
    assert saved["outcome"] == "saved" and saved["job_id"] in result["added_job_ids"]
    assert record["checks"] == {"rejected": 1, "saved": 1}
    assert any(event["kind"] == "note" and "No AI" in event["label"] for event in record["events"])

    # The activity list stays small: only the step timeline, never each posting.
    listed = next(r for r in observability.activity(services, runner)["runs"] if r["id"] == queued["id"])
    assert {event["kind"] for event in listed["events"]} <= set(observability.TIMELINE)


def test_a_research_run_shows_each_call_before_it_answers_and_the_pages_it_cites(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREER_FEATURES", "-graph_research")  # the classic three-call research step
    services = ireland_profile(tmp_path)
    job = services.w.add_job(
        "Example Company", "Data Analyst", "Dublin, Ireland", "https://example.org/jobs/analyst",
        "The analyst will maintain SQL reporting dashboards and explain findings to the business team. "
        "This is a documented test posting for a local profile.",
    )
    runner = AgentRunner(services)
    runner.gateway.resolve = lambda action, provider=None, model=None: (
        SimpleNamespace(id="kimi_cli", capabilities={"web"}), "kimi-runtime")
    report = {"summary": "Checked", "report": "Public facts", "limitations": [],
              "sources": [{"title": "About Example", "url": "https://example.org/about", "accessed_at": "2026-09-26"}]}
    runner.gateway.generate = lambda action, text, shape, **options: report
    try:
        queued = runner.enqueue("research", job["id"])
        runner.pool.shutdown(wait=True)
        record = observability.trace(services, queued["id"])
    finally:
        runner.pool.shutdown(wait=True)

    assert record["run"]["state"] == "completed", record["run"]["error"]
    kinds = [event["kind"] for event in record["events"]]
    # Each call is written down before it is made, so a slow call shows as still thinking.
    assert kinds.count("ai_start") == kinds.count("ai_call") == 3
    assert kinds.index("ai_start") < kinds.index("ai_call")
    start = next(event for event in record["events"] if event["kind"] == "ai_start")
    assert start["provider"] == "kimi_cli" and start["web"] is True
    assert [(s["label"], s["url"]) for s in record["sources"]] == [("About Example", "https://example.org/about")]
    notes = [event["label"] for event in record["events"] if event["kind"] == "note"]
    assert any("never your profile" in note for note in notes)


def test_an_unknown_run_has_no_trace(tmp_path):
    assert observability.trace(ireland_profile(tmp_path), "missing") is None
