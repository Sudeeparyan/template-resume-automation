"""The one-page US Letter contract: read from profile.yml + evidence.yml, enforced by the validator."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from backend.resume_contract import load_contract
from backend.services.resume_layout import set_density, validate_density
from test_career_workspace import workspace  # noqa: F401 - fixture

NAME = "Chetan Babu M"
INSOPS_BULLET = "Built data workflows integrating SQL Server, Azure SQL, AWS S3, and cloud databases"


def test_contract_is_one_letter_page_at_10_to_11pt():
    c = load_contract()
    assert (c.paper, c.pages) == ("letter", 1)
    assert c.page_size_pt() == (612.0, 792.0)
    assert c.is_paper(612, 792) and not c.is_paper(595.28, 841.89)
    assert (c.min_body_pt, c.max_body_pt) == (10.0, 11.0)
    assert c.preview_files == ["page-01.png"]
    assert c.pdf_author == NAME and c.header_values()[0] == NAME
    assert c.describe_pages() == "one page"
    # Experience first for tracks A/C, Projects first for B/D, and nothing else.
    assert set(c.allowed_section_orders) == {
        ("Education", "Technical Skills", "Professional Experience", "Projects"),
        ("Education", "Technical Skills", "Projects", "Professional Experience"),
    }
    assert [step.split()[0] for step in c.cut_order] == ["Third", "Last", "Coursework", "Second"]
    assert "study-plan.md" in c.required_artifacts and "resume-preview/page-01.png" in c.required_artifacts
    assert "resume-preview/page-02.png" not in c.required_artifacts


def test_stable_facts_come_from_the_registry():
    c = load_contract()
    for value in (NAME, "chetanprm0501@gmail.com", "+1 (479) 301-1366", "University of Arkansas",
                  "InsOps Inc.", "Jul 2024 - Present", "Soliton Technologies", "Jun 2023 - Jun 2024"):
        assert value in c.visible_invariants, value
    assert "Jul 2024 -- Present" in c.required_source_values
    assert "pdfauthor={" + NAME + "}" in c.required_source_values
    # Soliton has two roles: either registered title/date line satisfies the pairing check.
    soliton = c.conditional_visible_pairs["Soliton Technologies"]
    assert "Project Engineer Jun 2023 - Jun 2024" in soliton and "Engineering Intern Jul 2022 - May 2023" in soliton
    # Held and missing claims contribute nothing.
    assert not any("linkedin" in value.lower() for value in c.visible_invariants)
    assert {"94", "100"} <= c.allowed_visible_numbers


@pytest.mark.parametrize("text", [
    "3+ years of experience in data engineering",
    "2 years professional experience",
    "Published at ICCV 2025",
    "first author on a workshop paper",
    "GPA 3.9",
    "AWS Certified Cloud Practitioner",
    "linkedin.com/in/chetan",
    "Deployed services on Kubernetes",
    "passionate about data",
    "Spearheaded the migration",
    "InsOpsAI litigation mitigation models",
])
def test_open_questions_and_never_claims_are_unsafe(text):
    patterns = load_contract().unsafe_patterns
    assert any(re.search(pattern, text) for pattern in patterns.values()), text


def test_real_resume_wording_is_not_flagged():
    patterns = load_contract().unsafe_patterns
    for text in ("Data Engineering Intern", "Master of Science in Computer Engineering",
                 "automating approximately 94% of manual test cases", "Evaluated results using mAP, MOTA, IDF1"):
        assert not any(re.search(pattern, text) for pattern in patterns.values()), text


def test_fit_font_is_clamped_to_the_contract():
    source = (ROOT / "data/templates/resume-base.tex").read_text()
    for requested in (8, 9.5, 10, 10.5, 11, 12):
        fitted = set_density(source, requested)
        size = float(re.search(r"\\fontsize\{([\d.]+)pt\}", fitted).group(1))
        assert 10.0 <= size <= 11.0 and "letterpaper" in fitted
        assert validate_density(fitted)[0] == []


def validate(root, change=None, compile=False):
    """Run the copied validator on the copied base template (no evidence map needed for the base)."""
    base = root / "data/templates/resume-base.tex"
    if change:
        before = base.read_text(encoding="utf-8")
        after = change(before)
        assert after != before, "the mutation did not apply"
        base.write_text(after, encoding="utf-8")
    qa = root / "qa.json"
    command = [sys.executable, str(root / "backend/scripts/validate_resume.py"), str(base), "--qa-json", str(qa)]
    if compile:
        command += ["--compile", "--output", str(root / "out/resume.pdf"), "--render-dir", str(root / "out/pages")]
    subprocess.run(command, capture_output=True, text=True, timeout=240)
    return json.loads(qa.read_text())


def test_base_template_passes_every_static_gate(workspace):
    qa = validate(workspace.root)
    assert qa["failures"] == [] and qa["status"] == "AUTOMATED_PASS_MANUAL_PENDING"
    assert qa["selected_project_ids"] == ["PROJ-P01-IOT", "PROJ-P04-NEWS-RAG"]


MUTATIONS = {
    "page break": (lambda s: s.replace("\\section{Projects}", "\\newpage\n\\section{Projects}"), "explicit page break"),
    "A4 paper": (lambda s: s.replace("\\documentclass[letterpaper,10pt]", "\\documentclass[a4paper,10pt]"), "Letter 10pt document class"),
    "9pt body": (lambda s: s.replace("\\documentclass[letterpaper,10pt]", "\\documentclass[letterpaper,9pt]"), "Letter 10pt document class"),
    "narrow margins": (lambda s: s.replace("top=0.5in,bottom=0.5in", "top=0.3in,bottom=0.3in"), "fixed geometry"),
    "tight leading": (lambda s: s.replace("\\baselinestretch}{1.0}", "\\baselinestretch}{0.9}"), "baselinestretch"),
    "years total": (lambda s: s.replace(INSOPS_BULLET, "With 3+ years of experience, built data workflows"), "years-of-experience total"),
    "publication": (lambda s: s.replace(INSOPS_BULLET, "Published at ICCV; built data workflows"), "publication claim"),
    "94% moved off Dräger": (lambda s: s.replace(INSOPS_BULLET, "Automated approximately 94\\% of data workflows"), "Percentage 94%"),
    "untagged line": (lambda s: s.replace("  % EVIDENCE: EXP-TA-001\n  \\item", "  \\item", 1), "lacks an EVIDENCE tag"),
    "fill-in marker": (lambda s: s.replace(INSOPS_BULLET, "Built data workflows for [FILL IN: records per day] records"), "fill-in marker"),
    "held claim": (lambda s: s.replace("% EVIDENCE: EXP-INSOPS-001\n\\roleheading", "% EVIDENCE: EXP-INSOPS-DS-FRAMING\n\\roleheading", 1), "Held source EVIDENCE ID"),
    "hidden character": (lambda s: s.replace(INSOPS_BULLET, INSOPS_BULLET.replace("data ", "data​ ")), "(AI marks)"),
    "look-alike letter": (lambda s: s.replace(INSOPS_BULLET, INSOPS_BULLET.replace("SQL Server", "SQL Sеrver")), "(AI marks)"),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_validator_rejects_contract_breaks(workspace, name):
    change, expected = MUTATIONS[name]
    qa = validate(workspace.root, change)
    assert qa["status"] == "FAIL"
    assert any(expected in failure for failure in qa["failures"]), qa["failures"]


def test_base_template_compiles_to_exactly_one_letter_page(workspace):
    if not shutil.which("tectonic"):
        pytest.skip("PDF runtime unavailable")
    qa = validate(workspace.root, compile=True)
    assert qa["failures"] == [], qa["failures"]
    assert qa["compile_ok"] and qa["page_count"] == 1 and qa["overflow_count"] == 0
    assert list(qa["preview_sha256"]) == ["page-01.png"]
    assert qa["pdf_metadata"]["author"] == NAME
    # AI marks: the published PDF carries its title and author and nothing else.
    assert qa["ai_marks"] == {"source": [], "pdf": []}
    from pypdf import PdfReader
    assert sorted(PdfReader(str(workspace.root / "out/resume.pdf")).metadata) == ["/Author", "/Title"]
    assert qa["status"] == "AUTOMATED_PASS_MANUAL_PENDING" and not qa["release_ready"]


def test_a_second_page_is_a_failure_not_a_warning(workspace):
    if not shutil.which("tectonic"):
        pytest.skip("PDF runtime unavailable")
    qa = validate(workspace.root, lambda s: s.replace("\\section{Projects}", "\\vspace*{6in}\n\\section{Projects}"), compile=True)
    assert qa["page_count"] == 2
    assert any("exactly 1 page(s); found 2" in failure for failure in qa["failures"])
    assert not (workspace.root / "out/resume.pdf").exists(), "a failing PDF is never published"
