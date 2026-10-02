"""The Daily Search pipeline takes "find N jobs" end to end.

It searches again while it is short (skipping what it already turned away), then takes each
job through every helper in order; a closed posting stops that job before it gets a folder;
the final ready-to-submit check reads what the other steps produced. Disposable profiles only,
and no AI app, job board or posting page is reached.
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

from backend.job_quality import JobQualityService  # noqa: E402
from backend.resume_contract import contract_for  # noqa: E402
from backend.role_titles import RoleMatcher, related_titles  # noqa: E402
from backend.services import fit, pipeline as pipeline_module, portals, readiness  # noqa: E402
from backend.services.pipeline import STEP_IDS, Pipeline  # noqa: E402

from test_hunt import JD, ireland_profile, posting, wait_for  # noqa: E402 - the same disposable Ireland profile


class ScriptedRunner:
    """Each discovery pass saves the next scripted companies; every other agent run just completes."""

    def __init__(self, services, script):
        self.s, self.script, self.calls = services, list(script), []
        self.gateway = type("Gateway", (), {"providers": {}})()  # no AI app: the app picks one that can search

    def enqueue(self, kind, job_id, provider, model, preset="default", count=None, focus=None, free_only=False):
        self.calls.append({"kind": kind, "job_id": job_id, "preset": preset, "count": count, "focus": focus})
        result = {}
        if kind == "discovery":
            companies = self.script.pop(0) if self.script else []
            added, jobs = [], []
            for company in companies:
                number = len(self.calls) * 10 + len(added)
                saved = self.s.add_posting(posting(number, company=company), source="discovery")
                added.append(saved["job"]["id"])
                jobs.append({"url": saved["job"]["url"]})
            result = {"added_job_ids": added, "jobs": jobs + [{"url": f"https://jobs.example/turned-away/{len(self.calls)}"}],
                      "rejected_leads": [f"https://jobs.example/rejected/{len(self.calls)}: relevance gate"]}
        run_id = uuid.uuid4().hex
        with self.s.w.connect() as db:
            db.execute("INSERT INTO agent_runs(id,kind,job_id,state,input,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                       (run_id, kind, job_id, "completed", "{}", json.dumps(result), self.s.now(), self.s.now()))
        return {"id": run_id}


class OpenOnly:
    """The Studio calls the chain makes before any helper: opening the draft."""

    def __init__(self):
        self.opened = []

    def open(self, job_id):
        self.opened.append(job_id)


def chain(tmp_path, monkeypatch, script, *, closed=()):
    services = ireland_profile(tmp_path)
    runner, studio = ScriptedRunner(services, script), OpenOnly()
    pipeline = Pipeline(services, runner, studio, poll_seconds=0.01)
    ran = []
    for step in STEP_IDS:
        if step == "posting":
            continue  # the real posting check runs, against the fake page below

        def helper(job_id, config, step=step):
            ran.append((step, services.w.get_job(job_id)["company"]))
            return {"note": step, **({"verdict": "review", "score": 70, "next": ["Decide 1 item"]} if step == "ready" else {})}

        monkeypatch.setattr(pipeline, "_" + step, helper)

    def page(url):
        gone = any(url.rstrip("/").endswith("/" + number) for number in closed)
        return {"status": 404 if gone else 200, "final_url": url,
                "text": "" if gone else "Data Analyst in Dublin. Apply now."}

    monkeypatch.setattr(JobQualityService, "_fetch", staticmethod(page))
    run_id = uuid.uuid4().hex
    config = {"count": 3, "source": "default", "provider": "auto", "model": "auto",
              "steps": {step: True for step in STEP_IDS}}
    progress = {"stage": "Waiting to start", "jobs_target": 3, "started_epoch": 0, "find": {"state": "waiting"}, "jobs": []}
    with services.w.connect() as db:
        db.execute("INSERT INTO pipeline_runs(id,state,config,progress,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   (run_id, "queued", json.dumps(config), json.dumps(progress), services.now(), services.now()))
    pipeline._run(run_id)
    return pipeline.get(run_id), runner, studio, ran


def test_a_short_search_looks_again_then_takes_each_job_end_to_end(tmp_path, monkeypatch):
    # "Find 3": the first pass saves one, the focused passes save the other two.
    run, runner, studio, ran = chain(tmp_path, monkeypatch, [["Acme Analytics"], ["Birch Data"], ["Cedar Insights"]])
    assert run["state"] == "completed", run["error"]
    searches = [call for call in runner.calls if call["kind"] == "discovery"]
    assert [call["count"] for call in searches] == [3, 2, 1]
    assert searches[0]["focus"] is None and searches[1]["focus"]["queries"]
    # A follow-up pass skips everything the earlier passes already looked at.
    assert "https://jobs.example/turned-away/1" in searches[1]["focus"]["skip_urls"]
    assert "https://jobs.example/rejected/1" in searches[1]["focus"]["skip_urls"]
    find = run["progress"]["find"]
    assert find["found"] == 3 and len(find["passes"]) == 3 and "in 3 searches" in find["note"]
    # Every job goes through every helper, in order, before the next job starts.
    order = [step for step in STEP_IDS if step != "posting"]
    assert ran == [(step, company) for company in ("Acme Analytics", "Birch Data", "Cedar Insights") for step in order]
    for job in run["progress"]["jobs"]:
        assert list(job["steps"]) == STEP_IDS
        assert all(step["state"] == "done" for step in job["steps"].values())
    assert run["progress"]["ready"] == {"ready": 0, "review": 3, "blocked": 0}


def test_a_full_first_search_does_not_search_again(tmp_path, monkeypatch):
    run, runner, _, _ = chain(tmp_path, monkeypatch, [["Acme Analytics", "Birch Data", "Cedar Insights"]])
    assert [call["kind"] for call in runner.calls].count("discovery") == 1
    assert run["progress"]["find"]["found"] == 3


def test_a_closed_posting_gets_nothing_more_and_no_folder(tmp_path, monkeypatch):
    run, _, studio, ran = chain(tmp_path, monkeypatch, [["Acme Analytics", "Birch Data", "Cedar Insights"]],
                                closed=("11",))  # the first pass numbers its postings 10, 11, 12
    jobs = {job["company"]: job for job in run["progress"]["jobs"]}
    gone = next(job for job in jobs.values() if job["steps"]["posting"]["state"] == "failed")
    assert "closed" in gone["steps"]["posting"]["error"]
    assert all(step["state"] == "skipped" for name, step in gone["steps"].items() if name != "posting")
    assert gone["id"] not in studio.opened and not any(company == gone["company"] for _, company in ran)
    assert len(studio.opened) == 2  # the two open postings were prepared


def test_the_morning_hunt_uses_the_same_helpers_by_default():
    from backend.services.hunt import DEFAULTS

    assert DEFAULTS["steps"] == {step: True for step in STEP_IDS}
    assert pipeline_module.DEFAULT_STEPS == DEFAULTS["steps"]
    assert STEP_IDS[0] == "posting" and STEP_IDS[-1] == "ready"


# ----- the ready-to-submit check ----------------------------------------------------------------------

class BuiltStudio:
    """A Studio whose current draft has a built, assessed PDF of the contract's size."""

    def __init__(self, services, job_id):
        self.services, self.job_id = services, job_id
        folder = services.w.root / "data/output/sample-job/preview"
        folder.mkdir(parents=True)
        (folder / "resume.pdf").write_bytes(b"%PDF-1.7 sample")
        self.pdf_sha256 = hashlib.sha256(b"%PDF-1.7 sample").hexdigest()

    def contract(self, job_id):
        return contract_for(self.services.w.root)

    def get(self, job_id):
        return {"revision": 4, "profile_revision": self.services.w.evidence().get("candidate_revision"),
                "preview": {"current": True, "revision": 4, "page_count": self.contract(job_id).pages,
                            "path": "sample-job/preview", "layout": {"full_pages": True}},
                "match": {"current": True, "score": 80, "resume_coverage": {"score": 80}, "ats_readiness": {"score": 90}}}


