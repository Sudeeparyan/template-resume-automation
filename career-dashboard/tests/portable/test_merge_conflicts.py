"""Conflicting source facts must remain visible before a built profile is used."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.services.intake.merge import merge  # noqa: E402


def test_identity_authorization_and_employment_conflicts_keep_source_refs():
    first = {
        "contact": {"full_name": "Example Person", "email": "first@example.test", "refs": ["P001"]},
        "authorization": {"status": "Stamp 1G", "refs": ["P002"]},
        "experience": [{"employer": "Example Services", "title": "Support Specialist",
                        "start": "January 2023", "end": "Present", "refs": ["P003"]}],
    }
    second = {
        "contact": {"full_name": "Example P. Person", "email": "second@example.test", "refs": ["P004"]},
        "authorization": {"status": "Stamp 2", "refs": ["P005"]},
        "experience": [{"employer": "Example Services", "title": "Support Specialist",
                        "start": "February 2023", "end": "Present", "refs": ["P006"]}],
    }
    draft = merge([first, second])
    questions = "\n".join(draft["questions"])
    assert "full name" in questions and "Example P. Person" in questions
    assert "email" in questions and "second@example.test" in questions
    assert "status" in questions and "Stamp 2" in questions
    assert "start" in questions and "February 2023" in questions
    assert draft["contact"]["refs"] == ["P001", "P004"]
    assert draft["authorization"]["refs"] == ["P002", "P005"]
    assert draft["experience"][0]["refs"] == ["P003", "P006"]


def test_more_precise_date_does_not_create_false_conflict():
    sections = [
        {"experience": [{"employer": "Example Services", "title": "Support Specialist",
                         "start": "2023", "refs": ["P001"]}]},
        {"experience": [{"employer": "Example Services", "title": "Support Specialist",
                         "start": "February 2023", "refs": ["P002"]}]},
    ]
    draft = merge(sections)
    assert draft["experience"][0]["start"] == "February 2023"
    assert not draft["questions"]
