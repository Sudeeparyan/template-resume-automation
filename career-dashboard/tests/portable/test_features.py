from types import SimpleNamespace

import pytest

from backend.features import enabled, flags


def test_finished_parts_default_on_and_unfinished_ones_off():
    defaults = flags(environ={})
    assert {name for name, on in defaults.items() if on} == {"market_store", "tracker", "docx_export", "evidence_rewrites",
                                                             "graph_research", "graph_tracker_refresh"}


def test_the_market_store_switch_stops_recording(monkeypatch):
    from backend.services import job_sources

    monkeypatch.setenv("CAREER_FEATURES", "-market_store")
    posting = job_sources.make_posting("Acme", "Analyst", "https://example.org/jobs/1", "Dublin", "Text",
                                       source="directory", source_kind="employer_feed")
    assert job_sources.record_in_market([posting]) is None


def test_rollout_is_opt_in_and_environment_overrides_only_this_process():
    preferences = {
        "tracker": True,
        "graph_pipeline": "true",  # not a boolean: ignored
        "graph_tracker_refresh": False,
        "typo": True,
    }
    service = SimpleNamespace(pref=lambda key, default: preferences)
    resolved = flags(service, environ={"CAREER_FEATURES": "-tracker,+market_store"})
    assert resolved["market_store"] and not resolved["graph_pipeline"]
    assert not resolved["tracker"] and not resolved["graph_tracker_refresh"]
    assert "graph_hunt" not in resolved  # no switch for work that does not exist
    assert preferences["tracker"] is True
    assert enabled("tracker", service, environ={})


def test_unknown_flags_do_not_silently_enable_behaviour():
    with pytest.raises(ValueError, match="Unknown CAREER_FEATURES"):
        flags(environ={"CAREER_FEATURES": "trackre"})
    with pytest.raises(ValueError, match="Unknown career feature"):
        enabled("unknown", environ={})


@pytest.mark.parametrize(
    "token", ["++graph_pipeline", "--tracker", "+-tracker", "-+tracker", "+", "-"]
)
def test_malformed_prefixes_do_not_enable_any_feature(token):
    with pytest.raises(ValueError, match="Unknown CAREER_FEATURES"):
        flags(environ={"CAREER_FEATURES": token})
