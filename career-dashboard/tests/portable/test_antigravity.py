import json
from pathlib import Path

import pytest

from backend.ai import antigravity


def test_antigravity_command_uses_safe_headless_streaming_and_schema(tmp_path):
    cli = Path("agy.exe")
    schema = tmp_path / "schema.json"
    argv = antigravity.command(cli, schema, model="antigravity-runtime", timeout=600)
    assert argv[:1] == [str(cli)]
    assert "--input-format" in argv and "stream-json" in argv
    assert "--json-schema" in argv and str(schema) in argv
    assert "--sandbox" in argv
    assert "--dangerously-skip-permissions" not in argv
    assert "-p" not in argv


def test_antigravity_structured_result_and_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(antigravity, "find_cli", lambda: Path("agy"))
    monkeypatch.setattr(antigravity, "models", lambda: ("antigravity-runtime",))
    seen = {}

    class Result:
        returncode = 0
        stdout = json.dumps({"event": "result", "result": {
            "status": "SUCCESS", "structured_output": {"word": "ready"},
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }}) + "\n"
        stderr = ""

    def fake_run(argv, **options):
        seen.update(argv=argv, options=options)
        assert json.loads(options["input"])["event"] == "user"
        assert Path(argv[argv.index("--json-schema") + 1]).is_file()
        return Result()

    monkeypatch.setattr(antigravity.subprocess, "run", fake_run)
    result, usage = antigravity.run("Reply", {"type": "object"}, system="System")
    assert result == {"word": "ready"}
    assert usage == {"input_tokens": 12, "output_tokens": 3}
    assert "System" in seen["options"]["input"]
    assert "--dangerously-skip-permissions" not in seen["argv"]


@pytest.mark.parametrize("message,expected", [
    ("authentication required", "not signed in"),
    ("quota exceeded", "usage limit"),
    ("unknown model", "could not finish"),
])
def test_antigravity_failures_are_actionable(monkeypatch, message, expected):
    monkeypatch.setattr(antigravity, "find_cli", lambda: Path("agy"))
    monkeypatch.setattr(antigravity, "models", lambda: ("antigravity-runtime",))

    class Result:
        returncode = 1
        stdout = json.dumps({"event": "result", "result": {"status": "ERROR", "error": message}})
        stderr = ""

    monkeypatch.setattr(antigravity.subprocess, "run", lambda *args, **kwargs: Result())
    with pytest.raises(ValueError, match=expected):
        antigravity.run("Reply", {"type": "object"})


def test_antigravity_web_is_never_claimed_without_scoped_permissions(monkeypatch):
    monkeypatch.setattr(antigravity, "find_cli", lambda: Path("agy"))
    monkeypatch.setattr(antigravity, "models", lambda: ("antigravity-runtime",))
    with pytest.raises(ValueError, match="scoped URL permissions"):
        antigravity.run("Search", {"type": "object"}, web=True)


def test_antigravity_model_discovery_does_not_read_credentials(monkeypatch):
    monkeypatch.setattr(antigravity, "find_cli", lambda: Path("agy"))

    class Result:
        returncode = 0
        stdout = "Available models:\n* gemini-3.8-flash-medium\n  gemini-3.8-pro\n"
        stderr = ""

    monkeypatch.setattr(antigravity.subprocess, "run", lambda *args, **kwargs: Result())
    assert antigravity._model_probe() == {
        "models": ["gemini-3.8-flash-medium", "gemini-3.8-pro"], "signed_in": True,
    }


def test_antigravity_gateway_provider_is_structured_without_unproven_web(monkeypatch):
    from backend.ai.providers import AntigravityProvider

    calls = []
    provider = AntigravityProvider(invoke=lambda prompt, schema, **options: calls.append(options) or {"word": "ready"})
    monkeypatch.setattr(antigravity, "available", lambda: True)
    monkeypatch.setattr(antigravity, "models", lambda: ("antigravity-runtime",))
    assert provider.configured is True
    assert provider.capabilities == {"structured"}
    assert provider.generate("Reply", {}) == {"word": "ready"}
    assert calls == [{"model": "antigravity-runtime", "web": False}]
