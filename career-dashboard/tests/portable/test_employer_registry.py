"""The employer registry accepts a careers board only when it is in Ireland and names the same employer."""

from __future__ import annotations

import csv
from datetime import date

import pytest

from backend.countries import load_pack
from backend.market import registry, resolver
from backend.permits.employer_names import is_individual
from test_job_sources import fetcher

IRELAND = load_pack("ie").location_ok
GH = "https://boards-api.greenhouse.io/v1/boards/"


def test_slug_guesses_drop_descriptors_and_never_try_a_generic_word_alone():
    assert resolver.slug_variants("Kitman Labs Limited") == ["kitman", "kitmanlabs"]
    assert "global" not in resolver.slug_variants("Global Data Services Limited")
    assert resolver.slug_variants("Data Limited") == []
    assert resolver.slug_variants("SFDC Ireland Limited")[0] == "salesforce"  # the curated alias first
    assert len(resolver.slug_variants("Amazon Development Centre Ireland Limited")) <= resolver.MAX_VARIANTS


@pytest.mark.parametrize("board, legal, kind", [
    ("Hugging Face", "Hugging Face Ireland Limited", "exact"),
    ("Salesforce", "SFDC Ireland Limited", "alias"),
    ("Careers at Kitman Labs", "Kitman Labs Limited", "exact"),
    ("Stripe", "Stripe Technology Company Limited", "brand"),
    ("Fin", "Intercom R&D Unlimited Company", ""),
    ("Apple Inc", "Applegreen Limited", ""),
    ("3100 Accenture Limited Company", "Accenture Limited", "exact"),  # Workday's company code
    ("3100 Acme Rockets Limited", "Accenture Limited", ""),
    ("3M Ireland", "3M Ireland Limited", "exact"),  # a brand with a digit is not a code
])
def test_board_names_are_matched_with_their_strength(board, legal, kind):
    assert resolver.name_match(board, legal) == kind


def test_a_named_board_with_an_irish_posting_is_accepted_after_the_first_guess_misses():
    reader = fetcher({GH + "kitmanlabs/jobs": {"jobs": [{"title": "Software Engineer", "location": {"name": "Dublin, Ireland"}}]},
                      GH + "kitmanlabs": {"name": "Kitman Labs"}})
    found = resolver.resolve("Kitman Labs Limited", reader, location_ok=IRELAND)
    assert found["status"] == "resolved" and (found["ats"], found["token"]) == ("greenhouse", "kitmanlabs")
    assert found["reason"] == "board name: Kitman Labs (exact)" and found["irish_postings"] == 1
    assert "greenhouse:kitman" in found["tried"] and not any(t.startswith("workable:") for t in found["tried"])


def test_a_board_of_another_employer_or_with_no_irish_posting_is_refused():
    reader = fetcher({GH + "acme/jobs": {"jobs": [{"title": "Analyst", "location": {"name": "Dublin"}}]},
                      GH + "acme": {"name": "Acme Rockets"},
                      GH + "acmefoods/jobs": {"jobs": [{"title": "Analyst", "location": {"name": "Boston, MA"}}]},
                      GH + "acmefoods": {"name": "Acme Foods"}})
    found = resolver.resolve("Acme Foods Limited", reader, location_ok=IRELAND)
    assert found["status"] == "unresolved"
    reasons = {f"{r['token']}": r["reason"] for r in found["refused"]}
    assert reasons["acmefoods"] == "no current posting in Ireland"
    assert "another employer" in reasons.get("acme", "another employer")


def test_a_board_that_states_no_name_needs_an_irish_posting_naming_the_employer():
    lever = [{"text": "Data Analyst", "categories": {"location": "Dublin, Ireland", "allLocations": ["Dublin, Ireland"]},
              "descriptionPlain": "At Kitman Labs we build performance software.", "lists": [], "additionalPlain": ""}]
    reader = fetcher({"https://api.lever.co/v0/postings/kitman?mode=json": lever})
    found = resolver.resolve("Kitman Labs Limited", reader, location_ok=IRELAND)
    assert found["status"] == "resolved" and found["match"] == "text" and found["ats"] == "lever"


