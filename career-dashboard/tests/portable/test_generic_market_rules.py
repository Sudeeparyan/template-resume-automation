"""Cross-profile rules that must not inherit a reference candidate's assumptions."""

from datetime import date
from types import SimpleNamespace

from backend.countries import market_for_location, target_markets_for
from backend.job_quality import JobQualityService, ProfileRules
from backend.services.agents import mail_available
from backend.services.reapply import due_for_ghosting, settings
import pytest

# Dual-market rules: the dormant US market is switched on for this module.
pytestmark = pytest.mark.usefixtures("us_enabled")


def test_new_profile_has_no_fixed_profession_or_experience_cap():
    rules = ProfileRules({})
    assert rules.roles.search("Museum Curator")
    assert rules.roles.search("Senior Nurse Manager")
    assert rules.max_years == 0
    assert rules.block_seniority is False
    assert rules.phd_known_missing is False


def test_explicit_profile_limits_stay_profile_scoped():
    rules = ProfileRules({
        "target_roles": {"primary": ["Marine Biologist"], "max_years_required": 6},
        "scoring": {"block_seniority": True, "highest_degree": "MSc Biology"},
    })
    assert rules.roles.search("Marine Biologist")
    assert not rules.roles.search("Museum Curator")
    assert rules.max_years == 6
    assert rules.block_seniority is True
    assert rules.phd_known_missing is True


def test_dual_market_location_is_tagged_without_primary_fallback(tmp_path):
    config = tmp_path / "data/config"
    config.mkdir(parents=True)
    (config / "profile.yml").write_text("target_markets: [ie, us]\n", encoding="utf-8")
    assert target_markets_for(tmp_path) == ["ie", "us"]
    assert market_for_location(tmp_path, "Dublin, Ireland") == "ie"
    assert market_for_location(tmp_path, "Boston, MA") == "us"


def test_silence_never_sets_an_application_outcome():
    profile = {"reapply": {"auto_ghost": True, "ghost_after_days": 1}}
    assert settings(profile)["auto_ghost"] is False
    assert due_for_ghosting([{"status": "applied", "application_date": "2020-01-01"}],
                            profile, today=date(2026, 9, 25)) == []


def test_mail_is_opt_in_per_profile(tmp_path):
    assert not mail_available(tmp_path)
    assert not mail_available(tmp_path, {"connected": True, "connector_id": "gmail"})
    assert mail_available(tmp_path, {"connected": False, "connector_id": "gmail", "expected_email": "sample@example.org"})


def test_balanced_mix_uses_each_jobs_sponsor_market(tmp_path):
    config = tmp_path / "data/config"
    config.mkdir(parents=True)
    (config / "profile.yml").write_text("target_markets: [ie, us]\n", encoding="utf-8")
    service = JobQualityService(SimpleNamespace(w=SimpleNamespace(root=tmp_path)))
    base = {"size_category": "large", "legitimacy_state": "verified",
            "relevance": {"eligible": True}, "sponsor_tier": "C"}
    result = service.balanced_five([
        {**base, "market": "us", "url": "https://example.org/us"},
        {**base, "market": "ie", "url": "https://example.org/ie"},
    ], total=2)
    assert [job["market"] for job in result["jobs"]] == ["ie"]
