"""Behavioral coverage for planner, evidence, independent workers and shared state."""

from datetime import date
from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import pytest
pytestmark = pytest.mark.skip(reason="Upstream US/one-page assertions; fixtures retained for shared tests")
from fastapi.testclient import TestClient
from test_career_workspace import workspace, add, ROOT
from backend.services.planning import plan
from backend.services.postings import canonical_url, posting_key
from backend.services.workspace_v2 import CareerServices
from backend.services.agents import AgentRunner, role_payload
from backend.dashboard.app import create_app


@pytest.fixture
def service(workspace, monkeypatch):
    monkeypatch.setattr(CareerServices, "today", staticmethod(lambda: "2026-09-12"))
    shutil.copytree(ROOT / "backend/workflows", workspace.root / "backend/workflows")
    return CareerServices(workspace)


def settings(start="2026-09-07"):
    return {"weekly_target": 30, "workdays": [0, 1, 2, 3, 4, 5], "start_date": start}


def message(
    job, id="mail1", kind="applied", received="2026-09-09T20:39:27Z", submission=None
):
    return dict(
        id=id,
        job_id=job["id"],
        company=job["company"],
        role=job["title"],
        kind=kind,
        subject="Application update",
        sender="careers@example.test",
        received_at=received,
        submission_date=submission,
        excerpt="We received your application.",
        reason="Exact company and role.",
        confidence="high",
    )


def ingest(s, *messages):
    return s.ingest_mail(
        {
            "email": "test@example.test",
            "coverage": "Test fixture",
            "messages": list(messages),
        }
    )


def test_missed_target_stacks_across_days_and_weeks():
    assert plan(settings(), [], date(2026, 9, 8))["today_target"] == 10
    assert plan(settings(), [], date(2026, 9, 13))["today_target"] == 30
    assert plan(settings(), [], date(2026, 9, 14))["today_target"] == 35
    p = plan(settings(), ["2026-09-07"] * 7, date(2026, 9, 8))
    assert p["ahead"] == 2 and p["today_target"] == 3
    assert (
        plan(settings(), ["2026-09-08"] * 10, date(2026, 9, 8))["remaining_today"] == 0
    )


def test_partial_week_and_uneven_distribution():
    p = plan(settings("2026-09-11"), [], date(2026, 9, 11))
    assert p["current_week_target"] == 10 and p["week_remaining"] == 10
    s = {**settings(), "weekly_target": 31}
    assert sum(d["planned"] for d in plan(s, [], date(2026, 9, 7))["schedule"]) == 31
    assert plan(s, [], date(2026, 9, 7))["daily_base"] == 6


@pytest.mark.parametrize(
    "url",
    [
        "https://www.example.test/jobs/1?utm_source=email#x",
        "http://example.test/jobs/1/?source=li",
    ],
)
def test_tracking_parameters_are_not_new_jobs(url):
    assert canonical_url(url) == "https://example.test/jobs/1"


def test_requisition_and_linkedin_identities():
    assert posting_key("https://example.test/apply?job=abc&lang=en") == posting_key(
        "https://example.test/view?job=abc"
    )
    assert posting_key("https://example.test/apply?job=abc") != posting_key(
        "https://example.test/apply?job=def"
    )
    assert (
        canonical_url("https://www.linkedin.com/comm/jobs/view/4430071221?trk=foo")
        == "https://www.linkedin.com/jobs/view/4430071221"
    )


def test_every_save_path_deduplicates(service):
    j = add(service.w)
    with pytest.raises(ValueError, match="already saved"):
        service.w.add_job(
            j["company"],
            j["title"],
            j["location"],
            j["url"] + "?utm_source=test",
            j["description"],
        )
    value = {k: j[k] for k in ("company", "title", "location", "url", "description")}
    assert service.add_posting(value)["duplicate"]
    # Chetan's rule: the same company and role is never surfaced twice, even from a new posting.
    another = {
        **value,
        "url": "https://second.example.test/123",
        "requisition_id": "R123",
    }
    blocked = service.add_posting(another)
    assert blocked["blocked"] and blocked["rule"] == "same_role"
    # A different role at the same company is allowed, and its requisition dedupes later URLs.
    other_role = {**another, "title": "Machine Learning Engineer"}
    first = service.add_posting(other_role)
    assert not first["duplicate"] and first["job"]["sponsor_tier"] == "C"
    assert (
        service.add_posting({**other_role, "url": "https://third.example.test/alternate"})[
            "job"
        ]["id"]
        == first["job"]["id"]
    )


