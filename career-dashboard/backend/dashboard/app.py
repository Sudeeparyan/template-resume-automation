"""One profile's local dashboard API. Bind to loopback through run.py (via dashboard/shell.py)."""

from __future__ import annotations
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.paths import APP_ID, APP_ROOT as ROOT, APP_TITLE, SCRIPTS  # noqa: E402

sys.path.insert(0, str(SCRIPTS))
from career import Workspace, safe_child  # noqa: E402


class JobInput(BaseModel):
    company: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=150)
    url: str = Field(min_length=8, max_length=2500)
    description: str = Field(min_length=80, max_length=100000)
    market: str = Field(default="", pattern="^(|ie|us)$")


class StatusInput(BaseModel):
    status: str
    notes: Optional[str] = Field(default=None, max_length=10000)
    application_date: Optional[str] = None


class PrepareInput(BaseModel):
    project_id: Optional[str] = None


class NotesInput(BaseModel):
    text: str = Field(max_length=100000)


def guard(app):
    """Loopback only, same-origin only, and conservative response headers."""

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse(
                {"detail": "Use the local dashboard address"}, status_code=403
            )
        origin = request.headers.get("origin")
        if origin and (
            urlsplit(origin).netloc != request.url.netloc
            or urlsplit(origin).scheme != request.url.scheme
        ):
            return JSONResponse(
                {"detail": "Cross-origin requests are not accepted"}, status_code=403
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    return app


def create_app(root=ROOT, schedule: bool = False):
    root = Path(root).resolve()
    workspace = Workspace(root)
    app = FastAPI(title=APP_TITLE, version="2.0.0")
    app.state.workspace = workspace
    from backend.dashboard.api_v2 import attach

    service = attach(app, workspace, schedule=schedule)
    locks = {}
    locks_guard = threading.Lock()

    def job_lock(job_id):
        with locks_guard:
            return locks.setdefault(job_id, threading.Lock())

    guard(app)

    @app.exception_handler(ValueError)
    async def bad_input(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/")
    def home():
        built = ROOT / "frontend/dist/index.html"
        if not built.exists():
            raise HTTPException(
                503,
                "Build the React client with scripts/build_frontend.py or use Start Dashboard.command",
            )
        return FileResponse(built)

    started_at = time.time()

    @app.get("/api/health")
    def health():
        from backend.services.pipeline import busy

        # pid, started_at and busy let the launcher replace a server running older
        # code, and never one that is in the middle of a search or an agent run.
        return {"app": APP_ID, "version": "2.0.0", "root": str(root),
                "pid": os.getpid(), "started_at": started_at, "busy": busy(workspace)}

    @app.get("/api/overview")
    def overview():
        evidence = workspace.evidence()
        jobs = workspace.jobs()
        historical = (
            json.loads((root / "data/historical-packs.json").read_text(encoding="utf-8"))
            if (root / "data/historical-packs.json").exists()
            else []
        )
        return {
            "candidate": workspace.profile()["candidate"],
            "identity": workspace.profile()["professional_identity"],
            "revision": evidence["candidate_revision"],
            "counts": {
                "claims": len(evidence["claims"]),
                "projects": sum(
                    bool(bool(p.get("resume_content"))) for p in evidence["projects"]
                ),
                "saved_jobs": len(jobs),
                "applied": sum(bool(j["application_date"]) for j in jobs),
                "historical_packs": len(historical),
            },
            "artifacts": workspace.artifacts(),
            "questions": (root / "data/context/QUESTIONS-FOR-YOU.md").read_text(encoding="utf-8"),
            "historical": historical,
        }

    @app.get("/api/profile")
    def profile():
        return {
            "profile": workspace.profile(),
            "evidence": workspace.evidence(),
            "notes": (
                (root / "data/context/UPDATES.md").read_text(encoding="utf-8")
                if (root / "data/context/UPDATES.md").exists()
                else ""
            ),
        }

    @app.put("/api/profile/notes")
    def save_notes(data: NotesInput):
        return workspace.save_profile_notes(data.text)

    @app.get("/api/activity")
    def activity():
        return workspace.activity()

    @app.get("/api/search-runs")
    def searches():
        return {"today": workspace.today(), "runs": workspace.search_runs()}

    @app.post("/api/search-runs/{date}")
    def start_search(date: str):
        return workspace.start_search(date)

    @app.put("/api/search-runs/{date}")
    def update_search(date: str, data: NotesInput):
        return workspace.update_search(date, data.text)

    @app.post("/api/search-runs/{date}/jobs/{job_id}")
    def attach_search_job(date: str, job_id: str):
        return workspace.track_search_job(job_id, date)

    @app.get("/api/projects")
    def projects(q: str = ""):
        return workspace.rank_projects(q)

    @app.get("/api/jobs")
    def jobs():
        return workspace.jobs()

    @app.post("/api/jobs", status_code=201)
    def add_job(data: JobInput):
        result = service.add_posting(data.model_dump(), source="manual")
        if result.get("excluded") or result.get("blocked"):
            raise HTTPException(409, result.get("reason_label") or result.get("note") or "Posting did not pass the profile's screening rules")
        return result["job"]

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        value = workspace.get_job(job_id)
        artifacts = []
        if value["folder"]:
            folder = safe_child(root, value["folder"])
            artifacts = [
                str(p.relative_to(root / "data/output"))
                for p in folder.iterdir()
                if p.is_file() and p.suffix in {".pdf", ".tex", ".md", ".json", ".yml"}
            ]
        return {
            "job": value,
            "artifacts": artifacts,
            "screen": workspace.screen(value),
            "projects": workspace.rank_projects(
                value["title"] + " " + value["description"]
            ),
        }

    @app.patch("/api/jobs/{job_id}")
    def update_job(job_id: str, data: StatusInput):
        return workspace.update_job(job_id, **data.model_dump())

    @app.post("/api/jobs/{job_id}/prepare")
    def prepare(job_id: str, data: PrepareInput):
        from backend.countries import require_known_authorization
        require_known_authorization(root, workspace.get_job(job_id).get("market") or None)
        if service.profile_dirty():
            raise ValueError(
                "Profile edits are saved. Open Profile and confirm the pending entries before preparing a new draft so outdated facts cannot be used."
            )
        with job_lock(job_id):
            return workspace.prepare(job_id, data.project_id)

    @app.post("/api/jobs/{job_id}/preview")
    def preview(job_id: str):
        with job_lock(job_id):
            return workspace.compile_preview(job_id)

    @app.post("/api/jobs/{job_id}/validate")
    def validate(job_id: str):
        with job_lock(job_id):
            return workspace.validate(job_id)

    @app.get("/api/portals")
    def portals():
        from career import read_yaml

        return read_yaml(root / "data/config/portals.yml")

    @app.get("/api/files/{relative:path}")
    def output_file(relative: str):
        try:
            path = safe_child(root / "data/output", relative)
        except ValueError:
            raise HTTPException(404, "File not found")
        if not path.is_file() or path.suffix not in {
            ".pdf",
            ".png",
            ".tex",
            ".md",
            ".yml",
            ".json",
        }:
            raise HTTPException(404, "File not found")
        return FileResponse(
            path, media_type="application/pdf" if path.suffix == ".pdf" else None
        )

    if (ROOT / "frontend/dist/assets").exists():
        app.mount(
            "/assets",
            StaticFiles(directory=ROOT / "frontend/dist/assets"),
            name="assets",
        )
    return app
