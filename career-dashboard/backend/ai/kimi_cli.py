"""The local Kimi Code runtime as a structured-generation provider.

The Kimi counterpart of ``claude_code.py``: it runs the ``kimi`` CLI (Kimi Code
2.x), signed in to the user's Kimi membership through OAuth, so every call
counts against the membership's usage limits and needs no API key.

Each call is one-shot: ``kimi -p`` runs a single prompt non-interactively in a
fresh session, with tools handled under the CLI's automatic permission policy —
that is what gives research and discovery their web search. The CLI has no
schema flag, so the schema travels inside the prompt and the answer is the last
assistant line of the ``stream-json`` output, parsed as JSON. The prompt rides
on the command line; past Windows' safe command-line length it is written to a
file in the sealed working directory and the CLI is asked to read it. The
stream itself is captured through a file, not a pipe: on Windows the CLI loses
everything it buffered for a pipe when a long run exits (observed: a completed
six-minute research turn, zero bytes on stdout).
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from backend.ai import limits

ID = "kimi_cli"
LABEL = "Kimi Code (local, membership)"
# "kimi-runtime" means no ``--model`` flag: the CLI's own ``default_model`` is
# used. The other models her membership offers are read from the CLI's
# config.toml (``models()``), so the list does not go stale when Kimi ships a
# new one. Same convention as Codex's "codex-runtime".
MODELS = ("kimi-runtime",)
DEFAULTS = {"strong": "kimi-runtime", "cheap": "kimi-runtime"}
NOTE = ("Runs through the Kimi Code CLI on this machine, signed in to your Kimi "
        "membership. No API key; calls count against the membership's usage limits.")
LIMIT_MESSAGE = "Your Kimi membership's usage limit is reached. Wait for the window to reset, then retry."

# Where the CLI lives when it is not on PATH.
BUNDLED = (
    "~/.kimi-code/bin/kimi.exe",
    "~/.kimi-code/bin/kimi.cmd",
    "~/.kimi-code/bin/kimi",
)
# The VS Code extension runs Kimi inside the editor and installs no CLI.
EXTENSION = "~/.vscode/extensions/moonshot-ai.kimi-code-*"
INSTALL_COMMAND = "irm https://code.kimi.com/kimi-code/install.ps1 | iex"

# Seconds before a call is abandoned. Web research takes longer than a rewrite.
TIMEOUT = {"web": 900, "text": 600}

# Past this many characters the prompt goes to a file instead of the command
# line, which on Windows is limited to about 32k characters.
INLINE_LIMIT = 20000

SCHEMA_INSTRUCTION = (
    "Respond with only one JSON object that matches this JSON Schema. "
    "No markdown fences, no commentary, nothing before or after the object:\n"
)

FILE_HANDOFF = (
    "Your instructions are in the file prompt.md in the current working "
    "directory. Read that file first and follow it exactly. Your final message "
    "must be only the JSON object the instructions ask for."
)


def find_cli() -> Path | None:
    """The CLI binary to run, or None when Kimi Code is not installed."""
    override = os.environ.get("KIMI_CLI")
    if override and os.path.exists(override):
        return Path(override)
    on_path = shutil.which("kimi")
    if on_path:
        return Path(on_path)
    for pattern in BUNDLED:
        candidate = os.path.expanduser(pattern)
        if os.path.exists(candidate):
            return Path(candidate)
    return None


def available() -> bool:
    return find_cli() is not None


def extension_only() -> bool:
    """True when Kimi Code for VS Code is installed but the terminal CLI this app runs is not."""
    return not available() and bool(glob.glob(os.path.expanduser(EXTENSION)))


def _home() -> str:
    return os.environ.get("KIMI_CODE_HOME") or os.path.join(os.path.expanduser("~"), ".kimi-code")


def signed_in() -> bool:
    """Whether the CLI holds a Kimi membership sign-in (OAuth credentials file)."""
    return os.path.exists(os.path.join(_home(), "credentials", "kimi-code.json"))


def _config() -> dict:
    """The CLI's config.toml (shared with the VS Code extension), or {} when unreadable."""
    try:
        return tomllib.loads(Path(_home(), "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def listed_models() -> list[dict]:
    """The models her membership offers, from config.toml, the CLI's default first.

    Each entry's ``id`` is the alias ``--model`` takes (e.g. ``kimi-code/k3``);
    ``default`` marks the one the CLI uses when no model is named.
    """
    config = _config()
    default = config.get("default_model")
    rows = [{"id": alias, "label": spec.get("display_name") or spec.get("model") or alias,
             "default": alias == default, "context": spec.get("max_context_size")}
            for alias, spec in (config.get("models") or {}).items() if isinstance(spec, dict)]
    return sorted(rows, key=lambda row: not row["default"])


def models() -> tuple:
    """Every model a call may name: the default plus the membership's listed models."""
    return MODELS + tuple(row["id"] for row in listed_models() if row["id"] not in MODELS)


def launcher(cli: Path) -> list[str]:
    """Return a shell-free launcher for a native binary or Windows command shim."""
    if os.name == "nt" and cli.suffix.casefold() in {".cmd", ".bat"}:
        return [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/c", str(cli)]
    return [str(cli)]


def command(cli: Path, prompt: str, model: str | None = None) -> list:
    """The exact argument list, kept separate so tests can check it.

    Print mode runs non-interactively and answers permission prompts on its
    own, so no approval flag is needed; ``--yolo``/``--auto`` are rejected in
    combination with ``-p`` and are never passed. A named model goes in as
    ``--model <alias>``; the default needs no flag.
    """
    chosen = ["--model", model] if model and model not in MODELS else []
    return launcher(cli) + [*chosen, "-p", prompt, "--output-format", "stream-json"]


def payload(prompt: str, schema: dict, system: str | None) -> str:
    """System text first, then the task, then the schema instruction last."""
    parts = []
    if system:
        parts.append(system.strip())
    parts.append(prompt)
    parts.append(SCHEMA_INSTRUCTION + json.dumps(schema))
    return "\n\n---\n\n".join(parts)


def _wire_error(folder_name: str) -> str:
    """The real reason a turn failed, from the CLI's own session log.

    Print mode writes nothing to stdout or stderr when the turn itself fails
    (a quota stop, an auth error), but every session keeps a wire log under
    ``<home>/sessions/wd_<cwd-slug>_*/``. Best effort only: any problem reading
    it falls back to the generic message.
    """
    try:
        home = os.environ.get("KIMI_CODE_HOME") or os.path.join(os.path.expanduser("~"), ".kimi-code")
        slug = re.sub(r"[^a-z0-9]+", "-", folder_name.casefold()).strip("-")
        candidates = sorted((Path(home) / "sessions").glob(f"wd_{slug}_*"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        for candidate in candidates[:2]:
            wires = sorted(candidate.glob("*/agents/main/wire.jsonl"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            if not wires:
                continue
            lines = wires[0].read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
            for line in reversed(lines):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") not in ("turn.ended", "turn.step.interrupted"):
                    continue
                message = str(record.get("message") or (record.get("error") or {}).get("message") or "")
                if message and message != "[object Object]":
                    return " ".join(message.split())[:260]
    except OSError:
        pass
    return ""


def describe_failure(stderr: str, stdout: str, returncode: int, folder_name: str = "") -> str:
    """Why a failed call failed, in words the user can act on."""
    text = ((stderr or "") + "\n" + (stdout or "")).strip()
    full = text + "\n" + _wire_error(folder_name) if folder_name else text
    folded = full.casefold()
    if re.search(r"usage limit|quota|rate limit|\b429\b|out of (extra )?usage", folded):
        return limits.with_hint(LIMIT_MESSAGE, full)
    if re.search(r"(?i)\b401\b|\b403\b|unauthori[sz]ed|not logged in|sign[ -]?in|\blog ?in\b|authentication", folded):
        return "Kimi Code is not signed in on this machine: run `kimi login`, then retry."
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    detail = " ".join(lines[-1].split())[:200] if lines else ""
    return (f"Kimi Code could not finish (exit {returncode})"
            + (f": {detail}" if detail else "")
            + ". Retry; if it repeats, run `kimi doctor` and check sign-in and usage.")


def _answer(stdout: str) -> str:
    """The last assistant message in the stream-json lines is the result."""
    text = ""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("role") != "assistant":
            continue
        content = event.get("content")
        if isinstance(content, list):  # some builds send content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if isinstance(content, str) and content.strip():
            text = content
    return text


def _no_answer_error(stdout: str, stderr: str, folder_name: str = "") -> str:
    """An empty final answer must say what the CLI actually did, not fail silently."""
    wire = _wire_error(folder_name) if folder_name else ""
    if wire:
        if re.search(r"usage limit|quota|rate limit|\b429\b", wire.casefold()):
            return limits.with_hint(LIMIT_MESSAGE, wire)
        return "Kimi Code finished without a final answer: " + wire
    events = 0
    last = "none"
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events += 1
        tools = event.get("tool_calls") or []
        names = [((tool.get("function") or {}).get("name") or tool.get("name") or "?") for tool in tools]
        last = "/".join(str(part) for part in (event.get("role"), event.get("type")) if part) or "event"
        if names:
            last += " calling " + ",".join(names)
    tail = " ".join((stderr or "").split())[:160]
    return ("Kimi Code finished without a final answer"
            + (f" ({events} stream events, last: {last})" if events else " (no stream output)")
            + (f"; stderr: {tail}" if tail else "")
            + ". Retry; if it repeats, run this step on another provider.")


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(
                "Kimi Code did not return a JSON result"
                + (f" ({' '.join(text.split())[:160]})" if text.strip() else "")
                + ". Retry; if it repeats, run this step on another provider."
            ) from None
        try:
            result = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            raise ValueError("Kimi Code returned output that did not match the requested schema") from None
    if not isinstance(result, dict):
        raise ValueError("Kimi Code returned output that did not match the requested schema")
    return result


def run(prompt: str, schema: dict, *, model: str = "kimi-runtime", web: bool = False,
        system: str | None = None, timeout: int | None = None) -> tuple[dict, dict]:
    """One structured call. Returns (parsed object, token usage) or raises ValueError."""
    cli = find_cli()
    if cli is None:
        raise ValueError(
            "The Kimi Code terminal app is not installed on this machine (the VS Code "
            f"extension alone is not enough). In PowerShell run: {INSTALL_COMMAND} "
            "— it uses the same sign-in — then retry."
        )
    if model not in models():
        raise ValueError("Unsupported Kimi Code model")
    full = payload(prompt, schema, system)
    limit = timeout or TIMEOUT["web" if web else "text"]
    # An empty working directory: nothing for the file tools to read but the
    # handoff prompt, when one is needed.
    with tempfile.TemporaryDirectory(prefix="career-kimi-") as folder:
        session_hint = Path(folder).name
        argv_prompt = full
        if len(full) > INLINE_LIMIT:
            Path(folder, "prompt.md").write_text(full, encoding="utf-8")
            argv_prompt = FILE_HANDOFF
        out_file = Path(folder, "result.jsonl")
        try:
            with out_file.open("w", encoding="utf-8") as stream:
                # Kimi writes UTF-8; left to the default, Windows would decode its error
                # output with the ANSI code page and garble (or fail on) the reason.
                done = subprocess.run(
                    command(cli, argv_prompt, model),
                    text=True, encoding="utf-8", errors="replace", stdout=stream, stderr=subprocess.PIPE,
                    timeout=limit, cwd=folder,
                )
        except subprocess.TimeoutExpired:
            raise ValueError(
                "Kimi Code reached its time limit. Nothing was saved from this call. "
                "Retry a smaller pass."
            ) from None
        except OSError as error:
            raise ValueError(f"Kimi Code could not be started: {error}") from None
        stdout_text = out_file.read_text(encoding="utf-8", errors="replace")
    if done.returncode:
        raise ValueError(describe_failure(done.stderr, stdout_text, done.returncode, session_hint))
    text = _answer(stdout_text)
    if not text:
        raise ValueError(_no_answer_error(stdout_text, done.stderr, session_hint))
    return _extract_json(text), {"input_tokens": None, "output_tokens": None}


def invoke(prompt: str, schema: dict, **options) -> dict:
    """The provider-gateway shape: just the parsed object."""
    return run(prompt, schema, **options)[0]
