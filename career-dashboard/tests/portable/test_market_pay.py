"""Structured pay, market estimates and the salary policy: labelled evidence, never guessed."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.market.salary_estimates import estimate
from backend.market.store import MarketStore
from backend.services import opportunities, salary


def test_lever_ashby_and_careerjet_pay_fields_are_read_with_their_own_period():
    lever = salary.extract("", raw_salary={"min": 38000, "max": 42000, "currency": "EUR", "interval": "per-year-salary"})
    assert (lever["annual_min"], lever["annual_max"], lever["period"]) == (38000, 42000, "YEAR")
    ashby = salary.extract("", raw_salary={"compensationTierSummary": "€40K – €45K", "summaryComponents": [
        {"compensationType": "EquityPercentage", "minValue": 0.1, "maxValue": 0.2},
        {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "EUR", "minValue": 40000, "maxValue": 45000}]})
    assert (ashby["annual_min"], ashby["currency"], ashby["components"]) == (40000, "EUR", "base")
    careerjet = salary.extract("", raw_salary={"salary_min": 3200, "salary_max": 3500, "salary_type": "M",
                                               "salary_currency_code": "EUR", "salary": "€3,200 - €3,500 per month"})
    assert (careerjet["annual_min"], careerjet["period"]) == (38400, "MONTH")


def test_greenhouse_ranges_prefer_euro_and_never_guess_a_period():
    ranges = [{"min_cents": 15000000, "max_cents": 18000000, "currency_type": "USD", "title": "US base salary"},
              {"min_cents": 5000000, "max_cents": 6000000, "currency_type": "EUR", "title": "Ireland"}]
    pay = salary.extract("", raw_salary=ranges)
    assert (pay["currency"], pay["minimum"], pay["maximum"]) == ("EUR", 50000, 60000)
    assert pay["period"] == "UNKNOWN" and pay["annual_min"] is None  # "Ireland" states no period
    annual = salary.extract("", raw_salary=[{**ranges[1], "title": "Ireland annual base salary"}])
    assert annual["annual_min"] == 50000


def test_a_pay_figure_no_one_is_paid_is_unreadable_not_rescaled():
    # A live Greenhouse range: "Annual Cash Compensation Range" of €73,85 – €105,60 (7385-10560 cents).
    slip = [{"min_cents": 7385, "max_cents": 10560, "currency_type": "EUR", "title": "Annual Cash Compensation Range"}]
    assert salary.extract("", raw_salary=slip)["kind"] == "unknown"
    assert salary.extract("Salary: €40 per year.")["kind"] == "unknown"
    assert salary.extract("Pay: €15 per hour, 39 hours per week.")["annual_min"] == 15 * 39 * 52


def observe(store, employer, amount, *, family="data_analytics", level="entry"):
    title = {"data_analytics": "Junior Data Analyst", "software_engineering": "Graduate Software Engineer"}[family]
    store.record_postings([{"company": f"{employer} Limited", "title": title, "location": "Dublin",
                            "url": f"https://jobs.example.org/{employer}/{amount}", "description": f"Salary EUR {amount:,} per year.",
                            "salary": salary.extract(f"Salary EUR {amount:,} per year."), "source": "directory",
                            "source_kind": "employer_feed"}])


def test_an_estimate_needs_five_salaries_from_three_employers(tmp_path):
    store = MarketStore()
    for employer, amount in (("alpha", 34000), ("alpha", 36000), ("beta", 38000), ("beta", 40000)):
        observe(store, employer, amount)
    assert estimate("data_analytics", "entry", store=store) is None  # four salaries, two employers
    observe(store, "gamma", 42000)
    result = estimate("data_analytics", "entry", store=store)
    assert (result["observations"], result["employers"], result["median"]) == (5, 3, 38000)
    assert result["p25"] == 36000 and result["p75"] == 40000
    assert result["label"] == "Market estimate from 5 advertised salaries (3 employers). Not this vacancy's pay."
    later = datetime.now(timezone.utc) + timedelta(days=200)
    assert estimate("data_analytics", "entry", store=store, now=later) is None  # older than the window


def irish_job(description="Analyse data in SQL for our finance team; build dashboards in Power BI."):
    return {"market": "ie", "company": "Example Analytics Limited", "title": "Junior Data Analyst",
            "url": "https://jobs.example.org/roles/9", "description": description, "posting_metadata": {}}


def profile(policy="confirmed_or_estimated"):
    return {"job_search": {"salary_policy": policy}}


def test_a_market_estimate_can_prepare_a_job_only_under_that_policy(tmp_path):
    store = MarketStore()
    for employer, amount in (("a", 38000), ("b", 39000), ("c", 40000), ("d", 41000), ("e", 42000)):
        observe(store, employer, amount)
    job = irish_job()
    estimated = opportunities.evaluate(job, profile())
    assert estimated["section"] == "estimated_matches" and estimated["estimate"]["median"] == 40000
    assert opportunities.preparation_issue({"opportunity": estimated}) is None
    note = opportunities.pay_note({"opportunity": estimated})
    assert "states no salary" in note and "Not this vacancy's pay" in note and "EUR 36,605" in note
    strict = opportunities.evaluate(job, profile("confirmed_only"))
    assert "advertised pay only" in opportunities.preparation_issue({"opportunity": strict})


def test_no_estimate_keeps_the_job_in_needs_research(tmp_path):
    result = opportunities.evaluate(irish_job(), profile())
    assert result["estimate"] is None and result["section"] == "needs_research"
    assert opportunities.preparation_issue({"opportunity": result}).startswith("The vacancy's annual pay is not confirmed")


def test_by_default_a_job_that_states_no_pay_is_prepared_and_flagged(tmp_path):
    result = opportunities.evaluate(irish_job(), {})
    assert result["salary_policy"] == "include_unstated" and result["section"] == "needs_research"
    job = {"opportunity": result}
    assert opportunities.preparation_issue(job) is None
    assert not opportunities.pay_known(job)  # listed after jobs whose pay is known
    note = opportunities.pay_note(job)
    assert "states no salary" in note and "at least EUR 36,605" in note


def test_unstated_pay_is_not_a_warning_for_someone_who_needs_no_permit(tmp_path):
    citizen = {"work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "citizen",
                                                       "needs_sponsorship_later": "no"}}}
    result = opportunities.evaluate(irish_job(), citizen)
    assert result["needs_permit"] is False
    assert opportunities.preparation_issue({"opportunity": result}) is None
    assert opportunities.pay_note({"opportunity": result}) == ""


def test_a_range_that_starts_below_the_floor_is_flagged_and_pay_below_it_is_never_prepared(tmp_path):
    straddles = opportunities.evaluate(irish_job("Salary €32,000 - €40,000 per year. Analyse data in SQL."), {})
    assert opportunities.preparation_issue({"opportunity": straddles}) is None
    assert "starts at EUR 32,000, below your EUR 36,605 floor" in opportunities.pay_note({"opportunity": straddles})
    below = opportunities.evaluate(irish_job("Salary €28,000 - €30,000 per year. Analyse data in SQL."), {})
    assert below["section"] == "below_floor"
    assert opportunities.preparation_issue({"opportunity": below}).startswith("Advertised pay is below")


@pytest.mark.parametrize("words,state,points", [
    ("We offer visa sponsorship for this role.", "supports", 25),
    ("Applicants must have the right to work in Ireland. No visa sponsorship is available.", "refuses", 0),
    ("Analyse data for our finance team.", "silent", 10),
])
def test_the_permit_path_score_explains_each_part(words, state, points):
    result = opportunities.evaluate(irish_job(description=words + " Build dashboards in Power BI and SQL."), {})
    path = result["permit_path"]
    parts = {part["id"]: part for part in path["parts"]}
    assert result["sponsorship"]["state"] == state and parts["posting_statement"]["points"] == points
    assert path["label"] == "Evidence score, not approval likelihood" and path["disclaimer"] == "Not immigration advice"
    assert path["score"] == sum(part["points"] for part in path["parts"]) and 0 <= path["score"] <= 100
    assert parts["pay"]["points"] == 5 and parts["eures"]["points"] == 0


def test_a_jobsireland_posting_with_threshold_pay_scores_its_channel_and_pay():
    job = {**irish_job(description="Annual Salary: €36,605. Hours per Week: 39. Analyse data in SQL and Power BI."),
           "url": "https://jobsireland.ie/en-US/job-Details?id=2473309"}
    parts = {part["id"]: part for part in opportunities.evaluate(job, {})["permit_path"]["parts"]}
    assert parts["eures"]["points"] == 10 and parts["pay"]["points"] == 25
