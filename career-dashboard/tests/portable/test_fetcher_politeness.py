"""The fetcher is polite to a struggling or busy site: Retry-After, backoff, Crawl-delay, 304s, gzip."""

import gzip
from email.message import Message
from urllib.error import HTTPError

from backend.market.http_cache import HttpCache
from backend.services import job_sources
from test_job_sources import Page

URL = "https://jobs.example.org/feed.json"


def headers(**values):
    message = Message()
    for key, value in values.items():
        message[key.replace("_", "-")] = value
    return message


def scripted(*answers):
    """An opener answering each request in turn: a (status, headers) error or a Page."""
    calls = []

    def opener(request, timeout=None):
        calls.append({k.casefold(): v for k, v in request.header_items()})
        answer = answers[min(len(calls), len(answers)) - 1]
        if isinstance(answer, tuple):
            status, extra = answer
            raise HTTPError(request.full_url, status, "error", extra, None)
        return answer

    opener.calls = calls
    return opener


def make(opener, **options):
    sleeps = []
    reader = job_sources.Fetcher(opener=opener, min_interval=0, sleep=sleeps.append, respect_robots=False, **options)
    return reader, sleeps


def test_retry_after_is_waited_out_once():
    reader, sleeps = make(scripted((429, headers(Retry_After="7")), Page(URL, '{"ok": true}')))
    assert reader.json(URL) == ({"ok": True}, None)
    assert reader.requests == 2 and 7 <= sleeps[0] < 8


def test_a_retry_after_longer_than_the_cap_is_not_waited():
    reader, sleeps = make(scripted((503, headers(Retry_After="600"))))
    assert reader.json(URL) == (None, "HTTP 503")
    assert reader.requests == 1 and not [s for s in sleeps if s >= 600]


def test_repeated_failures_back_off_then_open_the_circuit():
    reader, sleeps = make(scripted((500, headers())))
    for _ in range(job_sources.BREAKER_FAILURES):
        assert reader.json(URL)[1] == "HTTP 500"
    assert sleeps and sleeps[-1] > sleeps[0]  # each failure lengthens the pause
    data, error = reader.json(URL)
    assert data is None and error.startswith("skipped") and reader.requests == job_sources.BREAKER_FAILURES


def test_robots_crawl_delay_paces_that_host():
    robots = Page("https://slow.example.org/robots.txt", "User-agent: *\nCrawl-delay: 10\nAllow: /\n")
    page = Page("https://slow.example.org/jobs", "<html>jobs</html>")
    answers = iter([robots, page, page])
    sleeps = []
    reader = job_sources.Fetcher(opener=lambda request, timeout=None: next(answers), min_interval=1, sleep=sleeps.append)
    assert reader.text("https://slow.example.org/jobs")[0] == "<html>jobs</html>"
    assert reader.text("https://slow.example.org/jobs")[0] == "<html>jobs</html>"
    assert sleeps and max(sleeps) > 5  # the second page waited for the site's 10-second delay


def test_gzip_bodies_are_inflated():
    page = Page(URL, gzip.compress(b'{"jobs": [1, 2]}'))
    page.headers["Content-Encoding"] = "gzip"
    reader, _ = make(scripted(page))
    assert reader.json(URL) == ({"jobs": [1, 2]}, None)


def test_an_oversized_page_is_that_pages_problem_and_never_closes_the_host(monkeypatch):
    monkeypatch.setattr(job_sources, "MAX_BYTES", 100)
    plain = Page(URL, '{"jobs": "' + "x" * 200 + '"}')
    packed = Page(URL, gzip.compress(('{"jobs": "' + "y" * 500 + '"}').encode()))
    packed.headers["Content-Encoding"] = "gzip"
    reader, _ = make(scripted(plain, packed, plain, packed, Page(URL, '{"ok": true}')))
    for _ in range(4):
        data, error = reader.json(URL)
        assert data is None and error.startswith(job_sources.TOO_LARGE)
    assert not reader.broken("jobs.example.org") and reader.json(URL) == ({"ok": True}, None)


def test_a_documented_job_api_may_answer_with_a_whole_board(monkeypatch):
    # OpenAI's Ashby board is about 15 MB: over the web-page limit, under the job-API one.
    monkeypatch.setattr(job_sources, "MAX_BYTES", 100)
    monkeypatch.setattr(job_sources, "API_MAX_BYTES", 1000)
    board = '{"jobs": "' + "z" * 500 + '"}'
    api = "https://api.ashbyhq.com/posting-api/job-board/example"
    reader, _ = make(scripted(Page(api, board), Page(URL, board)))
    assert reader.json(api) == ({"jobs": "z" * 500}, None)
    assert reader.json(URL)[1].startswith(job_sources.TOO_LARGE)


def test_an_unchanged_page_is_read_from_the_cache(tmp_path):
    cache = HttpCache(tmp_path / "http.db")
    first = Page(URL, "<html>v1</html>")
    first.headers["ETag"] = '"abc"'
    reader, _ = make(scripted(first, (304, headers())), cache=cache)
    assert reader.text(URL)[0] == "<html>v1</html>"
    assert reader.text(URL)[0] == "<html>v1</html>"
    assert reader.opener.calls[1]["if-none-match"] == '"abc"'
    assert reader.opener.calls[0]["accept-encoding"] == "gzip"



def test_rules_after_a_blank_line_still_belong_to_their_group():
    # europa.eu's layout: the delay and later rules sit after blank lines in the "*" group.
    robots = Page("https://site.example.org/robots.txt", "User-agent: bingbot\nDisallow: /x/\n\nUser-agent: *\nDisallow: /cgi-bin/\n\n# note\nDisallow: /archive/\n\nCrawl-delay: 10\n")
    answers = iter([robots])
    reader = job_sources.Fetcher(opener=lambda request, timeout=None: next(answers), min_interval=1, sleep=lambda s: None)
    assert not reader.allowed("https://site.example.org/archive/page")
    assert reader.allowed("https://site.example.org/jobs")
    assert reader._delays["site.example.org"] == 10