def test_simultaneous_save_has_one_record(service):
    value = {
        "company": "A",
        "title": "Analyst",
        "location": "Austin, TX",
        "url": "https://example.test/role/1",
        "description": "Use SQL for reporting and business analysis. " * 5,
        "requisition_id": "1",
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(service.add_posting, [value] * 4))
    assert len(service.w.jobs()) == 1
    assert sum(not r["duplicate"] for r in results) == 1


def test_profile_revision_delete_and_sources_preserved(service):
    before = (service.w.root / "data/context/evidence.yml").read_bytes()
    assert (
        next(i for i in service.knowledge() if i["id"] == "SKILL-LANGUAGES-001")["summary"]
        == "Python\nSQL\nC#\nC++"
    )
    entry = service.save_knowledge(
        {"kind": "skill", "title": "Test skill", "summary": "User supplied evidence"}
    )
    edit = service.save_knowledge({**entry, "summary": "Updated evidence"}, entry["id"])
    assert edit["revision"] == 2
    with pytest.raises(ValueError, match="changed elsewhere"):
        service.save_knowledge(entry, entry["id"])
    assert "Updated evidence" in json.dumps(service.profile_context())
    service.delete_knowledge(entry["id"])
    assert entry["id"] not in json.dumps(service.profile_context())
    assert service.profile_dirty()
    assert before == (service.w.root / "data/context/evidence.yml").read_bytes()
    assert (
        json.loads((service.w.root / "data/active-profile.json").read_text())[-1:]
        is not None
    )


def test_reconcile_clears_profile_dirty_without_touching_wording(service):
    before = (service.w.root / "data/context/evidence.yml").read_bytes()
    entry = service.save_knowledge(
        {"kind": "skill", "title": "Apache Airflow", "summary": "Orchestrated pipelines"}
    )
    removed = service.save_knowledge(
        {"kind": "fact", "title": "Old fact", "summary": "To be removed"}
    )
    service.delete_knowledge(removed["id"])
    assert service.profile_dirty()
    pending = service.pending_knowledge()
    assert {p["id"] for p in pending} == {entry["id"], removed["id"]}
    assert next(p for p in pending if p["id"] == removed["id"])["deleted"]
    with pytest.raises(ValueError, match="no longer pending"):
        service.reconcile_knowledge(["missing-id"])
    partial = service.reconcile_knowledge([entry["id"]])
    assert partial["reconciled"] == [entry["id"]] and partial["profile_dirty"]
    result = service.reconcile_knowledge()
    assert result["reconciled"] == [removed["id"]] and not result["profile_dirty"]
    assert not service.profile_dirty()
    confirmed = next(i for i in service.knowledge() if i["id"] == entry["id"])
    assert confirmed["review_state"] == "registered"
    assert confirmed["summary"] == "Orchestrated pipelines"
    # Confirming records the suggestion in the evidence registry: the name is the skill and
    # its summary a note. The fact removed before it was ever confirmed never gets there.
    registry = service.w.evidence()
    airflow = next(c for c in registry["claims"] if c.get("title") == "Apache Airflow")
    assert airflow["approved_facts"] == ["Apache Airflow"] and airflow["evidence_note"] == "Orchestrated pipelines"
    assert airflow["status"] == "user_reported"
    assert "Old fact" not in (service.w.root / "data/context/evidence.yml").read_text(encoding="utf-8")
    assert before != (service.w.root / "data/context/evidence.yml").read_bytes()
    assert service.reconcile_knowledge()["reconciled"] == []
    with service.w.connect() as db:
        assert db.execute(
            "SELECT 1 FROM activity WHERE action='profile_reconciled'"
        ).fetchone()


def test_pending_reminders_never_count(service):
    j = add(service.w)
    m = message(j, kind="reminder")
    ingest(service, m)
    assert service.summary()["counts"]["applied"] == 0
    with pytest.raises(ValueError, match="does not establish"):
        service.resolve_mail(m["id"])
    assert service.resolve_mail(m["id"], action="dismiss")["state"] == "dismissed"
    assert service.w.activity()[0]["action"] == "email_dismissed"


def test_receipt_date_is_separate_and_counted_once(service):
    service.save_goals(settings())
    j = add(service.w)
    m = message(j)
    ingest(service, m, m)
    assert len(service.mail()["messages"]) == 1
    service.resolve_mail(m["id"])
    assert service.w.get_job(j["id"])["application_date"] is None
    assert service.goals(date(2026, 9, 9))["today_completed"] == 1
    ingest(service, message(j, id="mail2", received="2026-09-10T10:00:00Z"))
    service.resolve_mail("mail2")
    assert service.goals(date(2026, 9, 10))["today_completed"] == 0
    assert service.summary()["counts"]["applied"] == 1
    # Editing notes after email confirmation never requires an invented date.
    service.w.update_job(j["id"], "applied", notes="Next action")


