"""Keys typed on the Settings page, one provider choice for every agent, key-based
providers in the gateway, and the step-by-step trace behind the Agents tab."""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

from backend.ai import catalog, claude_code, keys, models, settings as ai_settings  # noqa: E402
from backend.ai.providers import AIGateway, HostedProvider  # noqa: E402
from backend.services.agents import AgentRunner, REPORT_SCHEMA  # noqa: E402
from backend.services.observability import activity  # noqa: E402
from test_career_workspace import workspace, add  # noqa: E402,F401
from test_workspace_v2 import service  # noqa: E402,F401

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
          "additionalProperties": False}
FAKE = "sk-test0000000000000000000000000000"


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    for name in keys.NAMES:
        monkeypatch.delenv(name, raising=False)


# --- keys typed on the page ------------------------------------------------------

def test_saved_key_is_private_replaceable_and_wins_over_keys_txt(tmp_path):
    root = tmp_path / "career-dashboard"
    root.mkdir()
    (tmp_path / "keys.txt").write_text("OPENROUTER_API_KEY=sk-or-v1-older000000000000\n")
    assert keys.source(root, "OPENROUTER_API_KEY") == "Resume/keys.txt"

    keys.save(root, "OPENROUTER_API_KEY", FAKE)
    keys.save(root, "GEMINI_API_KEY", "AIza" + "x" * 30)
    keys.save(root, "OPENROUTER_API_KEY", FAKE.replace("0", "1"))  # replaces, never duplicates
    text = (root / ".env").read_text()
    assert text.count("OPENROUTER_API_KEY=") == 1 and "GEMINI_API_KEY=" in text
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(root / ".env").st_mode) == 0o600
    assert keys.secret(root, "OPENROUTER_API_KEY") == FAKE.replace("0", "1")
    assert keys.source(root, "OPENROUTER_API_KEY") == "saved in the app"

    # Removing the app's copy falls back to the hand-kept file, which is untouched.
    assert keys.remove(root, "OPENROUTER_API_KEY") == "Resume/keys.txt"
    assert "older" in (tmp_path / "keys.txt").read_text()
    keys.remove(root, "GEMINI_API_KEY")
    assert not (root / ".env").exists()


def test_key_input_is_checked_before_anything_is_written(tmp_path):
    with pytest.raises(ValueError, match="does not look like an API key"):
        keys.save(tmp_path, "OPENAI_API_KEY", "two words")
    with pytest.raises(ValueError, match="Unknown"):
        keys.save(tmp_path, "PATH", FAKE)
    assert not (tmp_path / ".env").exists()


def test_save_key_reports_whether_the_key_works_and_never_echoes_it(service, monkeypatch):
    monkeypatch.setattr(catalog, "_fetch", lambda provider, key: ["model-a", "model-b"])
    good = ai_settings.save_key(service, "openrouter", FAKE)
    assert good["ok"] and "2 models" in good["detail"] and FAKE not in json.dumps(good)

    import urllib.error
    def refused(provider, key):
        raise urllib.error.HTTPError("u", 401, "no", {}, None)
    monkeypatch.setattr(catalog, "_fetch", refused)
    bad = ai_settings.save_key(service, "kimi", FAKE)
    assert not bad["ok"] and "HTTP 401" in bad["detail"]
    with pytest.raises(ValueError, match="does not use an API key"):
        ai_settings.save_key(service, "codex", FAKE)
    with service.w.connect() as db:
        logged = " ".join(r[0] for r in db.execute("SELECT details FROM activity"))
    assert FAKE not in logged
    assert ai_settings.remove_key(service, "kimi")["source"] is None


# --- one choice for every agent ----------------------------------------------------

def test_key_providers_join_the_gateway_and_web_work_moves_to_a_capable_one(service, monkeypatch):
    monkeypatch.setattr(claude_code, "available", lambda: True)
    keys.save(service.w.root, "GEMINI_API_KEY", "AIza" + "x" * 30)
    gateway = AIGateway(service, lambda *a, **k: {})
    assert isinstance(gateway.providers["gemini"], HostedProvider)
    assert gateway.providers["gemini"].configured and not gateway.providers["kimi"].configured

    overview = ai_settings.choose_main(service, gateway, "gemini", "gemini-3.5-flash-lite")
    routes = {r["action"]: r for r in overview["routes"]}
    assert routes["document_review"]["provider"] == "gemini" and not routes["document_review"]["moved"]
    assert routes["role_research"]["provider"] == "claude_code" and routes["role_research"]["moved"]
    assert routes["email"]["provider"] == "codex"
    stored = service.pref("ai_preferences")
    assert stored["actions"] == {} and stored["tiers"]["strong"] == {"provider": "gemini", "model": "gemini-3.5-flash-lite"}
    assert stored["tiers"]["cheap"]["provider"] == "gemini"

    # Asking for a provider by name is still refused when it cannot do the work.
    with pytest.raises(ValueError, match="missing web"):
        gateway.resolve("role_research", "gemini", "gemini-3.5-flash-lite")


