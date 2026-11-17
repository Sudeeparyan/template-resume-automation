"""The AI-app layer: the shared skills every AI app reads, `career setup` and `--jobs N`.

The skills are instructions, so these tests keep them honest: every skill is well formed and
reachable from AGENTS.md, Claude's pointers match the shared copy, every `career` command a
skill names exists, and nothing personal is in them.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from test_autopilot import PROFILE, FakeApp, at, context, run, stamp
from test_build_pipeline import ExtractorFixture

import autopilot
import backend.services.intake.api as intake_api
from backend.pdf_compiler import tectonic_executable
from backend.profiles import ProfileStore

APP = Path(__file__).resolve().parents[2]
REPO = APP.parent
SKILLS = REPO / ".agents/skills"
SCRIPTS = APP / "backend/scripts"


@pytest.fixture(autouse=True)
def no_scheduled_tasks(monkeypatch):
    monkeypatch.setenv("CAREER_NO_SCHEDULED_TASKS", "1")


def front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    assert match, f"{path} has no front matter"
    return yaml.safe_load(match.group(1))


def shared_skills() -> list[Path]:
    return sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())


def test_actionable_skill_commands_always_identify_the_selected_profile():
    """A pasted instruction must not read or mutate the last-opened person's data."""
    pattern = re.compile(r"career\s+(?:ws|add|prepare|update|jobs|status|setup)\b[^`\n]*")
    for skill in shared_skills():
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        for command in pattern.findall(text):
            # Plain command names in prose are not runnable examples; initial
            # setup intentionally creates a new profile rather than selecting one.
            if "--" not in command or command.startswith("career setup --name"):
                continue
            assert "--profile" in command, f"{skill.name}: {command} relies on the last-opened profile"


def test_every_skill_is_well_formed_and_claude_lists_the_same_one():
    skills = shared_skills()
    assert {p.name for p in skills} >= {"career-setup", "find-jobs", "tailor-resume", "morning-jobs",
                                        "track-applications", "profile-intake", "interview-prep",
                                        "verify-job-url", "ireland-job-sources"}
    assert "us-job-sources" not in {p.name for p in skills}, "the US market is switched off (countries/markets.yml)"
    for skill in skills:
        meta = front_matter(skill / "SKILL.md")
        assert meta["name"] == skill.name
        assert 40 <= len(meta["description"]) <= 1024, skill.name
        codex = yaml.safe_load((skill / "agents/openai.yaml").read_text(encoding="utf-8"))["interface"]
        assert codex["display_name"] and codex["short_description"] and f"${skill.name}" in codex["default_prompt"]
        pointer = REPO / ".claude/skills" / skill.name / "SKILL.md"
        assert front_matter(pointer) == meta, f"{pointer} differs from the shared skill"
        assert f".agents/skills/{skill.name}/SKILL.md" in pointer.read_text(encoding="utf-8")
    assert {p.name for p in (REPO / ".claude/skills").iterdir()} == {p.name for p in skills}


