"""What mends itself: usage-limit rests, a hunt after an app restart, a step that timed out,
no AI at all, and the never-re-apply memory that learns from the person's own decisions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.ai import limits
from backend.services import hunt as hunt_module
from backend.services import reapply
from backend.services.reapply import REMOVAL_REASONS

from test_hunt import FakeClock, ireland_profile, make_hunt, posting, run_inline

NO_STEPS = {"research": False, "tailor": False, "study_plan": False, "pdf": False}


# ----- usage limits: the five-hour window -------------------------------------------------------

def test_a_limit_without_a_reset_time_rests_until_the_window_ends(tmp_path):
    now = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
    book = limits.HealthBook(tmp_path, clock=lambda: now)
    # The plan's window started four and a half hours ago: it resets in thirty minutes, not sixty.
    book._update("kimi_cli", lambda entry: entry.update(window_start=(now - timedelta(hours=4, minutes=30)).isoformat(),
                                                         window_tokens=900_000, window_calls=40))
    book.rest_for_limit("kimi_cli", "Kimi Code reached its usage limit.")
    until = datetime.fromisoformat(book.resting("kimi_cli")["until"])
    assert now + timedelta(minutes=30) <= until <= now + timedelta(minutes=32)


def test_the_window_never_makes_a_rest_longer_than_the_default(tmp_path):
    now = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
    book = limits.HealthBook(tmp_path, clock=lambda: now)
    # The window started an hour ago here, but the plan may have been used elsewhere before
    # that: try again after the default hour rather than waiting four more.
    book._update("codex", lambda entry: entry.update(window_start=(now - timedelta(hours=1)).isoformat(),
                                                      window_tokens=500_000, window_calls=10))
    book.rest_for_limit("codex", "You've hit your usage limit.")
    until = datetime.fromisoformat(book.resting("codex")["until"])
    assert until == now + timedelta(minutes=limits.DEFAULT_REST_MINUTES)
    # A reset time the plan states is kept as it is.
    book.rest_for_limit("codex", "Usage limit reached. Try again in 2 hours 10 minutes.")
    assert datetime.fromisoformat(book.resting("codex")["until"]) == now + timedelta(hours=2, minutes=10)


# ----- never re-apply, and learn from removals ---------------------------------------------------

def row(company, title, status="saved", **extra):
    return {"company": company, "title": title, "status": status, "url": f"https://jobs.example/{company}/{title}",
            "updated_at": "2026-09-01T10:00:00+00:00", **extra}


def test_a_role_already_applied_for_is_never_suggested_again_even_reposted():
    memory = [row("Acme", "Data Analyst", "applied", application_date="2026-09-01")]
    automatic = reapply.check("ACME", "Data analyst", memory, automatic=True)
    assert automatic["blocked"] and automatic["rule"] == "already_applied"
    assert "already applied" in automatic["note"] and "2026-09-01" in automatic["note"]
    # Added by hand it is only a note: the person decides.
    manual = reapply.check("Acme", "Data Analyst", memory)
    assert not manual["blocked"] and "already applied" in manual["note"]
    # A different role at the same company is fine.
    assert not reapply.check("Acme", "BI Developer", memory, automatic=True)["blocked"]


def test_removal_reasons_teach_the_next_searches():
    removed = "2026-09-20T10:00:00+00:00"
    memory = [
        row("Acme", "Sales Analyst", deleted_at=removed, deletion_reason=REMOVAL_REASONS["role"] + ": mostly sales"),
        row("Birch", "Data Analyst", deleted_at=removed, deletion_reason=REMOVAL_REASONS["company"]),
        row("Cedar", "BI Developer", deleted_at=removed, deletion_reason=REMOVAL_REASONS["location"]),
        row("Delta", "Data Engineer", deleted_at=removed, deletion_reason=REMOVAL_REASONS["permit"]),
    ]
    check = lambda company, title: reapply.check(company, title, memory, automatic=True)  # noqa: E731
    # The wrong kind of role: that title is skipped at every company.
    assert check("Elm", "Sales Analyst")["rule"] == "removed_title"
    # Not this company, or it needs a permit: every role there is skipped.
    assert check("Birch", "BI Developer")["rule"] == "removed_company"
    assert check("Delta", "Data Analyst")["rule"] == "removed_company"
    # The wrong location teaches only that role.
    assert check("Cedar", "BI Developer")["rule"] == "removed_before"
    assert not check("Cedar", "Data Analyst")["blocked"]
    assert not check("Elm", "Data Analyst")["blocked"]
    assert reapply.removal_kind("Marked not suitable by user") is None
    assert reapply.removal_kind(REMOVAL_REASONS["senior"]) == "senior"


def test_the_dashboard_offers_the_same_removal_reasons():
    source = (Path(__file__).resolve().parents[2] / "frontend/src/features/RemoveJob.tsx").read_text(encoding="utf-8")
    for kind, label in REMOVAL_REASONS.items():
        assert f'id: "{kind}"' in source and f'label: "{label}"' in source


# ----- the hunt mends itself --------------------------------------------------------------------

def inline_start(hunt, call):
    """Run whatever the call starts on this thread (the hunt's own thread, inline)."""
    started = {}
    real = hunt_module.threading.Thread

    class Inline:
        def __init__(self, target, args, name, daemon):
            started["call"] = (target, args)

        def start(self):
            pass

    hunt_module.threading.Thread = Inline
    try:
        result = call()
    finally:
        hunt_module.threading.Thread = real
    if "call" in started:
        target, args = started["call"]
        target(*args)
    return result


def test_with_no_ai_on_the_pc_the_hunt_still_reads_the_boards(tmp_path):
    services = ireland_profile(tmp_path)
    clock = FakeClock()
    hunt, runner, pipeline = make_hunt(services, [], clock)
    pipeline.providers = lambda: [{"id": "auto", "ready": False, "models": [{"id": "auto"}]},
                                  {"id": "kimi_cli", "ready": False, "models": [{"id": "kimi"}]}]
    config = hunt.validate({"sources": "all"})
    assert config["no_ai"] and config["sources"] == "feeds" and config["requested_sources"] == "all"
    done = run_inline(hunt, {"target": 1, "hours": 1, "sources": "all", "steps": NO_STEPS})
    assert done["state"] == "completed" and {call["preset"] for call in runner.calls} == {"feeds"}
    assert "No AI app was ready" in (services.w.root / done["progress"]["report"]).read_text(encoding="utf-8")
    # The fallback is for this run only: the person's own choice is what is remembered.
    assert services.pref("hunt_preferences", {})["sources"] == "all"


def test_a_helper_step_that_times_out_is_tried_once_more(tmp_path):
    services = ireland_profile(tmp_path)
    clock = FakeClock()
    hunt, _, pipeline = make_hunt(services, ["Acme Analytics"], clock)
    calls = []

    def flaky(step, job_id, config):
        calls.append(step)
        if len(calls) == 1:
            raise RuntimeError("The AI app timed out after 600 seconds.")
        return {"note": "tailored"}

    pipeline.run_step = flaky
    done = run_inline(hunt, {"target": 1, "hours": 2, "steps": {**NO_STEPS, "tailor": True}})
    job = done["progress"]["prepare"]["jobs"][0]
    assert calls == ["tailor", "tailor"] and job["steps"]["tailor"]["state"] == "done"


def test_an_interrupted_hunt_carries_on_with_the_jobs_it_saved(tmp_path):
    services = ireland_profile(tmp_path)
    clock = FakeClock()
    hunt, runner, pipeline = make_hunt(services, ["Birch Data"], clock)
    first = services.add_posting(posting(90, company="Acme Analytics"), source="discovery")["job"]
    config = hunt.validate({"target": 2, "hours": 4, "steps": {**NO_STEPS, "tailor": True, "pdf": True}})
    progress = {"stage": "Tailored resume for Acme Analytics", "started_epoch": clock.now - 3600,
                "search_until_epoch": clock.now + 1800, "deadline_epoch": clock.now + 3 * 3600, "target": 2, "cycle": 1,
                "saved": [{"id": first["id"], "company": "Acme Analytics", "title": "Data Analyst", "location": "Dublin",
                           "fit": 80, "url": first["url"], "pass": "Employer feeds"}],
                "passes": [], "waiting": None,
                "prepare": {"state": "running", "jobs": [{"id": first["id"], "company": "Acme Analytics", "title": "Data Analyst",
                                                          "steps": {"tailor": {"state": "done"}, "pdf": {"state": "running"}}}]}}
    with services.w.connect() as db:
        db.execute("INSERT INTO hunt_runs(id,state,config,progress,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   ("old", "running", json.dumps(config), json.dumps(progress), services.now(), services.now()))
    hunt.recover()  # the app restarted
    assert hunt.get("old")["state"] == "interrupted"

    new = inline_start(hunt, hunt.resume_interrupted)
    done = hunt.get(new["id"])
    assert done["state"] == "completed", done["error"]
    assert [job["company"] for job in done["progress"]["saved"]] == ["Acme Analytics", "Birch Data"]
    assert done["progress"]["resumed_from"] == "old" and done["config"]["hours"] <= 3
    # The step finished before the restart is not done twice.
    assert pipeline.steps == [("pdf", first["id"]), ("tailor", done["progress"]["saved"][1]["id"]),
                              ("pdf", done["progress"]["saved"][1]["id"])]
    old = hunt.get("old")
    assert old["progress"]["resumed_into"] == new["id"] and "carried on by itself" in old["error"]
    assert hunt.resume_interrupted() is None  # once only


def test_an_interrupted_hunt_whose_time_is_up_is_left_alone(tmp_path):
    services = ireland_profile(tmp_path)
    clock = FakeClock()
    hunt, _, _ = make_hunt(services, [], clock)
    config = hunt.validate({"target": 2, "hours": 1})
    progress = {"deadline_epoch": clock.now + 60, "saved": [], "passes": []}
    with services.w.connect() as db:
        db.execute("INSERT INTO hunt_runs(id,state,config,progress,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   ("late", "running", json.dumps(config), json.dumps(progress), services.now(), services.now()))
    hunt.recover()
    assert hunt.resume_interrupted() is None and hunt.status()["current"] is None
