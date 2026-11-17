#!/usr/bin/env python3
"""Build isolated synthetic student profiles without AI or real candidate files."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))

import yaml

from backend.countries import is_enabled, load_pack
from backend.profiles import ProfileStore
from backend.services.demo import MARKER, PRACTICE_POSTINGS, REGISTRY_MARKER
from backend.services.intake.build import file_set
from backend.services.intake.coverage import ledger

PERSONAS = ("software", "analytics")


def synthetic_draft(as_of: date, persona: str = "software") -> dict:
    if persona not in PERSONAS:
        raise ValueError("Choose a synthetic persona: " + ", ".join(PERSONAS))
    analyst = persona == "analytics"
    award = as_of - timedelta(days=60)
    expiry = as_of + timedelta(days=365)
    refs = ["DEMO001"]
    projects = [
        {
            "name": "Synthetic Sales Dashboard" if analyst else "Synthetic Dataset Explorer",
            "kind": "academic",
            "organisation": "Example Institute",
            "ownership": "Built individually for a synthetic academic exercise",
            "period": award.isoformat(),
            "tools": ["Python", "SQL"],
            "refs": refs,
            "facts": [
                "Used Python to validate records in a synthetic dataset.",
                "Wrote SQL queries to summarise the synthetic dataset.",
            ],
        },
        {
            "name": "Data Quality Notebook" if analyst else "Practice API Build",
            "kind": "academic",
            "organisation": "Example Institute",
            "ownership": "Built individually for a synthetic academic exercise",
            "period": award.isoformat(),
            "tools": ["Python", "SQL"] if analyst else ["Python", "Git"],
            "refs": refs,
            "facts": [
                "Used Python to flag missing values in a synthetic dataset.",
                "Documented the data-quality checks in a project notebook.",
            ] if analyst else [
                "Built a practice REST API endpoint with Python.",
                "Used Git to track changes and wrote tests for the practice endpoint.",
            ],
        },
    ]
    return {
        "contact": {
            "full_name": "Example Analyst" if analyst else "Example Graduate",
            "email": "analyst@example.org" if analyst else "graduate@example.org",
            "city": "Cork" if analyst else "Dublin",
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
            "roles": ["Junior Data Analyst", "Graduate Data Analyst"] if analyst else ["Graduate Software Engineer", "Junior Software Engineer"],
            "cities": ["Cork", "Dublin"],
            "arrangements": ["hybrid", "onsite"],
        },
        "education": [
            {
                "institution": "Example Institute",
                "degree": "MSc",
                "field": "Data Analytics" if analyst else "Computer Science",
                "start": (award - timedelta(days=365)).isoformat(),
                "end": award.isoformat(),
                "location": "Ireland",
                "coursework": ["Databases", "Data Visualisation"] if analyst else ["Databases", "Software Engineering"],
                "refs": refs,
            }
        ],
        "experience": [],
        "skills": [
            {
                "name": "Data tools" if analyst else "Programming",
                "skills": ["Python", "SQL"] if analyst else ["Python", "SQL", "Git"],
                "level": "used",
                "refs": refs,
            }
        ],
        "projects": projects,
        "certifications": [],
        "statements": [],
        "questions": [],
        "interview_answers": [],
    }


def _refuse_real_registry(base: Path) -> None:
    from backend.paths import PROFILES

    if base == PROFILES.resolve() or base.is_relative_to(PROFILES.resolve()):
        raise ValueError("The synthetic demo must use a separate registry, outside users' profiles.")


def _check_marked_registry(base: Path) -> None:
    try:
        marker = json.loads((base / REGISTRY_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("An existing demo registry must carry its synthetic registry marker.") from exc
    if marker.get("synthetic") is not True or marker.get("schema_version") != 1:
        raise ValueError("An existing demo registry must carry its synthetic registry marker.")


def make_demo(base: Path, *, as_of: date | None = None, persona: str = "software", _marked_registry: bool = False) -> dict:
    as_of = as_of or date.today()
    base = Path(base).resolve()
    _refuse_real_registry(base)
    if (base / "registry.json").exists():
        if not _marked_registry:
            raise ValueError("Profile registry already exists. Choose a new --profiles-dir for the synthetic demo.")
        _check_marked_registry(base)
    if not is_enabled("ie"):
        raise ValueError("The synthetic Irish demo requires the Ireland market to be enabled.")
    draft = synthetic_draft(as_of, persona)
    source = "# Synthetic demo only\n\n" + json.dumps(draft, ensure_ascii=False, indent=2)
    blocks = [{"id": "DEMO001", "source": "synthetic-demo.md", "text": source}]
    generated = file_set(draft, blocks, ledger(blocks, draft), load_pack("ie"), ["synthetic-demo.md"], as_of.isoformat())
    profile = yaml.safe_load(generated["data/config/profile.yml"])
    profile["synthetic_demo"] = True
    profile["work_authorization_by_market"] = draft["work_authorization_by_market"]
    profile["education_for_permits"] = draft["education_for_permits"]
    from backend.permits.assessment import personal_floor

    profile["job_search"] = {
        "salary_floor_eur": personal_floor(profile, on=as_of),
        "salary_floor_source": "permit_rules",
        "salary_policy": "include_unstated",
        "graduate_search_confirmed": True,
        "seniority": ["graduate", "entry", "junior"],
        "max_years_required": 2,
    }
    profile["target_roles"].update(seniority=["graduate", "entry", "junior"], max_years_required=2)
    profile["scoring"]["block_seniority"] = True
    generated["data/config/profile.yml"] = yaml.safe_dump(profile, sort_keys=False, allow_unicode=True)
    generated["data/context/sources/synthetic-demo.md"] = source
    generated[MARKER] = json.dumps({"schema_version": 1, "synthetic": True, "as_of": as_of.isoformat(), "persona": persona})
    store = ProfileStore(base=base, legacy_root=base.parent / "no-legacy")
    entry = store.create(draft["contact"]["full_name"])
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
        "persona": persona,
        "as_of": as_of.isoformat(),
        "pdf_built": False,
    }


def _seed_workspace(root: Path, persona: str) -> None:
    from backend.services.resume_studio import ResumeStudio
    from backend.services.workspace_v2 import CareerServices
    from career import Workspace

    service = CareerServices(Workspace(root))
    service.set_pref("demo_mode", "1")
    service.set_pref("ai_policy", {"daily_call_limit": 0})
    service.set_pref("ai_preferences", {
        "default": {"provider": "demo", "model": "guided-practice"},
        "tiers": {
            "strong": {"provider": "demo", "model": "guided-practice"},
            "cheap": {"provider": "demo", "model": "guided-practice"},
        },
    })
    service.set_pref("features", {"graph_research": False, "graph_pipeline": False, "graph_tracker_refresh": False})
    posting = PRACTICE_POSTINGS[1 if persona == "analytics" else 0]
    existing = next((job for job in service.w.jobs(include_deleted=True)
                     if job["company"] == posting["company"] and job["title"] == posting["title"]), None)
    if existing and existing["url"] != posting["url"]:
        from backend.services.postings import posting_key

        with service.w.connect() as db:
            db.execute("DELETE FROM posting_identities WHERE job_id=?", (existing["id"],))
            db.execute("UPDATE jobs SET url=?,updated_at=? WHERE id=?", (posting["url"], service.now(), existing["id"]))
            for key in {posting_key(posting["url"]), posting_key(posting["url"], posting["company"], "")}:
                db.execute("INSERT INTO posting_identities VALUES(?,?)", (key, existing["id"]))
            service.w.record_event(db, "demo_practice_url_repaired", existing["id"])
        saved = {"job": service.w.get_job(existing["id"]), "duplicate": True}
    else:
        saved = service.add_posting({**posting, "market": "ie"}, source="synthetic_demo")
    if saved.get("job"):
        ResumeStudio(service).open(saved["job"]["id"])
    service.export_state()


def seed_demo(base: Path, *, as_of: date | None = None) -> dict:
    """Create or reopen the two-persona demo registry, then seed each workspace once per URL."""
    base = Path(base).resolve()
    _refuse_real_registry(base)
    registry = base / "registry.json"
    created = not registry.exists()
    if not created:
        _check_marked_registry(base)
        store = ProfileStore(base=base, legacy_root=base.parent / "no-legacy")
        results = []
        for profile in store.list():
            root = store.root_for(profile["id"])
            require_synthetic_demo(root)
            _seed_workspace(root, (json.loads((root / MARKER).read_text(encoding="utf-8"))).get("persona", "software"))
            results.append(profile["id"])
        return {"profiles": results, "profiles_dir": str(base), "synthetic": True, "created": False}
    if base.exists() and any(base.iterdir()):
        raise ValueError("The new demo folder is not empty; existing data is never overwritten.")
    try:
        results = []
        for number, persona in enumerate(PERSONAS):
            result = make_demo(base, as_of=as_of, persona=persona, _marked_registry=number > 0)
            if number == 0:
                (base / REGISTRY_MARKER).write_text(json.dumps({"schema_version": 1, "synthetic": True}) + "\n", encoding="utf-8")
            _seed_workspace(Path(result["root"]), persona)
            results.append(result["profile_id"])
    except Exception:
        if created:
            shutil.rmtree(base, ignore_errors=True)
        raise
    return {"profiles": results, "profiles_dir": str(base), "synthetic": True, "created": True}


def require_synthetic_demo(root: Path) -> None:
    """Studio and smoke tools must call this before opening a demo workspace."""
    path = Path(root).resolve()
    from backend.paths import PROFILES

    if path == PROFILES.resolve() or path.is_relative_to(PROFILES.resolve()):
        raise ValueError("Real profiles cannot be opened by demo tools.")
    try:
        marker = json.loads((path / MARKER).read_text(encoding="utf-8"))
        profile = yaml.safe_load((path / "data/config/profile.yml").read_text(encoding="utf-8"))
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
        raise ValueError("A marked synthetic demo profile with a valid as-of date is required.") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles-dir", type=Path, default=APP / "data/demo/profiles")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--persona", choices=PERSONAS, default="software")
    parser.add_argument("--seed", action="store_true", help="Create or reopen the complete two-persona guided demo")
    args = parser.parse_args(argv)
    try:
        result = seed_demo(args.profiles_dir, as_of=args.as_of) if args.seed else make_demo(
            args.profiles_dir, as_of=args.as_of, persona=args.persona
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
