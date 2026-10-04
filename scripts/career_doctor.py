#!/usr/bin/env python3
"""Read-only JSON readiness for a fresh clone or an installed Career Workspace.

Uses only Python's standard library, installed package metadata, the profile
registry, and executable discovery. It never imports the backend, opens candidate
files, loads credentials, launches AI tools, installs packages, or creates files.
An installed executable is not proof of authentication or a working subscription.
"""

from __future__ import annotations

import argparse
import ast
import glob
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "career-dashboard"
PROFILE_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?\Z")
# These distributions cover the CLI's imports, including transitive dependencies
# used directly by setup. OCR packages are optional for ordinary text resumes.
CLI_PACKAGES = ("fastapi", "uvicorn", "PyYAML", "pypdf", "Pillow", "httpx", "pydantic",
                "langchain", "langgraph", "langchain-openai", "langchain-anthropic",
                "langchain-google-genai")
OCR_PACKAGES = ("rapidocr-onnxruntime", "pypdfium2")
# Dated Irish permit thresholds; read with the standard library like everything here.
PERMIT_RULES = APP / "backend/countries/ie/permit-rules.yml"
RULES_WARN_DAYS = 30
# The AI CLIs the app runs: the module that knows where each one's bundled copy lives (its
# source is read, never imported) and the environment variable that overrides it.
CLI_SOURCES = {"claude": (APP / "backend/ai/claude_code.py", "CLAUDE_CODE_CLI"),
               "codex": (APP / "backend/ai/codex.py", "CODEX_CLI"),
               "kimi": (APP / "backend/ai/kimi_cli.py", "KIMI_CLI")}
# Where Start Dashboard puts its pinned Tectonic download.
TOOLS = APP / "backend/.tools"


def permit_rules_status(today: date | None = None) -> dict:
    """When the Irish permit rules were last checked and when they are due for review."""
    today = today or date.today()
    try:
        text = PERMIT_RULES.read_text(encoding="utf-8")
    except OSError:
        try:
            label = str(PERMIT_RULES.relative_to(REPO))
        except ValueError:
            label = PERMIT_RULES.name
        return {"state": "missing", "file": label}
    except UnicodeError:
        return {"state": "unreadable"}
    fields = {}
    for key in ("version", "effective_from", "verified_at", "review_after"):
        found = re.search(rf"^{key}:\s*['\"]?([^'\"\s]+)", text, re.M)
        fields[key] = found.group(1) if found else ""
    try:
        effective = date.fromisoformat(fields["effective_from"])
        verified = date.fromisoformat(fields["verified_at"])
        review = date.fromisoformat(fields["review_after"])
        if not effective <= verified <= review or not fields["version"]:
            raise ValueError("Inconsistent permit-rule dates or missing version")
        days = (review - today).days
    except ValueError:
        return {"state": "unreadable", **fields}
    state = "stale" if today < effective or days < 0 else "due_soon" if days <= RULES_WARN_DAYS else "current"
    return {"state": state, **fields, "days_left": days}


def packages(names: tuple[str, ...]) -> dict:
    installed, missing = {}, []
    for name in names:
        try:
            installed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return {"ready": not missing, "installed": installed, "missing": missing,
            "check": "installed package metadata; imports and provider calls are not tested"}


def registry_status(folder: Path, requested: str | None = None) -> dict:
    """Read registry metadata only; a damaged list is never treated as empty."""
    result = {"profile_count": None, "profiles": [], "selected_profile": None,
              "last_used_profile": None, "registry_error": None}
    try:
        path = folder / "registry.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict) or not isinstance(data.get("profiles", []), list):
            raise ValueError("Invalid registry")
        profiles, seen = [], set()
        for entry in data.get("profiles", []):
            if not isinstance(entry, dict):
                raise ValueError("Invalid entry")
            pid, state = entry.get("id"), entry.get("state")
            if (not isinstance(pid, str) or not PROFILE_ID.fullmatch(pid) or pid in seen
                    or not isinstance(state, str) or state not in {"onboarding", "ready"}):
                raise ValueError("Invalid profile metadata")
            seen.add(pid)
            profiles.append({"id": pid, "state": state})
        result.update(profile_count=len(profiles), profiles=profiles)
        last = data.get("last_used")
        result["last_used_profile"] = last if isinstance(last, str) and last in seen else None
        # More than one person always needs an explicit selection in a new chat.
        result["selected_profile"] = (requested if requested in seen else None) if requested else (
            profiles[0]["id"] if len(profiles) == 1 else None)
        if requested and requested not in seen:
            result["selection_error"] = "The requested profile ID is not in the registry."
    except (OSError, ValueError, UnicodeError):
        result["registry_error"] = "The profile registry is unreadable or invalid; no profile was selected."
    return result


def executable(name: str) -> bool:
    # Report availability only. Absolute executable paths may contain a person's name.
    return shutil.which(name) is not None


def bundled_locations(source: Path) -> list[str]:
    """The app's own bundled-CLI locations (its BUNDLED constants), read from source without importing it."""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeError):
        return []
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith("BUNDLED") for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                continue
            found += [value] if isinstance(value, str) else [v for v in value if isinstance(v, str)]
    return found


