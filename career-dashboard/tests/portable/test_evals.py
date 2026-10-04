"""The honesty checks catch every planted fabrication in evals/cases.yml and accept every honest
change (evals/run.py, without AI); a recorded AI run replays offline when one is present."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

APP = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("career_evals", APP / "evals" / "run.py")
evals = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evals)
CASES = yaml.safe_load((APP / "evals" / "cases.yml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("suite, key", [(evals.rewrite_suite, "rewrite_guard"), (evals.letter_suite, "letter_check"),
                                        (evals.claim_suite, "claim_check")])
def test_every_planted_fabrication_is_caught_and_every_honest_change_accepted(suite, key):
    report = suite(CASES[key])
    assert report["ok"], report
    assert report["slipped"] == [] and report["refused_honest"] == []


def test_occupation_lists_are_never_confidently_wrong():
    report = evals.occupation_suite(CASES["occupation_check"])
    assert report["ok"], report
    assert report["confidently_wrong"] == [] and report["top1_accuracy"] == f"{len(CASES['occupation_check']['cases'])}/{len(CASES['occupation_check']['cases'])}"


def test_a_recorded_ai_run_replays_offline():
    if not (evals.CASSETTES / "cover_letter_writer.json").is_file():
        pytest.skip("no recorded AI run yet (python evals/run.py --live kimi_cli)")
    report = evals.writer_suite(provider=None, replay=True)
    assert report["ok"], report