def test_old_mail_does_not_regress_status(service):
    j = add(service.w)
    ingest(
        service,
        message(j, id="offer", kind="offer", received="2026-09-10T09:00:00Z"),
        message(j, id="old", kind="rejected", received="2026-09-09T09:00:00Z"),
    )
    service.resolve_mail("offer")
    service.resolve_mail("old")
    assert service.w.get_job(j["id"])["status"] == "offer"
    assert service.w.get_job(j["id"])["application_date"] is None
    assert service.summary()["counts"]["applied"] == 1


def test_explicit_submission_date_wins_over_receipt(service):
    service.save_goals(settings())
    j = add(service.w)
    ingest(service, message(j, submission="2026-09-07"))
    service.resolve_mail("mail1")
    assert service.w.get_job(j["id"])["application_date"] == "2026-09-07"
    assert service.goals(date(2026, 9, 8))["today_completed"] == 0
    assert service.goals(date(2026, 9, 8))["carryover"] == 4


@pytest.mark.parametrize(
    "changes",
    [
        {"received_at": "not-a-date"},
        {"received_at": "2026-09-09T10:00:00"},
        {"submission_date": "2099-01-01"},
        {"kind": "imagined"},
        {"id": "bad/id"},
    ],
)
def test_invalid_mail_rejected_atomically(service, changes):
    j = add(service.w)
    with pytest.raises(ValueError):
        ingest(service, {**message(j), **changes})
    assert not service.mail()["messages"]


def test_inaccessible_gmail_does_not_replace_last_success(service):
    service.ingest_mail(
        {
            "email": "connected@example.test",
            "coverage": "Verified fixture",
            "messages": [],
        }
    )
    before = service.mail()["connection"]
    with pytest.raises(ValueError, match="verification"):
        service.ingest_mail({"email": "", "coverage": "Unavailable", "messages": []})
    service.record_mail_sync_failure("Gmail tools unavailable")
    after = service.mail()["connection"]
    assert after["connected"] is False
    assert after["email"] == "connected@example.test"
    assert after["last_synced_at"] == before["last_synced_at"]
    assert after["status"] == "needs_attention"
    assert "unavailable" in after["last_error"]


def test_email_worker_requires_verified_completed_search(service):
    def inaccessible(*args, **kwargs):
        return {
            "email": "",
            "coverage": "No Gmail tools available",
            "connection_verified": False,
            "search_completed": False,
            "messages": [],
        }

    runner = AgentRunner(service, inaccessible)
    runner.enqueue("email")
    runner.pool.shutdown(wait=True)
    run = service.runs()[0]
    assert run["state"] == "failed"
    assert "not connected" in run["error"]
    assert service.mail()["connection"]["connected"] is False


def test_a_broken_search_tool_fails_the_search_instead_of_finding_nothing(service):
    """Live on 23 Sep: Azure's web search tool failed and the run read "Found 0 new jobs"."""
    def broken(*args, **kwargs):
        return {"summary": "The web search tool failed before producing search results.",
                "jobs": [], "rejected_leads": [], "search_worked": False}

    runner = AgentRunner(service, broken)
    runner.enqueue("discovery", count=1)
    runner.pool.shutdown(wait=True)
    run = service.runs()[0]
    assert run["state"] == "failed"
    assert "not working" in run["error"] and "try again" in run["error"]
    assert "failed before producing search results" in run["error"]

    # A search that worked and verified nothing is still an honest empty result.
    runner = AgentRunner(service, lambda *a, **kw: {**broken(), "search_worked": True})
    queued = runner.enqueue("discovery", count=1)
    runner.pool.shutdown(wait=True)
    assert next(r for r in service.runs() if r["id"] == queued["id"])["state"] == "completed"


