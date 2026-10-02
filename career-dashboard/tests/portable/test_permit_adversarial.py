"""Posting evidence and unknown authorization must not be inferred from unrelated wording."""

import sys
from datetime import date, timedelta

import pytest
import yaml

from backend.countries import load_pack, require_known_authorization
from backend.services.sponsorship import load_rules, screen
from backend.services.permit_assessment import assess


def rules(market):
    return load_rules(str(load_pack(market).template("sponsorship.yml")))


@pytest.mark.parametrize("market", ["ie", "us"])
@pytest.mark.parametrize("posting", [
    "We do not require citizenship and security clearance.",
    "This role does not require sponsorship, citizenship or a security clearance.",
    "We cannot sponsor a team or event.",
    "We sponsor an event every year.",
    "We will sponsor the local team.",
])
def test_unrelated_sponsorship_and_negated_requirement_lists_are_silent(market, posting):
    result = screen(posting, rules(market))
    assert result.verdict == "KEEP"
    assert result.reason == "silent"


@pytest.mark.parametrize("posting", [
    "No C2C engagements. Visa sponsorship is available.",
    "No third-party arrangements. Visa sponsorship is available.",
    "We sponsor an event, but we cannot provide visa sponsorship.",
])
def test_contract_models_and_event_sponsors_do_not_override_the_actual_permit_statement(posting):
    result = screen(posting, rules("us"))
    if "cannot" in posting:
        assert result.verdict == "EXCLUDED"
    else:
        assert result.verdict == "KEEP"
        assert result.reason == "explicit_sponsorship"


@pytest.mark.parametrize("posting", [
    "We support current or future visa sponsorship.",
    "We support current or future H-1B sponsorship.",
])
def test_temporal_sponsorship_wording_alone_is_not_a_refusal(posting):
    assert screen(posting, rules("us")).verdict == "KEEP"


@pytest.mark.parametrize("posting", [
    "Authorization to work in the United States without current or future sponsorship.",
    "Must be authorized to work in the U.S. without the need for current or future visa sponsorship.",
    "Applicants must not require present or future employer sponsorship.",
])
def test_explicit_current_and_future_refusals_still_exclude(posting):
    assert screen(posting, rules("us")).verdict == "EXCLUDED"


@pytest.mark.parametrize("posting", [
    "Applicants must be U.S. citizens only.",
    "We do not provide U.S.A. visa sponsorship.",
    "Benefits include " + "paid leave, " * 50 + "but we cannot provide visa sponsorship.",
])
def test_restriction_quote_keeps_every_original_character_and_the_end_of_long_sentences(posting):
    result = screen(posting, rules("us"))
    assert result.verdict == "EXCLUDED"
    assert result.sentence == posting


@pytest.mark.parametrize("authorization", ["missing", {}, None, [], "unknown"])
def test_cli_does_not_screen_a_selected_profile_with_missing_or_malformed_authorization(tmp_path, monkeypatch, authorization):
    import backend.profiles
    import workspace
    from backend.profiles import ProfileStore

    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    profile = store.create("Example Person")
    root = store.root_for(profile["id"])
    config = {"candidate": {"full_name": "Example Person"}, "country_pack": "ie", "target_markets": ["ie"]}
    if authorization != "missing":
        config["work_authorization_by_market"] = authorization
    (root / "data/config/profile.yml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (root / "data/context/evidence.yml").write_text("claims: []\nprojects: []\n", encoding="utf-8")
    posting = tmp_path / "posting.txt"
    posting.write_text("Data Analyst in Dublin. SQL and Power BI required.", encoding="utf-8")
    monkeypatch.setattr(backend.profiles, "store", lambda: store)
    monkeypatch.setattr(sys, "argv", ["workspace.py", "sponsor-check", "--profile", profile["id"],
                                    "--company", "Example Services", "--file", str(posting), "--market", "ie"])
    with pytest.raises(ValueError, match="Confirm work authorization"):
        workspace.main()


@pytest.mark.parametrize("authorization,expected", [
    ({"status": "authorized", "citizenship": "citizen"}, "pass"),
    ({"status": "authorized", "citizenship": "noncitizen", "needs_sponsorship_later": "no"}, "pass"),
    ({"status": "authorized", "citizenship": "noncitizen", "needs_sponsorship_later": "yes"}, "fail"),
    ({"status": "needs_sponsorship", "citizenship": "noncitizen", "needs_sponsorship_later": "no"}, "fail"),
    ({}, "fail"),
])
def test_informational_permit_refusal_obstacle_agrees_with_the_confirmed_profile(authorization, expected):
    quote = "We do not provide visa sponsorship."
    result = assess({"description": "Data Analyst in Dublin."}, {"kind": "unknown"},
                    {"work_authorization_by_market": {"ie": authorization}},
                    {"state": "refuses", "quote": quote}, on=date(2026, 10, 2))
    employer = next(check for check in result["checks"] if check["id"] == "employer_support")
    assert employer["state"] == expected
    assert quote in employer["note"]
    assert (result["state"] == "obstacle") == (expected == "fail")


def test_informational_permission_review_handles_missing_statement_and_invalid_expiry():
    result = assess({"description": "Data Analyst in Dublin."}, {"kind": "unknown"},
                    {"work_authorization_by_market": {"ie": {"valid_until": 20270101}}}, {}, on=date(2026, 10, 2))
    checks = {check["id"]: check for check in result["checks"]}
    assert checks["validity"]["state"] == "unknown"
    assert checks["employer_support"]["state"] == "unknown"


@pytest.mark.parametrize("expiry,block", [
    ((date.today() - timedelta(days=3)).isoformat(), "expired"),
    ((date.today() + timedelta(days=3)).isoformat(), None),
    ("not confirmed", "validity date"),
    (20270101, "validity date"),
])
def test_recorded_expiry_cannot_be_ignored_when_screening_current_permission(tmp_path, expiry, block):
    config = tmp_path / "data/config/profile.yml"
    config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump({"country_pack": "ie", "target_markets": ["ie"],
        "work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "noncitizen",
                                                "needs_sponsorship_later": "yes", "valid_until": expiry}}}), encoding="utf-8")
    if block:
        with pytest.raises(ValueError, match=block):
            require_known_authorization(tmp_path)
    else:
        require_known_authorization(tmp_path)


def test_a_malformed_profile_is_unknown_authorization_instead_of_a_type_error(tmp_path):
    config = tmp_path / "data/config/profile.yml"
    config.parent.mkdir(parents=True)
    config.write_text("not a profile mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Confirm work authorization"):
        require_known_authorization(tmp_path)


@pytest.mark.parametrize("location", [
    "Belfast, Ireland", "Derry, Ireland", "County Antrim, Ireland", "Newry, Ireland",
    "Cork, South Africa", "Clare, Australia", "Dublin, Canada", "Kerry, Wales",
])
def test_northern_ireland_and_foreign_namesakes_cannot_gain_an_irish_market_from_a_vague_label(location):
    assert not load_pack("ie").location_ok(location)


@pytest.mark.parametrize("location", [
    "Belfast / Dublin, Ireland", "Belfast, Northern Ireland; Cork", "Dublin, IE; London, UK",
    "Remote, Ireland", "IE", "Cork, Ireland; Cape Town, South Africa",
])
def test_explicit_republic_and_multilocation_options_still_establish_the_irish_market(location):
    assert load_pack("ie").location_ok(location)
