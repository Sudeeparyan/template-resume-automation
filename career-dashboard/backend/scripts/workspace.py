#!/usr/bin/env python3
"""Shared chat interface for the React dashboard's SQLite state."""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]
from career import Workspace
from backend.services.workspace_v2 import CareerServices
from backend.services.agents import AgentRunner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "fit",
            "tailor",
            "agent-control",
            "score",
            "stale-drafts",
            "recompile",
            "summary",
            "coverage",
            "salary",
            "goals",
            "profile",
            "mail",
            "runs",
            "export",
            "save-profile",
            "remove-profile",
            "confirm-mail",
            "run",
            "excluded",
            "restore-excluded",
            "sponsor-check",
            "check-reapply",
            "age",
            "ai-status",
            "ai-wake",
        ],
    )
    parser.add_argument("--file", type=Path)
    parser.add_argument("--id")
    parser.add_argument("--job-id")
    parser.add_argument("--kind", choices=["research", "resume_advisor", "email", "discovery", "resume_build", "resume_match", "instruction_interpret", "study_plan", "salary_research"])
    parser.add_argument("--refresh", action="store_true",
                        help="fit: check the job's requirements again (AI on a free plan when one is free)")
    parser.add_argument("--preset", choices=["default", "balanced_five", "portals"], default="default",
                        help="discovery mix: portals reads tracked career pages with no AI call")
    parser.add_argument("--company")
    parser.add_argument("--title")
    parser.add_argument("--url", default="")
    parser.add_argument("--location", default="", help="sponsor-check: posting location")
    parser.add_argument("--market", choices=["ie", "us"], help="sponsor-check: one of the profile's selected markets")
    parser.add_argument("--provider", help="ai-wake: which plan to try again now (kimi_cli, codex, claude_code, azure_openai)")
    parser.add_argument("--profile", help="profile ID (default: last opened profile)")
    args = parser.parse_args()
    from backend.profiles import store
    profiles = store()
    profile_id = args.profile or profiles.last_used()
    if not profile_id:
        parser.error("No profile exists yet. Run  career setup --name \"Full Name\"  or create one in the dashboard.")
    s = CareerServices(Workspace(profiles.root_for(profile_id)))
    if args.command == "coverage":
        from backend.services.source_coverage import Coverage
        result = Coverage(s.w.root).summary()
    elif args.command == "salary":
        if not args.job_id:
            parser.error("--job-id is required")
        job = s.w.get_job(args.job_id)
        result = {"job_id": job["id"], "company": job["company"], "title": job["title"],
                  "opportunity": job.get("opportunity"),
                  "note": "Salary evidence and permit checks are separate; researched pay is not an advertised offer."}
    elif args.command == "fit":
        # What one job asks for and which registered evidence meets each item (services/fit.py).
        if not args.job_id:
            parser.error("--job-id is required")
        from backend.services import fit
        result = fit.for_job(s, args.job_id, refresh=args.refresh)
    elif args.command == "tailor":
        # Resume Studio's own steps, as the dashboard runs them: the AI tailors the draft and fits
        # the PDF to the profile's page contract; with no AI ready, the draft is ranked and fitted
        # from registered evidence only. Nothing is submitted.
        if not args.job_id:
            parser.error("--job-id is required")
        from backend.ai import any_provider_configured, team_for
        from backend.services.assistant_tools import resume_card
        from backend.services.resume_studio import ResumeStudio
        studio = ResumeStudio(s)
        draft = studio.open(args.job_id)
        ai = any_provider_configured(s.w.root)
        fitted = studio.tailor(args.job_id, team_for(s)) if ai else studio.fit(args.job_id, draft["revision"])
        card = resume_card(s.w.get_job(args.job_id), studio.get(args.job_id), fitted)
        pdf = s.w.root / "data/output" / card["pdf"] if card["pdf"] else None
        result = {"job_id": args.job_id, "tailored_by_ai": ai, "pdf": str(pdf) if pdf else None, "pages": card["page"]["count"],
                  "coverage": card["coverage"], "ats": card["ats"], "gaps": card["gaps"], "warnings": card["warnings"],
                  "note": "Nothing is submitted: review the PDF and apply through the posting link."}
    elif args.command in {'agent-control', 'score', 'stale-drafts', 'recompile'}:
        from backend.services.resume_studio import ResumeStudio
        from backend.services.agent_cache import AgentCache
        studio = ResumeStudio(s)
        if args.command == 'score': result = studio.score(args.job_id)
        elif args.command == 'stale-drafts': result = studio.stale_drafts()
        elif args.command == 'recompile':
            result = studio.recompile_stale(
                [args.job_id] if args.job_id else None,
                lambda target: print('compiling ' + target['job_id'] + ' r' + str(target['revision']) + ' (' + target['reason'] + ')', file=sys.stderr, flush=True),
            )
        else: result = {'budget': AgentCache(s).stats(), 'runs': s.runs()}
    elif args.command == "save-profile":
        if not args.file:
            parser.error("--file is required")
        result = s.save_knowledge(json.loads(args.file.read_text(encoding="utf-8")), args.id)
    elif args.command == "remove-profile":
        result = s.delete_knowledge(args.id)
    elif args.command == "confirm-mail":
        result = s.resolve_mail(args.id, args.job_id)
    elif args.command == "run":
        if not args.kind:
            parser.error("--kind is required")
        runner = AgentRunner(s)
        from backend.services.resume_studio import ResumeStudio
        runner.studio = ResumeStudio(s)
        result = runner.enqueue(args.kind, args.job_id, preset=args.preset)
        print(json.dumps(result), flush=True)
        runner.pool.shutdown(wait=True)
        result = s.runs()[0]
    elif args.command == "excluded":
        result = s.excluded()
    elif args.command == "restore-excluded":
        if not args.id:
            parser.error("--id is required")
        result = s.restore_excluded(args.id)
    elif args.command == "sponsor-check":
        # The gate on one posting: --company plus the JD in --file. Nothing is saved.
        if not (args.company and args.file):
            parser.error("--company and --file are required")
        result = s.gate(args.company, args.file.read_text(encoding="utf-8"), args.url,
                        args.location, market=args.market or "").as_dict()
    elif args.command == "check-reapply":
        if not (args.company and args.title):
            parser.error("--company and --title are required")
        from backend.services import reapply
        result = reapply.check(args.company, args.title, s.reapply_memory(), s.excluded(), s.w.profile())
    elif args.command == "age":
        result = {"ghosted": s.age_applications()}
    elif args.command in {"ai-status", "ai-wake"}:
        # Auto's route (Kimi -> Codex -> Claude -> Azure): which plans are ready, resting
        # after a usage limit and until when, and today's paid calls. ai-wake clears a rest.
        from backend.ai import limits, main_choice, paid_gate, ready_providers, router
        from backend.services.agent_cache import AgentCache
        if args.command == "ai-wake":
            if args.provider not in router.DEFAULT_ORDER:
                parser.error("--provider must be one of " + ", ".join(router.DEFAULT_ORDER))
            limits.HealthBook(s.w.root).wake(args.provider)
            with s.w.connect() as db:
                s.w.record_event(db, "ai_plan_woken", provider=args.provider)
        preferences = s.pref("ai_preferences", {}) or {}
        ready = ready_providers(s.w.root)
        main = main_choice(preferences, ready)
        budget = AgentCache(s).stats()
        result = {
            "main": {"provider": main[0], "model": main[1]},
            "route": router.status(s.w.root, router.policy_from(preferences), ready_map=ready,
                                   paid={"block": paid_gate(s)("azure_openai")}),
            "paid_calls": {"used_today": budget["paid_calls_today"], "limit": budget["daily_call_limit"],
                           "left": budget["remaining_calls"]},
            "free_calls_today": budget["free_calls_today"],
        }
    elif args.command == "profile":
        result = s.knowledge()
    elif args.command == "export":
        s.export_profile()
        s.w.export_tracking()
        s.export_state()
        result = {"exported": True}
    else:
        result = getattr(s, args.command)()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
