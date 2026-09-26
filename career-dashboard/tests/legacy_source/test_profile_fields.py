"""The Profile form, the knowledge row and what agents read must always agree."""

import json
import shutil
import pytest
from fastapi.testclient import TestClient
from test_career_workspace import workspace, ROOT
from backend.services.workspace_v2 import CareerServices
from backend.services.profile_fields import apply_fields, fields_for, view
from backend.dashboard.app import create_app


@pytest.fixture
def service(workspace, monkeypatch):
    monkeypatch.setattr(CareerServices, "today", staticmethod(lambda: "2026-09-12"))
    shutil.copytree(ROOT / "backend/workflows", workspace.root / "backend/workflows")
    return CareerServices(workspace)


def entry(service, id):
    return next(i for i in service.knowledge() if i["id"] == id)


def lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def test_form_save_writes_title_summary_and_data_together(service):
    saved = service.save_knowledge({
        "kind": "experience", "title": "ignored when fields are sent",
        "fields": {"title": "Data Analyst Intern", "employer": "Acme", "dates": "Jan 2026 - Present",
                   "location": "Remote", "bullets": ["Built dashboards", "  ", "Cleaned data"]},
    })
    assert saved["title"] == "Data Analyst Intern"
    assert saved["summary"] == "Built dashboards\nCleaned data"
    fields = {"title": "Data Analyst Intern", "employer": "Acme", "dates": "Jan 2026 - Present",
              "location": "Remote", "approved_facts": ["Built dashboards", "Cleaned data"]}
    assert {key: saved["data"][key] for key in fields} == fields
    # Chetan's own save is registered at once and recorded in the evidence registry.
    assert saved["review_state"] == "registered" and not service.profile_dirty()
    assert saved["data"]["status"] == "user_reported" and saved["data"]["category"] == "employment"
    assert next(c for c in service.w.evidence()["claims"] if c["id"] == saved["data"]["id"]) == saved["data"]
    # Agents see the new wording and its structured details at once.
    context = next(i for i in service.profile_context() if i["id"] == saved["id"])
    assert context["summary"] == saved["summary"] and context["details"]["employer"] == "Acme"
    # The projection on disk matches the database.
    projected = json.loads((service.w.root / "data/active-profile.json").read_text(encoding="utf-8"))
    assert next(i for i in projected if i["id"] == saved["id"])["summary"] == saved["summary"]


def test_partial_edit_of_a_registry_entry_keeps_its_evidence(service):
    before = service.w.evidence()
    old = entry(service, "EXP-INSOPS-001")
    saved = service.save_knowledge({"kind": "experience", "title": old["title"], "revision": old["revision"],
                                    "fields": {"location": "Remote (US)"}}, old["id"])
    assert saved["data"]["location"] == "Remote (US)"
    assert saved["summary"] == old["summary"] and saved["title"] == old["title"]
    for key in ("status", "approved_external_use", "employer", "dates", "approved_facts"):
        assert saved["data"][key] == old["data"][key]
    assert saved["data"]["source_refs"] == old["data"]["source_refs"] + ["Profile page (dashboard) > edited 2026-09-12"]
    # In the registry only that key changed, plus the Profile page as a source.
    after = service.w.evidence()
    assert next(c for c in after["claims"] if c["id"] == old["id"]) == saved["data"]
    assert [c for c in after["claims"] if c["id"] != old["id"]] == [c for c in before["claims"] if c["id"] != old["id"]]
    assert after["projects"] == before["projects"]
    assert saved["revision"] == old["revision"] + 1


def test_unchanged_save_round_trips_every_imported_entry(service):
    for item in service.knowledge():
        title, summary, data = apply_fields(item["kind"], fields_for(item), item)
        assert title == view(item)["label"], item["id"]
        if item["kind"] == "personal" and isinstance(item["data"].get("value"), dict):
            assert data == item["data"], item["id"]
            continue
        assert lines(summary) == lines(item["summary"]), item["id"]
        assert data == item["data"], item["id"]


def test_summary_only_saves_from_the_chat_still_show_on_the_page(service):
    old = entry(service, "PROJ-P01-IOT")
    saved = service.save_knowledge({**old, "summary": "Rewritten by the Profile chat"}, old["id"])
    shown = view(saved)
    assert shown["fields"]["bullets"] == ["Rewritten by the Profile chat"] and shown["in_sync"]
    assert shown["fields"]["technologies"] == old["data"]["technologies"]
    # A kind whose summary is composed (education) flags the difference instead of hiding it.
    school = entry(service, "EDU-MS-001")
    changed = view(service.save_knowledge({**school, "summary": "MS, thesis track"}, school["id"]))
    assert not changed["in_sync"] and changed["fields"]["degree"] == school["data"]["degree_as_supplied"]