def test_a_workday_link_is_checked_on_its_irish_listing_and_the_employer_its_postings_name():
    host = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/External"
    reader = fetcher({
        host + "/jobs": {"jobPostings": [{"title": "Data Analyst", "externalPath": "/job/Dublin/Data-Analyst_R1",
                                          "locationsText": "Dublin"}], "total": 1},
        host + "/job/Dublin/Data-Analyst_R1": {"jobPostingInfo": {"title": "Data Analyst", "location": "Dublin",
                                                                  "country": {"descriptor": "Ireland"},
                                                                  "jobDescription": "<p>Report with SQL.</p>"},
                                               "hiringOrganization": {"name": "Acme Analytics"}}})
    found = resolver.check_link("Acme Analytics Limited", "https://acme.wd3.myworkdayjobs.com/en-US/External", reader,
                                location_ok=IRELAND)
    assert found["status"] == "resolved" and found["token"] == "acme.wd3.myworkdayjobs.com/External"
    assert found["reason"] == "board name: Acme Analytics (exact)"
    refused = resolver.check_link("Acme Analytics Limited", "https://example.org/careers", reader, location_ok=IRELAND)
    assert refused["status"] == "unresolved"


def test_registry_rows_become_employer_feed_rows_and_gone_boards_are_set_aside(tmp_path, monkeypatch):
    path = tmp_path / "employer-registry.csv"
    registry.write([
        {"employer": "Kitman Labs", "legal_name": "Kitman Labs Limited", "ats": "lever", "token": "kitmanlabs",
         "permits_24m": 12, "irish_postings": 3, "name_check": "an Irish posting's own text names the employer",
         "verified_on": "2026-10-04"},
        {"employer": "Acme", "legal_name": "Acme Analytics Limited", "ats": "workday", "host": "acme.wd3.myworkdayjobs.com",
         "site": "External", "permits_24m": 5, "irish_postings": 1, "name_check": "board name: Acme (exact)",
         "verified_on": "2026-10-04"}], path)
    monkeypatch.setattr(registry, "path_for", lambda market: path)
    rows = registry.employer_rows("ie")
    assert [(r["ats"], r.get("token"), r.get("host")) for r in rows] == [("lever", "kitmanlabs", None),
                                                                          ("workday", "", "acme.wd3.myworkdayjobs.com")]
    assert rows[0]["_source"] == "registry" and "DETE" in rows[0]["_vouched"]
    assert len(registry.employer_rows("ie", gone={("lever", "kitmanlabs", "", "")})) == 1


def test_gone_boards_are_remembered_in_the_market_store():
    from backend.market.store import MarketStore

    store = MarketStore()
    store.mark_board({"ats": "lever", "token": "kitmanlabs", "name": "Kitman Labs"}, "gone")
    assert ("lever", "kitmanlabs", "", "") in store.gone_boards(days=30)
    store.mark_board({"ats": "lever", "token": "kitmanlabs", "name": "Kitman Labs"}, "active")
    assert store.gone_boards(days=30) == set()


def test_the_builder_names_rows_and_skips_employers_the_directory_reads():
    from build_employer_registry import covered, display_name

    assert display_name("Kitman Labs Limited", "", False) == "Kitman Labs"
    assert display_name("Stripe Technology Company Limited", "Stripe", True) == "Stripe"
    assert display_name("Kaseya Limited", "Kaseya Careers", True) == "Kaseya"
    assert display_name("Accenture Limited", "3100 Accenture Limited Company", True) == "Accenture"
    assert display_name("Actavo (Ireland) Limited", "Careers at Actavo", True) == "Actavo"
    assert covered("stripe technology", {"stripe"}) and not covered("kitman labs", {"stripe"})


def test_the_committed_registry_is_well_formed():
    path = registry.path_for("ie")
    if not path.is_file():
        pytest.skip("no registry built in this copy")
    rows = registry.load(path)
    seen = set()
    for row in rows:
        identity = (row["ats"], row["token"], row["host"], row["site"])
        assert identity not in seen
        seen.add(identity)
        assert row["ats"] in (*resolver.ATS_ORDER, "workday")
        assert (row["host"] and row["site"]) if row["ats"] == "workday" else row["token"]
        assert row["name_check"] and int(row["irish_postings"]) >= 1 and int(row["permits_24m"]) >= 1
        assert date.fromisoformat(row["verified_on"]) and not is_individual(row["legal_name"])
    with path.open(encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == registry.FIELDS
