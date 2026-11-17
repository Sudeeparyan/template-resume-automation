"""Market selection must not turn a home address or current permit into an eligibility claim."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "backend/scripts"))

from backend.countries import load_pack, require_known_authorization  # noqa: E402
from backend.services.intake.coverage import ledger  # noqa: E402
from backend.services.intake.build import authorization_mode, build_profile, build_registry, draft_authorization_mode, file_set, preferred_locations  # noqa: E402
from backend.services.sponsorship import rules_for  # noqa: E402

# US and dual-market builds: the dormant US market is switched on for this module.
pytestmark = pytest.mark.usefixtures("us_enabled")


def _draft(status="authorized", later="unknown"):
    return {
        "contact": {"full_name": "Example Person", "city": "Dublin", "country": "Ireland", "refs": ["P001"]},
        "authorization": {"work_country": "United States", "status": "",
                          "conditions": "authorized to work without employer sponsorship", "refs": ["P002"]},
        "targets": {"roles": ["Customer Support Specialist"], "cities": ["Dublin"]},
        "target_markets": ["us"],
        "work_authorization_by_market": {"us": {"status": status, "citizenship": "noncitizen",
                                                "needs_sponsorship_later": later}},
    }


def test_us_build_keeps_work_country_wording_and_excludes_irish_home_city():
    draft = _draft()
    registry = build_registry(draft, "rev-1", {}, [])
    claim = next(item for item in registry["claims"] if item["id"] == "WORKAUTH-001")
    assert claim["value"] == "Work country: United States, authorized to work without employer sponsorship"
    assert claim["source_refs"]
    profile = build_profile(draft, registry, load_pack("us"), "rev-1")
    assert profile["candidate"]["location"] == "Dublin, Ireland"
    assert profile["location_preferences"]["preferred"] == []
    assert profile["location_preferences"]["arrangements"] == []
    assert profile["candidate"]["work_authorization"] == claim["value"]
    assert profile["candidate"]["sponsorship_need"] == "Currently authorized; future employer sponsorship is not confirmed"
    assert profile["work_authorization_by_market"]["us"]["needs_sponsorship_later"] == "unknown"
    assert draft_authorization_mode(draft, "us") == "current"


def test_selected_market_keeps_explicit_matching_cities():
    assert preferred_locations({"cities": ["Dublin", "Boston, MA"]}, ["us"]) == ["Boston, MA"]
    assert preferred_locations({"cities": ["Dublin", "Boston, MA"]}, ["ie", "us"]) == ["Dublin", "Boston, MA"]
    assert preferred_locations({}, ["ie"]) == []
    assert preferred_locations({}, ["us"]) == []


def test_residence_does_not_become_a_preference_in_ireland_or_both_markets():
    draft = _draft()
    draft["targets"]["cities"] = []
    for markets in (["ie"], ["ie", "us"]):
        draft["target_markets"] = markets
        registry = build_registry(draft, "rev-1", {}, [])
        profile = build_profile(draft, registry, load_pack(markets[0]), "rev-1")
        assert profile["candidate"]["location"] == "Dublin, Ireland"
        assert profile["location_preferences"]["preferred"] == []
        assert profile["location_preferences"]["arrangements"] == []
        assert profile["location_preferences"]["rule"].endswith(", ".join(markets) + ".")


def test_dual_market_build_writes_both_search_regions_and_guide():
    draft = _draft(later="no")
    draft["target_markets"] = ["ie", "us"]
    draft["country_pack"] = "ie"
    draft["work_authorization_by_market"]["ie"] = {
        "status": "authorized", "citizenship": "citizen", "needs_sponsorship_later": "no"}
    generated = file_set(draft, [], ledger([], draft), load_pack("ie"), [], "2026-09-26")
    portals = yaml.safe_load(generated["data/config/portals.yml"])
    regions = yaml.safe_load(generated["data/config/regions.yml"])
    guide = generated["data/config/guides/job-discovery.md"]
    queries = portals["search_queries"]["role_1"]
    assert portals["meta"]["target_markets"] == ["ie", "us"]
    assert any("Ireland" in query for query in queries)
    assert any("United States" in query for query in queries)
    assert set(regions["regions"]) == {"ie", "us"}
    assert regions["regions"]["ie"]["timezone"] == load_pack("ie").timezone
    assert regions["regions"]["us"]["timezone"] == load_pack("us").timezone
    assert "Ireland or United States" in guide
    assert "Ireland: Locations in the Republic of Ireland" in guide
    assert "United States: United States locations" in guide
    assert "candidate currently is" not in guide
    assert "data/config/sponsorship-us.yml" in generated
    assert "Ireland, United States" in generated["data/context/PROFILE.md"]
    assert "Ireland: one A4 page, United States: one US Letter page" in generated["data/context/PROFILE.md"]
    assert "sponsorship-us.yml" in generated["AGENTS.md"]


def test_a_negative_citizenship_phrase_never_implies_permanent_work_rights():
    assert authorization_mode({"status": "not a US citizen"}) == "later"
    assert authorization_mode({"status": "noncitizen"}) == "later"
    assert authorization_mode({"status": "US citizen"}) == "none"


def test_future_sponsorship_requires_a_separate_answer_before_screening(tmp_path):
    root = tmp_path / "profile"
    config = root / "data/config/profile.yml"
    config.parent.mkdir(parents=True)
    unknown = _draft()["work_authorization_by_market"]
    config.write_text(yaml.safe_dump({"country_pack": "us", "target_markets": ["us"],
                                      "work_authorization_by_market": unknown}), encoding="utf-8")
    with pytest.raises(ValueError, match="needed later"):
        require_known_authorization(root, "us")
    assert rules_for(root, "us")["exclude_no_sponsorship"]

    known = _draft(later="no")["work_authorization_by_market"]
    config.write_text(yaml.safe_dump({"country_pack": "us", "target_markets": ["us"],
                                      "work_authorization_by_market": known}), encoding="utf-8")
    require_known_authorization(root, "us")
    assert rules_for(root, "us")["exclude_no_sponsorship"] == []
    assert draft_authorization_mode(_draft(later="yes"), "us") == "later"
    future_yes = _draft(later="yes")["work_authorization_by_market"]
    config.write_text(yaml.safe_dump({"country_pack": "us", "target_markets": ["us"],
                                      "work_authorization_by_market": future_yes}), encoding="utf-8")
    require_known_authorization(root, "us")
    assert rules_for(root, "us")["exclude_no_sponsorship"]
