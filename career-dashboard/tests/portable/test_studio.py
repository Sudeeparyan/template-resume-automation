"""LangGraph Studio's graphs (backend/graphs/studio.py) run the real graphs on the synthetic demo profile only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from backend import paths
from backend.graphs import studio
from test_graphs import ABOUT, ABOUT_PAGE, NEWS, NEWS_PAGE, runner_for
from test_job_sources import fetcher

APP = Path(__file__).resolve().parents[2]


@pytest.fixture
def scratch_studio(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "STUDIO", tmp_path / "studio")
    monkeypatch.setattr(studio, "CONTEXT_OPTIONS", {"fetcher": fetcher({ABOUT: ABOUT_PAGE, NEWS: NEWS_PAGE}),
                                                    "url_check": lambda url: True})
    monkeypatch.setattr(paths, "MARKET_DB", paths.MARKET_DB)  # restored after the test
    monkeypatch.setattr(paths, "HTTP_CACHE_DB", paths.HTTP_CACHE_DB)
    calls = []
    monkeypatch.setattr(studio, "make_runner", lambda services: runner_for(services, calls))
    return tmp_path / "studio", calls


def test_studio_runs_the_real_dossier_graph_on_a_new_synthetic_demo_profile(scratch_studio):
    base, calls = scratch_studio
    final = studio.dossier().invoke({"employer": "Acme Analytics", "depth": "quick"})
    assert {claim["text"] for claim in final["dossier"]["claims"]} == {"Acme builds reporting software.",
                                                                       "Acme employs 240 people in Dublin."}
    assert len(calls) == 2 and all(call["web"] for call in calls)
    root = studio.demo_root()
    assert root.is_relative_to(base / "profiles")
    assert yaml.safe_load((root / "data/config/profile.yml").read_text(encoding="utf-8"))["synthetic_demo"] is True
    assert paths.MARKET_DB == base / "market.db" and paths.MARKET_DB.is_file()  # never the shared market store


def test_studio_refuses_a_profile_that_is_not_the_marked_demo(scratch_studio, monkeypatch):
    base, _ = scratch_studio
    monkeypatch.setattr(paths, "PROFILES", base.parent / "users-profiles")  # never the real folder in a test
    root = studio.demo_root()
    profile = yaml.safe_load((root / "data/config/profile.yml").read_text(encoding="utf-8"))
    profile["synthetic_demo"] = False
    (root / "data/config/profile.yml").write_text(yaml.safe_dump(profile), encoding="utf-8")
    with pytest.raises(ValueError, match="synthetic demo"):
        studio.dossier().invoke({"employer": "Acme Analytics", "depth": "quick"})
    with pytest.raises(ValueError, match="never opens users' profiles"):
        studio.demo_root(paths.PROFILES / "someone")


def test_langgraph_json_lists_the_studio_graphs():
    config = json.loads((APP / "langgraph.json").read_text(encoding="utf-8"))
    for name, target in config["graphs"].items():
        module, attribute = target.split(":")
        assert module == "./backend/graphs/studio.py" and callable(getattr(studio, attribute)), name
    assert "env" not in config  # Studio never loads the app's .env
