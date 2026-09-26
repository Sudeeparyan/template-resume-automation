"""The resume contract every validator, fitter and screen reads.

Page count, paper size, font bounds, section order, header fields and stable
facts come from the active profile and evidence registry. Change the YAML, not
the validators.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "data/config/profile.yml"
EVIDENCE_PATH = ROOT / "data/context/evidence.yml"

PAPER_SIZES_PT = {
    "letter": (612.0, 792.0),
    "a4": (595.28, 841.89),
}

# Fixed typography for the template. The validator demands exactly these so a
# resume can never be squeezed onto the page by shrinking margins or leading.
GEOMETRY = "top=0.5in,bottom=0.5in,left=0.6in,right=0.6in"
LINE_SPREAD = "1.0"
MARGIN_IN = 0.5

# Words per page the source may hold before it is certainly too sparse/dense to fit.
WORDS_PER_PAGE = (260, 720)
# A rendered page with fewer extractable characters than this is broken, not thin.
MIN_CHARS_PER_PAGE = 900

# Claims that must never appear, however they are phrased. The two open questions
# (years total, publications) live here rather than in each validator.
GENERIC_UNSAFE_PATTERNS = {
    "unsupported work-right claim": r"(?i)\b(?:unrestricted work|no sponsorship required|full work authorization|does not require sponsorship)\b",
    "prohibited filler": r"(?i)references available upon request",
    "years-of-experience total (never stated)": r"(?i)\b\d+\s*\+?\s*(?:years?|yrs)(?:\s+of)?\s+(?:combined\s+|professional\s+|industry\s+|hands-on\s+|relevant\s+)?experience\b",
    "publication claim (not on record)": r"(?i)\b(?:ICCV|CVPR|NeurIPS|ECCV|arXiv|publication|published|under review|first author|co-author)\b",
    "GPA (not on record)": r"(?i)\bGPA\b",
    "certification (none on record)": r"(?i)\bcertif(?:ied|ication)\b",
    "LinkedIn (none on record)": r"(?i)linkedin\.com",
}


def _load_yaml(path: Path) -> dict[str, Any]:
    """PyYAML when the venv is active; the macOS Ruby fallback for a bare interpreter."""
    try:
        import yaml
    except ImportError:
        import json
        import shutil
        import subprocess

        ruby = shutil.which("ruby")
        if not ruby:
            raise RuntimeError("Install the workspace requirements (PyYAML), or use Ruby for the fallback YAML parser")
        program = ('require "yaml"; require "json"; value = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: false); '
                   "STDOUT.write(JSON.generate(value))")
        result = subprocess.run([ruby, "-e", program, str(path)], text=True, capture_output=True, check=False)
        if result.returncode:
            raise RuntimeError(f"Unable to parse {path}: {result.stderr.strip() or 'unknown YAML parse error'}")
        return json.loads(result.stdout) or {}
    with path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def _dash_variants(text: str) -> list[str]:
    """Return the LaTeX and the rendered-PDF spelling of a date range."""
    plain = re.sub(r"\s*[-\u2013\u2014]+\s*", " - ", text)
    return [plain.replace(" - ", " -- "), plain]


@dataclass
class Contract:
    paper: str = "letter"
    pages: int = 1
    min_body_pt: float = 10.0
    max_body_pt: float = 11.0
    geometry: str = GEOMETRY
    line_spread: str = LINE_SPREAD
    required_sections: tuple[str, ...] = ("Education", "Technical Skills", "Professional Experience", "Projects")
    allowed_section_orders: tuple[tuple[str, ...], ...] = ()
    header_fields: tuple[str, ...] = ("full_name", "phone", "email", "portfolio_url", "github")
    candidate: dict[str, Any] = field(default_factory=dict)
    cut_order: tuple[str, ...] = ()
    required_selected_projects: int = 2
    required_artifacts: tuple[str, ...] = ()
    relaxed_min_words: bool = False
    min_pdf_chars_per_page: int | None = None
    # Derived from the registry.
    required_source_values: tuple[str, ...] = ()
    visible_invariants: tuple[str, ...] = ()
    allowed_visible_numbers: frozenset[str] = frozenset()
    unsafe_patterns: dict[str, str] = field(default_factory=dict)
    conditional_visible_pairs: dict[str, list[str]] = field(default_factory=dict)

    # --- paper -----------------------------------------------------------
    def page_size_pt(self) -> tuple[float, float]:
        return PAPER_SIZES_PT[self.paper]

    def is_paper(self, width: float, height: float, tolerance: float = 3.0) -> bool:
        expected_w, expected_h = self.page_size_pt()
        return abs(width - expected_w) <= tolerance and abs(height - expected_h) <= tolerance

    @property
    def documentclass_option(self) -> str:
        return {"letter": "letterpaper", "a4": "a4paper"}[self.paper]

    def documentclass_pattern(self) -> str:
        return r"\\documentclass\[" + self.documentclass_option + r",10pt\]\{article\}"

    @property
    def preview_files(self) -> list[str]:
        return [f"page-{n:02d}.png" for n in range(1, self.pages + 1)]

    @property
    def word_bounds(self) -> tuple[int, int]:
        return WORDS_PER_PAGE

    @property
    def pdf_author(self) -> str:
        return str(self.candidate.get("full_name", ""))

    def header_values(self) -> list[str]:
        return [str(self.candidate.get(key, "")).strip() for key in self.header_fields if self.candidate.get(key)]

    def describe_pages(self) -> str:
        return "one page" if self.pages == 1 else f"{self.pages} pages"


def _numbers_in(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z0-9])\d+(?:,\d{3})*(?:\.\d+)?", text))


@lru_cache(maxsize=4)
def load_contract(profile_path: str | None = None, evidence_path: str | None = None) -> Contract:
    profile = _load_yaml(Path(profile_path) if profile_path else PROFILE_PATH)
    evidence = _load_yaml(Path(evidence_path) if evidence_path else EVIDENCE_PATH)
    rc = profile.get("resume_contract", {})
    bc = profile.get("batch_contract", {})
    candidate = profile.get("candidate", {})

    orders = rc.get("section_order_by_track", {}) or {}
    allowed_orders = tuple(dict.fromkeys(tuple(order) for order in orders.values())) or (
        tuple(rc.get("required_sections", [])),
    )

    registry = {item["id"]: item for group in ("claims", "projects") for item in evidence.get(group, []) if isinstance(item, dict) and item.get("id")}
    immutable_ids = (evidence.get("immutable_across_resumes") or {}).get("claim_ids", [])

    # Stable facts every resume must carry: identity, contacts, and the employers /
    # degrees the registry marks immutable. Dates appear both as LaTeX ("--") and PDF text.
    source_values: list[str] = []
    visible: list[str] = []
    for claim_id in immutable_ids:
        claim = registry.get(claim_id, {})
        if claim.get("status") in {"hold", "missing"}:
            continue
        if claim.get("category") in {"identity", "contact"} and claim.get("value"):
            source_values.append(str(claim["value"]))
            visible_value = str(claim["value"])
            # A hyperlink's visible label may omit the scheme while the LaTeX
            # source retains the exact registered URL in \href.
            if visible_value.startswith(("https://", "http://")):
                visible_value = re.sub(r"^https?://", "", visible_value)
            visible.append(visible_value)
        for key in ("employer", "institution"):
            if claim.get(key):
                source_values.append(str(claim[key]))
                visible.append(str(claim[key]))
        if claim.get("dates"):
            latex_form, pdf_form = _dash_variants(str(claim["dates"]))
            source_values.append(latex_form)
            visible.append(pdf_form)
    if candidate.get("full_name"):
        source_values.append("pdfauthor={" + candidate["full_name"] + "}")

    # Whenever an employer or degree is printed, one of its registered "title dates" or
    # "name dates" lines must be printed with it (Soliton has two roles, so any one counts).
    pairs: dict[str, list[str]] = {}
    for claim in registry.values():
        if claim.get("category") in {"employment", "education"} and claim.get("status") not in {"hold", "missing"} and claim.get("dates"):
            anchor = claim.get("employer") or claim.get("institution")
            if not anchor:
                continue
            dates = _dash_variants(str(claim["dates"]))[1]
            accepted = [f"{anchor} {dates}"]
            for key in ("title", "degree_as_supplied"):
                if claim.get(key):
                    accepted.append(f"{claim[key]} {dates}")
            pairs.setdefault(str(anchor), []).extend(accepted)

    # Every number the PDF may show must come from registered wording.
    numbers: set[str] = set()
    for item in registry.values():
        if item.get("status") in {"hold", "missing"}:
            continue
        for key in ("value", "dates", "title", "employer", "institution", "degree_as_supplied", "grade"):
            if item.get(key):
                numbers |= _numbers_in(str(item[key]))
        for fact in item.get("approved_facts", []) or []:
            numbers |= _numbers_in(str(fact))
        content = item.get("resume_content") or {}
        for text in [content.get("title", ""), content.get("context", ""), *(content.get("bullets") or [])]:
            numbers |= _numbers_in(str(text))
    # Ordinal-like small numbers used by typography ("1st", list counts) are harmless.
    numbers |= {str(n) for n in range(0, 13)}

    unsafe = dict(GENERIC_UNSAFE_PATTERNS)
    # GPA, certifications, LinkedIn and publications are banned only while the registry
    # holds none that may be used. A
    # profile whose own documents register one may print it.
    def usable(item):
        return item.get("status") not in {"hold", "missing"}

    claims = [c for c in registry.values() if usable(c)]
    # A total-experience statement is safe only when the registry explicitly
    # supplies one.
    if any(
        c.get("id") == "EXP-TOTAL-001"
        or (
            c.get("category") in {"experience", "employment_total"}
            and re.search(r"(?i)\b\d+\s*\+?\s*(?:years?|yrs)\b", str(c.get("value") or ""))
        )
        for c in claims
    ):
        unsafe.pop("years-of-experience total (never stated)", None)
    if any(c.get("category") == "certification" for c in claims):
        unsafe.pop("certification (none on record)", None)
    if any(c.get("category") == "publication" for c in claims):
        unsafe.pop("publication claim (not on record)", None)
    if any(c.get("category") in {"education", "education_grade"} and c.get("grade") for c in claims):
        unsafe.pop("GPA (not on record)", None)
    if candidate.get("linkedin") and any(c.get("id", "").startswith("CONTACT-LINKEDIN") for c in claims):
        unsafe.pop("LinkedIn (none on record)", None)
    never = registry.get("SKILL-NEVER-001", {}).get("approved_facts", []) or []
    if never:
        unsafe["skill not in the bank (never claim)"] = r"(?i)\b(?:" + "|".join(re.escape(str(s)) for s in never) + r")\b"
    filler = [str(f) for f in rc.get("prohibited_filler", []) if str(f).lower() != "references available upon request"]
    if filler:
        unsafe["banned filler (08-voice.md)"] = r"(?i)\b(?:" + "|".join(re.escape(f) for f in filler) + r")\b"
    for hold in [c for c in registry.values() if c.get("status") == "hold" and c.get("category") == "employment"]:
        # A superseded role title remains private evidence, never resume copy.
        title = str(hold.get("title") or "").strip()
        if title:
            unsafe[f"held employment title: {hold.get('id', 'unknown')}"] = r"(?i)\b" + re.escape(title) + r"\b"

    return Contract(
        paper=str(rc.get("paper", "letter")).lower(),
        pages=int(rc.get("required_pages", 1)),
        min_body_pt=float(rc.get("minimum_body_font_pt", 10)),
        max_body_pt=float(rc.get("maximum_body_font_pt", 11)),
        geometry=str(rc.get("geometry", GEOMETRY)),
        line_spread=str(rc.get("line_spread", LINE_SPREAD)),
        required_sections=tuple(rc.get("required_sections", [])),
        allowed_section_orders=allowed_orders,
        header_fields=tuple(rc.get("header_fields", ("full_name", "phone", "email", "portfolio_url", "github"))),
        candidate=candidate,
        cut_order=tuple(rc.get("cut_order", [])),
        required_selected_projects=int(rc.get("required_selected_projects", 2)),
        required_artifacts=tuple(bc.get("required_artifacts", [])),
        relaxed_min_words=bool(rc.get("relaxed_min_words", False)),
        min_pdf_chars_per_page=(int(rc["min_pdf_chars_per_page"]) if rc.get("min_pdf_chars_per_page") is not None else None),
        required_source_values=tuple(dict.fromkeys(source_values)),
        visible_invariants=tuple(dict.fromkeys(visible)),
        allowed_visible_numbers=frozenset(numbers),
        unsafe_patterns=unsafe,
        conditional_visible_pairs=pairs,
    )


def contract_for(root: Path, market: str | None = None) -> Contract:
    """Contract for a workspace, with a job market's paper and page choices."""
    from dataclasses import replace

    root = Path(root)
    contract = load_contract(str(root / "data/config/profile.yml"), str(root / "data/context/evidence.yml"))
    if not market:
        return contract
    from backend.countries import pack_for, target_markets_for

    pack = pack_for(root, market)
    profile = _load_yaml(root / "data/config/profile.yml")
    choices = (profile.get("resume_contract_by_market") or {}).get(market) or {}
    primary = target_markets_for(root)[0]
    pages = contract.pages if market == primary else 1
    return replace(contract, paper=pack.paper, pages=int(choices.get("required_pages", pages)))
