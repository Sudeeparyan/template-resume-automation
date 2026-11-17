#!/usr/bin/env python3
"""Check that a fresh clone of a branch has everything the app needs, and nothing private.

    python scripts/fresh_clone_check.py [--branch template-clean] [--full]

1. Clones the branch's committed files (not this working copy) into a temporary folder.
2. Checks the clone: no profiles, outputs or keys; every data file the app reads is there (permit
   rules, DETE statistics, occupation lists, employer directory and registry); the launchers and
   the AI-app instructions are there; every Python file compiles; the privacy scan passes.
3. ``--full`` also makes a fresh virtual environment in the clone, installs the backend
   requirements and runs the portable tests (several minutes; needs the network).

Run it before sharing a branch with friends. Uncommitted changes are not in the clone, so a file
that is only in your working copy is reported as missing: commit it first.
"""

from __future__ import annotations

import argparse
import compileall
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REQUIRED = [
    "AGENTS.md", "CLAUDE.md", "README.md", "START-HERE.md", "career.cmd", "career",
    "Start Dashboard.cmd", "Start Dashboard.command", "Check Workspace.cmd", "Check Workspace.command",
    ".gitignore", "scripts/scan_release.py", "scripts/career_doctor.py", "scripts/bootstrap.py",
    "career-dashboard/backend/requirements.txt", "career-dashboard/frontend/package.json",
    "career-dashboard/frontend/package-lock.json", "career-dashboard/.env.example",
    "career-dashboard/backend/countries/markets.yml",
    *(f"career-dashboard/backend/countries/ie/{name}" for name in (
        "pack.yml", "permit-rules.yml", "sponsorship.yml", "sponsors-dete.csv", "sponsors-dete.meta.yml",
        "occupations.yml", "occupation-keywords.yml", "employer-aliases.yml", "employers.yml",
        "employer-registry.csv", "employer-registry.meta.yml")),
    "daily-job-search/autopilot.py", "docs/DEVELOPERS.md", "docs/DATA-SOURCES.md", "docs/OBSERVABILITY.md",
]
SKILLS = ["career-setup", "find-jobs", "tailor-resume", "morning-jobs", "track-applications", "profile-intake",
          "verify-job-url", "interview-prep", "ireland-job-sources"]
# Never in a clone: people's data, generated outputs, keys and local tool state.
FORBIDDEN = ["career-dashboard/profiles", "career-dashboard/data/market", "career-dashboard/data/demo",
             "career-dashboard/.env", "backup", "me/resume.pdf", "my-jobs/tracker.csv",
             "career-dashboard/backend/.venv", "career-dashboard/frontend/node_modules"]


def run(command: list[str], cwd: Path, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def check(clone: Path, *, full: bool) -> list[str]:
    problems = []
    for relative in REQUIRED + [f".agents/skills/{name}/SKILL.md" for name in SKILLS]:
        if not (clone / relative).exists():
            problems.append(f"missing: {relative}")
    for relative in FORBIDDEN:
        if (clone / relative).exists():
            problems.append(f"must not be committed: {relative}")
    profiles = clone / "career-dashboard/profiles"
    if profiles.exists() and any(profiles.iterdir()):
        problems.append("the clone holds profiles")
    if not compileall.compile_dir(clone / "career-dashboard/backend", quiet=1, force=True,
                                  rx=__import__("re").compile(r"[\\/]\.venv[\\/]")):
        problems.append("a backend Python file does not compile")
    scan = run([sys.executable, "scripts/scan_release.py"], clone, timeout=600)
    if scan.returncode != 0:
        problems.append("privacy scan failed:\n" + (scan.stdout + scan.stderr)[-2000:])
    if full:
        venv = clone / "career-dashboard/backend/.venv"
        python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        steps = [[sys.executable, "-m", "venv", str(venv)],
                 [str(python), "-m", "pip", "install", "-q", "-r", "career-dashboard/backend/requirements-dev.txt"],
                 [str(python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "career-dashboard/tests/portable"]]
        for step in steps:
            done = run(step, clone, timeout=3600)
            if done.returncode != 0:
                problems.append(f"{' '.join(step[:3])} failed:\n" + (done.stdout + done.stderr)[-3000:])
                break
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--branch", default="template-clean")
    parser.add_argument("--full", action="store_true", help="also install into a fresh environment and run the tests")
    parser.add_argument("--keep", action="store_true", help="keep the temporary clone for inspection")
    args = parser.parse_args(argv)
    folder = Path(tempfile.mkdtemp(prefix="career-clone-"))
    clone = folder / "clone"
    try:
        cloned = run(["git", "clone", "--quiet", "--depth", "1", "--branch", args.branch, REPO.as_uri(), str(clone)], REPO)
        if cloned.returncode != 0:
            print("Could not clone the branch:", cloned.stderr.strip())
            return 2
        problems = check(clone, full=args.full)
        if problems:
            print(f"Fresh clone of {args.branch}: {len(problems)} problem(s)")
            for problem in problems:
                print(" -", problem)
            return 1
        print(f"Fresh clone of {args.branch} is complete and holds no private files"
              + (" (tests passed in a fresh environment)." if args.full else ". Run with --full to install and test it."))
        return 0
    finally:
        if args.keep:
            print("Clone kept at", clone)
        else:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
