# AI handoff

Read the repository `AGENTS.md`, then `career-dashboard/AGENTS.md`, then
`WORKSPACE-STATE.md` for where things stand. `docs/ARCHITECTURE.md` describes
the layout; `docs/AI-AGENTS.md` describes the agent layer.

Never create a second application or status store. `data/career.db` is the only
mutable authority.

## Where things are

- `backend/paths.py` — the directory map; ask it, do not count parent folders.
- `backend/dashboard/` — FastAPI app and `/api/v2` routes.
- `backend/services/` — workspace, agents runner, resume studio, layout.
- `backend/scripts/` — `career.py` and `workspace.py` CLIs, validators.
- `backend/migrations.py` — numbered transactional migrations and recovery snapshot.
- `backend/job_quality.py` — posting liveness, relevance, legitimacy, balanced mix.
- `backend/assessment.py` — grounded requirements, ATS readiness, coverage, fit.
- `backend/resume_rules.py` — skill parsing and capitalisation.
- `backend/chat_changes.py` — revision-bound resume and profile change sets.
- `backend/ai/` — key discovery, model catalogues, providers, and the agents.

## Commands

```sh
backend/.venv/bin/python backend/scripts/workspace.py summary      # live state
backend/.venv/bin/python backend/scripts/career.py jobs            # saved postings
backend/.venv/bin/python backend/scripts/workspace.py stale-drafts # drafts needing a rebuild
backend/.venv/bin/python backend/scripts/workspace.py recompile    # rebuild them
./Check\ Workspace.command                                 # the full gate
```

## Constraints that are not negotiable

Preserve job IDs, application history and generated artifacts. Never infer an
application, a rejection or a candidate fact from elapsed time. Keep the
hiring-manager agent isolated from candidate data. Never expose a provider key.
Do not submit an application or contact anyone without explicit authorisation.

A resume claim must trace to registered evidence. When a request needs a fact
the registry does not hold, it becomes a profile proposal for review — it is
never written into a resume.
