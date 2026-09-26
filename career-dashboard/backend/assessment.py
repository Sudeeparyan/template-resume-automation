"""Grounded requirement extraction and separate resume document assessments."""

from __future__ import annotations

import hashlib
import json
import re


SCORING_VERSION = "career-assessment-v6"  # v4: a section heading in the JD is not a requirement; v5: requirement lists re-extracted per version; v6: the job's verified requirement matrix (services/fit.py) when it has one
ALIASES = {
    "Power BI": ("power bi", "powerbi"),
    "SQL": ("sql", "structured query language"),
    "Python": ("python",),
    "R": ("r programming", " r "),
    "AI": ("artificial intelligence", " ai "),
    "ML": ("machine learning", " ml "),
    "RAG": ("retrieval augmented generation", "retrieval-augmented generation", "rag"),
    "API": ("api", "apis", "application programming interface"),
    "AWS": ("aws", "amazon web services"),
    "FAISS": ("faiss",),
    "TF-IDF": ("tf-idf", "tfidf"),
    "SHAP": ("shap",),
    "DBSCAN": ("dbscan",),
    "Excel": ("excel", "spreadsheets"),
    "Tableau": ("tableau",),
    "Power Query": ("power query", "m query"),
    "DAX": ("dax",),
    "data quality": ("data quality", "data validation"),
    "dashboards": ("dashboard", "dashboards"),
    "stakeholders": ("stakeholder", "stakeholders"),
    # The skill, not the word: "Requirements:" heads most postings and is not something to meet.
    "requirements": ("business requirements", "requirements gathering", "gather requirements", "gathering requirements", "brd"),
    "ETL": ("etl", "extract transform load"),
    "data modelling": ("data modelling", "data modeling"),
    "statistics": ("statistics", "statistical"),
    "communication": ("communication", "communicate", "present findings"),
}
PREFERRED = re.compile(r"\b(preferred|desirable|nice[ -]to[ -]have|bonus|advantageous)\b", re.I)
RESPONSIBILITY = re.compile(r"\b(responsibilities|you will|will be responsible|responsible for|day[ -]to[ -]day)\b", re.I)
REQUIRED = re.compile(r"\b(required|must|essential|minimum|need to|you have|proficien|experience with|knowledge of)\b", re.I)
# A line that only introduces a list ("Helpful Though Not Required:", "Minimum Qualifications")
# is not something a resume can show; on 23 Sep one was reported as an unmet requirement.
SECTION_NAME = re.compile(
    r"^(?:(?:minimum|basic|preferred|required|desired|additional|key|bonus)\s+)?"
    r"(?:qualifications?|requirements?|responsibilities|skills|nice[ -]to[ -]haves?|bonus points|"
    r"what you(?:'|’)?ll (?:need|bring|do)|what you will (?:need|bring|do)|about you)$",
    re.I,
)
HARD_BLOCKER = re.compile(r"without sponsorship|cannot sponsor|no sponsorship|must already have (?:the )?right to work", re.I)


def _contains(text: str, aliases: tuple[str, ...]) -> bool:
    padded = " " + text.casefold() + " "
    return any(re.search(r"(?<!\w)" + re.escape(alias.strip()) + r"(?!\w)", padded) for alias in aliases)


def _sentences(jd: str) -> list[str]:
    candidates = re.split(r"(?:\r?\n|(?<=[.!?;])[ \t]+|[•●▪])", jd)
    # Collapse only ordinary spaces and tabs: a non-breaking space (\xa0) inside a sentence
    # must survive, so every excerpt stays an exact substring of the JD it was quoted from.
    return [re.sub(r"[ \t]+", " ", item).strip(" -\t") for item in candidates if len(item.strip()) >= 2]


def _is_heading(excerpt: str) -> bool:
    words = excerpt.rstrip(":").split()
    if not words or len(words) > 8:
        return False
    return excerpt.endswith(":") or bool(SECTION_NAME.match(excerpt.rstrip(":").strip()))


def extract_requirements(jd: str, extra: dict[str, tuple[str, ...]] | None = None) -> list[dict]:
    """Extract only items carrying an exact excerpt from the supplied JD.

    ``extra`` adds terms to the fixed vocabulary: the candidate's own tool names and her
    never-claim list (services/fit.py), so a Kafka or Kubernetes requirement is noticed.
    """
    vocabulary = {**ALIASES, **(extra or {})}
    output: list[dict] = []
    seen = set()
    for excerpt in _sentences(jd):
        category = "preferred" if PREFERRED.search(excerpt) else "responsibility" if RESPONSIBILITY.search(excerpt) else "required"
        found_terms = [(label, aliases) for label, aliases in vocabulary.items() if _contains(excerpt, aliases)]
        if not found_terms and not (REQUIRED.search(excerpt) or RESPONSIBILITY.search(excerpt) or PREFERRED.search(excerpt)):
            continue
        if not found_terms and _is_heading(excerpt):
            continue
        if found_terms:
            for label, aliases in found_terms:
                key = (category, label.casefold())
                if key in seen:
                    continue
                seen.add(key)
                output.append({"category": category, "requirement": label, "excerpt": excerpt, "aliases": list(aliases)})
        else:
            requirement = excerpt[:180]
            key = (category, requirement.casefold())
            if key not in seen:
                seen.add(key)
                output.append({"category": category, "requirement": requirement, "excerpt": excerpt, "aliases": []})
    return output


