"""The local Codex runtime as a structured-generation provider for the specialists.

The Codex counterpart of ``claude_code.py``: it runs the ``codex`` CLI that ships
with the ChatGPT app, signed in to the user's ChatGPT plan, so a call needs no
API key. Each call is one-shot and sealed: an empty working directory, read-only
sandbox, no shell tool, no apps, and web search only when the specialist asks.

``services/agents.py`` keeps its own Codex command for the durable agent runs
(it adds the Gmail connector for the mailbox worker). This module exists so the
chat specialists — the assistant's agent loop, the resume tailor, the profile
curator — can run on Codex too, which the specialist team could not do before.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from backend.ai import limits

ID = "codex"
LABEL = "Codex (local, free)"
# "codex-runtime" means no ``-m`` flag: Codex runs its own default model. The
# models her ChatGPT plan offers are added from the CLI's cache (``models()``).
MODELS = ("codex-runtime",)
DEFAULTS = {"strong": "codex-runtime", "cheap": "codex-runtime"}
BUNDLED = "/Applications/ChatGPT.app/Contents/Resources/codex"
# Windows: the Codex app and the ChatGPT extension for VS Code each carry the CLI
# without putting it on PATH. Each pattern may match several versions; the newest wins.
BUNDLED_WINDOWS = (
    "~/AppData/Local/OpenAI/Codex/bin/*/codex.exe",
    "~/.vscode/extensions/openai.chatgpt-*/bin/windows-x86_64/codex.exe",
)
TIMEOUT = {"web": 900, "text": 600}


def find_cli() -> Path | None:
    """The Codex binary to run, or None when the ChatGPT app is not installed."""
    override = os.environ.get("CODEX_CLI")
    if override and os.access(override, os.X_OK):
        return Path(override)
    on_path = shutil.which("codex")
    if on_path:
        return Path(on_path)
    if os.access(BUNDLED, os.X_OK):
        return Path(BUNDLED)
    for pattern in BUNDLED_WINDOWS:
        matches = [m for m in glob.glob(os.path.expanduser(pattern)) if os.path.isfile(m)]
        if matches:
            return Path(max(matches, key=os.path.getmtime))
    return None


def _home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def signed_in() -> bool:
    """Whether the CLI holds a ChatGPT sign-in (``codex login`` writes auth.json)."""
    return os.path.exists(os.path.join(_home(), "auth.json"))


def listed_models() -> list[dict]:
    """The models her ChatGPT plan offers Codex, in Codex's own order (most capable first).

    Codex keeps this list in ``models_cache.json`` and refreshes it from the
    server whenever the app runs, so it follows the plan without a code change.
    Hidden entries are left out; a missing or unreadable cache means none.
    """
    try:
        data = json.loads(Path(_home(), "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = [row for row in (data.get("models") or []) if isinstance(row, dict)
            and row.get("slug") and row.get("visibility", "list") == "list"]
    rows.sort(key=lambda row: row.get("priority", 999))
    return [{"id": row["slug"], "label": row.get("display_name") or row["slug"],
             "hint": row.get("description") or ""} for row in rows]


def models() -> tuple:
    """Every model a call may name: the default plus the plan's listed models."""
    return MODELS + tuple(row["id"] for row in listed_models() if row["id"] not in MODELS)


