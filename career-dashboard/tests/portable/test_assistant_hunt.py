"""The Assistant starts and reports the overnight hunt, and edits the search plan without touching evidence."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/scripts"))

from backend.job_quality import JobQualityService, ProfileRules  # noqa: E402
from backend.services.agents import AgentRunner  # noqa: E402
from backend.services.assistant import Assistant  # noqa: E402
from backend.services.assistant_tools import Toolbox  # noqa: E402
from backend.services.pipeline import STEP_IDS  # noqa: E402
from backend.services.resume_studio import ResumeStudio  # noqa: E402

from test_hunt import ireland_profile  # noqa: E402 - the same disposable Ireland profile


class FakeHunt:
    def __init__(self):
        self.started = []
        self.run = None

    def start(self, values):
        self.started.append(values)
        config = {"target": values.get("target", 10), "hours": values.get("hours", 8.0), "min_fit": values.get("min_fit", 70),
                  "allow_paid": False, "steps": {"research": True, "tailor": True, "study_plan": False, "pdf": True}}
        self.run = {"id": "hunt1", "state": "running", "config": config, "error": None, "finished_at": None,
                    "progress": {"stage": "Employer directory (cycle 1, 1 of 10 saved)",
                                 "saved": [{"company": "Acme", "title": "Data Analyst", "fit": 82}],
                                 "passes": [{"label": "Your tracked companies", "state": "done", "looked": 4, "saved": 1}],
                                 "waiting": {"until_text": "3:40 AM", "why": "Kimi Code rests until 3:40 AM"}}}
        return self.run

    def status(self):
        return {"current": self.run, "last": None}

    def get(self, id):
        return self.run

    def stop(self, id):
        self.run = {**self.run, "stop_requested": True}
        return self.run


def assistant_for(services, hunt):
    studio = ResumeStudio(services)
    runner = AgentRunner(services)
    runner.studio = studio
    quality = JobQualityService(services)
    tools = Toolbox(services, studio, runner, quality, hunt=hunt)
    return Assistant(services, studio, runner, quality, background=False, tools=tools), tools


def test_overnight_hunt_shortcut_and_status(tmp_path):
    services = ireland_profile(tmp_path)
    hunt = FakeHunt()
    assistant, tools = assistant_for(services, hunt)
    reply = assistant.send("start the overnight hunt")
    assert reply["state"] == "done" and hunt.started == [{}]
    assert "keeps searching until it has saved **10** jobs" in reply["response"]
    assert "never uses a paid AI" in reply["response"]
    status = assistant.send("hunt status")
    assert "1 of 10 saved" in status["response"] and "Waiting until 3:40 AM" in status["response"]
    assert "Acme — Data Analyst (fit 82)" in status["response"]
    assert tools.hunt_brief()["passes_done"] == 1
    assert assistant._snapshot()["overnight_hunt"]["target"] == 10


def test_start_hunt_tool_passes_only_what_was_asked(tmp_path):
    services = ireland_profile(tmp_path)
    hunt = FakeHunt()
    _, tools = assistant_for(services, hunt)
    result = tools.call("start_hunt", {"target": "15", "min_fit": 75, "steps": "tailor,pdf"})
    assert hunt.started[-1] == {"target": 15, "min_fit": 75,
                                "steps": {step: step in ("tailor", "pdf") for step in STEP_IDS}}
    assert result["hunt_id"] == "hunt1"
    assert tools.call("stop_hunt")["stopped"] is True


def test_search_plan_edits_change_where_to_look_and_keep_the_file(tmp_path):
    services = ireland_profile(tmp_path)
    _, tools = assistant_for(services, FakeHunt())
    profile = services.w.root / "data/config/profile.yml"
    profile.write_text("# My profile, edited by hand.\n" + profile.read_text(encoding="utf-8"), encoding="utf-8")
    assert tools.get("update_search_plan").confirm is True
    question = tools.describe("update_search_plan", {"add_titles": ["Insights Associate"], "exclude_titles": ["sales"]})
    assert "also search for *Insights Associate*" in question and "never your experience" in question

    tools.call("update_search_plan", {"add_titles": ["Insights Associate"], "remove_titles": ["mi analyst"],
                                      "exclude_titles": ["sales"]})
    text = profile.read_text(encoding="utf-8")
    assert text.startswith("# My profile, edited by hand.")  # only the changed keys were rewritten
    targets = yaml.safe_load(text)["target_roles"]
    assert targets["primary"] == ["Data Analyst"]
    assert targets["related_titles"] == ["Insights Associate"]
    assert targets["suppressed_related"] == ["mi analyst"] and targets["excluded_titles"] == ["sales"]
    rules = ProfileRules.of(services.w.root)
    assert rules.roles.search("Insights Associate") and not rules.roles.search("MI Analyst")
    assert not rules.roles.search("Sales Data Analyst")
    plan = tools.call("search_plan")
    assert "Insights Associate" in plan["related_titles"] and plan["passes"]

    tools.call("update_search_plan", {"track_company": "Acme", "careers_url": "https://acme.wd3.myworkdayjobs.com/en-US/Careers"})
    rows = yaml.safe_load((services.w.root / "data/config/portals.yml").read_text(encoding="utf-8"))["tracked_companies"]
    assert rows == [{"name": "Acme", "careers_url": "https://acme.wd3.myworkdayjobs.com/en-US/Careers", "enabled": True,
                     "ats": "workday", "host": "acme.wd3.myworkdayjobs.com", "site": "Careers"}]
    for url in ("https://www.linkedin.com/company/acme", "https://acme.example/careers"):
        try:
            tools.call("update_search_plan", {"track_company": "Other", "careers_url": url})
        except ValueError as error:
            assert "cannot be read directly" in str(error)
        else:
            raise AssertionError("an unreadable careers link was accepted")


def test_todays_jobs_come_from_the_morning_list(tmp_path):
    import json

    services = ireland_profile(tmp_path)
    assistant, tools = assistant_for(services, FakeHunt())
    empty = assistant.send("today's jobs")
    assert empty["state"] == "done" and "no morning list yet" in empty["response"]
    services.w.daily_dir.mkdir(parents=True, exist_ok=True)
    (services.w.daily_dir / "morning-jobs.json").write_text(json.dumps({
        "date": "2026-09-27", "new": [{"company": "Acme", "title": "Data Analyst", "location": "Dublin", "fit": 82,
                                       "url": "https://jobs.example/acme/1", "resume": "applications/acme/resume.pdf"}],
        "to_apply_total": 4, "held": 2, "needs_you": ["Open Kimi Code and sign in."],
        "fixed": ["03:10 The app had stopped answering, so it was restarted."]}), encoding="utf-8")
    reply = assistant.send("What are today's jobs?")
    text = reply["response"]
    assert "**Morning jobs, 2026-09-27**: 1 new · 4 still to apply." in text
    assert "**Acme — Data Analyst** · Dublin · fit 82 · [apply](https://jobs.example/acme/1)" in text
    assert "Needs you: Open Kimi Code" in text and "2 more are waiting" in text and "Fixed by itself" in text
    assert tools.call("morning_list")["summary"] == "2026-09-27: 1 new, 4 still to apply"