def saved_job(services):
    job = services.add_posting(posting(7, company="Acme Analytics"), source="discovery")["job"]
    with services.w.connect() as db:
        db.execute("UPDATE jobs SET posting_state='active', last_verified_at=? WHERE id=?", (services.now(), job["id"]))
        db.execute("UPDATE companies SET legitimacy_state='verified' WHERE id=?", (services.w.get_job(job["id"])["company_id"],))
    fit.for_job(services, job["id"])  # the rules requirement check (no AI in tests)
    return services.w.get_job(job["id"])


def reviewed(services, job, pdf_sha256):
    jd_sha256 = hashlib.sha256(job["description"].encode()).hexdigest()
    with services.w.connect() as db:
        db.execute("INSERT INTO agent_runs(id,kind,job_id,state,input,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   (uuid.uuid4().hex, "resume_match", job["id"], "completed", "{}",
                    json.dumps({"pdf_sha256": pdf_sha256, "jd_sha256": jd_sha256,
                                "review": {"summary": "Shows SQL and Power BI with quoted evidence.",
                                           "verdict": "pass", "issues": []}}),
                    services.now(), services.now()))


def test_a_job_with_no_resume_is_not_ready(tmp_path):
    from backend.services.resume_studio import ResumeStudio

    services = ireland_profile(tmp_path)
    job = services.add_posting(posting(3), source="discovery")["job"]
    result = readiness.check(services, ResumeStudio(services), job["id"])
    assert result["verdict"] == "blocked" and result["label"] == "Not ready"
    assert result["score"] is None
    checks = {c["id"]: c for c in result["checks"]}
    assert checks["pdf"]["state"] == "fail" and checks["posting"]["state"] == "warn"
    assert "not a prediction of an interview" in result["note"]


