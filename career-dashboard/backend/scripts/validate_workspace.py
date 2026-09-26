#!/usr/bin/env python3
"""Validate shareable code and any locally installed career profiles.

A fresh clone intentionally has no candidate data. Each ready profile owns its
own evidence, contract, resume and database under the ignored profiles folder.
"""

from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from backend.countries import pack_for, target_markets_for  # noqa: E402
from backend.profiles import store  # noqa: E402
from backend.resume_contract import contract_for  # noqa: E402

ERRORS: list[str] = []
WARNINGS: list[str] = []
REQUIRED_CODE = (
    "AGENTS.md", "README.md", "backend/dashboard/app.py", "backend/run.py",
    "backend/services/workspace_v2.py", "backend/services/intake/build.py",
    "backend/services/intake/runs.py", "frontend/package.json",
)
SKIP = {".venv", "node_modules", "dist", "__pycache__", ".pytest_cache",
        "data", "profiles", "backup", ".local-reference", ".agents"}


def read_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        ERRORS.append(f"Cannot read {path}: {exc}")
        return {}
    if not isinstance(value, dict):
        ERRORS.append(f"Expected YAML mapping: {path}")
        return {}
    return value


def validate_code() -> None:
    for relative in REQUIRED_CODE:
        if not (ROOT / relative).is_file():
            ERRORS.append(f"Missing application file: {relative}")
    for relative in ("AGENTS.md", "Check Workspace.cmd"):
        if not (REPO_ROOT / relative).is_file():
            ERRORS.append(f"Missing workspace file: {relative}")
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP for part in path.relative_to(ROOT).parts):
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            ERRORS.append(f"Python source invalid: {path.relative_to(ROOT)} ({exc})")


def validate_profile(profile: dict) -> None:
    profile_id = profile["id"]
    root = store().root_for(profile_id)
    config_path = root / "data/config/profile.yml"
    evidence_path = root / "data/context/evidence.yml"
    if profile.get("state") != "ready":
        return
    config = read_yaml(config_path)
    evidence = read_yaml(evidence_path)
    candidate = config.get("candidate") or {}
    claims = {c.get("id"): c for c in evidence.get("claims") or [] if isinstance(c, dict)}
    name = str(candidate.get("full_name") or "").strip()
    if not name:
        ERRORS.append(f"{profile_id}: ready profile has no candidate name")
    if claims.get("IDENTITY-001", {}).get("value") != name:
        ERRORS.append(f"{profile_id}: profile identity differs from its evidence registry")
    if config.get("candidate_revision") != evidence.get("candidate_revision"):
        ERRORS.append(f"{profile_id}: profile and evidence revisions differ")
    ids: set[str] = set()
    for group in ("claims", "projects"):
        for item in evidence.get(group) or []:
            item_id = item.get("id") if isinstance(item, dict) else None
            if not item_id or item_id in ids:
                ERRORS.append(f"{profile_id}: missing or duplicate evidence ID in {group}")
                continue
            ids.add(item_id)
    markets = target_markets_for(root)
    if markets != profile.get("target_markets", markets):
        WARNINGS.append(f"{profile_id}: profile registry and profile.yml markets differ")
    if pack_for(root).code not in markets:
        ERRORS.append(f"{profile_id}: active country pack is outside selected markets")
    try:
        contract = contract_for(root)
        if contract.pages not in {1, 2} or contract.paper not in {"a4", "letter"}:
            ERRORS.append(f"{profile_id}: unsupported resume pages or paper")
        ready = [p for p in evidence.get("projects") or [] if isinstance(p, dict)
                 and p.get("resume_content") and p.get("status") not in {"hold", "missing"}]
        if not 0 <= contract.required_selected_projects <= len(ready):
            ERRORS.append(f"{profile_id}: resume project count exceeds registered projects")
        template = root / "data/templates/resume-base.tex"
        if not template.is_file():
            ERRORS.append(f"{profile_id}: base resume template is missing")
        else:
            source = template.read_text(encoding="utf-8")
            for section in contract.required_sections:
                if source.count(r"\section{" + section + "}") != 1:
                    ERRORS.append(f"{profile_id}: missing resume section {section}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ERRORS.append(f"{profile_id}: resume contract cannot be checked ({exc})")
    db_path = root / "data/career.db"
    if db_path.is_file():
        try:
            with sqlite3.connect(db_path) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    ERRORS.append(f"{profile_id}: database integrity check failed")
                if db.execute("PRAGMA foreign_key_check").fetchone():
                    ERRORS.append(f"{profile_id}: database has foreign key violations")
        except sqlite3.DatabaseError as exc:
            ERRORS.append(f"{profile_id}: database is unreadable ({exc})")
    else:
        ERRORS.append(f"{profile_id}: ready profile has no database")


def main() -> int:
    validate_code()
    profiles = []
    try:
        profiles = store().list()
        for profile in profiles:
            validate_profile(profile)
    except (OSError, ValueError) as exc:
        ERRORS.append(f"Profile registry cannot be checked: {exc}")
    for message in WARNINGS:
        print("WARN:", message)
    for message in ERRORS:
        print("FAIL:", message)
    if ERRORS:
        print(f"Workspace validation failed with {len(ERRORS)} error(s).")
        return 1
    print(f"PASS: application code and {len(profiles)} local profile(s) are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
