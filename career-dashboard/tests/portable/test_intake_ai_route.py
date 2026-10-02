"""First-time intake uses Auto's saved route and its own paid-call budget."""

import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.ai import router
from backend.ai.agents.graph import AgentError
from backend.services.intake import api as intake_api
from backend.services.intake.job import IntakeJob


def intake_team(tmp_path, monkeypatch, *, ready, saved=None):
    app_root = tmp_path / "app"
    monkeypatch.setattr(intake_api, "APP_ROOT", app_root)
    monkeypatch.setattr("backend.ai.ready_providers", lambda _root: {**ready, "auto": True})
    monkeypatch.setattr(router, "DEFAULT_WHEN_UNSET", True)
    monkeypatch.setattr(router, "available_models", lambda _root, provider: {
        "codex": ("codex-runtime",), "kimi_cli": ("kimi-runtime",),
        "azure_openai": ("azure-test",),
    }.get(provider, ()))
    if saved is not None:
        monkeypatch.setattr(intake_api, "_legacy_preferences", lambda _profiles: saved)
    # The named legacy location is deliberately absent: a fresh clone has none.
    profiles = SimpleNamespace(legacy_root=tmp_path / "no-legacy-profile")
    job = IntakeJob(tmp_path / "profiles" / "sample-person")
    return intake_api.team_factory(profiles)(job._usage), job


def test_intake_honors_saved_route_and_logs_a_free_plan_switch(tmp_path, monkeypatch):
    saved = {"ai_preferences": {"route": {
        "order": ["codex", "kimi_cli", "claude_code", "azure_openai"],
        "enabled": {"codex": True, "kimi_cli": True, "claude_code": False, "azure_openai": False},
        "allow_fallbacks": True,
    }}, "ai_policy": {"daily_call_limit": 0}}
    team, job = intake_team(tmp_path, monkeypatch,
                            ready={"codex": True, "kimi_cli": True, "claude_code": False,
                                   "azure_openai": False}, saved=saved)
    calls = []

    def attempt(_agent, _text, provider, _model, _max_tokens):
        calls.append(provider)
        if provider == "codex":
            raise AgentError("Codex usage limit reached")
        return provider

    monkeypatch.setattr(team, "_repairing", attempt)
    assert team.run("profile_extractor", "disposable input") == "kimi_cli"
    assert calls == ["codex", "kimi_cli"]  # saved order, not the default Kimi-first order
    state = job.state()
    assert state["provider_fallbacks"][0]["from_provider"] == "codex"
    assert state["provider_fallbacks"][0]["to_provider"] == "kimi_cli"
    assert "paid_ai" not in state


def test_fresh_intake_can_use_a_paid_only_provider_with_its_own_budget(tmp_path, monkeypatch):
    team, job = intake_team(tmp_path, monkeypatch,
                            ready={"codex": False, "kimi_cli": False, "claude_code": False,
                                   "azure_openai": True})
    calls = []
    monkeypatch.setattr(team, "_repairing", lambda _a, _t, provider, _m, _n: calls.append(provider) or provider)
    assert team.run("profile_extractor", "disposable input") == "azure_openai"
    assert calls == ["azure_openai"]
    assert job.state()["paid_ai"]["calls"] == 1

    # Once the new profile opens, the same reservation joins its normal daily
    # counter. A repeated build must not count that call twice.
    reservation = job.state()["paid_ai"]["reservations"][0]
    day = datetime.fromisoformat(reservation["at"]).date().isoformat()
    service = SimpleNamespace(today=lambda: day, w=SimpleNamespace(timezone="UTC"))
    with sqlite3.connect(":memory:") as db:
        db.execute("""CREATE TABLE ai_calls(
            id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, day TEXT NOT NULL,
            state TEXT NOT NULL, created_at TEXT NOT NULL, provider TEXT NOT NULL,
            model TEXT NOT NULL, action TEXT NOT NULL, cache_version TEXT NOT NULL)""")
        intake_api._carry_intake_paid_calls(job, service, db)
        intake_api._carry_intake_paid_calls(job, service, db)
        rows = db.execute("SELECT day, provider, state FROM ai_calls").fetchall()
    assert rows == [(day, "azure_openai", "reserved")]


@pytest.mark.parametrize("limit,attempts", [(0, 0), (1, 1)])
def test_intake_paid_limit_blocks_and_reserves_failed_attempts(tmp_path, monkeypatch, limit, attempts):
    team, job = intake_team(tmp_path, monkeypatch,
                            ready={"codex": False, "kimi_cli": False, "claude_code": False,
                                   "azure_openai": True},
                            saved={"ai_policy": {"daily_call_limit": limit}})
    calls = []

    def fail(_agent, _text, provider, _model, _max_tokens):
        calls.append(provider)
        raise AgentError("Temporary provider failure")

    monkeypatch.setattr(team, "_repairing", fail)
    with pytest.raises(AgentError, match="paid AI is switched off|Temporary provider failure"):
        team.run("profile_extractor", "first try")
    with pytest.raises(AgentError, match="paid AI is switched off|paid limit"):
        team.run("profile_extractor", "second try")
    assert calls == ["azure_openai"] * attempts
    assert job.state().get("paid_ai", {}).get("calls", 0) == attempts
