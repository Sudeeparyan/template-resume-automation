"""Deterministic local AI for marked synthetic practice workspaces."""

from __future__ import annotations

import re
from pathlib import Path

from backend.ai.agents.graph import AgentError
from backend.ai.agents.schemas import (
    AgentTurn,
    CoverLetterDraft,
    FitAnalysis,
    HiringManagerReview,
    LetterAudit,
    PostingFields,
    ProfileChangeSet,
    ProfileProposal,
    ResumeChangeSet,
    ResumeEdit,
    TailoringResult,
)
from backend.services.demo import PRACTICE_POSTINGS, synthetic_demo

ID = "demo"
MODEL = "guided-practice"


def _report(summary: str, report: str) -> dict:
    return {
        "summary": summary,
        "report": report,
        "sources": [],
        "limitations": ["Simulated practice content; no company research or live vacancy check was performed."],
    }


def _discovery() -> dict:
    posting = PRACTICE_POSTINGS[2]
    return {
        "summary": "Added one simulated practice posting. No web search was performed.",
        "jobs": [{
            **posting,
            "requisition_id": "PRACTICE-003",
            "verification": "Synthetic posting supplied by the local practice provider.",
            "fit": "Matches the practice profile's Python, SQL and Git evidence.",
            "gap": "No live employer or requirement check was performed.",
            "legal_presence": "Fictional practice employer.",
            "company_sources": [],
            "red_flags": [],
            "size_category": "unknown",
            "employee_min": None,
            "employee_max": None,
            "sponsorship_state": "unknown",
            "sponsorship_evidence": [],
            "restriction_quote": "",
            "employer_type": "unknown",
            "applicant_count": None,
            "competition_signals": {
                "posted_within_72h": False,
                "limited_syndication": False,
                "niche_match": False,
            },
        }],
        "rejected_leads": [],
        "excluded": [],
        "search_worked": True,
    }


class DemoProvider:
    id = ID
    models = (MODEL,)
    capabilities = {"structured", "web"}

    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def configured(self) -> bool:
        return synthetic_demo(self.root)

    def generate(self, prompt: str, schema: dict, *, model=MODEL, **_options) -> dict:
        if not self.configured:
            raise ValueError("The practice provider is available only in a marked synthetic demo workspace")
        properties = schema.get("properties") or {}
        if "verdict" in properties:
            return {
                **_report(
                    "Practice PDF review passed.",
                    "The local practice reviewer found selectable resume text, recognisable sections and evidence for the demonstrated requirements.",
                ),
                "verdict": "pass",
                "issues": [],
            }
        if "jobs" in properties:
            return _discovery()
        if "report" in properties:
            return _report(
                "Simulated role guidance is ready.",
                "## Practice review\n\nFocus on the requirements visible in the practice posting. Use the registered project wording, review the generated files, and record the practice outcome yourself.\n\n"
                "## What to practise\n\nExplain one project, name the tools you actually used, and describe one honest gap.\n",
            )
        raise ValueError("The practice provider supports only the guided demo workflow")


