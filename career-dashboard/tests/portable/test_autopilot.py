"""Morning jobs (daily-job-search/autopilot.py): what it does at each time of day, how it mends
a hunt that stops, and the list it always writes."""

from __future__ import annotations

import io
import hashlib
import json
import re
import sys
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from backend.services import fit, search_memory
from backend.services.resume_studio import ResumeStudio
from backend.pdf_compiler import tectonic_executable

from test_hunt import JD, FakeClock, StubFitTeam, ireland_profile, make_hunt, posting

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

def checked_posting(services, job):
    from backend.job_quality import JobQualityService

    quality = JobQualityService(services)
    cid = quality.ensure_company(job["company"])
    with services.w.connect() as db:
        db.execute("UPDATE companies SET legitimacy_state='verified' WHERE id=?", (cid,))
        db.execute("UPDATE jobs SET company_id=?,last_verified_at=? WHERE id=?",
                   (cid, stamp(datetime.now(timezone.utc)), job["id"]))
    analysis = fit.analyse(services, job, team=StubFitTeam())
    fit.save(services, job["id"], analysis)


def recorded_review(services, job, pdf):
    stamp_now = services.now()
    with services.w.connect() as db:
        db.execute("INSERT INTO agent_runs(id,kind,job_id,state,input,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   ("review-" + job["id"], "resume_match", job["id"], "completed", "{}", json.dumps({
                       "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                       "jd_sha256": hashlib.sha256(job["description"].encode()).hexdigest(),
                       "review": {"summary": "Reviewed the current PDF against its posting.",
                                  "verdict": "pass", "issues": []},
                   }), stamp_now, stamp_now))


def checked_studio_pdf(services, job):
    """A saved Studio artifact and its actual hashes; no compiler is needed for freshness tests."""
    checked_posting(services, job)
    w = services.w
    evidence_path = w.root / "data/context/evidence.yml"
    evidence = w.evidence()
    evidence["candidate_revision"] = "test-revision-1"
    evidence_path.write_text(yaml.safe_dump(evidence), encoding="utf-8")
    ResumeStudio(services)
    source = "% EVIDENCE: SKILL-001\n\\newcommand{\\CoreSkills}{SQL, Power BI, Python}\n"
    folder = w.root / "data/output/applications" / job["id"] / "studio"
    preview = folder / "preview-2"
    preview.mkdir(parents=True)
    pdf = preview / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 current assessed fixture")
    metadata = {"revision": 2, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "path": preview.relative_to(w.root / "data/output").as_posix(), "page_count": 1,
                "layout": {"full_pages": True}, "review_required": True}
    (folder / "preview.json").write_text(json.dumps(metadata), encoding="utf-8")
    with w.connect() as db:
        db.execute("UPDATE jobs SET folder=? WHERE id=?", (folder.parent.relative_to(w.root).as_posix(), job["id"]))
        db.execute("INSERT INTO studio_drafts VALUES(?,?,?,?,?,?)", (job["id"], source, 2,
                   folder.relative_to(w.root).as_posix(), stamp(at(2)), "test-revision-1"))
        db.execute("INSERT INTO resume_scores VALUES(?,?,?,?,?,?,?)", (job["id"], 2, metadata["source_sha256"],
                   hashlib.sha256(pdf.read_bytes()).hexdigest(), hashlib.sha256(job["description"].encode()).hexdigest(),
                   "{}", stamp(at(2))))
    recorded_review(services, job, pdf)
    return pdf


def test_the_morning_list_shows_new_jobs_what_is_left_and_what_was_applied(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path, roles=("Data Analyst", "BI Analyst"))
    w = services.w
    fresh = w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/1", JD)
    older = w.add_job("Birch Data", "BI Analyst", "Cork, Ireland", "https://jobs.example/birch/2", JD)
    applied = w.add_job("Cedar Labs", "Data Engineer", "Dublin, Ireland", "https://jobs.example/cedar/3", JD)
    w.update_job(applied["id"], "applied", application_date="2026-09-20")
    fresh_pdf = checked_studio_pdf(services, fresh)
    checked_studio_pdf(services, older)
    with w.connect() as db:
        db.execute("UPDATE jobs SET created_at=?, fit_score=82, fit_rationale='Fit 82/100. Meets 3 of 3 must-haves.' "
                   "WHERE id=?", (stamp(at(2)), fresh["id"]))
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
    assert "Pay: EUR 42,000 a year (advertised in the posting)" in text
    assert re.search(r"Permit-path evidence: \d+/100 \(Evidence score, not approval likelihood\)", text)
    assert "## Your permit dates" in text and "Published GEP application lead time: 12 weeks before a job's start date" in text
    assert "Not immigration advice." in text and text.index("## Your permit dates") < text.index("## New this morning")
    assert autopilot._shown(fresh_pdf) in text and "review before applying" in text
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
    assert summary["new"] == [] and "No new job has both current checks" in text and "No hunt ran" in text


