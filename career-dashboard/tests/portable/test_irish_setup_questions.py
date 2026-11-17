"""The setup chat's Irish permit questions: few, plainly worded, and only those that matter."""

from backend.services.intake.interview import (
    IRISH_PERMISSIONS,
    _irish_graduate_questions,
    _irish_permission_questions,
    suggested_permission,
)

DRAFT = {"education": [{"degree": "MSc", "award_date_as_supplied": "November 2025"}]}


def keys(questions):
    return [q["key"] for q in questions]


def test_documents_only_suggest_the_permission_option_shown_first():
    assert suggested_permission("Stamp 1G until DEC2027") == "stamp_1g"
    assert suggested_permission("Stamp 4") == "stamp_4"
    assert suggested_permission("Irish citizen") == "irish_or_eea_citizen"
    assert suggested_permission("non-citizen on a Stamp 2") == "stamp_2"
    assert suggested_permission("I am not an Irish citizen") is None
    [question] = _irish_permission_questions({"status": "Stamp 1G until DEC2027"}, {})
    assert question["options"][0] == {"label": "Stamp 1G", "description": "From your documents", "value": "stamp_1g"}
    assert not question["skippable"]


def test_permission_options_are_plain_words_not_codes():
    [question] = _irish_permission_questions({}, {})
    assert len(question["options"]) == len(IRISH_PERMISSIONS)
    assert all("_" not in option["label"] for option in question["options"])


def test_a_student_or_other_permission_is_asked_whether_full_time_work_is_allowed():
    assert keys(_irish_permission_questions({}, {"permission_type": "stamp_2"})) == ["permit_status"]
    assert _irish_permission_questions({}, {"permission_type": "stamp_1g", "status": "authorized"}) == []


def test_stamp_1g_asks_expiry_then_the_degree_facts_behind_graduate_thresholds():
    facts = {"permission_type": "stamp_1g", "status": "authorized", "citizenship": "noncitizen"}
    questions = _irish_graduate_questions(DRAFT, {"valid_until": "DEC2027"}, facts)
    assert keys(questions) == ["permit_expiry", "permit_award", "permit_degree", "permit_relevance", "graduate_search"]
    expiry = questions[0]
    assert "DEC2027" in expiry["text"] and expiry["options"][0]["value"] == "2027-12-31"
    assert "Only a month" in expiry["why"]  # a month-end proposal says it needs the actual day
    assert questions[1]["options"][0]["value"] == "2025-11-30"


def test_people_who_never_need_a_permit_are_not_asked_degree_facts():
    citizen = {"permission_type": "irish_or_eea_citizen", "citizenship": "citizen", "needs_sponsorship_later": "no"}
    assert keys(_irish_graduate_questions(DRAFT, {}, citizen)) == ["graduate_search"]
    stamp_4 = {"permission_type": "stamp_4", "status": "authorized", "citizenship": "noncitizen"}
    assert keys(_irish_graduate_questions(DRAFT, {}, stamp_4)) == ["graduate_search"]


def test_confirmed_facts_are_not_asked_again():
    facts = {"permission_type": "stamp_1g", "valid_until": "2027-12-31", "valid_until_confirmed": True}
    draft = {**DRAFT, "education_for_permits": {"award_date_confirmed": True, "nfq_level": 9,
                                                "irish_institution": True, "relevant_degree": True},
             "job_search": {"graduate_search_confirmed": True}}
    assert _irish_graduate_questions(draft, {}, facts) == []
