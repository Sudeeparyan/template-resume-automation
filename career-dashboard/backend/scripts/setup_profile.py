#!/usr/bin/env python3
"""Build (or rebuild) a profile from the files in me/: `career setup --name "Full Name" --market ie`.

The same routes the dashboard's onboarding uses, run in this process (no server needed):
create the profile, add each resume or note from the folder as a source (unchanged files are
skipped), then run "Build Agent for You" with the markets and work authorization given here
and wait for it. An AI app signed in on this computer (Claude Code, Codex or Kimi Code) or a
key in career-dashboard/.env does the reading; Tectonic compiles the base resume.

  career setup --name "Ada Lovelace" --market ie --work-auth '{"ie": {"status": "needs_sponsorship",
      "citizenship": "noncitizen", "needs_sponsorship_later": "yes"}}'
  career setup --profile ada-lovelace          add changed files and rebuild an existing profile
  career setup --name "Ada Lovelace" --no-build   only create the profile and add the files

Work authorization per market: status authorized | needs_sponsorship, citizenship citizen |
noncitizen, needs_sponsorship_later yes | no. Leave it out and the profile keeps "unknown",
which blocks job searches until it is answered (Profile page or a rebuild with --work-auth).
Prints one JSON document; the exit code is 0 only when every requested step finished.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
REPO = APP.parent
sys.path.insert(0, str(APP))

# Written by an AI app in me/, or shipped as guides: never a person's evidence.
NOT_SOURCES = {"readme.md", "about-me.example.md", "profile.md", "questions.md"}
SUPPORTED = {".pdf", ".docx", ".txt", ".md"}
MARKETS = {"ie": ["ie"], "us": ["us"], "both": ["ie", "us"], "ie,us": ["ie", "us"], "us,ie": ["us", "ie"]}


def source_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise SystemExit(f"{folder} does not exist. Put the resume (PDF, DOCX, TXT or MD) in me/ first.")
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED
                   and p.name.casefold() not in NOT_SOURCES and not p.name.startswith((".", "~$")))
    if not files:
        raise SystemExit(f"No resume or notes found in {folder}. Add a PDF, DOCX, TXT or MD file first.")
    return files


def name_from_about(folder: Path) -> str:
    """The 'Full name:' line of me/about-me.md, when the person filled it in."""
    about = folder / "about-me.md"
    if not about.is_file():
        return ""
    for line in about.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = line.strip().lstrip("-*").partition(":")
        if key.strip(" *").casefold() == "full name" and value.strip():
            return value.strip()
    return ""


def unchanged(sources: list[dict], path: Path, data: bytes) -> bool:
    digest = hashlib.sha256(data).hexdigest()
    for source in sources:
        if source.get("name", "").casefold() == path.name.casefold() and source.get("active"):
            versions = source.get("versions") or []
            return bool(versions) and versions[-1].get("sha256") == digest
    return False


def running_server() -> str | None:
    """This copy's dashboard, when it is running: setup then goes through it, not around it."""
    import httpx

    for port in range(8000, 8011):
        try:
            health = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2).json()
        except (httpx.HTTPError, ValueError):
            continue
        if isinstance(health, dict) and Path(str(health.get("root") or "")).resolve() == APP.resolve():
            return f"http://127.0.0.1:{port}"
    return None


def client_for(profiles_dir: str | None):
    """A client for the onboarding routes.

    With the dashboard running, its own server does the work, so a hunt it is running is never
    disturbed. Otherwise the same routes run in this process, without the start-up step that
    opens every ready profile (only the profile being built is opened).
    """
    import httpx
    from fastapi.testclient import TestClient

    from backend.dashboard.shell import create_shell
    from backend.profiles import ProfileStore, store

    if profiles_dir:
        base = Path(profiles_dir).resolve()
        return TestClient(create_shell(ProfileStore(base=base, legacy_root=base.parent / "no-legacy")),
                          base_url="http://127.0.0.1"), None
    server = running_server()
    if server:
        return httpx.Client(base_url=server, timeout=120), server
    # The loopback guard accepts only local callers; this in-process client is one.
    return TestClient(create_shell(store()), base_url="http://127.0.0.1"), None


