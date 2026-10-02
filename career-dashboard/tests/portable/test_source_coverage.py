"""Broad coverage survives quotas/restarts and never invents a verified employer."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.countries import load_pack
from backend.services import job_sources, search_plan
from backend.services.source_coverage import Coverage


def test_all_counties_and_foreign_namesakes():
    ireland = load_pack("ie")
    assert len(search_plan.IRELAND_COUNTIES) == 26
    for county in search_plan.IRELAND_COUNTIES:
        assert ireland.location_ok(county), county
        assert ireland.location_ok("Town, County " + county), county
    for foreign in ("Dublin, OH", "Kerry, Texas", "Belfast, Northern Ireland"):
        assert not ireland.location_ok(foreign)
    assert ireland.location_ok("Monasterevin, Kildare")


def test_strategy_rotation_is_read_only_and_all_roles_counties_are_present(tmp_path):
    plan = {"markets": ["ie"], "cities": {"ie": ["Dublin"]}, "plain_roles": ["analyst", "engineer", "scientist", "tester", "designer"],
            "early_career": True}
    passes = search_plan.strategies(tmp_path, sources="ai", plan=plan)
    assert not (tmp_path / "data").exists()
    county_passes = [p for p in passes if "county" in p]
    assert len(county_passes) == 26 * 5
    year = datetime.now(ZoneInfo("Europe/Dublin")).year
    grad = next(p for p in passes if p["id"] == "ai:ie:graduate")
    assert str(year + 1) in " ".join(grad["queries"])
    Coverage(tmp_path).checkpoint(passes[0]["id"], state="attempted")
    assert search_plan.strategies(tmp_path, sources="ai", plan=plan)[-1]["id"] == passes[0]["id"]


def test_checkpoint_restart_is_profile_scoped_and_registry_needs_verification(tmp_path):
    one, two = Coverage(tmp_path / "one"), Coverage(tmp_path / "two")
    one.checkpoint("feed", {"page": 3}, "partial", found=2)
    assert Coverage(tmp_path / "one").get("feed")["cursor"] == {"page": 3}
    assert two.summary()["sources"] == []
    posting = {"company": "Example", "market": "ie", "location": "Cork", "description": "Full description",
               "url": "https://jobs.lever.co/example/abc"}
    assert not one.register_employer(posting)
    assert not one.register_employer({**posting, "url": "https://example.ie/jobs/abc"}, verified=True)
    assert one.register_employer(posting, verified=True)
    assert one.employers("ie")[0]["careers_url"] == "https://jobs.lever.co/example"
    assert not one.employers("us") and not two.employers("ie")


def test_workday_skips_duplicates_before_details_and_resumes(tmp_path):
    coverage = Coverage(tmp_path)
    listing = [{"title": "Analyst", "externalPath": f"/job/Cork/R{i}", "locationsText": "Cork"} for i in range(4)]
    class Reader:
        def __init__(self):
            self.details = []
        def json(self, url, payload=None):
            if payload is not None:
                return {"total": 4, "jobPostings": listing}, None
            self.details.append(url)
            return {"jobPostingInfo": {"title": "Analyst", "jobDescription": "Analyze data. EUR 40,000 per year.",
                                        "location": "Cork", "jobReqId": url.rsplit("/", 1)[-1]}}, None
    row = {"name": "Example", "host": "example.wd1.myworkdayjobs.com", "site": "Careers"}
    reader = Reader()
    first, _ = job_sources.workday_jobs(row, reader, bool, bool, search_text="Ireland", details=1, coverage=coverage,
                                        skip_posting=lambda p: p["url"].endswith("R0"))
    assert [p["requisition_id"] for p in first] == ["R1"]
    assert len(reader.details) == 1
    second, _ = job_sources.workday_jobs(row, reader, bool, bool, search_text="Ireland", details=1, coverage=Coverage(tmp_path))
    assert [p["requisition_id"] for p in second] == ["R2"]
    assert coverage.get("workday:example.wd1.myworkdayjobs.com/Careers:Ireland")["state"] == "partial"


def test_jsonld_and_posting_preserve_salary_and_deadline():
    raw = {"currency": "EUR", "value": {"minValue": 36000, "maxValue": 42000, "unitText": "YEAR"}}
    data = {"@type": "JobPosting", "title": "Analyst", "description": "Analyze data", "baseSalary": raw,
            "validThrough": "2099-12-31", "hiringOrganization": {"name": "Example"}}
    parsed = job_sources.jsonld_posting('<script type="application/ld+json">' + json.dumps(data) + '</script>')
    assert parsed["raw_salary"] == raw
    posting = job_sources.make_posting("Example", "Analyst", "https://example.ie/job/1", "Cork", "Analyze data",
                                       source="test", source_kind="employer_feed", raw_salary=parsed["raw_salary"],
                                       valid_through=parsed["valid_through"])
    assert posting["salary"]["annual_min"] == 36000
    assert posting["valid_through"] == "2099-12-31"


def test_fetcher_limits_global_and_per_host_concurrency():
    counts, peaks, lock = {}, {}, threading.Lock()
    class Response:
        status = 200
        headers = None
        def __init__(self, url): self.url = url
        def geturl(self): return self.url
        def read(self, _): return b"ok"
        def __enter__(self): return self
        def __exit__(self, *_): return False
    def opener(request, timeout):
        host = request.host
        with lock:
            counts[host] = counts.get(host, 0) + 1
            counts["all"] = counts.get("all", 0) + 1
            peaks[host] = max(peaks.get(host, 0), counts[host])
            peaks["all"] = max(peaks.get("all", 0), counts["all"])
        time.sleep(0.01)
        with lock:
            counts[host] -= 1
            counts["all"] -= 1
        return Response(request.full_url)
    fetcher = job_sources.Fetcher(opener=opener, min_interval=0, respect_robots=False)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(fetcher.text, [f"https://host{i % 6}.example/{i}" for i in range(24)]))
    assert all(body == "ok" for body, _ in results)
    assert 1 < peaks["all"] <= 4
    assert all(value == 1 for key, value in peaks.items() if key != "all")
