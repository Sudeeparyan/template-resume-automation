"""Structured results each specialist agent must return.

Every agent is constrained to one of these shapes. A model never returns a score
or a decision: it returns grounded observations, and deterministic code in
``backend/assessment.py`` and ``backend/job_quality.py`` turns those into
numbers and states. Excerpt fields exist so a claim can be checked against the
saved source before it is trusted.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class FitRequirement(BaseModel):
    """One thing the posting asks for, and whether the candidate's registered evidence shows it."""
    text: str = Field(description="The requirement as a short canonical phrase, e.g. 'Apache Kafka' or 'stream processing'")
    category: Literal["required", "preferred", "responsibility"]
    excerpt: str = Field(description="The exact sentence from the job description, copied verbatim")
    status: Literal["met", "partial", "missing"]
    evidence_ids: list[str] = Field(default_factory=list, description="Ids from the evidence catalogue that show it; empty when missing")
    note: str = Field(default="", description="One short clause on how the evidence meets it, or what is missing")


class FitBlocker(BaseModel):
    excerpt: str = Field(description="The exact sentence from the job description, copied verbatim")
    reason: str = Field(description="Why it rules the candidate out, in a few words")


class FitAnalysis(BaseModel):
    """The requirement matrix. Code verifies every excerpt and evidence id before it is trusted (services/fit.py)."""
    requirements: list[FitRequirement]
    hard_blockers: list[FitBlocker] = Field(default_factory=list)
    summary: str = Field(default="", description="One sentence on the overall fit")


class ResumeEdit(BaseModel):
    field: Literal["summary", "skills", "project", "second_project", "font"]
    value: str = Field(description="The replacement value for this field")
    rationale: str = Field(description="Why this change suits the role, in one sentence")
    evidence_ids: list[str] = Field(description="Registered evidence IDs supporting the new wording. Empty means unsupported.")


class ResumeChangeSet(BaseModel):
    """Edits confined to registered evidence. New claims become profile proposals."""
    edits: list[ResumeEdit]
    unsupported_requests: list[str] = Field(
        description="Parts of the request needing facts not in the registered evidence"
    )
    summary: str = Field(description="One sentence describing what will change")


class ProfileProposal(BaseModel):
    action: Literal["add", "correct", "merge", "remove"]
    target_id: Optional[str] = Field(default=None, description="Existing profile entry ID, for correct/merge/remove")
    kind: Optional[str] = Field(default=None, description="skill, experience, education, project, certification or fact")
    title: Optional[str] = None
    summary: Optional[str] = None
    rationale: str


class ProfileChangeSet(BaseModel):
    proposals: list[ProfileProposal]
    summary: str


class HiringManagerReview(BaseModel):
    """Written without any candidate information. See isolation note in graph.py."""
    must_have_signals: list[str] = Field(description="What a strong application must demonstrate")
    screening_questions: list[str]
    common_rejection_reasons: list[str]


class CoverLetterDraft(BaseModel):
    body: str = Field(description="The letter body, no header or signature block")
    evidence_ids: list[str]
    unsupported_claims: list[str] = Field(description="Anything written that the evidence does not support. Should be empty.")


class MailVerdict(BaseModel):
    kind: Literal["application_receipt", "rejection", "interview_invite", "offer", "unrelated"]
    company: Optional[str] = None
    role: Optional[str] = None
    states_submission_date: Optional[str] = Field(default=None, description="ISO date only if the message states when the application was submitted")
    confidence: Literal["high", "medium", "low"]
    excerpt: str


class PostingFields(BaseModel):
    """The four facts a pasted posting must yield before it can be saved.

    Every value must appear in the pasted text; the assistant discards anything
    it cannot find there, so a wrong guess costs nothing but an empty field.
    """
    company: str = Field(default="", description="Employer name exactly as the posting writes it; empty if not stated")
    title: str = Field(default="", description="Job title exactly as the posting writes it; empty if not stated")
    location: str = Field(default="", description="City and state or country, 'Remote (US)', 'Remote (Ireland)' or similar, as the posting states it; empty if not stated")
    url: str = Field(default="", description="The application or posting link if the text contains one; empty otherwise")


class TailoredProject(BaseModel):
    """One project slot in a per-job resume plan.

    'verified' entries are copied unchanged from the registry and carry its
    evidence id; 'predicted' entries are proposed wording aligned to the
    employer's stack and must be reviewed before they can reach a resume.
    """
    title: str
    context: str = Field(description="The stack line printed beside the title, e.g. 'Apache Kafka, Python'")
    bullets: list[str]
    origin: Literal["verified", "predicted"]
    evidence_id: str = Field(default="", description="Registry project id for verified items; empty for predicted")


class TailoredSkill(BaseModel):
    name: str
    origin: Literal["verified", "predicted"]
    evidence_id: str = Field(default="", description="Registry claim id for verified items; empty for predicted")


class TailoringResult(BaseModel):
    """A per-job Projects + Skills plan. Experience, Education and personal sections are never part of it."""
    projects: list[TailoredProject]
    skills: list[TailoredSkill]
    rationale: str = Field(description="One short paragraph on why this mix fits the role")


