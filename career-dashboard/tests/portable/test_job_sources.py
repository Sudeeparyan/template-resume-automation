"""The no-AI job sources read full postings politely: robots.txt, pacing, and the page's own data."""

from __future__ import annotations

import json
from email.message import Message
from urllib.error import HTTPError

from backend.countries import load_pack
from backend.role_titles import RoleMatcher
from backend.services import job_sources, portals
from backend.services.agents import verify_discovery_source

IRELAND = load_pack("ie")
TITLE = RoleMatcher(["Data Analyst"]).search


class Page:
    def __init__(self, url, body, charset="utf-8"):
        self.status, self.url = 200, url
        self.body = body.encode(charset) if isinstance(body, str) else body
        self.headers = Message()
        self.headers["Content-Type"] = f"text/html; charset={charset}"

    def read(self, _limit=-1):
        return self.body

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def site(pages: dict):
    """An opener serving ``pages`` (URL -> text, JSON-able value, or callable(request))."""
    calls = []

    def opener(request, timeout=None):
        url = request.full_url
        calls.append((request.get_method(), url, request.data))
        if url not in pages:
            raise HTTPError(url, 404, "Not Found", {}, None)
        body = pages[url]
        if callable(body):
            body = body(request)
        if not isinstance(body, (str, bytes)):
            body = json.dumps(body)
        return Page(url, body)

    opener.calls = calls
    return opener


def fetcher(pages, **options):
    return job_sources.Fetcher(opener=site(pages), min_interval=0, sleep=lambda s: None, **options)


def job_page(title="Data Analyst", company="Acme Analytics", locality="Dublin", country="IE",
             description="<p>Build Power BI dashboards and write SQL for the finance team.</p><ul><li>SQL</li></ul>",
             extra=None):
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": title,
               "description": description, "hiringOrganization": {"@type": "Organization", "name": company},
               "datePosted": "2026-09-20", "identifier": {"@type": "PropertyValue", "value": "R-77"},
               "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": locality,
                                                               "addressRegion": "Leinster", "addressCountry": country}}],
               **(extra or {})}
    return ('<html><head><script type="application/ld+json">' + json.dumps({"@graph": [posting]})
            + "</script></head><body>page</body></html>")


def test_jsonld_posting_reads_title_company_place_and_full_text():
    found = job_sources.jsonld_posting(job_page())
    assert found["title"] == "Data Analyst" and found["company"] == "Acme Analytics"
    assert found["location"] == "Dublin, Leinster, Ireland"
    assert "Power BI dashboards" in found["description"] and "- SQL" in found["description"]
    assert found["requisition_id"] == "R-77"
    assert IRELAND.location_ok(found["location"])
    assert job_sources.jsonld_posting("<html>no data</html>") is None


def test_robots_txt_is_obeyed_and_reported():
    pages = {"https://board.example/robots.txt": "User-agent: *\nDisallow: /jobs/\n",
             "https://board.example/jobs/1": job_page()}
    polite = fetcher(pages)
    text, error = polite.text("https://board.example/jobs/1")
    assert text is None and "robots.txt" in error
    assert [url for _, url, _ in polite.opener.calls] == ["https://board.example/robots.txt"]
    assert polite.refused == ["board.example"]
    # A documented public job API is read without a robots check.
    api = fetcher({"https://api.smartrecruiters.com/v1/companies/X/postings?country=ie&limit=100&offset=0": {"content": []}})
    assert api.json("https://api.smartrecruiters.com/v1/companies/X/postings?country=ie&limit=100&offset=0")[0] == {"content": []}


def test_requests_to_one_host_are_paced():
    waits = []
    pages = {"https://board.example/robots.txt": "User-agent: *\nAllow: /\n",
             "https://board.example/a": "a", "https://board.example/b": "b"}
    slow = job_sources.Fetcher(opener=site(pages), min_interval=5, sleep=waits.append)
    slow.text("https://board.example/a")
    slow.text("https://board.example/b")
    # robots.txt goes first; each later request to the host waits its turn.
    assert len(waits) == 2 and all(wait > 0 for wait in waits)


def test_text_falls_back_to_windows_1252():
    assert job_sources._decode("€68,300 – Dublin".encode("cp1252"), None) == "€68,300 – Dublin"
    assert job_sources._decode("Data – Dublin".encode("utf-8"), "utf-8") == "Data – Dublin"


