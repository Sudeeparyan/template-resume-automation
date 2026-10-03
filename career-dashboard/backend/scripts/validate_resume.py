#!/usr/bin/env python3
"""Compile and fail-closed validate a candidate's base or tailored LaTeX resume.

Page count, paper, fonts, sections, header and the stable facts come from
backend/resume_contract.py (profile.yml + evidence.yml), never from this file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.resume_contract import Contract, contract_for, load_contract  # noqa: E402
from backend.pdf_compiler import tectonic_executable  # noqa: E402
from backend.ai_marks import clean_pdf, find_marks, pdf_leftovers  # noqa: E402

# ROOT is where the code lives; DATA_ROOT is the workspace whose resume is checked.
# A clean installation has no profile files until its first build.
DATA_ROOT = ROOT
try:
    CONTRACT = load_contract()
except FileNotFoundError:
    CONTRACT = Contract()
BASE_TEMPLATE = (DATA_ROOT / "data/templates/resume-base.tex").resolve()
EVIDENCE_PATH = DATA_ROOT / "data/context/evidence.yml"
PDF_INSPECTOR = ROOT / "backend/scripts/pdf_inspect.swift"
MIN_CHARS = 900


REQUIRED_SECTIONS = tuple(CONTRACT.required_sections)

# Wording that can never appear: derived from profile.yml identity_guardrails, the
# never-claim skills list and the two open questions (years total, publications).
UNSAFE_PATTERNS = dict(CONTRACT.unsafe_patterns)

PLACEHOLDER_PATTERNS = {
    "double-brace placeholder": r"\{\{[^{}]+\}\}",
    "angle placeholder": r"<(?:COMPANY|ROLE|DATE|INSERT|TODO)[^>]*>",
    "unfinished marker": r"(?i)\b(?:TBD|TODO|FIXME|YOUR COMPANY|INSERT HERE)\b",
    # An unknown number must be supplied by the candidate, never guessed.
    "fill-in marker": r"(?i)\[\s*FILL\s*IN\b[^\]]*\]",
}

PROHIBITED_LAYOUT_PATTERNS = {
    "multi-column package/environment": r"(?i)\\(?:usepackage\{(?:multicol|paracol)\}|begin\{multicols?\})",
    "sidebar/minipage layout": r"\\begin\{minipage\}",
    "raster or vector image": r"\\includegraphics",
    "font below contract": r"\\(?:tiny|scriptsize|footnotesize|small)\b",
    "manual font shrinking": r"\\fontsize\s*\{",
    "geometry override": r"\\geometry\s*\{",
    "margin mutation": r"\\(?:addtolength|setlength)\s*\{\\(?:oddsidemargin|evensidemargin|textwidth|topmargin|textheight)",
    "line-spread override": r"\\linespread\s*\{",
}

# Every number the PDF may show must already appear in registered wording.
ALLOWED_VISIBLE_NUMBERS = set(CONTRACT.allowed_visible_numbers)


def configure(workspace, market: str | None = None) -> None:
    """Validate the resume of the workspace rooted at `workspace` (a profile folder)."""
    global DATA_ROOT, CONTRACT, BASE_TEMPLATE, EVIDENCE_PATH, REQUIRED_SECTIONS, UNSAFE_PATTERNS, ALLOWED_VISIBLE_NUMBERS
    DATA_ROOT = Path(workspace).resolve()
    CONTRACT = contract_for(DATA_ROOT, market or None)
    BASE_TEMPLATE = (DATA_ROOT / "data/templates/resume-base.tex").resolve()
    EVIDENCE_PATH = DATA_ROOT / "data/context/evidence.yml"
    REQUIRED_SECTIONS = tuple(CONTRACT.required_sections)
    UNSAFE_PATTERNS = dict(CONTRACT.unsafe_patterns)
    ALLOWED_VISIBLE_NUMBERS = set(CONTRACT.allowed_visible_numbers)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strip_latex_comments(source: str) -> str:
    lines: list[str] = []
    for line in source.splitlines():
        index = None
        escaped = False
        for position, character in enumerate(line):
            if character == "%" and not escaped:
                index = position
                break
            escaped = character == "\\" and not escaped
            if character != "\\":
                escaped = False
        lines.append(line[:index] if index is not None else line)
    return "\n".join(lines)


def latex_braces_balanced(source: str) -> bool:
    depth = 0
    escaped = False
    for character in strip_latex_comments(source):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def approximate_visible_words(source: str) -> int:
    text = strip_latex_comments(source)
    text = re.sub(r"\\(?:href|roleheading|clientheading)\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\1 \2", text)
    text = re.sub(r"\\[A-Za-z@]+(?:\[[^\]]*\])?", " ", text)
    text = text.replace(r"\&", "&").replace(r"\%", "%").replace(r"\textbar{}", "|")
    text = re.sub(r"[{}]", " ", text)
    text = re.sub(r"[^A-Za-z0-9+#/&.-]+", " ", text)
    return len(re.findall(r"\b[A-Za-z0-9][A-Za-z0-9+#/&.-]*\b", text))


def extract_zero_argument_macros(source: str) -> dict[str, str]:
    """Extract simple no-argument newcommands while preserving nested braces."""
    commands: dict[str, str] = {}
    pattern = re.compile(r"\\newcommand\{\\([A-Za-z@]+)\}")
    for match in pattern.finditer(source):
        position = match.end()
        while position < len(source) and source[position].isspace():
            position += 1
        if position >= len(source) or source[position] == "[" or source[position] != "{":
            continue
        depth = 0
        escaped = False
        end = None
        for index in range(position, len(source)):
            character = source[index]
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is not None:
            commands[match.group(1)] = source[position + 1 : end]
    return commands


def expand_zero_argument_macros(source: str, body: str) -> str:
    """Expand simple no-argument newcommands for source-density estimates."""
    commands = extract_zero_argument_macros(source)
    expanded = body
    for name, value in sorted(commands.items(), key=lambda item: len(item[0]), reverse=True):
        expanded = re.sub(rf"\\{re.escape(name)}\b", lambda _: value, expanded)
    return expanded


LATEX_ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303", "c": "\u0327", "v": "\u030c"}
LATEX_SYMBOLS = {
    r"\textbullet{}": "\u2022", r"\textperiodcentered{}": "\u00b7", r"\textdegree{}": "\u00b0",
    r"$\times$": "\u00d7", r"$\rightarrow$": "\u2192", r"$\le$": "\u2264", r"$\ge$": "\u2265",
    r"\texteuro{}": "\u20ac", r"\pounds{}": "\u00a3",
}
PLAIN_PUNCTUATION = (
    ("---", "-"), ("--", "-"), ("\u2014", "-"), ("\u2013", "-"), ("\u2012", "-"), ("\u2010", "-"), ("\u2011", "-"), ("\u2212", "-"),
    ("``", '"'), ("''", '"'), ("\u201c", '"'), ("\u201d", '"'), ("`", "'"), ("\u2018", "'"), ("\u2019", "'"),
    ("\u2026", "..."), ("\u00a0", " "),
)


def normalize_latex_text(value: str) -> str:
    # Callers pass an already isolated construct or plain registry text. Do not
    # treat a literal percent in registry prose as the start of a LaTeX comment.
    text = value
    replacements = {
        r"\%": "%",
        r"\&": "&",
        r"\_": "_",
        r"\#": "#",
        r"\$": "$",
        r"\textbar{}": "|",
        "--": "-",
    }
    # A forced line break ("\\" or "\\[1pt]") ends a skills line; it is layout, not text.
    text = re.sub(r"\\\\(?:\[[^\]]*\])?", " ", text)
    for old, new in replacements.items():
        text = text.replace(old, new)
    # career.tex_escape prints R² as R\textsuperscript{2}; read it back as registered.
    text = re.sub(r"\\textsuperscript\{([23])\}", lambda m: "\u00b2" if m[1] == "2" else "\u00b3", text)
    # ...and accented letters and symbols as LaTeX (Tectonic's T1 fonts drop raw Unicode).
    text = re.sub(r"\\(['`^\"~cv])\{?([A-Za-z])\}?",
                  lambda m: unicodedata.normalize("NFC", m[2] + LATEX_ACCENTS[m[1]]), text)
    for latex, plain in LATEX_SYMBOLS.items():
        text = text.replace(latex, plain)
    text = re.sub(r"\\(?:textbf|textit|emph)\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z@]+(?:\[[^\]]*\])?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    # One plain form for every dash and quote, whether typed as Unicode or as LaTeX.
    for fancy, plain in PLAIN_PUNCTUATION:
        text = text.replace(fancy, plain)
    return re.sub(r"\s+", " ", text).strip()


# One scanner feeds both the release gate and the --report mode so they never disagree.
CLAIM_LINE_PATTERN = re.compile(
    r"""(?x)
    ^(?:
      \\newcommand\{\\(?:ResumeSummary|ResumeName|ResumeContact|CoreSkills|Skills[A-Za-z]+|Coursework|(?:SelectedProject|SecondProject)(?:ID|Title|Context|BulletOne|BulletTwo|BulletThree))\}
      |\\roleheading\b
      |\\clientheading\b
      |\\item\b
      |\{\\LARGE\\bfseries\b
      |\\ResumeContact\b
      |\\textbf\{[^{}]+:
    )
    """
)

# Legacy tailoring tagged proposed content with its review-row id. Review rows
# are not candidate evidence, regardless of their origin or keep/remove decision.
PREDICTED_TAG = re.compile(r"^resume_items:([^\s]+)$")

# Keep the legacy report keys; unsupported suggestions have no evidence confidence.
CLAIM_CONFIDENCE = {"verified": 100, "predicted": 0, "missing": 0}

def _logical_section(macro_name: str | None, current: str | None, skill_section: str) -> str:
    """The section a claim line belongs to. Preamble macro definitions report the
    section they feed, so Projects/Skills fields share their section's review rules."""
    if current:
        return current
    if not macro_name:
        return "Header"
    if macro_name.startswith(("SelectedProject", "SecondProject")):
        return "Projects"
    if macro_name == "CoreSkills" or macro_name.startswith("Skills"):
        return skill_section
    if macro_name == "Coursework":
        return "Education"
    return "Header"


def scan_claims(source: str, evidence: dict[str, Any], predicted: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """One record per candidate-claim line: its EVIDENCE tags and where they resolve.

    `predicted` is retained for report-call compatibility. A ``resume_items:<id>``
    tag is never registry evidence. Registry-held ids count as missing: known but not usable.
    A line is 'verified' only when every tag resolves in the registry. Each record
    also carries `problems`, the gate's failure texts, so both consumers word the
    same issue the same way.
    """
    skill_section = "Skills" if r"\section{Skills}" in source else "Technical Skills"
    known = {
        item.get("id"): item
        for group in ("claims", "projects")
        for item in evidence.get(group, [])
        if isinstance(item, dict) and item.get("id")
    }
    claims: list[dict[str, Any]] = []
    pending: list[str] | None = None
    section: str | None = None
    for line_number, line in enumerate(source.splitlines(), start=1):
        heading = re.match(r"^\s*\\section\{([^{}]+)\}", line)
        if heading:
            section = heading.group(1)
        evidence_match = re.match(r"^\s*%\s*EVIDENCE:\s*(.*?)\s*$", line)
        if evidence_match:
            pending = [value for value in evidence_match.group(1).split() if value]
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if not CLAIM_LINE_PATTERN.search(stripped):
            if not stripped.startswith("%"):
                pending = None
            continue
        macro = re.match(r"\\newcommand\{\\([A-Za-z@]+)\}", stripped)
        line_section = _logical_section(macro.group(1) if macro else None, section, skill_section)
        evidence_ids = pending or []
        problems: list[str] = []
        if not evidence_ids:
            problems.append(f"Candidate content on source line {line_number} lacks an EVIDENCE tag")
        for evidence_id in evidence_ids:
            marker = PREDICTED_TAG.match(evidence_id)
            if marker:
                problems.append(f"Review item is not registered candidate evidence on line {line_number}: {evidence_id}")
                continue
            item = known.get(evidence_id)
            if item is None:
                problems.append(f"Unknown source EVIDENCE ID on line {line_number}: {evidence_id}")
            elif item.get("status") in {"hold", "missing"}:
                problems.append(f"Unavailable source EVIDENCE ID on line {line_number}: {evidence_id}")
        if problems:
            status = "missing"
            note = "; ".join(problems)
        else:
            status = "verified"
            note = "All evidence ids resolve in the registry"
        claims.append(
            {
                "line": line_number,
                "section": line_section,
                "evidence_ids": evidence_ids,
                "status": status,
                "confidence": CLAIM_CONFIDENCE[status],
                "note": note,
                "problems": problems,
            }
        )
        pending = None
    return claims


def evidence_ids_from_source(
    source: str,
    evidence: dict[str, Any],
    failures: list[str],
    warnings: list[str] | None = None,
) -> set[str]:
    """Require an evidence tag immediately before each candidate-content construct.

    Returns the registry ids actually used. Unknown, held, review-row and missing
    tags are hard failures in every section. `warnings` remains a compatibility
    argument; human review cannot replace candidate evidence.
    """
    known = {
        item.get("id"): item
        for group in ("claims", "projects")
        for item in evidence.get(group, [])
        if isinstance(item, dict) and item.get("id")
    }
    ids_used: set[str] = set()
    for claim in scan_claims(source, evidence):
        ids_used.update(
            evidence_id
            for evidence_id in claim["evidence_ids"]
            if evidence_id in known and known[evidence_id].get("status") not in {"hold", "missing"}
        )
        failures.extend(claim["problems"])
    return ids_used


def load_predicted_origins(db_path) -> dict[str, str]:
    """resume_items row id -> origin, for resolving ``resume_items:<id>`` evidence tags."""
    if not db_path or not Path(db_path).is_file():
        return {}
    import sqlite3

    try:
        with sqlite3.connect(str(db_path)) as db:
            rows = db.execute("SELECT id, origin FROM resume_items").fetchall()
    except sqlite3.Error:
        return {}  # An older database without the review table leaves every tag unresolved.
    return {row[0]: row[1] for row in rows}


def build_report(db_path, tex_path, evidence: dict[str, Any] | None = None, source: str | None = None) -> dict[str, Any]:
    """Importable per-claim assurance report for one resume source (--report and the API).

    The evidence registry is read from the workspace the database lives in
    (<root>/data/career.db -> <root>/data/context/evidence.yml) unless supplied.
    `source` lets an in-process caller pass the current draft text directly.
    """
    tex_path = Path(tex_path)
    if source is None:
        source = tex_path.read_text(encoding="utf-8")
    if evidence is None:
        workspace = Path(db_path).resolve().parent.parent if db_path else DATA_ROOT
        candidate = workspace / "data/context/evidence.yml"
        evidence = load_yaml(candidate if candidate.is_file() else EVIDENCE_PATH)
    claims = scan_claims(source, evidence, load_predicted_origins(db_path))
    summary = {status: sum(1 for claim in claims if claim["status"] == status) for status in CLAIM_CONFIDENCE}
    return {
        "source": str(tex_path),
        "db": str(db_path) if db_path else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": summary,
        "claims": [
            {key: claim[key] for key in ("line", "section", "evidence_ids", "status", "confidence", "note")}
            for claim in claims
        ],
    }


def percentages_trace_to_tags(source: str, evidence: dict[str, Any], failures: list[str]) -> None:
    """A percentage may only appear on a line whose EVIDENCE tags carry that exact figure.

    This pins a metric to the exact claim or project that supplied it.
    """
    known = {
        item.get("id"): item
        for group in ("claims", "projects")
        for item in evidence.get(group, [])
        if isinstance(item, dict) and item.get("id")
    }

    def wording(item: dict[str, Any]) -> str:
        content = item.get("resume_content") or {}
        parts = [item.get("value", ""), *(item.get("approved_facts") or []),
                 content.get("title", ""), content.get("context", ""), *(content.get("bullets") or [])]
        return " ".join(str(part) for part in parts)

    pending: list[str] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        tag = re.match(r"^\s*%\s*EVIDENCE:\s*(.*?)\s*$", line)
        if tag:
            pending = tag.group(1).split()
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("%"):
            continue
        figures = re.findall(r"(\d+(?:\.\d+)?)\s*\\%", stripped)
        if figures:
            allowed = set(re.findall(r"(\d+(?:\.\d+)?)\s*%", " ".join(wording(known.get(i, {})) for i in pending)))
            for figure in figures:
                if figure not in allowed:
                    failures.append(
                        f"Percentage {figure}% on source line {line_number} is not in the approved wording of its EVIDENCE tags"
                    )
        pending = []


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        pass  # Keep the original macOS fallback for an unconfigured interpreter.
    else:
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as exc:
            raise RuntimeError(f"Unable to parse {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"Expected a YAML mapping in {path}")
        return value
    ruby = shutil.which("ruby")
    if not ruby:
        raise RuntimeError("Install the workspace requirements (PyYAML), or use Ruby for the fallback YAML parser")
    program = (
        'require "yaml"; require "json"; '
        "value = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: false); "
        "STDOUT.write(JSON.generate(value))"
    )
    result = subprocess.run(
        [ruby, "-e", program, str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or "unknown YAML parse error"
        raise RuntimeError(f"Unable to parse {path}: {detail}")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a YAML mapping in {path}")
    return value


def inspect_pdf(pdf_path: Path, render_dir: Path) -> dict[str, Any]:
    render_dir.mkdir(parents=True, exist_ok=True)
    for stale_preview in render_dir.glob("page-*.png"):
        stale_preview.unlink()
    swiftc = shutil.which("swiftc")
    swift = shutil.which("swift")
    if (swiftc or swift) and PDF_INSPECTOR.is_file():
        command: list[str]
        if swiftc:
            inspector_hash = sha256(PDF_INSPECTOR)[:12]
            cached_inspector = Path(tempfile.gettempdir()) / f"career-pdf-inspect-{inspector_hash}"
            if not cached_inspector.is_file():
                compile_result = subprocess.run(
                    [swiftc, str(PDF_INSPECTOR), "-o", str(cached_inspector)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if compile_result.returncode:
                    raise RuntimeError(
                        compile_result.stderr.strip() or "Unable to compile Swift PDF inspector"
                    )
            command = [str(cached_inspector), str(pdf_path), str(render_dir)]
        else:
            command = [str(swift), str(PDF_INSPECTOR), str(pdf_path), str(render_dir)]
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Swift PDF inspection failed")
        return json.loads(result.stdout)

    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PDF text inspection requires Swift/PDFKit on macOS or the pypdf package"
        ) from exc

    renderer = shutil.which("pdftoppm")
    if renderer:
        rendered = subprocess.run([renderer, "-png", "-r", "144", str(pdf_path), str(render_dir / "page")], capture_output=True, text=True, timeout=120)
        if rendered.returncode:
            raise RuntimeError(rendered.stderr or "Poppler rendering failed")
        for preview in sorted(render_dir.glob("page-*.png")):
            number = int(preview.stem.split("-")[-1])
            desired = render_dir / f"page-{number:02d}.png"
            if desired != preview:
                preview.rename(desired)
    else:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:
            raise RuntimeError("PDF rendering requires the bundled pypdfium2 package") from exc
        document = pdfium.PdfDocument(str(pdf_path))
        try:
            for index in range(len(document)):
                page = document[index]
                try:
                    page.render(scale=2).to_pil().save(render_dir / f"page-{index + 1:02d}.png")
                finally:
                    page.close()
        finally:
            document.close()

    reader = PdfReader(str(pdf_path))
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        media_box = page.mediabox
        pages.append(
            {
                "number": index,
                "width_points": float(media_box.width),
                "height_points": float(media_box.height),
                "text_characters": len(text),
                "text": text,
            }
        )
    return {
        "page_count": len(pages),
        "text_characters": sum(page["text_characters"] for page in pages),
        "metadata": {
            "title": str((reader.metadata or {}).get("/Title", "")),
            "author": str((reader.metadata or {}).get("/Author", "")),
            "subject": str((reader.metadata or {}).get("/Subject", "")),
        },
        "pages": pages,
    }


def compile_latex(source_path: Path, output_dir: Path) -> tuple[Path | None, str, str]:
    tectonic = tectonic_executable()
    if not tectonic:
        return None, "", "tectonic is not installed"
    result = subprocess.run(
        [
            tectonic,
            "-p",
            "--keep-logs",
            "--outdir",
            str(output_dir),
            str(source_path),
        ],
        text=True,
        capture_output=True,
        check=False,
        cwd=str(source_path.parent),
    )
    chatter = "\n".join(part for part in (result.stdout, result.stderr) if part)
    pdf_path = output_dir / f"{source_path.stem}.pdf"
    log_path = output_dir / f"{source_path.stem}.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    if result.returncode or not pdf_path.is_file():
        detail = chatter.strip() or log_text.strip() or f"tectonic exited {result.returncode}"
        return None, log_text, detail
    # Every compiled resume leaves with only its title and author (backend/ai_marks.py).
    clean_pdf(pdf_path)
    return pdf_path, log_text, chatter


def add_failure(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def validate_evidence_map(
    evidence_map_path: Path | None,
    evidence: dict[str, Any],
    selected_project_id: str,
    source_evidence_ids: set[str],
    source_path: Path,
    failures: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "required": evidence_map_path is not None,
        "path": str(evidence_map_path) if evidence_map_path else None,
        "sha256": None,
        "job_id": None,
        "role_eligible": None,
        "candidate_revision": None,
        "job_snapshot_sha256": None,
        "supported_requirement_coverage": None,
        "requirements_count": None,
        "company_problem": None,
        "resume_claim_ids": [],
        "source_evidence_ids": sorted(source_evidence_ids),
        "held_claims_used": None,
        "all_candidate_claims_grounded": bool(source_evidence_ids),
    }
    if evidence_map_path is None:
        return result
    if not evidence_map_path.is_file():
        failures.append(f"Missing evidence map: {evidence_map_path}")
        result["all_candidate_claims_grounded"] = False
        return result

    try:
        mapping = load_yaml(evidence_map_path)
    except (RuntimeError, json.JSONDecodeError) as exc:
        failures.append(str(exc))
        result["all_candidate_claims_grounded"] = False
        return result
    result["sha256"] = sha256(evidence_map_path)

    known_claims = {
        item.get("id"): item
        for item in evidence.get("claims", [])
        if isinstance(item, dict) and item.get("id")
    }
    known_projects = {
        item.get("id"): item
        for item in evidence.get("projects", [])
        if isinstance(item, dict) and item.get("id")
    }
    claim_ids = mapping.get("resume_claim_ids")
    held = mapping.get("held_claims_used")
    mapped_project = mapping.get("selected_project_id")
    source_macros = extract_zero_argument_macros(source_path.read_text(encoding="utf-8"))
    selected_ids = [source_macros.get(k) for k in ('SelectedProjectID', 'SecondProjectID')]
    if CONTRACT.required_selected_projects < 2:
        selected_ids = [value for value in selected_ids if value]
    add_failure(failures, mapping.get('selected_project_ids') == selected_ids,
                'Evidence map selected_project_ids must match the resume project slots in order')
    requirements = mapping.get("requirements")
    company_problem = mapping.get("company_problem")
    coverage = mapping.get("supported_requirement_coverage")
    result.update(
        {
            "job_id": mapping.get("job_id"),
            "role_eligible": mapping.get("role_eligible"),
            "candidate_revision": mapping.get("candidate_revision"),
            "job_snapshot_sha256": mapping.get("job_snapshot_sha256"),
            "supported_requirement_coverage": coverage,
            "requirements_count": len(requirements) if isinstance(requirements, list) else None,
            "company_problem": company_problem,
            "resume_claim_ids": claim_ids if isinstance(claim_ids, list) else [],
            "held_claims_used": held,
        }
    )

    add_failure(
        failures,
        isinstance(mapping.get("job_id"), str) and len(mapping["job_id"].strip()) >= 5,
        "Evidence map needs a stable job_id",
    )
    add_failure(failures, mapping.get("role_eligible") is True, "Evidence map role_eligible must be true")
    add_failure(
        failures,
        mapping.get("candidate_revision") == evidence.get("candidate_revision"),
        "Evidence map candidate_revision does not match context/evidence.yml",
    )

    jd_snapshot = source_path.parent / "job-description.md"
    actual_jd_hash = sha256(jd_snapshot) if jd_snapshot.is_file() else None
    if actual_jd_hash is None:
        failures.append(f"Missing JD snapshot beside tailored resume: {jd_snapshot}")
    add_failure(
        failures,
        isinstance(mapping.get("job_snapshot_sha256"), str)
        and mapping.get("job_snapshot_sha256") == actual_jd_hash,
        "Evidence-map job_snapshot_sha256 does not match the saved JD snapshot",
    )
    add_failure(
        failures,
        (mapped_project or "") == selected_project_id,
        "Evidence map selected_project_id does not match LaTeX",
    )
    add_failure(failures, held == [], "held_claims_used must be an empty list")
    add_failure(
        failures,
        isinstance(claim_ids, list) and bool(claim_ids),
        "resume_claim_ids must be a non-empty list",
    )
    add_failure(
        failures,
        isinstance(coverage, (int, float)) and 0 <= float(coverage) <= 100,
        "supported_requirement_coverage must be a number from 0 to 100",
    )

    requirement_ids: set[str] = set()
    if not isinstance(requirements, list) or not requirements:
        failures.append("Evidence map requirements must be a non-empty list")
    else:
        for index, requirement in enumerate(requirements, start=1):
            if not isinstance(requirement, dict):
                failures.append(f"Requirement {index} is not a mapping")
                continue
            requirement_id = requirement.get("id")
            priority = requirement.get("priority")
            text = requirement.get("text")
            mapped_ids = requirement.get("evidence_ids")
            strength = requirement.get("strength")
            if not isinstance(requirement_id, str) or not re.fullmatch(r"R\d{2,}", requirement_id):
                failures.append(f"Requirement {index} has invalid id")
            elif requirement_id in requirement_ids:
                failures.append(f"Duplicate requirement id: {requirement_id}")
            else:
                requirement_ids.add(requirement_id)
            if priority not in {"required", "preferred"}:
                failures.append(f"Requirement {requirement_id or index} has invalid priority")
            if not isinstance(text, str) or len(text.strip()) < 3:
                failures.append(f"Requirement {requirement_id or index} needs text")
            if not isinstance(mapped_ids, list):
                failures.append(f"Requirement {requirement_id or index} needs evidence_ids")
                mapped_ids = []
            if strength not in {"strong", "partial", "gap"}:
                failures.append(f"Requirement {requirement_id or index} has invalid strength")
            if strength in {"strong", "partial"} and not mapped_ids:
                failures.append(
                    f"Requirement {requirement_id or index} claims support without evidence IDs"
                )
            if strength == "gap" and mapped_ids:
                failures.append(f"Gap requirement {requirement_id or index} must not cite evidence")
            for evidence_id in mapped_ids:
                item = known_claims.get(evidence_id) or known_projects.get(evidence_id)
                if not item:
                    failures.append(
                        f"Requirement {requirement_id or index} cites unknown ID: {evidence_id}"
                    )
                elif item.get("status") == "hold":
                    failures.append(
                        f"Requirement {requirement_id or index} cites held ID: {evidence_id}"
                    )

    if not isinstance(company_problem, dict):
        failures.append("Evidence map needs company_problem research")
    else:
        statement = company_problem.get("statement")
        source_url = company_problem.get("source_url")
        research_date = company_problem.get("published_or_accessed")
        evidence_class = company_problem.get("evidence_class")
        confidence = company_problem.get("confidence")
        if not isinstance(statement, str) or len(statement.strip()) < 15:
            failures.append("company_problem.statement is missing or too vague")
        if (
            not isinstance(source_url, str)
            or not re.match(r"^https?://", source_url)
            or "example.invalid" in source_url
        ):
            failures.append("company_problem.source_url must be a real HTTP(S) source")
        try:
            if not isinstance(research_date, str):
                raise ValueError
            datetime.strptime(research_date, "%Y-%m-%d")
        except ValueError:
            failures.append("company_problem.published_or_accessed must be YYYY-MM-DD")
        if evidence_class not in {"explicit", "inferred"}:
            failures.append("company_problem.evidence_class must be explicit or inferred")
        if confidence not in {"high", "medium", "low"}:
            failures.append("company_problem.confidence must be high, medium, or low")

    selected_reason = mapping.get("selected_project_reason")
    add_failure(
        failures,
        isinstance(selected_reason, str)
        and len(selected_reason.strip()) >= 15
        and "replace" not in selected_reason.lower(),
        "selected_project_reason is missing or placeholder text",
    )

    unknown_ids = []
    held_ids = []
    if isinstance(claim_ids, list):
        for claim_id in claim_ids:
            item = known_claims.get(claim_id) or known_projects.get(claim_id)
            if not item:
                unknown_ids.append(str(claim_id))
            elif item.get("status") == "hold":
                held_ids.append(str(claim_id))
    if unknown_ids:
        failures.append(f"Evidence map contains unknown IDs: {', '.join(sorted(unknown_ids))}")
    if held_ids:
        failures.append(f"Evidence map uses held IDs: {', '.join(sorted(held_ids))}")
    declared_ids = set(claim_ids) if isinstance(claim_ids, list) else set()
    if declared_ids != source_evidence_ids:
        missing_from_map = sorted(source_evidence_ids - declared_ids)
        unused_in_source = sorted(declared_ids - source_evidence_ids)
        if missing_from_map:
            failures.append(
                "Evidence map omits source-tagged IDs: " + ", ".join(missing_from_map)
            )
        if unused_in_source:
            failures.append(
                "Evidence map declares IDs not attached to visible source: "
                + ", ".join(unused_in_source)
            )
    if selected_project_id and selected_project_id not in declared_ids:
        failures.append("Selected project ID must appear in resume_claim_ids")
    result["all_candidate_claims_grounded"] = (
        bool(claim_ids)
        and not unknown_ids
        and not held_ids
        and held == []
        and declared_ids == source_evidence_ids
    )
    return result


def validate_selected_project(
    source: str,
    evidence: dict[str, Any],
    selected_project_id: str,
    failures: list[str],
    second: bool = False,
) -> dict[str, Any]:
    if second:
        # Validate each slot against the identical registry and rendering rules.
        source = source.replace('SelectedProject', 'IgnoredFirstProject').replace('SELECTED_PROJECT', 'IGNORED_FIRST_PROJECT')
        source = source.replace('SecondProject', 'SelectedProject').replace('SECOND_PROJECT', 'SELECTED_PROJECT')
    projects = {
        item.get("id"): item
        for item in evidence.get("projects", [])
        if isinstance(item, dict) and item.get("id")
    }
    project = projects.get(selected_project_id)
    result: dict[str, Any] = {
        "id": selected_project_id or None,
        "title": None,
        "bullet_count": None,
        "content_matches_registry": False,
    }
    if not project:
        return result
    resume_content = project.get("resume_content")
    if not isinstance(resume_content, dict):
        failures.append(f"Selected project has no registered resume_content: {selected_project_id}")
        return result

    macros = extract_zero_argument_macros(strip_latex_comments(source))
    expected_title = str(resume_content.get("title", ""))
    expected_context = str(resume_content.get("context", ""))
    expected_bullets = resume_content.get("bullets")
    if not isinstance(expected_bullets, list) or not 2 <= len(expected_bullets) <= 3:
        failures.append(f"Selected project registry needs 2-3 approved bullets: {selected_project_id}")
        return result

    actual_title = normalize_latex_text(macros.get("SelectedProjectTitle", ""))
    actual_context = normalize_latex_text(macros.get("SelectedProjectContext", ""))
    bullet_names = ("SelectedProjectBulletOne", "SelectedProjectBulletTwo", "SelectedProjectBulletThree")
    # A one-page fit may drop the third registered bullet (documented cut order), so the
    # rendered bullets must be the first two or all three registered ones, in order.
    rendered_count = sum(1 for name in bullet_names if name in macros)
    if rendered_count < 2 or rendered_count > len(expected_bullets):
        rendered_count = len(expected_bullets)
    expected_bullets = expected_bullets[:rendered_count]
    actual_bullets = [
        normalize_latex_text(macros.get(name, ""))
        for name in bullet_names[: len(expected_bullets)]
    ]
    expected_bullets_normalized = [normalize_latex_text(str(value)) for value in expected_bullets]
    result["title"] = actual_title
    result["bullet_count"] = len(expected_bullets)

    if actual_title != normalize_latex_text(expected_title):
        failures.append("Selected project title does not match its registered project ID")
    if actual_context != normalize_latex_text(expected_context):
        failures.append("Selected project context does not match its registered project ID")
    if actual_bullets != expected_bullets_normalized:
        failures.append("Selected project bullets do not match the approved project registry")

    start_markers = source.count("% SELECTED_PROJECT_BLOCK_START")
    end_markers = source.count("% SELECTED_PROJECT_BLOCK_END")
    if start_markers != 1 or end_markers != 1:
        failures.append("Selected project block must have exactly one start and end marker")
        return result
    block = source.split("% SELECTED_PROJECT_BLOCK_START", 1)[1].split(
        "% SELECTED_PROJECT_BLOCK_END", 1
    )[0]
    rendered_items = re.findall(
        r"\\item\s+\\(SelectedProjectBullet(?:One|Two|Three))\b",
        strip_latex_comments(block),
    )
    all_item_count = len(re.findall(r"\\item\b", strip_latex_comments(block)))
    expected_names = list(bullet_names[: len(expected_bullets)])
    if rendered_items != expected_names or all_item_count != len(expected_bullets):
        failures.append(
            "Selected project block must render only the registered 2-3 project bullets"
        )
    if len(re.findall(r"\\SelectedProjectTitle\b", block)) != 1:
        failures.append("Selected project title must render exactly once")
    if len(re.findall(r"\\SelectedProjectContext\b", block)) != 1:
        failures.append("Selected project context must render exactly once")
    if re.search(r"\\(?:section|roleheading|clientheading)\b", strip_latex_comments(block)):
        failures.append("Selected project block contains an additional project/section construct")

    result["content_matches_registry"] = not any(
        message.startswith("Selected project") for message in failures
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a candidate's LaTeX resume and optionally compile it to the page count and paper its profile.yml sets."
    )
    parser.add_argument("tex", type=Path, help="Path to the complete LaTeX resume")
    parser.add_argument("--compile", action="store_true", help="Compile with tectonic and inspect the PDF")
    parser.add_argument("--output", type=Path, help="PDF output path")
    parser.add_argument("--qa-json", type=Path, help="Write the QA manifest as JSON")
    parser.add_argument("--render-dir", type=Path, help="Directory for page preview PNGs")
    parser.add_argument("--evidence-map", type=Path, help="Tailored-resume evidence-map.yml")
    parser.add_argument("--studio-layout", action="store_true", help="Validate ranked Studio typography and measured page fill; all evidence/release gates still apply")
    parser.add_argument(
        "--visual-review",
        choices=("pending", "pass", "fail"),
        default="pending",
        help="Record the human visual-review result",
    )
    parser.add_argument(
        "--visual-reviewer",
        help="Reviewer name/identity; required when --visual-review is pass or fail",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print the per-claim assurance report as JSON and exit (no validation gates run)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        help="career.db used to resolve resume_items:<id> evidence tags (default: <workspace>/data/career.db)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Workspace (profile) folder whose profile.yml and evidence.yml govern the check (default: the app's own data)",
    )
    parser.add_argument("--market", choices=("", "ie", "us"), default="",
                        help="market contract for a job-specific resume")
    args = parser.parse_args()
    if args.workspace:
        configure(args.workspace.expanduser(), args.market)

    source_path = args.tex.expanduser().resolve()
    if args.report:
        if not source_path.is_file():
            print(json.dumps({"error": f"Missing LaTeX source: {source_path}"}))
            return 1
        db_path = args.db.expanduser().resolve() if args.db else DATA_ROOT / "data/career.db"
        print(json.dumps(build_report(db_path if db_path.is_file() else None, source_path), indent=2))
        return 0
    failures: list[str] = []
    warnings: list[str] = []
    qa: dict[str, Any] = {
        "schema_version": 2,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source_path),
        "source_sha256": None,
        "candidate_revision": None,
        "selected_project_id": None,
        "project_count": None,
        "compile_ok": False,
        "page_count": None,
        "page2_text_characters": None,
        "pdf_text_extractable": False,
        "required_headings_present": False,
        "overflow_count": None,
        "unresolved_placeholders": [],
        "unsafe_claims": [],
        "visual_review": args.visual_review,
        "visual_reviewer": args.visual_reviewer,
        "visual_reviewed_at": None,
        "preview_sha256": {},
        "release_ready": False,
        "status": "FAIL",
        "failures": failures,
        "warnings": warnings,
    }

    if not source_path.is_file():
        failures.append(f"Missing LaTeX source: {source_path}")
        return finish(args.qa_json, qa, 1)

    try:
        source = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        failures.append(f"LaTeX source is not UTF-8: {exc}")
        return finish(args.qa_json, qa, 1)

    qa["source_sha256"] = sha256(source_path)
    qa["ai_marks"] = {"source": find_marks(source)}
    if qa["ai_marks"]["source"]:
        failures.append("Hidden or look-alike characters (AI marks) in the resume source; delete or retype them: "
                        + "; ".join(qa["ai_marks"]["source"][:5]))
    clean = strip_latex_comments(source)
    required_order = list(REQUIRED_SECTIONS)
    layout_clean = clean
    if args.studio_layout:
        sys.path.insert(0, str(ROOT))
        from backend.services.resume_layout import validate_density, measure_pages
        layout_errors, layout_clean = validate_density(source, contract=CONTRACT)
        failures.extend(layout_errors)
        required_order = re.findall(r"\\section\{([^{}]+)\}", clean)
        allowed_orders = [list(order) for order in CONTRACT.allowed_section_orders]
        add_failure(failures, required_order in allowed_orders, "Studio section order must be one of the track orders in profile.yml and keep every required section exactly once")

    try:
        evidence = load_yaml(EVIDENCE_PATH)
    except (RuntimeError, json.JSONDecodeError) as exc:
        failures.append(str(exc))
        evidence = {}
    qa["candidate_revision"] = evidence.get("candidate_revision")
    qa["registry_sha256"] = sha256(EVIDENCE_PATH)
    qa["profile_sha256"] = sha256(DATA_ROOT / "data/config/profile.yml")
    profile = load_yaml(DATA_ROOT / "data/config/profile.yml")
    candidate = profile.get("candidate", {})
    phone = candidate.get("phone", "")
    unsafe_patterns = dict(UNSAFE_PATTERNS)
    if phone and candidate.get("phone_external_resume_policy") != "include_exactly_as_supplied":
        unsafe_patterns["unconfirmed phone"] = re.escape(phone)
    source_evidence_ids = evidence_ids_from_source(source, evidence, failures, warnings)
    percentages_trace_to_tags(source, evidence, failures)
    qa["source_evidence_ids"] = sorted(source_evidence_ids)

    add_failure(
        failures,
        len(re.findall(CONTRACT.documentclass_pattern(), clean)) == 1,
        f"Resume must contain exactly one fixed {CONTRACT.paper.title()} 10pt document class",
    )
    geometry_declarations = re.findall(
        r"\\usepackage\[([^\]]*)\]\{geometry\}",
        clean,
    )
    add_failure(
        failures,
        geometry_declarations == [CONTRACT.geometry],
        "Resume must contain exactly one fixed geometry declaration",
    )
    line_spread_values = re.findall(
        r"\\renewcommand\{\\baselinestretch\}\{([^{}]+)\}",
        clean,
    )
    add_failure(
        failures,
        line_spread_values == [CONTRACT.line_spread],
        "Resume must preserve the single fixed baselinestretch value",
    )
    add_failure(failures, latex_braces_balanced(source), "LaTeX braces are unbalanced")
    add_failure(
        failures,
        clean.rstrip().endswith(r"\end{document}"),
        "Resume must end with \\end{document}",
    )
    add_failure(
        failures,
        clean.count(r"\newpage") + clean.count(r"\clearpage") <= CONTRACT.pages - 1,
        f"A {CONTRACT.describe_pages()} resume contains too many explicit page breaks",
    )
    add_failure(
        failures,
        len(
            re.findall(
                r"\\usepackage(?:\[[^\]]*\])?\{hyperref\}",
                clean,
                flags=re.DOTALL,
            )
        )
        == 1,
        "Resume must load hyperref exactly once",
    )

    for label, pattern in PROHIBITED_LAYOUT_PATTERNS.items():
        if re.search(pattern, layout_clean):
            failures.append(f"Prohibited layout detected: {label}")

    placeholder_hits = []
    for label, pattern in PLACEHOLDER_PATTERNS.items():
        if re.search(pattern, clean):
            placeholder_hits.append(label)
            failures.append(f"Unresolved content detected: {label}")
    qa["unresolved_placeholders"] = placeholder_hits

    unsafe_hits = []
    for label, pattern in unsafe_patterns.items():
        if re.search(pattern, clean):
            unsafe_hits.append(label)
            failures.append(f"Unsafe candidate-facing content: {label}")
    qa["unsafe_claims"] = unsafe_hits

    for section in REQUIRED_SECTIONS:
        add_failure(
            failures,
            bool(re.search(rf"\\section\{{{re.escape(section)}\}}", clean)),
            f"Missing required section: {section}",
        )

    add_failure(
        failures,
        not re.search(r"\\section\{(?:Selected Project|Academic Projects)\}", clean),
        "Resume may not contain a separate Selected Project or Academic Projects section",
    )

    project_matches = re.findall(
        r"\\newcommand\{\\SelectedProjectID\}\{([^{}]+)\}",
        clean,
    )
    selected_project_id = project_matches[0] if len(project_matches) == 1 else ""
    qa["selected_project_id"] = selected_project_id or None
    project_section_count = len(re.findall(r"\\section\{Projects\}", clean))
    required_projects = max(0, int(CONTRACT.required_selected_projects))
    add_failure(
        failures,
        project_section_count == (1 if required_projects else 0),
        "Projects section must match the active resume contract",
    )
    ready_project_ids = {
        item.get("id")
        for item in evidence.get("projects", [])
        if isinstance(item, dict) and item.get("id") and item.get("resume_content") and item.get("status") not in {"hold", "missing"}
    }
    add_failure(failures, len(project_matches) == (1 if required_projects else 0),
                f"Resume must declare {min(required_projects, 1)} SelectedProjectID slot(s)")
    if required_projects:
        add_failure(failures, selected_project_id in ready_project_ids,
                    f"Selected project is not resume-ready: {selected_project_id or '<missing>'}")
        qa["selected_project"] = validate_selected_project(source, evidence, selected_project_id, failures)
    else:
        qa["selected_project"] = None
    second_matches = re.findall(r"\\newcommand\{\\SecondProjectID\}\{([^{}]+)\}", clean)
    second_id = second_matches[0] if len(second_matches) == 1 else ''
    qa['selected_project_ids'] = [value for value in (selected_project_id, second_id) if value]
    qa['project_count'] = len(project_matches) + len(second_matches)
    add_failure(failures, len(second_matches) == (1 if required_projects >= 2 else 0),
                f"Resume must declare {max(0, required_projects - 1)} SecondProjectID slot(s)")
    if required_projects >= 2:
        add_failure(failures, second_id in ready_project_ids and second_id != selected_project_id,
                    'Resume must contain distinct registered selected projects')
        second_failures = []
        qa['second_project'] = validate_selected_project(source, evidence, second_id, second_failures, second=True)
        failures.extend('Second project: ' + message for message in second_failures)
        if second_id:
            add_failure(failures, second_id in evidence_ids_from_source(source, evidence, []), 'Second project evidence is missing')
    else:
        qa['second_project'] = None

    section_positions = []
    for section in required_order:
        match = re.search(rf"\\section\{{{re.escape(section)}\}}", clean)
        section_positions.append(match.start() if match else -1)
    add_failure(
        failures,
        all(
            left < right
            for left, right in zip(section_positions, section_positions[1:])
            if left >= 0 and right >= 0
        ),
        "Required sections are not in the expected ATS reading order",
    )

    required_source_values = CONTRACT.required_source_values
    for value in required_source_values:
        add_failure(failures, value in clean, f"Missing required stable content: {value}")

    document_body = clean.split(r"\begin{document}", 1)[-1]
    document_body = expand_zero_argument_macros(clean, document_body)
    total_words = approximate_visible_words(document_body)
    qa["source_page_word_counts"] = [total_words]
    low, high = CONTRACT.word_bounds
    if total_words < low * CONTRACT.pages:
        if CONTRACT.relaxed_min_words:
            warnings.append(f"Base resume has {total_words} approximate words; add supported details before using it for an application")
            qa["review_required"] = True
        else:
            failures.append(f"Source is too sparse for {CONTRACT.describe_pages()} ({total_words} approximate words)")
    elif total_words > high * CONTRACT.pages:
        failures.append(f"Source is too dense for {CONTRACT.describe_pages()} ({total_words} approximate words); cut content, never fonts or margins")

    tailored = source_path != BASE_TEMPLATE
    evidence_map_path = args.evidence_map
    if tailored and evidence_map_path is None:
        sibling = source_path.parent / "evidence-map.yml"
        evidence_map_path = sibling
    evidence_map_result = validate_evidence_map(
        evidence_map_path.resolve() if evidence_map_path else None,
        evidence,
        selected_project_id,
        source_evidence_ids,
        source_path,
        failures,
    )
    qa["evidence_map"] = evidence_map_result

    if args.visual_review in {"pass", "fail"}:
        add_failure(
            failures,
            isinstance(args.visual_reviewer, str) and len(args.visual_reviewer.strip()) >= 3,
            "A visual reviewer identity is required for a pass/fail visual review",
        )
        if args.visual_reviewer:
            qa["visual_reviewed_at"] = datetime.now(timezone.utc).isoformat()

    if args.compile:
        with tempfile.TemporaryDirectory(prefix="career-resume-build-") as temporary:
            build_dir = Path(temporary)
            pdf_path, log_text, compile_detail = compile_latex(source_path, build_dir)
            if pdf_path is None:
                failures.append(f"LaTeX compilation failed: {compile_detail[-1500:]}")
            else:
                qa["compile_ok"] = True
                overflow_matches = re.findall(r"Overfull \\[hv]box", log_text)
                qa["overflow_count"] = len(overflow_matches)
                if overflow_matches:
                    failures.append(f"LaTeX reported {len(overflow_matches)} overfull box(es)")
                if re.search(r"LaTeX Warning:.*(?:undefined|Rerun)", log_text, re.IGNORECASE):
                    failures.append("LaTeX reported unresolved reference or rerun warnings")

                output_path = (
                    args.output.expanduser().resolve()
                    if args.output
                    else source_path.with_suffix(".pdf")
                )
                render_dir = (
                    args.render_dir.expanduser().resolve()
                    if args.render_dir
                    else output_path.parent / f"{output_path.stem}-preview"
                )
                try:
                    inspection = inspect_pdf(pdf_path, render_dir)
                except (RuntimeError, json.JSONDecodeError) as exc:
                    failures.append(f"PDF inspection failed: {exc}")
                    inspection = {}

                page_count = inspection.get("page_count")
                pages = inspection.get("pages") if isinstance(inspection.get("pages"), list) else []
                all_text = "\n".join(
                    str(page.get("text", "")) for page in pages if isinstance(page, dict)
                )
                qa["page_count"] = page_count
                if args.studio_layout and pages:
                    qa["layout"] = measure_pages(pdf_path, render_dir, contract=CONTRACT)
                    add_failure(failures, qa["layout"]["full_pages"], f"Studio PDF must fill {CONTRACT.describe_pages()} of {CONTRACT.paper.title()} with readable spacing and no large gaps")
                qa["pdf_metadata"] = (
                    inspection.get("metadata")
                    if isinstance(inspection.get("metadata"), dict)
                    else {}
                )
                minimum_page_chars = CONTRACT.min_pdf_chars_per_page
                if minimum_page_chars is None:
                    minimum_page_chars = 50 if CONTRACT.relaxed_min_words else MIN_CHARS
                qa["pdf_text_extractable"] = len(all_text.strip()) >= minimum_page_chars * max(1, CONTRACT.pages)
                qa["page2_text_characters"] = (
                    pages[1].get("text_characters") if len(pages) >= 2 and isinstance(pages[1], dict) else None
                )
                qa["render_dir"] = str(render_dir)
                add_failure(failures, page_count == CONTRACT.pages, f"Compiled PDF must have exactly {CONTRACT.pages} page(s); found {page_count}")
                add_failure(
                    failures,
                    qa["pdf_text_extractable"],
                    "Compiled PDF text is missing or not sufficiently extractable",
                )
                add_failure(
                    failures,
                    qa["pdf_metadata"].get("author") == CONTRACT.pdf_author
                    and str(qa["pdf_metadata"].get("title", "")).startswith(CONTRACT.pdf_author),
                    "Compiled PDF metadata author/title does not match the candidate",
                )
                qa["ai_marks"]["pdf"] = pdf_leftovers(pdf_path)
                add_failure(failures, not qa["ai_marks"]["pdf"],
                            "Compiled PDF still carries metadata beyond title and author: " + ", ".join(qa["ai_marks"]["pdf"]))

                for page in pages:
                    if not isinstance(page, dict):
                        continue
                    width = float(page.get("width_points", 0))
                    height = float(page.get("height_points", 0))
                    add_failure(
                        failures,
                        CONTRACT.is_paper(width, height),
                        f"PDF page {page.get('number')} is not {CONTRACT.paper.title()} ({width:.1f} x {height:.1f} pt)",
                    )
                    add_failure(
                        failures,
                        int(page.get("text_characters", 0)) >= minimum_page_chars,
                        f"PDF page {page.get('number')} is suspiciously sparse",
                    )

                # Tectonic's PDF extractor may emit typographic ligatures such
                # as "Certiﬁcations". NFKC restores their ordinary spelling.
                normalized_pdf_text = unicodedata.normalize("NFKC", all_text)
                normalized_pdf_text = (
                    normalized_pdf_text.replace("\u2013", "-")
                    .replace("\u2014", "-")
                    .replace("\u2212", "-")
                    .replace("\u00a0", " ")
                )
                missing_pdf_headings = [
                    heading for heading in REQUIRED_SECTIONS if heading not in normalized_pdf_text
                ]
                qa["required_headings_present"] = not missing_pdf_headings
                if missing_pdf_headings:
                    failures.append(
                        "PDF text is missing headings: " + ", ".join(missing_pdf_headings)
                    )
                if re.search(
                    r"\b(?:IDENTITY|CONTACT|EXP|EDU|CERT|PROJ|WORK|TRAINING|COURSEWORK|SKILL|LANG|HOLD)-[A-Z0-9-]+\b",
                    all_text,
                ):
                    failures.append("Evidence IDs leaked into visible PDF text")
                for label, pattern in unsafe_patterns.items():
                    if re.search(pattern, all_text):
                        failures.append(f"Unsafe content appears in compiled PDF: {label}")

                preview_files = sorted(path.name for path in render_dir.glob("page-*.png"))
                expected_preview_files = CONTRACT.preview_files
                add_failure(
                    failures,
                    preview_files == expected_preview_files,
                    "Preview directory must contain exactly " + ", ".join(expected_preview_files),
                )
                qa["preview_sha256"] = {
                    filename: sha256(render_dir / filename)
                    for filename in expected_preview_files
                    if (render_dir / filename).is_file()
                }

                # A visible invariant may legitimately wrap across two rendered lines,
                # so substring checks run against whitespace-collapsed text. Checks that
                # depend on line structure keep using normalized_pdf_text.
                flattened_pdf_text = re.sub(r"\s+", " ", normalized_pdf_text).strip()
                compact_pdf_text = re.sub(r"\s+", "", flattened_pdf_text).casefold()
                first_page_text = (
                    str(pages[0].get("text", "")) if pages and isinstance(pages[0], dict) else ""
                )
                header_values = CONTRACT.header_values()
                first_lines = [line.strip() for line in first_page_text.splitlines() if line.strip()]
                flattened_first = re.sub(r"\s+", " ", first_page_text)
                # PDF text extraction splits some kerned letter pairs ("T echnological",
                # "A WS"), so each value is also accepted with whitespace removed.
                def compact(value: str) -> str:
                    return re.sub(r"\s+", "", str(value)).casefold()

                compact_first = compact(flattened_first)
                add_failure(
                    failures,
                    bool(first_lines) and header_values and compact(first_lines[0]) == compact(header_values[0])
                    and all(value in flattened_first or compact(value) in compact_first for value in header_values[1:]),
                    "Visible PDF header identity/contact does not match the canonical profile",
                )
                for value in CONTRACT.visible_invariants:
                    add_failure(
                        failures,
                        value in flattened_pdf_text or compact(value) in compact_pdf_text,
                        f"Compiled PDF is missing visible invariant: {value}",
                    )
                # Any employer or degree that is printed must carry its registered title and dates.
                for anchor, accepted in CONTRACT.conditional_visible_pairs.items():
                    if anchor in flattened_pdf_text:
                        add_failure(
                            failures,
                            any(
                                re.sub(r"\s+", "", value).casefold() in compact_pdf_text
                                for value in accepted
                            ),
                            f"Compiled PDF prints {anchor} without a registered title/date line ({accepted[0]})",
                        )

                # A project heading starts its line; a stack may sit beside it, so match the line's
                # start. A registered bullet that names its own project ("The X app was my final
                # year project") is not a second heading.
                def heading_count(title: str) -> int:
                    return sum(compact(line).startswith(compact(title)) for line in normalized_pdf_text.splitlines())

                if required_projects:
                    expected_project_title = (qa.get("selected_project") or {}).get("title")
                    add_failure(
                        failures,
                        isinstance(expected_project_title, str) and heading_count(expected_project_title) == 1,
                        "Compiled PDF must show the registered selected-project title exactly once",
                    )
                if required_projects >= 2:
                    second_title = (qa.get('second_project') or {}).get('title')
                    add_failure(failures, isinstance(second_title, str) and heading_count(second_title) == 1,
                                'Compiled PDF must show the registered second-project title exactly once')
                heading_positions = [normalized_pdf_text.find(heading) for heading in required_order]
                qa["text_order_ok"] = all(
                    left >= 0 and right >= 0 and left < right
                    for left, right in zip(heading_positions, heading_positions[1:])
                )
                add_failure(
                    failures,
                    qa["text_order_ok"],
                    "Compiled PDF headings are not in the expected ATS reading order",
                )

                numeric_text = re.sub(
                    r"\b\S+@\S+\b|https?://\S+|linkedin\.com/\S+",
                    "",
                    normalized_pdf_text.replace(phone, "") if phone else normalized_pdf_text,
                    flags=re.IGNORECASE,
                )
                visible_numbers = set(
                    re.findall(r"(?<![A-Za-z0-9])\d+(?:,\d{3})*(?:\.\d+)?", numeric_text)
                )
                unsupported_numbers = sorted(visible_numbers - ALLOWED_VISIBLE_NUMBERS)
                qa["visible_numbers"] = sorted(visible_numbers)
                if unsupported_numbers:
                    failures.append(
                        "Compiled PDF contains unregistered numeric claims/values: "
                        + ", ".join(unsupported_numbers)
                    )

                qa["compiled_pdf_sha256"] = sha256(pdf_path)
                qa["pdf"] = str(output_path)
                qa["published_pdf"] = False
                if not failures and args.visual_review != "fail":
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(pdf_path, output_path)
                    qa["published_pdf"] = True
                    qa["pdf_sha256"] = sha256(output_path)
                    add_failure(
                        failures,
                        output_path.stat().st_mtime >= source_path.stat().st_mtime,
                        "Compiled PDF is older than its LaTeX source",
                    )
    else:
        warnings.append("Compilation and PDF gates were skipped")

    if args.visual_review == "fail":
        failures.append("Manual visual review failed")
    elif args.visual_review == "pending":
        warnings.append("Manual visual review is pending; artifact is not release-ready")

    qa["release_ready"] = (
        not failures
        and args.compile
        and args.visual_review == "pass"
        and bool(args.visual_reviewer)
        and qa["compile_ok"]
        and qa["page_count"] == CONTRACT.pages
        and qa.get("published_pdf") is True
        and not qa.get("review_required", False)
        and (CONTRACT.required_selected_projects == 0 or
             (qa.get("selected_project") or {}).get("content_matches_registry") is True)
        and qa.get("evidence_map", {}).get("all_candidate_claims_grounded") is True
        and set(qa.get("preview_sha256", {})) == set(CONTRACT.preview_files)
    )
    if failures:
        qa["status"] = "FAIL"
        exit_code = 1
    elif qa["release_ready"]:
        qa["status"] = "PASS"
        exit_code = 0
    else:
        qa["status"] = "AUTOMATED_PASS_MANUAL_PENDING"
        exit_code = 0
    return finish(args.qa_json, qa, exit_code)


def finish(qa_json_path: Path | None, qa: dict[str, Any], exit_code: int) -> int:
    if qa_json_path:
        path = qa_json_path.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(qa, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for warning in qa.get("warnings", []):
        print(f"WARN: {warning}")
    for failure in qa.get("failures", []):
        print(f"FAIL: {failure}")
    print(
        f"{qa.get('status')}: project={qa.get('selected_project_id')} "
        f"pages={qa.get('page_count')} release_ready={qa.get('release_ready')}"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
