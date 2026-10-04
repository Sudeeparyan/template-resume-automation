"""Cover letters (services/cover_letters.py): only registered evidence and verified company facts,
checked before saving, with the evidence ids the letter really uses."""

from __future__ import annotations

import json

import yaml
from fastapi.testclient import TestClient

import backend.profiles
from backend.dashboard.shell import create_shell
from backend.market.store import MarketStore
from backend.profiles import ProfileStore
from backend.services import cover_letters
from backend.services.workspace_v2 import CareerServices
from career import Workspace
from test_docx_export import CANDIDATE, PROJECT_BULLETS, evidence
from test_hunt import JD

PROFILE = {"country_pack": "ie", "target_markets": ["ie"],
           "work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "noncitizen",
                                                   "needs_sponsorship_later": "yes"}},
           "candidate": {**CANDIDATE, "timezone": "Europe/Dublin"},
           "target_roles": {"primary": ["Data Analyst"], "max_years_required": 3},
           "narrative": {"headline": "Data analyst with an MSc in Business Analytics"}}
GOOD = (
    "I am applying for the Data Analyst role at Acme Analytics. In my work as a Data Analyst at Example Retail Pvt Ltd "
    "I built 12 Power BI dashboards used by 40 store managers across Leinster, and I cut weekly reporting time by 30% "
    "with scheduled SQL automation, which matches your need for strong SQL for reporting queries.\n\n"
    "For my MSc at University College Dublin I forecast emergency department waits with an MAE of 7.3 minutes, and I "
    "compared ARIMA and gradient boosting on three years of hourly data before presenting the findings to two "
    "clinical leads at a Dublin hospital.\n\n"
    "Acme Analytics builds reporting software for hospitals, so the dashboards and forecasts I have built are close "
    "to the work your finance insights team does each week.\n\n"
    "I would welcome the chance to talk about how this work could help the team. Thank you for considering my "
    "application.")


def letter_profile(tmp_path) -> CareerServices:
    root = tmp_path / "profile"
    (root / "data/config").mkdir(parents=True)
    (root / "data/context").mkdir(parents=True)
    (root / "data/config/profile.yml").write_text(yaml.safe_dump(PROFILE, allow_unicode=True), encoding="utf-8")
    (root / "data/context/evidence.yml").write_text(yaml.safe_dump(evidence(), allow_unicode=True), encoding="utf-8")
    return CareerServices(Workspace(root))


def acme_dossier():
    claim = {"facet": "overview", "text": "Acme Analytics builds reporting software for hospitals.",
             "quote": "Acme Analytics builds reporting software for hospitals", "url": "https://acme.example/about",
             "published_at": "", "verified_on": "2026-10-04"}
    MarketStore().save_dossier("acme analytics", "standard", {"employer": "Acme Analytics", "employer_key": "acme analytics",
                                                             "depth": "standard", "created_at": "2026-10-04T09:00:00+00:00",
                                                             "facts": {}, "claims": [claim], "unverified": []})


class StubWriter:
    """The cover-letter writer and its auditor: returns the drafts given, in order, keeps every payload,
    and audits with the verdicts given (no unsupported sentence once they run out)."""
    served = ("kimi_cli", "kimi-runtime")

    def __init__(self, *drafts, audits=()):
        self.drafts, self.payloads, self.audits, self.audited = list(drafts), [], list(audits), []

    def run(self, name, payload):
        if name == "letter_auditor":
            self.audited.append(payload)
            verdict = self.audits.pop(0) if self.audits else {"unsupported": []}
            if isinstance(verdict, Exception):
                raise verdict
            return verdict
        assert name == "cover_letter_writer"
        self.payloads.append(payload)
        return self.drafts.pop(0)


def test_without_ai_the_letter_is_built_from_registered_sentences_with_the_ids_it_uses(tmp_path):
    services = letter_profile(tmp_path)
    job = services.w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/1", JD)
    letter = services.generate_cover_letter(job["id"])
    assert letter["method"] == "template" and letter["version"] == 1
    text = letter["content"]
    assert text.splitlines()[:4] == [CANDIDATE["full_name"], CANDIDATE["email"], CANDIDATE["phone"], CANDIDATE["linkedin"]]
    assert "Dear Hiring Team," in text and text.rstrip().endswith("Kind regards,\n" + CANDIDATE["full_name"])
    assert PROJECT_BULLETS[0] + "." in text  # the person's own registered sentence, word for word
    saved = json.loads((services.w.root / "data/output" / letter["path"].replace(".md", ".json")).read_text(encoding="utf-8"))
    known = {c["id"] for c in evidence()["claims"]} | {p["id"] for p in evidence()["projects"]}
    assert set(saved["evidence_ids"]) <= known and "PROJ-001" in saved["evidence_ids"]
    assert {"IDENTITY-001", "CONTACT-EMAIL-001", "CONTACT-PHONE-001", "CONTACT-LINKEDIN-001"} <= set(saved["evidence_ids"])
    assert "EDU-MS-001" not in saved["evidence_ids"] and "CONTACT-GITHUB-001" not in saved["evidence_ids"]
    assert (services.w.root / "data/output" / letter["docx_path"]).is_file()
    assert services.generate_cover_letter(job["id"])["version"] == 2


