"""The dated Irish permit rules (countries/ie/permit-rules.yml, v2) and their review dates.

The figures are DETE's published thresholds from 1 March 2026. Version 1 keys stay as
they were; v2 adds the conditions each figure needs. Every reader agrees on when the
rules are due for review: the assessment, workspace validation, the doctor and the
Dashboard notice.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest

APP = Path(__file__).resolve().parents[2]
REPO = APP.parent
sys.path.insert(0, str(APP))

from backend.services import permit_assessment  # noqa: E402
from backend.services.permit_assessment import (
    assess,
    freshness,
    load_rules,
)  # noqa: E402

OFFICIAL = {"enterprise.gov.ie", "www.irishimmigration.ie"}
STAMP_1G = {
    "work_authorization_by_market": {
        "ie": {
            "status": "authorized",
            "citizenship": "noncitizen",
            "needs_sponsorship_later": "yes",
        }
    }
}


def urls(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "url" or (isinstance(item, str) and item.startswith("http")):
                yield item
            else:
                yield from urls(item)
    elif isinstance(value, list):
        for item in value:
            yield from urls(item)


def test_v1_keys_and_published_thresholds():
    rules = load_rules()
    gep, csep = rules["routes"]["gep"], rules["routes"]["csep"]
    for route in (gep, csep):
        assert {
            "title",
            "annual",
            "graduate_annual",
            "graduate_condition",
            "url",
        } <= set(route)
    assert (
        gep["annual"],
        gep["graduate_annual"],
        csep["annual"],
        csep["graduate_annual"],
    ) == (36605, 34009, 40904, 36848)
    assert {t["id"]: t["annual"] for t in gep["thresholds"]} == {
        "standard": 36605,
        "irish_graduate": 34009,
    }
    assert {t["id"]: t["annual"] for t in csep["thresholds"]} == {
        "critical_occupation": 40904,
        "critical_occupation_graduate": 36848,
        "other_occupation": 68911,
    }
    assert gep["thresholds"][1]["requires"] == {
        "occupation_not": "ineligible",
        "degree_within_months": 12,
        "irish_institution": True,
        "relevant_degree": True,
        "min_nfq_level": 8,
    }
    assert csep["thresholds"][1]["requires"] == {
        "occupation_list": "critical",
        "degree_within_months": 12,
        "relevant_degree": True,
        "min_nfq_level": 8,
    }
    # The high-pay route also covers listed occupations without the lower route's degree.
    assert csep["thresholds"][2]["requires"] == {"occupation_not": "ineligible"}
    assert {"salary_at_least": 68911} in gep["lmnt"]["exemptions"] and gep[
        "application_lead_weeks"
    ] == 12
    assert gep["lmnt"]["minimum_continuous_days"] == 28
    assert gep["base_weekly_hours"] == csep["base_weekly_hours"] == 39
    assert csep["minimum_contract_months"] == 24
    assert rules["other_routes"]["ict"]["annual"] == 49523
    assert rules["other_routes"]["ict"]["trainee_annual"] == 36605
    assert any("further 12 months" in fact for fact in rules["stamp_1g"]["facts"])


def test_every_source_is_an_official_https_page():
    found = list(urls(load_rules()))
    assert len(found) >= 8
    for url in found:
        parts = urlsplit(url)
        assert parts.scheme == "https" and parts.hostname in OFFICIAL, url


def test_review_dates_give_current_due_soon_and_stale():
    rules = load_rules()
    review = date.fromisoformat(rules["review_after"])
    assert freshness(on=review - timedelta(days=90))["state"] == "current"
    soon = freshness(on=review - timedelta(days=10))
    assert (
        soon["state"] == "due_soon"
        and soon["days_left"] == 10
        and rules["review_after"] in soon["message"]
    )
    assert freshness(on=review + timedelta(days=1))["state"] == "stale"
    before = freshness(
        on=date.fromisoformat(rules["effective_from"]) - timedelta(days=1)
    )
    assert before["state"] == "stale" and "take effect" in before["message"]
    assert freshness(on=review - timedelta(days=30))["state"] == "due_soon"
    assert freshness(on=review - timedelta(days=31))["state"] == "current"
    assert freshness(on=review)["state"] == "due_soon"


def test_2026_figures_are_not_assumed_to_apply_to_the_january_2027_review():
    # DETE's roadmap (page 38) schedules January 2027 figures, still TBD.
    assert freshness(on=date(2027, 1, 1))["state"] == "stale"


def test_stale_rules_never_pass_a_salary_threshold():
    job = {"description": "Data Analyst in Dublin."}
    pay = {
        "kind": "advertised",
        "currency": "EUR",
        "annual_min": 45000,
        "annual_max": 50000,
    }
    review = date.fromisoformat(load_rules()["review_after"])
    current = assess(
        job, pay, STAMP_1G, {"state": "silent"}, on=review - timedelta(days=60)
    )
    stale = assess(
        job, pay, STAMP_1G, {"state": "silent"}, on=review + timedelta(days=1)
    )
    state = lambda result, key: next(
        c["state"] for c in result["checks"] if c["id"] == key
    )  # noqa: E731
    assert state(current, "rules") == "pass" and state(current, "gep_salary") == "pass"
    assert (
        state(stale, "rules") == "unknown" and state(stale, "gep_salary") == "unknown"
    )


def test_the_doctor_reads_the_same_dates_without_the_app():
    spec = importlib.util.spec_from_file_location(
        "career_doctor", REPO / "scripts/career_doctor.py"
    )
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    rules = load_rules()
    review = date.fromisoformat(rules["review_after"])
    days_to_check = [review - timedelta(days=days) for days in (90, 31, 30, 10, 0, -1)]
    days_to_check += [date.fromisoformat(rules["effective_from"]) - timedelta(days=1)]
    for on in days_to_check:
        expected = freshness(on=on)["state"]
        assert doctor.permit_rules_status(on)["state"] == expected
    assert doctor.permit_rules_status(review)["verified_at"] == rules["verified_at"]


@pytest.mark.parametrize(
    "changed",
    [
        {"review_after": "not a date"},
        {"effective_from": ""},
        {"verified_at": "2030-01-01"},
        {"version": ""},
        {"review_after": "2026-01-01"},
    ],
)
def test_invalid_rule_dates_cannot_be_reported_as_current(
    changed, tmp_path, monkeypatch
):
    import yaml

    rules = {**load_rules(), **changed}
    with pytest.raises((ValueError, KeyError)):
        freshness(on=date(2026, 10, 3), rules=rules)
    spec = importlib.util.spec_from_file_location(
        "career_doctor_dates", REPO / "scripts/career_doctor.py"
    )
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    path = tmp_path / "permit-rules.yml"
    path.write_text(yaml.safe_dump(rules), encoding="utf-8")
    monkeypatch.setattr(doctor, "PERMIT_RULES", path)
    assert doctor.permit_rules_status(date(2026, 10, 3))["state"] == "unreadable"


def test_workspace_validation_warns_and_ci_fails_on_stale_rules(monkeypatch):
    sys.path.insert(0, str(APP / "backend/scripts"))
    import validate_workspace

    def run(state, ci=False):
        monkeypatch.setattr(
            permit_assessment,
            "freshness",
            lambda **_: {"state": state, "message": f"rules {state}"},
        )
        (
            monkeypatch.setenv("CI", "true")
            if ci
            else monkeypatch.delenv("CI", raising=False)
        )
        validate_workspace.ERRORS.clear()
        validate_workspace.WARNINGS.clear()
        validate_workspace.validate_permit_rules()
        return list(validate_workspace.ERRORS), list(validate_workspace.WARNINGS)

    assert run("current") == ([], [])
    assert run("due_soon")[1] and not run("due_soon")[0]
    assert run("stale")[1] and not run("stale")[0]
    assert run("stale", ci=True)[0]


def test_the_dashboard_shows_a_notice_only_when_review_is_due(tmp_path, monkeypatch):
    import yaml
    from backend.services.workspace_v2 import CareerServices
    from career import Workspace

    root = tmp_path / "profile"
    (root / "data/config").mkdir(parents=True)
    (root / "data/context").mkdir(parents=True)
    (root / "data/config/profile.yml").write_text(
        yaml.safe_dump(
            {
                "country_pack": "ie",
                "target_markets": ["ie"],
                **STAMP_1G,
                "candidate": {
                    "full_name": "Sample Person",
                    "preferred_name": "Sample",
                    "timezone": "Europe/Dublin",
                },
                "target_roles": {"primary": ["Data Analyst"]},
            }
        ),
        encoding="utf-8",
    )
    (root / "data/context/evidence.yml").write_text(
        yaml.safe_dump({"claims": [], "projects": []}), encoding="utf-8"
    )
    services = CareerServices(Workspace(root))
    monkeypatch.setattr(
        permit_assessment, "freshness", lambda **_: {"state": "current", "message": ""}
    )
    assert services.notices() == []
    monkeypatch.setattr(
        permit_assessment,
        "freshness",
        lambda **_: {"state": "due_soon", "message": "Re-verify the thresholds."},
    )
    assert services.notices() == [
        {"id": "permit_rules", "level": "warning", "text": "Re-verify the thresholds."}
    ]
    for error in (
        OSError("missing rules"),
        ValueError("invalid dates"),
        yaml.YAMLError("invalid yaml"),
    ):

        def unreadable(**_):
            raise error

        monkeypatch.setattr(permit_assessment, "freshness", unreadable)
        warning = services.notices()
        assert warning[0]["id"] == "permit_rules" and warning[0]["level"] == "warning"
        assert "cannot be read" in warning[0]["text"]


@pytest.mark.parametrize("phrase", ["you are eligible", "you qualify", "guarantee"])
def test_rules_text_never_reads_as_advice(phrase):
    text = (
        (APP / "backend/countries/ie/permit-rules.yml")
        .read_text(encoding="utf-8")
        .casefold()
    )
    assert phrase not in text
