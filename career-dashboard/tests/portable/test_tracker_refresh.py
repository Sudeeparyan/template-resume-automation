"""TrackerRefresh (backend/graphs/tracker_refresh.py) keeps the market store current for the Tracker:
no AI, once per computer, resumable, and never touching a profile's jobs."""

from __future__ import annotations

import pytest

from backend.graphs import tracker_refresh
from backend.market.store import MarketStore
from backend.services import job_sources
from test_hunt import ireland_profile


def fake_sources(monkeypatch, fail_on=None):
    calls = []

    def harvest(root, sources, *, title_ok, place_ok, keywords, tz=None, seconds=900, **_):
        (source,) = sources
        calls.append(source)
        if source == fail_on:
            raise ConnectionError("the network dropped")
        postings = [job_sources.make_posting(f"{source.title()} Employer", "Data Analyst", f"https://jobs.example/{source}/1",
                                             "Dublin, Ireland", "Build SQL reports for finance.", source=source,
                                             source_kind="employer_feed")]
        postings = [p for p in postings if title_ok(p["title"]) and place_ok(p["location"])]
        job_sources.record_in_market(postings)
        return postings, [f"{source}: {len(postings)} postings"]

    monkeypatch.setattr(job_sources, "harvest", harvest)
    return calls


def test_a_refresh_records_every_source_then_waits_until_it_is_due(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    calls = fake_sources(monkeypatch)
    done = tracker_refresh.run(roots=[str(services.w.root)])
    assert done["state"] == "completed" and calls == list(tracker_refresh.SOURCES)
    assert done["found"] == len(tracker_refresh.SOURCES) and done["counts"]["open"] == len(tracker_refresh.SOURCES)
    assert services.w.jobs() == []  # the Tracker's store only; the person's jobs are untouched
    skipped = tracker_refresh.run(roots=[str(services.w.root)])
    assert skipped["state"] == "skipped" and "next one is due" in skipped["reason"] and not tracker_refresh.due()
    assert tracker_refresh.run(roots=[str(services.w.root)], force=True)["state"] == "completed"
    assert tracker_refresh.run(roots=[])["state"] == "skipped"


def test_only_one_refresh_runs_at_a_time():
    store = MarketStore()
    first = store.claim_run(tracker_refresh.NAME, fresh_hours=6)
    assert first and store.claim_run(tracker_refresh.NAME, fresh_hours=6) is None
    store.finish_run(first, state="completed")
    assert store.claim_run(tracker_refresh.NAME, fresh_hours=0)


def test_a_stopped_refresh_resumes_after_its_last_finished_source(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    calls = fake_sources(monkeypatch, fail_on="eures")
    with pytest.raises(ConnectionError):
        tracker_refresh.run(roots=[str(services.w.root)])
    run_id = MarketStore().last_run(tracker_refresh.NAME)["id"]
    assert MarketStore().last_run(tracker_refresh.NAME)["state"] == "failed"
    assert calls == ["directory", "registry", "eures"]
    again = fake_sources(monkeypatch)  # the network is back
    result = tracker_refresh.resume(run_id)
    assert again == ["eures", "gradireland", "jobs_ie"]  # directory and registry were not read again
    assert result["found"] == len(tracker_refresh.SOURCES)
    assert [s["source"] for s in result["sources"]] == list(tracker_refresh.SOURCES)
