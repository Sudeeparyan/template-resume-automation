"""Versioned API for the four-tab React application."""

import threading
from typing import Any, Optional
from contextlib import asynccontextmanager
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from backend.services.workspace_v2 import CareerServices, agents_for
from backend.services.agents import AgentRunner


class StudioSave(BaseModel):
    revision: int = Field(ge=1)
    source: Optional[str] = Field(default=None, max_length=150000)
    fields: Optional[dict[str, str]] = None
    project_id: Optional[str] = None
    second_project_id: Optional[str] = None
    restore_revision: Optional[int] = None


class StudioPreview(BaseModel):
    revision: int = Field(ge=1)


class AIPolicyInput(BaseModel):
    # Paid AI calls a day (Azure and API keys); free plans never count. 0 = never pay.
    daily_call_limit: int = Field(ge=0, le=200)


class AIRouteInput(BaseModel):
    order: list[str] = Field(default_factory=list, max_length=10)
    enabled: dict[str, bool] = Field(default_factory=dict)
    allow_fallbacks: bool = True
    # Tokens per 5-hour window she typed per free plan; empty = learned or the starting guess.
    capacity: dict[str, Optional[int]] = Field(default_factory=dict)


class GoalInput(BaseModel):
    weekly_target: int = Field(ge=1, le=200)
    workdays: list[int]
    start_date: str


class KnowledgeInput(BaseModel):
    kind: str
    title: str = Field(min_length=1, max_length=250)
    summary: str = Field(default="", max_length=30000)
    data: dict[str, Any] = Field(default_factory=dict)
    revision: Optional[int] = None
    # Profile form fields; when present they decide title, summary and data (profile_fields.py).
    fields: Optional[dict[str, Any]] = None


class ReconcileInput(BaseModel):
    ids: Optional[list[str]] = None


class PostingInput(BaseModel):
    company: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=150)
    url: str = Field(min_length=8, max_length=2500)
    description: str = Field(min_length=80, max_length=100000)
    requisition_id: str = ""


class MailResolution(BaseModel):
    job_id: Optional[str] = None
    action: str = "confirm"
    create_application: bool = False


class MailConnectionInput(BaseModel):
    connector_id: str = Field(default="", max_length=100)
    expected_email: str = Field(default="", max_length=254)


class JobRemoval(BaseModel):
    reason: str = Field(default="Not suitable", max_length=500)


class AgentInput(BaseModel):
    kind: str
    job_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    preset: str = "default"
    # Discovery only: how many jobs to save (default 5).
    count: Optional[int] = Field(default=None, ge=1, le=15)


class PipelineInput(BaseModel):
    count: int = Field(default=5, ge=1, le=15)
    source: str = Field(default="default", max_length=40)
    # Left out: the AI chosen in Settings (Auto by default) does the work.
    provider: Optional[str] = Field(default=None, max_length=40)
    model: Optional[str] = Field(default=None, max_length=200)
    steps: dict[str, bool] = Field(default_factory=dict)
    # Also prepare saved jobs an earlier run left unprepared (the 7 AM run sets this).
    include_unprepared: bool = False


class TierChoice(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)


class AISettingsInput(BaseModel):
    tiers: dict[str, TierChoice]


