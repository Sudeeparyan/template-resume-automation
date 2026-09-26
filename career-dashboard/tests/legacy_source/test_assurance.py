"""Guardrails as review warnings and the pre-apply assurance report.

Unknown or held evidence in Projects/Skills is a warning, because reviewable
predicted content lives there; the same problem in Experience, Education or the
header stays a hard failure. The assurance endpoint merges the validator's
per-claim report with the keep/remove decisions stored on resume_items.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from test_career_workspace import workspace  # noqa: E402,F401 - fixture
from test_tailoring import StubTeam, job, service, tailored_result  # noqa: E402,F401 - fixtures/helpers
from validate_resume import evidence_ids_from_source, scan_claims  # noqa: E402
from backend.dashboard.app import create_app  # noqa: E402
from backend.services import fit  # noqa: E402
from backend.services.resume_studio import ResumeStudio  # noqa: E402
import backend.ai  # noqa: E402

EMPTY_REGISTRY = {"claims": [], "projects": []}

PROJECTS_CLAIM = "% EVIDENCE: PROJ-NOPE-404\n\\newcommand{\\SelectedProjectTitle}{Invented Title}\n"
EXPERIENCE_CLAIM = "\\section{Professional Experience}\n% EVIDENCE: EXP-NOPE-404\n\\item Built data workflows\n"


def test_unresolvable_tags_soften_only_in_projects_and_skills():
    failures, warnings = [], []
    evidence_ids_from_source(PROJECTS_CLAIM + EXPERIENCE_CLAIM, EMPTY_REGISTRY, failures, warnings)
    assert failures == ["Unknown source EVIDENCE ID on line 5: EXP-NOPE-404"]
    assert warnings == ["Unknown source EVIDENCE ID on line 2: PROJ-NOPE-404"]


def test_the_gate_stays_hard_when_no_warnings_sink_is_given():
    failures = []
    evidence_ids_from_source(PROJECTS_CLAIM + EXPERIENCE_CLAIM, EMPTY_REGISTRY, failures)
    assert len(failures) == 2


def test_scan_claims_marks_predicted_and_verified_review_rows():
    source = "% EVIDENCE: resume_items:abc123\n\\newcommand{\\SkillsCloud}{Confluent Cloud}\n"
    (predicted,) = scan_claims(source, EMPTY_REGISTRY, {"abc123": "predicted"})
    assert predicted["status"] == "predicted" and predicted["confidence"] == 60
    assert predicted["section"] == "Technical Skills"
    (missing,) = scan_claims(source, EMPTY_REGISTRY)
    assert missing["status"] == "missing" and missing["confidence"] == 0
    (verified,) = scan_claims(source, EMPTY_REGISTRY, {"abc123": "verified"})
    assert verified["status"] == "verified" and verified["confidence"] == 100


def test_registry_ids_scan_verified(workspace):
    source = "% EVIDENCE: PROJ-P01-IOT\n\\newcommand{\\SelectedProjectTitle}{T}\n"
    (claim,) = scan_claims(source, workspace.evidence())
    assert claim["status"] == "verified" and claim["section"] == "Projects"


def test_report_mode_runs_standalone_on_the_base_template(workspace):
    base = workspace.root / "data/templates/resume-base.tex"
    command = [sys.executable, str(workspace.root / "backend/scripts/validate_resume.py"), str(base), "--report"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=240)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["summary"]["verified"] > 0
    assert report["summary"]["missing"] == 0 and report["summary"]["predicted"] == 0
    claim = report["claims"][0]
    assert {"line", "section", "evidence_ids", "status", "confidence", "note"} <= set(claim)


@pytest.fixture
def client(service, monkeypatch):
    monkeypatch.setattr(ResumeStudio, "fit", lambda self, job_id, revision: self.get(job_id))
    monkeypatch.setattr(ResumeStudio, "score", lambda self, job_id: {"cached": True})
    # The counts below are the plan's own items; the must-have skill top-up has its own tests.
    monkeypatch.setattr(fit, "for_job", lambda services, job_id, **_: None)
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


def test_assurance_without_a_draft_explains_itself(service, job, client):
    response = client.get("/api/v2/assurance/" + job["id"])
    assert response.status_code == 200
    body = response.json()
    assert body["claims"] == [] and "No resume draft yet" in body["note"]
    assert body["summary"] == {"verified": 0, "predicted": 0, "missing": 0, "kept": 0, "removed": 0, "pending": 0}
    assert body["company"] and body["role"]


def test_assurance_tracks_predicted_claims_and_decisions(service, job, client, monkeypatch):
    team = StubTeam(tailored_result(service))
    monkeypatch.setattr(backend.ai, "any_provider_configured", lambda root: True)
    monkeypatch.setattr(backend.ai, "team_for", lambda services, on_usage=None: team)
    tailored = client.post("/api/v2/studio/" + job["id"] + "/tailor")
    assert tailored.status_code == 200

    report = client.get("/api/v2/assurance/" + job["id"]).json()
    assert report["note"] is None
    assert report["summary"]["verified"] > 0
    # Waiting for her: the two suggestions only; the registry items never need a decision.
    assert report["summary"]["predicted"] >= 2 and report["summary"]["pending"] == 2
    predicted = [claim for claim in report["claims"] if claim["evidence_status"] == "predicted"]
    assert len(predicted) >= 2
    assert all(claim["decision"] == "pending" for claim in predicted)
    assert all(claim["origin"] == "predicted" for claim in predicted)
    assert all(claim["items"] and claim["evidence_ids"] for claim in predicted)
    assert {claim["section"] for claim in predicted} <= {"Projects", "Technical Skills"}
    assert all(claim["text"] for claim in predicted), predicted

    items = client.get("/api/v2/studio/" + job["id"] + "/items").json()
    target = next(row for row in items if row["section"] == "skills" and row["origin"] == "predicted")
    kept = client.post(
        "/api/v2/studio/" + job["id"] + "/items/" + target["id"] + "/decision",
        json={"decision": "kept"},
    )
    assert kept.status_code == 200

    after = client.get("/api/v2/assurance/" + job["id"]).json()
    assert after["summary"]["kept"] == 1 and after["summary"]["pending"] == 1
    skill_claim = next(claim for claim in after["claims"] if "Confluent" in claim["text"])
    assert skill_claim["decision"] == "kept"
    assert target["id"] in skill_claim["items"]

    overview = client.get("/api/v2/agents/activity?limit=5").json()
    assert overview["reviews"] == {"pending": 1, "kept": 1, "removed": 0, "tailored_jobs": 1}


def test_a_suggestion_not_on_the_page_still_has_its_keep_and_remove(service, job, client, monkeypatch):
    """Live on 23 Sep: Azure proposed more projects than the page holds; those claims came
    without `items` and the Assurance tab crashed reading items.length."""
    from backend.ai.agents import schemas

    result = tailored_result(service)
    result.projects.append(schemas.TailoredProject(
        title="Care-Team Guideline Retrieval Prototype", context="Python, LangChain",
        bullets=["Prototyped retrieval over care guidelines with LangChain and a local vector index"], origin="predicted"))
    monkeypatch.setattr(backend.ai, "any_provider_configured", lambda root: True)
    monkeypatch.setattr(backend.ai, "team_for", lambda services, on_usage=None: StubTeam(result))
    assert client.post("/api/v2/studio/" + job["id"] + "/tailor").status_code == 200
    report = client.get("/api/v2/assurance/" + job["id"]).json()
    assert all(isinstance(claim["items"], list) for claim in report["claims"])
    extra = next(claim for claim in report["claims"] if claim["text"] == "Care-Team Guideline Retrieval Prototype")
    assert extra["items"] and extra["line"] is None and "not printed" in extra["note"]


def test_claim_text_reads_as_printed():
    """Assurance showed "C\\#" and a trailing "\\\\[1pt]" line break on skills lines (23 Sep)."""
    from validate_resume import normalize_latex_text

    assert normalize_latex_text(r"\textbf{Languages:} Python, SQL, C\#, C++\\[1pt]") == "Languages: Python, SQL, C#, C++"
    assert normalize_latex_text(r"Automated 94\% of tests \& saved \$1k\\") == "Automated 94% of tests & saved $1k"


def test_new_extraction_rules_refresh_a_cached_requirement_list(service, job, monkeypatch):
    """On 23 Sep the v3 list (with the heading "Helpful Though Not Required:") kept being
    reused after v4, because the cache was keyed on the posting text alone."""
    from backend import assessment

    scorer = assessment.AssessmentService(service)
    first = scorer.requirements(job["id"])
    with service.w.connect() as db:
        db.execute("UPDATE job_requirements SET requirement='Stale heading:' WHERE id=(SELECT MIN(id) FROM job_requirements WHERE job_id=?)", (job["id"],))
    assert any(row["requirement"] == "Stale heading:" for row in scorer.requirements(job["id"]))
    monkeypatch.setattr(assessment, "SCORING_VERSION", "career-assessment-next")
    fresh = scorer.requirements(job["id"])
    assert [row["requirement"] for row in fresh] == [row["requirement"] for row in first]
