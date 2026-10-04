"""The Tracker lists each role once with its permit facts, the person's own overlays, filters, exports and alerts."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from backend.market import tracker
from backend.market.store import MarketStore
from backend.services import job_sources

FEED_URL = "https://boards.greenhouse.io/acme/jobs/5"


def posting(company, title, url, description, *, source="directory", kind="employer_feed", location="Dublin, Ireland", **extra):
    found = job_sources.make_posting(company, title, url, location, description, source=source, source_kind=kind,
                                     raw_salary=extra.pop("raw_salary", None))
    return {**found, **extra}


@pytest.fixture
def store():
    market = MarketStore()
    market.record_postings([
        posting("Acme Analytics", "Graduate Data Analyst", FEED_URL,
                "Build dashboards with SQL. Salary €40,000 - €45,000 per year."),
        posting("Acme Analytics", "Graduate Data Analyst", "https://ie.jooble.org/desc/1", "SQL and dashboards",
                source="jooble", kind="aggregator", lead=True, attribution="Jobs by Jooble"),
        posting("Rocket Widgets", "=SUM(A1) Data Analyst", "https://jobs.lever.co/rocket/abc",
                "We cannot provide visa sponsorship for this role."),
        posting("Google", "Data Analyst", "https://careers.google.example/jobs/1", "Analyse data for Google teams."),
        posting("Unlisted Widgets", "Data Analyst", "https://ie.jooble.org/desc/2", "Excel reporting",
                source="jooble", kind="aggregator", lead=True, attribution="Jobs by Jooble", location="Cork"),
    ])
    return market


class Services:
    """The few profile services the Tracker reads: the person's jobs, exclusions and preferences."""

    def __init__(self, jobs=(), excluded=()):
        self.job_rows, self.excluded_rows, self.prefs, self.saved = list(jobs), list(excluded), {}, []
        self.w = self

    def jobs(self):
        return self.job_rows

    def excluded(self):
        return self.excluded_rows

    def pref(self, key, default=None):
        return self.prefs.get(key, default)

    def set_pref(self, key, value):
        self.prefs[key] = value

    def add_posting(self, values, source="manual"):
        self.saved.append((values, source))
        return {"job": {"id": "JOB-1", "company": values["company"], "title": values["title"]}, "duplicate": False}

    def track_search_job(self, job_id, day):
        pass

    def today(self):
        return "2026-10-04"


def test_one_row_per_role_from_its_most_direct_source_with_refusals_hidden_by_default(store):
    result = tracker.query(store)
    titles = [(row["company"], row["source"]) for row in result["rows"]]
    assert ("Acme Analytics", "directory") in titles and ("Acme Analytics", "jooble") not in titles
    acme = next(row for row in result["rows"] if row["company"] == "Acme Analytics")
    assert acme["sources"] == 2 and acme["salary"]["kind"] == "advertised" and acme["salary"]["min"] == 40000
    assert "new" in acme["tags"] and not acme["lead"]
    assert all(row["statement"] != "refuses" for row in result["rows"])
    refusing = tracker.query(store, {"statement": "refuses"})["rows"]
    assert [row["statement_quote"] for row in refusing] == ["We cannot provide visa sponsorship for this role."]


def test_dete_counts_come_from_the_bundled_permit_statistics(store):
    rows = {row["company"]: row for row in tracker.query(store)["rows"]}
    assert rows["Google"]["permits"]["permits_24m"] > 0
    assert any("Google" in name for name in rows["Google"]["permits"]["legal_names"])
    with_permits = tracker.query(store, {"dete": "yes"})["rows"]
    assert {row["company"] for row in with_permits} == {"Google"}


def test_leads_carry_their_attribution_and_can_be_left_out(store):
    rows = tracker.query(store)["rows"]
    lead = next(row for row in rows if row["company"] == "Unlisted Widgets")
    assert lead["lead"] and lead["attribution"] == "Jobs by Jooble" and "lead" in lead["tags"]
    assert all(not row["lead"] for row in tracker.query(store, {"leads": ""})["rows"])
    assert [row["company"] for row in tracker.query(store, {"county": "Cork"})["rows"]] == ["Unlisted Widgets"]


def test_the_persons_applied_and_excluded_roles_are_overlaid_from_their_own_profile(store):
    people = Services(jobs=[{"id": "J1", "url": FEED_URL, "status": "applied"}],
                      excluded=[{"url": "https://careers.google.example/jobs/1"}])
    mine, excluded = tracker.overlays(people)
    shown = tracker.query(store, overlays=mine, excluded=excluded)["rows"]
    assert all(row["company"] not in ("Acme Analytics", "Google") for row in shown)
    with_applied = tracker.query(store, {"show_applied": "1"}, overlays=mine, excluded=excluded)["rows"]
    acme = next(row for row in with_applied if row["company"] == "Acme Analytics")
    assert acme["mine"] == {"job_id": "J1", "status": "applied"}


def test_exports_keep_text_as_text(store):
    rows = tracker.query(store, {"statement": "refuses"})["rows"]
    text = tracker.export_csv(rows)
    assert "'=SUM(A1) Data Analyst" in text and text.splitlines()[0].startswith("Title,Employer")
    with zipfile.ZipFile(io.BytesIO(tracker.export_xlsx(rows))) as book:
        sheet = book.read("xl/worksheets/sheet1.xml").decode()
        assert "=SUM(A1) Data Analyst" in sheet and "<f>" not in sheet and 't="inlineStr"' in sheet
        assert book.testzip() is None


def test_saving_re_reads_the_posting_and_goes_through_the_gates(store, monkeypatch):
    people = Services()
    key = store.key_for(FEED_URL, "Acme Analytics")
    monkeypatch.setattr(job_sources, "read_posting", lambda url, fetcher=None: {
        "description": "Fresh text: build dashboards with SQL.", "location": "Dublin, Ireland", "method": "ats_feed"})
    result = tracker.save(people, store, key)
    assert result["saved"] and people.saved[0][1] == "tracker"
    assert people.saved[0][0]["description"].startswith("Fresh text")
    lead_key = store.key_for("https://ie.jooble.org/desc/2", "Unlisted Widgets")
    with pytest.raises(ValueError, match="lead"):
        tracker.save(people, store, lead_key)


def test_a_posting_not_seen_for_a_week_is_not_saved_without_a_fresh_read(store, monkeypatch):
    key = store.key_for(FEED_URL, "Acme Analytics")
    old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat(timespec="seconds")
    with store.connect() as db:
        db.execute("UPDATE postings SET last_seen=? WHERE key=?", (old, key))
    monkeypatch.setattr(job_sources, "read_posting", lambda url, fetcher=None: None)
    with pytest.raises(ValueError, match="still open"):
        tracker.save(Services(), store, key)


def test_alerts_count_only_matches_first_seen_since_the_person_last_looked(store):
    people = Services()
    alert = tracker.save_alert(people, "Data roles in Cork", {"county": "Cork", "not_a_filter": "x"})
    assert alert["filters"] == {"county": "Cork"}
    assert tracker.alerts(people, store)[0]["new"] == 0  # everything was there when it was saved
    people.prefs[tracker.ALERTS][0]["seen_at"] = "2000-01-01T00:00:00+00:00"
    found = tracker.alerts(people, store)[0]
    assert found["new"] == 1 and found["examples"][0]["company"] == "Unlisted Widgets"
    tracker.mark_seen(people, alert["id"])
    assert tracker.alerts(people, store)[0]["new"] == 0
    tracker.delete_alert(people, alert["id"])
    assert tracker.alerts(people, store) == []
