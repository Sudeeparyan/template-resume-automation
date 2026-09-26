"""A second profile built from a person's documents: extraction, the file set, and the
workspace it boots into — Ireland rules, their own persona, nothing of Chetan's."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from backend.countries import load_pack, pack_for  # noqa: E402
from backend.services.intake import build as intake_build  # noqa: E402
from backend.services.intake.coverage import ledger  # noqa: E402
from backend.services.intake.extract import blocks_for, chunks  # noqa: E402
from backend.services.intake.merge import merge  # noqa: E402
from fixtures.intake_draft import blocks as fixture_blocks, section  # noqa: E402

EXAMPLE = ROOT.parent / "example_profile" / "About Me-1.docx"


def built_profile(root: Path) -> Path:
    """Write the Ireland profile's whole file set into `root`, as the Build button does."""
    blocks = fixture_blocks()
    draft = merge([section()], [{"missed": [{"text": "Presented a seminar on Kotlin", "refs": ["P999"], "category": "other"}]}])
    draft["coverage"] = ledger(blocks, draft)
    draft["country_pack"] = "ie"
    files = intake_build.file_set(draft, blocks, draft["coverage"], load_pack("ie"), ["About Me-1.docx"], "2026-09-23")
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def ireland(tmp_path):
    return built_profile(tmp_path / "profiles" / "srikanth")


@pytest.mark.skipif(not EXAMPLE.exists(), reason="example document not present")
def test_the_example_document_is_read_in_order_with_its_tables():
    blocks = blocks_for([(EXAMPLE, EXAMPLE.name)])
    kinds = [b.kind for b in blocks]
    assert blocks[0].text == "Srikanth Nadesharam" and kinds.count("table") == 4
    assert [b.id for b in blocks] == [f"P{n:03d}" for n in range(1, len(blocks) + 1)]
    text = "\n".join(b.text for b in blocks)
    for fact in ("Stamp 1G", "CGPA of 7.41", "157,824", "JRB Infotech", "Cisco Networking Academy", "18,751.93"):
        assert fact in text
    groups = chunks(blocks)
    assert sum(len(g) for g in groups) == len(blocks) and all(sum(len(b.text) for b in g) <= 12000 for g in groups if len(g) > 1)


def test_merge_joins_repeats_and_asks_about_conflicts():
    first, second = section(), section()
    second["experience"][0]["bullets"] = ["Created interactive Power BI dashboards on a star schema to monitor banking KPIs for stakeholders",
                                          "Presented findings to non-technical managers"]
    second["experience"][0]["start"] = "July 2022"
    second["projects"] = [{"name": "Dublin Hourly Cycling Demand Forecasting", "facts": ["Documented deployment limitations"], "refs": ["P083"]}]
    draft = merge([first, second])
    assert len(draft["experience"]) == 1 and "Presented findings to non-technical managers" in draft["experience"][0]["bullets"]
    assert len(draft["experience"][0]["bullets"]) == 5  # the repeated dashboard bullet is not duplicated
    assert len(draft["projects"]) == 3 and "Documented deployment limitations" in draft["projects"][1]["facts"]
    assert any("June 2022" in q and "July 2022" in q for q in draft["questions"])