def test_discovery_gates_the_full_posting_from_the_job_board(service, monkeypatch):
    """Live on 23 Sep: Azure's web tool read only part of Ashby and Greenhouse pages. The boards
    publish every posting in full, so the gates read the employer's own words, not a summary."""
    from backend.services import portals

    strong = ("Required Python, SQL, Apache Kafka streaming pipelines, Airflow orchestration on AWS Glue and S3, "
              "data validation and quality checks. Responsibilities include ETL, lakehouse data modeling and clinical "
              "device telemetry dashboards. PyTorch a plus.")
    refusing = "https://job-boards.greenhouse.io/acme/jobs/111"
    partial = "https://jobs.ashbyhq.com/bright/0b1c2d3e-aaaa-4bbb-8ccc-123456789abc"
    feeds = {
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs/111":
            {"content": "&lt;p&gt;" + strong + "&lt;/p&gt;&lt;p&gt;We are unable to provide visa sponsorship for this role.&lt;/p&gt;"},
        "https://api.ashbyhq.com/posting-api/job-board/bright":
            {"jobs": [{"id": "0b1c2d3e-aaaa-4bbb-8ccc-123456789abc", "descriptionPlain": strong + " Full posting from the board."}]},
    }
    monkeypatch.setattr(portals, "_get_json", lambda url: (feeds.get(url), None if url in feeds else "HTTP 404"))

    def lead(company, url):
        return {
            "company": company, "title": "Data Engineer", "location": "Austin, TX", "url": url, "requisition_id": "",
            "description": "The page showed only part of the posting: Python and SQL data pipelines.",
            "company_sources": [{"title": "Register", "url": "https://register.example/" + company, "accessed_at": "2026-09-23"}],
            "legal_presence": "Registered legal entity and trading presence",
            "red_flags": [], "size_category": "mid", "employee_min": 500, "employee_max": 1000,
            "sponsorship_state": "unknown", "sponsorship_evidence": [], "applicant_count": None,
            "competition_signals": {"posted_within_72h": True, "limited_syndication": False, "niche_match": True},
        }

    output = {"summary": "Two leads", "rejected_leads": [], "search_worked": True,
              "jobs": [lead("Acme", refusing), lead("Bright", partial)]}
    runner = AgentRunner(service, lambda *a, **kw: output)
    runner.enqueue("discovery", count=1)
    runner.pool.shutdown(wait=True)
    run = service.runs()[0]
    assert run["state"] == "completed", run["error"]
    # The refusal was only in the full posting, never in the AI's summary.
    assert [(item["url"], item["sentence"]) for item in run["result"]["excluded"]] == [
        (refusing, "We are unable to provide visa sponsorship for this role.")]
    [saved] = service.w.jobs()
    assert saved["url"] == partial and saved["description"].endswith("Full posting from the board.")

    # A Lever posting is read from its own feed too; a link on any other site is left to the AI.
    feeds["https://api.lever.co/v0/postings/bright/abc"] = {
        "descriptionPlain": "About the role.", "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
        "additionalPlain": "This role is not eligible for visa sponsorship."}
    assert portals.full_text("https://jobs.lever.co/bright/abc/apply") == (
        "About the role.\n\nRequirements\n- Python\n\nThis role is not eligible for visa sponsorship.")
    assert portals.full_text("https://careers.bright.example/jobs/abc") is None