class ToolCall(BaseModel):
    """One more read-only tool the agent runs in the same turn."""
    tool: str = Field(description="The tool name, exactly as listed in tools")
    arguments: str = Field(default="{}", description="The tool's arguments as one JSON object")


class AgentTurn(BaseModel):
    """One decision of the workspace agent: call a tool, ask the person something, or reply.

    The loop in services/assistant.py runs the chosen tool, appends its result
    to the task transcript and asks for the next turn, until the agent replies.
    """
    thought: str = Field(description="One short sentence addressed to the candidate as 'you', on what you are doing now and why; the candidate sees it as progress")
    action: Literal["call", "ask", "reply"] = Field(description="call = run one tool; ask = you need something only the candidate can supply; reply = the task is finished or cannot proceed")
    tool: str = Field(default="", description="For call: the tool name, exactly as listed in tools")
    arguments: str = Field(default="{}", description="For call: the tool's arguments as one JSON object, using the parameter names listed")
    reply: str = Field(default="", description="For ask or reply: what to say to the candidate, in plain sentences; **bold** for a job name is fine")
    suggestions: list[str] = Field(
        default_factory=list,
        description="For reply: up to four short messages the candidate could send next; for ask: the likely answers, e.g. yes / no",
    )
    more_calls: list[ToolCall] = Field(
        default_factory=list,
        description="For call: up to five more tools that only read (never change anything) to run in this same "
                    "turn, when you already know you need them all, e.g. get_job for three jobs. Leave empty otherwise.",
    )


# ---- Intake: a new profile read from the person's own documents -------------------
#
# Every item carries `refs`: the block ids ([P012]) of the section text it came from.
# Wording stays the person's own; nothing is summarised into a claim they did not make.

def _refs():
    return Field(default_factory=list, description="Block ids such as P012 that this item comes from; at least one")


class IntakeContact(BaseModel):
    full_name: str = Field(default="", description="As written in the document")
    preferred_name: str = Field(default="", description="Only if the document says what they like to be called")
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio_url: str = ""
    city: str = Field(default="", description="Where they live now, as stated")
    country: str = Field(default="", description="Country they live in now, as stated")
    languages: list[str] = Field(default_factory=list, description="Spoken languages, if stated")
    refs: list[str] = _refs()


class IntakeAuthorization(BaseModel):
    work_country: str = Field(default="", description="The country their permission to work covers, as stated")
    status: str = Field(default="", description="Visa, stamp or permit exactly as stated, e.g. 'Stamp 1G' or 'F-1 OPT'; empty if not stated")
    valid_until: str = Field(default="", description="Expiry as stated, e.g. 'DEC2027'; empty if not stated")
    conditions: str = Field(default="", description="Limits as stated, e.g. '40 hours'; empty if none stated")
    needs_sponsorship_later: Literal["yes", "no", "unknown"] = Field(
        default="unknown", description="Does the document say they will need an employer to sponsor a permit later?")
    citizenship: str = Field(default="", description="Only if stated")
    refs: list[str] = _refs()


class IntakeTargets(BaseModel):
    roles: list[str] = Field(default_factory=list, description="Job titles they say they want")
    countries: list[str] = Field(default_factory=list, description="Countries they want to work in")
    cities: list[str] = Field(default_factory=list)
    arrangements: list[str] = Field(default_factory=list, description="Onsite / hybrid / remote / relocation, as stated")
    seniority: str = Field(default="", description="e.g. graduate, junior, entry level, as stated")
    salary: str = Field(default="", description="Their stated expectation, verbatim")
    availability: str = Field(default="", description="When they can start, verbatim")
    refs: list[str] = _refs()


class IntakeEducation(BaseModel):
    institution: str
    degree: str = Field(description="Degree name as written, e.g. 'Master of Science'")
    field: str = Field(default="", description="Subject, e.g. 'Data Science and Analytics'")
    location: str = ""
    start: str = Field(default="", description="As written, e.g. 'September 2024' or '2019'")
    end: str = Field(default="", description="As written; 'Present' if ongoing")
    grade: str = Field(default="", description="GPA/CGPA/classification exactly as written, e.g. 'CGPA 7.41, First Class'")
    coursework: list[str] = Field(default_factory=list, description="Modules or courses named")
    facts: list[str] = Field(default_factory=list, description="Other concrete facts about this degree, each one sentence in their words")
    refs: list[str] = _refs()


class IntakeRole(BaseModel):
    employer: str
    title: str
    location: str = ""
    start: str = Field(default="", description="As written, e.g. 'June 2022'")
    end: str = Field(default="", description="As written, e.g. 'September 2024' or 'Present'")
    employment_type: str = Field(default="", description="full-time / part-time / internship / contract, only if stated")
    client_or_domain: str = Field(default="", description="e.g. 'a leading banking client', as stated")
    bullets: list[str] = Field(default_factory=list, description=(
        "Every concrete thing they did or delivered in this role, one per item, close to their own words, keeping "
        "their hedges (contributed to, supported). Do not merge two achievements into one."))
    metrics: list[str] = Field(default_factory=list, description="Every number they give for this role, with its context, verbatim")
    tools: list[str] = Field(default_factory=list, description="Tools and technologies they used here")
    refs: list[str] = _refs()


