"""The local Claude Code runtime as a structured-generation provider.

This is the Claude counterpart of the Codex path in ``services/agents.py``: it
runs the ``claude`` CLI that ships with the Claude desktop app or the Claude
Code extension. That CLI is signed in to the user's Claude subscription, so
every call counts against the plan's usage limits and needs no API key.

Each call is one-shot and sealed off from the rest of this Mac: no session is
kept, no settings, hooks, MCP servers, plugins or CLAUDE.md files are read, and
the only tools the model may use are web search and fetch, and only when the
action asks for them. The Codex path is not touched by any of this.
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

ID = "claude_code"
LABEL = "Claude Code (local, subscription)"
# Aliases the CLI resolves to the current model of each family, so the list
# does not go stale when a new release ships.
MODELS = ("sonnet", "opus", "haiku")
DEFAULTS = {"strong": "sonnet", "cheap": "haiku"}
NOTE = ("Runs through the Claude Code CLI on this machine, signed in to your Claude "
        "subscription. No API key; calls count against the plan's usage limits.")

# Where the CLI lives when it is not on PATH. Each pattern may match several
# versions; the newest wins.
BUNDLED = (
    "~/.claude/local/claude",
    "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude",
    # Windows: the VS Code extension's binary and the native installer's.
    "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe",
    "~/.local/bin/claude.exe",
    "~/Library/Application Support/Claude/claude-code/*/claude.app/Contents/MacOS/claude",
)

# Seconds before a call is abandoned. Web research takes longer than a rewrite.
TIMEOUT = {"web": 900, "text": 600}

# The nested-session markers this process may carry when it was itself started
# from inside Claude Code. The child must start as a plain, fresh CLI.
_INHERITED = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
              "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET",
              "CLAUDE_CODE_MESSAGING_TOKEN")


def _version(path: str) -> tuple:
    found = re.findall(r"\d+(?:\.\d+)+", path)
    return tuple(int(part) for part in found[-1].split(".")) if found else ()


def find_cli() -> Path | None:
    """The CLI binary to run, or None when Claude Code is not installed."""
    override = os.environ.get("CLAUDE_CODE_CLI")
    if override and os.access(override, os.X_OK):
        return Path(override)
    on_path = shutil.which("claude")
    if on_path:
        return Path(on_path)
    for pattern in BUNDLED:
        matches = [m for m in glob.glob(os.path.expanduser(pattern)) if os.access(m, os.X_OK)]
        if matches:
            return Path(max(matches, key=_version))
    return None


def available() -> bool:
    return find_cli() is not None


def command(cli: Path, schema: dict, *, model: str, web: bool, system: str | None) -> list:
    """The exact argument list, kept separate so tests can check it."""
    tools = ["WebSearch", "WebFetch"] if web else []
    cmd = [
        str(cli), "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--model", model,
        # Restricted mode drops the code-running tools and every settings file;
        # --tools then names the only tools left, and they are pre-approved so
        # a permission prompt (which nobody is there to answer) never fires.
        "--restricted",
        "--tools", ",".join(tools),
        "--permission-prompts", "none",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    if tools:
        cmd += ["--allowedTools", *tools]
    if system:
        cmd += ["--system-prompt", system]
    return cmd


def describe_failure(data: dict) -> str:
    """Why a finished-but-failed call failed, in words the user can act on."""
    status = data.get("api_error_status")
    raw = str(data.get("result") or data.get("error") or "")
    text = raw.casefold()
    if status == 401 or "not logged in" in text or "please run /login" in text or "authentication" in text:
        return "Claude Code is not signed in. Open the Claude app or run `claude` once and sign in."
    if status == 429 or limits.is_limit(text):
        # Keep "resets 5:40pm (America/Chicago)" so the router rests Claude until then.
        return limits.with_hint("Your Claude subscription's usage limit is reached. Wait for it to reset, then retry.", raw)
    if data.get("subtype") == "error_max_turns":
        return "Claude Code stopped before finishing the task. Retry a smaller pass."
    if status:
        return f"Claude Code's request failed with HTTP {status}."
    # Unknown failure: say what the CLI said, so the next person can act on it.
    detail = " ".join(str(data.get("result") or data.get("error") or data.get("errors") or "").split())[:200]
    subtype = data.get("subtype") or "no result"
    return f"Claude Code could not finish ({subtype}){': ' + detail if detail else ''}. Retry; if it repeats, open the Claude app and check sign-in and usage."


# Seen a few times on long prompts (the profile comparison) and once as the
# subscription limit was reached: the CLI reports success but the object is a
# stub ("summary": "test", "report": "test report"), which the app would then
# cache as a finished report. A report that short is never a real one; a second
# attempt has produced the real thing each time, so one retry is built in.
PLACEHOLDER_REPORT_CHARS = 120
PLACEHOLDER_RETRIES = 1


def placeholder(result: dict, schema: dict) -> bool:
    """True when a schema that asks for prose got a stub back."""
    properties = schema.get("properties") or {}
    if "report" in properties and "report" in (schema.get("required") or []):
        return len(str(result.get("report") or "").strip()) < PLACEHOLDER_REPORT_CHARS
    return False


def run(prompt: str, schema: dict, *, model: str = "sonnet", web: bool = False,
        system: str | None = None, timeout: int | None = None) -> tuple[dict, dict]:
    """One structured call. Returns (parsed object, token usage) or raises ValueError."""
    for attempt in range(PLACEHOLDER_RETRIES + 1):
        try:
            return _run_once(prompt, schema, model=model, web=web, system=system, timeout=timeout)
        except PlaceholderResult:
            if attempt == PLACEHOLDER_RETRIES:
                raise ValueError(
                    "Claude Code returned a placeholder instead of a report, twice. "
                    "Nothing was saved. Retry later or run this step on another provider."
                ) from None


class PlaceholderResult(Exception):
    """The CLI finished but the model returned a stub instead of the report."""


def _run_once(prompt: str, schema: dict, *, model: str, web: bool, system: str | None,
              timeout: int | None) -> tuple[dict, dict]:
    cli = find_cli()
    if cli is None:
        raise ValueError(
            "Claude Code is not installed on this machine. Install the Claude app or the "
            "Claude Code extension, sign in, then retry."
        )
    if model not in MODELS:
        raise ValueError("Unsupported Claude Code model")
    env = {name: value for name, value in os.environ.items() if name not in _INHERITED}
    limit = timeout or TIMEOUT["web" if web else "text"]
    # An empty working directory: nothing for the file tools to read.
    with tempfile.TemporaryDirectory(prefix="career-claude-") as folder:
        try:
            # The prompt travels on stdin, never inside a shell command. Claude Code reads and
            # writes UTF-8; Windows' ANSI default would fail on a "→" in a posting.
            done = subprocess.run(
                command(cli, schema, model=model, web=web, system=system),
                input=prompt, text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=limit, cwd=folder, env=env,
            )
        except subprocess.TimeoutExpired:
            raise ValueError(
                "Claude Code reached its time limit. Nothing was saved from this call. "
                "Retry a smaller pass."
            ) from None
    try:
        data = json.loads(done.stdout or "")
    except json.JSONDecodeError:
        said = " ".join((done.stderr or done.stdout or "").split())[:200]
        if limits.is_limit(said):
            raise ValueError(limits.with_hint(
                "Your Claude subscription's usage limit is reached. Wait for it to reset, then retry.", said)) from None
        raise ValueError(
            "Claude Code did not return a result"
            + (f" ({said})" if said else "")
            + ". Check the Claude app is signed in and retry."
        ) from None
    if done.returncode or data.get("is_error") or data.get("subtype") != "success":
        raise ValueError(describe_failure(data))
    result = data.get("structured_output")
    if not isinstance(result, dict):
        raise ValueError("Claude Code returned output that did not match the requested schema")
    if placeholder(result, schema):
        raise PlaceholderResult()
    used = data.get("usage") or {}
    return result, {"input_tokens": used.get("input_tokens"), "output_tokens": used.get("output_tokens")}


def invoke(prompt: str, schema: dict, **options) -> dict:
    """The provider-gateway shape: just the parsed object."""
    return run(prompt, schema, **options)[0]