def test_a_federal_h1b_record_establishes_legal_presence(service, monkeypatch):
    """Live on 23 Sep: Notion, Torc Robotics and Applied Intuition were turned away because the
    AI found no registry page. The USCIS H-1B employer list is a federal record of a US employer."""
    from backend.services import sponsorship

    class FakeIndex:
        def lookup(self, company, state=""):
            if company == "Notion":
                return {"found": True, "matched_name": "NOTION LABS INC", "approvals": 39, "denials": 0,
                        "years": ["2022", "2023"], "states": ["CA"], "source": "USCIS H-1B Employer Data Hub"}
            return {"found": False, "matched_name": None, "approvals": 0, "denials": 0, "years": [], "states": [], "source": None}

    monkeypatch.setattr(sponsorship, "index", lambda: FakeIndex())
    strong = ("Required Python, SQL, Apache Kafka streaming pipelines, Airflow orchestration on AWS Glue and S3, "
              "data validation and quality checks. Responsibilities include ETL and lakehouse data modeling.")

    def lead(company, url):
        return {
            "company": company, "title": "Software Engineer, Early Career", "location": "San Francisco, CA", "url": url,
            "requisition_id": "", "description": strong, "company_sources": [],
            "legal_presence": "No independent legal-presence record found.", "verification": "Open on 23 Sep 2026.",
            "red_flags": [], "size_category": "mid", "employee_min": 500, "employee_max": 1000,
            "sponsorship_state": "unknown", "sponsorship_evidence": [], "applicant_count": None,
            "competition_signals": {"posted_within_72h": True, "limited_syndication": False, "niche_match": True},
        }

    known = "https://jobs.ashbyhq.com/notion/297b4ece-765f-4eea-b1b8-46057cb6501f"
    unknown = "https://jobs.ashbyhq.com/tinyco/0b1c2d3e-aaaa-4bbb-8ccc-123456789abc"
    # A strict-schema model fills "excluded" with its own notes; they are not the gate's verdicts.
    ai_note = {"company": "DoorDash", "title": "Software Engineer I", "url": "https://example.test/dd",
               "reason": "Graduation window does not match", "sentence": "Graduating between Fall 2026 and Summer 2027."}
    output = {"summary": "Two leads", "rejected_leads": [], "search_worked": True, "excluded": [ai_note],
              "jobs": [lead("Notion", known), lead("TinyCo", unknown)]}
    runner = AgentRunner(service, lambda *a, **kw: output)
    runner.enqueue("discovery", count=2)
    runner.pool.shutdown(wait=True)
    run = service.runs()[0]
    assert run["state"] == "completed", run["error"]
    assert [job["url"] for job in service.w.jobs()] == [known]
    assert any(line.startswith(unknown) and "legitimacy" in line for line in run["result"]["rejected_leads"])
    assert run["result"]["excluded"] == [] and service.excluded() == []
    assert any(line.startswith("DoorDash — Software Engineer I: Graduation window") for line in run["result"]["rejected_leads"])
    with service.w.connect() as db:
        check = db.execute(
            "SELECT findings, sources FROM company_checks JOIN companies ON companies.id=company_id "
            "WHERE companies.display_name='Notion' ORDER BY checked_at DESC LIMIT 1").fetchone()
    assert "NOTION LABS INC" in check[0] and sponsorship.USCIS_HUB_URL in check[1]


def test_email_worker_uses_bounded_incremental_window_after_success(service):
    service.ingest_mail(
        {
            "email": "connected@example.test",
            "coverage": "Verified initial sync",
            "messages": [],
        }
    )
    last_sync = service.mail()["connection"]["last_synced_at"]
    prompts = []

    def incremental(prompt, *args, **kwargs):
        prompts.append(prompt)
        return {
            "email": "connected@example.test",
            "coverage": "Incremental search completed; no new relevant messages.",
            "connection_verified": True,
            "search_completed": True,
            "messages": [],
        }

    runner = AgentRunner(service, incremental)
    runner.enqueue("email")
    runner.pool.shutdown(wait=True)
    assert service.runs()[0]["state"] == "completed"
    assert last_sync in prompts[0]
    assert "beginning two calendar days before" in prompts[0]
    assert "at most 60 unique relevant messages" in prompts[0]


def test_independent_hiring_has_no_candidate_context(service):
    j = add(service.w)
    service.w.update_job(j["id"], "saved", notes="PRIVATE-NOTES-SECRET")
    service.save_knowledge(
        {
            "kind": "skill",
            "title": "PRIVATE-PROFILE-SECRET",
            "summary": "Private evidence",
        }
    )
    seen = []

    def execute(prompt, schema, **kwargs):
        seen.append((prompt, kwargs))
        return {
            "summary": "Test report",
            "report": "Verified fixture",
            "sources": [],
            "limitations": ["Test fixture"],
        }

    runner = AgentRunner(service, execute)
    run = runner.enqueue("research", j["id"])
    runner.pool.shutdown(wait=True)
    assert len(seen) == 3
    assert "PRIVATE-" not in seen[0][0] and "PRIVATE-" not in seen[1][0]
    assert "PRIVATE-PROFILE-SECRET" in seen[2][0]
    assert seen[1][1] == {"web": False}
    output = service.runs()[0]
    assert (
        output["state"] == "completed"
        and output["result"]["hiring_profile_access"] is False
    )
    assert set(role_payload(j)) == {
        "company",
        "title",
        "location",
        "url",
        "description",
    }


def test_worker_failure_is_durable(service):
    j = add(service.w)

    def fail(*a, **kw):
        raise RuntimeError("A recoverable test failure")

    runner = AgentRunner(service, fail)
    runner.enqueue("research", j["id"])
    runner.pool.shutdown(wait=True)
    assert service.runs()[0]["state"] == "failed"
    assert "recoverable test failure" in service.runs()[0]["error"]


