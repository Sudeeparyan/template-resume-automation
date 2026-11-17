"""A reset erases disposable candidate data and never follows filesystem links."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("reset_template", ROOT / "scripts/reset_template.py")
reset = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reset
spec.loader.exec_module(reset)


def put(root, relative, text="disposable"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    for path in ("AGENTS.md", "career.cmd", "career-dashboard/backend/profiles.py",
                 "me/README.md", "me/about-me.example.md", "my-jobs/README.md",
                 "career-dashboard/backend/countries/us/sponsors-uscis.csv",
                 "career-dashboard/tests/portable/test_example.py",
                 "career-dashboard/frontend/dist/index.html",
                 "career-dashboard/backend/.venv/installed.txt",
                 "backup/keep.txt", ".local-reference/keep.txt",
                 ".env", "career-dashboard/keys.txt", "career-dashboard/.env.example"):
        put(root, path)
    return root


def test_default_cli_is_a_preview(workspace, monkeypatch, capsys):
    private = put(workspace, "career-dashboard/profiles/registry.json", '{"profiles": []}')
    # Default arguments bind ROOT at definition time; redirect only this test's CLI.
    real_plan = reset.plan_reset
    monkeypatch.setattr(reset, "plan_reset", lambda **kw: real_plan(workspace, **kw))
    assert reset.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "preview"
    assert "career-dashboard/profiles" in report["paths"]
    assert private.exists()


def test_erases_all_candidates_and_generated_data_but_keeps_template(workspace):
    private = [
        "career-dashboard/profiles/registry.json",
        "career-dashboard/profiles/example-a/data/career.db",
        "career-dashboard/profiles/example-b/data/context/evidence.yml",
        "career-dashboard/profiles/example-c/data/output/resume.pdf",
        "career-dashboard/profiles/.trash/old/source.docx",
        "career-dashboard/data/context/profile.yml", "career-dashboard/output/resume.pdf",
        "career-dashboard/tests/test_old_person.py", "me/cv.pdf", "me/about-me.md",
        "my-jobs/tracker.csv", "my-jobs/2026-01-01/JOBS.md",
        "daily-job-search/history.csv", "daily-job-search/2026-01-01/MORNING-JOBS.md",
        "daily-job-search/logs/run.log", "daily-job-search/morning-jobs.json",
        ".claude/settings.local.json", ".claude/scheduled-tasks/morning.md",
        ".runtime/temporary/source.txt", "career-dashboard/.pytest_cache/lastfailed",
        "scripts/__pycache__/helper.pyc", "career-dashboard/.ai-plan-health.json",
    ]
    for relative in private:
        put(workspace, relative)
    original_files = {p.relative_to(workspace) for p in workspace.rglob("*") if p.is_file()}
    plan = reset.plan_reset(workspace)
    reset.apply_plan(plan)
    assert all(not (workspace / relative).exists() for relative in private)
    for relative in original_files - {Path(p) for p in private}:
        assert (workspace / relative).is_file(), relative
    assert reset.plan_reset(workspace).targets == ()


def test_secrets_need_the_separate_opt_in_and_are_never_read(workspace, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("The reset must not read any file contents")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    plan = reset.plan_reset(workspace, include_secrets=True)
    reset.apply_plan(plan)
    assert not (workspace / ".env").exists()
    assert not (workspace / "career-dashboard/keys.txt").exists()
    assert (workspace / "career-dashboard/.env.example").exists()
    assert (workspace / "backup/keep.txt").exists()


def test_rejects_wrong_workspace_and_arbitrary_delete_target(workspace, tmp_path):
    with pytest.raises(reset.ResetError, match="template folder"):
        reset.plan_reset(tmp_path)
    with pytest.raises(reset.ResetError, match="changed"):
        reset.apply_plan(reset.ResetPlan(workspace.resolve(), ("AGENTS.md",)))
    assert (workspace / "AGENTS.md").exists()
    with pytest.raises(reset.ResetError, match="relative paths"):
        reset._checked(workspace.resolve(), "../outside")


def test_changed_plan_is_refused_before_any_deletion(workspace):
    first = put(workspace, "me/cv.pdf")
    plan = reset.plan_reset(workspace)
    later = put(workspace, "my-jobs/tracker.csv")
    with pytest.raises(reset.ResetError, match="changed"):
        reset.apply_plan(plan)
    assert first.exists() and later.exists()


def directory_link(link, target):
    if sys.platform == "win32":
        # Junction creation needs no administrator rights and exercises Windows reparse checks.
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if result.returncode:
            pytest.skip("This filesystem cannot create a test junction")
    else:
        link.symlink_to(target, target_is_directory=True)


def test_nested_link_refuses_whole_reset_and_leaves_external_data(workspace, tmp_path):
    outside = tmp_path / "outside"
    kept = put(outside, "keep.txt")
    private = put(workspace, "career-dashboard/profiles/example/data/career.db")
    link = private.parent / "linked"
    directory_link(link, outside)
    first = put(workspace, "me/cv.pdf")
    try:
        plan = reset.plan_reset(workspace)
        with pytest.raises(reset.ResetError, match="link or junction"):
            reset.apply_plan(plan)
        assert kept.exists() and first.exists() and private.exists()
    finally:
        if sys.platform == "win32":
            link.rmdir()
        else:
            link.unlink()


def test_linked_private_root_is_refused(workspace, tmp_path):
    outside = tmp_path / "outside"
    kept = put(outside, "keep.txt")
    link = workspace / "career-dashboard/profiles"
    directory_link(link, outside)
    try:
        with pytest.raises(reset.ResetError, match="link or junction"):
            reset.plan_reset(workspace)
        assert kept.exists()
    finally:
        if sys.platform == "win32":
            link.rmdir()
        else:
            link.unlink()