def test_mapping_values_edit_as_lists_and_stay_typed(service):
    old = entry(service, "personal:target_roles")
    form = {spec["key"]: spec["type"] for spec in view(old)["form"]}
    assert form["value.primary"] == "list" and form["value.max_years_required"] == "number"
    saved = service.save_knowledge({"kind": "personal", "title": "Target roles", "revision": old["revision"],
                                    "fields": {"value.primary": ["Data Engineer", "ML Engineer"],
                                               "value.max_years_required": "3"}}, old["id"])
    value = saved["data"]["value"]
    assert value["primary"] == ["Data Engineer", "ML Engineer"] and value["max_years_required"] == 3
    assert value["secondary"] == old["data"]["value"]["secondary"]
    assert list(value) == list(old["data"]["value"])
    assert not saved["summary"].lstrip().startswith("{") and "Primary: Data Engineer, ML Engineer" in saved["summary"]


def test_form_rejects_unknown_fields_bad_types_and_category_changes(service):
    old = entry(service, "EDU-MS-001")
    with pytest.raises(ValueError, match="Unknown profile field"):
        service.save_knowledge({"kind": "education", "title": "x", "revision": old["revision"],
                                "fields": {"gpa": "4.0"}}, old["id"])
    with pytest.raises(ValueError, match="must be text"):
        service.save_knowledge({"kind": "education", "title": "x", "revision": old["revision"],
                                "fields": {"degree": ["a"]}}, old["id"])
    with pytest.raises(ValueError, match="keeps its category"):
        service.save_knowledge({"kind": "fact", "title": "x", "revision": old["revision"],
                                "fields": {"title": "x"}}, old["id"])
    with pytest.raises(ValueError, match="enter a title"):
        service.save_knowledge({"kind": "skill", "title": "x", "fields": {"title": " ", "skills": ["SQL"]}})
    assert entry(service, "EDU-MS-001")["revision"] == old["revision"]


def test_readable_labels_and_groups_replace_imported_ids(service):
    shown = {i["id"]: view(i) for i in service.knowledge()}
    assert shown["SKILL-CLOUD-001"]["label"] == "Cloud" and shown["SKILL-CLOUD-001"]["group"] == "Strong"
    assert shown["SKILL-NEVER-001"]["group"] == "Honest gaps"
    assert shown["CONTACT-EMAIL-001"]["label"] == "Email"
    assert shown["PUB-001"]["label"] == "Publication" and shown["PUB-001"]["status"] == "hold"
    assert shown["COURSEWORK-MS-001"]["label"] == "Academic coursework"
    assert shown["personal:github"]["label"] == "GitHub"
    assert shown["personal:email"]["group"] == "Contact & identity"
    assert shown["EXP-INSOPS-001"]["label"] == "Data Engineering Intern"
    assert shown["EXP-SOLITON-PE-001"]["extra"][0]["label"] == "Earlier dates (superseded)"


def test_profile_api_is_readable_and_saves_form_fields(service):
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        profile = client.get("/api/v2/profile").json()
        assert set(profile["schema"]) == {"personal", "experience", "project", "skill", "education",
                                          "certification", "fact"}
        for item in profile["items"]:
            assert item["label"] and item["form"] and "title" in item["fields"]
            for value in item["fields"].values():
                assert not (isinstance(value, str) and value.lstrip().startswith("{")), item["id"]
        roles = next(i for i in profile["items"] if i["id"] == "EXP-TA-001")
        edited = client.put("/api/v2/profile/items/EXP-TA-001", json={
            "kind": "experience", "title": roles["fields"]["title"], "revision": roles["revision"],
            "fields": {**roles["fields"], "bullets": ["Graded assignments", "Held office hours"]},
        })
        assert edited.status_code == 200
        created = client.post("/api/v2/profile/items", json={
            "kind": "certification", "title": "AWS Cloud Practitioner",
            "fields": {"title": "AWS Cloud Practitioner", "issuer": "Amazon Web Services", "date": "Sep 2026"},
        })
        assert created.status_code == 201
        assert client.post("/api/v2/profile/items", json={
            "kind": "skill", "title": "x", "fields": {"nope": 1}}).status_code == 400
        profile = client.get("/api/v2/profile").json()
        shown = {i["id"]: i for i in profile["items"]}
        assert shown["EXP-TA-001"]["summary"] == "Graded assignments\nHeld office hours"
        assert shown["EXP-TA-001"]["fields"]["bullets"] == ["Graded assignments", "Held office hours"]
        cert = shown[created.json()["id"]]
        assert cert["summary"] == "Amazon Web Services · Sep 2026" and cert["data"]["issuer"] == "Amazon Web Services"
        # Form saves apply at once: nothing waits for review, and the response says where it went.
        assert profile["pending"] == [] and not profile["profile_dirty"]
        assert "Evidence registry (evidence.yml)" in edited.json()["synced"]["updated"]
        assert shown["EXP-INSOPS-001"]["locked"] and not shown[cert["id"]]["locked"]
        # The legacy free-text shape still saves exactly as before, and waits for review.
        legacy = client.post("/api/v2/profile/items", json={"kind": "skill", "title": "Airbyte", "summary": "Learning"})
        assert legacy.status_code == 201 and legacy.json()["summary"] == "Learning"
        pending = {p["id"]: p for p in client.get("/api/v2/profile").json()["pending"]}
        assert pending[legacy.json()["id"]]["label"] == "Airbyte"
