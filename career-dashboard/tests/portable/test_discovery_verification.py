"""Discovery keeps employer evidence, posting completeness and market eligibility separate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.countries import load_pack
from backend.job_quality import JobQualityService
from backend.services import portals
from backend.services.agents import verify_discovery_source
from backend.services.workspace_v2 import CareerServices

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/scripts"))
from career import Workspace  # noqa: E402


def test_ordinary_caveats_do_not_block_a_sourced_company(tmp_path):
    config = tmp_path / "data/config"
    context = tmp_path / "data/context"
    config.mkdir(parents=True)
    context.mkdir(parents=True)
    (config / "profile.yml").write_text("country_pack: us\ntarget_markets: [us]\ncandidate:\n  full_name: Sample Person\n", encoding="utf-8")
    (context / "evidence.yml").write_text("claims: []\nprojects: []\n", encoding="utf-8")
    service = CareerServices(Workspace(tmp_path))
    quality = JobQualityService(service)
    url = "https://boards.greenhouse.io/samplefreight/jobs/123"
    sources = [{"title": "Company page", "url": "https://www.linkedin.com/company/samplefreight",
                "accessed_at": "2026-09-25"}]
    caveats = ["Seat supports one of the company's clients",
               "Location says Remote without naming a country"]

    checked = quality.assess_company("Sample Freight", url, sources,
                                     ["LinkedIn company page identifies the employer."], caveats)
    assert checked["state"] == "verified"
    assert checked["red_flags"] == []
    assert checked["caveats"] == caveats
    with service.w.connect() as db:
        row = db.execute("SELECT findings,red_flags FROM company_checks ORDER BY rowid DESC LIMIT 1").fetchone()
    assert json.loads(row["red_flags"]) == []
    assert all(caveat in " ".join(json.loads(row["findings"])) for caveat in caveats)

    fraud = quality.assess_company("Sample Freight", url, sources,
                                   ["LinkedIn company page identifies the employer."],
                                   ["Recruiter requests payment fee before interview"])
    assert fraud["state"] == "blocked"
    assert fraud["red_flags"] == ["Recruiter requests payment fee before interview"]
    assert quality.assess_company("Unverified Company", "https://boards.greenhouse.io/unverified/jobs/1",
                                  [{"title": "Posting", "url": "https://boards.greenhouse.io/unverified/jobs/1",
                                    "accessed_at": "2026-09-25"}],
                                  ["No registered legal entity could be confirmed."], [])["state"] == "needs_review"


def test_official_ats_facts_replace_ai_lead_and_keep_remote_market_unknown(monkeypatch):
    def feed(url):
        assert url == "https://boards-api.greenhouse.io/v1/boards/samplefreight/jobs/123?pay_transparency=true"
        return ({"content": "<p>Full duties: answer customers by phone and email.</p>",
                 "location": {"name": "Remote"}}, None)

    monkeypatch.setattr(portals, "_get_json", feed)
    lead = {"url": "https://boards.greenhouse.io/samplefreight/jobs/123",
            "location": "Remote, United States", "description": "AI snippet",
            "verification": "The web page was read."}
    assert verify_discovery_source(lead) == ""
    assert lead["description"].startswith("Full duties:")
    assert lead["location"] == "Remote"
    assert not load_pack("us").location_ok(lead["location"])
    assert not load_pack("ie").location_ok(lead["location"])


def test_closed_or_partially_read_posting_stays_out(monkeypatch):
    monkeypatch.setattr(portals, "_get_json", lambda url: (None, "HTTP 404"))
    closed = {"url": "https://job-boards.greenhouse.io/sampleverify/jobs/456",
              "location": "Nashville, TN", "description": "A search snippet",
              "verification": "Only the About section could be read."}
    assert "could not be read" in verify_discovery_source(closed)
    assert closed["description"] == "A search snippet"

    partial = {"url": "https://jobs.example.test/456", "location": "Nashville, TN",
               "description": "A search snippet",
               "verification": "The full job description could not be read; the duties came from snippets."}
    assert "not verifiable" in verify_discovery_source(partial)


def test_official_ats_location_can_confirm_the_us_market(monkeypatch):
    monkeypatch.setattr(portals, "_get_json", lambda url: (
        {"content": "<p>Answer customer requests and track every support ticket.</p>",
         "location": {"name": "Nashville, Tennessee, United States"}}, None))
    lead = {"url": "https://job-boards.greenhouse.io/sampleverify/jobs/456",
            "location": "Somewhere", "description": "AI snippet", "verification": "Partial web page."}
    assert verify_discovery_source(lead) == ""
    assert load_pack("us").location_ok(lead["location"])
    assert "ATS feed" in lead["verification"]