def test_choose_main_refuses_a_provider_that_is_not_ready(service):
    gateway = AIGateway(service, lambda *a, **k: {})
    with pytest.raises(ValueError, match="not ready"):
        ai_settings.choose_main(service, gateway, "kimi", "kimi-k2-0905-preview")
    with pytest.raises(ValueError, match="Unknown"):
        ai_settings.choose_main(service, gateway, "nobody", "x")


def test_choosing_codex_moves_the_chat_tiers_too(service):
    """The specialists (and the assistant's agent loop) run on Codex now, so one choice covers everything."""
    gateway = AIGateway(service, lambda *a, **k: {})
    service.set_pref("ai_preferences", {"tiers": {"strong": {"provider": "claude_code", "model": "sonnet"}}})
    ai_settings.choose_main(service, gateway, "codex", "codex-runtime")
    stored = service.pref("ai_preferences")
    assert stored["default"]["provider"] == "codex"
    assert stored["tiers"] == {"strong": {"provider": "codex", "model": "codex-runtime"}, "cheap": {"provider": "codex", "model": "codex-runtime"}}


def test_hosted_provider_returns_the_structured_object(service, monkeypatch):
    keys.save(service.w.root, "OPENROUTER_API_KEY", FAKE)
    seen = {}

    class Fake:
        def with_structured_output(self, schema, **options):
            seen.update(schema=schema, options=options)
            return self

        def invoke(self, prompt):
            return {"ok": True}

    monkeypatch.setattr(models, "build", lambda root, provider, model, **o: Fake())
    provider = AIGateway(service, lambda *a, **k: {}).providers["openrouter"]
    assert provider.generate("check", SCHEMA, model=catalog.default_model("openrouter", "cheap")) == {"ok": True}
    assert seen["schema"]["title"] == "career_result" and seen["options"] == {"method": "function_calling"}
    with pytest.raises(ValueError, match="Unsupported"):
        provider.generate("check", SCHEMA, model="not-a-listed-model")


# --- the Agents tab trace ------------------------------------------------------------

def test_runs_record_each_stage_and_ai_call_for_the_agents_tab(service):
    job = add(service.w)
    report = {"summary": "Public facts", "report": "A report " * 30, "sources": [], "limitations": []}
    runner = AgentRunner(service, lambda prompt, schema, **k: report)
    runner.enqueue("research", job["id"])
    runner.pool.shutdown(wait=True)
    runner2 = AgentRunner(service, lambda prompt, schema, **k: report)
    runner2.enqueue("research", job["id"])  # same prompts: every step is a saved result
    runner2.pool.shutdown(wait=True)

    seen = activity(service, runner2)
    latest, first = seen["runs"][0], seen["runs"][1]
    assert first["state"] == "completed" and set(first["outputs"]) == {"research", "hiring", "comparison"}
    kinds = [e["kind"] for e in first["events"]]
    assert kinds[0] == "stage" and kinds[-1] == "completed" and kinds.count("ai_call") == 3
    stages = [e["label"] for e in first["events"] if e["kind"] == "stage"]
    assert stages[1:] == ["Researching the company and role", "Independent hiring-manager review",
                          "Comparing your active profile"]
    calls = [e for e in first["events"] if e["kind"] == "ai_call"]
    assert all(c["ok"] and c["fresh"] and c["provider"] == "codex" for c in calls)
    assert calls[0]["label"] == "Researching the company and role" and calls[0]["web"] is True
    assert calls[1]["label"] == "Independent hiring-manager review" and calls[1]["web"] is False
    assert [c["fresh"] for c in latest["events"] if c["kind"] == "ai_call"] == [False, False, False]
    assert seen["calls_today"]["completed"] == 3 and first["company"] == job["company"]


def test_a_failed_call_is_traced_with_its_reason(service):
    job = add(service.w)
    def refuse(prompt, schema, **k):
        raise ValueError("Your Claude subscription's usage limit is reached.")
    runner = AgentRunner(service, refuse)
    runner.enqueue("research", job["id"])
    runner.pool.shutdown(wait=True)
    run = activity(service, runner)["runs"][0]
    assert run["state"] == "failed"
    call = next(e for e in run["events"] if e["kind"] == "ai_call")
    assert call["ok"] is False and "usage limit" in call["error"]
    assert run["events"][-1]["kind"] == "failed"
