"""Morning jobs (daily-job-search/autopilot.py): what it does at each time of day, how it mends
a hunt that stops, and the list it always writes."""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.services import search_memory

from test_hunt import JD, FakeClock, ireland_profile, make_hunt, posting

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "daily-job-search"))
import autopilot  # noqa: E402

ZONE = timezone(timedelta(hours=1))


def at(hour, minute=0, day=27):
    return datetime(2026, 9, day, hour, minute, tzinfo=ZONE)


def stamp(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


# ----- what to do at each time of day ------------------------------------------------------------

def test_the_night_run_hunts_until_shortly_before_the_ready_by_time():
    night = autopilot.plan(at(1, 30), "09:00", None)
    assert night["action"] == "start" and night["hours"] == 7.25
    assert abs(autopilot.plan(at(1, 30), "09:00", None, share=2)["hours"] - 3.625) < 0.01
    # Started in the evening it prepares the next morning, never longer than the longest hunt.
    assert autopilot.plan(at(22), "09:00", None)["hours"] == autopilot.MAX_HUNT_HOURS
    # Too close to the ready-by time: no new hunt, only the list.
    assert autopilot.plan(at(8, 40), "09:00", None)["action"] == "done"


def test_the_ready_by_run_only_rewrites_the_list_after_the_night_hunt():
    finished = {"state": "completed", "created_at": stamp(at(1, 30))}
    assert autopilot.plan(at(9), "09:00", finished)["action"] == "done"
    stopped = autopilot.plan(at(9), "09:00", {**finished, "state": "stopped"})
    assert stopped["action"] == "done" and "you stopped" in stopped["why"]


def test_a_missed_night_gets_a_catch_up_and_a_failed_hunt_is_started_again():
    catch_up = autopilot.plan(at(9, 5), "09:00", None)
    assert catch_up["action"] == "start" and catch_up["hours"] == autopilot.CATCH_UP_HOURS and "catch-up" in catch_up["why"]
    assert autopilot.plan(at(3), "09:00", {"state": "failed", "created_at": stamp(at(1, 30))})["action"] == "start"
    # Yesterday afternoon's hunt does not stand in for tonight's.
    assert autopilot.plan(at(1, 30), "09:00", {"state": "completed", "created_at": stamp(at(14, day=26))})["action"] == "start"


def test_which_profiles_run():
    class Store:
        def __init__(self, profiles, last=""):
            self.profiles, self.last = profiles, last

        def list(self):
            return self.profiles

        def last_used(self):
            return self.last

    on = {"id": "a", "name": "A", "state": "ready", "schedule": {"enabled": True, "ready_by": "08:00"}}
    off = {"id": "b", "name": "B", "state": "ready"}
    journal = autopilot.Journal()
    assert autopilot.choose(Store([on, off]), None, journal) == [on]
    assert autopilot.ready_by_for([on]) == "08:00"
    # Nothing switched on (an AI app ran it): the profile opened last, with a note saying so.
    assert autopilot.choose(Store([off], "b"), None, journal) == [off] and "not switched on" in journal.entries[-1]["text"]
    assert autopilot.choose(Store([]), None, journal) == [] and journal.entries[-1]["kind"] == "needs_you"


# ----- the hunt loop mends what stops it ---------------------------------------------------------------

class FakeApp:
    """The profile API, answering from a script; a callable answer can raise."""

    def __init__(self, script):
        self.script, self.calls = {key: list(value) for key, value in script.items()}, []

    def api(self, method, path, body=None, timeout=60):
        key = f"{method} {path.split('/api/v2', 1)[1]}"
        self.calls.append((key, body))
        answers = self.script[key]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Tick:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def context(app, now=at(1, 30), hours=None):
    tick = Tick()
    return autopilot.Context(journal=autopilot.Journal(), server=app, ready_by="09:00", hours=hours,
                             sleep=tick.sleep, clock=tick, now=lambda: now)


def run(id, state="running", **extra):
    return {"id": id, "state": state, "config": {"hours": 7.25, "target": 10, "min_fit": 70, "sources": "all"},
            "progress": extra.pop("progress", {}), "error": extra.pop("error", None), **extra}


PROFILE = {"id": "sample", "name": "Sample Person"}


def test_a_night_hunt_is_started_and_followed_to_the_end():
    app = FakeApp({
        "GET /hunt/status": [{"current": None, "last": None},
                             {"current": run("h1", progress={"stage": "Employer feeds", "saved": [{"company": "Acme"}]}), "last": None},
                             {"current": None, "last": run("h1", "completed", progress={"saved": [{"company": "Acme"}]})}],
        "POST /hunt/run": [run("h1")],
    })
    ctx = context(app)
    autopilot.hunt_profile(ctx, PROFILE, share=1)
    assert [call for call in app.calls if call[0] == "POST /hunt/run"] == [("POST /hunt/run", {"hours": 7.25})]
    assert not [e for e in ctx.journal.entries if e["kind"] in ("fixed", "needs_you")]


def test_what_only_the_person_can_answer_is_asked_once_not_retried():
    app = FakeApp({
        "GET /hunt/status": [{"current": None, "last": None}],
        "POST /hunt/run": [autopilot.ApiError("Confirm work authorization and citizenship for Ireland in Assistant.", 400)],
    })
    ctx = context(app)
    autopilot.hunt_profile(ctx, PROFILE, share=1)
    assert len([c for c in app.calls if c[0] == "POST /hunt/run"]) == 1
    assert ctx.journal.of("needs_you", "sample") and "work authorization" in ctx.journal.of("needs_you", "sample")[0]


def test_a_hunt_that_fails_is_started_again_with_the_time_left():
    failed = run("h1", "failed", error="database is locked", created_at=stamp(at(1, 30)))
    app = FakeApp({
        "GET /hunt/status": [{"current": None, "last": None},
                             {"current": None, "last": failed},
                             {"current": None, "last": failed},
                             {"current": None, "last": run("h2", "completed")}],
        "POST /hunt/run": [run("h1"), run("h2")],
    })
    ctx = context(app)
    autopilot.hunt_profile(ctx, PROFILE, share=1)
    assert len([c for c in app.calls if c[0] == "POST /hunt/run"]) == 2
    assert "started again" in ctx.journal.of("fixed", "sample")[0]


def test_a_hunt_carried_on_by_the_app_after_a_restart_is_followed():
    app = FakeApp({
        "GET /hunt/status": [{"current": None, "last": None},
                             {"current": run("h2", progress={"resumed_from": "h1"}), "last": None},
                             {"current": None, "last": run("h2", "completed")}],
        "POST /hunt/run": [run("h1")],
    })
    ctx = context(app)
    autopilot.hunt_profile(ctx, PROFILE, share=1)
    assert "carried on by itself" in ctx.journal.of("fixed", "sample")[0]


def test_no_ai_and_a_busy_profile_are_handled():
    started = run("h1")
    started["config"]["no_ai"] = True
    app = FakeApp({
        "GET /hunt/status": [{"current": None, "last": None}, {"current": None, "last": None},
                             {"current": None, "last": run("h1", "completed")}],
        "POST /hunt/run": [autopilot.ApiError("A Daily Search is running. Wait for it to finish, or stop it first.", 400),
                           started],
        "GET /pipeline/status": [{"current": None}],
    })
    ctx = context(app)
    autopilot.hunt_profile(ctx, PROFILE, share=1)
    assert ("GET /pipeline/status", None) in app.calls  # it waited for the Daily Search
    assert "No AI app was ready" in ctx.journal.of("needs_you", "sample")[0]


def test_an_app_that_stops_answering_is_started_again(monkeypatch):
    journal = autopilot.Journal()
    server = autopilot.Server(journal, sleep=lambda seconds: None)
    answers = [urllib.error.URLError("connection refused"), io.BytesIO(b'{"ok": true}')]

    def urlopen(request, data=None, timeout=None):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(autopilot.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(server, "ensure", lambda: "restarted")
    assert server.api("GET", "/p/sample/api/v2/hunt/status") == {"ok": True}
    assert "started again" in journal.entries[0]["text"] and journal.entries[0]["kind"] == "fixed"


def test_no_internet_is_waited_out():
    journal = autopilot.Journal()
    answers = iter([False, False, True])
    assert autopilot.wait_online(journal, time.time() + 3600, check=lambda: next(answers), sleep=lambda s: None)
    assert "no internet" in journal.entries[0]["text"] and journal.entries[0]["kind"] == "fixed"
    assert not autopilot.wait_online(journal, time.time() + 10, check=lambda: False, sleep=lambda s: None)
    assert journal.entries[-1]["kind"] == "needs_you"


def test_one_run_at_a_time(tmp_path):
    path = tmp_path / "autopilot.lock"
    first = autopilot.Lock(path)
    assert first.acquire() and not autopilot.Lock(path).acquire()
    first.release()
    path.write_text(json.dumps({"pid": 999_999, "started": time.time()}), encoding="utf-8")  # a run that died
    assert autopilot.Lock(path).acquire()


# ----- the list it always writes ---------------------------------------------------------------------

def test_the_morning_list_shows_new_jobs_what_is_left_and_what_was_applied(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    w = services.w
    fresh = w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/1", JD)
    older = w.add_job("Birch Data", "BI Analyst", "Cork, Ireland", "https://jobs.example/birch/2", JD)
    applied = w.add_job("Cedar Labs", "Data Engineer", "Dublin, Ireland", "https://jobs.example/cedar/3", JD)
    w.update_job(applied["id"], "applied", application_date="2026-09-20")
    (w.root / "applications/acme").mkdir(parents=True)
    (w.root / "applications/acme/resume.pdf").write_bytes(b"%PDF-1.4")
    with w.connect() as db:
        db.execute("UPDATE jobs SET created_at=?, fit_score=82, fit_rationale='Fit 82/100. Meets 3 of 3 must-haves.', "
                   "folder='applications/acme' WHERE id=?", (stamp(at(2)), fresh["id"]))
        db.execute("UPDATE jobs SET created_at=?, fit_score=71 WHERE id=?", (stamp(at(10, day=22)), older["id"]))
        search_memory.remember(db, posting(7, company="Elm Insights"), "held", stage="fit",
                               reason="waiting for an AI check", keep_posting=True)

    class Store:
        def root_for(self, profile_id):
            return w.root

    journal = autopilot.Journal()
    journal.fixed("The app had stopped answering, so it was restarted.")
    journal.needs_you("Open Kimi Code and sign in.", "sample")
    monkeypatch.setattr(autopilot, "INDEX", tmp_path / "MORNING-JOBS.md")
    summary = autopilot.write_list(Store(), PROFILE, journal, at(9), "09:00", "http://127.0.0.1:8000")
    autopilot.write_index([summary], journal, at(9))

    text = (w.daily_dir / "MORNING-JOBS.md").read_text(encoding="utf-8")
    assert "**Acme Analytics — Data Analyst** · Dublin, Ireland · fit 82/100" in text
    assert "Why: Fit 82/100. Meets 3 of 3 must-haves." in text and "Apply: https://jobs.example/acme/1" in text
    assert "applications/acme/resume.pdf" in text
    assert "## Still to apply" in text and "| 71 | Birch Data | BI Analyst |" in text
    assert "Cedar Labs" not in text.split("## Your applications")[0]
    assert "Applied 1" in text and "never suggests these roles again" in text
    assert "## Waiting for the AI requirement check (1)" in text and "Elm Insights" in text
    assert text.index("## Needs you") < text.index("## New this morning") and "Open Kimi Code" in text
    assert "Fixed by itself:" in text and "Nothing was submitted" in text
    dated = json.loads((w.daily_dir / "2026-09-27/morning-jobs.json").read_text(encoding="utf-8"))
    assert [job["company"] for job in dated["new"]] == ["Acme Analytics"]
    index = (tmp_path / "MORNING-JOBS.md").read_text(encoding="utf-8")
    assert "## Sample Person" in index and "1 new this morning" in index and "Acme Analytics — Data Analyst (fit 82)" in index

    # Every job the mornings bring is tracked once, however often the list is rewritten.
    autopilot.write_list(Store(), PROFILE, journal, at(9, 30), "09:00", "http://127.0.0.1:8000")
    history = (w.daily_dir / "history.csv").read_text(encoding="utf-8")
    assert history.count("https://jobs.example/acme/1") == 1


def test_a_quiet_morning_still_gets_a_list(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)

    class Store:
        def root_for(self, profile_id):
            return services.w.root

    monkeypatch.setattr(autopilot, "INDEX", tmp_path / "MORNING-JOBS.md")
    summary = autopilot.write_list(Store(), PROFILE, autopilot.Journal(), at(9), "09:00", "http://127.0.0.1:8000")
    text = (services.w.daily_dir / "MORNING-JOBS.md").read_text(encoding="utf-8")
    assert summary["new"] == [] and "No new job passed every check" in text and "No hunt ran" in text


def test_a_list_written_in_the_evening_shows_that_days_search(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    make_hunt(services, [], FakeClock())  # the hunt table
    job = services.w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/9", JD)
    with services.w.connect() as db:
        db.execute("UPDATE jobs SET created_at=? WHERE id=?", (stamp(at(19, 10)), job["id"]))
        db.execute("INSERT INTO hunt_runs(id,state,config,progress,created_at,updated_at,finished_at) VALUES(?,?,?,?,?,?,?)",
                   ("h1", "completed", json.dumps({"target": 5}),
                    json.dumps({"saved": [{}], "passes": [{"state": "done", "looked": 11, "turned_away": 1}]}),
                    stamp(at(19, 7)), stamp(at(19, 20)), stamp(at(19, 20))))

    class Store:
        def root_for(self, profile_id):
            return services.w.root

    monkeypatch.setattr(autopilot, "INDEX", tmp_path / "MORNING-JOBS.md")
    autopilot.write_list(Store(), PROFILE, autopilot.Journal(), at(19, 30), "09:00", "http://127.0.0.1:8000")
    text = (services.w.daily_dir / "MORNING-JOBS.md").read_text(encoding="utf-8")
    assert "Acme Analytics — Data Analyst" in text.split("## How the search went")[0]
    assert "No hunt ran" not in text and "looked at 11 postings, saved 1 of 5" in text