@pytest.mark.parametrize("change,reason", [
    ("base_only", "not prepared"),
    ("source", "current resume revision"),
    ("pdf", "resume assessment"),
    ("evidence", "current profile evidence"),
    ("posting", "blocked the automatic check"),
    ("fit", "requirement check"),
    ("profile", "Reconcile your pending profile edits"),
    ("posting_age", "more than two days ago"),
    ("review", "No independent review"),
    ("review_findings", "independent review needs attention"),
    ("review_blocked", "independent review found an error"),
    ("review_legacy", "no usable verdict"),
    ("assurance", "suggested item"),
    ("claims", "without registered candidate evidence"),
    ("discovery_without_verification", "more than two days ago"),
])
def test_unfinished_or_stale_work_is_pending_and_never_a_new_ready_resume(tmp_path, change, reason):
    services = ireland_profile(tmp_path)
    w = services.w
    job = w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/pending", JD)
    pdf = checked_studio_pdf(services, job)
    with w.connect() as db:
        db.execute("UPDATE jobs SET created_at=? WHERE id=?", (stamp(at(2)), job["id"]))
        if change == "base_only":
            db.execute("DELETE FROM studio_drafts WHERE job_id=?", (job["id"],))
            (pdf.parent.parent.parent / "resume.pdf").write_bytes(b"%PDF-1.4 stale base")
        elif change == "source":
            db.execute("UPDATE studio_drafts SET revision=revision+1,source=source || ' changed' WHERE job_id=?", (job["id"],))
        elif change == "pdf":
            pdf.write_bytes(b"%PDF-1.4 changed after assessment")
        elif change == "evidence":
            evidence = w.evidence()
            evidence["candidate_revision"] = "new-evidence-revision"
            (w.root / "data/context/evidence.yml").write_text(yaml.safe_dump(evidence), encoding="utf-8")
        elif change == "posting":
            db.execute("UPDATE jobs SET posting_state='needs_review' WHERE id=?", (job["id"],))
        elif change == "fit":
            db.execute("DELETE FROM job_fit WHERE job_id=?", (job["id"],))
        elif change == "profile":
            db.execute("UPDATE knowledge SET review_state='user_updated' WHERE id='SKILL-001'")
        elif change == "posting_age":
            db.execute("UPDATE jobs SET last_verified_at=? WHERE id=?",
                       (stamp(datetime.now(timezone.utc) - timedelta(days=3)), job["id"]))
        elif change == "review":
            db.execute("DELETE FROM agent_runs WHERE kind='resume_match' AND job_id=?", (job["id"],))
        elif change in {"review_findings", "review_blocked", "review_legacy"}:
            row = db.execute("SELECT id,result FROM agent_runs WHERE kind='resume_match' AND job_id=?", (job["id"],)).fetchone()
            review = json.loads(row['result'])
            if change == "review_legacy":
                review['review'].pop('verdict')
            else:
                review['review'].update(verdict='blocked' if change == 'review_blocked' else 'review',
                                        issues=['Confirm the document details.'])
            db.execute("UPDATE agent_runs SET result=? WHERE id=?", (json.dumps(review), row['id']))
        elif change == "assurance":
            db.execute("INSERT INTO resume_items(id,job_id,section,content,origin,evidence_id,decision,created_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?)", ("suggestion-1", job["id"], "skills", "Looker", "predicted", "", "pending",
                                                   services.now(), services.now()))
        elif change == "claims":
            source = "% EVIDENCE: resume_items:suggestion-1\n\\newcommand{\\CoreSkills}{Looker}\n"
            db.execute("UPDATE studio_drafts SET source=? WHERE job_id=?", (source, job["id"]))
            preview_file = pdf.parent.parent / "preview.json"
            preview = json.loads(preview_file.read_text(encoding="utf-8"))
            preview["source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
            preview_file.write_text(json.dumps(preview), encoding="utf-8")
            db.execute("UPDATE resume_scores SET source_sha256=? WHERE job_id=?",
                       (preview["source_sha256"], job["id"]))
        elif change == "discovery_without_verification":
            db.execute("UPDATE jobs SET last_verified_at=NULL WHERE id=?", (job["id"],))
            db.execute("INSERT INTO agent_runs(id,kind,state,input,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       ("discovery-example", "discovery", "completed", "{}",
                        json.dumps({"added_job_ids": [job["id"]]}), services.now(), services.now()))

    class Store:
        def root_for(self, _id):
            return w.root

    result = autopilot.collect(Store(), PROFILE, autopilot.Journal(), at(9), "09:00")
    assert result["new"] == []
    pending = result["pending"]
    assert len(pending) == 1 and pending[0]["resume"] is None
    assert any(reason in text for text in pending[0]["pending_reasons"])
    report = autopilot.render(result, "http://127.0.0.1:8000")
    assert "## Pending checks or resume (1)" in report
    assert "Tailored resume (review before applying):" not in report