def test_an_ai_draft_that_passes_the_check_is_used_with_its_cited_company_fact(tmp_path):
    services = letter_profile(tmp_path)
    acme_dossier()
    job = services.w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/1", JD)
    writer = StubWriter({"body": GOOD, "evidence_ids": ["EMP-001", "PROJ-001", "EDU-MSC-001", "COMPANY-1"],
                         "unsupported_claims": []})
    letter = services.generate_cover_letter(job["id"], team=writer)
    assert letter["method"] == "ai" and letter["provider"] == "kimi_cli" and GOOD in letter["content"]
    assert letter["company_facts"] == [{"text": "Acme Analytics builds reporting software for hospitals.",
                                        "quote": "Acme Analytics builds reporting software for hospitals",
                                        "url": "https://acme.example/about"}]
    assert "COMPANY-1" not in letter["evidence_ids"] and {"EMP-001", "PROJ-001"} <= set(letter["evidence_ids"])
    sent = writer.payloads[0]
    assert sent["company_facts"] == [{"id": "COMPANY-1", "text": "Acme Analytics builds reporting software for hospitals."}]
    assert {e["id"] for e in sent["evidence"]} >= {"EMP-001", "PROJ-001"} and "visas" in sent["rules"]


def test_a_draft_with_invented_facts_is_sent_back_once_then_replaced_by_the_template(tmp_path):
    services = letter_profile(tmp_path)
    job = services.w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/1", JD)
    invented = GOOD.replace("30%", "45%").replace("Example Retail Pvt Ltd", "Google") + "\n\nI hold Stamp 1G permission."
    bad = {"body": invented, "evidence_ids": ["EMP-001", "EDU-MS-001"], "unsupported_claims": []}
    writer = StubWriter(bad, bad)
    letter = services.generate_cover_letter(job["id"], team=writer)
    assert letter["method"] == "template" and "set aside" in letter["note"]
    problem = writer.payloads[1]["previous_attempt_problem"]
    assert "EDU-MS-001" in problem and "45" in problem and "Google" in problem and "immigration" in problem
    assert "Google" not in letter["content"] and "Stamp" not in letter["content"]


def test_the_auditor_sends_back_a_sentence_the_evidence_does_not_state(tmp_path):
    services = letter_profile(tmp_path)
    job = services.w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/1", JD)
    embellished = GOOD.replace("before presenting the findings", "on real patient data before presenting the findings")
    first = {"body": embellished, "evidence_ids": ["EMP-001", "PROJ-001", "EDU-MSC-001"], "unsupported_claims": []}
    second = {"body": GOOD, "evidence_ids": ["EMP-001", "PROJ-001", "EDU-MSC-001"], "unsupported_claims": []}
    flagged = {"unsupported": [{"sentence": "I compared ARIMA and gradient boosting on real patient data",
                                "why": "the evidence does not say the data was real patient data"}]}
    writer = StubWriter(first, second, audits=[flagged])
    letter = services.generate_cover_letter(job["id"], team=writer)
    assert letter["method"] == "ai" and "real patient data" not in letter["content"]
    assert "real patient data" in writer.payloads[1]["previous_attempt_problem"]
    assert "corrected draft passed every check" in letter["note"] and "registered sentences" not in letter["note"]
    sent = writer.audited[0]
    assert sent["letter"] == embellished.strip() and {e["id"] for e in sent["evidence"]} >= {"EMP-001", "PROJ-001"}


def test_a_draft_whose_audit_cannot_run_is_never_used(tmp_path):
    services = letter_profile(tmp_path)
    job = services.w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/1", JD)
    draft = {"body": GOOD, "evidence_ids": ["EMP-001", "PROJ-001", "EDU-MSC-001"], "unsupported_claims": []}
    failing = StubWriter(draft, audits=[RuntimeError("usage limit"), RuntimeError("usage limit")])
    letter = services.generate_cover_letter(job["id"], team=failing)
    assert letter["method"] == "template" and "claim check could not run" in letter["note"]
    assert GOOD not in letter["content"] and len(failing.audited) == 2
    # One crash of the AI app is tried again; the draft is used once an audit has run and passed.
    again = services.generate_cover_letter(job["id"], team=StubWriter(dict(draft), audits=[RuntimeError("exit 3221226505")]))
    assert again["method"] == "ai" and GOOD in again["content"]


