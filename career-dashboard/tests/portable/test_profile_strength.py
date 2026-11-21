"""The profile strength meter (services/profile_strength.py) counts only what resumes need."""

from __future__ import annotations

from app_fixture import profile_app
from backend.services.profile_strength import score
from test_docx_export import CANDIDATE, evidence

FULL_CONFIG = {
    "candidate": CANDIDATE,
    "work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "noncitizen"}},
    "target_roles": {"primary": ["Data Analyst"]},
}


def test_a_complete_profile_scores_full_marks_without_a_certificate():
    result = score(evidence(), FULL_CONFIG, pending=0)
    assert result["score"] == 100
    assert all(check["done"] for check in result["checks"])
    assert result["next"] == []
    # A certificate is only ever a suggestion.
    assert [tip["id"] for tip in result["tips"]] == ["certification"]


def test_missing_pieces_are_named_most_important_first():
    registry = evidence()
    registry["claims"] = [c for c in registry["claims"] if c["category"] not in ("employment", "education")]
    registry["projects"] = registry["projects"][:1]
    config = {**FULL_CONFIG, "work_authorization_by_market": {"ie": {"status": "unknown"}}}
    result = score(registry, config, pending=2)
    assert result["score"] == 100 - 20 - 15 - 15 - 10
    assert [c["id"] for c in result["next"]] == ["experience", "work_authorization", "education"]
    assert not next(c for c in result["checks"] if c["id"] == "reviewed")["done"]


def test_an_empty_profile_scores_zero_and_never_crashes():
    result = score({}, {}, pending=0)
    assert result["score"] == 10  # only "nothing waiting for review" holds
    assert len(result["next"]) == 3


def test_the_profile_api_returns_the_meter(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        body = client.get(base + "/v2/profile").json()
        meter = body["strength"]
        assert 0 <= meter["score"] <= 100
        assert {c["id"] for c in meter["checks"]} == {
            "contact", "work_authorization", "target_roles", "education", "experience", "skills", "reviewed"}
        # The fixture says the person is an Irish citizen: permission to work is known.
        assert next(c for c in meter["checks"] if c["id"] == "work_authorization")["done"]
