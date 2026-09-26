"""The 7 AM run is the Daily Search engine with every helper on, and its report is built from the run."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MORNING = ROOT.parent / "daily-job-search" / "morning_run.py"


def load(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("morning_run_under_test", MORNING)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, "argv", ["morning_run.py"])
    spec.loader.exec_module(module)
    # Nothing this test does reaches the real daily folder, the dashboard or the scheduler.
    monkeypatch.setattr(module, "DAILY_DIR", tmp_path)
    monkeypatch.setattr(module, "REPORT_DIR", tmp_path / "report")
    monkeypatch.setattr(module, "LOG_FILE", tmp_path / "run.log")
    monkeypatch.setattr(module, "POLL_SECONDS", 0)
    monkeypatch.setattr(module, "ensure_server", lambda: None)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: None)
    return module


def finished(run_id, source, jobs):
    return {"id": run_id, "state": "completed", "config": {"source": source},
            "progress": {"find": {"note": f"Found {len(jobs)} new job(s)"}, "jobs": jobs}}


def test_the_morning_run_is_the_daily_search_with_every_helper(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path)
    runs = {
        "portals": finished("r1", "portals", [{"id": "j1", "company": "Acme", "title": "Data Engineer", "steps": {
            "research": {"state": "done", "note": "Saved to company-research.md"},
            "tailor": {"state": "done", "note": "7 items from your profile + 2 suggestions to keep or remove in Assurance"},
            "study_plan": {"state": "failed", "error": "No AI could take this step."},
            "pdf": {"state": "done", "note": "One-page PDF ready"}}}]),
        "balanced_five": finished("r2", "balanced_five", [{"id": "j2", "company": "Beta", "title": "ML Engineer", "steps": {
            "research": {"state": "done"}, "tailor": {"state": "done"}, "study_plan": {"state": "done"}, "pdf": {"state": "done"}}}]),
    }
    posted, state = [], {"last": None}

    def api(method, path, body=None, timeout=30):
        if path == "/api/v2/jobs/age":
            return {"ghosted": 0}
        if path == "/api/v2/pipeline/run":
            posted.append(body)
            state["last"] = runs[body["source"]]
            return {"id": state["last"]["id"]}
        if path == "/api/v2/pipeline/status":
            return {"plan": {"remaining_today": 3}, "current": None, "last": state["last"]}
        raise AssertionError("unexpected call " + path)

    monkeypatch.setattr(module, "api", api)
    monkeypatch.setattr(module, "list_jobs", lambda: [
        {"id": "j1", "company": "Acme", "title": "Data Engineer", "url": "https://acme.example/1", "folder": "acme",
         "fit_score": 88, "fit_rationale": "Fit 88/100. Meets 3 of 3 must-haves."},
        {"id": "j2", "company": "Beta", "title": "ML Engineer", "url": "https://beta.example/2", "folder": "",
         "fit_score": 71, "fit_rationale": ""},
    ])
    monkeypatch.setattr(module, "validate_resume", lambda folder: (True, ""))

    assert module.main() == 0
    # Two passes of the same engine: her company list first (also picking up unprepared jobs),
    # then the balanced web mix for the rest of the day's plan; every helper on, research included.
    assert [(b["source"], b["count"], b["include_unprepared"]) for b in posted] == [
        ("portals", 3, True), ("balanced_five", 2, False)]
    assert all(b["steps"] == {"research": True, "tailor": True, "study_plan": True, "pdf": True} for b in posted)
    report = (tmp_path / "report" / "MORNING-REPORT.md").read_text(encoding="utf-8")
    assert "## Acme — Data Engineer" in report and "## Beta — ML Engineer" in report
    assert "Why it fits: Fit 88/100. Meets 3 of 3 must-haves." in report
    assert "Done: Company research, Tailored resume, One-page PDF" in report
    assert "Study plan failed: No AI could take this step." in report
    assert "No application folder was created." in report  # Beta has no folder
    assert "Nothing has been submitted anywhere." in report
    history = (tmp_path / "history.csv").read_text(encoding="utf-8").splitlines()
    assert len(history) == 3 and history[1].split(",")[1] == "Acme"


def test_a_full_day_needs_no_search(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "api", lambda method, path, body=None, timeout=30: (
        {"ghosted": 0} if path == "/api/v2/jobs/age" else {"plan": {"remaining_today": 0}}))
    monkeypatch.setattr(module, "list_jobs", lambda: [])
    assert module.main() == 0
    assert "No new jobs were saved today." in (tmp_path / "report" / "MORNING-REPORT.md").read_text(encoding="utf-8")


def test_a_test_copy_of_the_dashboard_can_be_named(monkeypatch, tmp_path):
    monkeypatch.setenv("CAREER_BASE_URL", "http://127.0.0.1:8011/")
    assert load(monkeypatch, tmp_path).BASE_URL == "http://127.0.0.1:8011"