def ats_readiness(resume_text: str, source: str = "", sections: tuple[str, ...] | None = None) -> dict:
    words = re.findall(r"\b\w+\b", resume_text)
    printable = sum(ch.isprintable() or ch in "\n\t" for ch in resume_text) / max(1, len(resume_text))
    extraction = 30 if len(words) >= 250 and printable >= 0.98 else 20 if len(words) >= 120 else 8
    # The headings the active resume contract requires (Education, Technical Skills, Professional
    # Experience, Projects); a plain "Skills" or "Experience" heading also counts.
    if sections is None:
        from backend.resume_contract import load_contract
        sections = load_contract().required_sections
    expected = tuple(name.casefold() for name in sections) or ("education", "skills", "experience", "projects")
    recognized = [
        name for name in expected
        if re.search(rf"(?im)^\s*(?:{re.escape(name)}|{re.escape(name.split()[-1])})\s*$", resume_text)
    ]
    sections = round(25 * len(recognized) / len(expected))
    unsafe_structure = bool(re.search(r"\\begin\{(?:tabular|multicols?|tikzpicture)\}", source))
    structure = 8 if unsafe_structure else 20
    email = bool(re.search(r"[\w.+-]+@[\w.-]+\.\w{2,}", resume_text))
    phone = bool(re.search(r"(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){8,12}", resume_text))
    name_line = bool(resume_text.strip().splitlines() and 1 < len(resume_text.strip().splitlines()[0].split()) < 7)
    contact = (5 if email else 0) + (5 if phone else 0) + (5 if name_line else 0)
    controls = any(ord(ch) < 32 and ch not in "\n\r\t" for ch in resume_text)
    broken = "�" in resume_text or controls
    encoding = 0 if broken else 10
    components = {"text_extraction": extraction, "recognizable_sections": sections, "simple_structure": structure, "contact_parsing": contact, "encoding_links": encoding}
    return {
        "score": sum(components.values()),
        "components": components,
        "recognized_sections": recognized,
        "limitations": ["Machine-readability checks do not predict recruiter or ATS ranking.", "Visual page-contract and evidence release review remain separate."],
    }



def _supporting_entries(profile_entries: list, diagnostics: list) -> list:
    """Profile entries that actually mention a requirement the JD asks for.

    The previous expression tested the whole profile text once per entry, so it
    returned either the first twenty entries or none, whatever the entry said.
    """
    wanted = tuple(
        alias
        for item in diagnostics
        if item["status"] in {"found_in_pdf", "supported_missing_from_pdf"}
        for alias in (item.get("aliases") or [item["requirement"]])
    )
    if not wanted:
        return []
    supporting = []
    for entry in profile_entries:
        if not entry.get("id") or entry.get("deleted"):
            continue
        text = f"{entry.get('title', '')}\n{entry.get('summary', '')}"
        if _contains(text, wanted):
            supporting.append(entry["id"])
    return supporting[:20]