def test_merge_asks_only_what_the_documents_leave_open():
    from backend.services.intake.merge import _refined

    # The same date said more precisely is not a conflict; different dates, or "Present", are.
    assert _refined("2023", "August 2023") == "August 2023"
    assert _refined("January 2026", "Recently completed") == "January 2026"
    assert _refined("completed in 2026", "Issued June 11, 2026") == "Issued June 11, 2026"
    assert _refined("DEC2027", "DEC2028") is None and _refined("June 2022", "July 2022") is None
    assert _refined("Present", "September 2024") is None

    first = section()
    first["education"] = [{"degree": "Bachelor of Technology", "institution": "JNTU", "field": "CSE", "start": "2019",
                           "end": "2023", "grade": "7.41", "refs": ["P008"]},
                          {"degree": "", "institution": "", "coursework": ["Operating Systems: paging", "Computer Networks"],
                           "refs": ["P020"]}]
    first["questions"] = ["What were the institution, field of study, dates, and grade for the Bachelor's degree? They are not stated in this section.",
                          "Was JRB your employer, and what was your job title and the start and end dates for this role?",
                          "Are the JRB Infotech dates and part-time/full-time arrangement accurate?",
                          "What was the exact month in which you switched to full-time work at JRB Infotech?"]
    second = section()
    second["education"] = [{"degree": "Bachelor's degree", "institution": "", "end": "August 2023", "refs": ["P030"]}]
    draft = merge([first, second])
    questions = draft["questions"]
    # Another section gave the degree's and the role's details: those two are not asked again (but are kept).
    assert not any("Bachelor's degree?" in q or "Was JRB your employer" in q for q in questions)
    assert len(draft["resolved_questions"]) == 2
    # A check and a genuinely open detail stay; "2023" and "August 2023" are one date.
    assert any(q.startswith("Are the JRB Infotech dates") for q in questions)
    assert any("exact month" in q for q in questions)
    assert not any("the documents give both" in q and "August 2023" in q for q in questions)
    bachelor = next(e for e in draft["education"] if e["institution"] == "JNTU")
    assert bachelor["end"] == "August 2023"
    # Modules without their programme are kept, labelled, and asked about.
    unplaced = next(e for e in draft["education"] if e.get("unplaced"))
    assert unplaced["degree"] == "Coursework (programme not stated)" and "Computer Networks" in unplaced["coursework"]
    assert any("Operating Systems, Computer Networks" in q for q in questions)


def test_resume_lines_lead_with_results_and_never_repeat():
    from backend.services.intake.resume_base import pick, role_limit

    facts = ["Integrated hourly observations by matching each counter to the nearest traffic sensor",
             "Lag features were 1-, 24-, and 168-hour",
             "The selected model achieved MAE 7.28 on the untouched test set",
             "The selected model achieved RMSE 12.24 on the untouched test set",
             "The validation-selected model achieved an MAE of 7.28, RMSE of 12.24 and R² of 0.968 on the test period",
             "After tuning, the text says the model matched the baseline at 64.2%"]
    chosen = pick(facts, 3)
    # The richest result and what was done come first; never the same numbers twice, never the extraction's own voice.
    assert chosen == [facts[0], facts[1], facts[4]]  # in the person's own order
    assert role_limit(1, 0) == 7 and role_limit(2, 1) == 3 and role_limit(5, 4) == 1 and role_limit(1, 0, trim=3) == 4


def test_the_intake_state_never_flickers_while_it_is_rewritten(tmp_path):
    import threading

    from backend.services.intake.job import IntakeJob

    job = IntakeJob(tmp_path)
    (tmp_path / "data/context/files").mkdir(parents=True)
    (tmp_path / "data/context/files/About Me.md").write_text("About me", encoding="utf-8")
    job._save(state="reading")
    stop = threading.Event()

    def analysis():  # the thread's progress writes, as fast as they can come
        n = 0
        while not stop.is_set():
            job._step(f"step {n % 3}", "running", str(n))
            n += 1

    writer = threading.Thread(target=analysis)
    writer.start()
    try:
        seen = {job.state()["state"] for _ in range(100)}  # the page polling meanwhile
    finally:
        stop.set()
        writer.join()
    assert seen == {"reading"}


def test_coverage_accounts_for_every_block_and_keeps_the_rest_verbatim():
    blocks = fixture_blocks() + [{"id": "P777", "kind": "paragraph", "text": "An unmentioned line.", "source": "About Me-1.docx"}]
    draft = merge([section()])
    coverage = ledger(blocks, draft)
    assert coverage["accounted"] == coverage["blocks"] == len(blocks)
    assert "P777" in coverage["verbatim"] and "P005" not in coverage["verbatim"]


def test_the_file_set_boots_into_an_ireland_workspace(ireland):
    from career import Workspace
    from backend.services.workspace_v2 import CareerServices

    profile = yaml.safe_load((ireland / "data/config/profile.yml").read_text(encoding="utf-8"))
    assert profile["country_pack"] == "ie" and profile["resume_contract"]["paper"] == "a4"
    assert profile["candidate"]["timezone"] == "Europe/Dublin"
    assert profile["location_preferences"]["country"] == "Ireland"
    service = CareerServices(Workspace(ireland))
    ids = {item["id"] for item in service.knowledge()}
    assert {"IDENTITY-001", "WORKAUTH-001", "SKILL-TOOLS-USED-001"} <= ids
    assert any(i.startswith("EXP-JRB-INFOTECH") for i in ids) and any(i.startswith("PROJ-P01-") for i in ids)
    assert service.w.timezone == "Europe/Dublin" and pack_for(ireland).code == "ie"
    # Nothing of Chetan's came along.
    everything = "\n".join(p.read_text(encoding="utf-8") for p in ireland.rglob("*") if p.is_file() and p.suffix in {".yml", ".md", ".tex"})
    for chetan in ("Chetan", "Manoharan", "InsOps", "F-1", "H-1B", "Soliton", "Fayetteville"):
        assert chetan not in everything, chetan