def check(response, step: str) -> dict:
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail") or response.text
        except ValueError:
            detail = response.text
        raise RuntimeError(f"{step}: {detail}")
    return response.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="folder", default=str(REPO / "me"), help="folder with the resume and notes (default: me/)")
    parser.add_argument("--name", help="full name for a new profile (default: 'Full name:' in me/about-me.md)")
    parser.add_argument("--profile", help="an existing profile ID to add files to and rebuild")
    parser.add_argument("--market", default="", help="a market this copy offers, e.g. ie (default: the profile's markets, else ie)")
    parser.add_argument("--work-auth", default="", help="JSON keyed by market, or a path to a JSON file")
    parser.add_argument("--permission-type", help="person-confirmed Irish permission, e.g. stamp_1g")
    parser.add_argument("--valid-until", help="person-confirmed exact permission expiry, YYYY-MM-DD")
    parser.add_argument("--award-date", help="person-confirmed actual degree award date, YYYY-MM-DD")
    parser.add_argument("--nfq-level", type=int, choices=range(1, 11))
    parser.add_argument("--irish-institution", choices=("yes", "no", "unknown"))
    parser.add_argument("--relevant-degree", choices=("yes", "no", "unknown"))
    parser.add_argument("--graduate-search", action="store_true", help="confirm graduate/junior/entry search, up to 3 years required")
    parser.add_argument("--seniority", help="confirmed search levels, comma separated")
    parser.add_argument("--max-years-required", type=int)
    parser.add_argument("--salary-floor-eur", type=float)
    parser.add_argument("--salary-policy", choices=("include_unstated", "confirmed_or_estimated", "confirmed_only"))
    parser.add_argument("--no-build", action="store_true", help="only create the profile and add the files")
    parser.add_argument("--wait-minutes", type=float, default=30, help="how long to wait for the build (default 30)")
    parser.add_argument("--profiles-dir", help=argparse.SUPPRESS)  # tests: a disposable profiles folder
    args = parser.parse_args(argv)

    os.environ.setdefault("CAREER_NO_SCHEDULED_TASKS", "1")
    folder = Path(args.folder).resolve()
    files = source_files(folder)
    markets = MARKETS.get(args.market.strip().casefold().replace(" ", "")) if args.market else None
    if args.market and markets is None:
        parser.error("--market must be ie, us or both")
    if markets:
        from backend.countries import enabled_markets

        switched_off = [m for m in markets if m not in enabled_markets()]
        if switched_off:
            parser.error(f"--market {','.join(switched_off)} is switched off in this copy (it searches "
                         f"{', '.join(enabled_markets())}); see docs/DEVELOPERS.md, 'Re-enabling a market'")
    authorization = {}
    if args.work_auth:
        text = Path(args.work_auth).read_text(encoding="utf-8") if Path(args.work_auth).is_file() else args.work_auth
        try:
            authorization = json.loads(text)
        except ValueError:
            parser.error("--work-auth must be JSON such as {\"ie\": {\"status\": \"authorized\"}}")
    try:
        from backend.services.intake.authorization import validate_authorization

        if not isinstance(authorization, dict):
            raise ValueError("--work-auth must be a market-keyed object.")
        if args.permission_type or args.valid_until:
            authorization.setdefault("ie", {})
            if args.permission_type:
                authorization["ie"]["permission_type"] = args.permission_type
            if args.valid_until:
                authorization["ie"].update(valid_until=args.valid_until, valid_until_raw=args.valid_until,
                                           valid_until_confirmed=True)
        authorization = {market: validate_authorization(value, market) for market, value in authorization.items()}
        args.permit_options = permit_options(args)
    except ValueError as error:
        parser.error(str(error))

    result: dict = {"added": [], "unchanged": []}
    client, server = client_for(args.profiles_dir)
    result["through"] = f"the running dashboard at {server}" if server else "this process"
    try:
        # Never "with client": for the in-process client that runs the start-up step (see client_for).
        status = build(client, args, parser, folder, files, markets, authorization, result)
    finally:
        apps = getattr(getattr(getattr(client, "app", None), "state", None), "apps", None)
        if apps is not None:
            apps.close_all()
        client.close()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return status


