"""The Daily Search pipeline: a custom job count, only the helpers she switched on, and honest estimates.

Find jobs runs first; then, for each new job, only the switched-on helpers run
(company research → tailor → study plan → one-page PDF) on the AI she chose.
Stop ends the run after the current step, the daily plan still caps the count,
and finished runs teach the page how long each step really takes.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from test_career_workspace import workspace  # noqa: E402,F401 - fixture
from test_tailoring import service  # noqa: E402,F401 - fixture (pinned day, workflows)
import backend.ai  # noqa: E402
from backend.job_quality import JobQualityService  # noqa: E402
from backend.services.agents import AgentRunner  # noqa: E402
from backend.services.pipeline import FIND, Pipeline, find_seconds  # noqa: E402
from backend.services.resume_studio import ResumeStudio  # noqa: E402
from backend.dashboard.app import create_app  # noqa: E402
from fixtures.personas import PERSONAS, posting  # noqa: E402

SPEC = PERSONAS["fresher"]
REPORT = {"summary": "Registered employer with a public data team.", "report": "Hiring for entry-level data roles.",
          "sources": [{"title": "Careers", "url": "https://brightbyte.example/careers", "accessed_at": "2026-09-12"}],
          "limitations": ["Team size not published."]}


def found():
    return {"summary": "Fixture search.", "rejected_leads": [],
            "jobs": [posting(company, n + 1) for n, company in enumerate(SPEC["companies"])]}


def fake_ai(prompt, schema, **_options):
    """Discovery gets the five fixture postings; every report-shaped call gets REPORT."""
    return found() if "jobs" in schema.get("properties", {}) else dict(REPORT)


@pytest.fixture
def onboarded(service):
    for entry in SPEC["knowledge"]:
        service.save_knowledge(entry)
    service.reconcile_knowledge()
    service.save_goals({"weekly_target": 35, "workdays": [0, 1, 2, 3, 4, 5, 6], "start_date": service.today()})
    return service


@pytest.fixture
def pipeline(onboarded, monkeypatch):
    monkeypatch.setattr(backend.ai, "ready_providers", lambda root: {"codex": True, "claude_code": False, "kimi_cli": False})
    runner = AgentRunner(onboarded, execute=fake_ai)
    runner.cache.configure(30)
    studio = ResumeStudio(onboarded)
    runner.studio = studio
    return Pipeline(onboarded, runner, studio, poll_seconds=0.02)


def config(**over):
    return {"count": 2, "source": "default", "provider": "codex", "model": "codex-runtime",
            "steps": {"research": True, "tailor": True, "study_plan": False, "pdf": True}, **over}


def finish(pipeline, run):
    pipeline.thread.join(timeout=60)
    return pipeline.get(run["id"])


def test_discovery_saves_the_requested_number_of_jobs(onboarded):
    runner = AgentRunner(onboarded, execute=fake_ai)
    runner.enqueue("discovery", count=3)
    runner.pool.shutdown(wait=True)
    run = onboarded.runs()[0]
    assert run["state"] == "completed", run["error"]
    assert len(run["result"]["added_job_ids"]) == 3
    assert len(onboarded.w.jobs()) == 3


def test_a_count_outside_one_to_fifteen_or_on_another_kind_is_refused(onboarded):
    runner = AgentRunner(onboarded, execute=fake_ai)
    for count in (0, 16):
        with pytest.raises(ValueError, match="between 1 and 15"):
            runner.enqueue("discovery", count=count)
    with pytest.raises(ValueError, match="between 1 and 15"):
        runner.enqueue("research", "job-1", count=3)


def test_the_balanced_mix_scales_with_the_count(service):
    quality = JobQualityService(service)
    wanted = lambda total: [(s["category"], s["needed"]) for s in quality.balanced_five([], total=total)["shortages"]]
    assert wanted(5) == [("startup", 2), ("mid", 1), ("large", 2)]
    assert wanted(10) == [("startup", 4), ("mid", 2), ("large", 4)]
    assert wanted(1) == [("large", 1)]


def test_only_the_switched_on_helpers_run_for_each_new_job(pipeline, monkeypatch):
    tailored = []

    def tailor(job_id, team):
        tailored.append((job_id, team.tiers))
        return {"items": {"verified": 3, "predicted": 1}}

    monkeypatch.setattr(pipeline.studio, "tailor", tailor)
    monkeypatch.setattr(pipeline.studio, "fit", lambda job_id, revision: {"preview": {"page_count": 1}})
    run = finish(pipeline, pipeline.start(config()))

    assert run["state"] == "completed", run["error"]
    progress = run["progress"]
    assert progress["find"]["state"] == "done" and progress["find"]["found"] == 2
    assert len(progress["jobs"]) == 2
    for job in progress["jobs"]:
        # Study plan was switched off, so it is not even listed.
        assert set(job["steps"]) == {"research", "tailor", "pdf"}
        assert all(step["state"] == "done" for step in job["steps"].values()), job
        assert "Assurance" in job["steps"]["tailor"]["note"]
        folder = pipeline.w.current_folder(job["id"])
        research = (folder / "company-research.md").read_text(encoding="utf-8")
        assert REPORT["summary"] in research and "https://brightbyte.example/careers" in research
    # The tailor wrote on the chosen AI; discovery asked for the chosen count.
    assert [tiers["strong"] for _, tiers in tailored] == [("codex", "codex-runtime")] * 2
    with pipeline.w.connect() as db:
        discovery = db.execute("SELECT input, provider FROM agent_runs WHERE kind='discovery'").fetchone()
        kinds = [row[0] for row in db.execute("SELECT kind FROM agent_runs")]
    assert json.loads(discovery["input"]) == {"count": 2} and discovery["provider"] == "codex"
    assert "study_plan" not in kinds and kinds.count("research") == 2
    # Her choice is remembered, and "where to look" is shared with the chat's find-jobs.
    assert pipeline.preferences()["steps"]["study_plan"] is False
    assert pipeline.s.pref("discovery_preferences")["preset"] == "default"


def test_a_failed_helper_is_reported_and_the_next_one_still_runs(pipeline, monkeypatch):
    def tailor(job_id, team):
        raise ValueError("Your Claude subscription's usage limit is reached.")

    monkeypatch.setattr(pipeline.studio, "tailor", tailor)
    monkeypatch.setattr(pipeline.studio, "fit", lambda job_id, revision: {"preview": {"page_count": 1}})
    run = finish(pipeline, pipeline.start(config(count=1, steps={"tailor": True, "pdf": True})))
    assert run["state"] == "completed"
    steps = run["progress"]["jobs"][0]["steps"]
    assert steps["tailor"]["state"] == "failed" and "usage limit" in steps["tailor"]["error"]
    assert steps["pdf"]["state"] == "done"


def test_stop_ends_the_run_after_the_current_step(pipeline, monkeypatch):
    def tailor(job_id, team):
        pipeline.stop(pipeline.status()["current"]["id"])
        return {"items": {"verified": 1, "predicted": 0}}

    monkeypatch.setattr(pipeline.studio, "tailor", tailor)
    run = finish(pipeline, pipeline.start(config(steps={"tailor": True, "study_plan": True})))
    assert run["state"] == "stopped"
    first, second = run["progress"]["jobs"]
    assert first["steps"]["tailor"]["state"] == "done"
    assert first["steps"]["study_plan"]["state"] == "skipped"
    assert {step["state"] for step in second["steps"].values()} == {"skipped"}


def test_the_daily_plan_still_caps_the_search(pipeline):
    pipeline.s.save_goals({"weekly_target": 1, "workdays": [0], "start_date": pipeline.s.today()})
    assert pipeline.s.goals()["remaining_today"] == 0
    with pytest.raises(ValueError, match="plan is already complete"):
        pipeline.start(config())


def test_an_ai_that_is_not_on_this_pc_cannot_start_a_run(pipeline):
    with pytest.raises(ValueError, match="not installed"):
        pipeline.start(config(provider="claude_code", model="sonnet"))
    with pytest.raises(ValueError, match="Choose a model"):
        pipeline.start(config(model="gpt-9"))


def test_finished_runs_teach_the_estimate(pipeline):
    assert pipeline.speeds(pipeline.providers())["codex:codex-runtime"]["find_ai"] == {
        "factor": 1.2, "learned": False, "runs": 0}
    progress = {"jobs_target": 5, "find": {"state": "done", "seconds": find_seconds("find_ai", 5) * 2},
                "jobs": [{"id": "j", "steps": {"tailor": {"state": "done", "seconds": 90},
                                               "pdf": {"state": "done", "seconds": 1, "quick": True}}}]}
    with pipeline.w.connect() as db:
        db.execute("INSERT INTO pipeline_runs(id,state,config,progress,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   ("old", "completed", json.dumps(config(count=5)), json.dumps(progress), "2026-09-11", "2026-09-11"))
    speeds = pipeline.speeds(pipeline.providers())["codex:codex-runtime"]
    assert speeds["find_ai"] == {"factor": 2.0, "learned": True, "runs": 1}
    assert speeds["tailor"]["factor"] == 0.5  # 90 s against the 3-minute first-run estimate
    assert speeds["pdf"]["learned"] is False  # an already-current PDF says nothing about compile time
    assert FIND["find_pages"]["tokens"] == 0
    assert "tokens" not in speeds["tailor"]


def test_measured_tailor_tokens_replace_the_first_run_guess(pipeline):
    """The tailor's specialist reports its usage; the page shows that instead of the 22K guess."""
    from backend.ai import usage_recorder

    record = usage_recorder(pipeline.s)
    for used in (5296, 7000, 6000):
        record({"agent": "job_tailor", "provider": "codex", "model": "codex-runtime",
                "input_tokens": used, "output_tokens": 1000})
    speeds = pipeline.speeds(pipeline.providers())
    assert speeds["codex:codex-runtime"]["tailor"]["tokens"] == 7000  # median of 6296, 8000, 7000
    assert "tokens" not in speeds["claude_code:sonnet"]["tailor"]