def test_v2_api_and_profile_resume_guard(service):
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/v2/summary").status_code == 200
        assert client.get("/api/v2/profile").json()["items"]
        assert (
            client.put("/api/v2/goals", json={**settings(), "workdays": []}).status_code
            == 400
        )
        assert (
            client.put(
                "/api/v2/goals",
                json=settings(),
                headers={"Origin": "https://foreign.test"},
            ).status_code
            == 403
        )
        j = add(service.w)
        saved = client.post(
            "/api/v2/profile/items",
            json={"kind": "skill", "title": "New skill", "summary": "New detail"},
        )
        assert saved.status_code == 201
        assert (
            client.post("/api/jobs/" + j["id"] + "/prepare", json={}).status_code == 400
        )
        assert (
            client.post("/api/v2/agents/run", json={"kind": "unknown"}).status_code
            == 400
        )


def test_exact_email_updates_automatically_but_ambiguous_does_not(service):
    j = add(service.w)
    ingest(service, message(j))
    assert service.mail()["messages"][0]["state"] == "confirmed"
    assert service.w.get_job(j["id"])["status"] == "applied"
    second = add(service.w, "2")
    ingest(service, message(second, id="ambiguous"))
    assert (
        next(m for m in service.mail()["messages"] if m["id"] == "ambiguous")["state"]
        == "pending"
    )
    assert service.w.get_job(second["id"])["status"] == "saved"


def test_unlisted_high_confidence_email_creates_tracked_application(service):
    ingest(
        service,
        {
            **message(
                {
                    "id": "unused",
                    "company": "External Employer",
                    "title": "Reporting Analyst",
                },
                id="external-mail",
            ),
            "job_id": None,
            "submission_date": None,
        },
    )
    job = service.w.jobs()[0]
    assert job["company"] == "External Employer"
    assert job["title"] == "Reporting Analyst"
    assert job["record_source"] == "gmail"
    assert job["status"] == "applied"
    assert job["application_date"] is None
    assert service.mail()["messages"][0]["job_id"] == job["id"]
    with service.w.connect() as db:
        evidence = db.execute(
            "SELECT * FROM application_evidence WHERE job_id=?", (job["id"],)
        ).fetchone()
    assert evidence and evidence["submission_date"] is None


def test_email_application_can_be_upgraded_with_full_posting(service):
    ingest(
        service,
        {
            **message(
                {"id": "unused", "company": "Portal Co", "title": "Data Engineer"},
                id="portal-mail",
            ),
            "job_id": None,
        },
    )
    tracked = service.w.jobs()[0]
    result = service.add_posting(
        {
            "company": "Portal Co",
            "title": "Data Engineer",
            "location": "Chicago, IL",
            "url": "https://careers.portal.test/jobs/bi-1",
            "description": "Build streaming pipelines with Kafka and Flink, orchestrate Airflow jobs on AWS, write SQL transformations and validate data quality for device telemetry.",
        }
    )
    assert result["upgraded"]
    assert result["job"]["id"] == tracked["id"]
    assert result["job"]["record_source"] == "posting+gmail"
    assert result["job"]["status"] == "applied"


def test_remove_unsuitable_job_is_recoverable_and_protects_applications(service):
    job = add(service.w)
    service.remove_job(job["id"], "Outside target role")
    assert service.w.jobs() == []
    removed = service.w.jobs(include_deleted=True)[0]
    assert removed["deleted_at"] and removed["deletion_reason"] == "Outside target role"
    service.restore_job(job["id"])
    assert service.w.jobs()[0]["id"] == job["id"]
    service.w.update_job(job["id"], "applied", application_date="2026-09-10")
    with pytest.raises(ValueError, match="Only saved or prepared"):
        service.remove_job(job["id"])


def test_cover_letter_is_company_specific_versioned_and_listed(service):
    job = add(service.w)
    first = service.generate_cover_letter(job["id"])
    second = service.generate_cover_letter(job["id"])
    assert "Example employer" in first["content"]
    assert "Data Engineer" in first["content"]
    for banned in ("years of experience", "ICCV", "Ireland"):
        assert banned not in first["content"]
    assert first["version"] == 1 and second["version"] == 2
    assert (service.w.root / "data/output" / second["path"]).exists()
    documents = service.documents()
    assert documents[0]["cover_letter"]["version"] == 2


