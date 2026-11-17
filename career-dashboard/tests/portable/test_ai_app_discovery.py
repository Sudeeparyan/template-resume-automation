"""Claude Code and Antigravity are found where their own installers put them, and setup says how to add them."""

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.ai import antigravity, claude_code, codex, kimi_cli, settings as ai_settings

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("bootstrap_for_discovery", ROOT / "scripts/bootstrap.py")
bootstrap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bootstrap)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A made-up home folder with nothing on PATH, so only the known install folders can be found."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.delenv("AGY_CLI", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_CLI", raising=False)
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    return tmp_path


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"binary")
    path.chmod(0o755)
    return path


def test_agy_is_found_in_the_official_install_folder_when_path_is_stale(home):
    assert antigravity.find_cli() is None
    if os.name == "nt":
        placed = executable(home / "AppData" / "Local" / "agy" / "bin" / "agy.exe")
    else:
        placed = executable(home / ".local" / "bin" / "agy")
    assert antigravity.find_cli() == placed
    assert antigravity.available() is True


def test_the_desktop_app_alone_is_reported_as_missing_its_command_line_tool(home):
    assert antigravity.desktop_only() is False
    if os.name == "nt":
        executable(home / "AppData" / "Local" / "Programs" / "Antigravity" / "Antigravity.exe")
    else:
        (home / "Applications" / "Antigravity.app").mkdir(parents=True)
    assert antigravity.desktop_only() is True
    executable(home / ".local" / "bin" / ("agy.exe" if os.name == "nt" else "agy"))
    assert antigravity.desktop_only() is False


def test_install_commands_are_the_official_ones_for_this_system():
    expected_ag = "install.ps1 | iex" if os.name == "nt" else "install.sh | bash"
    assert antigravity.install_command().startswith(("irm https://antigravity.google/cli/", "curl -fsSL https://antigravity.google/cli/"))
    assert antigravity.install_command().endswith(expected_ag)
    assert claude_code.install_command().endswith(expected_ag)
    assert "claude.ai/install" in claude_code.install_command()


def test_the_sign_in_check_never_waits_for_the_console(monkeypatch):
    seen = {}
    monkeypatch.setattr(antigravity, "find_cli", lambda: Path("agy"))

    def fake_run(argv, **options):
        seen.update(options)
        raise subprocess.TimeoutExpired(argv, 20)

    monkeypatch.setattr(antigravity.subprocess, "run", fake_run)
    assert antigravity._model_probe() == {"models": [], "signed_in": False}
    assert seen["stdin"] is subprocess.DEVNULL


def test_a_missing_agy_says_exactly_how_to_install_and_sign_in(monkeypatch):
    monkeypatch.setattr(antigravity, "find_cli", lambda: None)
    with pytest.raises(ValueError) as error:
        antigravity.run("Reply", {"type": "object"})
    assert antigravity.install_command() in str(error.value)
    assert "sign in" in str(error.value)


def test_claude_code_inside_another_editor_is_found(home):
    assert claude_code.find_cli() is None
    name = "claude.exe" if os.name == "nt" else "claude"
    older = executable(home / ".cursor/extensions/anthropic.claude-code-2.1.300-x64/resources/native-binary" / name)
    newer = executable(home / ".antigravity/extensions/anthropic.claude-code-2.1.400-x64/resources/native-binary" / name)
    assert claude_code.find_cli() in {older, newer}
    # The native installer's own copy wins over any editor's bundled one.
    native = executable(home / ".local" / "bin" / name)
    assert claude_code.find_cli() == native


@pytest.mark.skipif(os.name != "nt", reason="Windows can only start the .exe")
def test_a_bare_unix_script_is_never_chosen_on_windows(home):
    executable(home / ".local" / "bin" / "claude")
    assert claude_code.find_cli() is None


def test_setup_status_carries_the_install_command_and_sign_in_step(monkeypatch):
    for module in (claude_code, antigravity, codex, kimi_cli):
        monkeypatch.setattr(module, "available", lambda: False)
    monkeypatch.setattr(antigravity, "desktop_only", lambda: True)
    monkeypatch.setattr("backend.ai.ready_providers", lambda root: {})
    rows = {row["id"]: row for row in ai_settings.machine_status()["clis"]}
    assert [row for row in rows] == ["claude_code", "antigravity", "codex", "kimi_cli"]
    assert rows["claude_code"]["install"] == claude_code.install_command()
    assert rows["claude_code"]["sign_in"] == claude_code.SIGN_IN
    assert rows["antigravity"]["install"] == antigravity.install_command()
    assert rows["antigravity"]["app_only"] is True
    assert rows["claude_code"]["app_only"] is False
    assert rows["codex"]["install"] == ""


def test_the_launcher_never_asks_without_someone_at_the_window(monkeypatch):
    monkeypatch.setattr(bootstrap, "OFFER_INSTALLS", False)
    assert bootstrap.ask("Install?") is False
    monkeypatch.setattr(bootstrap, "OFFER_INSTALLS", True)
    monkeypatch.setenv("CAREER_NO_INSTALL", "1")
    assert bootstrap.ask("Install?") is False


def test_node_is_installed_only_after_a_yes(monkeypatch, tmp_path):
    folder = tmp_path / "nodejs"
    calls = []
    monkeypatch.setattr(bootstrap, "node_installer", lambda: (["winget", "install", "node"], [str(folder)]))
    monkeypatch.setattr(bootstrap, "run", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(bootstrap, "ask", lambda question: False)
    assert bootstrap.install_node() is False and calls == []

    monkeypatch.setattr(bootstrap, "ask", lambda question: True)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: str(folder / name) if name == "node" else None)
    monkeypatch.setenv("PATH", "elsewhere")
    assert bootstrap.install_node() is True
    assert calls == [["winget", "install", "node"]]
    assert os.environ["PATH"].split(os.pathsep)[0] == str(folder)


def test_the_launcher_report_names_each_ai_app_and_how_to_add_the_missing_ones(monkeypatch, capsys):
    monkeypatch.setattr(claude_code, "available", lambda: True)
    for module in (antigravity, codex, kimi_cli):
        monkeypatch.setattr(module, "available", lambda: False)
    bootstrap.report_ai({"claude_code": True, "anthropic": True, "auto": True})
    out = capsys.readouterr().out
    assert "Claude Code: found" in out
    assert "Antigravity: not found. To add it" in out and antigravity.install_command() in out
    assert "Codex" not in out  # no install line to offer, so it is not listed when missing
    assert "API keys: " in out
    assert "No AI is ready yet" not in out
