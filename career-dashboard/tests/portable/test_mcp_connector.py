"""The optional local MCP connector reuses one exact profile and no external actions."""

import json
import tomllib

import pytest

from backend.profiles import ProfileStore
from backend.services.mcp_connector import CareerConnector, configuration
from career import Workspace
from test_opportunity_interfaces import make_profile


def test_host_configuration_is_copyable_and_never_writes_global_settings(tmp_path):
    script = tmp_path / "mcp_server.py"
    for host in ("antigravity", "claude"):
        result = configuration(host, "example-person", python="python", script=str(script), cwd=str(tmp_path))
        parsed = json.loads(result["config"])
        entry = parsed["mcpServers"]["career-workspace"]
        assert entry["args"][1:3] == ["--profile", "example-person"]
        assert entry["cwd"] == str(tmp_path)
    codex = configuration("codex", "example-person", python="python", script=str(script), cwd=str(tmp_path), demo=True)
    parsed = tomllib.loads(codex["config"])
    assert parsed["mcp_servers"]["career-workspace"]["args"][-1] == "--demo"
    with pytest.raises(ValueError, match="Antigravity"):
        configuration("unknown", "example-person", python="python", script=str(script), cwd=str(tmp_path))


def test_connector_reads_only_the_selected_profile(tmp_path):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    first, first_root = make_profile(store, "Example Person")
    second, second_root = make_profile(store, "Other Example")
    job = Workspace(first_root).add_job("Example Employer", "Data Analyst", "Dublin, Ireland",
        "https://example.org/jobs/analyst", "Use SQL and Python to analyse data and write reports. " * 4)
    Workspace(second_root).add_job("Other Employer", "Engineer", "Dublin, Ireland",
        "https://example.org/jobs/engineer", "Build Python services and write tested project code. " * 4)
    connector = CareerConnector(first, profiles=store)
    try:
        summary = connector.summary()
        assert summary["profile_id"] == first
        assert [item["id"] for item in summary["jobs"]] == [job["id"]]
        assert connector.list_jobs()["count"] == 1
        with pytest.raises(ValueError, match="Opportunity not found"):
            connector.get_job("not-in-this-profile")
    finally:
        connector.close()


def test_profile_change_requires_confirmation_and_uses_current_revision(tmp_path):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    pid, root = make_profile(store, "Example Person")
    connector = CareerConnector(pid, profiles=store)
    try:
        proposal = connector.propose_profile_change("add fact: Volunteering | I mentor students in a coding club.")
        assert proposal["changes"]
        with pytest.raises(ValueError, match="explicitly confirm"):
            connector.apply_profile_change(proposal["change_set_id"], proposal["source_revision"], confirmed=False)
        applied = connector.apply_profile_change(proposal["change_set_id"], proposal["source_revision"], confirmed=True)
        assert applied["state"] == "applied" and applied["profile_dirty"] is False
        assert applied["reconciled"]["reconciled"] == applied["entry_ids"]
        assert (root / "data/context/sources" / ("profile-chat-" + proposal["change_set_id"] + ".md")).is_file()
    finally:
        connector.close()


def test_registered_tools_have_no_send_delete_or_submission_capability():
    pytest.importorskip("mcp")
    from backend.scripts.mcp_server import build_server
    import asyncio

    server = build_server(None)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {"workspace_summary", "get_profile", "list_jobs", "prepare_application", "tailor_resume",
            "propose_profile_change", "apply_profile_change"} <= names
    assert not any(any(word in name for word in ("send", "delete", "submit", "outreach", "mail")) for name in names)
