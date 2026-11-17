"""JobPrepGraph (backend/graphs/job_prep.py) runs the Daily Search helpers exactly as the classic
loop does, and resumes a stopped job after its last finished helper."""

from __future__ import annotations

import pytest

from backend.graphs import checkpoint, job_prep
from backend.services.pipeline import STEP_IDS, Pipeline
from test_hunt import ireland_profile, posting
from test_pipeline_chain import OpenOnly, ScriptedRunner, chain


def comparable(run):
    """Each job's helpers without timings: what the page shows."""
    return [(job["company"], {step: {k: v for k, v in entry.items() if k not in {"seconds", "started_epoch"}}
                              for step, entry in job["steps"].items()})
            for job in run["progress"]["jobs"]]


@pytest.mark.parametrize("closed", [(), ("11",)])
def test_the_graph_and_the_classic_loop_give_the_same_progress(tmp_path, monkeypatch, closed):
    classic, _, classic_studio, classic_ran = chain(tmp_path / "classic", monkeypatch,
                                                    [["Acme Analytics", "Birch Data", "Cedar Insights"]], closed=closed)
    monkeypatch.setenv("CAREER_FEATURES", "+graph_pipeline")
    graphed, _, graph_studio, graph_ran = chain(tmp_path / "graph", monkeypatch,
                                                [["Acme Analytics", "Birch Data", "Cedar Insights"]], closed=closed)
    assert classic["state"] == graphed["state"] == "completed", (classic["error"], graphed["error"])
    assert comparable(graphed) == comparable(classic)
    assert graph_ran == classic_ran and len(graph_studio.opened) == len(classic_studio.opened)
    threads = checkpoint.threads(tmp_path / "graph" / "profile")
    assert len([t for t in threads if t.startswith("job_prep:")]) == 3


def test_a_job_stopped_mid_way_resumes_after_its_last_finished_helper(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    pipeline = Pipeline(services, ScriptedRunner(services, []), OpenOnly(), poll_seconds=0.01)
    job = services.add_posting(posting(1), source="discovery")["job"]  # advertised pay: nothing stops its preparation
    entry = {"id": job["id"], "company": job["company"], "title": job["title"],
             "steps": {step: {"state": "waiting"} for step in STEP_IDS}}
    progress = {"stage": "", "jobs": [entry]}
    ran, crash = [], {"at": "tailor"}

    def helper(step, job_id, config):
        if step == crash["at"]:
            crash["at"] = None
            raise KeyboardInterrupt("the app was closed")  # not caught by the helper loop, like a shutdown
        ran.append(step)
        return {"note": step}

    monkeypatch.setattr(pipeline, "run_step", helper)
    monkeypatch.setattr(pipeline, "_save", lambda *args, **kwargs: None)
    with pytest.raises(KeyboardInterrupt):
        job_prep.run(pipeline, "run-1", {"steps": {}}, progress, entry, STEP_IDS, 1, 1)
    assert ran == ["posting", "research"]
    job_prep.run(pipeline, "run-1", {"steps": {}}, progress, entry, STEP_IDS, 1, 1)
    assert ran == STEP_IDS  # posting and research did not run again
    assert all(entry["steps"][step]["state"] == "done" for step in STEP_IDS)
    # A finished job's graph is never run twice.
    job_prep.run(pipeline, "run-1", {"steps": {}}, progress, entry, STEP_IDS, 1, 1)
    assert ran == STEP_IDS
    checkpoint.close(services.w.root)
