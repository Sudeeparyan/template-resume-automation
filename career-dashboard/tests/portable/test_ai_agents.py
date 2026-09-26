"""Key discovery, specialist wiring and the candidate-data isolation boundary."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

from backend.ai import catalog, keys  # noqa: E402
from backend.ai.agents import schemas, specialists  # noqa: E402
from backend.ai.agents.graph import AgentError, AgentTeam  # noqa: E402

OPENROUTER = "sk-or-v1-" + "b" * 60
ANTHROPIC = "sk-ant-" + "c" * 60
GEMINI = "AQ." + "d" * 50


def workspace(tmp_path):
    """A repository layout: <repo>/career-dashboard, with keys files alongside."""
    root = tmp_path / "career-dashboard"
    root.mkdir()
    return root


def test_reads_named_pairs_from_the_backend_env(tmp_path):
    root = workspace(tmp_path)
    (root / ".env").write_text(f"OPENROUTER_API_KEY={OPENROUTER}\n# comment\n\n")
    assert keys.load(root)["OPENROUTER_API_KEY"] == OPENROUTER


def test_reads_bare_tokens_pasted_into_keys_txt(tmp_path):
    """keys.txt is written by hand, so a line holding only the token must work."""
    root = workspace(tmp_path)
    (root.parent / "keys.txt").write_text(
        f"{OPENROUTER}\n\ncurl https://openrouter.ai/api/v1/chat/completions \\\n"
        '  -d \'{"model": "openai/gpt-4o"}\'\n'
    )
    loaded = keys.load(root)
    assert loaded["OPENROUTER_API_KEY"] == OPENROUTER
    # The sample curl block must not be mistaken for another key.
    assert set(loaded) == {"OPENROUTER_API_KEY"}


def test_bare_tokens_are_matched_by_provider_prefix(tmp_path):
    root = workspace(tmp_path)
    (root.parent / ".env").write_text(f"{ANTHROPIC}\n{GEMINI}\n")
    loaded = keys.load(root)
    assert loaded["ANTHROPIC_API_KEY"] == ANTHROPIC
    assert loaded["GEMINI_API_KEY"] == GEMINI


def test_nearest_key_file_wins(tmp_path):
    root = workspace(tmp_path)
    (root / ".env").write_text(f"OPENROUTER_API_KEY={OPENROUTER}\n")
    (root.parent / ".env").write_text("OPENROUTER_API_KEY=sk-or-v1-" + "z" * 60 + "\n")
    assert keys.load(root)["OPENROUTER_API_KEY"] == OPENROUTER


def test_environment_outranks_every_file(tmp_path, monkeypatch):
    root = workspace(tmp_path)
    (root / ".env").write_text(f"OPENROUTER_API_KEY={OPENROUTER}\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "e" * 60)
    assert keys.load(root)["OPENROUTER_API_KEY"].endswith("e" * 60)


def test_configured_reports_presence_without_exposing_values(tmp_path):
    root = workspace(tmp_path)
    (root / ".env").write_text(f"OPENROUTER_API_KEY={OPENROUTER}\n")
    reported = keys.configured(root)
    assert reported["OPENROUTER_API_KEY"] is True
    assert reported["ANTHROPIC_API_KEY"] is False
    assert OPENROUTER not in repr(reported)


def test_missing_key_files_are_not_an_error(tmp_path):
    assert keys.load(workspace(tmp_path)) == {}


def test_every_provider_declares_a_model_for_each_tier():
    for provider_id, spec in catalog.PROVIDERS.items():
        for tier in catalog.TIERS:
            # Azure's model is her own deployment, named in .env (tests/test_azure_openai.py).
            if provider_id != catalog.AZURE:
                assert spec["defaults"][tier], f"{provider_id} has no {tier} default"
            assert spec["key"] in keys.NAMES


def test_the_hiring_manager_is_marked_isolated():
    assert specialists.REGISTRY["hiring_manager"].isolated is True
    assert not any(
        agent.isolated for name, agent in specialists.REGISTRY.items() if name != "hiring_manager"
    )


def test_an_isolated_agent_cannot_be_called_on_the_ordinary_path(tmp_path):
    """run() takes arbitrary payloads, so it must refuse the candidate-blind agent."""
    team = AgentTeam(workspace(tmp_path), {"strong": ("openrouter", "m"), "cheap": ("openrouter", "m")})
    with pytest.raises(AgentError, match="isolated"):
        team.run("hiring_manager", {"profile": "everything about the candidate"})


def test_run_isolated_refuses_an_agent_that_is_not_isolated(tmp_path):
    team = AgentTeam(workspace(tmp_path), {"strong": ("openrouter", "m"), "cheap": ("openrouter", "m")})
    with pytest.raises(AgentError, match="not an isolated agent"):
        team.run_isolated("resume_tailor", job_description="jd")


def test_run_isolated_sends_only_the_job_and_public_research(tmp_path, monkeypatch):
    """The payload shape is the isolation guarantee; assert it directly."""
    team = AgentTeam(workspace(tmp_path), {"strong": ("openrouter", "m"), "cheap": ("openrouter", "m")})
    seen = {}

    def capture(agent, payload, max_tokens):
        seen["payload"] = payload
        return "reviewed"

    monkeypatch.setattr(team, "_invoke", capture)
    team.run_isolated("hiring_manager", job_description="Build dashboards",
                      public_research="Example Ltd is a mid-sized firm")
    assert set(seen["payload"]) == {"job_description", "public_company_research"}


def test_preferences_choose_the_provider_and_model_per_tier(tmp_path):
    team = AgentTeam.from_preferences(workspace(tmp_path), {
        "tiers": {"strong": {"provider": "anthropic", "model": "claude-sonnet-5"}}
    })
    assert team.tiers["strong"] == ("anthropic", "claude-sonnet-5")
    # An unset tier falls back to that provider's documented default.
    assert team.tiers["cheap"] == ("openrouter", catalog.default_model("openrouter", "cheap"))


def test_cheap_tier_carries_the_extraction_and_classification_work():
    """Cost control: only candidate-facing prose may use the expensive tier."""
    cheap = {name for name, agent in specialists.REGISTRY.items() if agent.tier == "cheap"}
    assert {"fit_analyst", "posting_parser", "mail_classifier"} <= cheap
    strong = {name for name, agent in specialists.REGISTRY.items() if agent.tier == "strong"}
    assert {"resume_tailor", "cover_letter_writer", "profile_curator"} <= strong
