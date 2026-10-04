#!/usr/bin/env python3
"""One command for every AI app and every OS: `career <command> [args]`.

Run it from the repo root as `.\\career.cmd ...` (Windows) or `./career ...` (macOS, Linux, Git Bash);
both find the app's own Python. The same commands work in Claude Code, Codex, Kimi Code or any other
AI app opened in this folder. Commands use the selected profile's private database.

  career doctor                 read-only installation and profile-list readiness; works before setup
  career setup --name "Full Name" --market ie [--work-auth JSON]
                                 setup_profile.py: build a profile from the resume and notes in me/
  career ws <command> ...        workspace.py: summary, goals, fit --job-id, tailor --job-id, run --kind ...,
                                 docx --job-id (Word copy of the checked PDF), cover-letter --job-id,
                                 coverage, salary --job-id, sponsor-check, check-reapply,
                                 excluded, restore-excluded, age, ai-status, ai-wake, ...
  career check-resume <resume.tex>   validate_resume.py (--compile --output ... --render-dir ... --qa-json ...)
  career batch-check <batch.yml> validate_batch.py
  career verify-url --url <url>  inspect a posting URL for expiry or access barriers
  career check                   validate_workspace.py (the whole-workspace check)
  career trace list|show <run>   trace_cli.py: agent run timelines (steps, AI calls, web requests)
  career market status|refresh   market_cli.py: the Tracker's shared store of public postings; refresh reads
                                 the public sources for this computer's profiles (no AI)
  career add --url <link>        career.py: resolve a pasted link to the employer's own posting, then save it
                                 through the gates (or --file posting.json)
  career <anything else>         career.py: status, jobs, activity, projects, update, prepare, export, ...

Commands accept --profile <id>; otherwise they use the last opened local profile.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
REPO = APP.parent
SCRIPTS = APP / "backend" / "scripts"
TARGETS = {
    "doctor": REPO / "scripts" / "career_doctor.py",
    "setup": SCRIPTS / "setup_profile.py",
    "ws": SCRIPTS / "workspace.py",
    "check-resume": SCRIPTS / "validate_resume.py",
    "batch-check": SCRIPTS / "validate_batch.py",
    "verify-url": SCRIPTS / "verify_job_url.py",
    "check": SCRIPTS / "validate_workspace.py",
    "trace": SCRIPTS / "trace_cli.py",
    "market": SCRIPTS / "market_cli.py",
}
DEFAULT = SCRIPTS / "career.py"


def target_for(argv: list[str]) -> tuple[Path, list[str]]:
    """The script a command runs, and the arguments it gets."""
    if argv and argv[0] in TARGETS:
        return TARGETS[argv[0]], argv[1:]
    # career.py has subparsers, so its global --profile must precede the command.
    # Accept the same suffix form as workspace.py and setup_profile.py without
    # changing the order or values of any command-specific arguments.
    prefix, rest = [], []
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "--":
            rest.extend(argv[index:])
            break
        if value == "--profile":
            prefix.append(value)
            if index + 1 < len(argv):
                index += 1
                prefix.append(argv[index])
        elif value.startswith("--profile="):
            prefix.append(value)
        else:
            rest.append(value)
        index += 1
    return DEFAULT, prefix + rest


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    script, args = target_for(argv)
    # UTF-8 out, whatever the console's code page: names like "Zürich" or "Dún Laoghaire" must print on Windows too.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    # Apply before spawning even read-only commands; importing backend itself is stdlib-only.
    sys.path.insert(0, str(APP))
    from backend import keep_traces_local

    keep_traces_local(env)
    return subprocess.call([sys.executable, str(script), *args], env=env)


if __name__ == "__main__":
    sys.exit(main())