def test_morning_readiness_does_not_write_artifacts_or_request_another_agent(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    job = services.w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/read-only", JD)
    pdf = checked_studio_pdf(services, job)
    source_file = pdf.parent.parent / "resume.tex"
    source_file.write_text("A file reporting must not overwrite.\n", encoding="utf-8")
    files = list(pdf.parent.parent.rglob("*"))
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files if path.is_file()}

    def unexpected(*args, **kwargs):
        raise AssertionError("Reporting must not request AI, compile or rescore a PDF")

    from backend.services.agents import AgentRunner

    monkeypatch.setattr(AgentRunner, "enqueue", unexpected)
    monkeypatch.setattr(ResumeStudio, "preview", unexpected)
    monkeypatch.setattr(ResumeStudio, "score", unexpected)
    monkeypatch.setattr(fit, "for_job", unexpected)
    current = services.w.get_job(job["id"])
    result, reasons = autopilot._readiness(services, current, set(), fit.catalogue(services), {})
    assert result == pdf and reasons == []
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before} == before


def test_ready_studio_pdf_wins_over_base_and_manual_job_waits_for_checks(tmp_path):
    services = ireland_profile(tmp_path)
    w = services.w
    ready = w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/ready", JD)
    manual = w.add_job("Manual Example", "Data Analyst", "Dublin, Ireland", "https://jobs.example/manual", JD)
    pdf = checked_studio_pdf(services, ready)
    (pdf.parent.parent.parent / "resume.pdf").write_bytes(b"%PDF-1.4 obsolete base")
    (pdf.parent.parent / "preview-9").mkdir()
    (pdf.parent.parent / "preview-9/resume.pdf").write_bytes(b"%PDF-1.4 unrelated newer-looking preview")
    with w.connect() as db:
        db.execute("UPDATE jobs SET created_at=?", (stamp(at(2)),))
        db.execute("INSERT INTO agent_runs(id,kind,state,input,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                   ("discovery-example", "discovery", "completed", "{}", json.dumps({"added_job_ids": [ready["id"]]}),
                    stamp(at(2)), stamp(at(2))))

    class Store:
        def root_for(self, _id):
            return w.root

    result = autopilot.collect(Store(), PROFILE, autopilot.Journal(), at(9), "09:00")
    assert [j["id"] for j in result["new"]] == [ready["id"]]
    assert result["new"][0]["resume"] == autopilot._shown(pdf)
    assert result["new"][0]["review_required"] is True
    assert [j["id"] for j in result["pending"]] == [manual["id"]]
    assert any("not prepared" in reason for reason in result["pending"][0]["pending_reasons"])


def test_aging_service_keeps_application_status_notes_and_events_unchanged(tmp_path):
    services = ireland_profile(tmp_path)
    job = services.w.add_job("Example Services", "Data Analyst", "Dublin, Ireland", "https://jobs.example/applied", JD)
    services.w.update_job(job["id"], "applied", notes="Waiting for a reply", application_date="2020-01-01")
    before = services.w.get_job(job["id"])
    events = services.w.activity()
    assert services.age_applications() == []
    assert services.w.get_job(job["id"]) == before
    assert services.w.activity() == events


