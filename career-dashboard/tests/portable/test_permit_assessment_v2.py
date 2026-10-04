"""Evidence boundaries, calendar windows and remuneration are separate permit facts."""
from copy import deepcopy
from datetime import date
import pytest
from backend.permits.assessment import assess, personal_floor
from backend.permits.timeline import graduate_window, timeline
from backend.services.salary import floor_for

ON = date(2026, 10, 3)
GRADUATE = {
    "work_authorization_by_market": {"ie": {"status": "authorized", "permission_type": "stamp_1g",
        "citizenship": "noncitizen", "needs_sponsorship_later": "yes", "valid_until": "2027-10-03", "valid_until_confirmed": True}},
    "education_for_permits": {"award_date": "2026-08-03", "award_date_confirmed": True,
        "nfq_level": 9, "irish_institution": True, "relevant_degree": True},
}


def result(profile=None, job=None, pay=None, occupation="critical", on=ON):
    return assess(job or {"description": "A 24-month contract is offered."},
        pay or {"kind": "advertised", "currency": "EUR", "annual_min": 37000, "annual_max": 37000},
        profile or GRADUATE, {"state": "silent"}, on=on,
        occupation={"classification": occupation, "matches": [{"soc4": "2136", "quote": "Programmers and software development professionals"}]}, history={})


def route(output, name):
    return next(row for row in output["routes"] if row["id"] == name)


def checks(output):
    return {row["id"]: row for row in output["checks"]}


def test_graduate_variants_use_confirmed_facts_and_each_routes_conditions():
    output = result()
    assert route(output, "gep")["applied_annual"] == 34009
    assert route(output, "csep")["applied_annual"] == 36848
    assert route(output, "csep")["salary_state"] == "pass"
    assert output["state"] == "needs_confirmation"
    assert checks(output)["eea_ratio"]["state"] == "unknown"
    assert checks(output)["contract"]["state"] == "pass"
    assert personal_floor(GRADUATE, on=ON) == 34009


@pytest.mark.parametrize("field,value", [("award_date_confirmed", False), ("award_date", "DEC2026"),
    ("nfq_level", True), ("nfq_level", 7), ("irish_institution", None), ("relevant_degree", None),
    ("award_date", "2026-12-03"), ("award_date", "2025-10-03")])
def test_missing_ambiguous_future_or_expired_person_facts_do_not_lower_default(field, value):
    profile = deepcopy(GRADUATE)
    profile["education_for_permits"][field] = value
    assert personal_floor(profile, on=ON) == 36605
    assert route(result(profile=profile), "gep")["applied_annual"] == 36605


def test_foreign_graduate_does_not_get_irish_gep_rate_but_csep_is_separate():
    profile = deepcopy(GRADUATE)
    profile["education_for_permits"]["irish_institution"] = False
    output = result(profile=profile)
    assert route(output, "gep")["applied_annual"] == 36605
    assert route(output, "csep")["applied_annual"] == 36848


def test_higher_csep_route_never_uses_lower_critical_figure_without_relevant_degree():
    profile = deepcopy(GRADUATE)
    profile["education_for_permits"]["relevant_degree"] = False
    output = result(profile=profile, occupation="neither")
    assert route(output, "csep")["applied_annual"] == 68911
    assert route(output, "csep")["salary_state"] == "unknown"
    at_boundary = result(profile=profile, occupation="neither", pay={"kind": "advertised", "currency": "EUR", "annual_min": 68911})
    assert route(at_boundary, "csep")["salary_state"] == "unknown"
    assert route(result(profile=profile, occupation="neither", pay={"kind": "advertised", "currency": "EUR", "annual_min": 68912}), "csep")["salary_state"] == "pass"


def test_unknown_occupation_cannot_apply_graduate_variants_or_approve_a_permit():
    output = result(occupation="unknown")
    assert route(output, "gep")["selected_variant"] is None
    assert route(output, "gep")["applied_annual"] == 36605
    assert checks(output)["occupation"]["state"] == "unknown"
    assert output["state"] == "needs_confirmation"


def test_estimates_stale_rules_and_total_compensation_cannot_pass_thresholds():
    for pay in ({"kind": "researched", "currency": "EUR", "annual_min": 90000},
                {"kind": "advertised", "currency": "EUR", "annual_min": 90000, "components": "total"}):
        assert all(r["salary_state"] == "unknown" for r in result(pay=pay)["routes"])
    stale = result(on=date(2027, 1, 1))
    assert all(r["salary_state"] == "unknown" and r["applied_annual"] is None for r in stale["routes"])
    assert checks(stale)["lmnt"]["state"] == "unknown"


def test_longer_week_cannot_pass_the_39_hour_threshold_without_adjustment():
    pay = {"kind": "advertised", "currency": "EUR", "annual_min": 34009, "annual_max": 34009, "hours_per_week": 40}
    assert route(result(pay=pay), "gep")["salary_state"] == "unknown"


def test_short_contract_is_a_csep_obstacle_and_eures_is_only_a_channel_signal():
    output = result(job={"description": "A 12-month fixed-term contract.", "on_eures": True}, occupation="neither")
    current = checks(output)
    assert current["contract"]["state"] == "fail"
    assert current["eures"]["state"] == "pass" and current["lmnt"]["state"] == "unknown"
    assert output["state"] == "needs_confirmation"  # shorter offer may use another route


def test_calendar_window_and_timeline_do_not_guess_dates_or_give_advice():
    profile = deepcopy(GRADUATE)
    profile["education_for_permits"]["award_date"] = "2024-02-29"
    assert graduate_window(profile, on=date(2025, 2, 27))["state"] == "active"
    assert graduate_window(profile, on=date(2025, 2, 28))["state"] == "ended"
    output = timeline(GRADUATE, {"start_date": "2027-01-01"}, on=ON)
    events = {row["id"]: row for row in output["events"]}
    assert events["stamp_1g_expiry"]["days_left"] == 365
    assert events["gep_lead_time"]["date"] == "2026-10-09"
    profile["work_authorization_by_market"]["ie"]["valid_until_confirmed"] = False
    assert timeline(profile, on=ON)["events"][0]["date"] is None


def test_default_floor_refreshes_but_a_persons_explicit_preference_stays():
    profile = deepcopy(GRADUATE)
    profile["job_search"] = {"salary_floor_eur": 34009, "salary_floor_source": "permit_rules"}
    assert floor_for(profile, on=ON) == 34009
    assert floor_for(profile, on=date(2027, 8, 3)) == 36605
    profile["job_search"]["salary_floor_source"] = "person"
    assert floor_for(profile, on=date(2027, 8, 3)) == 34009
