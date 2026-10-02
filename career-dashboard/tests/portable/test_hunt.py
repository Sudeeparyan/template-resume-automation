"""The overnight hunt: feeds pass, holding for an AI check, search memory, waiting for plans, stopping."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
import yaml

from backend.services import fit, hunt as hunt_module, job_sources, search_memory
from backend.services.agents import AgentRunner
from backend.services.hunt import Hunt
from backend.services.pipeline import STEP_IDS
from backend.services.search_plan import focus_instructions, plan_for, strategies
from backend.services.workspace_v2 import CareerServices

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/scripts"))
from career import Workspace  # noqa: E402

JD = ("About the role: join the finance insights team in Dublin.\n"
      "Base salary: EUR 42,000 per year.\n"
      "Requirements:\n- Strong SQL for reporting queries\n- Power BI dashboards for stakeholders\n"
      "- Python for data cleaning\nResponsibilities: build weekly KPI reports and explain trends to managers.")


def ireland_profile(tmp_path, roles=("Data Analyst",)):
    root = tmp_path / "profile"
    (root / "data/config").mkdir(parents=True)
    (root / "data/context").mkdir(parents=True)
    (root / "data/config/profile.yml").write_text(yaml.safe_dump({
        "country_pack": "ie", "target_markets": ["ie"],
        "work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "noncitizen",
                                                "needs_sponsorship_later": "yes"}},
        "candidate": {"full_name": "Sample Person", "preferred_name": "Sample", "timezone": "Europe/Dublin"},
        "target_roles": {"primary": list(roles), "max_years_required": 3},
        "scoring": {"highest_degree": "a Master's"},
    }), encoding="utf-8")
    (root / "data/context/evidence.yml").write_text(yaml.safe_dump({
        "claims": [{"id": "SKILL-001", "category": "skill", "title": "Data skills", "status": "user_reported",
                    "approved_facts": ["SQL", "Power BI", "Python", "Excel"]}],
        "projects": [],
    }), encoding="utf-8")
    return CareerServices(Workspace(root))


def posting(number=1, company="Acme Analytics", title="Data Analyst"):
    return job_sources.make_posting(company, title, f"https://jobs.example/acme/{number}", "Dublin, Ireland", JD,
                                    source="directory", source_kind="employer_feed",
                                    vouched=f"{company} is in the verified IE employer directory.",
                                    requisition_id=str(number))


class StubFitTeam:
    """A free plan's requirement check that meets every requirement it can quote."""
    served = ("kimi_cli", "kimi-runtime")

    def run(self, name, payload):
        assert name == "fit_analyst"
        text = payload["job"]["description"]
        items = []
        for line, term in (("Strong SQL for reporting queries", "SQL"), ("Power BI dashboards for stakeholders", "Power BI"),
                           ("Python for data cleaning", "Python")):
            if line in text:
                items.append({"text": term, "category": "required", "excerpt": line, "status": "met",
                              "evidence_ids": ["SKILL-001"], "note": ""})
        return {"requirements": items, "hard_blockers": [], "summary": "Meets the must-haves."}


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
    """Discovery re-checks saved postings first; here every page is simply open."""
    from backend.job_quality import JobQualityService

    monkeypatch.setattr(JobQualityService, "_fetch", staticmethod(lambda url: {"status": 200, "final_url": url, "text": "Open"}))


def feeds_run(runner, sources, **focus):
    return runner.enqueue("discovery", None, None, None, "feeds", count=2,
                          focus={"hunt": True, "sources": sources, "min_fit": 60, "require_ai_fit": True, **focus})