def build(client, args, parser, folder: Path, files: list[Path], markets, authorization: dict, result: dict) -> int:
    """Create or find the profile, add the changed files, then build and wait. Fills ``result``."""
    if args.profile:
        listing = check(client.get("/api/profiles"), "Profiles")["profiles"]
        profile = next((p for p in listing if p["id"] == args.profile), None)
        if profile is None:
            parser.error(f"No profile {args.profile!r}. Run  career setup --name \"Full Name\"  to create one.")
    else:
        name = args.name or name_from_about(folder)
        if not name:
            parser.error("Give --name \"Full Name\" (or fill in 'Full name:' in me/about-me.md).")
        profile = check(client.post("/api/profiles", json={"name": name}), "Create profile")["profile"]
    pid = profile["id"]
    result["profile"] = pid
    base = f"/api/profiles/{pid}"
    sources = check(client.get(base + "/sources"), "Sources")["sources"]
    for path in files:
        data = path.read_bytes()
        if unchanged(sources, path, data):
            result["unchanged"].append(path.name)
            continue
        sources = check(client.post(base + "/sources", params={"name": path.name}, content=data),
                        f"Add {path.name}")["sources"]
        result["added"].append(path.name)
    if args.no_build:
        result["build"] = "skipped (--no-build)"
        return 0
    options = {"work_authorization_by_market": authorization}
    options.update(getattr(args, "permit_options", {}))
    if markets:
        options["target_markets"] = markets
    run = check(client.post(base + "/build-runs", json=options), "Start the build")
    print(f"Building {profile['name']}'s profile from {len(files)} file(s)...", file=sys.stderr, flush=True)
    deadline = time.monotonic() + args.wait_minutes * 60
    seen = None
    while run["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(2)
        run = check(client.get(f"{base}/build-runs/{run['id']}"), "Build status")
        step = (run.get("phase"), run.get("progress"))
        if step != seen:
            seen = step
            print(f"  {run.get('phase')}: {run.get('progress')}%", file=sys.stderr, flush=True)
    if run["status"] in {"queued", "running"}:
        client.post(f"{base}/build-runs/{run['id']}/stop")
        run["errors"] = [*(run.get("errors") or []), f"Stopped after {args.wait_minutes:g} minutes."]
    result["build"] = {key: run.get(key) for key in ("id", "status", "phase", "flags", "errors",
                                                      "completed_profile_revision", "target_markets")}
    listing = check(client.get("/api/profiles"), "Profiles")["profiles"]
    result["state"] = next((p.get("state") for p in listing if p["id"] == pid), None)
    return 0 if run["status"] == "completed" else 1


def permit_options(args) -> dict:
    """Only explicit CLI arguments are person confirmations; omitted fields remain unknown."""
    from backend.services.intake.authorization import validate_education, validate_job_search

    education = {}
    for key in ("nfq_level", "irish_institution", "relevant_degree"):
        value = getattr(args, key, None)
        if value is not None:
            education[key] = value
    if getattr(args, "award_date", None):
        education.update(award_date=args.award_date, award_date_raw=args.award_date, award_date_confirmed=True)
    preferences = {}
    for key in ("max_years_required", "salary_floor_eur", "salary_policy"):
        value = getattr(args, key, None)
        if value is not None:
            preferences[key] = value
    if getattr(args, "seniority", None):
        preferences["seniority"] = [part.strip() for part in args.seniority.split(",") if part.strip()]
    if getattr(args, "graduate_search", False):
        preferences["graduate_search_confirmed"] = True
    return {**({"education_for_permits": validate_education(education)} if education else {}),
            **({"job_search": validate_job_search(preferences)} if preferences else {})}

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
