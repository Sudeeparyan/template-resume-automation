# Operations and recovery

## Runtime

Python 3.12 in `career-dashboard/backend/.venv`; the LangChain agent layer needs
3.10 or newer. Node and npm build the React client. `tectonic` compiles resumes —
it is the only LaTeX engine this workspace uses.

```sh
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python run.py
```

For UI work, `npm run dev` in `frontend/` serves the client on 5173 and proxies
`/api` to the server on 8000.

## API keys

Keys are read server-side, in this order: the process environment,
`career-dashboard/.env`, the repository-root `.env`, then `keys.txt`. Both
`NAME=value` and a line holding only the token work; a bare token is matched to
a provider by its prefix.

| Variable | Provider |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter — reaches GPT, Claude, Gemini and Kimi with one key |
| `OPENAI_API_KEY` | OpenAI directly |
| `ANTHROPIC_API_KEY` | Claude directly |
| `GEMINI_API_KEY` | Gemini directly |
| `MOONSHOT_API_KEY` | Kimi directly — named form only: Kimi keys start with `sk-`, so a bare token is never guessed as Kimi |

`keys.txt` and every `.env` are git-ignored. The default AI is **Auto**
(`docs/AI-AGENTS.md`): the local runtimes, which cost nothing extra, in the
order Kimi Code → Codex → Claude Code, each rested while its plan is at its
usage limit, then Azure OpenAI only when all three are out and the daily
paid-call limit has room. Settings shows which keys were found, without
revealing any value, and Test connection proves one works. When one provider is
chosen by name instead, Settings can also name a **backup provider**: a failed
call is retried once on it. Every switch is recorded in the activity log
(`provider_fallback`). Plan rest state and usage windows are in
`career-dashboard/.ai-plan-health.json` (git-ignored); `workspace.py ai-status`
prints them.

An OpenRouter key's `limit` is a cap on the key, not money. Paid models need
purchased credits on the account; without them a call returns HTTP 402.

## Migrations

`backend/migrations.py` applies numbered migrations transactionally and records
`schema_migrations`. Before migration 1 it writes `data/migrations/pre-v1-career.db`
and an artifact hash manifest. To recover: stop the dashboard, keep the failed
database, restore the pre-v1 copy, verify the manifest, and rerun against a
disposable copy before touching live state.

## Routine checks

```sh
./Check\ Workspace.command
```

That runs the Python tests, workspace validation, the projection staleness
check, the production frontend build and the component tests.

After changing the resume contract or template, rebuild affected drafts:

```sh
backend/.venv/bin/python backend/scripts/workspace.py recompile
```

A draft whose source changed without a rebuild keeps serving its old PDF and
hides the PDF download.

## Scheduled work

The dashboard's background loop re-checks due postings at most hourly (re-gating
their live wording), marks applications quiet for 21 days as ghosted, and runs
Gmail sync when enabled. It starts only for a real server run. The app's
timezone is America/Chicago (US Central); do not add a second automation.

Expired postings stay in SQLite and appear under Expired roles. Applied,
interview, offer and rejected records are never removed by a liveness check.