def cli_available(name: str) -> bool:
    """On PATH, named by its override variable, or in a location the app knows (a desktop app's bundled copy)."""
    source, override = CLI_SOURCES[name]
    if executable(name):
        return True
    named = os.environ.get(override)
    if named and os.path.isfile(named):
        return True
    return any(glob.glob(os.path.expanduser(pattern)) for pattern in bundled_locations(source))


def tectonic_available() -> bool:
    return executable("tectonic") or any(TOOLS.glob("tectonic-*/tectonic*"))


def in_synced_folder(path: Path = REPO) -> bool:
    """True inside OneDrive: syncing a running app's databases can lock or damage them."""
    text = str(path).casefold()
    roots = [os.environ.get(name, "") for name in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
    return any(root and text.startswith(root.casefold()) for root in roots) or "onedrive" in (
        part.casefold().split(" -")[0] for part in path.parts)


def node_status() -> dict:
    node = shutil.which("node")
    result = {"available": bool(node), "supported": False, "version": None}
    if node:
        try:
            probe = subprocess.run([node, "--version"], capture_output=True, text=True,
                                   timeout=5, check=False)
            version = probe.stdout.strip()
            match = re.fullmatch(r"v(\d+)\.\d+\.\d+", version)
            if probe.returncode == 0 and match:
                result.update(version=version, supported=int(match[1]) >= 20)
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def report(profiles_dir: Path, requested: str | None = None) -> dict:
    required = CLI_PACKAGES + (("tzdata",) if os.name == "nt" else ())
    dependencies = packages(required)
    python = {"version": sys.version.split()[0], "cli_supported": sys.version_info >= (3, 11),
              "installer_supported": sys.version_info[:2] == (3, 12), "installer_requires": "Python 3.12"}
    profiles = registry_status(profiles_dir, requested)
    node = node_status()
    tools = {"node": node, "npm": executable("npm.cmd" if os.name == "nt" else "npm"),
             "tectonic": tectonic_available(),
             "ai_cli": {name: cli_available(name) for name in ("codex", "claude", "kimi")},
             "ai_check": "installed CLIs (PATH and the app's bundled locations) only; sign-in, API keys and connectivity are not checked"}
    synced = in_synced_folder()
    app_mode = python["cli_supported"] and dependencies["ready"]
    actions = []

    def action(code: str, message: str) -> None:
        actions.append({"code": code, "message": message})

    if not python["installer_supported"]:
        action("install_python", "Install Python 3.12 for the supported installation flow in README.md.")
    if not dependencies["ready"]:
        action("install_app", "Run Start Dashboard on your own computer to install the app, then rerun career doctor.")
    if not node["supported"] or not tools["npm"]:
        action("install_node", "Install Node.js 20 or newer with npm before starting the dashboard.")
    if profiles["registry_error"]:
        action("repair_registry", "Resolve the registry error before creating or selecting a profile; do not overwrite the list.")
    elif profiles.get("selection_error"):
        action("select_profile", "Choose an existing profile ID from the list or explicitly request a new profile.")
    elif profiles["profile_count"] == 0:
        action("setup_profile", "Add your resume and notes to me/, confirm your name, target market and work authorization, then run career setup.")
    elif profiles["selected_profile"] is None:
        action("select_profile", "Ask whose profile to use, then rerun career doctor --profile <id> with that exact ID.")
    else:
        selected = next(p for p in profiles["profiles"] if p["id"] == profiles["selected_profile"])
        if selected["state"] == "onboarding":
            action("finish_setup", "Finish setup for the selected profile using career setup --profile <id> before requesting jobs.")
        else:
            action("read_profile_status", "Run career status --profile <id> for the selected profile before preparing jobs.")
    if not tools["tectonic"]:
        action("check_pdf_compiler", "Tectonic was not found. Start Dashboard downloads a pinned copy; run it on your own "
               "computer, then check again before promising resume PDFs.")
    if synced:
        action("move_out_of_sync", "This workspace is inside OneDrive. Syncing can lock or damage the app's databases while "
               "it runs; move the folder somewhere local (for example C:\\CareerWorkspace) before starting the dashboard.")
    rules = permit_rules_status()
    if rules["state"] != "current":
        action("review_permit_rules", "The Irish employment-permit thresholds in career-dashboard/backend/countries/ie/"
               f"permit-rules.yml (checked {rules.get('verified_at') or 'unknown'}) are due for review on "
               f"{rules.get('review_after') or 'an unknown date'}. Re-verify them against the official DETE pages; "
               "after that date salary-threshold checks show 'unknown'.")
    action("verify_ai_provider", "Before AI work, verify a configured provider in the app; executable discovery does not prove sign-in or availability.")
    return {"schema_version": 1, "app_mode": app_mode, "python": python,
            "dependencies": dependencies, "ocr_packages": packages(OCR_PACKAGES),
            "tools": tools, "synced_folder": synced, **profiles, "permit_rules": rules, "next_actions": actions,
            "read_only": True, "note": "No candidate documents, credentials, AI providers or external services were opened."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", help="explicit profile ID; never changes the selected profile on disk")
    parser.add_argument("--profiles-dir", type=Path, default=APP / "profiles", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = report(args.profiles_dir, args.profile)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    # A successful diagnostic is useful even when installation/setup is needed.
    return 1 if result["registry_error"] or result.get("selection_error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