class ChatPreviewInput(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    request_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


class ChatApplyInput(BaseModel):
    change_set_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


class InstructionInput(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    job_id: Optional[str] = None
    revision: Optional[int] = Field(default=None, ge=1)
    request_id: Optional[str] = Field(default=None, max_length=100)


class AIPreferencesInput(BaseModel):
    default: dict[str, str]
    actions: dict[str, dict[str, str]] = Field(default_factory=dict)
    fallback: Optional[dict[str, str]] = None


class AIMainInput(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=200)


class AIFallbackInput(BaseModel):
    provider: str = Field(default="", max_length=40)  # empty clears the backup
    model: str = Field(default="", max_length=200)
class AIKeyInput(BaseModel):
    value: str = Field(min_length=1, max_length=500)


class AITestInput(BaseModel):
    provider: str
    model: str
    action: str = "requirement_extraction"


class CompanyCheckInput(BaseModel):
    sources: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    size_category: str = "unknown"
    employee_min: Optional[int] = None
    employee_max: Optional[int] = None
    sponsorship_state: str = "unknown"
    override_reason: str = ""


class DiscoveryPreferencesInput(BaseModel):
    preset: str = "default"


class ScheduleInput(BaseModel):
    enabled: bool
    hours: int = Field(ge=1, le=24)


class AssistantInput(BaseModel):
    message: str = Field(min_length=1, max_length=120000)
    request_id: str = Field(min_length=1, max_length=100)


class AutoApplyInput(BaseModel):
    enabled: bool


class ItemDecision(BaseModel):
    decision: str = Field(min_length=1, max_length=20)


def attach(app, workspace, schedule: bool = False):
    service = CareerServices(workspace)
    runner = AgentRunner(service)
    from backend.services.resume_studio import ResumeStudio
    studio = ResumeStudio(service)
    runner.studio = studio
    from backend.services.instruction_tracker import InstructionTracker
    tracker = InstructionTracker(service, studio)
    from backend.job_quality import JobQualityService
    from backend.chat_changes import ChatChangeService
    quality = JobQualityService(service)
    chats = ChatChangeService(service, studio)
    from backend.services.pipeline import Pipeline
    pipeline = Pipeline(service, runner, studio)
    app.state.pipeline = pipeline
    from backend.services.assistant import Assistant
    from backend.services.assistant_tools import Toolbox
    # The chat reaches the Daily Search pipeline through the same object the page uses.
    assistant = Assistant(service, studio, runner, quality, tools=Toolbox(service, studio, runner, quality, chats, pipeline))
    app.state.assistant = assistant
    app.state.studio = studio
    app.state.career = service
    app.state.agents = runner
    router = APIRouter(prefix="/api/v2")

    @router.get("/summary")
    def summary():
        return service.summary()

    @router.get("/goals")
    def goals():
        return service.goals()

    @router.put("/goals")
    def save_goals(data: GoalInput):
        return service.save_goals(data.model_dump())

    @router.get("/profile")
    def profile():
        from backend.services.profile_fields import SCHEMAS, label_for, lines, view
        from backend.services.profile_sync import locate, lock_reason, resume_gap

        labels = {item["id"]: label_for(item) for item in service.knowledge(True)}
        registry = workspace.evidence()
        registered_projects = {p.get("id") for p in registry.get("projects") or []}

        def off_resumes(item):
            """Why a saved project is kept off resumes (it is not in the registry yet), or None."""
            if item["kind"] != "project" or item["review_state"] != "registered":
                return None
            if locate(item)[1] in registered_projects:
                return None
            data, label = item["data"], labels[item["id"]]
            content = {"title": label, "bullets": lines(item["summary"]),
                       "context": (data.get("resume_content") or {}).get("context") or ", ".join(data.get("technologies") or [])}
            return resume_gap(content, label) or "Not on resumes yet."

        sources = {}
        # Every readable file in data/context is a source: the profile's 01-09 files, the
        # readable profile, the questions ledger, pending updates and sources/*.md.
        context = workspace.root / "data/context"
        for path in sorted(list(context.glob("*.md")) + list((context / "sources").glob("*.md"))):
            sources[str(path.relative_to(workspace.root))] = path.read_text(encoding="utf-8")
        return {
            "items": [
                {**view(item), "locked": lock_reason(item, registry, labels[item["id"]]),
                 "off_resumes": off_resumes(item)}
                for item in service.knowledge()
            ],
            "schema": SCHEMAS,
            "removed": sum(i["deleted"] for i in service.knowledge(True)),
            "registry": registry,
            "configuration": workspace.profile(),
            "sources": sources,
            "agents": agents_for(workspace.root),
            "profile_dirty": service.profile_dirty(),
            "pending": [
                {**entry, "label": labels.get(entry["id"], entry["title"])}
                for entry in service.pending_knowledge()
            ],
            "revision": service.profile_revision(),
            "skills": [
                {
                    "name": "Evidence registry",
                    "purpose": "Approved candidate wording and evidence IDs",
                    "path": "data/context/evidence.yml",
                },
                {
                    "name": "Company-research playbook",
                    "purpose": "Public company and role investigation",
                    "path": "backend/workflows/agents/company-researcher.md",
                },
                {
                    "name": "Independent hiring review",
                    "purpose": "Role expectations without candidate context",
                    "path": "backend/workflows/agents/hiring-manager.md",
                },
                {
                    "name": "Verify job URL",
                    "purpose": "Checks specific posting URLs; browser review may still be required",
                    "path": "backend/scripts/verify_job_url.py",
                },
                {
                    "name": "Resume validation",
                    "purpose": "Evidence, page-contract PDF and visual-review gates",
                    "path": "backend/scripts/validate_resume.py",
                },
            ],
        }

    @router.post("/profile/items", status_code=201)
    def add_item(data: KnowledgeInput):
        return service.save_knowledge(data.model_dump())

    @router.put("/profile/items/{id}")
    def edit_item(id: str, data: KnowledgeInput):
        return service.save_knowledge(data.model_dump(), id)

    @router.delete("/profile/items/{id}")
    def remove_item(id: str):
        # The Profile page's Remove is the candidate's decision, applied everywhere at once.
        return service.delete_knowledge(id, propagate=True)

    @router.post("/profile/items/{id}/restore")
    def keep_item(id: str):
        return service.restore_knowledge(id)

    @router.post("/profile/reconcile")
    def reconcile_items(data: ReconcileInput):
        return service.reconcile_knowledge(data.ids)

    @router.post("/jobs")
    def add_posting(data: PostingInput):
        """Save a posting, and report how well it matches rather than refusing it.

        Discovery applies the relevance gate before saving; a manually-added job
        had no check at all. Saving is still the user's call, so this warns.
        """
        posting = data.model_dump()
        result = service.add_posting(posting, source="manual")
        if result.get("excluded") or result.get("blocked"):
            # Not saved. The UI shows the exact sentence (or the never-re-apply rule) and the
            # posting sits in Excluded roles where a wrong call can be restored.
            return result
        try:
            relevance = quality.relevance(
                posting,
                "\n".join(item["title"] + " " + item["summary"] for item in service.profile_context()),
            )
        except Exception:
            return result
        if not result.get("duplicate") and result.get("job"):
            from backend.services import fit
            # The rules check now; the AI check (free plans) runs when the resume is tailored.
            fit.save(service, result["job"]["id"], relevance["fit"])
            with workspace.connect() as db:
                db.execute("UPDATE jobs SET raw_jd=CASE WHEN raw_jd='' THEN description ELSE raw_jd END WHERE id=?",
                           (result["job"]["id"],))
        relevance = {k: v for k, v in relevance.items() if k != "fit"}
        return {**result, "relevance": relevance}

    @router.get("/jobs/{job_id}/fit")
    def job_fit(job_id: str):
        """What the job asks for and which registered evidence meets each item (services/fit.py)."""
        from backend.services import fit

        workspace.get_job(job_id)
        return fit.for_job(service, job_id, use_ai=False)

    @router.post("/jobs/{job_id}/fit")
    def refresh_job_fit(job_id: str):
        """Check again, by AI on a free plan when one is free (never a paid key)."""
        from backend.services import fit

        workspace.get_job(job_id)
        return fit.for_job(service, job_id, refresh=True)

    @router.get("/excluded")
    def excluded_postings(include_restored: bool = False):
        return {"items": service.excluded(include_restored)}

    @router.get("/rejected-leads")
    def rejected_leads(limit: int = 50):
        """Leads the discovery gates turned away, with the reason, newest first."""
        with workspace.connect() as db:
            rows = db.execute(
                "SELECT * FROM rejected_leads ORDER BY id DESC LIMIT ?", (min(limit, 200),)
            ).fetchall()
        return {"items": [dict(row) for row in rows]}

    @router.post("/excluded/{excluded_id}/restore")
    def restore_excluded(excluded_id: str):
        return service.restore_excluded(excluded_id)

    @router.post("/jobs/{job_id}/sponsorship")
    def recheck_sponsorship(job_id: str):
        return service.reevaluate_sponsorship(job_id)

    @router.post("/jobs/age")
    def age_applications():
        return {"ghosted": service.age_applications()}

    @router.delete("/jobs/{job_id}")
    def remove_job(job_id: str, data: Optional[JobRemoval] = None):
        return service.remove_job(job_id, data.reason if data else "Not suitable")

    @router.post("/jobs/{job_id}/restore")
    def restore_job(job_id: str):
        return service.restore_job(job_id)

    @router.post("/jobs/verify-due")
    def verify_due_jobs():
        return quality.verify_due()

    @router.post("/jobs/{job_id}/verify")
    def verify_job(job_id: str):
        return quality.verify_posting(job_id)

    @router.get("/jobs/{job_id}/verification")
    def job_verification(job_id: str):
        return quality.posting_history(job_id)

    @router.post("/companies/{job_id}/check")
    def check_company(job_id: str, data: CompanyCheckInput):
        job = workspace.get_job(job_id)
        return quality.assess_company(job["company"], job["url"], **data.model_dump())

    @router.post("/jobs/{job_id}/cover-letter")
    def generate_cover_letter(job_id: str):
        return service.generate_cover_letter(job_id)

    @router.get("/mail")
    def mail():
        return service.mail()

    @router.get("/mail/connection")
    def mail_connection():
        return service.mail()["connection"]

    @router.put("/mail/connection")
    def save_mail_connection(data: MailConnectionInput):
        return service.save_mail_connection(**data.model_dump())

    @router.post("/mail/{id}/resolve")
    def resolve_mail(id: str, data: MailResolution):
        return service.resolve_mail(id, **data.model_dump())

    @router.get("/email-schedule")
    def schedule():
        return service.pref("email_schedule", {"enabled": False, "hours": 6})

    @router.put("/email-schedule")
    def save_schedule(data: ScheduleInput):
        service.set_pref("email_schedule", data.model_dump())
        with workspace.connect() as db:
            workspace.record_event(
                db, "email_schedule_updated", settings=data.model_dump()
            )
        workspace.export_tracking()
        service.export_state()
        return data.model_dump()

    @router.get("/agents")
    def agents():
        return {"agents": agents_for(workspace.root), "runs": service.runs()}

    @router.post("/agents/run", status_code=202)
    def run(data: AgentInput):
        return runner.enqueue(data.kind, data.job_id, data.provider, data.model, data.preset, count=data.count)

    # Daily Search pipeline: find jobs, then only the helpers she switched on (services/pipeline.py).
    @router.get("/pipeline")
    def pipeline_overview():
        return pipeline.overview()

    @router.get("/pipeline/status")
    def pipeline_status():
        return pipeline.status()

    @router.put("/pipeline/preferences")
    def pipeline_preferences(data: PipelineInput):
        return pipeline.save_preferences(data.model_dump())

    @router.post("/pipeline/run", status_code=202)
    def pipeline_run(data: PipelineInput):
        return pipeline.start(data.model_dump())

    @router.post("/pipeline/{run_id}/stop")
    def pipeline_stop(run_id: str):
        stopped = pipeline.stop(run_id)
        if stopped is None:
            raise ValueError("That search run was not found.")
        return stopped

    @router.get("/ai/providers")
    def ai_providers(action: Optional[str] = None):
        return runner.gateway.catalog(action)

    @router.put("/ai/preferences")
    def ai_preferences(data: AIPreferencesInput):
        return runner.gateway.save_preferences(data.model_dump())

    @router.post("/ai/test")
    def ai_test(data: AITestInput):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        result = runner.gateway.generate(data.action, "Return {\"ok\": true} to verify structured generation.", schema, provider=data.provider, model=data.model, web=False)
        return {"ok": result.get("ok") is True, "provider": data.provider, "model": data.model}

    @router.get("/ai/settings")
    def ai_settings(refresh: bool = False):
        from backend.ai import settings as ai_settings

        return ai_settings.overview(service, refresh=refresh, gateway=runner.gateway)

    @router.put("/ai/main")
    def choose_main_ai(data: AIMainInput):
        from backend.ai import settings as ai_settings

        return ai_settings.choose_main(service, runner.gateway, data.provider, data.model)

    @router.put("/ai/fallback")
    def choose_fallback_ai(data: AIFallbackInput):
        from backend.ai import settings as ai_settings

        return ai_settings.save_fallback(service, runner.gateway, data.provider, data.model)

    @router.get("/ai/route")
    def ai_route():
        """Auto's route: the saved order and switches, and each endpoint's live state."""
        from backend.ai import settings as ai_settings

        entry = ai_settings._auto_entry(service, runner.gateway)
        return {"route": entry["route"], "endpoints": entry["endpoints"], "ready": entry["configured"]}

    @router.put("/ai/route")
    def save_ai_route(data: AIRouteInput):
        from backend.ai import settings as ai_settings

        return ai_settings.save_route(service, runner.gateway, data.model_dump())

    @router.delete("/ai/route/rest/{provider}")
    def wake_ai_plan(provider: str):
        from backend.ai import settings as ai_settings

        return ai_settings.wake(service, runner.gateway, provider)

    @router.put("/ai/keys/{provider}")
    def save_ai_key(provider: str, data: AIKeyInput):
        from backend.ai import settings as ai_settings

        return ai_settings.save_key(service, provider, data.value)

    @router.delete("/ai/keys/{provider}")
    def remove_ai_key(provider: str):
        from backend.ai import settings as ai_settings

        return ai_settings.remove_key(service, provider)

    @router.put("/ai/settings")
    def save_ai_settings(data: AISettingsInput):
        from backend.ai import settings as ai_settings

        return ai_settings.save(service, {
            "tiers": {tier: choice.model_dump() for tier, choice in data.tiers.items()}
        })

    @router.post("/ai/settings/test")
    def test_ai_settings(data: AITestInput):
        from backend.ai import settings as ai_settings

        return ai_settings.test_provider(service, data.provider, data.model)

    @router.get("/discovery/preferences")
    def discovery_preferences():
        return service.pref("discovery_preferences", {"preset": "default"})

    @router.put("/discovery/preferences")
    def save_discovery_preferences(data: DiscoveryPreferencesInput):
        if data.preset not in {"default", "balanced_five", "portals"}:
            raise ValueError("Choose default, balanced_five or portals")
        service.set_pref("discovery_preferences", data.model_dump())
        service.sync_projections()
        return data.model_dump()

    @router.post("/studio/{job_id}/open")
    def open_studio(job_id: str):
        # Opening a document never spends AI credits.
        return studio.open(job_id)

    @router.get("/studio/{job_id}")
    def get_studio(job_id: str):
        return studio.get(job_id)

    @router.put("/studio/{job_id}")
    def save_studio(job_id: str, data: StudioSave):
        return studio.save(job_id, **data.model_dump())

    @router.post('/studio/{job_id}/sync-profile')
    def sync_profile_studio(job_id: str, data: StudioPreview):
        return studio.sync_profile(job_id, data.revision)

    @router.get('/studio/{job_id}/assessment')
    def studio_assessment(job_id: str):
        return studio.assessment(job_id)

    @router.get('/studio/{job_id}/download')
    def studio_download(job_id: str, format: str):
        path, filename = studio.download(job_id, format)
        return FileResponse(path, filename=filename, media_type='application/pdf' if format == 'pdf' else 'application/x-tex')

    @router.post('/studio/{job_id}/chat/preview')
    def resume_chat_preview(job_id: str, data: ChatPreviewInput):
        return chats.resume_preview(job_id, data.message, data.request_id, data.expected_revision)

    @router.post('/studio/{job_id}/chat/apply')
    def resume_chat_apply(job_id: str, data: ChatApplyInput):
        return chats.resume_apply(job_id, data.change_set_id, data.request_id, data.expected_revision)

    @router.post('/studio/{job_id}/chat/undo')
    def resume_chat_undo(job_id: str, data: ChatApplyInput):
        return chats.resume_undo(job_id, data.change_set_id, data.request_id, data.expected_revision)

    @router.post('/profile/chat/preview')
    def profile_chat_preview(data: ChatPreviewInput):
        return chats.profile_preview(data.message, data.request_id, data.expected_revision)

    @router.post('/profile/chat/apply')
    def profile_chat_apply(data: ChatApplyInput):
        return chats.profile_apply(data.change_set_id, data.request_id, data.expected_revision)

    @router.post("/studio/{job_id}/fill")
    def fill_studio(job_id: str, data: StudioPreview):
        return studio.fill(job_id, data.revision)

    @router.post("/studio/{job_id}/preview")
    def preview_studio(job_id: str, data: StudioPreview):
        return studio.preview(job_id, data.revision)

    @router.get('/agents/activity')
    def agent_activity(limit: int = 60):
        from backend.services.observability import activity

        return activity(service, runner, max(1, min(limit, 200)))

    @router.get('/agent-control')
    def agent_control():
        return {'budget': runner.cache.stats(), 'runs': service.runs(), 'agents': agents_for(workspace.root)}

    @router.put('/agent-control/budget')
    def ai_budget(data: AIPolicyInput):
        return runner.cache.configure(data.daily_call_limit)

    @router.get('/instructions')
    def instructions(job_id: Optional[str] = None):
        return tracker.history(job_id)

    @router.post('/instructions')
    def instruction(data: InstructionInput):
        return tracker.send(**data.model_dump())

    @router.post('/studio/{job_id}/score')
    def score_studio(job_id: str):
        return studio.score(job_id)

    @router.post('/studio/{job_id}/tailor')
    def tailor_studio(job_id: str):
        from backend.ai import any_provider_configured, team_for

        if not any_provider_configured(service.w.root):
            raise ValueError("No AI runtime is set up on this machine, so the resume cannot be tailored automatically. Add an API key or sign in to a local runtime in Settings, then try again.")
        return studio.tailor(job_id, team_for(service))

    @router.get('/studio/{job_id}/items')
    def studio_items(job_id: str):
        return studio.items(job_id)

    @router.post('/studio/{job_id}/items/{item_id}/decision')
    def studio_item_decision(job_id: str, item_id: str, data: ItemDecision):
        return studio.decide(job_id, item_id, data.decision)

    @router.get('/assurance/{job_id}')
    def job_assurance(job_id: str):
        from backend.services.assurance import build_assurance

        return build_assurance(service, studio, job_id)

    @router.post('/documents/text')
    async def document_text(request: Request, name: str):
        """Read one more document about the candidate (Word, PDF, text) so the Assistant can
        propose what is new in it. The file is kept in data/context/files/; nothing reaches
        the profile until the candidate confirms each proposed change."""
        from backend.services.intake.extract import MAX_BYTES, blocks_for
        from backend.services.intake.job import safe_name

        data = await request.body()
        if not data or len(data) > MAX_BYTES:
            raise ValueError("Upload a document of up to 20 MB.")
        filename = safe_name(name)
        folder = workspace.root / "data/context/files"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / filename
        path.write_bytes(data)
        try:
            blocks = blocks_for([(path, filename)])
        except ValueError:
            path.unlink(missing_ok=True)
            raise
        text = "\n".join(("## " if b.kind == "heading" else "- " if b.kind == "list" else "") + b.text for b in blocks)
        return {"name": filename, "characters": len(text), "blocks": len(blocks), "text": text}

    @router.get('/assistant')
    def assistant_overview():
        return assistant.overview()

    @router.post('/assistant/messages', status_code=202)
    def assistant_send(data: AssistantInput):
        # Returns at once; the reply and its steps fill in on the worker thread.
        return assistant.send(data.message, data.request_id)

    @router.get('/assistant/messages/{message_id}')
    def assistant_message(message_id: str):
        return assistant.get(message_id)

    @router.post('/assistant/messages/{message_id}/stop')
    def assistant_stop(message_id: str):
        # The worker ends the reply at its next step; the row reads "Stopping…" until then.
        return assistant.stop(message_id)

    @router.put('/assistant/auto-apply')
    def assistant_auto_apply(data: AutoApplyInput):
        assistant.auto_apply(data.enabled)
        return assistant.overview()

    @router.get('/assistant/conversations')
    def assistant_conversations():
        return assistant.conversations()

    @router.post('/assistant/conversations', status_code=201)
    def assistant_new_conversation():
        assistant.new_conversation()
        return assistant.overview()

    @router.delete('/assistant/conversations')
    def assistant_clear_history():
        assistant.clear_history()
        return assistant.overview()

    @router.put('/assistant/conversations/{conversation_id}')
    def assistant_open_conversation(conversation_id: str):
        assistant.open_conversation(conversation_id)
        return assistant.overview()

    @router.delete('/assistant/conversations/{conversation_id}')
    def assistant_delete_conversation(conversation_id: str):
        assistant.delete_conversation(conversation_id)
        return assistant.overview()

    app.include_router(router)

    def start_background():
        runner.recover()
        pipeline.recover()
        # Background work (Gmail sync, posting liveness sweep) reaches the network,
        # so it starts only for a real server run, never for a constructed app.
        if schedule:
            runner.start_schedule()

            def refresh_fit():
                # Saved jobs show the verified requirement check's score, not an older formula's.
                from backend.services import fit

                try:
                    fit.backfill(service)
                except Exception as exc:  # noqa: BLE001 - a stale score must never stop the server
                    with workspace.connect() as db:
                        workspace.record_event(db, "fit_backfill_failed", error=str(exc)[:300])

            threading.Thread(target=refresh_fit, name="fit-backfill", daemon=True).start()

    def stop_background(stop_search: bool = False):
        # Closing a profile (reset/delete) also asks a running Daily Search to stop at
        # its next step; a normal shutdown leaves that to recover() on the next start.
        if stop_search:
            latest = pipeline._latest(active=True)
            if latest:
                pipeline.stop(latest["id"])
        runner.stop.set()
        runner.pool.shutdown(wait=False, cancel_futures=True)
        if assistant.pool:
            assistant.pool.shutdown(wait=False, cancel_futures=True)

    # A profile app mounted by the shell (dashboard/shell.py) has no lifespan of its
    # own, so the shell calls these two directly; a standalone app uses the lifespan.
    app.state.start_background = start_background
    app.state.stop_background = stop_background

    @asynccontextmanager
    async def lifespan(app):
        start_background()
        yield
        stop_background()

    app.router.lifespan_context = lifespan
    return service
