"""Pre-apply claim assurance per job: validator report, review decisions and cached score.

Claims come from the current draft source via validate_resume.build_report; the
60/40 resume_items rows overlay their origin and keep/remove decision. Rows no
source line cites (predicted skills, content edited out by hand) appear as their
own claims so a decision is always visible. The summary counts claims by evidence
status and review rows by decision.
"""

from __future__ import annotations

import re
from typing import Any

# Most cautious decision wins when several review rows cite the same claim line.
REVIEW_PRIORITY = ("removed", "pending", "kept")


def _human_text(source: str, macros: dict, line_number: int) -> str:
    """The claim line as a person reads it: macro values expanded, LaTeX stripped."""
    from validate_resume import normalize_latex_text

    lines = source.splitlines()
    if not 1 <= line_number <= len(lines):
        return ""
    raw = lines[line_number - 1].strip()
    definition = re.match(r"\\newcommand\{\\([A-Za-z@]+)\}", raw)
    if definition:
        return normalize_latex_text(macros.get(definition.group(1), ""))
    expanded = raw
    for name, value in sorted(macros.items(), key=lambda item: len(item[0]), reverse=True):
        expanded = re.sub(r"\\" + re.escape(name) + r"\b", lambda _: value, expanded)
    return normalize_latex_text(expanded)


def _decision_of(linked: list) -> str | None:
    decisions = {row["decision"] for row in linked}
    return next((decision for decision in REVIEW_PRIORITY if decision in decisions), None)


def build_assurance(service, studio, job_id: str) -> dict[str, Any]:
    from validate_resume import build_report, extract_zero_argument_macros

    job = service.w.get_job(job_id)
    rows = studio.items(job_id)
    by_tag: dict[str, list] = {}
    for row in rows:
        by_tag.setdefault("resume_items:" + row["id"], []).append(row)
        if row["evidence_id"]:
            by_tag.setdefault(row["evidence_id"], []).append(row)

    claims: list[dict[str, Any]] = []
    note = None
    score = None
    linked: set[str] = set()
    try:
        draft = studio.get(job_id)
    except ValueError:
        draft = None
    if draft is None:
        note = "No resume draft yet. Open Resume Studio for this job and the claim report appears here."
    else:
        source = draft["source"]
        macros = extract_zero_argument_macros(source)
        tex_path = service.w.root / "data/output" / draft["file_root"] / "resume.tex"
        report = build_report(service.w.db_path, tex_path, evidence=service.w.evidence(), source=source)
        for claim in report["claims"]:
            text = _human_text(source, macros, claim["line"])
            if not text:
                continue  # An emptied macro (e.g. an unused third bullet) renders nothing to review.
            linked_rows = [row for tag in claim["evidence_ids"] for row in by_tag.get(tag, [])]
            linked.update(row["id"] for row in linked_rows)
            status = claim["status"]
            claims.append(
                {
                    "text": text,
                    "section": claim["section"],
                    "origin": status if status in ("verified", "predicted") else None,
                    "evidence_status": status,
                    "confidence": claim["confidence"],
                    "decision": _decision_of(linked_rows),
                    "items": [row["id"] for row in linked_rows],
                    "evidence_ids": claim["evidence_ids"],
                    "line": claim["line"],
                    "note": claim["note"],
                }
            )
        match = draft.get("match") or {}
        score = (match.get("ats_readiness") or {}).get("score")

    skill_section = "Skills" if draft and r"\section{Skills}" in draft["source"] else "Technical Skills"
    for row in rows:
        if row["id"] in linked:
            continue
        section = "Projects" if row["section"] == "projects" else skill_section
        text = row["content"].get("title", "") if row["section"] == "projects" else row["content"]
        status = "predicted" if row["origin"] == "predicted" else "verified"
        # Same shape as a printed claim: the page reads `items` on every claim (it crashed on
        # 23 Sep when a tailoring proposed more projects than the page holds), and the row's
        # own id lets her keep or remove it here too.
        claims.append(
            {
                "text": text,
                "section": section,
                "origin": row["origin"],
                "evidence_status": status,
                "confidence": {"verified": 100, "predicted": 60}[status],
                "decision": row["decision"],
                "items": [row["id"]],
                "evidence_ids": [row["evidence_id"]] if row["evidence_id"] else [],
                "line": None,
                "note": "Suggested by tailoring; not printed on the current page.",
            }
        )

    summary = {
        "verified": sum(1 for claim in claims if claim["evidence_status"] == "verified"),
        "predicted": sum(1 for claim in claims if claim["evidence_status"] == "predicted"),
        "missing": sum(1 for claim in claims if claim["evidence_status"] == "missing"),
        "kept": sum(1 for row in rows if row["decision"] == "kept"),
        "removed": sum(1 for row in rows if row["decision"] == "removed"),
        # What still waits for her: suggestions only. Registry items start "pending" in the table
        # but never need a decision (on 24 Sep a tailored draft showed "76 still waiting" for 5).
        "pending": sum(1 for row in rows if row["decision"] == "pending" and row["origin"] == "predicted"),
    }
    return {
        "job_id": job_id,
        "company": job["company"],
        "role": job["title"],
        "generated_at": service.now(),
        "summary": summary,
        "claims": claims,
        "score": score,
        "note": note,
    }
