"""Workable, Recruitee, Personio, Teamtailor and Lever EU boards are read in full from one request each."""

from __future__ import annotations

from backend.countries import load_pack
from backend.market.readers import ats
from backend.role_titles import RoleMatcher
from backend.services import job_sources, portals
from test_job_sources import fetcher

IRELAND = load_pack("ie")
TITLE = RoleMatcher(["Data Analyst"]).search

WORKABLE = {"name": "Acme Analytics", "jobs": [
    {"title": "Graduate Data Analyst", "shortcode": "AB12CD34EF", "state": "published", "telecommuting": False,
     "locations": [{"country": "Ireland", "countryCode": "IE", "city": "Dublin", "region": "County Dublin", "hidden": False}],
     "published_on": "2026-09-30", "created_at": "2026-09-29",
     "description": "<p>Build dashboards in Power BI.</p><p><strong>Requirements</strong></p><ul><li>SQL</li></ul>"},
    {"title": "Data Analyst", "shortcode": "PARIS1", "state": "published",
     "locations": [{"country": "France", "city": "Paris"}], "description": "<p>Analyse data in Paris.</p>"},
]}
WORKABLE_URL = "https://apply.workable.com/api/v1/widget/accounts/acme-analytics?details=true"


def test_a_workable_board_gives_full_postings_and_its_own_name():
    board = ats.read_board({"ats": "workable", "token": "acme-analytics", "name": "Acme Analytics"},
                           fetcher({WORKABLE_URL: WORKABLE}))
    assert board["error"] is None and board["name"] == "Acme Analytics"
    first = board["postings"][0]
    assert first["url"] == "https://apply.workable.com/acme-analytics/j/AB12CD34EF/"
    assert first["location"] == "Dublin, County Dublin, Ireland"
    assert "Power BI" in first["description"] and "- SQL" in first["description"]
    assert first["source_kind"] == "employer_feed" and first["requisition_id"] == "AB12CD34EF"


def test_the_employer_feed_keeps_only_matching_irish_workable_roles():
    row = {"ats": "workable", "token": "acme-analytics", "name": "Acme Analytics", "_source": "registry", "_vouched": "DETE"}
    found, error = job_sources.employer_feed(row, fetcher({WORKABLE_URL: WORKABLE}), TITLE, IRELAND.location_ok,
                                             country="ie", search_text="Ireland")
    assert error is None and [p["title"] for p in found] == ["Graduate Data Analyst"]
    assert found[0]["source"] == "registry" and found[0]["vouched"] == "DETE"


def test_a_bare_workable_link_finds_its_account_then_reads_the_whole_posting():
    page = ('<html><head><link rel="alternate" hreflang="x-default" '
            'href="https://apply.workable.com/acme-analytics/j/AB12CD34EF"></head></html>')
    detail = {"title": "Graduate Data Analyst", "description": "<p>Build dashboards.</p>",
              "requirements": "<p>SQL and Python.</p>", "benefits": "<p>Pension.</p>", "remote": False,
              "locations": [{"city": "Dublin", "region": "Leinster", "country": "Ireland"}], "published": "2026-09-30"}
    reader = fetcher({"https://apply.workable.com/j/AB12CD34EF": page,
                      "https://apply.workable.com/api/v2/accounts/acme-analytics/jobs/AB12CD34EF": detail})
    found = job_sources.read_posting("https://apply.workable.com/j/AB12CD34EF", reader)
    assert found["method"] == "workable_feed" and found["location"] == "Dublin, Leinster, Ireland"
    assert "Requirements\nSQL and Python." in found["description"] and "Benefits\nPension." in found["description"]


