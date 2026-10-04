"""Facts read from a posting's own fields: county, kind, level, role family and closing date."""

import pytest

from backend.market import normalize


@pytest.mark.parametrize("location,expected", [
    ("Dublin 18, Ireland", ["Dublin"]),
    ("Sandyford Business Park", ["Dublin"]),
    ("Ringaskiddy, Co. Cork", ["Cork"]),
    ("Carrick-on-Shannon, Co. Leitrim", ["Leitrim"]),
    ("Shannon, Ireland", ["Clare"]),
    ("Dublin or Galway (hybrid)", ["Dublin", "Galway"]),
    ("Belfast, Northern Ireland", []),
    ("Remote, Ireland", []),
])
def test_counties_come_from_the_location_field(location, expected):
    assert normalize.counties(location) == expected


def test_a_eures_region_settles_a_county_only_when_it_has_one():
    assert normalize.counties("", ["IE061"]) == ["Dublin"]
    assert normalize.counties("", ["IE062"]) == []
    assert normalize.region(["IE062"]) == "Mid-East"
    assert normalize.counties("Naas", ["IE062"]) == ["Kildare"]  # the location field wins


@pytest.mark.parametrize("title,description,kind", [
    ("Graduate Programme 2027 - Technology", "", "graduate_programme"),
    ("Graduate Software Engineer", "Join our graduate programme in September.", "graduate_programme"),
    ("Summer Internship, Data", "", "internship"),
    ("Junior Data Analyst", "", "entry"),
    ("Data Analyst", "You will have 1-2 years of experience in SQL.", "entry"),
    ("Data Analyst", "At least 4 years of experience in analytics.", "experienced"),
    ("Senior Data Engineer", "", "experienced"),
    ("Data Analyst", "Build dashboards.", "unspecified"),
])
def test_posting_kind(title, description, kind):
    assert normalize.posting_type(title, description) == kind


def test_level_and_role_family_for_salary_estimates():
    assert normalize.level("Graduate Data Analyst") == "entry"
    assert normalize.level("Data Analyst", "3+ years of experience") == "mid"
    assert normalize.level("Lead Data Scientist") == "senior"
    assert normalize.level("Data Analyst", "Build dashboards.") == ""
    assert normalize.role_family("Junior Data Analyst (Power BI)") == "data_analytics"
    assert normalize.role_family("Graduate Software Engineer") == "software_engineering"
    assert normalize.role_family("Data Engineer") == "data_engineering"
    assert normalize.role_family("Chef de Partie") == "hospitality"
    assert normalize.role_family("Mystery Role") == ""


def test_closing_dates_are_read_day_first_and_never_guessed():
    assert normalize.closing_date("", "2026-10-30T23:59:00Z") == "2026-10-30"
    assert normalize.closing_date("Closing date: 30/10/2026. Apply now.") == "2026-10-30"
    assert normalize.closing_date("Applications close on Friday, 6th November 2026") == "2026-11-06"
    assert normalize.closing_date("Apply soon; we review applications weekly.") == ""
    assert normalize.closing_date("Closing date: 31/02/2026") == ""


def test_title_key_ignores_codes_and_brackets():
    assert normalize.title_key("Data Analyst (Hybrid) - Req #R-1234") == normalize.title_key("Data Analyst")
    assert normalize.epoch_ms(1790956354000).startswith("2026-")
    assert normalize.epoch_ms("not a date") == ""
