"""The one server that holds every profile, each in its own isolated app.

    /                         -> the last-used profile's page
    /p/<id>/                  -> the React client for profile <id>
    /p/<id>/api/...           -> that profile's own API (dashboard/app.py create_app)
    /api/health               -> the launcher's identity check (run.py)
    /api/profiles...          -> list, create, reset, delete; the intake for new profiles

Each ready profile gets its own `create_app(root)`: its own Workspace, SQLite
database, services, agent runner, Daily Search pipeline, assistant and
scheduler. A request reaches a profile only through its URL prefix, and no
endpoint takes a profile id in its body, so one profile's request can never
touch another profile's data.
"""

from __future__ import annotations

import gc
import os
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.paths import APP_ID, APP_ROOT, APP_TITLE
from backend.profiles import ProfileError, ProfileStore, store as default_store

PROFILE_API = re.compile(r"^/p/([a-z0-9-]{1,40})(/api(?:/.*)?)$")


class NewProfile(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class Confirmation(BaseModel):
    confirm: str = Field(default="", max_length=120)


class MachineKey(BaseModel):
    value: str = Field(min_length=1, max_length=400)


class ScheduleToggle(BaseModel):
    enabled: bool
    ready_by: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class ProfileApps:
    """Builds each ready profile's app on first use and keeps it; closes it on reset/delete."""

    def __init__(self, profiles: ProfileStore, schedule: bool = False):
        self.profiles = profiles
        self.schedule = schedule
        self.apps: dict = {}
        self.lock = threading.RLock()

    def app(self, profile_id: str):
        with self.lock:
            if profile_id in self.apps:
                return self.apps[profile_id]
            profile = self.profiles.get(profile_id)
            if profile["state"] != "ready":
                raise ProfileError("This profile is still being set up. Upload the documents about you on the Assistant page first.")
            from backend.dashboard.app import create_app

            app = create_app(self.profiles.root_for(profile_id), schedule=self.schedule)
            app.state.profile_id = profile_id
            app.state.start_background()
            self.apps[profile_id] = app
            return app

    def loaded(self):
        with self.lock:
            return list(self.apps.items())

    def close(self, profile_id: str, wait_seconds: float = 10.0) -> None:
        """Stop a profile's background work and forget its app, so its files can be erased."""
        with self.lock:
            app = self.apps.pop(profile_id, None)
        if app is None:
            return
        try:
            app.state.stop_background(stop_search=True)
        except Exception:  # noqa: BLE001 - closing must never block a reset
            pass
        workspace = app.state.workspace
        deadline = time.monotonic() + wait_seconds
        from backend.services.pipeline import busy

        while time.monotonic() < deadline and busy(workspace):
            time.sleep(0.25)
        del app, workspace
        gc.collect()

    def close_all(self) -> None:
        for profile_id, _ in self.loaded():
            self.close(profile_id, wait_seconds=0)

    def busy(self) -> bool:
        from backend.services.pipeline import busy

        return any(busy(app.state.workspace) for _, app in self.loaded())


class Shell:
    """ASGI entry point: /p/<id>/api/... goes to that profile's app, the rest to the shell API."""

    def __init__(self, api: FastAPI, apps: ProfileApps):
        self.api = api
        self.apps = apps
        self.state = api.state

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            match = PROFILE_API.match(scope.get("path", ""))
            if match:
                try:
                    app = self.apps.app(match[1])
                except ProfileError as error:
                    response = JSONResponse({"detail": str(error)}, status_code=404 if "No such" in str(error) else 409)
                    return await response(scope, receive, send)
                child = dict(scope)
                # Starlette routes on path minus root_path: the profile app sees /api/...
                child["root_path"] = scope.get("root_path", "") + "/p/" + match[1]
                return await app(child, receive, send)
        return await self.api(scope, receive, send)


def create_shell(profiles: ProfileStore | None = None, schedule: bool = False, frontend: Path | None = None):
    profiles = profiles or default_store()
    profiles.purge_trash()
    apps = ProfileApps(profiles, schedule=schedule)
    dist = Path(frontend) if frontend else APP_ROOT / "frontend/dist"
    started_at = time.time()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        # Open every ready profile at start, so each one's configured hourly work
        # applications, posting checks) runs whether or not its page is open.
        for profile in profiles.list():
            if profile["state"] == "ready":
                try:
                    apps.app(profile["id"])
                except Exception as error:  # noqa: BLE001 - one broken profile must not stop the others
                    print(f"Profile {profile['id']} could not start: {type(error).__name__}: {error}", flush=True)
        yield
        apps.close_all()

    api = FastAPI(title=APP_TITLE, version="2.0.0", lifespan=lifespan)
    api.state.profiles = profiles
    api.state.apps = apps
    intake_jobs: dict = {}  # replaced below by the intake's own job table
    from backend.dashboard.app import guard

    guard(api)

    @api.exception_handler(ValueError)
    async def bad_input(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # ---- identity (run.py) --------------------------------------------------
    @api.get("/api/health")
    def health():
        # pid, started_at and busy let the launcher replace a server running older
        # code, and never one that is in the middle of a search or an agent run.
        return {"app": APP_ID, "version": "2.0.0", "root": str(APP_ROOT), "pid": os.getpid(),
                "started_at": started_at, "busy": apps.busy(), "profiles": True}

    # ---- AI on this computer, before any profile exists ---------------------------------
    # Keys belong to the installation (career-dashboard/.env), not to a person: onboarding
    # checks them before the first build instead of failing half-way through.
    @api.get("/api/ai/status")
    def ai_status():
        from backend.ai import settings as ai_settings

        return ai_settings.machine_status()

    @api.put("/api/ai/keys/{provider}")
    def save_machine_key(provider: str, data: MachineKey):
        from backend.ai import settings as ai_settings

        return {**ai_settings.save_machine_key(provider, data.value), "status": ai_settings.machine_status()}

    # ---- profiles -------------------------------------------------------------
    def describe(profile: dict) -> dict:
        """A profile plus the market and resume contract shown by the client."""
        from backend.countries import enabled_markets, load_pack
        from backend.resume_contract import contract_for

        profile = {**profile, "offered_markets": enabled_markets()}
        try:
            pack = load_pack(profile.get("country") or "ie") if profile.get("country") or profile.get("legacy") else None
        except ValueError:
            pack = None
        if pack is None:
            return {**profile, "market": None, "target_markets": profile.get("target_markets") or ["ie"]}
        contract = contract_for(profiles.root_for(profile["id"])) if profile.get("state") == "ready" else None
        return {**profile, "target_markets": profile.get("target_markets") or [pack.code],
                "market": {"code": pack.code, "name": pack.name, "adjective": pack.adjective, "paper": pack.paper_label,
                                      "timezone": pack.timezone, "default_location": pack.text("default_location"),
                                      "tier_labels": pack.tier_labels,
                                      "pages": contract.pages if contract else 1,
                                      "min_body_pt": contract.min_body_pt if contract else 10,
                                      "max_body_pt": contract.max_body_pt if contract else 11}}

    def listing():
        return {"profiles": [describe(p) for p in profiles.list()], "last_used": profiles.last_used()}

    @api.get("/api/profiles")
    def list_profiles():
        return listing()

    @api.get("/api/profiles/{profile_id}")
    def get_profile(profile_id: str):
        try:
            return {"profile": describe(profiles.get(profile_id))}
        except ProfileError as error:
            raise HTTPException(404, str(error)) from None

    @api.post("/api/profiles", status_code=201)
    def create_profile(data: NewProfile):
        profile = profiles.create(data.name)
        return {"profile": profile, **listing()}

    def stop_everything(profile_id: str) -> None:
        """Before erasing: stop the intake, the profile app's work and its daily task."""
        from backend.services import schedule_tasks

        job = intake_jobs.pop(profile_id, None)
        if job is not None:
            job.cancel()
            build_runs = getattr(job, "build_runs", None)
            if build_runs is not None:
                build_runs.cancelled.set()
            if job.thread is not None:
                job.thread.join(timeout=15)
            if build_runs is not None and build_runs.thread is not None:
                build_runs.thread.join(timeout=15)
        apps.close(profile_id)
        schedule_tasks.remove(profile_id)

    @api.post("/api/profiles/{profile_id}/reset")
    def reset_profile(profile_id: str, data: Confirmation):
        profiles.check_confirmation(profile_id, data.confirm)
        stop_everything(profile_id)
        profile = profiles.reset(profile_id)
        resync_morning()
        return {"profile": profile, **listing()}

    @api.delete("/api/profiles/{profile_id}")
    def delete_profile(profile_id: str, data: Confirmation):
        profiles.check_confirmation(profile_id, data.confirm)
        stop_everything(profile_id)
        profiles.delete(profile_id)
        resync_morning()
        return {"deleted": profile_id, **listing()}

    def resync_morning() -> None:
        """The morning task follows the profiles that still have Morning jobs on."""
        from backend.services import schedule_tasks

        try:
            schedule_tasks.sync(profiles)
        except Exception:  # noqa: BLE001 - the profile change itself has already happened
            pass

    # ---- Morning jobs: the one scheduled task for the PC (backend/services/schedule_tasks) --
    @api.get("/api/profiles/{profile_id}/schedule")
    def get_schedule(profile_id: str):
        from backend.services import schedule_tasks

        profile = profiles.get(profile_id)
        saved = profile.get("schedule") or {}
        ready_by = saved.get("ready_by") or schedule_tasks.READY_BY
        task = schedule_tasks.morning_status()
        note = schedule_tasks.unsupported_note() if task.get("supported") is False else saved.get("note", "")
        return {"enabled": bool(saved.get("enabled")), "ready_by": ready_by,
                "night_start": schedule_tasks.night_start(ready_by), "note": note,
                "list": f"{profile_id}/daily-job-search/MORNING-JOBS.md", **task}

    @api.put("/api/profiles/{profile_id}/schedule")
    def set_schedule(profile_id: str, data: ScheduleToggle):
        from backend.services import schedule_tasks

        profile = profiles.get(profile_id)
        if profile["state"] != "ready":
            raise ProfileError("Build this profile's workspace first; its morning jobs start after that.")
        saved = profile.get("schedule") or {}
        chosen = {"enabled": data.enabled, "ready_by": data.ready_by or saved.get("ready_by") or schedule_tasks.READY_BY}
        profiles.update(profile_id, schedule=chosen)
        result = schedule_tasks.sync(profiles)
        note = "" if result.get("installed") or result.get("removed") else result.get("note", "")
        if data.enabled and not result.get("installed"):
            note = result.get("note") or "Task Scheduler did not take the task."
        profiles.update(profile_id, schedule={**chosen, "note": note})
        return get_schedule(profile_id)

    # ---- the intake for new profiles (backend/services/intake) ---------------
    from backend.services.intake.api import attach_intake

    # The same dict attach_intake keeps its jobs in, so stop_everything sees every job.
    intake_jobs = attach_intake(api, profiles, apps)

    # ---- the React client -------------------------------------------------
    def index():
        built = dist / "index.html"
        if not built.exists():
            raise HTTPException(503, "Build the React client with scripts/build_frontend.py or use Start Dashboard.cmd")
        return FileResponse(built)

    @api.get("/")
    def home():
        last_used = profiles.last_used()
        return RedirectResponse(f"/p/{last_used}/", status_code=307) if last_used else index()

    @api.get("/p/{profile_id}")
    def profile_home_redirect(profile_id: str):
        return RedirectResponse(f"/p/{profile_id}/", status_code=307)

    @api.get("/p/{profile_id}/")
    def profile_home(profile_id: str):
        if not profiles.exists(profile_id):
            return RedirectResponse("/", status_code=307)
        profiles.mark_used(profile_id)
        return index()

    if (dist / "assets").exists():
        api.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    return Shell(api, apps)
