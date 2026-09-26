"""Model choices for Codex and Kimi Code, like Claude Code's Sonnet / Opus / Haiku.

Neither app has a fixed list here: Codex caches the models her ChatGPT plan
offers in ~/.codex/models_cache.json, and Kimi Code lists her membership's
models in ~/.kimi-code/config.toml. Both are read as they are, so the choices
follow the plan. The app's own default stays first ("codex-runtime",
"kimi-runtime": no model flag at all); a named model is passed to the CLI.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

from backend.ai import codex, kimi_cli  # noqa: E402
from backend.ai.providers import CodexProvider, KimiCliProvider  # noqa: E402
from backend.services.pipeline import default_speed, local_models  # noqa: E402

CODEX_CACHE = {"models": [
    {"slug": "gpt-6-sol", "display_name": "GPT-6-Sol", "visibility": "list", "priority": 2,
     "description": "Workhorse model for coding and everyday work."},
    {"slug": "gpt-6-astra", "display_name": "GPT-6-Astra", "visibility": "list", "priority": 1,
     "description": "Frontier intelligence for the most demanding work."},
    {"slug": "codex-auto-review", "display_name": "Codex Auto Review", "visibility": "hide", "priority": 43},
    {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "visibility": "list", "priority": 3,
     "description": "Fast and affordable model for easier tasks."},
    {"slug": "gpt-5.5", "display_name": "GPT-5.5", "visibility": "list", "priority": 12, "description": "Legacy."},
]}

KIMI_CONFIG = '''
default_model = "kimi-code/kimi-for-coding"

[providers."managed:kimi-code"]
type = "kimi"
api_key = "not-a-real-key"

[models."kimi-code/kimi-for-coding"]
provider = "managed:kimi-code"
model = "kimi-for-coding"
display_name = "K2.8 Preview"

[models."kimi-code/kimi-for-coding-highspeed"]
provider = "managed:kimi-code"
model = "kimi-for-coding-highspeed"
display_name = "K2.7 Code Highspeed"

[models."kimi-code/k3"]
provider = "managed:kimi-code"
model = "k3"
display_name = "K3"
'''


def write_codex_cache(homes):
    folder = Path(os.environ["CODEX_HOME"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "models_cache.json").write_text(json.dumps(CODEX_CACHE), encoding="utf-8")


def write_kimi_config():
    folder = Path(os.environ["KIMI_CODE_HOME"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.toml").write_text(KIMI_CONFIG, encoding="utf-8")


def test_codex_lists_the_plans_models_in_codex_order(empty_ai_app_homes):
    assert codex.listed_models() == [] and codex.models() == ("codex-runtime",)
    write_codex_cache(empty_ai_app_homes)
    assert [m["id"] for m in codex.listed_models()] == ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.5"]
    assert codex.listed_models()[0] == {"id": "gpt-6-astra", "label": "GPT-6-Astra",
                                        "hint": "Frontier intelligence for the most demanding work."}
    assert codex.models()[0] == "codex-runtime" and "codex-auto-review" not in codex.models()


def test_a_named_codex_model_goes_to_the_cli_as_dash_m(tmp_path):
    base = codex.command(Path("codex"), tmp_path, tmp_path / "s.json", tmp_path / "o.json", web=True)
    named = codex.command(Path("codex"), tmp_path, tmp_path / "s.json", tmp_path / "o.json", web=True,
                          model="gpt-6-luna")
    assert "-m" not in base
    assert named[named.index("-m") + 1] == "gpt-6-luna" and named.index("-m") < named.index("-C")
    assert codex.model_flag("codex-runtime") == [] and codex.model_flag(None) == []


def test_the_codex_provider_passes_a_named_model_only(empty_ai_app_homes):
    write_codex_cache(empty_ai_app_homes)
    calls = []
    provider = CodexProvider(lambda prompt, schema, **options: calls.append(options) or {"ok": True})
    provider.generate("p", {}, model="codex-runtime", web=True)
    provider.generate("p", {}, model="gpt-6-sol", web=False)
    assert calls == [{"web": True}, {"web": False, "model": "gpt-6-sol"}]
    assert "gpt-6-luna" in provider.models


def test_kimi_lists_the_memberships_models_default_first():
    assert kimi_cli.listed_models() == [] and kimi_cli.models() == ("kimi-runtime",)
    write_kimi_config()
    listed = kimi_cli.listed_models()
    assert [m["id"] for m in listed] == ["kimi-code/kimi-for-coding", "kimi-code/kimi-for-coding-highspeed", "kimi-code/k3"]
    assert listed[0]["default"] is True and listed[0]["label"] == "K2.8 Preview"
    assert "api_key" not in json.dumps(listed)  # only names leave the config
    assert KimiCliProvider().models == ("kimi-runtime", *[m["id"] for m in listed])


def test_a_named_kimi_model_goes_to_the_cli_as_dash_dash_model():
    assert kimi_cli.command(Path("kimi"), "hi") == ["kimi", "-p", "hi", "--output-format", "stream-json"]
    assert kimi_cli.command(Path("kimi"), "hi", "kimi-code/k3")[:3] == ["kimi", "--model", "kimi-code/k3"]
    assert "--model" not in kimi_cli.command(Path("kimi"), "hi", "kimi-runtime")


def test_the_daily_search_cards_offer_default_plus_three(empty_ai_app_homes):
    write_codex_cache(empty_ai_app_homes)
    write_kimi_config()
    codex_choices = local_models("codex")
    assert [m["label"] for m in codex_choices] == ["Default", "GPT-6-Astra", "GPT-6-Sol", "GPT-6-Luna"]
    kimi_choices = local_models("kimi_cli")
    assert [m["label"] for m in kimi_choices] == ["Default", "K2.7 Code Highspeed", "K3"]
    assert kimi_choices[0]["hint"] == "Kimi Code's usual model (K2.8 Preview)."
    assert kimi_choices[1]["hint"] == "Faster replies."
    assert [m["id"] for m in local_models("claude_code")] == ["sonnet", "opus", "haiku"]
    # A light model is guessed faster until a run is measured.
    assert default_speed("codex", "gpt-6-luna") < default_speed("codex", "gpt-6-astra") == 1.2


def test_the_vscode_extension_alone_is_named_with_the_install_command(empty_ai_app_homes, monkeypatch):
    monkeypatch.delenv("KIMI_CLI", raising=False)
    monkeypatch.setattr(kimi_cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(kimi_cli, "BUNDLED", ())
    assert kimi_cli.extension_only() is False
    (empty_ai_app_homes / "moonshot-ai.kimi-code-0.8.1-win32-x64").mkdir()
    monkeypatch.setattr(kimi_cli, "EXTENSION", str(empty_ai_app_homes / "moonshot-ai.kimi-code-*"))
    assert kimi_cli.extension_only() is True
    try:
        kimi_cli.run("hi", {"type": "object"})
    except ValueError as error:
        assert kimi_cli.INSTALL_COMMAND in str(error) and "same sign-in" in str(error)
    else:
        raise AssertionError("expected the not-installed message")


def test_testing_the_codex_connection_makes_one_real_call(monkeypatch):
    """Installed is not enough: a signed-out app or a spent plan must fail here, not mid-run."""
    from backend.ai import settings as ai_settings

    seen = {}
    monkeypatch.setattr(codex, "available", lambda: True)

    def answer(prompt, schema, **options):
        seen.update(options)
        return {"word": "ready"}

    monkeypatch.setattr(codex, "invoke", answer)
    result = ai_settings.test_provider(None, "codex", "gpt-6-luna")
    assert result["ok"] is True and "ready" in result["detail"] and seen["model"] == "gpt-6-luna"

    def spent(prompt, schema, **options):
        raise ValueError("Codex could not finish: You've hit your usage limit.")

    monkeypatch.setattr(codex, "invoke", spent)
    result = ai_settings.test_provider(None, "codex", "codex-runtime")
    assert result["ok"] is False and "usage limit" in result["detail"]

    monkeypatch.setattr(codex, "available", lambda: False)
    assert "not installed" in ai_settings.test_provider(None, "codex", "codex-runtime")["detail"]
