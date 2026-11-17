"""A pasted link is resolved to the employer's own posting, or the person is asked for it; nothing is guessed."""

from __future__ import annotations

import json

from backend.services import job_sources, lead_resolver
from test_job_sources import Page, fetcher, job_page, site

LEVER_JOB = {"id": "uuid-1", "text": "Graduate Data Analyst", "hostedUrl": "https://jobs.lever.co/acme/uuid-1",
             "categories": {"location": "Dublin, Ireland"}, "descriptionPlain": "Build dashboards with SQL and Power BI. " * 5,
             "lists": [], "additionalPlain": ""}
LEVER_PAGE = job_page(title="Graduate Data Analyst", company="Acme Analytics")


def test_a_site_that_refuses_automated_reading_is_never_opened():
    reader = fetcher({})
    result = lead_resolver.resolve("https://www.linkedin.com/jobs/view/12345", fetcher=reader)
    assert result["status"] == "needs_employer_link" and result["site"] == "linkedin.com"
    assert reader.opener.calls == []


def test_an_ats_posting_is_read_from_its_feed_with_its_title_and_employer():
    reader = fetcher({"https://boards-api.greenhouse.io/v1/boards/acme/jobs/123?pay_transparency=true": {
        "title": "Data Analyst", "company_name": "Acme Analytics", "location": {"name": "Dublin, Ireland"},
        "content": "&lt;p&gt;Report on sales with SQL and Python every week.&lt;/p&gt;"}})
    result = lead_resolver.resolve("https://boards.greenhouse.io/acme/jobs/123", fetcher=reader)
    assert result["status"] == "read" and result["method"] == "ats_feed"
    posting = lead_resolver.to_posting(result)
    assert (posting["company"], posting["title"], posting["location"]) == ("Acme Analytics", "Data Analyst", "Dublin, Ireland")


def test_an_aggregator_page_linking_one_employer_posting_is_followed_and_the_employer_read_from_its_page():
    aggregator = '<html><body><a href="https://jobs.lever.co/acme/uuid-1">Apply on the employer site</a></body></html>'
    reader = fetcher({"https://jobs.example.org/view/9": aggregator,
                      "https://api.lever.co/v0/postings/acme/uuid-1": LEVER_JOB,
                      "https://jobs.lever.co/acme/uuid-1": LEVER_PAGE})
    result = lead_resolver.resolve("https://jobs.example.org/view/9", fetcher=reader)
    assert result["status"] == "read" and result["url"] == "https://jobs.lever.co/acme/uuid-1"
    assert result["via"] == "https://jobs.example.org/view/9"
    posting = lead_resolver.to_posting(result)
    assert posting["company"] == "Acme Analytics" and posting["title"] == "Graduate Data Analyst"
    assert "reached from the pasted link" in posting["verification"]


def test_a_tracking_link_that_redirects_to_an_ats_posting_is_read_there():
    pages = {"https://api.lever.co/v0/postings/acme/uuid-1": json.dumps(LEVER_JOB),
             "https://jobs.lever.co/acme/uuid-1": LEVER_PAGE}

    def opener(request, timeout=None):
        url = request.full_url
        if url == "https://track.example.org/r/1":
            return Page("https://jobs.lever.co/acme/uuid-1", "<html>redirected</html>")
        return site(pages)(request, timeout)

    reader = job_sources.Fetcher(opener=opener, min_interval=0, sleep=lambda s: None)
    result = lead_resolver.resolve("https://track.example.org/r/1", fetcher=reader)
    assert result["status"] == "read" and result["url"] == "https://jobs.lever.co/acme/uuid-1"


def test_a_page_with_its_own_structured_posting_is_read_and_one_with_several_postings_is_not_guessed():
    reader = fetcher({"https://careers.example.org/jobs/1": job_page(),
                      "https://news.example.org/roundup": '<a href="https://jobs.lever.co/acme/a1">One</a>'
                                                          '<a href="https://jobs.lever.co/acme/b2">Two</a>'})
    one = lead_resolver.resolve("https://careers.example.org/jobs/1", fetcher=reader)
    assert one["status"] == "read" and one["method"] == "structured_data"
    several = lead_resolver.resolve("https://news.example.org/roundup", fetcher=reader)
    assert several["status"] == "needs_employer_link" and len(several["candidates"]) == 2


def test_posting_links_are_told_apart_from_boards():
    assert lead_resolver.posting_link("https://jobs.lever.co/acme/uuid-1")
    assert not lead_resolver.posting_link("https://jobs.lever.co/acme")
    assert lead_resolver.posting_link("https://apply.workable.com/acme/j/AB12/")
    assert lead_resolver.posting_link("https://acme.recruitee.com/o/data-analyst")
    assert not lead_resolver.posting_link("https://example.org/o/data-analyst")
    assert lead_resolver.board_links('<a href="https://jobs.lever.co/acme">Jobs</a>', "https://acme.example") == [
        "https://jobs.lever.co/acme"]
