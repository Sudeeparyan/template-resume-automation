"""EURES reader contract: the public search API's shape, JobsIreland links, cursors and details.

The fixture mirrors the API's real field names (recorded 4 Oct 2026) with synthetic values only.
"""

import base64
import json

from backend.market.readers import eures
from backend.role_titles import RoleMatcher
from backend.services import job_sources
from backend.services.source_coverage import Coverage
from test_job_sources import fetcher

TITLE = RoleMatcher(["Data Analyst"]).search
NOTICE = ("In order to work in Ireland a non-EEA National, unless they are exempted, must hold a valid employment permit. "
          "Please review the Eligibility and requirements for an employment permit if you are unsure of your eligibility to "
          "apply for this vacancy. <br> ")


def eures_id(number, point="18"):
    return base64.b64encode(f"{number} {point}".encode()).decode().rstrip("=")


def jv(number, title, created, *, point="18", region="IE061", employer="Example Analytics Limited"):
    return {"id": eures_id(number, point), "title": title, "creationDate": created, "lastModificationDate": created,
            "description": NOTICE + f"Example Analytics is recruiting a {title}.\nAnalyse data in SQL and Power BI.\n"
                                    "Annual Salary: €36,605\n Hours per Week: 39\n",
            "employer": {"name": employer}, "locationMap": {"IE": [region]}, "numberOfPosts": 1,
            "positionScheduleCodes": ["fulltime"], "euresFlag": True}


def search_page(*items):
    return {"numberRecords": len(items), "jvs": list(items), "facets": []}


def test_the_jobsireland_number_is_read_only_from_its_connection_point():
    assert eures.jobsireland_number(eures_id(2473309)) == "2473309"
    assert eures.jobsireland_number("MjQ3MzMwOSAxOA") == "2473309"  # a real id's shape
    assert eures.jobsireland_number(eures_id(2473309, point="7")) == ""
    assert eures.jobsireland_number("not base64!") == ""


def test_search_body_is_ireland_titles_newest_first():
    body = eures.search_body("data analyst", 2)
    assert body["locationCodes"] == ["ie"] and body["page"] == 2 and body["sortSearch"] == "MOST_RECENT"
    assert body["keywords"] == [{"keyword": "data analyst", "specificSearchCode": "TITLE"}]


def test_a_result_becomes_a_jobsireland_posting_with_advertised_pay():
    found = eures.posting(jv(2473309, "Data Analyst", 1790956354000))
    assert found["url"] == "https://jobsireland.ie/en-US/job-Details?id=2473309"
    assert found["source_urls"] == [eures.PAGE_URL.format(id=eures_id(2473309))]
    assert found["location"] == "Dublin, Ireland" and found["nuts"] == ["IE061"] and found["on_eures"]
    assert found["source"] == "eures" and found["source_kind"] == "official_board"
    assert found["salary"]["annual_min"] == 36605 and found["salary"]["period"] == "YEAR"
    assert "JobsIreland" in found["vouched"] and found["posted_at"].startswith("2026-")


def test_a_non_jobsireland_result_keeps_the_eures_page():
    found = eures.posting(jv(99, "Data Analyst", 1, point="7", region="IE062"))
    assert found["url"] == eures.PAGE_URL.format(id=eures_id(99, "7"))
    assert found["location"] == "Mid-East, Ireland"


def test_pages_stop_at_the_profiles_cursor_and_details_add_closing_dates(tmp_path):
    coverage = Coverage(tmp_path)
    page_one = search_page(jv(3, "Data Analyst", 3000), jv(2, "Chef de Partie", 2000), jv(1, "Junior Data Analyst", 1000))
    detail = {"jvProfiles": {"en": {"lastApplicationDate": 1793318400000}}}

    def search(request):
        assert json.loads(request.data)["page"] == 1
        return page_one

    pages = {eures.SEARCH_URL: search, **{eures.DETAIL_URL.format(id=eures_id(n)): detail for n in (1, 2, 3)}}
    reader = fetcher(pages, respect_robots=False)
    found, error = eures.eures_jobs(reader, ["data analyst"], TITLE, lambda place: True, coverage=coverage)
    assert error is None
    assert [item["title"] for item in found] == ["Data Analyst", "Junior Data Analyst"]  # the chef's title does not match
    assert found[0]["valid_through"] == "2026-10-30"
    assert coverage.get("eures:data analyst")["cursor"] == {"newest": 3000}
    # The next pass reads only what is newer than this profile's cursor.
    page_one["jvs"].insert(0, jv(4, "Data Analyst II", 4000))
    again, _ = eures.eures_jobs(reader, ["data analyst"], TITLE, lambda place: True, coverage=coverage)
    assert [item["title"] for item in again] == ["Data Analyst II"]
    # Another profile has its own cursor and still sees everything.
    other, _ = eures.eures_jobs(reader, ["data analyst"], TITLE, lambda place: True, coverage=Coverage(tmp_path / "other"))
    assert len(other) == 3


def test_a_failed_search_keeps_the_cursor_and_reports_why(tmp_path):
    coverage = Coverage(tmp_path)
    coverage.checkpoint("eures:data analyst", {"newest": 10}, "complete")
    found, error = eures.eures_jobs(fetcher({}, respect_robots=False), ["data analyst"], TITLE, lambda p: True, coverage=coverage)
    assert found == [] and error == "HTTP 404"
    assert coverage.get("eures:data analyst")["cursor"] == {"newest": 10}
    assert coverage.get("eures:data analyst")["state"] == "failed"


def test_harvest_reads_eures_and_records_the_market(tmp_path, monkeypatch):
    from backend.market.store import MarketStore

    monkeypatch.setattr(job_sources, "employer_rows", lambda root, source: [])
    pages = {eures.SEARCH_URL: search_page(jv(2473500, "Data Analyst", 5000))}
    found, coverage = job_sources.harvest(tmp_path, ["eures"], title_ok=TITLE, place_ok=lambda p: True,
                                          keywords=["data analyst"], fetcher=fetcher(pages, respect_robots=False))
    assert [item["url"] for item in found] == ["https://jobsireland.ie/en-US/job-Details?id=2473500"]
    assert "EURES / JobsIreland: 1 postings matched" in coverage[0]
    assert MarketStore().counts()["open"] == 1