def test_a_recruitee_board_reads_pay_with_its_own_period():
    offers = {"offers": [{"id": 7, "slug": "data-analyst", "title": "Data Analyst", "status": "published",
                          "careers_url": "https://acme.recruitee.com/o/data-analyst", "company_name": "Acme Analytics",
                          "locations": [{"city": "Cork", "state": "Cork", "country": "Ireland"}], "remote": False,
                          "description": "<p>Analyse sales data.</p>", "requirements": "<p>Python and SQL.</p>",
                          "salary": {"min": "40000", "max": "48000", "period": "year", "currency": "EUR"},
                          "published_at": "2026-09-30 10:00:00 UTC", "close_at": "2026-10-30"}]}
    board = ats.read_board({"ats": "recruitee", "token": "acme"}, fetcher({"https://acme.recruitee.com/api/offers/": offers}))
    posting = board["postings"][0]
    assert board["name"] == "Acme Analytics" and posting["company"] == "Acme Analytics"
    assert posting["location"] == "Cork, Ireland" and posting["valid_through"] == "2026-10-30"
    assert "Python and SQL." in posting["description"]
    assert posting["salary"]["kind"] == "advertised" and posting["salary"]["annual_min"] == 40000


PERSONIO = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>1234567</id>
    <subcompany>Acme Analytics Ltd</subcompany>
    <office>Dublin</office>
    <additionalOffices><office>Galway</office></additionalOffices>
    <name>Junior Data Analyst</name>
    <jobDescriptions>
      <jobDescription><name>Your role</name><value><![CDATA[<p>Report on sales with <b>SQL</b>.</p>]]></value></jobDescription>
    </jobDescriptions>
    <createdAt>2026-09-28T09:00:00+00:00</createdAt>
  </position>
</workzag-jobs>"""


def test_a_personio_feed_is_parsed_and_a_feed_with_a_dtd_is_refused():
    url = "https://acme.jobs.personio.de/xml?language=en"
    board = ats.read_board({"ats": "personio", "token": "acme"}, fetcher({url: PERSONIO}))
    posting = board["postings"][0]
    assert board["name"] == "Acme Analytics Ltd" and posting["url"] == "https://acme.jobs.personio.de/job/1234567"
    assert posting["location"] == "Dublin; Galway" and posting["description"] == "Your role\nReport on sales with SQL ."
    hostile = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><workzag-jobs/>'
    assert ats.read_board({"ats": "personio", "token": "acme"}, fetcher({url: hostile}))["error"] == "Invalid Personio feed"


def test_personio_refuses_when_its_robots_txt_does():
    reader = fetcher({"https://acme.jobs.personio.de/robots.txt": "User-agent: *\nDisallow: /\n",
                      "https://acme.jobs.personio.de/xml?language=en": PERSONIO})
    board = ats.read_board({"ats": "personio", "token": "acme"}, reader)
    assert board["postings"] == [] and "robots.txt" in board["error"]


TEAMTAILOR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel><title>Acme Analytics</title>
<item><title>Data Analyst</title><description>&lt;p&gt;Use SQL and Python.&lt;/p&gt;</description>
<pubDate>Wed, 30 Sep 2026 09:00:00 +0100</pubDate><link>https://acme.teamtailor.com/jobs/123456-data-analyst</link>
<guid isPermaLink="false">abc</guid><remoteStatus>hybrid</remoteStatus>
<tt:locations><tt:location><tt:name>Dublin office</tt:name><tt:city>Dublin</tt:city><tt:country>Ireland</tt:country></tt:location></tt:locations>
</item></channel></rss>"""


def test_a_teamtailor_feed_gives_place_date_and_id():
    board = ats.read_board({"ats": "teamtailor", "token": "acme"},
                           fetcher({"https://acme.teamtailor.com/jobs.rss": TEAMTAILOR}))
    posting = board["postings"][0]
    assert posting["location"] == "Dublin, Ireland" and posting["requisition_id"] == "123456"
    assert posting["posted_at"].startswith("2026-09-30T09:00:00") and posting["description"] == "Use SQL and Python."


