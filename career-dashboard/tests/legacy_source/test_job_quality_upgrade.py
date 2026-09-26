"""Liveness sweep coverage, the balanced mix, and how gaps are reported."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from test_career_workspace import add, workspace  # noqa: E402,F401
from backend.assessment import assess  # noqa: E402
from backend.job_quality import JobQualityService  # noqa: E402
from backend.services.workspace_v2 import CareerServices  # noqa: E402


def quality(workspace):
    return JobQualityService(CareerServices(workspace))


def mark(workspace, job_id, state, checked_hours_ago):
    stamp = (datetime.now(timezone.utc) - timedelta(hours=checked_hours_ago)).isoformat()
    with workspace.connect() as db:
        db.execute("UPDATE jobs SET posting_state=?, last_verified_at=? WHERE id=?",
                   (state, stamp, job_id))


def due_ids(service, **options):
    """The ids verify_due would check, without making any network request."""
    checked = []
    service.verify_posting = lambda job_id: checked.append(job_id) or {"job_id": job_id, "state": "active"}
    service.verify_due(**options)
    return checked


def test_a_recently_checked_posting_is_not_rechecked(workspace):
    job = add(workspace)
    mark(workspace, job["id"], "active", 1)
    assert due_ids(quality(workspace)) == []


def test_an_active_posting_is_rechecked_after_a_day(workspace):
    job = add(workspace)
    mark(workspace, job["id"], "active", 30)
    assert due_ids(quality(workspace)) == [job["id"]]


def test_an_expired_posting_is_still_reachable_for_recheck(workspace):
    """Workspace.jobs() hides expired roles, so the sweep once could never revisit them."""
    job = add(workspace)
    mark(workspace, job["id"], "expired", 200)
    assert due_ids(quality(workspace)) == [job["id"]]


def test_an_expired_posting_is_not_rechecked_every_day(workspace):
    job = add(workspace)
    mark(workspace, job["id"], "expired", 30)
    assert due_ids(quality(workspace)) == []


def test_a_posting_never_checked_is_due(workspace):
    job = add(workspace)
    assert due_ids(quality(workspace)) == [job["id"]]


def candidate(name, size, tier="B", applicants=1, eligible=True, legit="verified"):
    """A discovery candidate after the gate: `tier` is S/A/B/C (never EXCLUDED here; those are gone)."""
    return {
        "company": name, "title": "Data Engineer", "url": f"https://example.test/{name}",
        "size_category": size, "sponsor_tier": tier,
        "applicant_count": applicants, "legitimacy_state": legit,
        "relevance": {"eligible": eligible},
    }


def test_the_balanced_mix_fills_two_startups_one_mid_and_two_large(workspace):
    chosen = quality(workspace).balanced_five([
        candidate("s1", "startup"), candidate("s2", "startup"), candidate("s3", "startup"),
        candidate("m1", "mid"), candidate("l1", "large"), candidate("l2", "large"),
    ])
    sizes = [job["size_category"] for job in chosen["jobs"]]
    assert sizes.count("startup") == 2
    assert sizes.count("mid") == 1
    assert sizes.count("large") == 2
    assert chosen["complete"] is True


def test_a_large_company_needs_a_sponsorship_tier_to_fill_its_slot(workspace):
    """Mid and large slots want S (cap-exempt), A (says yes) or B (proven sponsor); silent C is not enough there."""
    chosen = quality(workspace).balanced_five([candidate("l1", "large", tier="C")])
    assert chosen["jobs"] == []
    assert any(item["category"] == "large" for item in chosen["shortages"])
    for tier in ("S", "A", "B"):
        assert [j["company"] for j in quality(workspace).balanced_five([candidate("l1", "large", tier=tier)])["jobs"]] == ["l1"]


def test_a_startup_may_fill_its_slot_while_silent(workspace):
    chosen = quality(workspace).balanced_five([candidate("s1", "startup", tier="C")])
    assert [job["company"] for job in chosen["jobs"]] == ["s1"]


def test_a_crowded_startup_posting_is_not_treated_as_easy_to_enter(workspace):
    chosen = quality(workspace).balanced_five([candidate("s1", "startup", applicants=400)])
    assert chosen["jobs"] == []


def test_the_shortfall_is_reported_rather_than_padded(workspace):
    """An honest short list beats five results padded with weaker matches."""
    chosen = quality(workspace).balanced_five([candidate("m1", "mid")])
    assert [job["company"] for job in chosen["jobs"]] == ["m1"]
    assert chosen["complete"] is False
    missing = {item["category"]: item for item in chosen["shortages"]}
    assert missing["startup"]["needed"] == 2 and missing["startup"]["found"] == 0
    assert missing["large"]["needed"] == 2


def test_an_unverified_employer_never_reaches_the_mix(workspace):
    chosen = quality(workspace).balanced_five([
        candidate("s1", "startup", legit="needs_review"),
        candidate("s2", "startup", legit="blocked"),
    ])
    assert chosen["jobs"] == []


RESUME = """Chetan Babu M
Professional Experience
Built Kafka and Flink streaming pipelines and wrote SQL for clinical telemetry.
Education
Master of Science in Computer Engineering
"""

JD = "We need Kafka and SQL. Experience with Kubernetes is required. Python is preferred."


def test_gaps_separate_what_is_supported_from_what_is_invented():
    """One combined missing-keyword list is what made this section unreadable."""
    result = assess(RESUME, JD, profile_entries=[
        {"id": "SKILL-PY", "title": "Python", "summary": "Python for analysis coursework."},
    ])
    supported = result["missing_supported"]
    unsupported = result["missing_unsupported"]
    assert "Python" in " ".join(supported), supported
    assert not any("Python" in item for item in unsupported)
    assert supported or unsupported
    # Every gap belongs to exactly one of the two lists.
    assert len(supported) + len(unsupported) == len(result["missing_keywords"])


def test_fit_evidence_names_only_entries_that_mention_a_requirement():
    """The old expression returned the first twenty entries whatever they said."""
    result = assess(RESUME, JD, profile_entries=[
        {"id": "SKILL-PY", "title": "Python", "summary": "Python for analysis."},
        {"id": "HOBBY-1", "title": "Hillwalking", "summary": "Weekend hillwalking in Kerry."},
    ])
    assert "HOBBY-1" not in result["opportunity_fit"]["evidence_ids"]
