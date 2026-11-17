"""The release check validates published market config, independently of dev overrides."""

import pytest
import yaml

import validate_workspace


@pytest.fixture(autouse=True)
def empty_findings():
    validate_workspace.ERRORS.clear()
    validate_workspace.WARNINGS.clear()
    yield
    validate_workspace.ERRORS.clear()
    validate_workspace.WARNINGS.clear()


def test_a_dev_market_override_does_not_change_published_skills(monkeypatch):
    monkeypatch.setenv("CAREER_MARKETS", "ie,us")
    validate_workspace.validate_markets()
    assert validate_workspace.ERRORS == []


@pytest.mark.parametrize("enabled", [[], "ie", ["unknown"], [None], [{"ie": True}]])
def test_malformed_committed_markets_are_not_silently_accepted(tmp_path, monkeypatch, enabled):
    import backend.countries

    config = tmp_path / "markets.yml"
    config.write_text(yaml.safe_dump({"enabled": enabled}), encoding="utf-8")
    monkeypatch.setattr(backend.countries, "MARKETS_FILE", config)
    validate_workspace.validate_markets()
    assert len(validate_workspace.ERRORS) == 1
    assert "non-empty list of known markets" in validate_workspace.ERRORS[0]
