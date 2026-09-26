# Developer guide

How the workspace fits together, where to change things, and how to prove a change is safe to
publish. Users start at [START-HERE.md](../START-HERE.md); setup is in [README.md](../README.md).

## Layout

```text
AGENTS.md, CLAUDE.md        instructions every AI app reads first (CLAUDE.md imports AGENTS.md)
START-HERE.md               the non-technical guide
.agents/skills/<name>/      the shared skills: SKILL.md (+ references/, agents/openai.yaml for Codex)
.claude/skills/<name>/      pointers so Claude Code lists the same skills; never add rules here
me/, my-jobs/               a person's inbox and the AI-only lists (private; only READMEs ship)
career, career.cmd          the CLI for AI apps (macOS/Linux/Git Bash, Windows)
Start Dashboard.*           launcher: scripts/bootstrap.py installs, checks tools, runs the app
Check Workspace.*           the full release gate
scripts/                    bootstrap, scan_release (privacy), check_profiles, smoke_first_run
daily-job-search/           autopilot.py: the morning runner (morning-jobs.cmd/.command), AUTOPILOT.md
career-dashboard/
  backend/                  FastAPI app, services, AI routing, country packs, CLI scripts
  frontend/                 React + Vite client (eight tabs)
  tests/portable/           the test suite (disposable profiles only)
  profiles/<id>/            private profiles (ignored): sources, SQLite database, outputs
backup/                     local backups (ignored, never read by AI apps)
```

## The two modes of the AI-app layer

`AGENTS.md` routes each request to a skill and picks a mode once per conversation:

- **App mode**: the AI app can run commands on this computer and `career status` works. Skills
  call the CLI and the morning runner; the app's gates, evidence registry and validators do the
  work.
- **AI-only mode**: anything else (for example Cowork's cloud sandbox, which cannot run the
  installed app). Skills tell the AI to do the same steps by hand from `me/` and write to
  `my-jobs/` (`tracker.csv`, dated `JOBS.md`, one folder per job).

Every skill describes both modes with the same rules, so a request gives the same kind of answer
in either. When you change app behaviour that a skill describes (a command, a gate, a file
path), update the skill in the same change. Codex and Kimi Code discover `.agents/skills/`;
Claude Code discovers `.claude/skills/`, whose pointer files must keep the same `name` and
`description` as the shared skill (a test checks this).

## Backend (`career-dashboard/backend`)

| Area | Where |
|---|---|
| Profiles and the registry (`profiles/registry.json`) | `profiles.py` (`ProfileStore`), `paths.py` |
| App shell: profile routes, onboarding, loopback guard | `dashboard/shell.py`, `dashboard/app.py` |
| Per-profile API (`/p/<id>/api/v2/...`) | `dashboard/api_v2.py` |
| Application services (jobs, goals, profile, cover letters) | `services/workspace_v2.py` (`CareerServices`) |
| Onboarding: sources, build runs, extraction | `services/source_library.py`, `services/intake/` |
| Job discovery: feeds without AI, the overnight hunt | `services/job_sources.py`, `services/hunt.py`, `services/search_plan.py`, `services/search_memory.py` |
| Gates: market, work permit, never re-apply, fit | `services/sponsorship.py`, `countries/<ie\|us>/`, `services/reapply.py`, `services/fit.py`, `job_quality.py`, `role_titles.py` |
| Agents and runs (research, tailoring, study plan) | `services/agents.py` (`AgentRunner`), `services/pipeline.py` |
| Resume Studio, page contract, PDF | `services/resume_studio.py`, `resume_contract.py`, `pdf_compiler.py` (Tectonic), `ai_marks.py` |
| Assistant (one chat, every feature as a tool) | `services/assistant.py`, `services/assistant_tools.py` (`Toolbox`) |
| AI providers and routing (Kimi Code, Codex, Claude Code, keyed APIs) | `ai/router.py`, `ai/providers.py`, `ai/agents/` |
| Morning task (Windows Task Scheduler) | `services/schedule_tasks.py` |

SQLite in each profile owns mutable state; source documents and the evidence registry
(`data/context/evidence.yml`) are the only candidate evidence. The hiring-manager review and
company research never see the profile.

## The `career` CLI

`career.cmd` (Windows) or `sh career` runs `backend/scripts/career_cli.py` with the app's Python.
Every command works on the last opened profile unless given `--profile <id>`, prints JSON and
needs no running server.

| Command | Does |
|---|---|
| `career setup --name "…" --market ie\|us\|both --work-auth '<json>'` | create a profile from the files in `me/` and run the build (`--profile <id>` rebuilds) |
| `career status`, `career jobs`, `career activity` | profile summary, saved jobs, event log |
| `career add --file job.json` | save a posting through the sponsorship and never-re-apply gates |
| `career update <id> --status … [--application-date]` | record an application outcome |
| `career prepare <id>`, `preview <id>`, `validate <id>` | application folder, compile, QA |
| `career ws fit --job-id <id> [--refresh]` | requirement matrix and fit score |
| `career ws tailor --job-id <id>` | Resume Studio tailoring (AI when ready) and the fitted PDF |
| `career ws run --kind research\|resume_match\|study_plan\|discovery … --job-id <id>` | one agent run |
| `career ws sponsor-check`, `check-reapply`, `ai-status` | gates and AI readiness |
| `career verify-url --url …`, `career check-resume …`, `career check` | link check, resume validator, workspace validator |

The morning runner: `daily-job-search/morning-jobs.cmd` (`.command`) with `--jobs N` (find N now),
`--list-only`, `--background`, `--check`, `--hours`, `--profile`. See
[AUTOPILOT.md](../daily-job-search/AUTOPILOT.md).

## Tests and the release gate

```text
Check Workspace.cmd                       Windows: everything below, in order
bash "Check Workspace.command"            macOS
```

1. Launcher preflight (dependencies, tools), then `pip install -r backend/requirements-dev.txt`.
2. `python -m pytest -q` in `career-dashboard/` (`pytest.ini` selects `tests/portable`).
3. `scripts/check_profiles.py` (local profiles' database and PDFs; passes with none) and
   `backend/scripts/validate_workspace.py`.
4. `npm test` and `npm run build` in `career-dashboard/frontend/`.
5. `scripts/scan_release.py`: fails when a file Git would publish holds a private path, a person's
   name or resume detail, a phone number, an email outside example domains, a home path or a
   credential, or when a required `.gitignore` rule is missing.
6. `scripts/smoke_first_run.py`: a zero-profile start, onboarding and profile isolation.

Tests use disposable profiles (`ProfileStore(base=tmp_path/...)`); `tests/portable/conftest.py`
keeps AI apps, job boards and the Task Scheduler offline. Never point a test, a screenshot or a
fixture at a real profile. GitHub Actions (`.github/workflows/check.yml`) runs the privacy scan
first, then the same gate on Windows and macOS.

## Publishing

Only generic code, public country data and the guides are published. Before a commit, run
`python scripts/scan_release.py`. A fresh clone must start with no profile, database, outputs,
credentials or application history.
