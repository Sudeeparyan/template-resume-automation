"""The shared market store keeps public postings only, refreshes them and counts budgets."""

from contextlib import closing
from datetime import date

from backend import paths
from backend.market.store import MarketStore
from backend.services import salary


def posting(**overrides):
    description = overrides.pop("description", "Build dashboards in Power BI and SQL for the finance team. Salary: EUR 40,000 per year.")
    base = {"company": "Example Analytics Limited", "title": "Junior Data Analyst", "location": "Dublin 2, Ireland",
            "url": "https://jobs.example.org/roles/123", "requisition_id": "", "description": description,
            "salary": salary.extract(description, url="https://jobs.example.org/roles/123", observed_at="2026-10-04"),
            "source": "directory", "source_kind": "employer_feed", "posted_at": "2026-10-01"}
    return {**base, **overrides}


def test_postings_are_stored_once_and_refreshed(tmp_path):
    store = MarketStore()
    assert store.path == paths.MARKET_DB and str(store.path).startswith(str(tmp_path))  # never the shared file
    assert store.record_postings([posting()]) == {"new": 1, "refreshed": 0, "skipped": 0}
    assert store.record_postings([posting(), {"title": "no company"}, "junk"]) == {"new": 0, "refreshed": 1, "skipped": 2}
    key = store.key_for("https://jobs.example.org/roles/123")
    saved = store.posting(key)
    assert saved["counties"] == ["Dublin"] and saved["posting_type"] == "entry" and saved["role_family"] == "data_analytics"
    assert saved["salary_min"] == 40000 and saved["statement"] == "silent" and saved["employer_key"] == "example analytics"
    assert [s["source"] for s in saved["sources"]] == ["directory"]


def test_a_eures_copy_keeps_its_listing_and_the_jobsireland_notice_is_neutral(tmp_path):
    store = MarketStore()
    notice = ("In order to work in Ireland a non-EEA National, unless they are exempted, must hold a valid employment "
              "permit. Please review the Eligibility and requirements for an employment permit if you are unsure of your "
              "eligibility to apply for this vacancy. ")
    store.record_postings([posting(url="https://jobsireland.ie/en-US/job-Details?id=2473309", source="eures",
                                   source_kind="official_board", on_eures=True, description=notice + "Analyse sales data.",
                                   source_urls=["https://europa.eu/eures/portal/jv-se/jv-details/X?lang=en"])])
    saved = store.posting(store.key_for("https://jobsireland.ie/en-US/job-Details?id=2473309"))
    assert saved["on_eures"] == 1 and saved["statement"] == "silent" and saved["source_rank"] == 2
    assert {s["url"] for s in saved["sources"]} == {"https://jobsireland.ie/en-US/job-Details?id=2473309",
                                                     "https://europa.eu/eures/portal/jv-se/jv-details/X?lang=en"}


def test_only_public_posting_fields_are_stored(tmp_path):
    store = MarketStore()
    store.record_postings([posting(relevance={"score": 91, "why": "private fit"}, fit="private", status="applied")])
    with closing(store.connect()) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(postings)")}
        text = " ".join(str(value) for row in db.execute("SELECT * FROM postings") for value in tuple(row))
    assert not {"relevance", "fit", "status", "notes", "profile_id"} & columns
    assert "private fit" not in text


def test_states_close_past_their_closing_date_and_go_stale(tmp_path):
    store = MarketStore()
    store.record_postings([posting(description="Junior role. Closing date: 01/10/2026. Salary EUR 38,000 per year.")])
    assert store.refresh_states(on=date(2026, 10, 4)) == {"closed": 1, "stale": 0}
    assert store.counts()["closed"] == 1


def test_budgets_stop_at_daily_and_lifetime_caps(tmp_path):
    store = MarketStore()
    assert store.take_budget("jooble", per_day=2, lifetime=3)
    assert store.take_budget("jooble", per_day=2, lifetime=3)
    assert not store.take_budget("jooble", per_day=2, lifetime=3)  # today's cap
    assert store.budget_used("jooble") == {"today": 2, "lifetime": 2}
    assert not store.take_budget("jooble", per_day=10, lifetime=2)  # the lifetime cap
    store.save_cursor("eures", "data analyst", {"newest": 5})
    assert store.cursor("eures", "data analyst") == {"newest": 5}
    run = store.start_run("eures")
    store.finish_run(run, state="complete", found=3, new=2)
    assert store.last_run("eures")["state"] == "complete"
