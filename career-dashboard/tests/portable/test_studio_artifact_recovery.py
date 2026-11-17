"""A damaged saved preview must not trap a candidate outside the draft editor."""

import hashlib
import json

import pytest

from test_resume_evidence_only import studio_fixture


@pytest.mark.parametrize("metadata", [
    "{", "null", "[]", "{}",
    json.dumps({"revision": 1, "path": "sample-job/studio/preview-1"}),
    json.dumps({"revision": 1, "page_count": 2, "path": 42, "source_sha256": "CURRENT_SOURCE_HASH"}),
    json.dumps({"revision": 1, "page_count": 2, "path": "../../outside", "source_sha256": "CURRENT_SOURCE_HASH"}),
    json.dumps({"revision": 1, "path": "sample-job/studio/preview-1",
                "page_count": 2, "source_sha256": "CURRENT_SOURCE_HASH", "layout": ["full_pages"]}),
])
def test_damaged_preview_can_be_edited_but_cannot_be_exported(tmp_path, monkeypatch, metadata):
    services, studio, job, source, _ = studio_fixture(tmp_path, monkeypatch)
    folder = services.w.root / "data/output/sample-job/studio"
    metadata = metadata.replace("CURRENT_SOURCE_HASH", hashlib.sha256(source.encode()).hexdigest())
    (folder / "preview.json").write_text(metadata, encoding="utf-8")

    draft = studio.get(job["id"])
    assert draft["source"] == source and draft["preview"] is None
    assert any("preview metadata" in warning.lower() for warning in draft["warnings"])
    assert services.documents() == []
    edited = source + "% Layout reviewed\n"
    saved = studio.save(job["id"], 1, source=edited)
    assert saved["revision"] == 2 and saved["source"] == edited
    with pytest.raises(ValueError, match="current successfully compiled revision"):
        studio.download(job["id"], "pdf")


def test_a_directory_at_the_pdf_path_is_not_a_current_artifact(tmp_path, monkeypatch):
    services, studio, job, source, _ = studio_fixture(tmp_path, monkeypatch)
    folder = services.w.root / "data/output/sample-job/studio"
    (folder / "preview-1/resume.pdf").mkdir(parents=True)
    (folder / "preview.json").write_text(json.dumps({
        "revision": 1, "page_count": 2, "path": "sample-job/studio/preview-1",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
    }), encoding="utf-8")
    assert studio.get(job["id"])["preview"]["current"] is False
    assert services.documents() == []
    with pytest.raises(ValueError, match="current successfully compiled revision"):
        studio.download(job["id"], "pdf")