def test_workday_listing_opens_only_matching_irish_titles():
    host, site_name = "acme.wd3.myworkdayjobs.com", "Careers"
    base = f"https://{host}/wday/cxs/acme/{site_name}"
    detail = {"jobPostingInfo": {"title": "Data Analyst", "jobDescription": "<p>SQL and Power BI reporting for finance.</p>",
                                 "location": "Dublin", "country": {"descriptor": "Ireland"}, "jobReqId": "R1",
                                 "externalUrl": f"https://{host}/{site_name}/job/Dublin/Data-Analyst_R1"}}
    pages = {f"https://{host}/robots.txt": "User-agent: *\nAllow: /\n",
             f"{base}/jobs": {"total": 3, "jobPostings": [
                 {"title": "Data Analyst", "externalPath": "/job/Dublin/Data-Analyst_R1", "locationsText": "Dublin, Ireland"},
                 {"title": "Payroll Specialist", "externalPath": "/job/Dublin/Payroll_R2", "locationsText": "Dublin, Ireland"},
                 {"title": "Data Analyst", "externalPath": "/job/Austin/Data-Analyst_R3", "locationsText": "Austin, TX"}]},
             f"{base}/job/Dublin/Data-Analyst_R1": detail}
    reader = fetcher(pages)
    found, error = job_sources.workday_jobs({"name": "Acme", "host": host, "site": site_name, "_vouched": "directory"},
                                            reader, TITLE, IRELAND.location_ok, search_text="Ireland")
    assert error is None and len(found) == 1
    posting = found[0]
    assert posting["company"] == "Acme" and posting["location"] == "Dublin, Ireland"
    assert posting["requisition_id"] == "R1" and "Power BI" in posting["description"]
    assert posting["source_kind"] == "employer_feed" and posting["vouched"] == "directory"
    opened = [url for method, url, _ in reader.opener.calls if method == "GET" and "/job/" in url]
    assert opened == [f"{base}/job/Dublin/Data-Analyst_R1"]
    assert job_sources.workday_parts(f"https://{host}/en-US/{site_name}/job/Dublin/Data-Analyst_R1") == (
        host, "acme", site_name, "/job/Dublin/Data-Analyst_R1")


def test_smartrecruiters_postings_are_read_in_full():
    api = "https://api.smartrecruiters.com/v1/companies/Acme/postings"
    pages = {f"{api}?country=ie&limit=100&offset=0": {"totalFound": 2, "content": [
                 {"id": "7440001", "name": "Data Analyst"}, {"id": "7440002", "name": "Chef"}]},
             f"{api}/7440001": {"name": "Data Analyst", "refNumber": "REF1", "releasedDate": "2026-09-20",
                                "postingUrl": "https://jobs.smartrecruiters.com/Acme/7440001-data-analyst",
                                "location": {"city": "Cork", "country": "ie", "fullLocation": "Cork, , Ireland"},
                                "jobAd": {"sections": {"jobDescription": {"title": "The job", "text": "<p>SQL daily.</p>"},
                                                       "qualifications": {"title": "You", "text": "<p>Power BI.</p>"}}}}}
    found, _ = job_sources.smartrecruiters_jobs({"name": "Acme", "token": "Acme"}, fetcher(pages), TITLE, country="ie")
    assert [p["title"] for p in found] == ["Data Analyst"]
    assert found[0]["location"] == "Cork, Ireland" and "Power BI" in found[0]["description"]
    assert job_sources.smartrecruiters_parts(found[0]["url"]) == ("Acme", "7440001")


