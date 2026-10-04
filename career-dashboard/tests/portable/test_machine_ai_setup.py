"""Before any profile exists, the shell says which AI this computer can use and saves a key locally."""

from fastapi.testclient import TestClient

from backend.ai import keys, settings as ai_settings
from backend.dashboard.shell import create_shell
from backend.profiles import ProfileStore


def client(tmp_path):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    return TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1")


def test_status_lists_ai_apps_and_key_providers_without_values(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.ai.ready_providers", lambda root: {"anthropic": False, "claude_code": False, "codex": False})
    with client(tmp_path) as shell:
        status = shell.get("/api/ai/status").json()
    assert status["any_ready"] is False
    assert {cli["id"] for cli in status["clis"]} == {"claude_code", "codex", "kimi_cli"}
    assert {"anthropic", "openai", "gemini"} <= {key["id"] for key in status["keys"]}
    assert all(key["get_key"].startswith("https://") for key in status["keys"])
    assert "sk-" not in str(status)


def test_a_key_is_saved_for_the_computer_and_tested(tmp_path, monkeypatch):
    saved = {}
    monkeypatch.setattr(keys, "save", lambda root, name, value: saved.update({name: value}))  # never the real .env
    monkeypatch.setattr(ai_settings.catalog, "models", lambda root, provider, refresh=False: {"error": "", "models": ["a", "b"]})
    monkeypatch.setattr("backend.ai.ready_providers", lambda root: {"anthropic": bool(saved)})
    with client(tmp_path) as shell:
        answer = shell.put("/api/ai/keys/anthropic", json={"value": "example-key-value-0000"}).json()
        refused = shell.put("/api/ai/keys/codex", json={"value": "anything"})
    assert answer["ok"] and "2 models" in answer["detail"] and answer["status"]["any_ready"] is True
    assert saved == {"ANTHROPIC_API_KEY": "example-key-value-0000"}
    assert "example-key-value" not in str(answer)
    assert refused.status_code == 400
