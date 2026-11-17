"""Independent review outcomes are explicit and restricted to the supplied PDF/JD."""

import json
import uuid
from types import SimpleNamespace

import pytest

from backend.services.agents import RESUME_REVIEW_SCHEMA
from backend.services.pipeline import Pipeline
from test_agent_lifecycle import record, worker


def review_run(services):
    run_id = uuid.uuid4().hex
    payload = {"revision": 1, "source_sha256": "source-v1", "pdf_sha256": "pdf-v1", "jd_sha256": "posting-v1",
               "resume_text": "Sample candidate lists Python and reporting.", "job_description": "SQL is essential."}
    with services.w.connect() as db:
        db.execute("INSERT INTO agent_runs(id,kind,state,input,created_at,updated_at,provider,model,preset) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (run_id, "resume_match", "queued", json.dumps(payload),
                                                  services.now(), services.now(), "codex", "sample", "default"))
    return run_id


@pytest.mark.parametrize("verdict,issues,expected", [
    ("pass", [], "pass"),
    ("pass", ["SQL evidence needs clarification."], "review"),
    ("review", ["The posting does not explain the required SQL depth."], "review"),
    ("blocked", ["The PDF provides no evidence for essential SQL."], "blocked"),
])
def test_agent_records_the_independent_outcome_and_exact_artifact_hashes(worker, monkeypatch, verdict, issues, expected):
    services, runner, _ = worker
    run_id = review_run(services)
    calls = []

    def review(prompt, schema, **options):
        calls.append((prompt, schema, options))
        return {"summary": "Checked the supplied documents.", "report": "Document evidence reviewed.",
                "sources": [], "limitations": [], "verdict": verdict, "issues": issues}

    monkeypatch.setattr(runner, "cached", review)
    runner.run(run_id)
    saved = record(services, run_id)
    assert saved["state"] == "completed", saved["error"]
    result = json.loads(saved["result"])
    assert result["review"]["verdict"] == expected and result["review"]["issues"] == issues
    assert result["pdf_sha256"] == "pdf-v1" and result["jd_sha256"] == "posting-v1"
    assert result["profile_access"] is False
    prompt, schema, options = calls[0]
    assert schema == RESUME_REVIEW_SCHEMA and options["web"] is False
    assert set(json.loads(prompt.rsplit("\n", 1)[1])) == {"resume_text", "job_description"}


@pytest.mark.parametrize("extra", [{}, {"verdict": "approved", "issues": []},
                                    {"verdict": "pass", "issues": "none"}, {"verdict": "blocked", "issues": []},
                                    {"verdict": {"pass": True}, "issues": []}])
def test_incomplete_or_invalid_review_output_is_not_marked_completed(worker, monkeypatch, extra):
    services, runner, timers = worker
    run_id = review_run(services)
    monkeypatch.setattr(runner, "cached", lambda *args, **kwargs:
                        {"summary": "Checked", "report": "Notes", "sources": [], "limitations": [], **extra})
    runner.run(run_id)
    assert record(services, run_id)["state"] == "failed"
    assert timers == []


@pytest.mark.parametrize("review", [None, [], "legacy", {}, {"verdict": ["pass"], "issues": []},
                                    {"verdict": {"pass": True}, "issues": []},
                                    {"verdict": "pass", "issues": "none"}, {"verdict": "blocked", "issues": []}])
def test_pipeline_treats_malformed_legacy_review_as_needing_attention(review):
    pipeline = Pipeline.__new__(Pipeline)
    pipeline.studio = SimpleNamespace(get=lambda job_id:
                                     {"revision": 1, "preview": {"current": True, "revision": 1}})
    pipeline._agent = lambda *args, **kwargs: {"result": {"review": review}}
    result = pipeline._review("sample-job", {"provider": "codex", "model": "sample"})
    assert result["review_verdict"] == "review"
    assert "needs your attention" in result["note"] and "new independent review" in result["issues"][0]