def test_the_evidence_keeps_every_fact_and_number(ireland):
    evidence = yaml.safe_load((ireland / "data/context/evidence.yml").read_text(encoding="utf-8"))
    text = json.dumps(evidence, ensure_ascii=False)
    for fact in ("50,000 banking records", "95 percent", "R² 0.968", "CGPA 7.41", "Stamp 1G", "DEC2027", "Cisco Networking Academy",
                 "9.2 GPA", "Presented a seminar on Kotlin"):
        assert fact in text, fact
    education = [c for c in evidence["claims"] if c["category"] == "education"]
    assert education[0]["institution"] == "Munster Technological University"  # newest first
    assert education[0]["dates"] == "Sep 2024 - Jan 2026"
    projects = {p["canonical_name"]: p for p in evidence["projects"]}
    assert "resume_content" in projects["AMCS Waste Operations Analytics & Forecasting"]
    assert "resume_content" not in projects["Local LLM & RAG System"]  # one sentence: kept off resumes
    assert all(r.startswith("data/context/sources/About Me-1.md#P") for r in projects["Local LLM & RAG System"]["source_refs"])


def test_the_base_resume_passes_the_static_contract(ireland):
    from backend.resume_contract import contract_for

    contract = contract_for(ireland)
    assert contract.paper == "a4" and "certification (none on record)" not in contract.unsafe_patterns
    assert "GPA (not on record)" not in contract.unsafe_patterns
    run = subprocess.run([sys.executable, str(ROOT / "backend/scripts/validate_resume.py"),
                          str(ireland / "data/templates/resume-base.tex"), "--workspace", str(ireland),
                          "--qa-json", str(ireland / "qa.json")], capture_output=True, text=True, timeout=120)
    qa = json.loads((ireland / "qa.json").read_text(encoding="utf-8"))
    assert qa["failures"] == [], run.stdout + run.stderr
    source = (ireland / "data/templates/resume-base.tex").read_text(encoding="utf-8")
    assert "\\documentclass[a4paper,10pt]{article}" in source and "SECOND_PROJECT_BLOCK_START" in source


def test_their_agents_speak_of_them_in_their_country(ireland):
    from backend.ai.agents.specialists import RESUME_TAILOR, WORKSPACE_AGENT
    from backend.ai.persona import persona_for

    persona = persona_for(ireland)
    agent = WORKSPACE_AGENT.system_for(persona)
    assert "Srikanth" in agent and "one A4 page" in agent and "Stamp 1G" in agent and "${" not in agent
    for word in ("Chetan", "F-1", "US Letter", " she ", " her "):
        assert word not in agent, word
    assert "modelling" in RESUME_TAILOR.system_for(persona) and "modeling" not in RESUME_TAILOR.system_for(persona)
    discovery = (ireland / "data/config/guides/job-discovery.md").read_text(encoding="utf-8")
    assert "jobs in Ireland" in discovery and "core.cro.ie" in discovery and "${" not in discovery
    assert "Data Analyst; Data Scientist; Machine Learning Engineer" in discovery
    study = (ireland / "data/config/guides/study-planner.md").read_text(encoding="utf-8")
    assert " she " not in study and " her " not in study


def test_the_backup_profile_keeps_its_original_prompts(tmp_path):
    from backend.ai.agents.specialists import RESUME_TAILOR, WORKSPACE_AGENT
    from backend.ai.persona import persona_for

    assert persona_for(ROOT) is None
    assert WORKSPACE_AGENT.system_for(None) is WORKSPACE_AGENT.system
    assert "one candidate, Chetan, on F-1 OPT" in WORKSPACE_AGENT.system
    assert "US English spelling (modeling, specializing, analyze)" in RESUME_TAILOR.system_for(None)


