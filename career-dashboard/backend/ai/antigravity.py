"""Google Antigravity's signed-in `agy` CLI as a no-API-key provider."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from backend.ai import limits

ID = "antigravity"
LABEL = "Antigravity (local, Google plan)"
MODELS = ("antigravity-runtime",)
DEFAULTS = {"strong": MODELS[0], "cheap": MODELS[0]}
NOTE = ("Runs through the Antigravity CLI on this machine, using its shared Google sign-in. "
        "No API key. Live web work needs separately granted Antigravity URL permissions or the MCP connector.")
TIMEOUT = {"web": 900, "text": 600}
_PROBE = {"at": 0.0, "models": [], "signed_in": False}

# The official installer's binary folders (antigravity.google/docs/getting-started, CLI tab):
# %LOCALAPPDATA%\agy\bin on Windows, ~/.local/bin elsewhere. They are searched as well as PATH,
# because a dashboard started before the install still has the PATH it started with. A plain
# literal: `career doctor` reads it without importing the app.
BUNDLED = (
    "~/AppData/Local/agy/bin/agy.exe",
    "~/AppData/Local/agy/bin/agy.cmd",
    "~/.local/bin/agy.exe",
    "~/.local/bin/agy",
)
INSTALL = {"windows": "irm https://antigravity.google/cli/install.ps1 | iex",
           "other": "curl -fsSL https://antigravity.google/cli/install.sh | bash"}
SIGN_IN = "Run `agy` once in a terminal and sign in with your Google account."
# The desktop app alone does not include the `agy` command-line tool the dashboard runs.
DESKTOP_APP = ("~/AppData/Local/Programs/Antigravity/Antigravity.exe", "/Applications/Antigravity.app",
               "~/Applications/Antigravity.app")


def install_command() -> str:
    return INSTALL["windows" if os.name == "nt" else "other"]


def desktop_only() -> bool:
    """The Antigravity desktop app is here but its command-line tool is not."""
    if find_cli() is not None:
        return False
    return any(os.path.exists(os.path.expanduser(place)) for place in DESKTOP_APP)


def find_cli() -> Path | None:
    override = os.environ.get("AGY_CLI")
    if override and os.path.isfile(override):
        return Path(override)
    found = shutil.which("agy")
    if found:
        return Path(found)
    for place in BUNDLED:
        # Windows can only start the .exe or .cmd; a bare `agy` there is a script for another system.
        if os.name == "nt" and not place.endswith((".exe", ".cmd")):
            continue
        if os.path.isfile(os.path.expanduser(place)):
            return Path(os.path.expanduser(place))
    return None


def launcher(cli: Path) -> list[str]:
    if os.name == "nt" and cli.suffix.casefold() in {".cmd", ".bat"}:
        return [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", str(cli)]
    return [str(cli)]


def available() -> bool:
    return find_cli() is not None


def _model_probe() -> dict:
    cli = find_cli()
    if cli is None:
        return {"models": [], "signed_in": False}
    try:
        # No console input: a check that needs a sign-in must fail fast, never wait on the dashboard's window.
        done = subprocess.run(
            launcher(cli) + ["models"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"models": [], "signed_in": False}
    text = (done.stdout or "") + "\n" + (done.stderr or "")
    if done.returncode or re.search(r"(?i)authentication required|not (?:signed|logged) in|\b401\b", text):
        return {"models": [], "signed_in": False}
    listed = []
    try:
        data = json.loads(done.stdout or "")
        rows = data if isinstance(data, list) else data.get("models") or []
        for row in rows:
            value = row.get("id") or row.get("slug") or row.get("name") if isinstance(row, dict) else str(row)
            if value:
                listed.append(str(value))
    except (ValueError, AttributeError):
        for line in (done.stdout or "").splitlines():
            line = line.strip().lstrip("*-• ")
            if not line or re.search(r"(?i)^(?:available )?models?\b|^\s*slug\b|^[-+ ]+$", line):
                continue
            token = line.split()[0]
            if re.fullmatch(r"[a-zA-Z0-9]+(?:[._-][a-zA-Z0-9]+)+", token):
                listed.append(token)
    return {"models": list(dict.fromkeys(listed)), "signed_in": True}


def _probe(refresh: bool = False) -> dict:
    if refresh or time.monotonic() - _PROBE["at"] > 30:
        _PROBE.update(_model_probe(), at=time.monotonic())
    return _PROBE


def signed_in() -> bool:
    return bool(_probe()["signed_in"])


def models() -> tuple:
    return MODELS + tuple(model for model in _probe()["models"] if model not in MODELS)


def command(cli: Path, schema_file: Path, *, model: str, timeout: int) -> list[str]:
    chosen = ["--model", model] if model and model not in MODELS else []
    return launcher(cli) + [
        *chosen,
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--json-schema", str(schema_file),
        "--print-timeout", f"{timeout}s",
        "--disable-slash-commands",
        "--sandbox",
    ]


def describe_failure(text: str) -> str:
    folded = text.casefold()
    if re.search(r"usage limit|quota|rate limit|\b429\b|credits?", folded):
        return limits.with_hint("Your Antigravity/Google plan's usage limit is reached. Wait for it to reset, then retry.", text)
    if re.search(r"authentication required|not (?:signed|logged) in|unauthori[sz]ed|\b401\b", folded):
        return "Antigravity is not signed in. " + SIGN_IN + " Then retry."
    detail = " ".join(text.split())[:260]
    return "Antigravity could not finish" + (f": {detail}" if detail else ". Check sign-in and usage, then retry.")


def _terminal(stdout: str) -> dict:
    result = None
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "result" and isinstance(event.get("result"), dict):
            result = event["result"]
        elif "status" in event and "structured_output" in event:
            result = event
    return result or {}


def run(prompt: str, schema: dict, *, model: str = MODELS[0], web: bool = False,
        system: str | None = None, timeout: int | None = None) -> tuple[dict, dict]:
    cli = find_cli()
    if cli is None:
        raise ValueError("The Antigravity command-line tool (agy) is not installed. Install it in a terminal "
                         f"with `{install_command()}`, then {SIGN_IN[0].lower() + SIGN_IN[1:]}")
    if web:
        raise ValueError("Antigravity web work needs scoped URL permissions. Use the MCP connector or another enabled plan for this step.")
    if model not in models():
        raise ValueError("Unsupported Antigravity model")
    seconds = timeout or TIMEOUT["text"]
    full = (system.strip() + "\n\n---\n\n" if system else "") + prompt
    message = json.dumps({"event": "user", "message": {"content": full}}, ensure_ascii=False) + "\n"
    with tempfile.TemporaryDirectory(prefix="career-antigravity-") as folder:
        schema_file = Path(folder) / "schema.json"
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        try:
            done = subprocess.run(
                command(cli, schema_file, model=model, timeout=seconds),
                input=message, text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=seconds + 15, cwd=folder,
            )
        except subprocess.TimeoutExpired:
            raise ValueError("Antigravity reached its time limit. Nothing was saved from this call. Retry a smaller pass.") from None
        except OSError as error:
            raise ValueError(f"Antigravity could not be started: {error}") from None
    result = _terminal(done.stdout or "")
    raw = "\n".join([done.stderr or "", done.stdout or "", str(result.get("error") or "")])
    if done.returncode or not result or str(result.get("status", "")).upper() != "SUCCESS":
        raise ValueError(describe_failure(raw))
    answer = result.get("structured_output")
    if not isinstance(answer, dict):
        raise ValueError("Antigravity returned output that did not match the requested schema")
    usage = result.get("usage") or {}
    return answer, {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")}


def invoke(prompt: str, schema: dict, **options) -> dict:
    from backend import telemetry

    result, usage = run(prompt, schema, **options)
    telemetry.record_usage((usage or {}).get("input_tokens"), (usage or {}).get("output_tokens"))
    return result
