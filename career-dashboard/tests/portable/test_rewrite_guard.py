"""Reworded project bullets (services/rewrite_guard.py): the same facts in the posting's words, or the
registered wording stays."""

from __future__ import annotations

import pytest

from backend.services.rewrite_guard import check

ORIGINAL = "Built 12 Power BI dashboards used by 40 store managers across Leinster"
SKILLS = ["SQL", "Power BI", "Python", "Excel", "stakeholder reporting"]


def test_a_rewording_that_keeps_every_fact_passes():
    assert check(ORIGINAL, "Built 12 Power BI dashboards for stakeholder reporting, used by 40 store managers across Leinster",
                 skills=SKILLS) == []
    assert check(ORIGINAL, "Designed 12 Power BI dashboards that 40 store managers across Leinster used weekly",
                 skills=SKILLS) == []
    # A tool may come from the evidence the line cites.
    assert check(ORIGINAL, "Built 12 Power BI dashboards on SQL views, used by 40 store managers across Leinster",
                 sources=["SQL, Power BI, Python"]) == []


@pytest.mark.parametrize("rewrite, problem", [
    ("Built 15 Power BI dashboards used by 40 store managers across Leinster", "numbers changed: added 15; dropped 12"),
    ("Built Power BI dashboards used by 40 store managers across Leinster", "dropped 12"),
    ("Built 12 Tableau dashboards used by 40 store managers across Leinster", "names or tools the evidence does not hold: tableau"),
    ("Built 12 Power BI dashboards used by 40 store managers at Tesco Ireland", "tesco"),
    ("Built 12 Power BI dashboards used by 40 store managers across Leinster in March", "a month"),
    ("Spearheaded 12 Power BI dashboards used by 40 store managers across Leinster", "filler: spearheaded"),
    ("I built 12 Power BI dashboards used by 40 store managers across Leinster", "does not start with I"),
    (ORIGINAL, "the same as the registered one"),
    ("Built 12 Power BI dashboards used by 40 store managers across Leinster, cutting churn through predictive "
     "segmentation and executive storytelling workshops", "new words"),
    ("Delivered 12 dashboards and 40 forecasts", "keeps too little"),
])
def test_a_rewording_that_changes_a_fact_is_refused(rewrite, problem):
    problems = " | ".join(check(ORIGINAL, rewrite, skills=SKILLS))
    assert problem in problems


def test_posting_terms_and_never_claim_skills_need_the_evidence():
    lowercase_tool = "Built 12 Power BI dashboards on dbt models, used by 40 store managers across Leinster"
    assert "posting terms the evidence does not hold: dbt" in " ".join(check(ORIGINAL, lowercase_tool, posting_terms=["dbt"]))
    assert check(ORIGINAL, lowercase_tool, sources=["dbt, Airflow, PostgreSQL"], posting_terms=["dbt"]) == []
    excel = "Built 12 Power BI and Excel dashboards used by 40 store managers across Leinster"
    assert "never-claim skills: Excel" in " ".join(check(ORIGINAL, excel, skills=SKILLS, never=["Excel"]))