def test_a_built_and_reviewed_resume_waits_only_for_her_decisions(tmp_path):
    services = ireland_profile(tmp_path)
    job = saved_job(services)
    studio = BuiltStudio(services, job["id"])
    reviewed(services, job, studio.pdf_sha256)
    with services.w.connect() as db:
        db.execute("INSERT INTO resume_items(id,job_id,section,content,origin,evidence_id,decision,created_at,updated_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", ("item-1", job["id"], "skills", "Looker", "predicted", "", "pending",
                                                 services.now(), services.now()))
    result = readiness.check(services, studio, job["id"])
    checks = {c["id"]: c for c in result["checks"]}
    assert checks["review"]["state"] == "pass" and "Power BI" in checks["review"]["note"]
    assert checks["assurance"]["state"] == "warn" and "1 suggested item" in checks["assurance"]["note"]
    assert result["verdict"] in {"review", "blocked"}
    # The score is the fixed-weight mix of what was measured, never an interview probability.
    parts = result["parts"]
    assert parts["coverage"] == 80 and parts["ats"] == 90 and isinstance(parts["fit"], int)
    assert result["score"] == round((50 * parts["fit"] + 35 * 80 + 15 * 90) / 100)

    with services.w.connect() as db:
        db.execute("UPDATE resume_items SET decision='kept' WHERE id='item-1'")
    assert {c["id"]: c for c in readiness.check(services, studio, job["id"])["checks"]}["assurance"]["state"] == "pass"


def test_a_review_of_an_older_pdf_does_not_count(tmp_path):
    services = ireland_profile(tmp_path)
    job = saved_job(services)
    studio = BuiltStudio(services, job["id"])
    reviewed(services, job, "an-older-pdf")
    checks = {c["id"]: c for c in readiness.check(services, studio, job["id"])["checks"]}
    assert checks["review"]["state"] == "warn" and "No independent review of this PDF" in checks["review"]["note"]


