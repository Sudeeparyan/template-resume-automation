"""Server-side API key discovery.

Keys are read from, in order: the process environment, ``career-dashboard/.env``,
the repository-root ``.env``, and the repository-root ``keys.txt``. The last two
exist because keys are pasted there by hand, so a line carrying only the token is
accepted as well as ``NAME=value``; a bare token is matched to a provider by its
prefix. Values are never returned to the browser, logged, or written to SQLite.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

NAMES = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "MOONSHOT_API_KEY",
    "AZURE_OPENAI_API_KEY",
)
# Not secrets, but read the same way: where the Azure resource is and which deployments to use.
SETTINGS = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")
# Optional job-source keys (market/policy.py), read and saved the same way. CAREERJET_USER_IP is the
# public address the person registered with Careerjet, which its API requires on every call.
SOURCE_NAMES = ("CAREERJET_API_KEY", "CAREERJET_USER_IP", "JOOBLE_API_KEY")
IP_ADDRESS = re.compile(r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$|^[0-9A-Fa-f:]{2,39}$")
# Azure AI Foundry / Azure OpenAI keys: 84 characters with the JQQJ99 signature at a
# fixed place. One pasted as OPENAI_API_KEY would only fail against api.openai.com,
# so it is filed as the Azure key wherever it is found.
AZURE_KEY = re.compile(r"[A-Za-z0-9]{52}JQQJ99[A-Za-z0-9]{26}")

# Bare tokens are identified by the prefix each provider issues.
PREFIXES = (
    ("sk-or-v1-", "OPENROUTER_API_KEY"),
    ("sk-ant-", "ANTHROPIC_API_KEY"),
    ("sk-proj-", "OPENAI_API_KEY"),
    ("AIza", "GEMINI_API_KEY"),
    ("AQ.", "GEMINI_API_KEY"),
)

ASSIGNMENT = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _base(root: Path) -> Path:
    """Keys belong to the machine: a profile folder uses the app's (career-dashboard/)."""
    from backend.paths import secrets_root_for

    return secrets_root_for(root)


def _files(root: Path):
    """The key files to scan, nearest first. ``root`` is career-dashboard/ (or a profile in it)."""
    root = _base(root)
    return (root / ".env", root.parent / ".env", root.parent / "keys.txt")


def _parse(text: str) -> dict:
    found: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ASSIGNMENT.match(line)
        if match:
            name, value = match.group(1).strip(), match.group(2).strip().strip('"').strip("'")
            if name == "OPENAI_API_KEY" and AZURE_KEY.fullmatch(value):
                name = "AZURE_OPENAI_API_KEY"
            if value:
                found.setdefault(name, value)
            continue
        # A line holding only a token, as pasted from a provider's console.
        token = line.split()[0].strip('"').strip("'")
        if AZURE_KEY.fullmatch(token):
            found.setdefault("AZURE_OPENAI_API_KEY", token)
            continue
        for prefix, name in PREFIXES:
            if token.startswith(prefix) and len(token) > len(prefix) + 8:
                found.setdefault(name, token)
                break
    return found


def load(root: Path) -> dict:
    """Every key this machine exposes, nearest source winning."""
    found: dict = {name: os.environ[name] for name in NAMES + SETTINGS + SOURCE_NAMES if os.environ.get(name)}
    if AZURE_KEY.fullmatch(found.get("OPENAI_API_KEY", "")):
        found.setdefault("AZURE_OPENAI_API_KEY", found.pop("OPENAI_API_KEY"))
    for path in _files(root):
        try:
            if path.exists():
                for name, value in _parse(path.read_text(encoding="utf-8")).items():
                    found.setdefault(name, value)
        except OSError:
            continue
    return found


def secret(root: Path, name: str) -> str:
    return load(root).get(name, "")


def configured(root: Path) -> dict:
    """Which keys exist, without exposing any value. Safe to send to the client."""
    available = load(root)
    return {name: bool(available.get(name)) for name in NAMES}


# --- Keys entered on the Settings page -----------------------------------------
#
# The page writes to career-dashboard/.env only. It is git-ignored, it is the
# nearest file above, so a key saved here wins over an older one in the root
# .env or keys.txt, and those hand-kept files are never rewritten.

VALID_TOKEN = re.compile(r"^[A-Za-z0-9._\-]{16,400}$")


def managed_file(root: Path) -> Path:
    return _base(root) / ".env"


def source(root: Path, name: str) -> str | None:
    """Where the key in use comes from, as a label. Never the value."""
    if os.environ.get(name):
        return "environment"
    root = _base(root)
    labels = {root / ".env": "saved in the app", root.parent / ".env": "repository root .env",
              root.parent / "keys.txt": "repository root keys.txt"}
    for path in _files(root):
        try:
            if path.exists() and _parse(path.read_text(encoding="utf-8")).get(name):
                return labels[path]
        except OSError:
            continue
    return None


def _rewrite(path: Path, name: str, value: str | None) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept = [line for line in lines
            if not (ASSIGNMENT.match(line.strip()) and ASSIGNMENT.match(line.strip()).group(1) == name)]
    if value is not None:
        kept.append(f"{name}={value}")
    if not any(line.strip() for line in kept):
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(".env.saving")
    temporary.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def save(root: Path, name: str, value: str) -> None:
    """Store one key in career-dashboard/.env, replacing any earlier one there."""
    if name not in NAMES + SOURCE_NAMES:
        raise ValueError("Unknown API key name")
    value = (value or "").strip().strip('"').strip("'")
    if name == "CAREERJET_USER_IP":
        if not IP_ADDRESS.match(value):
            raise ValueError("That is not an IP address. Enter the public address you registered with Careerjet.")
    elif not VALID_TOKEN.match(value):
        raise ValueError("That does not look like an API key. Paste the whole key, with no spaces.")
    _rewrite(managed_file(root), name, value)


def remove(root: Path, name: str) -> str | None:
    """Delete the key saved by the app. Returns where another copy still comes from, if any."""
    if name not in NAMES + SOURCE_NAMES:
        raise ValueError("Unknown API key name")
    path = managed_file(root)
    if path.exists():
        _rewrite(path, name, None)
    return source(root, name)