def test_gradireland_reads_recent_matching_jobs_from_its_sitemap():
    sitemap = ("<urlset>"
               "<url><loc>https://gradireland.com/jobs/data-analyst-graduate-101</loc><lastmod>2099-01-01T00:00:00Z</lastmod></url>"
               "<url><loc>https://gradireland.com/jobs/chef-102</loc><lastmod>2099-01-01T00:00:00Z</lastmod></url>"
               "<url><loc>https://gradireland.com/jobs/data-analyst-103</loc><lastmod>2001-01-01T00:00:00Z</lastmod></url>"
               "<url><loc>https://gradireland.com/jobs/data-analyst-intern-104</loc><lastmod>2099-01-01T00:00:00Z</lastmod></url>"
               "</urlset>")
    pages = {"https://gradireland.com/robots.txt": "User-agent: *\nAllow: /\n",
             "https://gradireland.com/sitemap-0.xml": sitemap,
             "https://gradireland.com/jobs/data-analyst-graduate-101": job_page(title="Data Analyst Graduate"),
             "https://gradireland.com/jobs/data-analyst-intern-104": job_page(
                 title="Data Analyst Intern", extra={"validThrough": "2001-02-01T00:00:00Z"})}
    reader = fetcher(pages)
    found, _ = job_sources.gradireland_jobs(reader, TITLE, IRELAND.location_ok)
    assert [p["url"] for p in found] == ["https://gradireland.com/jobs/data-analyst-graduate-101"]
    assert found[0]["source"] == "gradireland" and found[0]["source_kind"] == "job_board"
    assert "gradireland" in found[0]["vouched"]
    opened = {url for _, url, _ in reader.opener.calls}
    assert "https://gradireland.com/jobs/chef-102" not in opened and "https://gradireland.com/jobs/data-analyst-103" not in opened


def test_jobs_ie_search_results_are_read_from_each_posting_page():
    state = {"searchResults": {"items": [
        {"id": 1, "title": "Data Analyst", "url": "/job/data-analyst/acme-job1", "companyName": "Acme",
         "location": "Dublin", "datePosted": "2099-01-01T00:00:00Z"},
        {"id": 2, "title": "Warehouse Operative", "url": "/job/warehouse/acme-job2", "companyName": "Acme",
         "location": "Dublin", "datePosted": "2099-01-01T00:00:00Z"}]}}
    listing = ('<script>window.__PRELOADED_STATE__ = window.__PRELOADED_STATE__ || {};'
               'window.__PRELOADED_STATE__["app-unifiedResultlist"] = ' + json.dumps(state) + ";</script>")
    pages = {"https://www.jobs.ie/robots.txt": "User-agent: *\nAllow: /jobs/\nAllow: /job/\n",
             "https://www.jobs.ie/jobs/data-analyst": listing,
             "https://www.jobs.ie/job/data-analyst/acme-job1": job_page(company="Acme")}
    found, _ = job_sources.jobs_ie_jobs(fetcher(pages), ["Data Analyst"], TITLE, IRELAND.location_ok)
    assert [p["url"] for p in found] == ["https://www.jobs.ie/job/data-analyst/acme-job1"]
    assert found[0]["company"] == "Acme" and found[0]["requisition_id"] == "1"


def test_askmanavi_roles_are_read_from_the_employers_own_feed(monkeypatch):
    roles = [{"id": "gh-acme-555", "city": "Dublin", "title": "Data Analyst", "company": "Acme",
              "applyUrl": "https://acme.example/careers?gh_jid=555", "location": "Dublin, Ireland",
              "postedDate": "2099-01-01", "visaSponsorship": "Yes"},
             {"id": "wd-acme-x", "city": "Dublin", "title": "Nurse", "company": "Acme",
              "applyUrl": "https://acme.wd1.myworkdayjobs.com/External/job/Dublin/Nurse_R9", "location": "Dublin"}]
    payload = json.dumps(roles, separators=(",", ":")).replace('"', '\\"')
    html = '<script>self.__next_f.push([1,"' + payload + '"])</script>'
    pages = {"https://askmanavi.com/robots.txt": "User-agent: *\nAllow: /\nDisallow: /api/\n",
             "https://askmanavi.com/graduate-tracker": html}

    def feed(url):
        assert url == "https://boards-api.greenhouse.io/v1/boards/acme/jobs/555"
        return {"content": "<p>SQL and Power BI for the insights team.</p>", "location": {"name": "Dublin"}}, None

    monkeypatch.setattr(portals, "_get_json", feed)
    found, _ = job_sources.askmanavi_jobs(fetcher(pages), TITLE, IRELAND.location_ok)
    assert len(found) == 1
    posting = found[0]
    assert posting["url"] == "https://job-boards.greenhouse.io/acme/jobs/555"
    assert posting["requisition_id"] == "555" and "Power BI" in posting["description"]
    assert "visa sponsorship as Yes" in posting["verification"]