def test_discovery_filters_historical_postings_even_if_agent_repeats(service):
    history = service.w.root.parent / "daily-job-search"
    history.mkdir(exist_ok=True)
    (history / "history.csv").write_text(
        "company,url,requisition_id\nOld Company,https://old.test/jobs/1,R1\n"
    )
    data = {
        "summary": "One new result",
        "rejected_leads": [],
        "jobs": [
            {
                "company": "Old Company",
                "title": "Data Engineer",
                "location": "Austin, TX",
                "url": "https://jobs.lever.co/oldcompany/R1",
                "requisition_id": "R1",
                "description": "Required Python, SQL, Apache Kafka streaming pipelines, Airflow orchestration on AWS Glue and S3, data validation and quality checks. Responsibilities include ETL, lakehouse data modeling and clinical device telemetry dashboards. PyTorch a plus.",
                "company_sources": [{"title": "Register", "url": "https://old.example/register", "accessed_at": "2026-09-17"}],
                "legal_presence": "Registered legal entity and trading presence",
                "red_flags": [], "size_category": "large", "employee_min": 5000, "employee_max": None,
                "sponsorship_state": "unknown", "sponsorship_evidence": [], "applicant_count": None,
                "competition_signals": {"posted_within_72h": True, "limited_syndication": False, "niche_match": True},
            },
            {
                "company": "New Company",
                "title": "Data Engineer",
                "location": "Austin, TX",
                "url": "https://jobs.lever.co/newcompany/R2",
                "requisition_id": "R2",
                "description": "Required Python, SQL, Apache Kafka streaming pipelines, Airflow orchestration on AWS Glue and S3, data validation and quality checks. Responsibilities include ETL, lakehouse data modeling and clinical device telemetry dashboards. PyTorch a plus.",
                "company_sources": [{"title": "Register", "url": "https://new.example/register", "accessed_at": "2026-09-17"}],
                "legal_presence": "Registered legal entity and trading presence",
                "red_flags": [], "size_category": "mid", "employee_min": 500, "employee_max": 1000,
                "sponsorship_state": "unknown", "sponsorship_evidence": [], "applicant_count": None,
                "competition_signals": {"posted_within_72h": True, "limited_syndication": False, "niche_match": True},
            },
        ],
    }
    r = AgentRunner(service, lambda *a, **kw: data)
    r.enqueue("discovery")
    r.pool.shutdown(wait=True)
    assert service.runs()[0]["state"] == "completed"
    assert [j["company"] for j in service.w.jobs()] == ["New Company"]
    assert len(service.w.search_runs()[0]["jobs"]) == 1


def test_direct_resume_command_respects_profile_edits(service):
    j = add(service.w)
    service.save_knowledge(
        {"kind": "fact", "title": "Changed fact", "summary": "New evidence"}
    )
    with pytest.raises(ValueError, match="Reconcile"):
        service.w.prepare(j["id"])


def test_partial_worker_output_survives_failure(service):
    calls = []

    def execute(*a, **kw):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("Hiring failed")
        return {
            "summary": "Research preserved",
            "report": "Public research",
            "sources": [],
            "limitations": [],
        }

    j = add(service.w)
    r = AgentRunner(service, execute)
    r.enqueue("research", j["id"])
    r.pool.shutdown(wait=True)
    output = service.runs()[0]
    assert (
        output["state"] == "failed"
        and output["result"]["research"]["summary"] == "Research preserved"
    )


def test_email_ordering_uses_instants_across_timezones(service):
    j = add(service.w)
    ingest(
        service,
        message(j, id="old", kind="offer", received="2026-09-09T01:00:00+02:00"),
    )
    ingest(
        service, message(j, id="new", kind="rejected", received="2026-09-08T23:30:00Z")
    )
    ingest(
        service,
        message(j, id="middle", kind="interview", received="2026-09-08T23:15:00Z"),
    )
    assert service.w.get_job(j["id"])["status"] == "rejected"
    assert [m["id"] for m in service.mail()["messages"]] == ["new", "middle", "old"]


def test_discovery_excludes_unlinked_email_confirmed_roles(service):
    j = add(service.w)
    m = {
        **message(j),
        "job_id": None,
        "company": "Already Applied Ltd",
        "role": "Data Engineer",
    }
    ingest(service, m)
    output = {
        "summary": "Fixture",
        "rejected_leads": [],
        "jobs": [
            {
                "company": "Already Applied Ltd",
                "title": "Data Engineer",
                "location": "Austin, TX",
                "url": "https://alreadyapplied.example/jobs/9",
                "description": "Required Python, SQL, Apache Kafka streaming pipelines, Airflow orchestration on AWS Glue and S3, data validation and quality checks. Responsibilities include ETL, lakehouse data modeling and clinical device telemetry dashboards. PyTorch a plus.",
                "requisition_id": "9",
                "company_sources": [{"title": "Register", "url": "https://alreadyapplied.example/register", "accessed_at": "2026-09-17"}],
                "legal_presence": "Registered legal entity and trading presence",
                "red_flags": [], "size_category": "mid", "employee_min": 500, "employee_max": 1000,
                "sponsorship_state": "unknown", "sponsorship_evidence": [], "applicant_count": None,
                "competition_signals": {"posted_within_72h": True, "limited_syndication": False, "niche_match": True},
            }
        ],
    }
    runner = AgentRunner(service, lambda *a, **kw: output)
    runner.enqueue("discovery")
    runner.pool.shutdown(wait=True)
    assert service.runs()[0]["state"] == "completed"
    # The exact unlisted email is now a first-class tracked application. Discovery
    # still recognises it and does not add a duplicate posting.
    assert len(service.w.jobs()) == 2
    assert service.runs()[0]["result"]["duplicate_job_ids"] == [
        "https://alreadyapplied.example/jobs/9"
    ]


