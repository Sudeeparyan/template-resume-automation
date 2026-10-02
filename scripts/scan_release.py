"""Fail when the prospective GitHub tree contains private profiles or credentials.

Run this before staging and in CI. In a Git checkout, Git decides which tracked and
untracked files are candidates; ignored files are checked with ``check-ignore``.
Before the first commit, a conservative path walk gives a preliminary check.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CSV = "career-dashboard/backend/countries/us/sponsors-uscis.csv"
TEXT_SUFFIXES = {
    ".cmd", ".command", ".css", ".csv", ".html", ".ini", ".js",
    ".json", ".md", ".py", ".sh", ".svg", ".tex", ".toml",
    ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml", ".swift",
}
TEXT_NAMES = {"career", ".gitignore", ".gitattributes", "Dockerfile"}
REQUIRED_IGNORES = (
    ".local-reference/", "backup/", "pytest-of-*/", ".test-source-audit/", "me/*", "my-jobs/*",
    "career-dashboard/profiles/", "career-dashboard/data/",
    "career-dashboard/tests/*", "daily-job-search/history.csv",
    "**/.env", "**/keys.txt", "**/*.db", "**/node_modules/",
)
PRIVATE_ROOTS = {
    ".local-reference", ".venv", "backup",
    "resume-builder-main",
}
# me/ holds a person's resume and answers; my-jobs/ the lists an AI app writes for them.
# Only these guides are shared; everything else in the two folders stays on the computer.
PUBLIC_PERSONAL_GUIDES = {"me/README.md", "me/about-me.example.md", "my-jobs/README.md"}
PRIVATE_ROOT_FILES = {
    "PLAN.md", "WORKSPACE-STATE.md", "Resume.code-workspace",
}
PRIVATE_APP_DIRS = {"profiles", "data", "output", "career-dashboard", "docs"}
PRIVATE_APP_FILES = {"CLAUDE.md", "DATA_CONTRACT.md"}
PRUNE_DIRS = {".git", ".venv", ".runtime", "__pycache__", ".pytest_cache", "node_modules", "dist", ".test-source-audit"}
PERSON_NAMES = tuple("".join(parts) for parts in (
    ("che", "tan"), ("an", "nie"), ("sus", "ma"), ("sush", "ma"),
    ("sud", "ee"), ("sri", "kanth"),
))
# Details from real resumes that once reached shared code; split for the same reason as the names.
RESUME_MARKERS = tuple("".join(parts) for parts in (
    ("ins", "ops"), ("sol", "iton"), ("mano", "haran"), ("che", "tan0159"),
    ("university of ark", "ansas"), ("dr", "äger"),
))


def local_names() -> tuple[str, ...]:
    """Names of the people using this copy: its profiles and me/about-me.md must never reach Git."""
    names: set[str] = set()
    try:
        registry = json.loads((ROOT / "career-dashboard/profiles/registry.json").read_text(encoding="utf-8"))
        names.update(str(profile.get("name") or "") for profile in registry.get("profiles") or [])
    except (OSError, ValueError, AttributeError):
        pass
    try:
        for line in (ROOT / "me/about-me.md").read_text(encoding="utf-8").splitlines():
            match = re.match(r"\W*full name\W*:\s*(.+)", line, re.IGNORECASE)
            if match:
                names.add(match.group(1))
    except OSError:
        pass
    # Whole names only ("Ada Lovelace", any spacing): a single word such as Grace or Hunter is also plain English.
    patterns = {r"\s+".join(map(re.escape, name.split())) for name in names if len(name.split()) >= 2}
    return tuple(sorted(patterns))


NAME_RE = re.compile(r"\b(?:" + "|".join(PERSON_NAMES + local_names()) + r")\b", re.IGNORECASE)
MARKER_RE = re.compile("|".join(map(re.escape, RESUME_MARKERS)), re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)
HOME_SEGMENT = "Us" + "ers"
UNIX_HOME = "/ho" + "me/"
PATH_RE = re.compile(
    r"(?i)(?:[a-z]:[/\\]" + HOME_SEGMENT + r"[/\\]|/" + HOME_SEGMENT + r"/[^/\\]+[/\\]|" + UNIX_HOME + r"[^/\\]+[/\\])"
)
PHONE_RE = re.compile(r"(?<!\w)\+(?:1|91|353)[\s().-]*(?:\d[\s().-]*){9,11}(?!\d)")
TOKEN_RE = re.compile(
    r"\b(?:sk-(?:proj-|ant-|or-v1-)?[A-Za-z0-9_-]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AIza[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16})\b"
)
ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b"
    r"\s*[=:]\s*['\"]?([A-Za-z0-9_./+=-]{24,})"
)


def git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=False)


def in_git_checkout() -> bool:
    result = git("rev-parse", "--show-toplevel")
    return result.returncode == 0 and Path(os.fsdecode(result.stdout).strip()).resolve() == ROOT


def private_path(relative: str) -> bool:
    parts = Path(relative).parts
    if not parts:
        return False
    if (parts[0] in PRIVATE_ROOTS or parts[0].startswith("pytest-of-")
            or relative in PRIVATE_ROOT_FILES or relative.endswith(".ipynb")):
        return True
    if parts[0] in {"me", "my-jobs"} and len(parts) >= 2 and relative not in PUBLIC_PERSONAL_GUIDES:
        return True
    if any(part in PRUNE_DIRS for part in parts):
        return True
    if any(part.startswith(".env") and part != ".env.example" for part in parts):
        return True
    if parts[-1] == "keys.txt" or parts[-1].endswith((".db", ".sqlite", ".sqlite3", ".tsbuildinfo", ".pyc")):
        return True
    if len(parts) >= 2 and parts[0] == "career-dashboard":
        if parts[1] in PRIVATE_APP_DIRS or (len(parts) == 2 and parts[1] in PRIVATE_APP_FILES):
            return True
        # Keep the tests/ directory traversable so the pre-Git scan also checks
        # the portable fixture suite that .gitignore explicitly publishes.
        if parts[1] == "tests" and len(parts) >= 3 and parts[2] != "portable":
            return True
    if parts[0] == "daily-job-search" and len(parts) >= 2:
        if re.fullmatch(r"20\d\d-\d\d-\d\d", parts[1]) or parts[1] in {
            "logs", "history.csv", "DAILY_BRIEF.md", "MORNING-JOBS.md",
        }:
            return True
    return False


def preliminary_files() -> list[str]:
    found: list[str] = []
    for base, dirs, files in os.walk(ROOT):
        relative_dir = Path(base).relative_to(ROOT)
        dirs[:] = [name for name in dirs if name not in PRUNE_DIRS
                   and not private_path((relative_dir / name).as_posix())]
        for name in files:
            relative = (relative_dir / name).as_posix()
            if not private_path(relative):
                found.append(relative)
    return found


def candidate_files() -> list[str]:
    if not in_git_checkout():
        print("Preliminary scan: this folder is not a Git checkout yet.")
        return preliminary_files()
    result = git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if result.returncode:
        raise RuntimeError("git ls-files failed")
    return sorted({os.fsdecode(path) for path in result.stdout.split(b"\0") if path})


def ignore_errors() -> list[str]:
    ignore_path = ROOT / ".gitignore"
    if not ignore_path.is_file():
        return [".gitignore: missing"]
    text = ignore_path.read_text(encoding="utf-8")
    errors = [f".gitignore: missing rule {rule}" for rule in REQUIRED_IGNORES
              if rule not in text.splitlines()]
    if not in_git_checkout():
        return errors
    examples = (
        "career-dashboard/profiles/example/data/career.db",
        "career-dashboard/data/context/evidence.yml",
        "career-dashboard/tests/test_old.py",
        "career-dashboard/.test-source-audit/example/data/config/profile.yml",
        ".local-reference/example/notes.txt",
        "backup/old/profile.yml",
        "daily-job-search/history.csv",
        "career-dashboard/.env",
        "career-dashboard/keys.txt",
        "career-dashboard/frontend/node_modules/package/file.js",
        "me/resume.pdf",
        "me/about-me.md",
        "my-jobs/tracker.csv",
        "my-jobs/2026-01-05/JOBS.md",
    )
    for path in examples:
        result = git("check-ignore", "--no-index", "-q", path)
        if result.returncode != 0:
            errors.append(f".gitignore: Git would include {path}")
    for path in (PUBLIC_CSV, ".agents/skills/find-jobs/SKILL.md", *sorted(PUBLIC_PERSONAL_GUIDES)):
        if git("check-ignore", "--no-index", "-q", path).returncode == 0:
            errors.append(f".gitignore: Git would exclude shared file {path}")
    return errors


def file_errors(relative: str) -> list[str]:
    errors: list[str] = []
    path = ROOT / relative
    if private_path(relative):
        return [f"{relative}: private path is staged or otherwise publishable"]
    if NAME_RE.search(relative):
        errors.append(f"{relative}: person name in path")
    if path.is_symlink():
        return errors + [f"{relative}: symlink requires manual review"]
    if not path.is_file():
        return errors
    if relative == PUBLIC_CSV:
        return errors
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES and path.name != ".env.example":
        return errors + [f"{relative}: unexpected file type requires manual review"]
    if path.stat().st_size > 2_000_000:
        return errors + [f"{relative}: oversized text file requires manual review"]
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeError:
        return errors + [f"{relative}: not UTF-8 text"]
    for number, line in enumerate(content.splitlines(), 1):
        kinds: list[str] = []
        if NAME_RE.search(line):
            kinds.append("person name")
        if MARKER_RE.search(line):
            kinds.append("detail from a real resume")
        if PATH_RE.search(line):
            kinds.append("local home path")
        if PHONE_RE.search(line):
            kinds.append("phone number")
        if TOKEN_RE.search(line) or ASSIGNMENT_RE.search(line):
            kinds.append("credential-like value")
        if any(domain.lower() not in {"example.com", "example.org", "example.net", "example.test", "test.invalid"}
               for domain in EMAIL_RE.findall(line)):
            kinds.append("email address")
        if kinds:
            errors.append(f"{relative}:{number}: {', '.join(kinds)}")
    return errors


def main() -> int:
    errors = ignore_errors()
    files = candidate_files()
    for relative in files:
        errors.extend(file_errors(relative))
    for error in errors:
        print("FAIL: " + error, file=sys.stderr)
    if errors:
        print(f"Release privacy scan failed ({len(errors)} issue(s) in {len(files)} candidate files).", file=sys.stderr)
        print("What it means: Git would publish the files above, and they are private (a person's details, local\n"
              "notes, old tests or secrets) or the .gitignore rules that keep them out were removed.\n"
              "How to fix: put the missing rules back in .gitignore, then run  git rm -r --cached <path>  for each\n"
              "private path. That only stops Git publishing it; the file stays on this PC.", file=sys.stderr)
        return 1
    print(f"Release privacy scan passed ({len(files)} candidate files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