def test_agents_md_routes_to_every_skill_and_every_file_a_skill_names_exists():
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert "@AGENTS.md" in (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    for skill in shared_skills():
        assert f".agents/skills/{skill.name}/SKILL.md" in agents, f"AGENTS.md never routes to {skill.name}"
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        for reference in re.findall(r"`(references/[\w.-]+)`", text):
            assert (skill / reference).is_file(), f"{skill.name} names a missing {reference}"
        for path in re.findall(r"`((?:career-dashboard|daily-job-search|me|my-jobs)/[\w./-]+\.(?:md|py|yml|csv))`", text):
            personal = path.startswith(("me/", "my-jobs/")) and path not in {"me/README.md", "me/about-me.example.md", "my-jobs/README.md"}
            if "<" not in path and not personal and "MORNING-JOBS" not in path:
                assert (REPO / path).exists(), f"{skill.name} names a missing {path}"


def _choices(script: str) -> set[str]:
    """The sub-commands a CLI script accepts, read from its own --help."""
    result = subprocess.run([sys.executable, str(SCRIPTS / script), "--help"], capture_output=True, text=True,
                            encoding="utf-8", cwd=APP, check=True)
    return set(re.search(r"positional arguments:\s*\{([\w,-]+)\}", result.stdout).group(1).split(","))


def test_every_career_command_a_skill_or_guide_names_exists():
    cli = importlib.util.spec_from_file_location("career_cli", SCRIPTS / "career_cli.py")
    module = importlib.util.module_from_spec(cli)
    cli.loader.exec_module(module)
    top = set(module.TARGETS) | _choices("career.py")
    workspace = _choices("workspace.py")
    kinds = set(re.search(r'"--kind", choices=\[([^\]]+)\]', (SCRIPTS / "workspace.py").read_text(encoding="utf-8"))
                .group(1).replace('"', "").replace(" ", "").split(","))
    texts = [p.read_text(encoding="utf-8") for p in SKILLS.rglob("*.md")]
    texts += [(REPO / name).read_text(encoding="utf-8") for name in ("AGENTS.md", "START-HERE.md", "README.md", "docs/DEVELOPERS.md")]
    named = 0
    for text in texts:
        # Commands are written as code; prose such as "employer career feeds" is not one.
        text = "\n".join(re.findall(r"```.*?```|`[^`\n]+`", text, re.S))
        for sub, command in re.findall(r"\bcareer (ws )?([a-z][a-z-]+)", text):
            named += 1
            assert command in (workspace if sub else top), f"`career {sub}{command}` does not exist"
        for kind in re.findall(r"--kind ([a-z_]+)", text):
            assert kind in kinds, f"--kind {kind} does not exist"
    assert named > 10


def test_the_shared_layer_passes_the_privacy_scan():
    spec = importlib.util.spec_from_file_location("scan_release", REPO / "scripts/scan_release.py")
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    files = [p for p in (REPO / ".agents").rglob("*") if p.is_file()]
    files += [p for p in (REPO / ".claude/skills").rglob("*") if p.is_file()]
    files += [REPO / name for name in ("AGENTS.md", "CLAUDE.md", "START-HERE.md", "me/README.md",
                                       "me/about-me.example.md", "my-jobs/README.md", "docs/DEVELOPERS.md")]
    for path in files:
        assert scan.file_errors(path.relative_to(REPO).as_posix()) == []
    # A person's own files in me/ and my-jobs/ are private; only the guides are shared.
    assert scan.private_path("me/resume.pdf") and scan.private_path("my-jobs/tracker.csv")
    scratch = "career-dashboard/.test-source-audit/example/data/config/profile.yml"
    assert scan.private_path(scratch)
    assert scan.file_errors(scratch) == [f"{scratch}: private path is staged or otherwise publishable"]
    assert scan.ignore_errors() == []
    assert not scan.private_path("me/about-me.example.md") and not scan.private_path(".agents/skills/find-jobs/SKILL.md")


# ----- career setup ---------------------------------------------------------------------------

def _setup(argv):
    spec = importlib.util.spec_from_file_location("setup_profile", SCRIPTS / "setup_profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(argv)


def _me(tmp_path) -> Path:
    me = tmp_path / "me"
    me.mkdir()
    (me / "README.md").write_text("guide", encoding="utf-8")
    (me / "about-me.example.md").write_text("example", encoding="utf-8")
    (me / "about-me.md").write_text("- Full name: Example Person\n- Country to search: Ireland\n", encoding="utf-8")
    (me / "resume.md").write_text("Example Person\nCustomer Support Specialist, Example Services, Dublin, "
                                  "January 2023 - Present. Used Zendesk.\n", encoding="utf-8")
    return me


def test_setup_creates_a_profile_from_the_me_folder_and_skips_unchanged_files(tmp_path, capsys):
    me = _me(tmp_path)
    profiles = tmp_path / "profiles"
    assert _setup(["--from", str(me), "--no-build", "--profiles-dir", str(profiles)]) == 0
    first = json.loads(capsys.readouterr().out)
    # The name comes from about-me.md; the guides are never evidence.
    assert first["profile"] == "example-person" and sorted(first["added"]) == ["about-me.md", "resume.md"]
    store = ProfileStore(base=profiles, legacy_root=tmp_path / "no-legacy")
    assert store.get("example-person")["state"] == "onboarding"

    (me / "notes.md").write_text("Finished a Zendesk administration course in 2024.", encoding="utf-8")
    assert _setup(["--from", str(me), "--profile", "example-person", "--no-build", "--profiles-dir", str(profiles)]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["added"] == ["notes.md"] and sorted(second["unchanged"]) == ["about-me.md", "resume.md"]


def test_setup_refuses_an_empty_folder_and_a_bad_market(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit, match="No resume or notes"):
        _setup(["--from", str(empty), "--name", "Example Person", "--profiles-dir", str(tmp_path / "p")])
    with pytest.raises(SystemExit):
        _setup(["--from", str(_me(tmp_path)), "--market", "mars", "--profiles-dir", str(tmp_path / "p")])


@pytest.mark.skipif(not tectonic_executable(), reason="Tectonic is required for the full PDF build")
def test_setup_builds_a_ready_profile_with_its_market_and_authorization(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(intake_api, "team_factory", lambda _profiles: lambda _on_usage: ExtractorFixture())
    authorization = {"ie": {"status": "needs_sponsorship", "citizenship": "noncitizen", "needs_sponsorship_later": "yes"}}
    status = _setup(["--from", str(_me(tmp_path)), "--market", "ie", "--work-auth", json.dumps(authorization),
                     "--profiles-dir", str(tmp_path / "profiles"), "--wait-minutes", "3"])
    result = json.loads(capsys.readouterr().out)
    assert status == 0, result
    assert result["build"]["status"] == "completed" and result["state"] == "ready"
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    profile = store.get("example-person")
    assert profile["target_markets"] == ["ie"]
    assert profile["work_authorization_by_market"]["ie"]["status"] == "needs_sponsorship"

    # `career ws tailor` on a saved job: with no AI ready, registered evidence is ranked and fitted.
    import backend.ai
    import backend.profiles
    import workspace
    from backend.services.workspace_v2 import CareerServices
    from career import Workspace

    saved = CareerServices(Workspace(store.root_for("example-person"))).add_posting({
        "company": "Example Services", "title": "Customer Support Specialist", "location": "Dublin, Ireland",
        "url": "https://example.com/jobs/support-1",
        "description": "Customer Support Specialist in Dublin. Answer customer requests by email and phone, "
                       "track tickets in Zendesk and follow up until each request is resolved."}, source="cli")
    job_id = saved.get("id") or saved.get("job", {}).get("id")
    assert job_id, saved
    monkeypatch.setattr(backend.ai, "any_provider_configured", lambda _root: False)
    monkeypatch.setattr(backend.profiles, "store", lambda: store)
    monkeypatch.setattr(sys, "argv", ["workspace.py", "tailor", "--job-id", job_id, "--profile", "example-person"])
    workspace.main()
    tailored = json.loads(capsys.readouterr().out)
    assert tailored["tailored_by_ai"] is False and tailored["pages"] >= 1
    assert Path(tailored["pdf"]).is_file() and Path(tailored["pdf"]).is_relative_to(tmp_path)


# ----- --jobs N: jobs now -----------------------------------------------------------------------

def test_jobs_now_search_even_after_this_mornings_hunt_and_ask_the_hunt_for_that_many():
    finished = {"state": "completed", "created_at": stamp(at(1, 30))}
    assert autopilot.plan(at(9, 30), "09:00", finished, hours=1.5)["action"] == "done"  # plain --hours: already done
    now = autopilot.plan(at(9, 30), "09:00", finished, hours=1.5, on_demand=True)
    assert now == {"action": "start", "hours": 1.5, "why": "the jobs you asked for now"}

    app = FakeApp({
        "GET /hunt/status": [{"current": None, "last": finished},
                             {"current": None, "last": run("h2", "completed", created_at=stamp(at(9, 30)))}],
        "POST /hunt/run": [run("h2")],
    })
    ctx = context(app, now=at(9, 30), hours=autopilot.ON_DEMAND_HOURS)
    ctx.jobs = 5
    autopilot.hunt_profile(ctx, PROFILE, share=1)
    assert [c for c in app.calls if c[0] == "POST /hunt/run"] == [("POST /hunt/run", {"hours": 1.5, "target": 5})]


def test_jobs_now_is_validated():
    assert autopilot.parse(["--jobs", "5"]).jobs == 5
    for bad in ("0", "41"):
        with pytest.raises(SystemExit):
            autopilot.parse(["--jobs", bad])