def test_a_closed_posting_blocks_the_application(tmp_path):
    services = ireland_profile(tmp_path)
    job = saved_job(services)
    with services.w.connect() as db:
        db.execute("UPDATE jobs SET posting_state='expired' WHERE id=?", (job["id"],))
    result = readiness.check(services, BuiltStudio(services, job["id"]), job["id"])
    assert result["verdict"] == "blocked" and "closed" in result["next"][0]


# ----- the gates that turned good leads away (26 Sep) ------------------------------------------------

def test_trust_and_safety_titles_match_their_target_roles():
    roles = ["Trust & Safety", "Content Moderation", "Policy Operations", "Platform Integrity", "Online Safety"]
    matcher = RoleMatcher(roles, related_titles(roles))
    for title in ("Abuse Investigator", "Content Moderator", "Child Safety Enforcement Specialist",
                  "Trust and Safety Analyst", "Integrity Analyst", "Policy Enforcement Specialist"):
        assert matcher.search(title), title
    for title in ("Safety Engineer", "Health and Safety Officer", "Content Designer", "Software Engineer"):
        assert not matcher.search(title), title
    # Another profile's roles are untouched.
    analyst = RoleMatcher(["Data Analyst"], related_titles(["Data Analyst"]))
    assert not analyst.search("Abuse Investigator") and not analyst.search("Content Moderator")


def test_an_ashby_posting_keeps_its_other_locations(monkeypatch):
    board = {"jobs": [{"id": "role-1", "title": "Child Safety Enforcement Specialist", "location": "San Francisco",
                       "secondaryLocations": [{"location": "Dublin, Ireland"}], "descriptionPlain": "Review reports."}]}
    monkeypatch.setattr(portals, "_get_json", lambda url: (board, None))
    read = portals.official_posting("https://jobs.ashbyhq.com/example/role-1")
    assert read["location"] == "San Francisco; Dublin, Ireland"
    found, _ = portals.fetch_board({"name": "Example", "careers_url": "https://jobs.ashbyhq.com/example"})
    assert found and "Dublin" in found[0]["location"]


# ----- "find jobs" in the chat ------------------------------------------------------------------------

def test_find_jobs_in_the_chat_starts_the_whole_pipeline(tmp_path):
    from test_assistant_hunt import FakeHunt, assistant_for

    services = ireland_profile(tmp_path)
    assistant, tools = assistant_for(services, FakeHunt())
    started = []
    tools.pipeline = object()  # the app has a Daily Search pipeline
    tools.run_search_pipeline = lambda count=None, **_: started.append(count) or {
        "summary": "Started", "pipeline_run_id": "p1", "jobs_target": count or 5}
    for text, count in (("give me 5 jobs", 5), ("find jobs", None), ("find me 3 jobs with resumes", 3),
                        ("find 40 jobs", 15)):
        reply = assistant.send(text)
        assert reply["state"] == "done" and "independent review" in reply["response"], text
        assert started[-1] == count, text
    # Nothing ran a bare discovery pass.
    with services.w.connect() as db:
        assert not db.execute("SELECT 1 FROM agent_runs WHERE kind='discovery'").fetchone()


# ----- a degree with no university recorded (26 Sep) --------------------------------------------------

def degree_without_university(services):
    path = services.w.root / "data/context/evidence.yml"
    import yaml

    evidence = yaml.safe_load(path.read_text(encoding="utf-8"))
    evidence["claims"].append({
        "id": "EDU-HISTORY-001", "category": "education_history", "status": "user_reported",
        "approved_external_use": "Profile context; not printed on resumes.", "source_refs": [],
        "title": "MSc", "value": "MSc; Computer Science; 2024-09 to 2025-09; Databases, Cloud Computing"})
    path.write_text(yaml.safe_dump(evidence), encoding="utf-8")


