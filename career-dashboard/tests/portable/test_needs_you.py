"""What only the person can do now (services/needs_you.py): one list for the Dashboard and the morning list."""

from __future__ import annotations

import json
from datetime import date, timedelta

import yaml

from backend.services import needs_you
from test_hunt import JD, ireland_profile, posting


def ids(services):
    return [item["id"] for item in needs_you.items(services)]


def set_ie(services, **facts):
    path = services.w.root / "data/config/profile.yml"
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    profile["work_authorization_by_market"]["ie"].update(facts)
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")


def test_a_ready_profile_with_an_ai_app_needs_nothing(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    monkeypatch.setattr("backend.ai.any_provider_configured", lambda root: True)
    assert needs_you.items(services) == []
    assert services.summary()["needs_you"] == []


def test_each_thing_only_the_person_can_do_is_listed_with_its_page(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    monkeypatch.setattr("backend.ai.any_provider_configured", lambda root: False)
    soon = (date.today() + timedelta(days=30)).isoformat()
    set_ie(services, permission_type="stamp_1g", valid_until=soon, valid_until_confirmed=True)
    services.add_posting({**posting(3), "description": JD.replace("Base salary: EUR 42,000 per year.\n", "")}, source="discovery")
    from backend.services.hunt import Hunt

    Hunt(services, runner=None, pipeline=None)  # creates the hunt table
    with services.w.connect() as db:
        db.execute("INSERT INTO hunt_runs(id,state,config,progress,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   ("h1", "completed", json.dumps({"allow_paid": False}),
                    json.dumps({"passes": [{"state": "skipped", "note": "No AI plan was free before the search time ran out."},
                                           {"state": "done"}]}), services.now(), services.now()))
    found = {item["id"]: item for item in needs_you.items(services)}
    assert list(found) == ["stamp_1g_expiry", "ai_setup", "pay_unconfirmed", "hunt_waited_for_free_ai"]
    assert f"expiry is {soon}, in 30 days" in found["stamp_1g_expiry"]["text"] and found["stamp_1g_expiry"]["url"]
    assert "advice" not in found["stamp_1g_expiry"]["text"].casefold()
    assert found["pay_unconfirmed"]["where"] == "daily" and "1 saved job states no pay" in found["pay_unconfirmed"]["text"]
    assert "skipped 1 AI search " in found["hunt_waited_for_free_ai"]["text"]


def test_missing_work_authorization_comes_first(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    monkeypatch.setattr("backend.ai.any_provider_configured", lambda root: True)
    set_ie(services, permission_type="stamp_1g", valid_until="", valid_until_confirmed=False)
    first = needs_you.items(services)[0]
    assert first["id"] == "work_authorization" and "Stamp 1G expiry" in first["text"] and first["where"] == "profile"
