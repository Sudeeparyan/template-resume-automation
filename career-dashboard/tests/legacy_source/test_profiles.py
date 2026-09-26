"""Profiles: the registry, the shell that isolates them, reset and delete, and the intake
that builds a new profile from uploaded documents — all without touching Chetan's data."""

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from test_career_workspace import workspace  # noqa: E402,F401 - fixture: a disposable copy of Chetan's workspace
from test_intake_build import built_profile  # noqa: E402
from backend.ai.agents import schemas  # noqa: E402
from backend.dashboard.shell import create_shell  # noqa: E402
from backend.profiles import ProfileError, ProfileStore, slug  # noqa: E402
from backend.services.intake import api as intake_api  # noqa: E402
from backend.services import schedule_tasks  # noqa: E402
from fixtures.intake_draft import section  # noqa: E402

POSTING = {
    "company": "Acme Analytics", "title": "Graduate Data Analyst", "location": "Cork, Ireland",
    "url": "https://jobs.lever.co/acme/123",
    "description": "Graduate Data Analyst working with SQL, Python and Power BI dashboards on banking KPIs. " * 3,
}


@pytest.fixture
def store(workspace, tmp_path):
    return ProfileStore(base=tmp_path / "profiles", legacy_root=workspace.root)


@pytest.fixture
def shell(store, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    monkeypatch.setattr(schedule_tasks, "supported", lambda: True)
    with TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1") as client:
        yield client


def ready(store, name="Srikanth Nadesharam"):
    """A second profile built from the fixture draft (no AI)."""
    profile = store.create(name)
    built_profile(store.root_for(profile["id"]))
    store.update(profile["id"], state="ready", country="ie")
    return profile["id"]


def test_chetan_is_the_locked_backup_profile(store):
    profiles = store.list()
    assert profiles[0]["id"] == "chetan" and profiles[0]["locked"] and profiles[0]["state"] == "ready"
    assert store.root_for("chetan") == store.legacy_root
    with pytest.raises(ProfileError, match="locked"):
        store.check_confirmation("chetan", "Chetan Babu M")
    with pytest.raises(ProfileError, match="locked"):
        store.wipe("chetan")


def test_a_new_profile_is_an_empty_folder_of_its_own(store):
    profile = store.create("Srikanth Nadesharam")
    assert profile["id"] == slug("Srikanth Nadesharam") == "srikanth-nadesharam"
    assert profile["state"] == "onboarding" and not profile["locked"] and profile["initials"] == "SN"
    root = store.root_for(profile["id"])
    assert root == store.base / "srikanth-nadesharam" and (root / "data/context/files").is_dir()
    assert store.create("Srikanth Nadesharam")["id"] == "srikanth-nadesharam-2"


def test_shell_lists_profiles_and_serves_each_page(shell):
    listing = shell.get("/api/profiles").json()
    assert [p["id"] for p in listing["profiles"]] == ["chetan"]
    health = shell.get("/api/health").json()
    assert health["app"] == "chetan-career-workspace" and health["profiles"] is True
    assert shell.get("/", follow_redirects=False).headers["location"] == "/p/chetan/"
    assert shell.get("/p/chetan/").text == "<html>app</html>"
    assert shell.get("/p/chetan/api/v2/summary").status_code == 200


def test_reset_and_delete_need_the_exact_name_and_never_touch_chetan(shell, store):
    assert "locked" in shell.post("/api/profiles/chetan/reset", json={"confirm": "Chetan Babu M"}).json()["detail"]
    assert shell.request("DELETE", "/api/profiles/chetan", json={"confirm": "Chetan Babu M"}).status_code == 400
    profile_id = ready(store)
    wrong = shell.post(f"/api/profiles/{profile_id}/reset", json={"confirm": "srikanth"})
    assert wrong.status_code == 400 and "exactly" in wrong.json()["detail"]
    assert (store.root_for(profile_id) / "data/config/profile.yml").exists()


def test_profiles_never_share_jobs_chats_or_settings(shell, store):
    profile_id = ready(store)
    saved = shell.post(f"/p/{profile_id}/api/v2/jobs", json=POSTING).json()
    assert saved["job"]["company"] == "Acme Analytics"
    theirs = shell.get(f"/p/{profile_id}/api/v2/summary").json()
    chetans = shell.get("/p/chetan/api/v2/summary").json()
    assert [j["company"] for j in theirs["jobs"]] == ["Acme Analytics"]
    assert "Acme Analytics" not in [j["company"] for j in chetans["jobs"]]
    shell.put(f"/p/{profile_id}/api/v2/goals", json={"weekly_target": 7, "workdays": [0, 1, 2], "start_date": "2026-09-21"})
    assert shell.get(f"/p/{profile_id}/api/v2/goals").json()["weekly_target"] == 7
    assert shell.get("/p/chetan/api/v2/goals").json()["weekly_target"] != 7
    # Each profile's files and database are its own.
    assert (store.root_for(profile_id) / "data/career.db").exists()
    assert (store.root_for(profile_id) / "data/pipeline.md").read_text(encoding="utf-8").count("Acme Analytics") == 1
    legacy = store.legacy_root / "data/pipeline.md"
    assert not legacy.exists() or "Acme Analytics" not in legacy.read_text(encoding="utf-8")
    # This PC's Gmail is the backup profile's owner's mailbox: no other profile can read it.
    mail = shell.post(f"/p/{profile_id}/api/v2/agents/run", json={"kind": "email"})
    assert mail.status_code == 400 and "backup profile" in mail.json()["detail"]
    assert theirs["mail"]["available"] is False and chetans["mail"]["available"] is True
    assert "email" not in [a["id"] for a in theirs["agents"]] and "email" in [a["id"] for a in chetans["agents"]]


def test_a_us_only_posting_is_turned_away_for_the_ireland_profile(shell, store):
    profile_id = ready(store)
    refused = shell.post(f"/p/{profile_id}/api/v2/jobs", json={**POSTING, "url": "https://jobs.lever.co/acme/456",
                                                              "description": POSTING["description"] + " Applicants must be EU/EEA citizens only."}).json()
    assert refused["excluded"] and "citizenship" in refused["reason_label"].casefold()
    # Restoring it (a wrong reading) keeps the Irish wording: no H-1B on the card.
    excluded = shell.get(f"/p/{profile_id}/api/v2/excluded").json()
    rows = excluded if isinstance(excluded, list) else excluded.get("excluded") or excluded.get("items") or []
    restored = shell.post(f"/p/{profile_id}/api/v2/excluded/{rows[0]['id']}/restore").json()
    label = restored["job"]["sponsor_evidence"]["label"]
    assert "H-1B" not in label and "work permits" in label


def test_reset_brings_a_profile_back_to_zero_and_delete_removes_it(shell, store, no_task_scheduler):
    profile_id = ready(store)
    shell.get(f"/p/{profile_id}/api/v2/summary")  # the profile app is open
    reset = shell.post(f"/api/profiles/{profile_id}/reset", json={"confirm": "Srikanth Nadesharam"})
    assert reset.status_code == 200 and reset.json()["profile"]["state"] == "onboarding"
    root = store.root_for(profile_id)
    assert not (root / "data/config/profile.yml").exists() and not (root / "data/career.db").exists()
    assert shell.get(f"/p/{profile_id}/api/v2/summary").status_code == 409
    assert any("Unregister-ScheduledTask" in script and f"({profile_id})" in script for script in no_task_scheduler)
    deleted = shell.request("DELETE", f"/api/profiles/{profile_id}", json={"confirm": "Srikanth Nadesharam"})
    assert deleted.status_code == 200 and [p["id"] for p in deleted.json()["profiles"]] == ["chetan"]
    assert not root.exists()
    assert shell.get(f"/p/{profile_id}/api/v2/summary").status_code == 404


class StubIntakeTeam:
    """Stands in for the AI: the first section yields the fixture's facts, the rest nothing."""

    def __init__(self):
        self.calls = []

    def run(self, name, payload):
        self.calls.append(name)
        if name == "intake_auditor":
            return schemas.IntakeAudit(missed=[])
        first = payload["section"].startswith("1 of")
        return schemas.IntakeFacts.model_validate(section() if first else {})


def test_the_intake_builds_a_ready_profile_from_an_uploaded_document(shell, store, monkeypatch, no_task_scheduler):
    team = StubIntakeTeam()
    monkeypatch.setattr(intake_api, "team_factory", lambda profiles: (lambda on_usage: team))
    # create_shell already captured the factory; build a fresh shell so the stub is used.
    dist = store.base.parent / "dist"
    with TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1") as client:
        profile = client.post("/api/profiles", json={"name": "Srikanth Nadesharam"}).json()["profile"]
        pid = profile["id"]
        assert client.get(f"/api/profiles/{pid}/intake").json()["state"] == "empty"
        text = ("# About me\n\nMy name is Srikanth Nadesharam and I live in Cork, Ireland.\n\n"
                "I worked at JRB Infotech as an Associate Data Analyst from June 2022 to September 2024.\n")
        uploaded = client.post(f"/api/profiles/{pid}/intake/files?name=About%20Me.md", content=text.encode()).json()
        assert [f["name"] for f in uploaded["files"]] == ["About Me.md"]
        assert client.post(f"/api/profiles/{pid}/intake/files?name=virus.exe", content=b"x").status_code == 400
        client.post(f"/api/profiles/{pid}/intake/start")
        for _ in range(100):
            state = client.get(f"/api/profiles/{pid}/intake").json()
            if state["state"] not in ("reading",):
                break
            time.sleep(0.05)
        assert state["state"] == "review", state
        assert state["draft"]["country_pack"] == "ie" and state["draft"]["counts"]["projects"] == 3
        assert state["draft"]["coverage"]["accounted"] == state["draft"]["coverage"]["blocks"]
        assert "profile_extractor" in team.calls and "intake_auditor" in team.calls
        # The review card's corrections land in the draft.
        client.put(f"/api/profiles/{pid}/intake/draft", json={"preferred_name": "Srikanth", "roles": ["Data Analyst", "Data Scientist"]})
        built = client.post(f"/api/profiles/{pid}/intake/build").json()
        assert built["profile"]["state"] == "ready" and built["profile"]["country"] == "ie"
        root = store.root_for(pid)
        assert (root / "data/context/sources/About Me.md").exists() and (root / "data/context/files/About Me.md").exists()
        summary = client.get(f"/p/{pid}/api/v2/summary").json()
        assert summary["jobs"] == []
        profile_page = client.get(f"/p/{pid}/api/v2/profile").json()
        assert profile_page["configuration"]["candidate"]["full_name"] == "Srikanth Nadesharam"
        assert profile_page["configuration"]["target_roles"]["primary"] == ["Data Analyst", "Data Scientist"]
        assert any(item["id"].startswith("EXP-JRB-INFOTECH") for item in profile_page["items"])
        assert any("Register-ScheduledTask" in s and f"--profile {pid}" in s for s in no_task_scheduler)
        schedule = store.get(pid)["schedule"]
        assert schedule["time"] == "07:20"
        # Building again is refused: the profile is ready; new facts come through the Assistant.
        assert client.post(f"/api/profiles/{pid}/intake/files?name=more.md", content=b"More").status_code == 400
        # Chetan's registry and database were not touched.
        chetan = client.get("/p/chetan/api/v2/profile").json()
        assert chetan["configuration"]["candidate"]["full_name"] == "Chetan Babu M"
        assert not any(item["id"].startswith("EXP-JRB") for item in chetan["items"])


def test_daily_tasks_are_staggered_after_chetans():
    assert schedule_tasks.next_slot([]) == "07:20"
    assert schedule_tasks.next_slot(["07:20"]) == "07:40"
    assert schedule_tasks.next_slot(["07:20", "07:40", None]) == "08:00"
    assert schedule_tasks.task_name("srikanth", "Srikanth Nadesharam") == "Career Daily Job Search - Srikanth Nadesharam (srikanth)"
