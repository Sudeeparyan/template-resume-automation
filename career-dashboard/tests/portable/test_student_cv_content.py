"""An international graduate's CV keeps what recruiters screen for: the main skills, the
city, the degree results and the certifications, all from the person's own documents."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from backend.countries import load_pack
from backend.job_quality import JobQualityService
from backend.pdf_compiler import tectonic_executable
from backend.services.intake.build import already_listed, file_set, skill_entry
from backend.services.intake.coverage import ledger
from backend.services.intake.resume_base import render
from backend.services.resume_studio import ResumeStudio

APP = Path(__file__).resolve().parents[2]
REFS = ["S001"]


def student_draft(*, roles=None, skills=None) -> dict:
    return {
        "contact": {"full_name": "Example Graduate", "email": "graduate@example.org", "phone": "PHONE-PLACEHOLDER",
                    "github": "github.com/example-graduate", "city": "Dublin", "country": "Ireland", "refs": REFS},
        "authorization": {"work_country": "Ireland", "status": "Stamp 1G", "valid_until": "2027-09-24",
                          "citizenship": "noncitizen", "needs_sponsorship_later": "yes", "refs": REFS},
        "target_markets": ["ie"], "country_pack": "ie",
        "targets": {"roles": ["Data Analyst"], "cities": ["Dublin"], "refs": REFS},
        "job_search": {"graduate_search_confirmed": True},
        "education": [
            {"institution": "Example Institute", "degree": "MSc in Data Analytics", "location": "Dublin, Ireland",
             "start": "September 2025", "end": "August 2026", "grade": "2:1",
             "coursework": ["Machine Learning", "Statistics"], "refs": REFS},
            {"institution": "Example College", "degree": "BTech in Computer Science", "location": "Hyderabad, India",
             "start": "2017", "end": "2021", "grade": "CGPA 8.1 / 10", "refs": REFS},
        ],
        "experience": roles if roles is not None else [
            {"employer": "Example Shop", "title": "Retail Assistant", "location": "Dublin, Ireland",
             "start": "October 2025", "end": "present",
             "bullets": ["Serve customers at the till and restock shelves from delivery notes."], "refs": REFS},
            {"employer": "Example Analytics", "title": "Data Analyst", "location": "Bengaluru, India",
             "start": "July 2021", "end": "July 2023", "tools": ["Python (pandas)", "AWS EC2", "XGBoost"],
             "bullets": ["Built 12 Power BI dashboards that the sales team used every week.",
                         "Automated the weekly sales report with SQL and Python, cutting it from 6 hours to 1 hour.",
                         "Removed 15% duplicate records from a table of about 2 million customers.",
                         "Defined KPIs with two product managers for fortnightly business reviews.",
                         "Wrote data-quality checks in SQL that flagged missing store codes nightly."], "refs": REFS},
        ],
        "skills": skills if skills is not None else [
            {"name": "Languages and libraries", "level": "used", "refs": REFS,
             "skills": ["Python (pandas, NumPy, scikit-learn, Matplotlib)", "SQL (PostgreSQL, MySQL)", "R (basic)",
                        "I enjoy building dashboards for the operations team every week"]},
            {"name": "BI and reporting", "level": "used", "skills": ["Power BI (DAX)", "Tableau", "Excel"], "refs": REFS},
            {"name": "Cloud", "level": "used", "skills": ["AWS (EC2, S3)", "Apache Airflow", "Git"], "refs": REFS},
            {"name": "Tools", "skills": ["Jira", "Confluence"], "refs": REFS},
            {"name": "Statistics", "skills": ["hypothesis testing", "regression", "A/B test analysis"], "refs": REFS},
        ],
        "projects": [
            {"name": f"Example project {n}", "kind": "academic", "period": "2026", "tools": ["Python"], "refs": REFS,
             "facts": [f"Trained a model in Python on {n},043 public records and reached an AUC of 0.8{n}.",
                       f"Explained the drivers of project {n} with SHAP values for a retention team."]}
            for n in (1, 2)
        ],
        "certifications": [{"name": "Microsoft Certified: Power BI Data Analyst Associate (PL-300)",
                            "issuer": "Microsoft", "date": "2023", "refs": REFS}],
        "statements": [], "questions": [], "interview_answers": [],
    }


def build(tmp_path, draft=None) -> tuple[Path, dict, dict, str]:
    draft = draft or student_draft()
    blocks = [{"id": "S001", "source": "cv.md", "text": json.dumps(draft)}]
    files = file_set(draft, blocks, ledger(blocks, draft), load_pack("ie"), ["cv.md"], "2026-10-08")
    root = tmp_path / "profile"
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    profile = yaml.safe_load(files["data/config/profile.yml"])
    evidence = yaml.safe_load(files["data/context/evidence.yml"])
    return root, profile, evidence, files["data/templates/resume-base.tex"]


def skills_of(evidence, claim_id):
    return next(c for c in evidence["claims"] if c["id"] == claim_id)["approved_facts"]


def test_a_named_tool_keeps_its_parts_and_a_sentence_is_not_a_skill(tmp_path):
    assert skill_entry("Python (pandas, NumPy, scikit-learn, Matplotlib)")
    assert not skill_entry("I enjoy building dashboards for the operations team every week")
    _, _, evidence, tex = build(tmp_path)
    languages = skills_of(evidence, "SKILL-LANGUAGES-001")
    assert languages[0] == "Python (pandas, NumPy, scikit-learn, Matplotlib)"
    assert not any("enjoy" in s for s in languages)
    assert "Python (pandas, NumPy, scikit-learn, Matplotlib)" in tex


def test_other_tools_leave_out_what_a_skills_line_already_names(tmp_path):
    assert already_listed("Python (pandas)", {"python (pandas, numpy)"})
    assert already_listed("AWS EC2", {"aws (ec2, s3)"})
    assert not already_listed("PySpark", {"spark"})
    _, _, evidence, _ = build(tmp_path)
    assert skills_of(evidence, "SKILL-TOOLS-USED-001") == ["XGBoost"]


def test_the_irish_cv_shows_the_city_the_results_and_the_certifications(tmp_path):
    _, profile, evidence, tex = build(tmp_path)
    contract = profile["resume_contract"]
    assert contract["header_fields"][:2] == ["full_name", "location"]
    assert "City" not in contract["omitted_by_default"]
    assert contract["required_sections"][-1] == "Certifications"
    assert "City and country" in next(c for c in evidence["claims"] if c["id"] == "LOCATION-001")["approved_external_use"]
    contact = re.search(r"% EVIDENCE: ([^\n]+)\n\\newcommand\{\\ResumeContact\}\{([^\n]+)\}", tex)
    assert "LOCATION-001" in contact[1].split() and contact[2].startswith("Dublin, Ireland")
    assert "\\newcommand{\\GradeOne}{2:1}" in tex and "\\textbf{Result:} \\GradeOne\\\\" in tex
    assert "\\textbf{Result:} \\GradeTwo\n" in tex  # no coursework under the second degree, so no line break
    assert "\\section{Certifications}" in tex
    assert "\\newcommand{\\CertificationOne}{Microsoft Certified: Power BI Data Analyst Associate (PL-300), Microsoft (2023)}" in tex


def test_a_short_part_time_role_leaves_its_room_to_the_analyst_role(tmp_path):
    _, _, _, tex = build(tmp_path)
    experience = tex.split("\\section{Professional Experience}")[1].split("\\section{")[0]
    analyst = experience.split("Data Analyst")[1]
    assert analyst.count("\\item ") == 5


def test_the_fifth_skill_line_is_the_first_cut_when_the_page_is_full(tmp_path):
    _, profile, evidence, tex = build(tmp_path)

    def skill_lines(source):
        body = source.split("\\section{Skills}")[1].split("\\section{")[0]
        return len(re.findall(r"\\textbf\{[^}]+:\}", body))

    def bullets(source):
        return source.split("\\section{Professional Experience}")[1].split("\\section{")[0].count("\\item ")

    assert skill_lines(tex) == 5 and "Statistics" in tex.split("\\section{Skills}")[1].split("\\section{")[0]
    first_cut = render(profile, evidence, 1)
    assert skill_lines(first_cut) == 4 and bullets(first_cut) == bullets(tex)
    assert bullets(render(profile, evidence, 2)) < bullets(tex)


def test_dropping_the_second_degree_takes_its_result_line_along(tmp_path):
    _, _, _, tex = build(tmp_path)
    cut = ResumeStudio._drop_second_degree(tex)
    education = cut.split("\\section{Education}")[1].split("\\section{")[0]
    assert "Example College" not in education and "\\GradeTwo" not in education
    assert "\\textbf{Result:} \\GradeOne" in education


@pytest.mark.parametrize("title, blocked", [
    ("Data Analyst Intern", True),
    ("Information Systems & Data Analytics Co-Op Placements 2027", True),
    ("Software Engineering & Data Science Internship 2026", True),
    ("IT Graduate Development Programme 2027 - Data & Analytics", False),
    ("Data Analyst", False),
])
def test_internships_are_left_out_of_a_graduate_search(tmp_path, title, blocked):
    root, _, _, _ = build(tmp_path)
    quality = JobQualityService(SimpleNamespace(w=SimpleNamespace(root=root)))
    posting = {"title": title, "location": "Dublin, Ireland", "url": "https://jobs.example.org/1",
               "description": "Analyse data with SQL and Python and build Power BI reports for the business. " * 3}
    reasons = quality.blockers(posting)
    assert any("internship" in reason for reason in reasons) is blocked


def test_someone_who_asks_for_internships_still_gets_them(tmp_path):
    root, _, _, _ = build(tmp_path)
    path = root / "data/config/profile.yml"
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    profile["target_roles"]["seniority"] = ["internship", "graduate"]
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    quality = JobQualityService(SimpleNamespace(w=SimpleNamespace(root=root)))
    posting = {"title": "Data Analyst Intern", "location": "Dublin, Ireland", "url": "https://jobs.example.org/2",
               "description": "Analyse data with SQL and Python and build Power BI reports for the business. " * 3}
    assert not any("internship" in reason for reason in quality.blockers(posting))


@pytest.mark.skipif(not tectonic_executable(), reason="Tectonic is not installed")
def test_the_base_cv_compiles_to_one_a4_page_and_passes_the_resume_check(tmp_path):
    root, _, _, _ = build(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    run = subprocess.run([sys.executable, str(APP / "backend/scripts/validate_resume.py"),
                          str(root / "data/templates/resume-base.tex"), "--workspace", str(root),
                          "--qa-json", str(out / "qa.json"), "--compile", "--output", str(out / "cv.pdf")],
                         capture_output=True, text=True, timeout=240)
    qa = json.loads((out / "qa.json").read_text(encoding="utf-8"))
    assert qa["status"] != "FAIL", qa.get("failures") or run.stdout[-1500:]
    assert qa["page_count"] == 1