def test_a_graduate_with_one_registered_project_still_gets_a_template_letter(tmp_path):
    services = letter_profile(tmp_path)
    data = evidence()
    data["claims"] = [c for c in data["claims"] if c.get("category") != "employment"]
    data["projects"] = data["projects"][:1]
    (services.w.root / "data/context/evidence.yml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    job = services.w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/1", JD)
    letter = services.generate_cover_letter(job["id"])
    assert letter["method"] == "template" and PROJECT_BULLETS[0] + "." in letter["content"]
    assert "I can also point to" not in letter["content"]


def test_a_list_of_known_names_is_not_a_stranger():
    everything = cover_letters._plain("Python, SQL, Git. Coursework: Databases, Software Engineering")
    assert cover_letters._known("Python and SQL", everything)
    assert cover_letters._known("Databases and Software Engineering", everything)
    assert not cover_letters._known("Python and Snowflake", everything)
    assert not cover_letters._known("Bank of Ireland", everything)


def test_the_check_names_each_problem():
    job = {"company": "Acme Analytics", "title": "Data Analyst", "location": "Dublin", "description": "Build SQL reports."}
    cat = {"entries": [{"id": "EMP-001", "kind": "experience", "name": "Example Retail",
                        "text": "Data Analyst at Example Retail: Built 12 Power BI dashboards for 40 managers"},
                       {"id": "SKILL-001", "kind": "skill", "name": "", "text": "SQL, Power BI"}],
           "never": ["Tableau"], "hash": "x"}
    matrix = {"requirements": [{"text": "Apache Kafka", "category": "required", "status": "missing", "evidence_ids": []}]}
    body = ("I am applying for the Data Analyst role at Acme Analytics. At Example Retail I built 12 Power BI dashboards "
            "for 40 managers, using SQL to prepare each one, and I kept them current every week for the team. ") * 2
    ok = {"body": "\n\n".join([body, body, "Thank you for considering my application; I would welcome a conversation."]),
          "evidence_ids": ["EMP-001", "SKILL-001"], "unsupported_claims": []}
    assert cover_letters.check(ok, job=job, cat=cat, matrix=matrix, facts=[]) == []

    def problems(**change):
        return " | ".join(cover_letters.check({**ok, **change}, job=job, cat=cat, matrix=matrix, facts=[]))

    assert "not registered: EDU-9" in problems(evidence_ids=["EMP-001", "EDU-9"])
    assert "none of the person's registered evidence" in problems(evidence_ids=[])
    assert "numbers" in problems(body=ok["body"].replace("12 Power", "15 Power"))
    assert "names that are not in the evidence" in problems(body=ok["body"].replace("using SQL", "using Snowflake"))
    assert "never claims: Tableau" in problems(body=ok["body"] + " I also use Tableau.")
    assert "requirements the evidence does not show: Apache Kafka" in problems(body=ok["body"] + " I know Kafka.")
    assert "immigration" in problems(body=ok["body"] + " I will need a work permit.")
    assert "filler" in problems(body=ok["body"] + " I am passionate about data.")
    assert "app's words" in problems(body=ok["body"] + " My registered skills are SQL and Power BI.")
    assert "app's words" in problems(body=ok["body"] + " My evidence shows strong SQL.")
    assert "paragraphs" in problems(body=body)
    assert "does not support" in problems(unsupported_claims=["led a team"])


def test_the_letter_downloads_as_word_and_markdown(tmp_path, monkeypatch):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    pid = store.create("Example Person")["id"]
    root = store.root_for(pid)
    (root / "data/config/profile.yml").write_text(yaml.safe_dump(PROFILE, allow_unicode=True), encoding="utf-8")
    (root / "data/context/evidence.yml").write_text(yaml.safe_dump(evidence(), allow_unicode=True), encoding="utf-8")
    store.update(pid, state="ready")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    monkeypatch.setattr(backend.profiles, "store", lambda: store)
    client, base = TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1"), f"/p/{pid}/api/v2"
    with client:
        saved = client.post(base + "/jobs", json={"company": "Acme Analytics", "title": "Data Analyst", "location": "Dublin, Ireland",
                                                  "url": "https://jobs.example/acme/1", "description": JD}).json()
        job = saved["job"]
        assert client.get(base + f"/jobs/{job['id']}/cover-letter/download").status_code == 400
        letter = client.post(base + f"/jobs/{job['id']}/cover-letter").json()
        assert letter["method"] == "template" and letter["docx_path"].endswith("cover-letter-v1.docx")
        word = client.get(base + f"/jobs/{job['id']}/cover-letter/download", params={"format": "docx"})
        assert word.status_code == 200 and word.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert "acme-analytics-data-analyst-cover-letter-v1.docx" in word.headers["content-disposition"]
        markdown = client.get(base + f"/jobs/{job['id']}/cover-letter/download", params={"format": "md"})
        assert markdown.status_code == 200 and PROJECT_BULLETS[0] in markdown.text
        monkeypatch.setenv("CAREER_FEATURES", "-docx_export")
        assert client.get(base + f"/jobs/{job['id']}/cover-letter/download", params={"format": "docx"}).status_code == 400