def test_the_agents_page_speaks_each_profiles_country(ireland):
    from backend.countries import load_pack
    from backend.services.workspace_v2 import AGENT_SWITCHES, AGENTS, agent_switches_for, agents_for

    # The backup profile's list is the original, unchanged.
    assert agents_for(ROOT) is AGENTS and agent_switches_for(ROOT) is AGENT_SWITCHES
    text = json.dumps([agents_for(ireland), agent_switches_for(ireland)])
    for us_only in ("H-1B", "USCIS", "US Letter", "US postings", "cap-exempt", "7am"):
        assert us_only not in text, us_only
    assert "Irish postings" in text and "one-page A4 layout" in text and "Stamp 4 or Stamp 5" in text
    # The page's market carries the country's own tier wording; the US keeps the page's wording.
    assert "work permits" in load_pack("ie").tier_labels["C"] and load_pack("us").tier_labels == {}
    from backend.services.pipeline import SOURCES, sources_for

    assert sources_for(ROOT) is SOURCES
    sources = json.dumps(sources_for(ireland))
    assert "fresh Irish postings" in sources and "sponsorship record" not in sources


def test_a_balanced_mix_in_ireland_needs_no_sponsor_record(ireland):
    from career import Workspace
    from backend.job_quality import JobQualityService
    from backend.services.workspace_v2 import CareerServices

    quality = JobQualityService(CareerServices(Workspace(ireland)))
    # Ireland has no sponsor index, so a silent (tier C) large employer counts; a refusal never got this far.
    job = {"company": "l1", "size_category": "large", "sponsor_tier": "C", "legitimacy_state": "verified",
           "relevance": {"eligible": True}}
    assert [j["company"] for j in quality.balanced_five([job], total=1)["jobs"]] == ["l1"]


def test_the_ireland_gate_and_location_screen(ireland):
    from career import Workspace
    from backend.job_quality import JobQualityService
    from backend.services.sponsorship import rules_for, screen
    from backend.services.workspace_v2 import CareerServices

    rules = rules_for(ireland)
    assert screen("This role is open to EU/EEA citizens only.", rules).verdict == "EXCLUDED"
    assert screen("We are unable to sponsor employment permits.", rules).verdict == "EXCLUDED"
    assert screen("Applicants must be eligible to work in Ireland.", rules).verdict == "KEEP"
    assert screen("All hires are subject to Garda vetting.", rules).verdict == "KEEP"
    assert screen("Stamp 1G holders are welcome to apply.", rules).reason == "explicit_sponsorship"
    # A live search's own note (24 Sep): it could not see the wording, and its example is not the posting's.
    note = ("Eligibility wording: the public summary pages I could access during this pass describe the programme in detail "
            "but, in the text available, do not show the precise sentence regarding work permits or sponsorship (for example, "
            "whether applicants must already have the right to work in Ireland “now and in future” without sponsorship).")
    assert screen(note, rules).verdict == "KEEP"
    assert screen("I could not find any wording about employment permits on the careers page.", rules).verdict == "KEEP"
    assert screen("Applicants must have the right to work in Ireland now and in the future without sponsorship.", rules).verdict == "EXCLUDED"
    assert screen("Unfortunately we are unable to sponsor visas or employment permits for this role.", rules).verdict == "EXCLUDED"
    quality = JobQualityService(CareerServices(Workspace(ireland)))
    jd = "Graduate Data Analyst using SQL, Python and Power BI dashboards for banking KPIs. " * 3
    cork = quality.relevance({"title": "Graduate Data Analyst", "location": "Cork, Ireland", "description": jd, "url": "https://x.ie/1"})
    austin = quality.relevance({"title": "Graduate Data Analyst", "location": "Austin, TX", "description": jd, "url": "https://x.com/1"})
    assert not any("Ireland" in b for b in cork["blockers"]) and cork["components"]["location"] == 10  # fit.LOCATION_POINTS
    assert any("Only roles in Ireland" in b for b in austin["blockers"])
    verdict = CareerServices(Workspace(ireland)).gate("Acme", "We will sponsor a Critical Skills Employment Permit.")
    assert verdict.tier == "A" and "work permits" in verdict.label()