def test_a_feeds_pass_holds_for_the_ai_check_then_saves_and_remembers(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    runner = AgentRunner(services)
    found = [posting(1)]
    monkeypatch.setattr(job_sources, "harvest",
                        lambda root, sources, **kw: (list(kw["held"]) if "held" in sources else list(found), ["stub coverage"]))

    # No free plan: the plausible posting is held, not judged by rules.
    first = wait_for(services, feeds_run(runner, ["directory"])["id"])
    assert first["added_job_ids"] == [] and first["held"] == 1
    assert "held for an AI" in first["summary"]
    with services.w.connect() as db:
        assert search_memory.held_count(db) == 1

    # A free plan is back: the held posting is checked by AI and saved.
    monkeypatch.setattr(fit, "fit_team", lambda s: StubFitTeam())
    second = wait_for(services, feeds_run(runner, ["held"])["id"])
    assert len(second["added_job_ids"]) == 1
    job = services.w.get_job(second["added_job_ids"][0])
    assert job["company"] == "Acme Analytics" and job["fit_score"] >= 60
    assert "Checked by AI" in job["fit_rationale"]
    with services.w.connect() as db:
        assert search_memory.held_count(db) == 0
        assert search_memory.decided(db, found[0], fit.catalogue(services)["hash"])["outcome"] == "saved"

    # The next pass skips it instead of spending another AI check on it.
    third = wait_for(services, feeds_run(runner, ["directory"])["id"])
    assert third["added_job_ids"] == [] and "Skipped 1 postings" in third["summary"]


def test_a_plan_that_stops_answering_mid_check_holds_instead_of_judging_by_rules(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    runner = AgentRunner(services)

    class LimitedTeam(StubFitTeam):
        def run(self, name, payload):
            raise RuntimeError("fit_analyst could not run on kimi_cli: usage limit reached")

    monkeypatch.setattr(job_sources, "harvest", lambda root, sources, **kw: ([posting(5)], []))
    monkeypatch.setattr(fit, "fit_team", lambda s: LimitedTeam())
    result = wait_for(services, feeds_run(runner, ["directory"])["id"])
    assert result["added_job_ids"] == [] and result["held"] == 1
    assert "stopped answering part-way" in result["summary"]
    with services.w.connect() as db:
        assert search_memory.held_count(db) == 1


def test_the_hunt_bar_turns_away_a_fit_below_it(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    runner = AgentRunner(services)
    monkeypatch.setattr(job_sources, "harvest", lambda root, sources, **kw: ([posting(2)], []))
    monkeypatch.setattr(fit, "fit_team", lambda s: StubFitTeam())
    result = wait_for(services, feeds_run(runner, ["directory"], min_fit=99)["id"])
    assert result["added_job_ids"] == []
    assert any("below this search's bar of 99" in line for line in result["rejected_leads"])
    with services.w.connect() as db:
        remembered = search_memory.decided(db, posting(2), fit.catalogue(services)["hash"])
    assert remembered["outcome"] == "rejected" and remembered["stage"] == "fit"


def test_a_fit_decision_is_revisited_when_the_evidence_changes(tmp_path):
    services = ireland_profile(tmp_path)
    with services.w.connect() as db:
        search_memory.remember(db, posting(3), "rejected", stage="fit", reason="Fit 40/100", evidence_hash="old")
        assert search_memory.decided(db, posting(3), "old")
        assert search_memory.decided(db, posting(3), "new") is None
        search_memory.remember(db, posting(4), "excluded", stage="sponsorship", evidence_hash="old")
        assert search_memory.decided(db, posting(4), "new")["outcome"] == "excluded"


def test_the_plan_adapts_to_the_profile(tmp_path):
    services = ireland_profile(tmp_path, roles=("Data Analyst", "BI Developer"))
    plan = plan_for(services.w.root)
    assert plan["markets"] == ["ie"] and plan["plain_roles"] == ["data analyst", "bi developer"]
    assert plan["early_career"] and "graduate data analyst" in plan["board_keywords"]
    passes = strategies(services.w.root, plan=plan)
    kinds = [p["kind"] for p in passes]
    assert kinds[:5] == ["feeds"] * 5 and set(kinds[5:]) == {"ai"}
    assert [p["id"] for p in passes[:5]] == ["feeds:tracked", "feeds:directory", "feeds:gradireland", "feeds:jobs_ie",
                                             "feeds:askmanavi"]
    queries = " ".join(q for p in passes for q in p.get("queries", []))
    for site in ("site:irishjobs.ie", "site:gradireland.com", "site:myworkdayjobs.com", "site:linkedin.com/jobs/view",
                 "site:publicjobs.ie"):
        assert site in queries
    assert "graduate programme" in queries
    assert strategies(services.w.root, sources="feeds", plan=plan)[-1]["kind"] == "feeds"
    text = focus_instructions(passes[5])
    assert "FOCUS FOR THIS PASS" in text and passes[5]["queries"][0] in text


# ----- the loop, with a stand-in runner and pipeline ------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.01)


class FakePipeline:
    def __init__(self):
        self.steps = []
        self.studio = type("Studio", (), {"open": lambda self, job_id: None})()

    def preferences(self):
        return {"provider": "auto", "model": "auto", "count": 5, "source": "default", "steps": {}}

    def providers(self):
        return [{"id": "auto", "label": "Auto", "ready": True, "models": [{"id": "auto"}]}]

    def status(self):
        return {"current": None}

    def _web_choice(self, config):
        return config["provider"], config["model"]

    def run_step(self, step, job_id, config):
        assert config["free_only"] is True
        self.steps.append((step, job_id))
        return {"note": f"{step} done"}


class FakeRunner:
    """Answers each discovery pass by saving the next scripted company (or nothing)."""

    def __init__(self, services, script):
        self.s, self.script, self.calls = services, list(script), []

    def enqueue(self, kind, job_id, provider, model, preset, count=None, focus=None, free_only=False):
        self.calls.append({"preset": preset, "count": count, "focus": focus, "free_only": free_only, "provider": provider})
        company = self.script.pop(0) if self.script else None
        added = []
        if company:
            saved = self.s.add_posting({**posting(len(self.calls), company=company)}, source="discovery")
            added = [saved["job"]["id"]]
        run_id = f"run{len(self.calls)}"
        with self.s.w.connect() as db:
            db.execute("INSERT INTO agent_runs(id,kind,job_id,state,input,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                       (run_id, "discovery", None, "completed", "{}",
                        json.dumps({"added_job_ids": added, "jobs": [{}] * (3 if company else 1), "rejected_leads": []}),
                        self.s.now(), self.s.now()))
        return {"id": run_id}


def make_hunt(services, script, clock, ai_states=None):
    runner = FakeRunner(services, script)
    pipeline = FakePipeline()
    hunt = Hunt(services, runner, pipeline, poll=0.01, sleep=clock.sleep, clock=clock)
    states = list(ai_states or [])
    hunt._ai_state = lambda config: states.pop(0) if states else (True, None, "")
    return hunt, runner, pipeline


def run_inline(hunt, values):
    """start() without the thread: the same validation and row, then the loop on this thread."""
    started = {}
    real = hunt_module.threading.Thread

    class Inline:
        def __init__(self, target, args, name, daemon):
            started["call"] = (target, args)

        def start(self):
            pass

    hunt_module.threading.Thread = Inline
    try:
        run = hunt.start(values)
    finally:
        hunt_module.threading.Thread = real
    target, args = started["call"]
    target(*args)
    return hunt.get(run["id"])


def test_the_hunt_stops_at_its_target_and_prepares_each_job(tmp_path):
    services = ireland_profile(tmp_path)
    clock = FakeClock()
    hunt, runner, pipeline = make_hunt(services, ["Acme Analytics", "Birch Data"], clock)
    done = run_inline(hunt, {"target": 2, "hours": 2, "steps": {s: s in ("tailor", "pdf") for s in STEP_IDS}})
    assert done["state"] == "completed", done["error"]
    saved = done["progress"]["saved"]
    assert [job["company"] for job in saved] == ["Acme Analytics", "Birch Data"]
    assert len(runner.calls) == 2  # it stopped as soon as the target was met
    first = runner.calls[0]
    assert first["preset"] == "feeds" and first["focus"]["hunt"] and first["focus"]["sources"] == ["tracked"]
    assert first["focus"]["min_fit"] == 70 and first["focus"]["require_ai_fit"] and first["free_only"]
    assert [step for step, _ in pipeline.steps] == ["tailor", "pdf", "tailor", "pdf"]
    report = services.w.root / done["progress"]["report"]
    text = report.read_text(encoding="utf-8")
    assert "2 of 2 jobs saved" in text and "Acme Analytics" in text and "Nothing was submitted" in text


def test_the_hunt_waits_for_a_plan_to_reset_after_the_no_ai_work(tmp_path):
    services = ireland_profile(tmp_path)
    clock = FakeClock()
    wake = clock.now + 3600
    # Every AI check says "resting" until the clock passes the wake time.
    hunt, runner, _ = make_hunt(services, [], clock)
    hunt._ai_state = lambda config: (clock.now >= wake, None if clock.now >= wake else wake, "Kimi Code rests until 3:00 AM")
    done = run_inline(hunt, {"target": 1, "hours": 3, "sources": "all", "steps": {s: False for s in STEP_IDS}})
    presets = [call["preset"] for call in runner.calls]
    first_ai = presets.index("default")
    assert set(presets[:first_ai]) == {"feeds"} and first_ai == 5  # every feed ran before waiting
    assert clock.now >= wake
    ai_call = runner.calls[first_ai]
    assert ai_call["focus"]["queries"] and ai_call["provider"] == "auto"
    assert done["state"] == "completed"


def test_no_plan_before_the_deadline_skips_ai_passes_but_not_feeds(tmp_path):
    services = ireland_profile(tmp_path)
    clock = FakeClock()
    hunt, runner, _ = make_hunt(services, [], clock)
    hunt._ai_state = lambda config: (False, clock.now + 99 * 3600, "Codex rests until Tuesday")
    done = run_inline(hunt, {"target": 3, "hours": 1, "steps": {s: False for s in STEP_IDS}})
    assert {call["preset"] for call in runner.calls} == {"feeds"}
    skipped = [p for p in done["progress"]["passes"] if p["state"] == "skipped"]
    assert skipped and "No AI plan was free" in skipped[0]["note"]


def test_stop_ends_the_hunt(tmp_path):
    services = ireland_profile(tmp_path)
    clock = FakeClock()
    hunt, runner, _ = make_hunt(services, [], clock)
    real = hunt._pass

    def pass_then_stop(id, config, progress, strategy):
        hunt.stop(id)
        return real(id, config, progress, strategy)

    hunt._pass = pass_then_stop
    done = run_inline(hunt, {"target": 5, "hours": 2})
    assert done["state"] == "stopped" and len(runner.calls) == 1


def test_hunt_choices_are_checked(tmp_path):
    services = ireland_profile(tmp_path)
    hunt, _, _ = make_hunt(services, [], FakeClock())
    for values, message in (({"target": 0}, "between 1 and"), ({"hours": 30}, "hours"), ({"min_fit": 20}, "fit bar"),
                            ({"sources": "web"}, "where to look"), ({"steps": {"cook": True}}, "Unknown helper")):
        with pytest.raises(ValueError, match=message):
            hunt.validate(values)
    assert hunt.validate({})["target"] == 10


def test_an_unfinished_profile_has_nothing_to_hunt(tmp_path):
    services = ireland_profile(tmp_path, roles=())
    clock = FakeClock()
    hunt, _, _ = make_hunt(services, [], clock)
    done = run_inline(hunt, {"target": 1, "hours": 1, "sources": "ai"})
    assert done["state"] == "failed" and "no target roles" in done["error"]
