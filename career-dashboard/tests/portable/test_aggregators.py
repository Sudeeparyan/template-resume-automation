"""Careerjet and Jooble are optional leads: looked up on the employer's own board, never clicked by the app."""

from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from backend import telemetry
from backend.ai import keys
from backend.countries import load_pack
from backend.market import policy
from backend.market.readers import aggregators
from backend.market.store import MarketStore
from backend.role_titles import RoleMatcher
from backend.services import job_sources
from test_job_sources import Page

IRELAND = load_pack("ie").location_ok
TITLE = RoleMatcher(["Data Analyst"]).search
KEY = "k" * 24
JOOBLE = {"totalCount": 2, "jobs": [
    {"title": "Graduate Data Analyst", "company": "Acme Analytics", "location": "Dublin", "snippet": "SQL and dashboards",
     "salary": "€40,000", "link": "https://ie.jooble.org/desc/1", "updated": "2026-10-01T00:00:00", "id": 11},
    {"title": "Data Analyst", "company": "Unlisted Widgets", "location": "Cork", "snippet": "Excel reporting",
     "salary": "", "link": "https://ie.jooble.org/desc/2", "updated": "2026-10-01T00:00:00", "id": 12}]}
BOARD = {"jobs": [{"id": 5, "title": "Graduate Data Analyst", "absolute_url": "https://boards.greenhouse.io/acme/jobs/5",
                   "location": {"name": "Dublin, Ireland"}, "content": "&lt;p&gt;Report with SQL.&lt;/p&gt;"}]}


def recording(pages):
    calls = []

    def opener(request, timeout=None):
        calls.append({"url": request.full_url, "headers": dict(request.header_items()),
                      "body": json.loads(request.data) if request.data else None})
        body = pages[request.full_url.split("?")[0] if request.full_url.split("?")[0] in pages else request.full_url]
        return Page(request.full_url, json.dumps(body))

    opener.calls = calls
    return job_sources.Fetcher(opener=opener, min_interval=0, sleep=lambda s: None), calls


def test_careerjet_is_called_with_basic_auth_the_irish_locale_and_the_registered_ip():
    reader, calls = recording({aggregators.CAREERJET_URL: {"type": "JOBS", "jobs": [
        {"title": "Data Analyst", "company": "Acme", "locations": "Dublin", "description": "SQL", "url": "https://jobviewtrack.com/x",
         "date": "2026-10-01", "salary_min": 40000, "salary_max": 45000, "salary_currency_code": "EUR", "salary_type": "Y"}]}})
    leads, error = aggregators.careerjet_leads(reader, "data analyst", key=KEY, user_ip="203.0.113.7")
    assert error is None and leads[0]["raw_salary"]["salary_type"] == "Y"
    sent = calls[0]
    query = parse_qs(urlsplit(sent["url"]).query)
    assert query["locale_code"] == ["en_IE"] and query["user_ip"] == ["203.0.113.7"]
    assert sent["headers"]["Authorization"] == "Basic " + base64.b64encode(f"{KEY}:".encode()).decode()
    assert "203.0.113.7" not in telemetry.safe_url(sent["url"])  # the person's address never reaches a trace


def test_a_jooble_key_in_the_path_is_redacted_from_traces():
    assert KEY not in telemetry.safe_url(aggregators.JOOBLE_URL.format(key=KEY))


def test_jooble_leads_become_employer_postings_where_the_employer_has_a_board(monkeypatch):
    monkeypatch.setattr(keys, "load", lambda root: {"JOOBLE_API_KEY": KEY})
    reader, calls = recording({aggregators.JOOBLE_URL.format(key=KEY): JOOBLE,
                               "https://boards-api.greenhouse.io/v1/boards/acme/jobs": BOARD})
    rows = [{"name": "Acme Analytics", "ats": "greenhouse", "token": "acme", "_source": "directory", "_vouched": "Directory"}]
    store = MarketStore()
    postings, error, note = aggregators.aggregator_jobs("jooble", reader, ["data analyst"], TITLE, IRELAND,
                                                        root="unused", rows=rows, store=store)
    assert error is None and [p["url"] for p in postings] == ["https://boards.greenhouse.io/acme/jobs/5"]
    assert postings[0]["found_via"] == "jooble" and postings[0]["source_kind"] == "employer_feed"
    assert "1 read from the employer's own board, 1 kept as leads" in note
    assert calls[0]["body"] == {"keywords": "data analyst", "location": "Ireland", "page": 1}
    assert not any("jooble.org/desc" in call["url"] for call in calls)  # the aggregator's links are never opened
    with store.connect() as db:
        lead = dict(db.execute("SELECT * FROM postings WHERE source='jooble'").fetchone())
        observations = db.execute("SELECT COUNT(*) FROM salary_observations").fetchone()[0]
    assert lead["lead"] == 1 and lead["attribution"] == "Jobs by Jooble" and lead["company"] == "Unlisted Widgets"
    assert observations == 0


def test_the_request_budget_stops_a_source(monkeypatch):
    monkeypatch.setattr(keys, "load", lambda root: {"JOOBLE_API_KEY": KEY})
    monkeypatch.setitem(policy.SOURCE_POLICY["jooble"], "budget", {"per_day": 1, "lifetime": 500})
    reader, calls = recording({aggregators.JOOBLE_URL.format(key=KEY): {"jobs": []}})
    store = MarketStore()
    _, _, note = aggregators.aggregator_jobs("jooble", reader, ["data analyst", "bi analyst"], TITLE, IRELAND,
                                             root="unused", rows=[], store=store)
    assert len(calls) == 1 and note.startswith("1 searches") and store.budget_used("jooble")["today"] == 1


def test_an_aggregator_without_its_key_is_reported_not_set_up(monkeypatch):
    monkeypatch.setattr(keys, "load", lambda root: {})
    ok, missing = policy.ready("careerjet", "unused")
    assert not ok and "CAREERJET_API_KEY" in missing and "CAREERJET_USER_IP" in missing
    assert policy.ready("eures", "unused") == (True, "")
    postings, error, note = aggregators.aggregator_jobs("careerjet", None, ["x"], TITLE, IRELAND, root="unused", rows=[])
    assert postings == [] and error is None and note.startswith("not set up")


def test_source_keys_are_validated_and_listed_without_values(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        keys.save(tmp_path, "CAREERJET_USER_IP", "not an address")
    with pytest.raises(ValueError):
        keys.save(tmp_path, "SOME_OTHER_KEY", "x" * 20)
    monkeypatch.setattr(keys, "load", lambda root: {"JOOBLE_API_KEY": KEY})
    monkeypatch.setattr(keys, "source", lambda root, name: "saved in the app" if name == "JOOBLE_API_KEY" else None)
    status = {row["id"]: row for row in policy.status("unused", MarketStore())}
    assert status["jooble"]["ready"] and status["jooble"]["used"] == {"today": 0, "lifetime": 0}
    assert not status["careerjet"]["ready"] and KEY not in json.dumps(status)
