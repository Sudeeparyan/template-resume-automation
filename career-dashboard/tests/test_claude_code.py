"""The local Claude Code runtime: CLI discovery, the sealed command, and its
place beside Codex in the gateway, the Settings tab and the specialist team."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

from backend.ai import claude_code, settings as ai_settings  # noqa: E402
from backend.ai.agents import schemas  # noqa: E402
from backend.ai.agents.graph import AgentError, AgentTeam  # noqa: E402
from backend.ai.providers import AIGateway  # noqa: E402
from test_career_workspace import workspace  # noqa: E402,F401
from test_workspace_v2 import service  # noqa: E402,F401

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
          "additionalProperties": False}


def fake_cli(tmp_path, name="claude"):
    path = tmp_path / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def envelope(**overrides):
    data = {"type": "result", "subtype": "success", "is_error": False,
            "structured_output": {"ok": True},
            "usage": {"input_tokens": 12, "output_tokens": 3}, "api_error_status": None}
    data.update(overrides)
    return data


def completed(data, returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=json.dumps(data), stderr="")


# --- finding the CLI ---------------------------------------------------------

def test_env_override_wins_then_path_then_bundled_copies(tmp_path, monkeypatch):
    override = fake_cli(tmp_path, "override")
    monkeypatch.setenv("CLAUDE_CODE_CLI", str(override))
    assert claude_code.find_cli() == override

    monkeypatch.delenv("CLAUDE_CODE_CLI")
    on_path = fake_cli(tmp_path, "claude")
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: str(on_path))
    assert claude_code.find_cli() == on_path


def test_newest_bundled_version_is_chosen(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_CLI", raising=False)
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    old = tmp_path / "ext/anthropic.claude-code-2.1.9-darwin-arm64/resources/native-binary"
    new = tmp_path / "ext/anthropic.claude-code-2.1.273-darwin-arm64/resources/native-binary"
    for folder in (old, new):
        folder.mkdir(parents=True)
        fake_cli(folder)
    monkeypatch.setattr(claude_code, "BUNDLED", (str(tmp_path / "ext/anthropic.claude-code-*/resources/native-binary/claude"),))
    assert claude_code.find_cli() == new / "claude"


def test_not_installed_is_reported_not_raised(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_CLI", raising=False)
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    monkeypatch.setattr(claude_code, "BUNDLED", ())
    assert claude_code.available() is False
    with pytest.raises(ValueError, match="not installed"):
        claude_code.invoke("hi", SCHEMA)


# --- the sealed command ------------------------------------------------------

def test_command_is_one_shot_restricted_and_never_bare(tmp_path):
    cmd = claude_code.command(fake_cli(tmp_path), SCHEMA, model="haiku", web=False, system="Be brief.")
    assert cmd[1] == "-p"
    assert "--restricted" in cmd and "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--allowedTools" not in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == "Be brief."
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA
    # --bare would switch off subscription sign-in and demand an API key.
    assert "--bare" not in cmd
    assert "--dangerously-skip-permissions" not in cmd


def test_web_actions_get_only_search_and_fetch_pre_approved(tmp_path):
    cmd = claude_code.command(fake_cli(tmp_path), SCHEMA, model="sonnet", web=True, system=None)
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"
    at = cmd.index("--allowedTools")
    assert cmd[at + 1:at + 3] == ["WebSearch", "WebFetch"]
    assert "--system-prompt" not in cmd


def test_prompt_goes_on_stdin_and_nested_session_markers_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI", str(fake_cli(tmp_path)))
    monkeypatch.setenv("CLAUDECODE", "1")
    seen = {}

    def run(cmd, **options):
        seen.update(cmd=cmd, **options)
        return completed(envelope())

    monkeypatch.setattr(claude_code.subprocess, "run", run)
    result, usage = claude_code.run("the prompt", SCHEMA, model="haiku")
    assert result == {"ok": True}
    assert usage == {"input_tokens": 12, "output_tokens": 3}
    assert seen["input"] == "the prompt"
    assert "the prompt" not in " ".join(seen["cmd"])
    assert "CLAUDECODE" not in seen["env"]
    assert seen["timeout"] == claude_code.TIMEOUT["text"]
    # UTF-8 both ways, never the Windows ANSI code page (a "→" in a posting would not encode).
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"


def test_failures_become_plain_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_CLI", str(fake_cli(tmp_path)))
    cases = [
        (envelope(is_error=True, subtype="error", api_error_status=429, result="rate limit"), "usage limit"),
        (envelope(is_error=True, subtype="error", result="Not logged in. Please run /login"), "not signed in"),
        (envelope(subtype="error_max_turns", is_error=True, result=""), "stopped before finishing"),
        (envelope(structured_output=None), "did not match"),
    ]
    for data, expected in cases:
        monkeypatch.setattr(claude_code.subprocess, "run", lambda cmd, **o: completed(data))
        with pytest.raises(ValueError, match=expected):
            claude_code.invoke("hi", SCHEMA)

    def timeout(cmd, **options):
        raise subprocess.TimeoutExpired(cmd, options["timeout"])

    monkeypatch.setattr(claude_code.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="time limit"):
        claude_code.invoke("hi", SCHEMA, web=True)

    monkeypatch.setattr(claude_code.subprocess, "run",
                        lambda cmd, **o: subprocess.CompletedProcess([], 1, stdout="", stderr="boom"))
    with pytest.raises(ValueError, match=r"did not return a result \(boom\)"):
        claude_code.invoke("hi", SCHEMA)
    # An unknown failure carries the CLI's own words so it can be acted on.
    odd = envelope(is_error=True, subtype="error_during_execution", result="Structured output validation failed")
    monkeypatch.setattr(claude_code.subprocess, "run", lambda cmd, **o: completed(odd))
    with pytest.raises(ValueError, match=r"could not finish \(error_during_execution\): Structured output validation failed"):
        claude_code.invoke("hi", SCHEMA)
    with pytest.raises(ValueError, match="Unsupported"):
        claude_code.invoke("hi", SCHEMA, model="gpt-9")


def test_a_stub_report_is_a_failure_not_a_result(tmp_path, monkeypatch):
    """The limit was hit mid-call once and the CLI still said success, handing
    back {"summary": "test", "report": "test report"}; that must never be cached."""
    monkeypatch.setenv("CLAUDE_CODE_CLI", str(fake_cli(tmp_path)))
    report_schema = {"type": "object", "properties": {"summary": {"type": "string"}, "report": {"type": "string"}},
                     "required": ["summary", "report"], "additionalProperties": False}
    stub = envelope(structured_output={"summary": "test", "report": "test report"})
    real = envelope(structured_output={"summary": "ok", "report": "## Posting verification\n" + "Detail. " * 40})
    attempts = []
    monkeypatch.setattr(claude_code.subprocess, "run", lambda cmd, **o: attempts.append(1) or completed(stub))
    with pytest.raises(ValueError, match="placeholder"):
        claude_code.invoke("research this", report_schema)
    assert len(attempts) == 2, "one automatic retry, then give up"
    # A stub followed by a real answer is the usual case and succeeds silently.
    answers = iter([completed(stub), completed(real)])
    monkeypatch.setattr(claude_code.subprocess, "run", lambda cmd, **o: next(answers))
    assert claude_code.invoke("research this", report_schema)["summary"] == "ok"
    # Schemas without a report field (a ping, a verdict) are not second-guessed.
    monkeypatch.setattr(claude_code.subprocess, "run", lambda cmd, **o: completed(envelope()))
    assert claude_code.invoke("ping", SCHEMA) == {"ok": True}


# --- beside Codex in the gateway ----------------------------------------------

def test_gateway_offers_claude_code_with_web_but_without_gmail(service, monkeypatch):
    monkeypatch.setattr(claude_code, "available", lambda: True)
    gateway = AIGateway(service, lambda *a, **k: {})
    listed = {p["id"]: p for p in gateway.catalog("discovery")["providers"]}
    assert listed["claude_code"]["compatible"] and listed["claude_code"]["configured"]
    assert listed["codex"]["compatible"]
    mail = {p["id"]: p for p in gateway.catalog("email")["providers"]}
    assert mail["codex"]["compatible"] and not mail["claude_code"]["compatible"]
    with pytest.raises(ValueError, match="missing apps"):
        gateway.resolve("email", "claude_code", "sonnet")


def test_gateway_generate_routes_to_the_cli_with_the_action_web_flag(service, monkeypatch):
    calls = []
    monkeypatch.setattr(claude_code, "available", lambda: True)
    gateway = AIGateway(service, lambda *a, **k: {})
    gateway.providers["claude_code"].invoke = lambda prompt, schema, **o: calls.append((prompt, o)) or {"ok": True}
    # Discovery and research pass no flag and expect the web, as with Codex.
    assert gateway.generate("discovery", "find", SCHEMA, provider="claude_code", model="sonnet") == {"ok": True}
    gateway.generate("resume_chat", "edit", SCHEMA, provider="claude_code", model="sonnet", web=False)
    assert calls == [("find", {"model": "sonnet", "web": True}), ("edit", {"model": "sonnet", "web": False})]


def test_settings_tiers_and_gateway_choices_share_one_key_without_clobbering(service):
    """Saving either side used to wipe the other, then every run failed with
    'Unknown AI provider' because the gateway found no default."""
    ai_settings.save(service, {"tiers": {"strong": {"provider": "codex", "model": "codex-runtime"},
                                         "cheap": {"provider": "codex", "model": "codex-runtime"}}})
    gateway = AIGateway(service, lambda *a, **k: {})
    provider, model = gateway.resolve("discovery")
    assert provider.id == "codex" and model == "codex-runtime"

    gateway.save_preferences({"default": {"provider": "claude_code", "model": "sonnet"}, "actions": {}, "fallback": None})
    assert ai_settings.overview(service)["preferences"]["tiers"]["strong"]["provider"] == "codex"
    assert gateway.preferences()["default"] == {"provider": "claude_code", "model": "sonnet"}


# --- the Settings tab ----------------------------------------------------------

def test_settings_lists_claude_code_and_accepts_it_for_both_tiers(service, monkeypatch):
    monkeypatch.setattr(claude_code, "available", lambda: True)
    listed = {p["id"]: p for p in ai_settings.overview(service)["providers"]}
    assert listed["claude_code"]["configured"] is True
    assert listed["claude_code"]["models"] == ["sonnet", "opus", "haiku"]
    saved = ai_settings.save(service, {"tiers": {"strong": {"provider": "claude_code", "model": "sonnet"},
                                                 "cheap": {"provider": "claude_code", "model": "haiku"}}})
    assert saved["tiers"]["cheap"] == {"provider": "claude_code", "model": "haiku"}
    # A tier saved without a model falls back to the runtime's own default.
    ai_settings.save(service, {"tiers": {"strong": {"provider": "claude_code", "model": "opus"},
                                         "cheap": {"provider": "claude_code", "model": "haiku"}}})
    service.set_pref("ai_preferences", {"tiers": {"cheap": {"provider": "claude_code"}}})
    assert ai_settings.overview(service)["preferences"]["tiers"]["cheap"]["model"] == "haiku"


def test_settings_test_connection_makes_one_real_call(service, monkeypatch):
    monkeypatch.setattr(claude_code, "invoke", lambda prompt, schema, **o: {"word": "ready"})
    assert ai_settings.test_provider(service, "claude_code", "haiku")["ok"] is True

    def failing(prompt, schema, **o):
        raise ValueError("Claude Code is not signed in.")

    monkeypatch.setattr(claude_code, "invoke", failing)
    result = ai_settings.test_provider(service, "claude_code", "haiku")
    assert result["ok"] is False and "not signed in" in result["detail"]


# --- the specialist team -------------------------------------------------------

def test_specialists_run_through_the_cli_and_are_schema_checked(tmp_path, monkeypatch):
    seen = {}
    recorded = []

    def run(prompt, schema, **options):
        seen.update(prompt=prompt, schema=schema, **options)
        return {"company": "Acme", "title": "Data Analyst", "location": "Austin, TX",
                "url": ""}, {"input_tokens": 5, "output_tokens": 2}

    monkeypatch.setattr(claude_code, "run", run)
    team = AgentTeam(tmp_path, {"strong": ("claude_code", "sonnet"), "cheap": ("claude_code", "haiku")},
                     on_usage=recorded.append)
    verdict = team.run("posting_parser", {"posting_text": "Data Analyst at Acme, Austin, TX"})
    assert isinstance(verdict, schemas.PostingFields) and verdict.company == "Acme"
    assert seen["model"] == "haiku" and seen["web"] is False
    assert "Data Analyst" in seen["prompt"] and "Data Analyst" not in seen["system"]
    assert "company" in seen["schema"]["properties"]
    assert recorded[0]["provider"] == "claude_code" and recorded[0]["input_tokens"] == 5

    monkeypatch.setattr(claude_code, "run", lambda *a, **k: ({"company": ["not", "text"]}, {}))
    with pytest.raises(AgentError, match="did not match its schema"):
        team.run("posting_parser", {})

    def down(*a, **k):
        raise ValueError("Your Claude subscription's usage limit is reached.")

    monkeypatch.setattr(claude_code, "run", down)
    with pytest.raises(AgentError, match="usage limit"):
        team.run("posting_parser", {})


def test_the_hiring_manager_stays_isolated_on_the_local_runtime(tmp_path, monkeypatch):
    prompts = []
    monkeypatch.setattr(claude_code, "run", lambda prompt, schema, **o: (prompts.append(prompt) or {
        "expected_skills": [], "expected_experience": [], "convincing_evidence": [],
        "interview_topics": [], "cannot_promise": "no shortlist"}, {}))
    team = AgentTeam(tmp_path, {"strong": ("claude_code", "sonnet"), "cheap": ("claude_code", "haiku")})
    try:
        team.run_isolated("hiring_manager", job_description="JD text", public_research="public")
    except AgentError as error:
        if "did not match" not in str(error):
            raise
    assert set(json.loads(prompts[0])) == {"job_description", "public_company_research"}
    # A re-ask after an answer of the wrong shape adds only the reason, never other data.
    assert all(p.startswith(prompts[0]) and "YOUR PREVIOUS ANSWER WAS REJECTED" in p[len(prompts[0]):] for p in prompts[1:])


# --- starting again from an empty job list ------------------------------------

def test_fresh_start_backs_up_then_clears_jobs_but_keeps_profile_and_settings(service):
    from fresh_start import fresh_start
    from test_career_workspace import add

    job = add(service.w)
    service.w.track_search_job(job["id"])
    service.set_pref("goals", {"weekly_target": 30, "workdays": [0, 1, 2, 3, 4, 5], "start_date": "2026-09-07"})
    knowledge_before = len(service.knowledge())
    folder = service.w.root / "data/output/applications" / job["id"]
    folder.mkdir(parents=True)
    (folder / "resume.tex").write_text("draft")

    result = fresh_start(service.w.root, today="2026-09-12")

    assert result["jobs_now"] == 0 and result["cleared"]["jobs"] == 1
    assert service.w.jobs(include_deleted=True) == []
    assert len(service.knowledge()) == knowledge_before
    assert service.goals()["settings"]["start_date"] == "2026-09-12"
    backup = Path(result["backup"])
    assert backup == service.w.root.parent / "backup/2026-09-12-fresh-start"
    assert (backup / "applications" / job["id"] / "resume.tex").read_text() == "draft"
    assert not (service.w.root / "data/output/applications" / job["id"]).exists()
    copy = sqlite3.connect(backup / "career.db")
    assert copy.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert "jobs: 1" in (backup / "README.md").read_text()
    assert service.w.activity()[0]["action"] == "workspace_reset"
    # A second run the same day gets its own folder rather than overwriting.
    assert Path(fresh_start(service.w.root, today="2026-09-12")["backup"]).name == "2026-09-12-fresh-start-2"
