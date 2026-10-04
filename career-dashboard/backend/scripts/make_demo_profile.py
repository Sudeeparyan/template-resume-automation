#!/usr/bin/env python3
"""Build an isolated synthetic Irish graduate, without AI or real candidate files.

Run from the repository root with the backend Python. The default registry lives
in ignored ``career-dashboard/data/demo/profiles``, separate from users' profiles.
Existing folders are refused; this command never resets a workspace.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))

import yaml

from backend.countries import is_enabled, load_pack
from backend.profiles import ProfileStore
from backend.services.intake.build import file_set
from backend.services.intake.coverage import ledger

MARKER = ".career-synthetic-demo.json"


def synthetic_draft(as_of: date) -> dict:
    award = as_of - timedelta(days=60)
    expiry = as_of + timedelta(days=365)
    refs = ["DEMO001"]
    return {
        "contact": {
            "full_name": "Example Graduate",
            "email": "graduate@example.org",
            "city": "Dublin",
            "country": "Ireland",
            "refs": refs,
        },
        "authorization": {
            "work_country": "Ireland",
            "status": "Stamp 1G",
            "valid_until": expiry.isoformat(),
            "citizenship": "noncitizen",
            "conditions": "Synthetic example: employer permit needed later.",
            "needs_sponsorship_later": "yes",
            "refs": refs,
        },
        "target_markets": ["ie"],
        "country_pack": "ie",
        "work_authorization_by_market": {
            "ie": {
                "status": "authorized",
                "citizenship": "noncitizen",
                "needs_sponsorship_later": "yes",
                "permission_type": "stamp_1g",
                "valid_until": expiry.isoformat(),
                "valid_until_confirmed": True,
            }
        },
        "education_for_permits": {
            "award_date": award.isoformat(),
            "award_date_confirmed": True,
            "nfq_level": 9,
            "irish_institution": True,
            "relevant_degree": True,
        },
        "targets": {
            "roles": ["Graduate Software Engineer", "Junior Data Analyst"],
            "cities": ["Dublin", "Cork"],
            "arrangements": ["hybrid", "onsite"],
        },
        "education": [
            {
                "institution": "Example Institute",
                "degree": "MSc",
                "field": "Computer Science",
                "start": (award - timedelta(days=365)).isoformat(),
                "end": award.isoformat(),
                "location": "Ireland",
                "coursework": ["Databases", "Software Engineering"],
                "refs": refs,
            }
        ],
        "experience": [],
        "skills": [
            {
                "name": "Programming",
                "skills": ["Python", "SQL", "Git"],
                "level": "used",
                "refs": refs,
            }
        ],
        "projects": [
            {
                "name": "Synthetic Dataset Explorer",
                "kind": "academic",
                "organisation": "Example Institute",
                "ownership": "Built for a synthetic academic exercise",
                "period": award.isoformat(),
                "tools": ["Python", "SQL"],
                "refs": refs,
                "facts": [
                    "Used Python to validate records in a synthetic dataset.",
                    "Wrote SQL queries to summarise the synthetic dataset.",
                ],
            }
        ],
        "certifications": [],
        "statements": [],
        "questions": [],
        "interview_answers": [],
    }


def make_demo(base: Path, *, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    base = Path(base).resolve()
    # Refuse the real registry even when it currently happens to be empty.
    from backend.paths import PROFILES

    if base == PROFILES.resolve() or base.is_relative_to(PROFILES.resolve()):
        raise ValueError(
            "The synthetic demo must use a separate registry, outside users' profiles."
        )
    if (base / "registry.json").exists():
        raise ValueError(
            "Profile registry already exists. Choose a new --profiles-dir for the synthetic demo."
        )
    if not is_enabled("ie"):
        raise ValueError(
            "The synthetic Irish demo requires the Ireland market to be enabled."
        )
    root = base / "example-graduate"
    if root.exists():
        raise ValueError(
            "Demo folder already exists. Choose a new --profiles-dir; existing data is never overwritten."
        )
    draft = synthetic_draft(as_of)
    source = "# Synthetic demo only\n\n" + json.dumps(
        draft, ensure_ascii=False, indent=2
    )
    blocks = [{"id": "DEMO001", "source": "synthetic-demo.md", "text": source}]
    generated = file_set(
        draft,
        blocks,
        ledger(blocks, draft),
        load_pack("ie"),
        ["synthetic-demo.md"],
        as_of.isoformat(),
    )
    profile = yaml.safe_load(generated["data/config/profile.yml"])
    profile["synthetic_demo"] = True
    profile["work_authorization_by_market"] = draft["work_authorization_by_market"]
    profile["education_for_permits"] = draft["education_for_permits"]
    from backend.permits.assessment import personal_floor

    profile["job_search"] = {
        "salary_floor_eur": personal_floor(profile, on=as_of),
        "salary_floor_source": "permit_rules",
        "salary_policy": "confirmed_or_estimated",
        "graduate_search_confirmed": True,
        "seniority": ["graduate", "entry", "junior"],
        "max_years_required": 2,
    }
    profile["target_roles"].update(
        seniority=["graduate", "entry", "junior"], max_years_required=2
    )
    profile["scoring"]["block_seniority"] = True
    generated["data/config/profile.yml"] = yaml.safe_dump(
        profile, sort_keys=False, allow_unicode=True
    )
    generated["data/context/sources/synthetic-demo.md"] = source
    generated[MARKER] = json.dumps(
        {"schema_version": 1, "synthetic": True, "as_of": as_of.isoformat()}
    )
    store = ProfileStore(base=base, legacy_root=base.parent / "no-legacy")
    entry = store.create("Example Graduate")
    root = store.root_for(entry["id"])
    for relative, content in generated.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    store.update(
        entry["id"],
        state="ready",
        target_markets=["ie"],
        work_authorization_by_market=draft["work_authorization_by_market"],
        synthetic_demo=True,
    )
    return {
        "profile_id": entry["id"],
        "root": str(root),
        "profiles_dir": str(base),
        "synthetic": True,
        "as_of": as_of.isoformat(),
        "pdf_built": False,
    }


def require_synthetic_demo(root: Path) -> None:
    """Studio and smoke tools must call this before opening a demo workspace."""
    path = Path(root).resolve()
    from backend.paths import PROFILES

    if path == PROFILES.resolve() or path.is_relative_to(PROFILES.resolve()):
        raise ValueError("Real profiles cannot be opened by demo tools.")
    try:
        marker = json.loads((path / MARKER).read_text(encoding="utf-8"))
        profile = yaml.safe_load(
            (path / "data/config/profile.yml").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError("A marked synthetic demo profile is required.") from exc
    if (
        not isinstance(marker, dict)
        or marker.get("synthetic") is not True
        or not isinstance(profile, dict)
        or profile.get("synthetic_demo") is not True
        or type(marker.get("schema_version")) is not int
        or marker["schema_version"] != 1
    ):
        raise ValueError("A marked synthetic demo profile is required.")
    try:
        date.fromisoformat(marker["as_of"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "A marked synthetic demo profile with a valid as-of date is required."
        ) from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles-dir", type=Path, default=APP / "data/demo/profiles")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args(argv)
    try:
        result = make_demo(args.profiles_dir, as_of=args.as_of)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
