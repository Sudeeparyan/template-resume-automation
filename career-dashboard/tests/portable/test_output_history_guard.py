"""Historical files are checked against their saved source, not a repaired current draft."""

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.dashboard.app import create_app
from test_resume_evidence_only import predicted_skill, studio_fixture


def _request(services, artifact):
    app = create_app(services.w.root, schedule=False)
    relative = artifact.relative_to(services.w.root / "data/output").as_posix()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        return client.get("/api/files/" + relative)


def test_legacy_preview_png_checks_ancestor_source_after_current_draft_is_repaired(tmp_path, monkeypatch):
    services, _, job, clean_source, _ = studio_fixture(tmp_path, monkeypatch)
    unsupported = predicted_skill(services, job["id"], clean_source, decision="kept")
    with services.w.connect() as db:
        db.execute("UPDATE jobs SET folder=? WHERE id=?", ("data/output/sample-job", job["id"]))
        db.execute("UPDATE studio_drafts SET source=? WHERE job_id=?", (clean_source, job["id"]))
    folder = services.w.root / "data/output/sample-job"
    (folder / "resume.tex").write_text(unsupported, encoding="utf-8")
    png = folder / "resume-preview/page-01.png"
    png.parent.mkdir()
    png.write_bytes(b"disposable legacy page image")

    response = _request(services, png)
    assert response.status_code == 409
    assert "evidence" in response.json()["detail"].lower()


@pytest.mark.parametrize("filename", ["resume.pdf", "resume-preview/page-01.png"])
@pytest.mark.parametrize("unsupported", [True, False])
def test_historical_outputs_resolve_saved_job_after_latest_folder_changes(tmp_path, monkeypatch, filename, unsupported):
    services, _, job, clean_source, _ = studio_fixture(tmp_path, monkeypatch)
    old_source = predicted_skill(services, job["id"], clean_source, decision="kept") if unsupported else clean_source
    with services.w.connect() as db:
        db.execute("UPDATE jobs SET folder=? WHERE id=?", ("data/output/newer-job", job["id"]))
        db.execute("UPDATE studio_drafts SET source=? WHERE job_id=?", (clean_source, job["id"]))
    folder = services.w.root / "data/output/historical-job"
    folder.mkdir()
    (folder / "resume.tex").write_text(old_source, encoding="utf-8")
    (folder / "evidence-map.yml").write_text(yaml.safe_dump({"job_id": job["id"]}), encoding="utf-8")
    artifact = folder / filename
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_bytes(b"disposable historical artifact")

    response = _request(services, artifact)
    assert response.status_code == (409 if unsupported else 200)
    if unsupported:
        assert "evidence" in response.json()["detail"].lower()
