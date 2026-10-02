"""Completed independent reviews must pass their findings before a resume is ready."""

import hashlib
import json
import uuid

import pytest

from backend.services import readiness
from test_hunt import ireland_profile
from test_pipeline_chain import BuiltStudio, reviewed, saved_job


@pytest.mark.parametrize("review, state", [
    ({"verdict": "pass", "issues": [], "summary": "No document issues."}, "pass"),
    ({"verdict": "review", "issues": ["Check the qualification wording."]}, "warn"),
    ({"verdict": "blocked", "issues": ["The PDF is missing the contact information."]}, "fail"),
    ({"verdict": "pass", "issues": ["The dates need review."]}, "warn"),
    ({"summary": "Legacy prose-only report."}, "warn"),
    ({"verdict": "pass", "issues": "not a list"}, "warn"),
    ({"verdict": "pass", "issues": [None]}, "warn"),
    ({"verdict": "pass", "issues": [" "]}, "warn"),
    ({"verdict": "review", "issues": []}, "warn"),
    ({"verdict": [], "issues": []}, "warn"),
    ([], "warn"),
])
def test_exact_pdf_review_findings_control_readiness(tmp_path, review, state):
    services = ireland_profile(tmp_path)
    job = saved_job(services)
    studio = BuiltStudio(services, job["id"])
    # The new review must supersede an earlier pass, even within the same timestamp.
    reviewed(services, job, studio.pdf_sha256)
    result = {"pdf_sha256": studio.pdf_sha256,
              "jd_sha256": hashlib.sha256(job["description"].encode()).hexdigest(), "review": review}
    with services.w.connect() as db:
        db.execute("INSERT INTO agent_runs(id,kind,job_id,state,input,result,created_at,updated_at) "
                   "VALUES(?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, "resume_match", job["id"], "completed",
                                              "{}", json.dumps(result), services.now(), services.now()))

    checked = readiness.check(services, studio, job["id"])
    review_check = next(item for item in checked["checks"] if item["id"] == "review")
    assert review_check["state"] == state
    if state != "pass":
        assert checked["verdict"] != "ready"
    if state == "fail":
        assert checked["verdict"] == "blocked"
        assert "contact information" in review_check["note"]