def test_the_page_api_lists_choices_and_validates(pipeline, onboarded, monkeypatch):
    with TestClient(create_app(onboarded.w.root), base_url="http://127.0.0.1") as client:
        overview = client.get("/api/v2/pipeline").json()
        assert {s["id"] for s in overview["steps"]} == {"research", "tailor", "study_plan", "pdf"}
        local = {p["id"]: p for p in overview["providers"] if p["kind"] == "local"}
        assert set(local) == {"claude_code", "codex", "kimi_cli"}
        assert local["codex"]["ready"] and not local["claude_code"]["ready"]
        assert [m["id"] for m in local["claude_code"]["models"]] == ["sonnet", "opus", "haiku"]
        assert overview["preferences"]["count"] == 5 and overview["plan"]["remaining_today"] == 5
        assert overview["current"] is None and "tools" in overview
        bad = client.put("/api/v2/pipeline/preferences", json=config(provider="nobody"))
        assert bad.status_code == 400 and "AI apps listed" in bad.json()["detail"]
        saved = client.put("/api/v2/pipeline/preferences", json=config(count=7))
        assert saved.status_code == 200 and saved.json()["count"] == 7
        health = client.get("/api/health").json()
        assert isinstance(health["pid"], int) and health["busy"] is False


def test_research_started_outside_daily_search_is_saved_for_the_tailor(onboarded):
    """The Agents tab, the assistant and the morning run start research too; the tailor must see it."""
    runner = AgentRunner(onboarded, execute=fake_ai)
    runner.cache.configure(30)
    runner.enqueue("discovery", count=1)
    runner.pool.shutdown(wait=True)
    job_id = onboarded.runs()[0]["result"]["added_job_ids"][0]
    ResumeStudio(onboarded).open(job_id)
    runner = AgentRunner(onboarded, execute=fake_ai)
    runner.enqueue("research", job_id)
    runner.pool.shutdown(wait=True)
    run = next(r for r in onboarded.runs() if r["kind"] == "research")
    assert run["state"] == "completed", run["error"]
    research = (onboarded.w.current_folder(job_id) / "company-research.md").read_text(encoding="utf-8")
    assert REPORT["summary"] in research and "Research pending" not in research
    assert run["result"]["path"].endswith("company-research.md")


