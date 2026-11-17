"""Confirmed chat answers become sourced profile evidence and synchronized drafts."""

import yaml

from backend.chat_changes import ChatChangeService
from backend.profiles import ProfileStore
from backend.services.resume_studio import ResumeStudio
from backend.services.workspace_v2 import CareerServices
from test_opportunity_interfaces import make_profile


def test_confirmed_profile_answer_is_sourced_and_reconciled_immediately(tmp_path):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    pid, root = make_profile(store, "Example Person")
    service = CareerServices(__import__("career").Workspace(root))
    chats = ChatChangeService(service, ResumeStudio(service))
    request = "add fact: Volunteering | I volunteer at a local coding club on weekends."
    preview = chats.profile_preview(request, "request-1", service.profile_revision())
    assert preview["state"] == "preview"

    applied = chats.profile_apply(preview["id"], "request-1", preview["source_revision"])
    assert applied["state"] == "applied"
    assert applied["reconciled"]["reconciled"] == applied["evidence_ids"]
    assert service.profile_dirty() is False
    entry = next(item for item in service.knowledge() if item["id"] == applied["evidence_ids"][0])
    assert entry["review_state"] == "registered"
    source = root / entry["source"]
    assert source.is_file()
    assert request in source.read_text(encoding="utf-8")

    evidence = yaml.safe_load((root / "data/context/evidence.yml").read_text(encoding="utf-8"))
    claim = next(claim for claim in evidence["claims"] if "coding club" in str(claim))
    assert entry["source"] + "#answer" in claim["source_refs"]
    assert claim["value"] == "I volunteer at a local coding club on weekends."

    again = chats.profile_apply(preview["id"], "request-1", preview["source_revision"])
    assert again["id"] == preview["id"]
    assert sum("coding club" in str(claim) for claim in evidence["claims"]) == 1


def test_protected_assistant_actions_are_not_blanket_auto_applied():
    from backend.services.assistant import ALWAYS_CONFIRM_TOOLS

    assert {"update_job", "remove_job", "resolve_mail"} <= ALWAYS_CONFIRM_TOOLS