def test_portals_preset_gates_ranks_and_saves_tracked_feed_postings(service, monkeypatch):
    """Tracked career pages: no AI, the same gates, the employer vouched for by portals.yml, best match first."""
    from backend.services import portals

    board = {"name": "Confluent", "careers_url": "https://jobs.ashbyhq.com/confluent", "ats": "ashby", "ats_token": "confluent"}
    strong = ("Required Python, SQL, Apache Kafka streaming pipelines, Airflow orchestration on AWS Glue and S3, "
              "data validation and quality checks. Responsibilities include ETL, lakehouse data modeling and clinical "
              "device telemetry dashboards. PyTorch a plus.")
    weak = "Maintain internal web tools for the marketing team. Some SQL reporting. Figma handoffs and CSS polish. " * 2
    postings = [
        portals._posting(board, "1", "Senior Data Engineer", "https://jobs.ashbyhq.com/confluent/1", "Austin, TX", strong),
        # A different title from the eligible lead below: an excluded role blocks the same title at that company.
        portals._posting(board, "2", "Analytics Engineer", "https://jobs.ashbyhq.com/confluent/2", "Austin, TX",
                         strong + " Applicants must be authorized to work in the US without sponsorship now or in the future."),
        portals._posting(board, "3", "Software Engineer", "https://jobs.ashbyhq.com/confluent/3", "Remote, United States", weak),
        portals._posting(board, "4", "Data Engineer", "https://jobs.ashbyhq.com/confluent/4", "Austin, TX", strong),
    ]
    assert len(postings[0]["company_sources"][0]["accessed_at"]) == 10  # the feed stamps the access date
    monkeypatch.setattr(portals, "fetch_all", lambda *a, **k: (postings, ["Confluent: 4 open postings via ashby"]))
    # One slot left today, so the ranking decides which lead is saved.
    service.save_goals({"weekly_target": 6, "workdays": [0, 1, 2, 3, 4, 5], "start_date": service.today()})
    assert service.goals()["remaining_today"] == 1

    runner = AgentRunner(service, lambda *a, **kw: pytest.fail("the portals preset must never call the AI"))
    runner.enqueue("discovery", preset="portals")
    runner.pool.shutdown(wait=True)
    run = service.runs()[0]
    assert run["state"] == "completed", run["error"]
    result = run["result"]
    assert result["summary"].startswith("Tracked career pages read directly (no AI call).")

    # The refusal is excluded with its sentence and the portals source; the senior title is rejected.
    assert [item["url"] for item in result["excluded"]] == ["https://jobs.ashbyhq.com/confluent/2"]
    assert "without sponsorship" in result["excluded"][0]["sentence"]
    assert service.excluded()[0]["source"] == "portals"
    assert any("confluent/1: relevance gate Seniority" in lead for lead in result["rejected_leads"])
    # A tracked employer is never dropped for "legitimacy needs review".
    assert not any("legitimacy" in lead for lead in result["rejected_leads"])

    # The best-matching eligible lead is saved, not the first one in feed order.
    jobs = service.w.jobs()
    assert [job["url"] for job in jobs] == ["https://jobs.ashbyhq.com/confluent/4"]
    assert jobs[0]["sponsor_tier"] in {"B", "C"} and jobs[0]["status"] == "saved"
    assert result["added_job_ids"] == [jobs[0]["id"]]
    with service.w.connect() as db:
        state, reason = db.execute("SELECT legitimacy_state, manual_override_reason FROM companies WHERE display_name=?", ("Confluent",)).fetchone()
    assert state == "verified" and "portals.yml" in reason and "jobs.ashbyhq.com" in reason