class DemoTeam:
    """Small deterministic specialist team used only by marked demo profiles."""

    def __init__(self, services):
        self.s = services
        self.served = (ID, MODEL)
        self.fallback = None
        self.fell_back = None

    def _fit(self, payload: dict) -> FitAnalysis:
        from backend.assessment import extract_requirements

        evidence = payload.get("evidence") or []
        extra = {name: (name.casefold(),) for name in ("Git", "JavaScript", "React", "pandas", "NumPy", "REST API")}
        extracted = extract_requirements(str((payload.get("job") or {}).get("description") or ""), extra)
        requirements = []
        for item in extracted:
            aliases = [item["requirement"], *(item.get("aliases") or [])]
            ids = [
                entry["id"] for entry in evidence
                if any(re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", entry.get("text", ""), re.I) for alias in aliases)
            ][:4]
            requirements.append({
                "text": item["requirement"],
                "category": item["category"],
                "excerpt": item["excerpt"],
                "status": "met" if ids else "missing",
                "evidence_ids": ids,
                "note": "Shown in the synthetic evidence." if ids else "Not shown in the synthetic evidence.",
            })
        return FitAnalysis.model_validate({
            "requirements": requirements,
            "hard_blockers": [],
            "summary": "Practice fit uses only the synthetic profile and posting.",
        })

    @staticmethod
    def _tailoring(payload: dict) -> TailoringResult:
        projects = [{
            "title": project["title"],
            "context": project.get("context", ""),
            "bullets": list(project.get("bullets") or []),
            "origin": "verified",
            "evidence_id": project["evidence_id"],
        } for project in (payload.get("verified_projects") or [])[:2]]
        skills = [{
            "name": skill["name"],
            "origin": "verified",
            "evidence_id": skill["evidence_id"],
        } for skill in payload.get("verified_skills") or []]
        return TailoringResult.model_validate({
            "projects": projects,
            "skills": skills,
            "rewrites": [],
            "rationale": "The practice provider copies registered synthetic projects and skills without adding claims.",
        })

    @staticmethod
    def _resume_chat(payload: dict) -> ResumeChangeSet:
        request = str(payload.get("request") or "")
        fields = payload.get("current_resume_fields") or {}
        evidence = payload.get("registered_evidence") or []
        if "summary" in request.casefold() and "short" in request.casefold() and fields.get("ResumeSummary") and evidence:
            shortened = re.split(r"(?<=[.!?])\s+", fields["ResumeSummary"].strip())[0]
            return ResumeChangeSet.model_validate({
                "edits": [{
                    "field": "summary",
                    "value": shortened,
                    "rationale": "Keep the first registered sentence for a shorter practice summary.",
                    "evidence_ids": [evidence[0]["id"]],
                }],
                "unsupported_requests": [],
                "summary": "Shorten the practice summary without adding a fact.",
            })
        return ResumeChangeSet.model_validate({
            "edits": [],
            "unsupported_requests": [request] if request else [],
            "summary": "Try ‘make the summary shorter’ in this practice workspace.",
        })

    @staticmethod
    def _letter(payload: dict) -> CoverLetterDraft:
        role = payload.get("role") or {}
        evidence = [entry for entry in payload.get("evidence") or [] if entry.get("kind") == "project"][:2]
        if not evidence:
            raise AgentError("The practice letter needs a registered synthetic project")
        first = evidence[0]
        paragraphs = [
            f"I am applying for the {role.get('title', 'practice role')} role at {role.get('company', 'the practice employer')}.",
            f"My synthetic project work includes: {first.get('text', '')}",
            "I would welcome the opportunity to discuss this practice application.",
        ]
        if len(evidence) > 1:
            paragraphs.insert(2, f"A second synthetic project records: {evidence[1].get('text', '')}")
        return CoverLetterDraft.model_validate({
            "body": "\n\n".join(paragraphs),
            "evidence_ids": [entry["id"] for entry in evidence],
            "unsupported_claims": [],
        })

    def run(self, agent_name: str, payload, **_options):
        self.served = (ID, MODEL)
        self.fell_back = None
        if agent_name == "fit_analyst":
            return self._fit(payload)
        if agent_name == "job_tailor":
            return self._tailoring(payload)
        if agent_name == "resume_tailor":
            return self._resume_chat(payload)
        if agent_name == "profile_curator":
            request = str(payload.get("request") or "")
            return ProfileChangeSet.model_validate({
                "proposals": [{
                    "action": "add",
                    "kind": "fact",
                    "title": "Practice answer",
                    "summary": request,
                    "rationale": "Save the user's exact practice answer for review.",
                }],
                "summary": "Review the practice profile answer before reconciling it.",
            })
        if agent_name == "posting_parser":
            text = str(payload.get("posting_text") or "")
            values = {}
            for key, label in (("company", "Company"), ("title", "Title"), ("location", "Location"), ("url", "URL")):
                match = re.search(rf"(?im)^{label}:\s*(.+)$", text)
                values[key] = match[1].strip() if match else ""
            return PostingFields.model_validate(values)
        if agent_name == "cover_letter_writer":
            return self._letter(payload)
        if agent_name == "letter_auditor":
            return LetterAudit.model_validate({"unsupported": []})
        if agent_name == "hiring_manager":
            return HiringManagerReview.model_validate({
                "must_have_signals": ["A concise account of one registered project", "Evidence for the posting's named tools"],
                "screening_questions": ["What did you build, and which part was yours?"],
                "common_rejection_reasons": ["Claims without examples", "A resume that does not address the posting"],
            })
        if agent_name == "workspace_agent":
            task = payload.get("task") or []
            latest = next((str(item.get("content") or "") for item in reversed(task) if item.get("role") == "user"), "")
            return AgentTurn.model_validate({
                "thought": "You are using the local practice assistant.",
                "action": "reply",
                "reply": "This is simulated practice AI. Use the guided checklist to tailor a sample resume, or use the editing and tracking controls directly. No live search or application was performed.",
                "suggestions": ["status", "help"],
            })
        raise AgentError(f"{agent_name} is outside the guided practice workflow")

    def run_isolated(self, agent_name: str, **payload):
        return self.run(agent_name, payload)