def assess(resume_text: str, jd: str, *, source: str = "", profile_entries: list[dict] | None = None, requirements: list[dict] | None = None, sections: tuple[str, ...] | None = None) -> dict:
    if not resume_text.strip() or not jd.strip():
        raise ValueError("A readable current PDF and saved job description are required")
    requirements = requirements or extract_requirements(jd)
    profile_entries = profile_entries or []
    profile_text = "\n".join(f"{item.get('title', '')}\n{item.get('summary', '')}" for item in profile_entries if not item.get("deleted"))
    diagnostics = []
    for requirement in requirements:
        aliases = tuple(requirement.get("aliases") or (requirement["requirement"],))
        found = _contains(resume_text, aliases)
        # A matrix requirement already knows whether her evidence shows it (services/fit.py).
        supported = requirement["fit_status"] in {"met", "partial"} if requirement.get("fit_status") else _contains(profile_text, aliases)
        token_overlap = set(re.findall(r"[a-z]{4,}", requirement["requirement"].casefold())) & set(re.findall(r"[a-z]{4,}", resume_text.casefold()))
        if found:
            status = "found_in_pdf"
        elif supported:
            status = "supported_missing_from_pdf"
        elif requirement.get("fit_status") == "missing":
            status = "unsupported_or_unknown"
        elif token_overlap:
            status = "partially_supported"
        else:
            status = "unsupported_or_unknown"
        diagnostics.append({**requirement, "status": status})
    weights = {"required": 60, "responsibility": 25, "preferred": 15}
    earned = 0.0
    available = 0
    breakdown = {}
    for category, weight in weights.items():
        items = [item for item in diagnostics if item["category"] == category]
        matched = sum(item["status"] == "found_in_pdf" for item in items)
        category_score = 0 if not items else weight * matched / len(items)
        if items:
            available += weight
        earned += category_score
        breakdown[category] = {"weight": weight, "found": matched, "total": len(items), "points": round(category_score, 1)}
    blocker = bool(HARD_BLOCKER.search(jd))
    supported_count = sum(item["status"] in {"found_in_pdf", "supported_missing_from_pdf"} for item in diagnostics)
    ratio = supported_count / max(1, len(diagnostics))
    fit = "Blocked" if blocker else "Strong" if ratio >= 0.75 else "Partial" if ratio >= 0.4 else "Weak"
    readiness = ats_readiness(resume_text, source, sections)
    coverage_score = round(100 * earned / available) if available else None
    return {
        "scoring_version": SCORING_VERSION,
        "ats_readiness": readiness,
        "resume_coverage": {"score": coverage_score, "breakdown": breakdown},
        "opportunity_fit": {"label": fit, "hard_blockers": ["Work-authorisation or sponsorship constraint in the JD."] if blocker else [], "evidence_ids": _supporting_entries(profile_entries, diagnostics)},
        "keywords": diagnostics,
        "missing_keywords": [item["requirement"] for item in diagnostics if item["status"] != "found_in_pdf"],
        # Two very different kinds of gap: one is safe to add to the PDF, the
        # other would be an unevidenced claim. Reporting them as one list is
        # what made the missing-keyword section unreadable.
        "missing_supported": [item["requirement"] for item in diagnostics if item["status"] == "supported_missing_from_pdf"],
        "missing_unsupported": [item["requirement"] for item in diagnostics if item["status"] in {"partially_supported", "unsupported_or_unknown"}],
        "gaps": [item["requirement"].casefold() for item in diagnostics if item["status"] != "found_in_pdf"],
        "score": coverage_score,
        "label": "Resume coverage",
        "method": SCORING_VERSION,
        "profile_access": bool(profile_entries),
        "ai_used": False,
        "limitations": ["Coverage is deterministic phrase evidence, not a hiring probability.", "Unsupported requirements must not be inserted as keywords without candidate evidence."],
    }


class AssessmentService:
    def __init__(self, service):
        self.s = service
        self.w = service.w

    def requirements(self, job_id: str) -> list[dict]:
        from backend.services import fit

        job = self.w.get_job(job_id)
        cat = fit.catalogue(self.s)
        # The job's verified requirement matrix, when it has a current one, is the list.
        matrix = fit.cached(self.s, job_id, cat=cat)
        if matrix and any(item["status"] != "unknown" for item in matrix["matrix"]["requirements"]):
            return fit.coverage_requirements(matrix, cat)
        vocabulary = fit.vocabulary(cat)
        # Keyed on the extraction rules and her vocabulary too: after the v4 change (headings are
        # not requirements) the v3 list, heading and all, kept being reused because the posting had not changed.
        jd_hash = hashlib.sha256((SCORING_VERSION + "|" + cat["hash"] + "|" + job["description"]).encode()).hexdigest()
        with self.w.connect() as db:
            rows = db.execute("SELECT * FROM job_requirements WHERE job_id=? AND jd_hash=? ORDER BY id", (job_id, jd_hash)).fetchall()
            if not rows:
                extracted = extract_requirements(job["description"], vocabulary)
                for item in extracted:
                    if item["excerpt"] not in job["description"]:
                        raise ValueError("Requirement extraction produced an ungrounded excerpt")
                    db.execute(
                        "INSERT INTO job_requirements(job_id,jd_hash,category,requirement,excerpt,aliases,created_at) VALUES(?,?,?,?,?,?,?)",
                        (job_id, jd_hash, item["category"], item["requirement"], item["excerpt"], json.dumps(item["aliases"]), self.s.now()),
                    )
                rows = db.execute("SELECT * FROM job_requirements WHERE job_id=? AND jd_hash=? ORDER BY id", (job_id, jd_hash)).fetchall()
        return [{**dict(row), "aliases": json.loads(row["aliases"])} for row in rows]

    def run(self, job_id: str, payload: dict, source: str) -> dict:
        from backend.resume_contract import contract_for
        result = assess(payload["resume_text"], payload["job_description"], source=source, profile_entries=self.s.knowledge(), requirements=self.requirements(job_id),
                        sections=contract_for(self.s.w.root).required_sections)
        with self.w.connect() as db:
            db.execute(
                """INSERT INTO resume_assessments(job_id,draft_revision,pdf_hash,jd_hash,scoring_version,ats_readiness,resume_coverage,opportunity_fit,details,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(job_id,draft_revision,pdf_hash,jd_hash,scoring_version)
                DO UPDATE SET details=excluded.details,created_at=excluded.created_at""",
                (job_id, payload["revision"], payload["pdf_sha256"], payload["jd_sha256"], SCORING_VERSION, result["ats_readiness"]["score"], result["resume_coverage"]["score"] if result["resume_coverage"]["score"] is not None else -1, result["opportunity_fit"]["label"], json.dumps(result), self.s.now()),
            )
        return result