class IntakeProject(BaseModel):
    name: str = Field(description="The project's name as written, without list numbering")
    kind: Literal["academic", "professional", "personal", "research", "self-directed", "unspecified"] = "unspecified"
    period: str = Field(default="", description="When, as written (e.g. 'MSc', '2023'); empty if not stated")
    organisation: str = Field(default="", description="University, course, employer or group it was for, if stated")
    ownership: str = Field(default="", description="Individual / group project, their role in it, as stated")
    summary: str = Field(default="", description="One sentence on what it is, in their words")
    facts: list[str] = Field(default_factory=list, description="Every concrete step, method, decision and result, one per item")
    metrics: list[str] = Field(default_factory=list, description="Every number with its context, verbatim (accuracy, R², rows, errors)")
    tools: list[str] = Field(default_factory=list, description="Languages, libraries and platforms used")
    refs: list[str] = _refs()


class IntakeSkillGroup(BaseModel):
    name: str = Field(description="The group as they describe it, e.g. 'Machine learning' or 'Cloud platforms'")
    skills: list[str] = Field(description="Each skill as a short name (1-4 words)")
    level: Literal["strong", "used", "familiar", "unspecified"] = Field(
        default="unspecified", description="Only if the text says how well they know them")
    refs: list[str] = _refs()


class IntakeCredential(BaseModel):
    name: str
    issuer: str = ""
    date: str = Field(default="", description="As written, e.g. 'Issued June 11, 2026'")
    details: list[str] = Field(default_factory=list, description="What it covered, one fact per item")
    refs: list[str] = _refs()


class IntakeStatement(BaseModel):
    topic: str = Field(description="e.g. 'strength', 'weakness', 'value', 'achievement', 'school results', 'career goal', 'voice'")
    text: str = Field(description="The statement in their own words")
    refs: list[str] = _refs()


class IntakeAnswer(BaseModel):
    question: str
    answer: str = Field(description="Their prepared answer, verbatim or nearly")
    refs: list[str] = _refs()


class IntakeFacts(BaseModel):
    """Everything one section of the documents says about the person."""
    contact: IntakeContact = Field(default_factory=IntakeContact)
    authorization: IntakeAuthorization = Field(default_factory=IntakeAuthorization)
    targets: IntakeTargets = Field(default_factory=IntakeTargets)
    education: list[IntakeEducation] = Field(default_factory=list)
    experience: list[IntakeRole] = Field(default_factory=list)
    projects: list[IntakeProject] = Field(default_factory=list)
    skills: list[IntakeSkillGroup] = Field(default_factory=list)
    certifications: list[IntakeCredential] = Field(default_factory=list)
    statements: list[IntakeStatement] = Field(default_factory=list, description="Strengths, values, goals, school results, achievements and anything else about them")
    interview_answers: list[IntakeAnswer] = Field(default_factory=list)
    narrative_only: list[str] = Field(default_factory=list, description="Block ids in this section that state no new fact (reflection, transitions)")
    questions: list[str] = Field(default_factory=list, description="Anything unclear or contradictory, asked as a short question to the person")


class IntakeMissed(BaseModel):
    text: str = Field(description="The fact, in the document's words")
    refs: list[str] = _refs()
    category: Literal["contact", "authorization", "targets", "education", "experience", "project", "skill",
                      "certification", "statement", "interview_answer", "other"]


class IntakeAudit(BaseModel):
    """What the extraction of one section missed. Empty `missed` when nothing was."""
    missed: list[IntakeMissed] = Field(default_factory=list)


# ---- The setup interview: questions asked in the chat before a new profile is built ----

class InterviewOption(BaseModel):
    label: str = Field(description="What they would click: 1 to 6 words, in plain language")
    description: str = Field(default="", description="One short line on what choosing it means; empty if obvious")


class InterviewUpdate(BaseModel):
    field: str = Field(description="One of the field names listed in the input under `fields`")
    value: str = Field(description="The value in their own words; for a list field, items separated by ' | '")


class InterviewTurn(BaseModel):
    """One step of the setup chat: ask the next question, or say the interview is done."""
    action: Literal["ask", "done"] = Field(description="ask = one more question is worth their time; done = build now")
    say: str = Field(default="", description="One or two sentences acknowledging their last message; empty if there is none")
    question: str = Field(default="", description="For ask: the one question, in plain words, addressed to them as 'you'")
    why: str = Field(default="", description="For ask: one short sentence on how the answer changes their job search or resume")
    field: str = Field(default="note", description="For ask: the listed field the answer fills, or 'note' for anything else")
    options: list[InterviewOption] = Field(
        default_factory=list, description="For ask: 2 to 5 likely answers they can click; empty only for a truly open question")
    multi_select: bool = Field(default=False, description="True when several options can be true at once (cities, arrangements, roles)")
    resolves: str = Field(default="", description="For ask: the open question this settles, copied exactly from open_questions; empty otherwise")
    updates: list[InterviewUpdate] = Field(
        default_factory=list, description="Values their last message stated for any listed field; empty when it stated none")
