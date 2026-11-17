"""Saved public reports are reused only while their actual job artifacts match."""

from types import SimpleNamespace

import pytest

from backend.services.opportunities import digest
from backend.services.pipeline import Pipeline
from test_hunt import ireland_profile, posting


@pytest.mark.parametrize("step,name", [("research", "company-research.md"), ("study_plan", "study-plan.md")])
def test_report_stage_reuses_current_file_and_rebuilds_changed_or_deleted_file(tmp_path, step, name):
    services = ireland_profile(tmp_path)
    job = services.add_posting(posting(), source="discovery")["job"]
    folder = services.w.root / "data/output/applications/sample-job"
    folder.mkdir(parents=True)
    with services.w.connect() as db:
        db.execute("UPDATE jobs SET folder=? WHERE id=?", (str(folder.relative_to(services.w.root)), job["id"]))
    report = folder / name
    calls = []

    def restarted_pipeline():
        pipeline = Pipeline(services, None, SimpleNamespace())

        def write_report(job_id, config):
            calls.append(job_id)
            report.write_text("Sample public report.", encoding="utf-8")
            return {"note": "Report saved"}

        setattr(pipeline, "_" + step, write_report)
        return pipeline

    config = {"provider": "codex", "model": "sample", "free_only": True}
    pipeline = restarted_pipeline()
    pipeline.run_step(step, job["id"], config)
    assert pipeline._artifact(job["id"], step)["report"] == digest("Sample public report.")
    pipeline = restarted_pipeline()
    pipeline.run_step(step, job["id"], config)
    assert calls == [job["id"]]
    report.write_text("Changed after the previous stage.", encoding="utf-8")
    pipeline.run_step(step, job["id"], config)
    assert calls == [job["id"]] * 2
    report.unlink()
    pipeline.run_step(step, job["id"], config)
    assert calls == [job["id"]] * 3 and report.exists()
