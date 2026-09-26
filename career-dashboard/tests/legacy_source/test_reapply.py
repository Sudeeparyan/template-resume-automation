"""Never apply twice: same role forever, rejected company 180 days, ghosted after 21 quiet days."""
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from backend.services import reapply
from test_career_workspace import workspace, add, JD  # noqa: F401 - fixtures
from test_workspace_v2 import service  # noqa: F401

TODAY = date(2026, 9, 18)


def row(company, title, status, updated="2026-09-01", **extra):
    return {"company": company, "title": title, "status": status, "updated_at": updated + "T10:00:00+00:00", "url": "https://x.test/" + title.replace(" ", "-"), **extra}


def test_same_company_and_role_is_blocked_at_any_status():
    for status in ("saved", "prepared", "applied", "interview", "offer", "rejected", "withdrawn", "ghosted"):
        result = reapply.check("Acme", "Data Engineer", [row("Acme", "Data Engineer", status)], today=TODAY)
        assert result["blocked"] and result["rule"] == "same_role", status
    # Normalisation: punctuation and case do not create a "different" role.
    assert reapply.check("ACME, Inc.", "data-engineer", [row("Acme Inc", "Data Engineer", "saved")], today=TODAY)["blocked"]
    # An excluded posting also counts as seen.
    excluded = [{"company": "Acme", "title": "Data Engineer", "excluded_at": "2026-09-10T00:00:00+00:00", "url": "https://x.test/e"}]
    assert reapply.check("Acme", "Data Engineer", [], excluded, today=TODAY)["rule"] == "same_role"


def test_rejection_hides_the_whole_company_for_180_days():
    recent = [row("Acme", "Data Engineer", "rejected", updated="2026-03-25")]  # 177 days ago
    result = reapply.check("Acme", "ML Engineer", recent, today=TODAY)
    assert result["blocked"] and result["rule"] == "rejected_180" and "more days" in result["note"]
    old = [row("Acme", "Data Engineer", "rejected", updated="2026-03-20")]  # 182 days ago
    assert not reapply.check("Acme", "ML Engineer", old, today=TODAY)["blocked"]
    # The exact role stays blocked even after the cooldown.
    assert reapply.check("Acme", "Data Engineer", old, today=TODAY)["rule"] == "same_role"


def test_ghosted_company_allows_a_different_role_after_90_days():
    fresh = [row("Acme", "Data Engineer", "ghosted", updated="2026-08-01")]  # 48 days ago
    assert reapply.check("Acme", "ML Engineer", fresh, today=TODAY)["rule"] == "ghosted_90"
    old = [row("Acme", "Data Engineer", "ghosted", updated="2026-06-01")]  # 109 days ago
    result = reapply.check("Acme", "ML Engineer", old, today=TODAY)
    assert not result["blocked"] and "ghosted" in result["note"]
    assert reapply.check("Acme", "Data Engineer", old, today=TODAY)["rule"] == "same_role"


def test_applied_and_silent_for_21_days_is_due_for_ghosting():
    quiet = row("Acme", "Data Engineer", "applied", application_date="2026-08-27")   # 22 days
    fresh = row("Beta", "Data Engineer", "applied", application_date="2026-09-01")   # 17 days
    replied = row("Gamma", "Data Engineer", "interview", application_date="2026-08-01")
    due = reapply.due_for_ghosting([quiet, fresh, replied], today=TODAY)
    assert [d["company"] for d in due] == ["Acme"] and due[0]["quiet_days"] == 22
    assert reapply.quiet_days(fresh, today=TODAY) == 17


def test_settings_come_from_profile_yml():
    assert reapply.settings({"reapply": {"ghost_after_days": 30}})["ghost_after_days"] == 30
    assert reapply.settings({})["reject_cooldown_days"] == 180


def test_service_ages_applications_and_a_reply_reopens_them(service):
    job = add(service.w)
    service.w.update_job(job["id"], "applied", application_date="2026-08-01")
    flipped = service.age_applications()
    assert flipped == [job["id"]]
    assert service.w.get_job(job["id"])["status"] == "ghosted"
    assert service.summary()["counts"]["ghosted"] == 1
    # A second pass does nothing; a status change (a reply) takes it back out of ghosted.
    assert service.age_applications() == []
    service.w.update_job(job["id"], "interview")
    assert service.w.get_job(job["id"])["status"] == "interview"
    # The ghosted company may be approached again for a different role only after 90 days.
    service.w.update_job(job["id"], "ghosted")
    blocked = service.add_posting({"company": job["company"], "title": "Machine Learning Engineer", "location": "Austin, TX", "url": "https://careers.example.test/jobs/ml", "description": JD})
    assert blocked["blocked"] and blocked["rule"] == "ghosted_90"


def test_fresh_start_keeps_the_never_reapply_memory(service):
    """Clearing the job list must not let an already-seen role or a rejecting company back in."""
    from fresh_start import fresh_start
    seen = add(service.w)
    rejected = service.add_posting({"company": "Beta Health", "title": "Data Engineer", "location": "Austin, TX",
                                    "url": "https://careers.beta.test/jobs/1", "description": JD})["job"]
    service.w.update_job(rejected["id"], "rejected", notes="Declined after screen")
    with service.w.connect() as db:
        db.execute("INSERT INTO signature_assignments VALUES(?,?,?,?)", ("betahealth", "PROJ-P01-IOT", rejected["id"], service.now()))
    fresh_start(service.w.root, today="2026-09-18")
    assert service.w.jobs(include_deleted=True) == []
    again = service.add_posting({"company": seen["company"], "title": seen["title"], "location": "Austin, TX",
                                 "url": "https://careers.example.test/jobs/new-url", "description": JD})
    assert again["blocked"] and again["rule"] == "same_role"
    other_role = service.add_posting({"company": "Beta Health", "title": "Machine Learning Engineer", "location": "Austin, TX",
                                      "url": "https://careers.beta.test/jobs/2", "description": JD})
    assert other_role["blocked"] and other_role["rule"] == "rejected_180"
    with service.w.connect() as db:
        assert [tuple(r) for r in db.execute("SELECT project_id, job_id FROM signature_assignments")] == [("PROJ-P01-IOT", None)]
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
