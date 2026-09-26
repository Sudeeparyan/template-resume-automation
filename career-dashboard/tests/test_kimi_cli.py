"""The local Kimi Code runtime: CLI discovery, the one-shot command, JSONL
parsing, and its place beside Claude Code and Codex in the gateway, the
Settings tab and the specialist team."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

from backend.ai import kimi_cli, settings as ai_settings  # noqa: E402
from backend.ai.agents import schemas  # noqa: E402
from backend.ai.agents.graph import AgentError, AgentTeam  # noqa: E402
from backend.ai.providers import AIGateway  # noqa: E402
from test_career_workspace import workspace  # noqa: E402,F401
from test_workspace_v2 import service  # noqa: E402,F401

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
          "additionalProperties": False}


def fake_cli(tmp_path, name="kimi"):
    path = tmp_path / name
    path.write_text("#!/bin/sh\n")
    return path


def stream(*events) -> str:
    return "\n".join(json.dumps(event) for event in events)


def answering(text):
    return stream({"role": "meta", "type": "system.version", "version": "2.0.2"},
                  {"role": "assistant", "content": text},
                  {"role": "meta", "type": "session.resume_hint", "session_id": "s1"})


def fake_run(out="", err="", code=0, before=None):
    """A subprocess.run stand-in: the stream-json goes to the stdout FILE the
    provider hands over, the way the real CLI writes it."""
    def run(cmd, **options):
        if before:
            before(cmd, **options)
        options["stdout"].write(out)
        options["stdout"].flush()
        return subprocess.CompletedProcess(cmd, code, stdout=None, stderr=err)
    return run


# --- finding the CLI ---------------------------------------------------------

def test_env_override_wins_then_path_then_bundled_copy(tmp_path, monkeypatch):
    override = fake_cli(tmp_path, "override")
    monkeypatch.setenv("KIMI_CLI", str(override))
    assert kimi_cli.find_cli() == override

    monkeypatch.delenv("KIMI_CLI")
    on_path = fake_cli(tmp_path)
    monkeypatch.setattr(kimi_cli.shutil, "which", lambda name: str(on_path))
    assert kimi_cli.find_cli() == on_path

    monkeypatch.setattr(kimi_cli.shutil, "which", lambda name: None)
    bundled = fake_cli(tmp_path, "kimi.exe")
    monkeypatch.setattr(kimi_cli, "BUNDLED", (str(bundled),))
    assert kimi_cli.find_cli() == bundled


def test_not_installed_is_reported_not_raised(monkeypatch):
    monkeypatch.delenv("KIMI_CLI", raising=False)
    monkeypatch.setattr(kimi_cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(kimi_cli, "BUNDLED", ())
    assert kimi_cli.available() is False
    with pytest.raises(ValueError, match="not installed"):
        kimi_cli.invoke("hi", SCHEMA)


def test_signed_in_checks_the_credentials_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    assert kimi_cli.signed_in() is False
    folder = tmp_path / "credentials"
    folder.mkdir()
    (folder / "kimi-code.json").write_text("{}")
    assert kimi_cli.signed_in() is True


# --- the one-shot command ------------------------------------------------------

def test_command_is_one_shot_print_mode(tmp_path):
    cmd = kimi_cli.command(fake_cli(tmp_path), "the prompt")
    assert cmd[1] == "-p" and cmd[2] == "the prompt"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    # --yolo/--auto are rejected in combination with -p and must never appear.
    assert "--yolo" not in cmd and "--auto" not in cmd


def test_payload_orders_system_task_then_schema():
    text = kimi_cli.payload("the task", SCHEMA, "Be brief.")
    assert text.index("Be brief.") < text.index("the task") < text.index("JSON Schema")
    assert json.loads(text.split("JSON Schema. No markdown fences, no commentary, nothing before or after the object:\n")[1]) == SCHEMA
    assert kimi_cli.payload("the task", SCHEMA, None).startswith("the task")


def test_short_prompt_goes_on_the_command_line_and_stdout_is_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CLI", str(fake_cli(tmp_path)))
    seen = {}

    def before(cmd, **options):
        seen.update(cmd=cmd, timeout=options["timeout"], encoding=options.get("encoding"))
        # The provider captures the stream through a file, never a pipe.
        assert hasattr(options["stdout"], "write"), "stdout must be a writable file"

    monkeypatch.setattr(kimi_cli.subprocess, "run", fake_run(out=answering('{"ok": true}'), before=before))
    result, usage = kimi_cli.run("the prompt", SCHEMA, system="Be brief.")
    assert result == {"ok": True}
    assert usage == {"input_tokens": None, "output_tokens": None}
    argv_prompt = seen["cmd"][seen["cmd"].index("-p") + 1]
    assert "the prompt" in argv_prompt and "Be brief." in argv_prompt
    assert "JSON Schema" in argv_prompt
    assert seen["timeout"] == kimi_cli.TIMEOUT["text"]
    # Its error output is read as UTF-8, never the Windows ANSI code page.
    assert seen["encoding"] == "utf-8"


def test_long_prompt_is_handed_off_as_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CLI", str(fake_cli(tmp_path)))
    seen = {}
    long_prompt = "find postings. " * 2000  # past INLINE_LIMIT

    def before(cmd, **options):
        seen.update(cmd=cmd, timeout=options["timeout"])
        handoff = Path(options["cwd"]) / "prompt.md"
        assert handoff.exists(), "the full instructions travel in prompt.md"
        seen["file"] = handoff.read_text(encoding="utf-8")

    monkeypatch.setattr(kimi_cli.subprocess, "run", fake_run(out=answering('{"ok": true}'), before=before))
    assert kimi_cli.run(long_prompt, SCHEMA, web=True) == ({"ok": True}, {"input_tokens": None, "output_tokens": None})
    argv_prompt = seen["cmd"][seen["cmd"].index("-p") + 1]
    assert argv_prompt == kimi_cli.FILE_HANDOFF
    assert long_prompt in seen["file"] and "JSON Schema" in seen["file"]
    assert seen["timeout"] == kimi_cli.TIMEOUT["web"]


# --- parsing the stream-json answer --------------------------------------------

def test_the_last_assistant_message_is_the_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CLI", str(fake_cli(tmp_path)))
    out = stream({"role": "meta", "type": "system.version", "version": "2.0.2"},
                 {"role": "assistant", "tool_calls": [{"name": "WebSearch"}]},
                 {"role": "tool", "content": "results"},
                 {"role": "assistant", "content": 'thinking out loud'},
                 {"role": "assistant", "content": '```json\n{"ok": true}\n```'})
    monkeypatch.setattr(kimi_cli.subprocess, "run", fake_run(out=out))
    assert kimi_cli.invoke("hi", SCHEMA) == {"ok": True}


def test_non_json_answers_become_plain_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CLI", str(fake_cli(tmp_path)))
    monkeypatch.setattr(kimi_cli.subprocess, "run", fake_run(out=answering("I could not find anything")))
    with pytest.raises(ValueError, match="did not return a JSON result"):
        kimi_cli.invoke("hi", SCHEMA)
    monkeypatch.setattr(kimi_cli.subprocess, "run", fake_run(out=answering('[1, 2]')))
    with pytest.raises(ValueError, match="did not match the requested schema"):
        kimi_cli.invoke("hi", SCHEMA)


def test_an_empty_answer_reports_what_the_cli_did(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CLI", str(fake_cli(tmp_path)))
    out = stream({"role": "meta", "type": "system.version", "version": "2.0.2"},
                 {"role": "assistant", "tool_calls": [{"function": {"name": "WebSearch"}}]},
                 {"role": "tool", "content": "results"})
    monkeypatch.setattr(kimi_cli.subprocess, "run", fake_run(out=out, err="oops here"))
    with pytest.raises(ValueError, match=r"without a final answer \(3 stream events, last: tool\)"):
        kimi_cli.invoke("hi", SCHEMA)
    # The assistant-with-tool-calls line names the tool it stopped on.
    out = stream({"role": "assistant", "tool_calls": [{"function": {"name": "WebSearch"}}]})
    monkeypatch.setattr(kimi_cli.subprocess, "run", fake_run(out=out))
    with pytest.raises(ValueError, match=r"last: assistant calling WebSearch"):
        kimi_cli.invoke("hi", SCHEMA)


def _wire_log(home: Path, folder_name: str, *records: dict) -> None:
    slug = re.sub(r"[^a-z0-9]+", "-", folder_name.casefold()).strip("-")
    wire = home / "sessions" / f"wd_{slug}_abc123" / "session_1" / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True)
    wire.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def test_a_quota_stop_is_named_from_the_session_wire_log(tmp_path, monkeypatch):
    """Print mode exits 1 with no output when the membership's 5-hour window is
    exhausted; the only record of why is the session wire log."""
    monkeypatch.setenv("KIMI_CLI", str(fake_cli(tmp_path)))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))

    def write_quota_wire(cmd, **options):
        _wire_log(tmp_path, Path(options["cwd"]).name,
                  {"type": "turn.step.interrupted", "message": "[provider.auth_error] 403 You've reached your 5-hour usage limit."},
                  {"type": "turn.ended", "error": {"code": "provider.auth_error", "message": "403 You've reached your 5-hour usage limit."}})

    monkeypatch.setattr(kimi_cli.subprocess, "run", fake_run(code=1, before=write_quota_wire))
    with pytest.raises(ValueError, match="usage limit"):
        kimi_cli.invoke("hi", SCHEMA)

    # The same diagnosis when the CLI exits 0 but never answered.
    def write_quota_wire_exit0(cmd, **options):
        _wire_log(tmp_path, Path(options["cwd"]).name,
                  {"type": "turn.ended", "error": {"code": "provider.auth_error", "message": "403 You've reached your 5-hour usage limit."}})

    monkeypatch.setattr(kimi_cli.subprocess, "run", fake_run(code=0, before=write_quota_wire_exit0))
    with pytest.raises(ValueError, match="usage limit"):
        kimi_cli.invoke("hi", SCHEMA)


def test_failures_become_plain_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_CLI", str(fake_cli(tmp_path)))
    cases = [
        (fake_run(err="HTTP 401 Unauthorized", code=1), "not signed in"),
        (fake_run(err="Error 429: membership usage limit reached", code=1), "usage limit"),
        (fake_run(err="boom", code=2), "could not finish"),
    ]
    for fake, expected in cases:
        monkeypatch.setattr(kimi_cli.subprocess, "run", fake)
        with pytest.raises(ValueError, match=expected):
            kimi_cli.invoke("hi", SCHEMA)

    def timeout(cmd, **options):
        raise subprocess.TimeoutExpired(cmd, options["timeout"])

    monkeypatch.setattr(kimi_cli.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="time limit"):
        kimi_cli.invoke("hi", SCHEMA, web=True)
    with pytest.raises(ValueError, match="Unsupported"):
        kimi_cli.invoke("hi", SCHEMA, model="gpt-9")


# --- beside Claude Code and Codex in the gateway --------------------------------

def test_gateway_offers_kimi_cli_with_web_but_without_gmail(service, monkeypatch):
    monkeypatch.setattr(kimi_cli, "available", lambda: True)
    gateway = AIGateway(service, lambda *a, **k: {})
    listed = {p["id"]: p for p in gateway.catalog("discovery")["providers"]}
    assert listed["kimi_cli"]["compatible"] and listed["kimi_cli"]["configured"]
    mail = {p["id"]: p for p in gateway.catalog("email")["providers"]}
    assert not mail["kimi_cli"]["compatible"]
    with pytest.raises(ValueError, match="missing apps"):
        gateway.resolve("email", "kimi_cli", "kimi-runtime")


def test_gateway_generate_routes_to_the_cli_with_the_action_web_flag(service, monkeypatch):
    calls = []
    monkeypatch.setattr(kimi_cli, "available", lambda: True)
    gateway = AIGateway(service, lambda *a, **k: {})
    gateway.providers["kimi_cli"].invoke = lambda prompt, schema, **o: calls.append((prompt, o)) or {"ok": True}
    assert gateway.generate("discovery", "find", SCHEMA, provider="kimi_cli", model="kimi-runtime") == {"ok": True}
    gateway.generate("resume_chat", "edit", SCHEMA, provider="kimi_cli", model="kimi-runtime", web=False)
    assert calls == [("find", {"model": "kimi-runtime", "web": True}), ("edit", {"model": "kimi-runtime", "web": False})]


def test_fallback_still_applies_when_kimi_cli_is_the_main_choice(service, monkeypatch):
    monkeypatch.setattr(kimi_cli, "available", lambda: True)
    gateway = AIGateway(service, lambda *a, **k: {})

    class _Backup:
        id = "openai"
        capabilities = {"structured", "web"}
        configured = True
        models = ("gpt-test",)

        def generate(self, prompt, schema, *, model, **options):
            return {"ok": True}

    gateway.providers["openai"] = _Backup()
    gateway.providers["kimi_cli"].invoke = lambda *a, **k: (_ for _ in ()).throw(ValueError("usage limit"))
    gateway.save_preferences({"default": {"provider": "kimi_cli", "model": "kimi-runtime"},
                              "actions": {},
                              "fallback": {"provider": "openai", "model": "gpt-test"}})
    assert gateway.generate("discovery", "find", SCHEMA) == {"ok": True}


# --- the Settings tab ----------------------------------------------------------

def test_settings_lists_kimi_cli_and_accepts_it_for_both_tiers(service, monkeypatch):
    monkeypatch.setattr(kimi_cli, "available", lambda: True)
    listed = {p["id"]: p for p in ai_settings.overview(service)["providers"]}
    assert listed["kimi_cli"]["configured"] is True
    assert listed["kimi_cli"]["models"] == ["kimi-runtime"]
    saved = ai_settings.save(service, {"tiers": {"strong": {"provider": "kimi_cli", "model": "kimi-runtime"},
                                                 "cheap": {"provider": "kimi_cli", "model": "kimi-runtime"}}})
    assert saved["tiers"]["strong"] == {"provider": "kimi_cli", "model": "kimi-runtime"}


def test_settings_test_connection_makes_one_real_call(service, monkeypatch):
    monkeypatch.setattr(kimi_cli, "available", lambda: True)
    monkeypatch.setattr(kimi_cli, "signed_in", lambda: True)
    monkeypatch.setattr(kimi_cli, "invoke", lambda prompt, schema, **o: {"word": "ready"})
    assert ai_settings.test_provider(service, "kimi_cli", "kimi-runtime")["ok"] is True

    monkeypatch.setattr(kimi_cli, "signed_in", lambda: False)
    result = ai_settings.test_provider(service, "kimi_cli", "kimi-runtime")
    assert result["ok"] is False and "not signed in" in result["detail"]

    monkeypatch.setattr(kimi_cli, "available", lambda: False)
    result = ai_settings.test_provider(service, "kimi_cli", "kimi-runtime")
    assert result["ok"] is False and "not installed" in result["detail"]


# --- the specialist team -------------------------------------------------------

def test_specialists_run_through_the_cli_and_are_schema_checked(tmp_path, monkeypatch):
    seen = {}
    recorded = []

    def run(prompt, schema, **options):
        seen.update(prompt=prompt, schema=schema, **options)
        return {"company": "Acme", "title": "Data Analyst", "location": "Austin, TX",
                "url": ""}, {"input_tokens": 5, "output_tokens": 2}

    monkeypatch.setattr(kimi_cli, "run", run)
    team = AgentTeam(tmp_path, {"strong": ("kimi_cli", "kimi-runtime"), "cheap": ("kimi_cli", "kimi-runtime")},
                     on_usage=recorded.append)
    verdict = team.run("posting_parser", {"posting_text": "Data Analyst at Acme, Austin, TX"})
    assert isinstance(verdict, schemas.PostingFields) and verdict.company == "Acme"
    assert seen["model"] == "kimi-runtime" and seen["web"] is False
    assert "Data Analyst" in seen["prompt"] and "Data Analyst" not in seen["system"]
    assert "company" in seen["schema"]["properties"]
    assert recorded[0]["provider"] == "kimi_cli"

    monkeypatch.setattr(kimi_cli, "run", lambda *a, **k: ({"company": ["not", "text"]}, {}))
    with pytest.raises(AgentError, match="did not match its schema"):
        team.run("posting_parser", {})

    def down(*a, **k):
        raise ValueError("Your Kimi membership's usage limit is reached.")

    monkeypatch.setattr(kimi_cli, "run", down)
    with pytest.raises(AgentError, match="usage limit"):
        team.run("posting_parser", {})