def test_lever_eu_boards_and_postings_use_the_eu_api():
    job = {"id": "6a0c", "text": "Data Analyst", "hostedUrl": "https://jobs.eu.lever.co/acme/6a0c",
           "categories": {"location": "Dublin, Ireland"}, "descriptionPlain": "Report with SQL.", "lists": [],
           "additionalPlain": "We can support a Critical Skills Employment Permit.", "createdAt": 1790000000000}
    reader = fetcher({"https://api.eu.lever.co/v0/postings/acme?mode=json": [job],
                      "https://api.eu.lever.co/v0/postings/acme/6a0c": job})
    assert portals.board_token({"careers_url": "https://jobs.eu.lever.co/acme"}) == ("lever_eu", "acme")
    postings, error = portals.fetch_board({"ats": "lever_eu", "token": "acme", "name": "Acme"}, fetcher=reader)
    assert error is None and postings[0]["url"] == "https://jobs.eu.lever.co/acme/6a0c"
    found = portals.official_posting("https://jobs.eu.lever.co/acme/6a0c", fetcher=reader)
    assert found["title"] == "Data Analyst" and "Critical Skills" in found["description"]


def test_careers_links_on_the_new_boards_become_tracked_rows():
    cases = {"https://apply.workable.com/acme-analytics/": ("workable", "acme-analytics"),
             "https://acme.recruitee.com/": ("recruitee", "acme"),
             "https://acme.jobs.personio.de/": ("personio", "acme"),
             "https://acme.teamtailor.com/jobs": ("teamtailor", "acme"),
             "https://jobs.eu.lever.co/acme": ("lever_eu", "acme")}
    for url, (kind, token) in cases.items():
        row = job_sources.tracked_row("Acme", url)
        assert (row["ats"], row["token"]) == (kind, token)
        assert job_sources.row_url({k: v for k, v in row.items() if k != "careers_url"}).startswith("https://")


def test_a_greenhouse_board_too_big_to_read_whole_gives_its_irish_roles_with_their_pay():
    from backend.services import job_sources, portals

    api = "https://boards-api.greenhouse.io/v1/boards/bigco/jobs"
    listed = {"jobs": [{"id": 1, "title": "Data Analyst", "location": {"name": "Dublin, Ireland"}},
                       {"id": 2, "title": "Data Analyst", "location": {"name": "San Francisco, CA"}},
                       {"id": 3, "title": "Software Engineer", "location": {"name": "Remote - Ireland"}}]}

    def detail(job_id, place):
        return {"id": job_id, "title": "Role", "absolute_url": f"https://boards.greenhouse.io/bigco/jobs/{job_id}",
                "location": {"name": place}, "content": "&lt;p&gt;Build SQL reports for the finance team.&lt;/p&gt;",
                "first_published": "2026-09-30T09:00:00Z",
                "pay_input_ranges": [{"min_cents": 4500000, "max_cents": 5500000, "currency_type": "EUR", "title": "Dublin"}]}

    asked = []

    class Reader:
        def json(self, url):
            asked.append(url)
            if url == api + "?content=true&pay_transparency=true":
                return None, job_sources.TOO_LARGE
            if url == api:
                return listed, None
            job_id = int(url.split("/jobs/")[1].split("?")[0])
            assert url.endswith("?pay_transparency=true")
            return detail(job_id, {1: "Dublin, Ireland", 3: "Remote - Ireland"}[job_id]), None

    found, error = portals.fetch_board({"name": "BigCo", "ats": "greenhouse", "token": "bigco"}, fetcher=Reader())
    assert error is None and [p["url"] for p in found] == ["https://boards.greenhouse.io/bigco/jobs/1",
                                                           "https://boards.greenhouse.io/bigco/jobs/3"]
    assert not any("/jobs/2" in url for url in asked)  # a role outside Ireland is never fetched
    assert "Build SQL reports" in found[0]["description"] and found[0]["raw_salary"]