def test_a_degree_without_its_university_still_meets_a_degree_requirement(tmp_path):
    services = ireland_profile(tmp_path)
    degree_without_university(services)
    entries = {e["id"]: e for e in fit.catalogue(services)["entries"]}
    degree = entries["EDU-HISTORY-001"]
    assert degree["kind"] == "education" and degree["text"] == "MSc; Computer Science"
    # The modules are never read as the degree.
    assert "Databases" not in degree["text"]
    matrix = fit.verify({"requirements": [{"text": "Degree in computer science", "category": "required",
                                           "excerpt": "Requirements:", "status": "met",
                                           "evidence_ids": ["EDU-HISTORY-001"]}]}, JD, fit.catalogue(services))
    assert matrix["requirements"][0]["status"] == "met"


def test_the_final_check_says_when_the_resume_has_no_education_section(tmp_path):
    services = ireland_profile(tmp_path)
    degree_without_university(services)
    job = saved_job(services)
    checks = {c["id"]: c for c in readiness.check(services, BuiltStudio(services, job["id"]), job["id"])["checks"]}
    assert checks["education"]["state"] == "warn" and "Add the university in Profile" in checks["education"]["note"]


# ----- demo mode: gates advise instead of blocking ----------------------------------------------------

def test_demo_mode_readiness_is_never_blocked(tmp_path):
    from backend.services.resume_studio import ResumeStudio

    services = ireland_profile(tmp_path)
    services.set_pref("demo_mode", "1")
    job = saved_job(services)
    with services.w.connect() as db:
        # Pending profile edits and an undecided Assurance suggestion would normally hold the resume.
        db.execute("UPDATE knowledge SET review_state='user_updated' WHERE id='SKILL-001'")
        db.execute("INSERT INTO resume_items(id,job_id,section,content,origin,evidence_id,decision,created_at,updated_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", ("item-9", job["id"], "skills", "Looker", "predicted", "", "pending",
                                                 services.now(), services.now()))
    result = readiness.check(services, ResumeStudio(services), job["id"])
    assert result["demo_mode"] is True
    assert result["verdict"] != "blocked"  # no draft at all: still only "review" in demo mode
    checks = {c["id"]: c for c in result["checks"]}
    assert checks["assurance"]["state"] == "pass" and "demo mode" in checks["assurance"]["note"]
    assert checks["profile"]["state"] == "pass" and "demo mode" in checks["profile"]["note"]
    # The same job with demo mode off is blocked as before.
    services.set_pref("demo_mode", "0")
    result = readiness.check(services, ResumeStudio(services), job["id"])
    assert result["demo_mode"] is False and result["verdict"] == "blocked"


def test_demo_mode_keeps_a_below_bar_posting_with_a_note(tmp_path, monkeypatch):
    from backend.services import job_sources
    from backend.services.agents import AgentRunner

    def search(tmp, demo):
        services = ireland_profile(tmp)
        if demo:
            services.set_pref("demo_mode", "1")
        runner = AgentRunner(services)
        odd = posting(9, company="Mercy Hospital", title="Senior Neurologist")
        odd["description"] = ("Consultant neurologist for our Dublin hospital. 15+ years of clinical practice and "
                              "board certification in neurology are required.\n" + JD)
        monkeypatch.setattr(job_sources, "harvest",
                            lambda root, sources, **kw: ([dict(odd)], ["stub coverage"]))
        result = wait_for(services, runner.enqueue("discovery", None, None, None, "feeds", count=1,
                                                   focus={"hunt": True, "sources": ["directory"],
                                                          "min_fit": 95, "require_ai_fit": True})["id"])
        return services, result

    # Demo mode on: the fit bar and the missing free AI plan advise instead of turning the posting away.
    services, result = search(tmp_path, demo=True)
    assert len(result["added_job_ids"]) == 1, result.get("summary")
    job = services.w.get_job(result["added_job_ids"][0])
    assert "demo:" in (job.get("notes") or "")

    # Demo mode off: the same posting is turned away by the relevance gate, never saved.
    _, result = search(tmp_path / "off", demo=False)
    assert result["added_job_ids"] == []
    assert any("relevance gate" in line for line in result["rejected_leads"])
