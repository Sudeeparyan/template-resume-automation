"""Preview or erase this copy's candidate data so onboarding starts with no profiles.

Nothing is removed without --apply. File contents are never read. Backups, source
code, shipped examples, installed packages and built frontend assets are kept.
Stop the dashboard and morning runner before applying a reset.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PATHS = (
    "career-dashboard/profiles", "career-dashboard/data", "career-dashboard/output",
    "daily-job-search/logs", "daily-job-search/history.csv",
    "daily-job-search/DAILY_BRIEF.md", "daily-job-search/MORNING-JOBS.md",
    "daily-job-search/morning-jobs.json",
    ".claude/settings.local.json", ".claude/scheduled-tasks",
)
GUIDES = {"me": {"README.md", "about-me.example.md"}, "my-jobs": {"README.md"}}
CACHES = {".runtime", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
# These folders are neither inspected nor changed, including with --include-secrets.
PRESERVED = {"backup", ".local-reference", ".git", ".venv", "node_modules", "dist"}
GENERATED_SUFFIXES = (".log", ".pid", ".pyc", ".pyo", ".aux", ".synctex.gz", ".tsbuildinfo")


class ResetError(ValueError):
    """A reset refused before deleting an unsafe path."""


@dataclass(frozen=True)
class ResetPlan:
    root: Path
    targets: tuple[str, ...]
    include_secrets: bool = False


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _checked(root: Path, relative: str) -> Path:
    """Check lexical and resolved containment and refuse symlinks/junctions."""
    part = Path(relative)
    if part.is_absolute() or not part.parts or any(p in {"..", "."} for p in part.parts):
        raise ResetError("Reset targets must be relative paths inside the workspace.")
    target = root / part
    current = root
    for name in part.parts:
        current = current / name
        if _exists(current) and _is_link(current):
            raise ResetError(f"Refusing a symbolic link or junction: {relative}")
    resolved = target.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ResetError(f"Reset target escapes the workspace: {relative}")
    return target


def _root(root: Path) -> Path:
    root = Path(root).resolve()
    for marker in ("AGENTS.md", "career-dashboard/backend/profiles.py", "career.cmd"):
        if not _checked(root, marker).is_file():
            raise ResetError("This is not a Career Workspace template folder.")
    return root


def plan_reset(root: Path = ROOT, *, include_secrets: bool = False) -> ResetPlan:
    """List known private/generated paths using names and metadata only."""
    root = _root(root)
    targets: set[str] = set()

    def add(relative: str) -> None:
        path = _checked(root, relative)
        if _exists(path):
            targets.add(relative)

    for relative in PRIVATE_PATHS:
        add(relative)
    for folder, keep in GUIDES.items():
        path = _checked(root, folder)
        if path.is_dir():
            for child in path.iterdir():
                if child.name not in keep:
                    add(child.relative_to(root).as_posix())
    tests = _checked(root, "career-dashboard/tests")
    if tests.is_dir():
        for child in tests.iterdir():
            if child.name != "portable":
                add(child.relative_to(root).as_posix())
    daily = _checked(root, "daily-job-search")
    if daily.is_dir():
        for child in daily.iterdir():
            if re.fullmatch(r"20\d\d(?:-.*)?", child.name):
                add(child.relative_to(root).as_posix())

    def scan(folder: Path) -> None:
        for child in folder.iterdir():
            relative = child.relative_to(root).as_posix()
            if child.name in PRESERVED or relative in targets:
                continue
            if child.name in CACHES or child.name.startswith("pytest-of-"):
                add(relative)
            elif child.name.startswith(".env") or child.name == "keys.txt":
                if include_secrets and child.name != ".env.example":
                    add(relative)
            elif child.name.endswith(GENERATED_SUFFIXES) or child.name in {
                ".ai-plan-health.json", ".ai-plan-health.json.saving",
            }:
                add(relative)
            elif not _is_link(child) and child.is_dir():
                scan(child)

    scan(root)
    # A containing target already owns all of its children.
    minimal = tuple(sorted(p for p in targets if not any(
        parent.as_posix() in targets for parent in Path(p).parents if str(parent) != "."
    )))
    return ResetPlan(root, minimal, include_secrets)


def _check_tree(root: Path, relative: str) -> None:
    path = _checked(root, relative)
    if not _exists(path):
        return
    if path.is_dir():
        for child in path.iterdir():
            _check_tree(root, child.relative_to(root).as_posix())
    elif not path.is_file():
        raise ResetError(f"Refusing an unsupported filesystem object: {relative}")


def _remove_tree(root: Path, relative: str) -> None:
    # Recheck each entry immediately before deletion. Never follow a link.
    path = _checked(root, relative)
    if not _exists(path):
        return
    if path.is_dir():
        for child in path.iterdir():
            _remove_tree(root, child.relative_to(root).as_posix())
        _checked(root, relative).rmdir()
    else:
        path.unlink()


def apply_plan(plan: ResetPlan) -> None:
    """Apply only a freshly checked plan; do not allow arbitrary target injection."""
    current = plan_reset(plan.root, include_secrets=plan.include_secrets)
    if current != plan:
        raise ResetError("Workspace paths changed after the preview. Create a new reset plan.")
    # Check every tree before deleting any entry, including nested junctions.
    for relative in plan.targets:
        _check_tree(plan.root, relative)
    for relative in plan.targets:
        _remove_tree(plan.root, relative)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Permanently delete the listed private data")
    parser.add_argument("--include-secrets", action="store_true",
                        help="Also delete local .env files and keys.txt; keep .env.example")
    args = parser.parse_args(argv)
    try:
        plan = plan_reset(include_secrets=args.include_secrets)
        if args.apply:
            apply_plan(plan)
        print(json.dumps({
            "mode": "applied" if args.apply else "preview",
            "paths": list(plan.targets),
            "include_secrets": args.include_secrets,
            "preserved": ["backup/", ".local-reference/", "source code and shared guides",
                          "installed dependencies and frontend build", "external schedules and AI sign-ins"],
            "note": ("Candidate data removed. New onboarding starts with zero profiles."
                     if args.apply else "Nothing removed. Stop the dashboard and morning runner, then use --apply."),
        }, indent=2))
        return 0
    except (OSError, ResetError) as error:
        print(f"Reset failed: {error}. If files are in use, close the app and morning runner first.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
