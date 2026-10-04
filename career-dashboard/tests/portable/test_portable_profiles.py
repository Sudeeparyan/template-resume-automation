"""Portable profile and API regressions; every mutation stays in a disposable folder."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "backend/scripts"))

from backend.countries import market_for_location, require_known_authorization, target_markets_for  # noqa: E402
from backend.dashboard.shell import create_shell  # noqa: E402
from backend.profiles import ProfileStore  # noqa: E402
from backend.services.sponsorship import index_for  # noqa: E402
from backend.services.workspace_v2 import CareerServices  # noqa: E402
from career import Workspace  # noqa: E402


def _profile(root: Path, markets: list[str], authorization: dict | None = None) -> None:
    import yaml

    path = root / "data/config/profile.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"country_pack": markets[0], "target_markets": markets,
                                    "candidate": {"full_name": "Example Person"},
                                    "work_authorization_by_market": authorization or {}}), encoding="utf-8")


def test_fresh_install_has_no_candidate_and_source_api_is_isolated(tmp_path):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    assert store.list() == []
    assert store.last_used() == ""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>fresh install</html>", encoding="utf-8")
    with TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1") as client:
        assert client.get("/").status_code == 200
        left = client.post("/api/profiles", json={"name": "Example Person"}).json()["profile"]
        right = client.post("/api/profiles", json={"name": "Other Person"}).json()["profile"]
        assert left["target_markets"] == ["ie"]
        assert right["state"] == "onboarding"
        base = f"/api/profiles/{left['id']}"
        upload = client.post(base + "/sources?name=resume.md", content=b"A documented role in Cork.")
        assert upload.status_code == 200
        source = upload.json()["sources"][0]
        version = client.post(base + "/sources/" + source["id"] + "/versions", content=b"A revised documented role in Cork.")
        assert version.status_code == 200
        assert version.json()["source"]["current_version"] == 2
        note = client.post(base + "/sources/notes", json={"name": "Preferences", "text": "I prefer hybrid work."})
        assert note.status_code == 200
        assert len(client.get(base + "/sources").json()["sources"]) == 2
        assert client.get(f"/api/profiles/{right['id']}/sources").json()["sources"] == []
        assert client.put(base + "/sources/" + source["id"], json={"active": False}).status_code == 200
        assert len([s for s in client.get(base + "/sources").json()["sources"] if s["active"]]) == 1
        assert client.post(base + "/sources?name=bad.pdf", content=b"x" * (20 * 1024 * 1024 + 1)).status_code == 400
        assert client.post(base + "/build-runs", json={"target_markets": ["ca"]}).status_code == 400
    assert (store.root_for(left["id"]) / "data/source_library/sources.json").is_file()
    assert not (store.root_for(right["id"]) / "data/source_library/sources.json").exists()


def test_existing_intake_and_chat_uploads_use_the_profile_source_library(tmp_path):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    with TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1") as client:
        first = client.post("/api/profiles", json={"name": "Example Person"}).json()["profile"]["id"]
        second = client.post("/api/profiles", json={"name": "Other Person"}).json()["profile"]["id"]
        base = f"/api/profiles/{first}"
        assert client.post(base + "/intake/files?name=resume.md",
                           content=b"Example Person worked in customer support.").status_code == 200
        chat = client.post(base + "/intake/chat/files?name=career.txt",
                           content=b"Example Person prefers customer support roles.")
        assert chat.status_code == 200, chat.text
        pending = chat.json()["pending"]
        assert pending["key"] == "read"
        assert len(client.get(base + "/sources").json()["sources"]) == 2
        stale = client.post(base + "/intake/chat/answer", json={"question_id": "old-question",
                                                               "choices": ["Read now"]})
        assert stale.status_code == 400
        assert client.get(f"/api/profiles/{second}/sources").json()["sources"] == []


def test_dual_market_routing_and_authorization_gate(tmp_path, us_enabled):
    root = tmp_path / "profile"
    _profile(root, ["ie", "us"], {"ie": {"status": "authorized", "citizenship": "noncitizen",
                                         "needs_sponsorship_later": "no"},
                                  "us": {"status": "unknown", "citizenship": "unknown"}})
    assert target_markets_for(root) == ["ie", "us"]
    assert market_for_location(root, "Dublin, Ireland") == "ie"
    assert market_for_location(root, "Boston, MA") == "us"
    with pytest.raises(ValueError, match="not selected"):
        market_for_location(root, "Canada", "ca")
    require_known_authorization(root, "ie")
    with pytest.raises(ValueError, match="United States"):
        require_known_authorization(root)
    assert type(index_for(root, "ie")).__name__ == "PermitHistoryIndex"
    assert type(index_for(root, "us")).__name__ == "SponsorIndex"


@pytest.mark.parametrize("location,requested", [
    ("Toronto, Canada", None),
    ("Remote", None),
    ("Dublin, Ireland", "us"),
])
def test_manual_posting_requires_a_matching_selected_market(tmp_path, location, requested, us_enabled):
    _profile(tmp_path, ["ie", "us"], {
        "ie": {"status": "authorized", "citizenship": "citizen"},
        "us": {"status": "authorized", "citizenship": "citizen"},
    })
    context = tmp_path / "data/context"
    context.mkdir(parents=True)
    (context / "evidence.yml").write_text("claims: []\nprojects: []\n", encoding="utf-8")
    service = CareerServices(Workspace(tmp_path))
    posting = {"company": "Example Company", "title": "Support Specialist", "location": location,
               "url": "https://example.org/jobs/support-specialist", "description": "A sample job description."}
    if requested:
        posting["market"] = requested
    with pytest.raises(ValueError, match="Posting location does not establish"):
        service.add_posting(posting, source="manual")
    assert service.w.jobs() == []


def test_bundled_us_sponsor_history_is_available_on_a_fresh_profile(tmp_path):
    root = tmp_path / "profile"
    _profile(root, ["us"])
    sponsor = index_for(root, "us").lookup("Amazon Com Services LLC")
    assert sponsor["found"] is True
    assert sponsor["approvals"] > 0
    assert sponsor["source"] == "USCIS H-1B Employer Data Hub"


def test_legacy_job_migrates_to_market_without_changing_history(tmp_path):
    root = tmp_path / "profile"
    _profile(root, ["us"])
    db_path = root / "data/career.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, company TEXT NOT NULL, title TEXT NOT NULL,
                   location TEXT NOT NULL, url TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
                   status TEXT NOT NULL DEFAULT 'saved', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                   notes TEXT NOT NULL DEFAULT '', application_date TEXT, selected_project_id TEXT, folder TEXT,
                   verification TEXT NOT NULL DEFAULT 'not_verified')""")
        db.execute("INSERT INTO jobs(id,company,title,location,url,description,created_at,updated_at) "
                   "VALUES('job-1','Example','Engineer','Boston, MA','https://example.org/jobs/1','Full description','old','old')")
    workspace = Workspace(root)
    with workspace.connect() as db:
        row = dict(db.execute("SELECT id,market,status,created_at FROM jobs WHERE id='job-1'").fetchone())
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert row == {"id": "job-1", "market": "us", "status": "saved", "created_at": "old"}
