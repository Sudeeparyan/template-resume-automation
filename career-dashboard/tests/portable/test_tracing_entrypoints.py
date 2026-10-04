"""CLI subprocesses inherit the local tracing policy before importing AI libraries."""

from backend import HOSTED_TRACING
import career_cli


def test_cli_clears_inherited_hosted_tracing_before_spawning_doctor(monkeypatch):
    for name in HOSTED_TRACING:
        monkeypatch.setenv(name, "true")
    calls = []
    monkeypatch.setattr(
        career_cli.subprocess, "call", lambda args, env: calls.append((args, env)) or 0
    )
    assert career_cli.main(["doctor"]) == 0
    [(_, environment)] = calls
    assert all(environment[name] == "false" for name in HOSTED_TRACING)


def test_cli_preserves_an_explicit_local_developer_opt_in(monkeypatch):
    monkeypatch.setenv("CAREER_ALLOW_LANGSMITH", "1")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    calls = []
    monkeypatch.setattr(
        career_cli.subprocess, "call", lambda args, env: calls.append(env) or 0
    )
    assert career_cli.main(["ws", "summary", "--profile", "example"]) == 0
    assert calls[0]["LANGSMITH_TRACING"] == "true"
