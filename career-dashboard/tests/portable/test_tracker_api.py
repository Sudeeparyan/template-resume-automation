"""The Tracker and job-source routes: the person's floor, exports, saving, alerts and keys that are never echoed."""

from __future__ import annotations

import io
import zipfile

import yaml
from fastapi.testclient import TestClient

import backend.profiles
from backend.ai import keys
from backend.dashboard.shell import create_shell
from backend.market.store import MarketStore
from backend.profiles import ProfileStore
from backend.services import job_sources


def profile_client(tmp_path, monkeypatch):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    pid = store.create("Example Person")["id"]
    root = store.root_for(pid)
    (root / "data/config/profile.yml").write_text(yaml.safe_dump({
        "country_pack": "ie", "target_markets": ["ie"], "candidate_revision": "2026-10-04.1",
        "candidate": {"full_name": "Example Person", "timezone": "Europe/Dublin"},
        "work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "citizen"}},
    }), encoding="utf-8")
    (root / "data/context/evidence.yml").write_text(yaml.safe_dump({
        "candidate_revision": "2026-10-04.1",
        "claims": [{"id": "FACT-EXAMPLE", "category": "other", "approved_external_use": "Example fixture fact."}],
        "projects": []}), encoding="utf-8")
    store.update(pid, state="ready")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    monkeypatch.setattr(backend.profiles, "store", lambda: store)
    return TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1"), f"/p/{pid}/api/v2"


def test_tracker_routes_list_export_save_and_alert(tmp_path, monkeypatch):
    MarketStore().record_postings([
        job_sources.make_posting("Acme Analytics", "Graduate Data Analyst", "https://boards.greenhouse.io/acme/jobs/5",
                                 "Dublin, Ireland", "Build dashboards with SQL.", source="directory", source_kind="employer_feed"),
        {**job_sources.make_posting("Unlisted Widgets", "Data Analyst", "https://ie.jooble.org/desc/2", "Cork",
                                    "Excel reporting", source="jooble", source_kind="aggregator"),
         "lead": True, "attribution": "Jobs by Jooble"}])
    client, base = profile_client(tmp_path, monkeypatch)
    with client:
        listed = client.get(base + "/tracker").json()
        assert listed["all"] == 2 and listed["floor_eur"] > 0 and "not immigration advice" in listed["note"]
        book = client.get(base + "/tracker/export", params={"format": "xlsx"})
        assert book.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert zipfile.ZipFile(io.BytesIO(book.content)).testzip() is None
        assert client.get(base + "/tracker/export", params={"format": "csv", "county": "Cork"}).text.count("\n") == 2
        lead = next(row for row in listed["rows"] if row["lead"])
        refused = client.post(base + "/tracker/save", json={"key": lead["key"]})
        assert refused.status_code == 400 and "lead" in refused.json()["detail"]
        assert client.post(base + "/tracker/alerts", json={"name": "Cork data", "filters": {"county": "Cork"}}).status_code == 200
        assert client.get(base + "/tracker/alerts").json()["alerts"][0]["matches"] == 1


def test_source_keys_are_saved_through_the_key_store_and_never_returned(tmp_path, monkeypatch):
    saved = {}
    monkeypatch.setattr(keys, "save", lambda root, name, value: saved.update({name: value}))  # never the real .env
    monkeypatch.setattr(keys, "load", lambda root: dict(saved))
    client, base = profile_client(tmp_path, monkeypatch)
    with client:
        answer = client.put(base + "/sources/keys/JOOBLE_API_KEY", json={"value": "example-jooble-key-0000"})
        assert answer.status_code == 200 and saved == {"JOOBLE_API_KEY": "example-jooble-key-0000"}
        assert "example-jooble-key" not in answer.text
        jooble = next(row for row in answer.json()["sources"] if row["id"] == "jooble")
        assert jooble["ready"] and jooble["attribution"] == "Jobs by Jooble"
        assert client.put(base + "/sources/keys/OPENAI_API_KEY", json={"value": "x" * 30}).status_code == 400


def test_the_tracker_switch_turns_its_api_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREER_FEATURES", "-tracker")
    client, base = profile_client(tmp_path, monkeypatch)
    with client:
        answer = client.get(base + "/tracker")
    assert answer.status_code == 400 and "switched off" in answer.json()["detail"]
