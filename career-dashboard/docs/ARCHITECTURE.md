# Career workspace architecture

SQLite at `data/career.db` is the sole mutable authority. The JSON and Markdown
files beside it are regenerated projections, never independent state.

## Five folders

| Folder | Holds |
|---|---|
| `frontend/` | React and TypeScript client; `npm run dev` for development. |
| `backend/` | All Python, self-contained: its own `.venv`, `run.py`, requirements, the FastAPI app, domain services, CLI, agent prompts, and the AI layer. |
| `data/` | The database, the candidate's own config and evidence, LaTeX templates, generated applications, and recovery snapshots. |
| `tests/` | Python and API tests. Frontend component tests live beside their components. |
| `docs/` | This directory. |

`backend/paths.py` is the directory map. Code asks it where things are rather
than counting parent directories, so a future move is one file.

Inside `backend/`: `dashboard/` (HTTP), `services/` (domain), `scripts/` (CLI
and validators), `workflows/` (agent briefs as Markdown), `ai/` (providers and
the specialist agents), plus the standalone modules `migrations.py`,
`job_quality.py`, `assessment.py`, `chat_changes.py` and `resume_rules.py`.

## Profiles

One server holds several profiles, each a separate workspace with the same
layout. `backend/profiles.py` is the registry (`profiles/registry.json`):
Chetan (`chetan`) is the locked backup whose root is the app folder itself
(`data/` here); every other profile's root is `profiles/<id>/`.
`backend/dashboard/shell.py` is what `run.py` serves: it builds one
`create_app(root)` per ready profile (its own `Workspace`, SQLite database,
services, agent runner, pipeline, assistant and scheduler) and routes
`/p/<id>/api/...` to it, so no request can reach another profile's objects.

Everything that differs between people is read from the profile's own files:
`profile.yml` (identity, targets, `country_pack`, `resume_contract`, `scoring`,
`persona`), `sponsorship.yml` (the gate), `portals.yml`, and optional guides in
`data/config/guides/`. Country specifics live in `backend/countries/<code>/`
(`us`, `ie`): location matching, gate templates, paper, spelling, time zone.
Chetan's prompts are the original text (she has no `persona`); another profile's
agents get `Specialist.profile_system` filled from `backend/ai/persona.py`.

New profiles are built by `backend/services/intake/` from uploaded documents:
deterministic extraction into numbered blocks, one `profile_extractor` call and
one `intake_auditor` call per section, a deterministic merge, a coverage ledger
(every block cited, judged narrative, or kept verbatim), the person's review,
then one atomic write of the whole file set. The app then seeds the new
database from those files exactly as it does for Chetan.

## Write path

Every mutation commits to SQLite, records an activity event where appropriate,
then calls `CareerServices.sync_projections()`. Posting expiry never deletes a
row. Resume and Profile chats go through revision-bound `chat_change_sets`;
resume changes create `studio_versions`, and Profile changes stay reviewable
knowledge entries until confirmed.

## What AI decides, and what it does not

Models supply judgement inside a step: which requirements a job description
states, whether a page reads as closed, how to reword a summary. They never
produce a score or a state. Every number comes from deterministic code in
`backend/assessment.py` and `backend/job_quality.py`, so a model change cannot
move a score. Requirement excerpts are checked against the saved job
description and discarded when they do not match.

## Isolation

The hiring-manager agent receives only the job description and public company
research. It is reached through `run_isolated()`, which accepts those two
fields and nothing else; the ordinary `run()` refuses it outright. Candidate
data enters only in the separate comparison step. See `AI-AGENTS.md`.

API keys are read server-side from the environment, either `.env`, or
`keys.txt`. They are never returned to the browser, written to SQLite, or
included in an error message.
