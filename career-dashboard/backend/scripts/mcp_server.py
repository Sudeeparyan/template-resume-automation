#!/usr/bin/env python3
"""Print a host connection config, or run the local Career Workspace MCP stdio server."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(APP), str(APP / "backend" / "scripts")]

from backend.services.mcp_connector import CareerConnector, configuration


def build_server(connector: CareerConnector):
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:
        raise ValueError("The optional MCP package is missing. Install backend/requirements-mcp.txt first.") from None

    mcp = MCPServer(
        "Career Workspace",
        instructions=(
            "Use only the selected local profile. Read current state before answering. Draft and edit locally; "
            "never invent candidate facts. No tool submits applications, sends messages, deletes data or contacts anyone. "
            "Profile changes must be previewed, shown to the user, and applied only after explicit confirmation."
        ),
    )

    @mcp.tool(annotations={"readOnlyHint": True})
    def workspace_summary() -> dict:
        """Current jobs, goals, funnel and actions for the selected profile."""
        return connector.summary()

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_profile() -> dict:
        """Registered profile, evidence and source-linked entries for the selected profile."""
        return connector.profile_context()

    @mcp.tool(annotations={"readOnlyHint": True})
    def list_jobs(status: str | None = None) -> dict:
        """List saved jobs, optionally filtered by exact status."""
        return connector.list_jobs(status)

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_job(job_id: str) -> dict:
        """Read one exact saved job, including its full posting."""
        return connector.get_job(job_id)

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_job_fit(job_id: str, refresh: bool = False) -> dict:
        """Read the requirement/evidence fit for one exact job."""
        return connector.job_fit(job_id, refresh)

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    def resolve_posting(url: str) -> dict:
        """Read a public posting link without saving it."""
        return connector.resolve_posting(url)

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": False})
    def save_posting(company: str, title: str, location: str, url: str, description: str) -> dict:
        """Save one full posting locally through the app's normal gates; never submits it."""
        return connector.save_posting(company, title, location, url, description)

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    def start_job_search(count: int = 5, source: str = "default", cover_letter: bool = False) -> dict:
        """Start the existing resumable search pipeline for this profile."""
        return connector.start_search(count, source, cover_letter)

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_search_status(run_id: str | None = None) -> dict:
        """Read one run or the latest pipeline status without starting work."""
        return connector.search_status(run_id)

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    def prepare_application(job_id: str, cover_letter: bool = True) -> dict:
        """Prepare one saved job through the existing pipeline; no employer submission."""
        return connector.prepare_job(job_id, cover_letter)

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": False})
    def tailor_resume(job_id: str) -> dict:
        """Tailor one saved job from registered evidence and save a reviewable draft."""
        return connector.tailor_job(job_id)

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_application_documents(job_id: str) -> dict:
        """Current document paths and the actual readiness checks for one job."""
        return connector.documents(job_id)

    @mcp.tool(annotations={"readOnlyHint": False})
    def propose_profile_change(request: str) -> dict:
        """Turn the user's own answer into a reviewable, revision-bound profile proposal."""
        return connector.propose_profile_change(request)

    @mcp.tool(annotations={"readOnlyHint": False})
    def apply_profile_change(change_set_id: str, expected_revision: int, confirmed: bool = False) -> dict:
        """Apply a shown proposal only with confirmed=true after the user's explicit yes."""
        return connector.apply_profile_change(change_set_id, expected_revision, confirmed)

    return mcp


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="Exact local profile ID")
    parser.add_argument("--host", choices=["antigravity", "claude", "codex"], default="antigravity")
    parser.add_argument("--demo", action="store_true", help="Use the separate synthetic demo registry")
    parser.add_argument("--serve", action="store_true", help="Run the stdio server instead of printing configuration")
    args = parser.parse_args(argv)
    if not args.serve:
        result = configuration(args.host, args.profile, python=sys.executable,
                               script=str(Path(__file__).resolve()), cwd=str(APP.parent), demo=args.demo)
        result["note"] = "Paste this into your AI host's MCP configuration. No global settings were changed."
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    profiles = None
    if args.demo:
        from backend.profiles import ProfileStore
        from make_demo_profile import require_synthetic_demo

        base = APP / "data/demo/profiles"
        profiles = ProfileStore(base=base, legacy_root=base.parent / "no-legacy")
        require_synthetic_demo(profiles.root_for(args.profile))
    connector = CareerConnector(args.profile, profiles=profiles)
    try:
        build_server(connector).run(transport="stdio")
    finally:
        connector.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
