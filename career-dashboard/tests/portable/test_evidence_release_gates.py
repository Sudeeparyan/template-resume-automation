"""Release checks never turn a review decision or stale metadata into evidence."""

import pytest
from fastapi.testclient import TestClient

from backend.dashboard.app import create_app
from backend.services import readiness
from validate_resume import evidence_ids_from_source, scan_claims
from test_pipeline_chain import BuiltStudio, saved_job
from test_hunt import ireland_profile


@pytest.mark.parametrize("section", ["Projects", "Skills", "Technical Skills"])
@pytest.mark.parametrize("tag", ["", "% EVIDENCE: UNKNOWN\n", "% EVIDENCE: HELD\n", "% EVIDENCE: MISSING\n"])
def test_unsupported_claims_fail_in_every_resume_section(section, tag):
    evidence = {"claims": [{"id": "HELD", "status": "hold"}, {"id": "MISSING", "status": "missing"}], "projects": []}
    source = "\\section{" + section + "}\n" + tag + "\\item Unverified candidate claim\n"
    failures, warnings = [], []
    evidence_ids_from_source(source, evidence, failures, warnings)
    assert failures and warnings == []


@pytest.mark.parametrize("origin", ["predicted", "verified"])
def test_a_review_row_never_resolves_as_candidate_evidence(origin):
    source = "\\section{Skills}\n% EVIDENCE: resume_items:old-row\n\\item Apache Kafka\n"
    claim = scan_claims(source, {"claims": [], "projects": []}, {"old-row": origin})[0]
    assert claim["status"] == "missing" and claim["confidence"] == 0
    assert "not registered candidate evidence" in claim["problems"][0]


def test_missing_pdf_blocks_readiness_despite_saved_preview_metadata(tmp_path):
    services = ireland_profile(tmp_path)
    job = saved_job(services)
    studio = BuiltStudio(services, job["id"])
    (services.w.root / "data/output/sample-job/preview/resume.pdf").unlink()
    result = readiness.check(services, studio, job["id"])
    pdf = next(check for check in result["checks"] if check["id"] == "pdf")
    assert result["verdict"] == "blocked" and pdf["state"] == "fail"
    assert "missing" in pdf["note"]


@pytest.mark.parametrize("demo", [False, True])
def test_unsupported_resume_claims_block_readiness_even_in_demo(tmp_path, demo):
    services = ireland_profile(tmp_path)
    services.set_pref("demo_mode", demo)
    job = saved_job(services)
    studio = BuiltStudio(services, job["id"])
    studio.resume_evidence_problem = lambda *_: "Remove unsupported candidate claims."
    result = readiness.check(services, studio, job["id"])
    claims = next(check for check in result["checks"] if check["id"] == "claims")
    assert result["verdict"] == "blocked" and claims["state"] == "fail"


def test_generic_file_route_blocks_a_legacy_predicted_resume(tmp_path, monkeypatch):
    from test_resume_evidence_only import studio_fixture, predicted_skill

    services, studio, job, source, _ = studio_fixture(tmp_path, monkeypatch)
    draft = predicted_skill(services, job["id"], source, decision="kept")
    with services.w.connect() as db:
        db.execute("UPDATE jobs SET folder=? WHERE id=?", ("data/output/sample-job", job["id"]))
    folder = services.w.root / "data/output/sample-job"
    artifact = folder / "legacy-preview"
    artifact.mkdir()
    (artifact / "resume.tex").write_text(draft, encoding="utf-8")
    (artifact / "resume.pdf").write_bytes(b"%PDF-1.7 legacy preview")
    app = create_app(services.w.root, schedule=False)
    relative = (artifact / "resume.pdf").relative_to(services.w.root / "data/output").as_posix()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/files/" + relative)
    assert response.status_code == 409
    assert "evidence" in response.json()["detail"].lower()
