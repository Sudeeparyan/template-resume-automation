"""Durable progress for the explicit "Build Agent for You" action."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.services.intake.job import STATE_BUILT, STATE_FAILED, STATE_REVIEW, IntakeJob

PHASE_SECONDS = {"reading": 12.0, "extracting": 45.0, "indexing": 3.0,
                 "configuring": 7.0, "resume": 25.0}
PHASE_PROGRESS = {"reading": 0, "extracting": 12, "indexing": 72, "configuring": 80,
                  "resume": 90, "complete": 100}


class _BuildStopped(Exception):
    """A stop requested after extraction still rolls back the pending revision."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BuildRuns:
    def __init__(self, profile_id: str, job: IntakeJob, profiles, apps, finish: Callable):
        self.profile_id = profile_id
        self.job = job
        self.profiles = profiles
        self.apps = apps
        self.finish = finish
        self.directory = job.root / "data/build-runs"
        self.path = self.directory / "runs.json"
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.cancelled = threading.Event()

    def _read(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return json.loads(self.path.read_text(encoding="utf-8")).get("runs", [])

    def _write(self, runs: list[dict]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / ("runs-" + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json.dumps({"runs": runs}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def _one(self, runs: list[dict], run_id: str) -> dict:
        for run in runs:
            if run["id"] == run_id:
                return run
        raise ValueError("No such build run in this profile.")

    def _update(self, run_id: str, **changes) -> dict:
        with self.lock:
            runs = self._read()
            run = self._one(runs, run_id)
            run.update(changes, updated_at=_now())
            self._write(runs)
            return dict(run)

    def _public(self, run: dict) -> dict:
        result = dict(run)
        if run["status"] in {"queued", "running"}:
            if not self.thread or not self.thread.is_alive():
                result["status"] = "interrupted"
                result["errors"] = ["The app stopped while building. Retry this run."]
                self._update(run["id"], status="interrupted", errors=result["errors"])
            elif run["phase"] in {"reading", "extracting"}:
                state = self.job.state()
                steps = state.get("steps") or []
                detail = next((s.get("detail", "") for s in steps if s.get("label") == "Finding every fact, section by section"), "")
                match = re.search(r"(\d+) of (\d+) sections", detail)
                if match and int(match[2]):
                    result["phase"] = "extracting"
                    result["progress"] = min(71, 12 + int(59 * int(match[1]) / int(match[2])))
                elif state.get("state") == "reading":
                    result["progress"] = max(result["progress"], 5)
            durations = self._timings()
            remaining = sum(durations.get(k, PHASE_SECONDS[k]) for k in PHASE_SECONDS
                            if PHASE_PROGRESS[k] >= result["progress"])
            if result["phase"] == "extracting":
                remaining = max(10, remaining * (100 - result["progress"]) / 88)
            result["eta_seconds_low"] = max(1, int(remaining * .65))
            result["eta_seconds_high"] = max(result["eta_seconds_low"] + 1, int(remaining * 1.6))
        return result

    def list(self) -> list[dict]:
        with self.lock:
            return [self._public(run) for run in reversed(self._read())]

    def get(self, run_id: str) -> dict:
        with self.lock:
            return self._public(self._one(self._read(), run_id))

    def start(self, options: dict | None = None) -> dict:
        return self._start(options or {}, reviewed_draft=False)

    def start_reviewed(self, options: dict | None = None, *, retry: bool = False) -> dict:
        """Publish an already reviewed intake draft through the same guarded build."""
        state = self.job.state()["state"]
        if state not in ({STATE_REVIEW, STATE_BUILT, STATE_FAILED} if retry else {STATE_REVIEW}):
            raise ValueError("Read the documents and review what was found first.")
        draft = self.job.draft()
        if not draft:
            raise ValueError("Read the documents and review what was found first.")
        selected = {"target_markets": draft.get("target_markets") or [draft.get("country_pack") or "ie"],
                    "work_authorization_by_market": draft.get("work_authorization_by_market") or {},
                    "education_for_permits": draft.get("education_for_permits") or {},
                    "job_search": draft.get("job_search") or {}}
        selected.update(options or {})
        return self._start(selected, reviewed_draft=True)

    def build_reviewed_sync(self) -> dict:
        """Compatibility route: return when the reviewed draft and PDF are ready."""
        run = self.start_reviewed()
        assert self.thread is not None
        self.thread.join()
        result = self.get(run["id"])
        if result["status"] != "completed":
            raise ValueError("; ".join(result.get("errors") or ["The profile build was stopped."]))
        return result

    def _start(self, options: dict, *, reviewed_draft: bool) -> dict:
        from backend.countries import enabled_markets, known_markets, load_pack

        current = self.profiles.get(self.profile_id)
        # Re-read the selected profile: Profile page edits may be newer than registry metadata.
        settings_path = self.job.root / "data/config/profile.yml"
        if settings_path.is_file():
            import yaml

            saved = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
            current = {**current, **{key: saved[key] for key in
                       ("work_authorization_by_market", "education_for_permits", "job_search") if key in saved}}
        offered = enabled_markets()
        offer_text = "Choose from the markets this copy offers: " + ", ".join(load_pack(m).name for m in offered) + "."
        chosen = options.get("target_markets")
        markets = chosen if chosen is not None else (current.get("target_markets") or ([current["country"]] if current.get("country") else [offered[0]]))
        if not isinstance(markets, list) or not markets or len(markets) != len(set(markets)) or any(m not in known_markets() for m in markets):
            raise ValueError(offer_text)
        if chosen is not None and any(m not in offered for m in chosen):
            raise ValueError(offer_text)
        # An older profile keeps the markets this copy still offers; it is never moved to another country.
        markets = [m for m in markets if m in offered]
        if not markets:
            raise ValueError("This profile was built for a market this copy no longer offers. " + offer_text)
        authorization = options.get("work_authorization_by_market")
        if authorization is None or not authorization:
            authorization = current.get("work_authorization_by_market") or {}
        if not isinstance(authorization, dict) or any(k not in known_markets() or not isinstance(v, dict) for k, v in authorization.items()):
            raise ValueError("Work authorization must be keyed by a market code such as ie.")
        from backend.services.intake.authorization import validate_authorization, validate_education, validate_job_search

        authorization = {market: validate_authorization(value, market) for market, value in authorization.items()}
        education = validate_education(options.get("education_for_permits", current.get("education_for_permits") or {}))
        preferences = dict(options.get("job_search", current.get("job_search") or {}))
        if "job_search" not in options and preferences.get("salary_floor_source") == "permit_rules":
            preferences.pop("salary_floor_eur", None)
        preferences = validate_job_search(preferences)
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("This profile already has a build in progress.")
            snapshot = self.job.library.active_snapshot()
            if not snapshot:
                raise ValueError("Add at least one active document or note before building.")
            self.cancelled.clear()
            phase = "indexing" if reviewed_draft else "reading"
            run = {"id": uuid.uuid4().hex[:16], "status": "queued", "phase": phase,
                   "progress": PHASE_PROGRESS[phase],
                   "eta_seconds_low": 60, "eta_seconds_high": 150, "flags": [], "errors": [],
                   "completed_profile_revision": None, "started_at": _now(), "updated_at": _now(),
                   "source_snapshot": snapshot, "target_markets": markets,
                   "work_authorization_by_market": authorization, "education_for_permits": education,
                   "job_search": preferences, "reviewed_draft": reviewed_draft}
            runs = self._read()
            runs.append(run)
            self._write(runs)
            self.thread = threading.Thread(target=self._run, args=(run["id"],), name="profile-build", daemon=True)
            self.thread.start()
            return self._public(run)

    def stop(self, run_id: str) -> dict:
        with self.lock:
            run = self.get(run_id)
            if run["status"] in {"queued", "running"}:
                self.cancelled.set()
                self.job.cancel()
            return self.get(run_id)

    def retry(self, run_id: str) -> dict:
        before = self.get(run_id)
        if before["status"] not in {"failed", "stopped", "interrupted"}:
            raise ValueError("Only a failed, stopped or interrupted build can be retried.")
        options = {"target_markets": before["target_markets"],
                   "work_authorization_by_market": before.get("work_authorization_by_market") or {},
                   "education_for_permits": before.get("education_for_permits") or {},
                   "job_search": before.get("job_search") or {}}
        if before.get("reviewed_draft"):
            if self.job.library.active_snapshot() != before["source_snapshot"]:
                raise ValueError("Sources changed since this reviewed build. Read them again before retrying.")
            return self.start_reviewed(options, retry=True)
        return self.start(options)

    def _timings(self) -> dict:
        path = self.directory / "timings.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _phase(self, run_id: str, phase: str, **extra) -> None:
        # Polling calls _public() -> _timings() while the worker changes phases.
        # Publishing timings directly truncated the JSON before the reader could
        # parse it, intermittently turning a build-status GET into a 400 response.
        with self.lock:
            before = self._one(self._read(), run_id)
            previous = before.get("phase")
            started = before.get("phase_started_at")
            if previous in PHASE_SECONDS and previous != phase and started:
                elapsed = max(.1, (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds())
                durations = self._timings()
                durations[previous] = round((durations.get(previous, elapsed) * 3 + elapsed) / 4, 2)
                self.directory.mkdir(parents=True, exist_ok=True)
                temporary = self.directory / ("timings-" + uuid.uuid4().hex + ".tmp")
                temporary.write_text(json.dumps(durations, indent=2), encoding="utf-8")
                temporary.replace(self.directory / "timings.json")
            self._update(run_id, status="running", phase=phase, progress=PHASE_PROGRESS[phase],
                         phase_started_at=_now(), **extra)

    def _backup(self, run_id: str) -> tuple[Path, list[str], bool]:
        """Back up the active config and database before the file swap."""
        root = self.job.root
        directory = self.directory / run_id / "previous"
        files = []
        for folder in ("data/config", "data/context", "data/templates", "data/interview-prep", "data/output/base"):
            base = root / folder
            if base.is_dir():
                for path in base.rglob("*"):
                    if path.is_file() and "source_library" not in path.parts and "files" not in path.relative_to(root).parts:
                        relative = str(path.relative_to(root)).replace("\\", "/")
                        target = directory / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(path, target)
                        files.append(relative)
        for name in ("AGENTS.md",):
            path = root / name
            if path.is_file():
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                files.append(name)
        database = root / "data/career.db"
        db_exists = database.is_file()
        if db_exists:
            target = directory / "data/career.db"
            target.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(database)) as original, closing(sqlite3.connect(target)) as saved:
                original.backup(saved)
        return directory, files, db_exists

    def _restore(self, backup: Path, old_files: list[str], db_exists: bool, generated: list[str]) -> None:
        root = self.job.root
        for relative in generated:
            if relative not in old_files:
                (root / relative).unlink(missing_ok=True)
        # The PDF validator may have created a new PDF, QA report or preview
        # before a stop/failure. None of those belong to the prior revision.
        base = root / "data/output/base"
        if base.is_dir():
            for path in base.rglob("*"):
                if path.is_file() and str(path.relative_to(root)).replace("\\", "/") not in old_files:
                    path.unlink()
        for relative in old_files:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / relative, target)
        db = root / "data/career.db"
        if db_exists:
            shutil.copy2(backup / "data/career.db", db)
        else:
            db.unlink(missing_ok=True)

    def _run(self, run_id: str) -> None:
        prior_profile = self.profiles.get(self.profile_id)
        backup = None
        old_files: list[str] = []
        db_exists = False
        generated: list[str] = []
        try:
            run = self.get(run_id)
            if run.get("reviewed_draft"):
                if self.job.library.active_snapshot() != run["source_snapshot"]:
                    raise ValueError("Sources changed since this draft was reviewed. Read them again before building.")
                if self.job.state()["state"] not in {STATE_REVIEW, STATE_BUILT, STATE_FAILED}:
                    raise ValueError("Read the documents and review what was found first.")
            else:
                self._phase(run_id, "reading")
                self.job.start()
                extracting = False
                while self.job.thread and self.job.thread.is_alive():
                    self.job.thread.join(timeout=.2)
                    if self.cancelled.is_set():
                        self.job.cancel()
                    if not extracting and any(s.get("label") == "Reading your documents" and s.get("status") == "done"
                                              for s in (self.job.state().get("steps") or [])):
                        self._phase(run_id, "extracting")
                        extracting = True
                if self.cancelled.is_set():
                    self._update(run_id, status="stopped", errors=[], eta_seconds_low=0, eta_seconds_high=0)
                    return
                state = self.job.state()
                if state["state"] != STATE_REVIEW:
                    raise ValueError(state.get("error") or "Reading the documents did not finish.")
                if not extracting:
                    self._phase(run_id, "extracting")
            self._phase(run_id, "indexing")
            run = self.get(run_id)
            changes = {"target_markets": run["target_markets"],
                       "work_authorization_by_market": run.get("work_authorization_by_market") or {},
                       "education_for_permits": run.get("education_for_permits") or {},
                       "job_search": run.get("job_search") or {}}
            draft = self.job.draft() or {}
            flags = []
            if not (draft.get("contact") or {}).get("full_name"):
                changes["full_name"] = prior_profile["name"]
                flags.append("Full name came from the profile name; confirm it before using a resume.")
            if not (draft.get("targets") or {}).get("roles"):
                flags.append("No target role was found. Add one before searching for jobs.")
            for question in draft.get("questions") or []:
                flags.append("Needs clarification: " + str(question)[:200])
            for market in run["target_markets"]:
                auth = (run.get("work_authorization_by_market") or {}).get(market) or {}
                if market == "ie" and auth.get("permission_type") == "stamp_1g" and not auth.get("valid_until_confirmed"):
                    flags.append("Stamp 1G expiry is not confirmed. Confirm the exact day before searching or preparing jobs.")
                if auth.get("status", "unknown") == "unknown":
                    flags.append(f"Work authorization for {market.upper()} is unknown. Confirm it before eligibility decisions.")
                elif (auth.get("status") == "authorized" and auth.get("citizenship") != "citizen"
                      and auth.get("needs_sponsorship_later", "unknown") == "unknown"):
                    flags.append(f"Future sponsorship for {market.upper()} is unknown. Confirm it before eligibility decisions.")
            self.job.update(changes)
            self._update(run_id, flags=flags)
            if self.cancelled.is_set():
                self._update(run_id, status="stopped", eta_seconds_low=0, eta_seconds_high=0)
                return
            self._phase(run_id, "configuring")
            if prior_profile["state"] == "ready":
                self.apps.close(self.profile_id)
            backup, old_files, db_exists = self._backup(run_id)
            if self.cancelled.is_set():
                raise _BuildStopped()
            summary = self.job.build()
            generated = summary["files"]
            if self.cancelled.is_set():
                raise _BuildStopped()
            self._phase(run_id, "resume")
            result = self.finish(self.profile_id, self.job, summary, synchronous_resume=True)
            if self.cancelled.is_set():
                raise _BuildStopped()
            if not result.get("compiled") or result.get("status") not in {"PASS", "AUTOMATED_PASS_MANUAL_PENDING"}:
                raise ValueError("Base resume PDF did not pass validation: " + "; ".join(result.get("failures") or ["Tectonic/PDF build unavailable"]))
            if result.get("status") == "AUTOMATED_PASS_MANUAL_PENDING":
                flags.append("The base resume PDF passed automated checks; review its layout and supported claims before applying.")
            with self.lock:
                if self.cancelled.is_set():
                    raise _BuildStopped()
                self._phase(run_id, "complete")
                self._update(run_id, status="completed", phase="complete", progress=100,
                             flags=flags, eta_seconds_low=0, eta_seconds_high=0,
                             completed_profile_revision=summary["revision"], profile=self.profiles.get(self.profile_id))
        except Exception as error:  # noqa: BLE001 - reported to the UI; previous revision stays active
            try:
                self.apps.close(self.profile_id)
                if backup is not None:
                    self._restore(backup, old_files, db_exists, generated)
                    self.profiles.restore_build_metadata(self.profile_id, prior_profile)
                    if prior_profile["state"] == "ready":
                        self.apps.app(self.profile_id)
                run = self._one(self._read(), run_id)
                if (run.get("reviewed_draft") and self.job.library.active_snapshot() == run["source_snapshot"]
                        and self.job.state()["state"] in {STATE_BUILT, STATE_FAILED}):
                    # The legacy review card and setup chat offer Build again after a
                    # failed PDF. Keep their reviewed answers available for that retry.
                    self.job._save(state=STATE_REVIEW, error=None)
            except Exception as restore_error:  # noqa: BLE001
                error = RuntimeError(f"{error}; restoring the previous profile also failed: {restore_error}")
            stopped = isinstance(error, _BuildStopped)
            self._update(run_id, status="stopped" if stopped else "failed",
                         errors=[] if stopped else [f"{type(error).__name__}: {error}"],
                         eta_seconds_low=0, eta_seconds_high=0)