def failure_reason(stderr: str, stdout: str = "") -> str:
    """The one line worth showing from a failed ``codex exec``.

    Codex prints a banner (version, workdir, model) before anything goes wrong and
    reports API errors as JSON blobs, so the last error message is the reason;
    the banner never is. A sign-in problem is named as such.
    """
    text = (stderr or "") + "\n" + (stdout or "")
    messages = re.findall(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    reason = messages[-1] if messages else ""
    if not reason:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        errors = [line for line in lines if re.match(r"(?i)^(error|fatal|failed)\b", line)]
        reason = errors[-1] if errors else ""
    reason = " ".join(reason.replace("\\n", " ").split())
    # The plan's usage window ("You've hit your usage limit ... try again in 2 hours"):
    # named as a limit, with the reset time kept, so the router rests Codex until then.
    if limits.is_limit(reason) or re.search(r"(?i)usage.?limit", text):
        return limits.with_hint("Your ChatGPT plan's usage limit is reached. Wait for it to reset, then retry.",
                                reason or text)
    if re.search(r"(?i)not logged in|\blog ?in\b|unauthori[sz]ed|\b401\b|\bauth(entication|orization)?\b", reason):
        return "Codex is not signed in on this machine: open the ChatGPT app and sign in, then retry."
    return reason[:300]


def available() -> bool:
    return find_cli() is not None


def launcher(cli: Path) -> list[str]:
    """Return a shell-free launcher for a native binary or Windows command shim."""
    if os.name == "nt" and cli.suffix.casefold() in {".cmd", ".bat"}:
        return [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", str(cli)]
    return [str(cli)]


def strict_schema(schema: dict) -> dict:
    """A copy of a pydantic JSON schema in the shape strict structured-output APIs accept.

    Every object closes with ``additionalProperties: false`` and lists all of its
    properties as required; ``default`` markers go, because the model must write
    every field. Optional fields keep their ``null`` alternative, so an empty
    value is still expressible.
    """
    def walk(node):
        if isinstance(node, dict):
            node = {key: walk(value) for key, value in node.items() if key != "default"}
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            return node
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node
    return walk(schema)


def model_flag(model: str | None) -> list:
    """``-m <model>`` for a chosen plan model; nothing for the default."""
    return ["-m", model] if model and model not in MODELS else []


def command(cli: Path, folder: Path, schema_file: Path, out: Path, *, web: bool,
            model: str | None = None) -> list:
    """The exact argument list, kept separate so tests can check it."""
    return launcher(cli) + [
        "exec",
        "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
        *model_flag(model),
        "-C", str(folder),
        "-s", "read-only",
        "-c", "features.shell_tool=false",
        "-c", "apps._default.destructive_enabled=false",
        "-c", "apps._default.open_world_enabled=false",
        "-c", "features.apps=false",
        "-c", f'web_search="{"live" if web else "disabled"}"',
        "--output-schema", str(schema_file),
        "-o", str(out),
        "-",
    ]


def run(prompt: str, schema: dict, *, model: str = "codex-runtime", web: bool = False,
        system: str | None = None, timeout: int | None = None) -> tuple[dict, dict]:
    """One structured call. Returns (parsed object, token usage) or raises ValueError."""
    cli = find_cli()
    if cli is None:
        raise ValueError("Codex is not installed on this machine. Install the ChatGPT app, sign in, then retry.")
    if model not in models():
        raise ValueError("Unsupported Codex model")
    # Codex exec has no system-prompt flag: the instructions travel first on stdin.
    text = (system.strip() + "\n\n---\n\n" if system else "") + prompt
    limit = timeout or TIMEOUT["web" if web else "text"]
    with tempfile.TemporaryDirectory(prefix="career-codex-") as temp:
        folder = Path(temp)
        schema_file, out = folder / "schema.json", folder / "result.json"
        schema_file.write_text(json.dumps(strict_schema(schema)), encoding="utf-8")
        try:
            # Codex reads and writes UTF-8. Left to the default, Windows would use its
            # ANSI code page: a dash in a job title then fails as "input is not valid
            # UTF-8", and a curly quote in the answer fails to decode.
            done = subprocess.run(
                command(cli, folder, schema_file, out, web=web, model=model),
                input=text, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=limit, cwd=folder,
            )
        except subprocess.TimeoutExpired:
            raise ValueError("Codex reached its time limit. Nothing was saved from this call. Retry a smaller pass.") from None
        if done.returncode or not out.exists():
            said = failure_reason(done.stderr, done.stdout)
            raise ValueError("Codex could not finish" + (f": {said}" if said else "")
                             + " (open the ChatGPT app, check sign-in and usage, then retry)")
        try:
            result = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise ValueError("Codex returned output that did not match the requested schema") from None
    if not isinstance(result, dict):
        raise ValueError("Codex returned output that did not match the requested schema")
    return result, {"input_tokens": None, "output_tokens": None}


def invoke(prompt: str, schema: dict, **options) -> dict:
    """The provider-gateway shape: just the parsed object."""
    return run(prompt, schema, **options)[0]
