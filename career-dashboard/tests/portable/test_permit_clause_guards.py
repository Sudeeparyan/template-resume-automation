"""Permit guards cover their own phrases, without hiding independent restrictions."""

import pytest

from backend.countries import load_pack
from backend.services.sponsorship import load_rules, screen


def rules(market):
    return load_rules(str(load_pack(market).template("sponsorship.yml")))


@pytest.mark.parametrize("market", ["ie", "us"])
@pytest.mark.parametrize("posting", [
    "No security clearance is required and we cannot sponsor work permits.",
    "No security clearance is required, and we do not offer visa sponsorship.",
    "No security clearance is required, but visa sponsorship is not available.",
    "You report to the executive sponsor and we cannot provide visa sponsorship.",
    "No sponsorship or citizenship sentence was visible, but we cannot sponsor work permits.",
])
def test_independent_refusal_in_a_guarded_sentence_is_excluded(market, posting):
    result = screen(posting, rules(market))
    assert result.verdict == "EXCLUDED"
    assert result.reason == "no_sponsorship"
    assert result.sentence == posting


@pytest.mark.parametrize("posting", [
    "Garda vetting is required and we cannot sponsor employment permits.",
    "Garda vetting and security clearance are required.",
    "No security clearance is required and Stamp 4 is mandatory.",
])
def test_irish_routine_checks_do_not_mask_permit_or_stamp_restrictions(posting):
    result = screen(posting, rules("ie"))
    assert result.verdict == "EXCLUDED"
    assert result.sentence == posting


@pytest.mark.parametrize("market", ["ie", "us"])
@pytest.mark.parametrize("posting", [
    "No security clearance is required for this role.",
    "This role does not require sponsorship or a clearance.",
    "You will report to the executive sponsor for the programme.",
    "No sponsorship refusal, citizenship requirement or security clearance wording was found on the page.",
    "The pages reviewed do not show the precise sentence about citizenship or security clearance.",
    "The application asks about present or future sponsorship but does not state whether it is available.",
    "Will you now or in the future require sponsorship?",
])
def test_negations_questions_and_absence_notes_still_keep_the_posting(market, posting):
    assert screen(posting, rules(market)).verdict == "KEEP"


def test_routine_irish_vetting_and_independent_sponsorship_offer_are_kept():
    result = screen("Garda vetting is required and visa sponsorship is available.", rules("ie"))
    assert result.verdict == "KEEP"
    assert result.reason == "explicit_sponsorship"


@pytest.mark.parametrize("location", [
    "Dublin, Ohio", "Dublin, OH", "Dublin, oh", "Dublin (Ohio)", "Dublin (OH)",
    "Dublin (US)", "Waterford, Wisconsin", "Galway, New York", "Cork, Texas",
    "Belfast, Northern Ireland", "Derry, Northern Ireland",
])
def test_us_namesakes_and_northern_ireland_do_not_establish_an_irish_market(location):
    assert not load_pack("ie").location_ok(location)


@pytest.mark.parametrize("location", [
    "Dublin", "Dublin, Ireland", "Galway, County Galway", "Cork, Republic of Ireland",
    "Remote - IE", "Ireland-remote", "Dublin or London", "Dublin, Ireland; Boston, MA",
])
def test_irish_and_explicitly_multicountry_locations_still_match(location):
    assert load_pack("ie").location_ok(location)


def test_us_namesakes_remain_eligible_for_the_us_market():
    for location in ("Dublin, Ohio", "Dublin, OH", "Waterford, Wisconsin", "Galway, New York"):
        assert load_pack("us").location_ok(location), location