def test_an_ai_lead_on_a_job_board_gets_the_boards_own_text(monkeypatch):
    monkeypatch.setattr(job_sources, "read_posting", lambda url, fetcher=None: {
        "description": "Full posting: SQL, Power BI, stakeholder reporting.", "location": "Galway, Ireland",
        "method": "structured_data"})
    lead = {"url": "https://www.jobs.ie/job/x-1", "location": "Ireland", "description": "AI summary",
            "verification": "Read the page."}
    assert verify_discovery_source(lead) == ""
    assert lead["description"].startswith("Full posting") and lead["location"] == "Galway, Ireland"
    assert "schema.org JobPosting" in lead["verification"]
    # A Workday lead whose requisition no longer reads is held, like any other ATS.
    monkeypatch.setattr(job_sources, "read_posting", lambda url, fetcher=None: None)
    gone = {"url": "https://acme.wd1.myworkdayjobs.com/External/job/Dublin/X_R1", "description": "", "verification": ""}
    assert "could not be read" in verify_discovery_source(gone)


def test_an_ai_lead_the_app_cannot_re_read_must_be_a_whole_posting(monkeypatch):
    monkeypatch.setattr(job_sources, "read_posting", lambda url, fetcher=None: None)
    whole = ("Responsibilities: build and maintain Power BI dashboards for the finance team, write SQL against the "
             "warehouse, reconcile monthly KPIs and explain variances to managers. ") * 5
    base = {"url": "https://careers.example.ie/jobs/42", "location": "Dublin, Ireland"}
    assert verify_discovery_source({**base, "description": whole, "verification": "Read the whole posting page."}) == ""
    assert "part of the posting" in verify_discovery_source(
        {**base, "description": "Data Analyst, Dublin. SQL and Tableau.", "verification": "Read the page."})
    for said in ("LinkedIn job-view page returned metadata only", "Could not read the full posting; used the listing",
                 "irishjobs.ie returned HTTP 403", "Only a search snippet was available"):
        assert "not verifiable" in verify_discovery_source({**base, "description": whole, "verification": said}), said


def test_irish_ats_and_board_hosts_still_need_legal_presence(tmp_path):
    from backend.job_quality import JobQualityService
    from backend.services.workspace_v2 import CareerServices
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/scripts"))
    from career import Workspace

    (tmp_path / "data/config").mkdir(parents=True)
    (tmp_path / "data/context").mkdir(parents=True)
    (tmp_path / "data/config/profile.yml").write_text(
        "country_pack: ie\ntarget_markets: [ie]\ncandidate:\n  full_name: Sample Person\n", encoding="utf-8")
    (tmp_path / "data/context/evidence.yml").write_text("claims: []\nprojects: []\n", encoding="utf-8")
    quality = JobQualityService(CareerServices(Workspace(tmp_path)))
    record = [{"title": "LinkedIn", "url": "https://www.linkedin.com/company/applegreen", "accessed_at": "2026-09-26"}]
    for url in ("https://www.rezoomo.com/job/92952/", "https://gradireland.com/jobs/data-analyst-1"):
        assert quality.assess_company("Applegreen", url, record, ["LinkedIn company page identifies the employer."], [])["state"] == "verified"
        assert quality.assess_company("Applegreen", url, [], ["No independent legal-presence record found."], [])["state"] == "needs_review"


def test_feeds_preset_postings_are_not_re_verified():
    posting = {"url": "https://gradireland.com/jobs/x-1", "description": "Board text", "verification": ""}
    assert verify_discovery_source(posting, preset="feeds") == ""
    assert posting["description"] == "Board text"


def test_tracked_rows_accept_the_documented_token_key():
    assert portals.board_token({"ats": "greenhouse", "token": "acme"}) == ("greenhouse", "acme")
    assert portals.board_token({"ats": "lever", "ats_token": "acme"}) == ("lever", "acme")


def test_the_irish_directory_is_well_formed():
    listed = job_sources.directory("ie")
    assert len(listed["employers"]) >= 30
    for row in listed["employers"]:
        assert row["ats"] in {"greenhouse", "lever", "ashby", "smartrecruiters", "workday"}, row
        assert (row.get("host") and row.get("site")) if row["ats"] == "workday" else row.get("token"), row
    assert job_sources.is_agency("Cpl Resources", "ie") and not job_sources.is_agency("Stripe", "ie")
    assert job_sources.directory("zz") == {"employers": [], "agencies": []}
