"""Kimi (Moonshot) as a first-class provider: catalogue, keys, gateway, fallback."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from test_career_workspace import workspace  # noqa: E402,F401
from backend.ai import catalog, keys, models  # noqa: E402
from backend.ai.providers import AIGateway  # noqa: E402
from backend.services.workspace_v2 import CareerServices  # noqa: E402


@pytest.fixture
def service(workspace):
    return CareerServices(workspace)


def test_kimi_is_in_the_hosted_catalogue():
    spec = catalog.PROVIDERS["kimi"]
    assert spec["key"] == "MOONSHOT_API_KEY"
    assert spec["base_url"] == "https://api.moonshot.ai/v1"
    assert spec["defaults"]["strong"]


def test_kimi_builds_an_openai_compatible_client(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test-moonshot-key-1234567890")
    llm = models.build(ROOT, "kimi", "kimi-k2-0905-preview")
    assert type(llm).__name__ == "ChatOpenAI"
    assert "moonshot" in str(llm.openai_api_base)


def test_kimi_without_a_key_says_so_plainly(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setattr(keys, "_files", lambda root: ())
    with pytest.raises(models.ProviderNotConfigured, match="MOONSHOT_API_KEY"):
        models.build(ROOT, "kimi", "kimi-k2-0905-preview")


def test_bare_kimi_token_is_not_guessed():
    # Kimi keys share OpenAI's "sk-" shape, so only the named form is accepted.
    found = keys._parse("sk-1234567890abcdefghij\n")
    assert "MOONSHOT_API_KEY" not in found
    named = keys._parse("MOONSHOT_API_KEY=sk-1234567890abcdefghij\n")
    assert named["MOONSHOT_API_KEY"] == "sk-1234567890abcdefghij"


def test_kimi_resolves_for_a_structured_action(service, monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test-moonshot-key-1234567890")
    gateway = AIGateway(service, lambda *a, **k: {})
    gateway.save_preferences({"default": {"provider": "kimi", "model": "kimi-k2-0905-preview"}, "actions": {}})
    provider, model = gateway.resolve("requirement_extraction")
    assert provider.id == "kimi" and model == "kimi-k2-0905-preview"


def test_kimi_needs_a_capable_provider_for_web_actions(service, monkeypatch):
    # Kimi has no built-in web tool here; discovery must move to one that has.
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test-moonshot-key-1234567890")
    gateway = AIGateway(service, lambda *a, **k: {})
    with pytest.raises(ValueError, match="not compatible"):
        gateway.resolve("discovery", "kimi", "kimi-k2-0905-preview")


class _Down:
    id = "kimi"
    capabilities = {"structured"}

    @property
    def configured(self):
        return True

    @property
    def models(self):
        return ("kimi-k2-0905-preview",)

    def generate(self, prompt, schema, *, model, **options):
        raise ValueError("Kimi: the account is out of credits")


class _Backup:
    id = "openai"
    capabilities = {"structured", "web"}

    @property
    def configured(self):
        return True

    @property
    def models(self):
        return ("gpt-test",)

    def generate(self, prompt, schema, *, model, **options):
        return {"word": "ready"}


def test_generate_falls_back_to_the_configured_backup(service):
    gateway = AIGateway(service, lambda *a, **k: {})
    gateway.providers["kimi"] = _Down()
    gateway.providers["openai"] = _Backup()
    gateway.save_preferences(
        {
            "default": {"provider": "kimi", "model": "kimi-k2-0905-preview"},
            "actions": {},
            "fallback": {"provider": "openai", "model": "gpt-test"},
        }
    )
    result = gateway.generate(
        "requirement_extraction",
        "Reply with the single word: ready",
        {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
    )
    assert result == {"word": "ready"}
    with service.w.connect() as db:
        row = db.execute(
            "SELECT details FROM activity WHERE action='provider_fallback' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row and '"from_provider": "kimi"' in row[0] and '"to_provider": "openai"' in row[0]


def test_no_fallback_configured_raises_the_original_error(service):
    gateway = AIGateway(service, lambda *a, **k: {})
    gateway.providers["kimi"] = _Down()
    gateway.save_preferences({"default": {"provider": "kimi", "model": "kimi-k2-0905-preview"}, "actions": {}})
    with pytest.raises(ValueError, match="out of credits"):
        gateway.generate("requirement_extraction", "x", {"type": "object", "properties": {}, "required": []})


def test_save_fallback_roundtrip(service):
    from backend.ai import settings

    gateway = AIGateway(service, lambda *a, **k: {})
    gateway.providers["openai"] = _Backup()
    settings.save_fallback(service, gateway, "openai", "gpt-test")
    assert service.pref("ai_preferences")["fallback"] == {"provider": "openai", "model": "gpt-test"}
    settings.save_fallback(service, gateway, "", "")
    assert service.pref("ai_preferences")["fallback"] is None
    with pytest.raises(ValueError, match="Unknown AI provider"):
        settings.save_fallback(service, gateway, "not-a-provider", "x")
