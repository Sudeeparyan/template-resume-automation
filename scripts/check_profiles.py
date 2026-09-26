"""Check local profile isolation, database health, and base resume contracts."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "career-dashboard/profiles"


def check() -> list[str]:
    registry_path = PROFILES / "registry.json"
    if not registry_path.exists():
        print("No local profiles yet; profile checks will run after the first build.")
        return []
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    ids: set[str] = set()
    for entry in registry.get("profiles") or []:
        profile_id = entry.get("id", "")
        if not profile_id or profile_id in ids:
            errors.append(f"Duplicate or empty profile id: {profile_id!r}")
            continue
        ids.add(profile_id)
        root = PROFILES / profile_id
        if not root.resolve().is_relative_to(PROFILES.resolve()) or not root.is_dir():
            errors.append(f"Missing or unsafe profile folder: {profile_id}")
            continue
        if entry.get("state") != "ready":
            print(f"{profile_id}: onboarding")
            continue
        db_path = root / "data/career.db"
        config_path = root / "data/config/profile.yml"
        if not db_path.is_file() or not config_path.is_file():
            errors.append(f"{profile_id}: ready profile lacks database or profile.yml")
            continue
        with sqlite3.connect(db_path) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                errors.append(f"{profile_id}: database integrity check failed")
            if db.execute("PRAGMA foreign_key_check").fetchall():
                errors.append(f"{profile_id}: database has foreign key violations")
            jobs = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        contract = config.get("resume_contract") or {}
        base_dir = root / "data/output/base"
        pdfs = list(base_dir.glob("*.pdf")) if base_dir.is_dir() else []
        if not pdfs:
            errors.append(f"{profile_id}: ready profile lacks a base resume PDF")
            continue
        for pdf in pdfs:
            try:
                reader = PdfReader(str(pdf))
                expected_pages = int(contract.get("required_pages") or 0)
                if expected_pages and len(reader.pages) != expected_pages:
                    errors.append(f"{profile_id}: {pdf.name} has {len(reader.pages)} pages; expected {expected_pages}")
                paper = str(contract.get("paper") or "").lower()
                if paper in {"a4", "letter"}:
                    page = reader.pages[0]
                    dimensions = sorted((float(page.mediabox.width), float(page.mediabox.height)))
                    expected = (595.3, 841.9) if paper == "a4" else (612.0, 792.0)
                    if any(abs(actual - target) > 3 for actual, target in zip(dimensions, expected)):
                        errors.append(f"{profile_id}: {pdf.name} paper does not match {paper.upper()}")
            except Exception as error:  # a broken PDF is a failed profile check
                errors.append(f"{profile_id}: cannot read {pdf.name}: {type(error).__name__}")
        print(f"{profile_id}: {jobs} jobs, {len(pdfs)} base PDF(s), database OK")
    return errors


if __name__ == "__main__":
    problems = check()
    for problem in problems:
        print("FAIL: " + problem, file=sys.stderr)
    raise SystemExit(1 if problems else 0)
