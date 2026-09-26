#!/usr/bin/env python3
"""One command for every AI app and every OS: `career <command> [args]`.

Run it from the repo root as `.\\career.cmd ...` (Windows) or `./career ...` (macOS, Linux, Git Bash);
both find the app's own Python. The same commands work in Claude Code, Codex, Kimi Code or any other
AI app opened in this folder. Commands use the selected profile's private database.

  career setup --name "Full Name" --market ie|us|both [--work-auth JSON]
                                 setup_profile.py: build a profile from the resume and notes in me/
  career ws <command> ...        workspace.py: summary, goals, fit --job-id, tailor --job-id, run --kind ...,
                                 sponsor-check, check-reapply,
                                 excluded, restore-excluded, age, ai-status, ai-wake, ...
  career check-resume <resume.tex>   validate_resume.py (--compile --output ... --render-dir ... --qa-json ...)
  career batch-check <batch.yml> validate_batch.py
  career verify-url --url <url>  inspect a posting URL for expiry or access barriers
  career check                   validate_workspace.py (the whole-workspace check)
  career <anything else>         career.py: status, jobs, activity, projects, add, update, prepare, export, ...

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
    "setup": SCRIPTS / "setup_profile.py",
    "ws": SCRIPTS / "workspace.py",
    "check-resume": SCRIPTS / "validate_resume.py",
    "batch-check": SCRIPTS / "validate_batch.py",
    "verify-url": SCRIPTS / "verify_job_url.py",
    "check": SCRIPTS / "validate_workspace.py",
}
DEFAULT = SCRIPTS / "career.py"


def target_for(argv: list[str]) -> tuple[Path, list[str]]:
    """The script a command runs, and the arguments it gets."""
    if argv and argv[0] in TARGETS:
        return TARGETS[argv[0]], argv[1:]
    return DEFAULT, argv


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    script, args = target_for(argv)
    # UTF-8 out, whatever the console's code page: names like "Zürich" or "Dún Laoghaire" must print on Windows too.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return subprocess.call([sys.executable, str(script), *args], env=env)


if __name__ == "__main__":
    sys.exit(main())