@pytest.mark.skipif(not tectonic_executable(), reason="Tectonic is required for the complete Studio PDF")
def test_a_real_onboarded_and_compiled_studio_resume_is_in_the_ready_morning_list(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.dashboard.shell import create_shell
    from backend.profiles import ProfileStore
    from backend.job_quality import JobQualityService
    from backend.services.workspace_v2 import CareerServices
    import backend.services.intake.api as intake_api
    from career import Workspace
    from test_build_pipeline import ExtractorFixture, _wait

    monkeypatch.setattr(intake_api, "team_factory", lambda _profiles: lambda _on_usage: ExtractorFixture())
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    with TestClient(create_shell(store), base_url="http://127.0.0.1") as client:
        profile = client.post("/api/profiles", json={"name": "Example Person"}).json()["profile"]
        base = "/api/profiles/" + profile["id"]
        client.post(base + "/sources?name=resume.md", content=(
            b"Example Person\nCustomer Support Specialist, Example Services, Dublin, Ireland.\n"
            b"January 2023 to Present. Responded to customer requests by email and phone.\n"
            b"Used Zendesk to track support tickets and follow-up actions.\n"))
        run = client.post(base + "/build-runs", json={"target_markets": ["ie"],
            "work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "citizen",
                                                      "needs_sponsorship_later": "no"}}}).json()
        built = _wait(client, base, run["id"])
        assert built["status"] == "completed", built

    services = CareerServices(Workspace(store.root_for(profile["id"])))
    services.set_pref("hunt_preferences", {"require_ai_fit": False, "min_fit": 70})
    job = services.add_posting({"company": "Example Services", "title": "Customer Support Specialist",
        "location": "Dublin, Ireland", "url": "https://example.org/careers/support",
        "description": "Customer Support Specialist in Dublin. Requirements: Zendesk. "
                       "Answer customer requests by email and phone and follow up on support tickets."})["job"]
    quality = JobQualityService(services)
    quality.assess_company(job["company"], job["url"], [], [], [], override_reason="Test employer feed")
    quality.verify_posting(job["id"], response={"status": 200, "text": job["description"], "final_url": job["url"]})
    analysis = fit.for_job(services, job["id"], use_ai=False)
    assert analysis["score"] >= 70
    studio = ResumeStudio(services)
    draft = studio.open(job["id"])
    fitted = studio.fit(job["id"], draft["revision"])
    assert fitted["preview"]["current"]
    pdf = services.w.root / "data/output" / fitted["preview"]["path"] / "resume.pdf"
    recorded_review(services, job, pdf)
    with services.w.connect() as db:
        db.execute("UPDATE jobs SET created_at=? WHERE id=?", (stamp(at(2)), job["id"]))
    result = autopilot.collect(store, profile, autopilot.Journal(), at(9), "09:00")
    assert result["pending"] == [], result["pending"]
    assert [j["id"] for j in result["new"]] == [job["id"]]
    assert result["new"][0]["resume"] == autopilot._shown(pdf) and pdf.is_file()
    assert result["new"][0]["review_required"] is True


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


def test_the_morning_list_shows_tracker_alerts_with_new_matches(tmp_path):
    from backend.market import tracker
    from backend.market.store import MarketStore
    from backend.services import job_sources

    services = ireland_profile(tmp_path)
    tracker.save_alert(services, "Data roles in Cork", {"county": "Cork"})
    alerts = services.pref(tracker.ALERTS)
    alerts[0]["seen_at"] = "2000-01-01T00:00:00+00:00"
    services.set_pref(tracker.ALERTS, alerts)
    MarketStore().record_postings([job_sources.make_posting(
        "Acme Analytics", "Data Analyst", "https://boards.greenhouse.io/acme/jobs/9", "Cork, Ireland",
        "Report with SQL.", source="directory", source_kind="employer_feed")])

    class Store:
        def root_for(self, _id):
            return services.w.root

    result = autopilot.collect(Store(), PROFILE, autopilot.Journal(), at(9), "09:00")
    assert [alert["new"] for alert in result["tracker_alerts"]] == [1]
    report = autopilot.render(result, "http://127.0.0.1:8000")
    assert "## Tracker alerts" in report and "**Data roles in Cork**: 1 new (for example Data Analyst at Acme Analytics)" in report