def test_the_ai_comes_from_settings_when_the_page_names_none(pipeline, monkeypatch):
    """Settings is the one place the AI is chosen; Daily Search follows it and never remembers its own."""
    monkeypatch.setattr(pipeline.studio, "tailor", lambda job_id, team: {"items": {"verified": 3, "predicted": 1}})
    run = finish(pipeline, pipeline.start({"count": 1, "source": "default",
                                           "steps": {"research": False, "tailor": True, "study_plan": False, "pdf": False}}))
    assert run["state"] == "completed", run["error"]
    assert run["config"]["provider"] == pipeline.preferences()["provider"] == "codex"
    assert set(pipeline.s.pref("pipeline_preferences")) == {"count", "source", "steps"}


def test_jobs_an_earlier_run_left_unprepared_are_picked_up(pipeline, monkeypatch):
    """The 7 AM run asks for this: a job found by a run that stopped midway is prepared the next time."""
    left = pipeline.w.add_job("Earlier Co", "Data Engineer", "Austin, TX", "https://earlier.example/jobs/1",
                              "Build data pipelines in Python and SQL for an entry-level data team. " * 3)
    monkeypatch.setattr(pipeline, "_agent", lambda kind, *a, **k: {"id": "d", "result": {"added_job_ids": []}})
    monkeypatch.setattr(pipeline.studio, "tailor", lambda job_id, team: {"items": {"verified": 3, "predicted": 1}})
    steps = {"research": False, "tailor": True, "study_plan": False, "pdf": False}
    run = finish(pipeline, pipeline.start(config(count=3, steps=steps, include_unprepared=True)))
    assert [job["id"] for job in run["progress"]["jobs"]] == [left["id"]]
    assert "not yet prepared" in run["progress"]["find"]["note"]
    assert run["progress"]["jobs"][0]["steps"]["tailor"]["state"] == "done"
    # Without the option, a run prepares only what it found itself.
    again = finish(pipeline, pipeline.start(config(count=3, steps=steps)))
    assert again["progress"]["jobs"] == []
