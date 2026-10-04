# Hosting the career workspace: design

Status: design only. Today the app runs on each person's own computer (the friends beta). This
document says what has to change before it can run as a hosted service for many people, and in
which order. Nothing here is implemented yet.

## What the local app relies on

| Local today | Why it cannot be hosted as it is |
|---|---|
| One process per computer; background work on threads (`AgentRunner`, `Pipeline`, `Hunt`) | A web server restarts and scales out; threads die with it |
| SQLite per profile (`career.db`, `agents.db`, `traces.db`) and a shared `market.db` | Many writers on one server need a database server; files need backups and encryption |
| The person's own AI apps (Kimi Code, Codex, Claude Code CLIs) on their own plans | Subscriptions are personal: a server must use keyed APIs under its own terms |
| `career-dashboard/.env` for keys | Shared secrets need a vault; a person's own keys need per-user encryption |
| gradireland and jobs.ie read for personal use | Their terms allow personal use only |
| Tectonic compiling PDFs on the person's computer | Compiling untrusted LaTeX needs a sandbox |

What carries over unchanged: the evidence registry and every honesty gate, the permit rules and
DETE data, the market store's schema, the LangGraph graphs (`backend/graphs/`), the redaction
rules for traces, and the source policy table (`backend/market/policy.py`).

## Target architecture

```
Browser ── HTTPS ── API (FastAPI, stateless, several copies) ── Postgres (per-user rows, RLS)
                       │                                         ├─ market schema (shared, public)
                       │                                         └─ LangGraph checkpoints (encrypted)
                       └── queue ── workers (graphs, hunts, PDF sandbox) ── keyed AI APIs (EU region)
Scheduler (morning hunts per time zone, TrackerRefresh every 6 h) ── queue
Object storage (CVs, PDFs, DOCX; per-user prefix, encrypted) · OTel collector ── Langfuse (self-hosted, EU)
```

### 1. Identity and tenancy

- Sign-in with OpenID Connect (Microsoft and Google accounts through one identity provider). No
  passwords stored by the app.
- One person, one profile by default. Every row that holds personal data carries `user_id`, and
  Postgres row-level security enforces it, so a query bug cannot read another person's data.
  The profile isolation the local app has (one folder per profile) becomes this policy.
- Per-user quotas: jobs prepared per day, AI tokens per day, hunts per night. The local
  `AgentCache` daily paid-call limit becomes a per-user budget.

### 2. Data stores

- **Postgres** replaces the SQLite files. `backend/market/store.py` already keeps every query
  behind repository methods, so the market store moves first (one shared schema of public data).
  The per-profile tables follow, with migrations ported from the numbered SQLite ones.
- **Checkpoints**: `langgraph-checkpoint-postgres` (`PostgresSaver`) instead of `SqliteSaver`,
  with LangGraph's encrypted serializer (AES key from the cloud key vault). Checkpoints hold the
  posting, public company facts and the profile comparison, so they are personal data: 30-day
  retention as today, deleted with the account.
- **Files**: CVs, generated PDFs, DOCX and letters in object storage under a per-user prefix,
  encrypted at rest, served through short-lived signed links. The local `/api/files` route's
  evidence check stays in front of every resume download.

### 3. Background work

- Runs become queue jobs (a Postgres-backed queue is enough at first). A worker leases a job,
  runs the graph with the Postgres checkpointer, and on a crash another worker resumes the same
  thread (`<graph>:<run id>`) after its last finished node: the behaviour the graphs already
  have locally.
- The overnight hunt (`services/hunt.py`) keeps its loop but runs in a worker; its waits for AI
  plan resets become scheduled re-queues instead of sleeps.
- One scheduler process enqueues morning hunts by each person's time zone and the shared
  TrackerRefresh every six hours (`graphs/tracker_refresh.py`; its lease already allows only one).
- PDFs compile in a sandboxed worker (no network, CPU and time limits, read-only template), since
  a person can edit the LaTeX source in Resume Studio.

### 4. AI providers

- Keyed APIs only: Anthropic, Azure OpenAI or OpenAI, under data-processing agreements that
  exclude training on the data, in an EU region where offered. The router (`backend/ai/router.py`)
  keeps its order and fallbacks; the CLI endpoints are switched off in hosted mode.
- Web search for research, the dossier and AI discovery comes from the provider's server-side
  search tool, with every claim still verified on its cited page (the dossier's word-for-word
  check does not depend on the provider).
- Cost control: per-user token budgets, the shared 30-day dossier cache, and cover letters only
  on request. The Daily Search's own first-run estimates (`services/pipeline.py`, `STEPS`) put one
  fully prepared job (research, tailor, review, study plan) at roughly 70,000 tokens; measure it
  on real runs before pricing.

### 5. Sources and their terms

`backend/market/policy.py` becomes enforced, not advisory:

| Source | Hosted |
|---|---|
| EURES / JobsIreland | Allowed with attribution; confirm the reuse terms before launch |
| Employer boards (directory, registry, tracked) | Allowed: public job APIs, robots.txt and one shared politeness limiter per host for all users |
| gradireland, jobs.ie | Off (personal use only), unless a written agreement allows it |
| Careerjet, Jooble | Only under the service's own publisher agreement and key, with their attribution; never a user's personal key on the server |
| askmanavi | Never (its terms forbid reuse) |

The fetcher's per-host pacing, backoff and circuit breaker must be shared across workers (a
Postgres or Redis-backed limiter), or many users would multiply the load on each employer's board.

### 6. Privacy and GDPR

- The app processes CVs, contact details and immigration status for job search. Before launch:
  a Data Protection Impact Assessment, a records-of-processing entry, a privacy notice in plain
  words, and data-processing agreements with the hosting, AI and email providers.
- Lawful basis: the contract to provide the service; explicit consent for optional features
  (email, Gmail reading).
- Rights: export (the local Export already produces the person's files) and erasure (delete the
  account: rows, files, checkpoints, traces, the dossier cache entries are public and stay).
- Retention: traces 14 days, checkpoints 30 days, documents until the person deletes them or
  12 months after their last sign-in.
- No automated decisions with legal or similar effects: the app never applies, never contacts an
  employer, and its permit information stays "dated facts, not immigration advice".
- Traces keep today's redaction (no prompts, answers, documents or contact details). LangSmith
  stays off; self-hosted Langfuse in the EU replaces it for LLM tracing, fed from the same
  redacted OpenTelemetry spans.

### 7. Security

- Secrets in the cloud key vault; no `.env` on servers. A person's own optional keys (Careerjet,
  Jooble) are not used server-side.
- CSRF protection on every write route, strict CORS, per-user rate limits on the API, audit log
  of sign-ins and data exports.
- Dependency pins and the release privacy scan (`scripts/scan_release.py`) stay in the CI gate.

## Order of work

1. **Single small group** (one organisation, under 50 people): OIDC sign-in, Postgres for the
   market store and profiles, PostgresSaver, a queue with one worker, keyed AI, gradireland and
   jobs.ie off. DPIA and privacy notice done before the first real CV is uploaded.
2. **Open sign-up**: per-user quotas and budgets, the shared politeness limiter, the sandboxed PDF
   worker pool, Langfuse, backups and restore drills.
3. **Scale**: several workers per graph type, read replicas for the Tracker, the source
   agreements in place for any aggregator.

The local app stays the reference: a hosted change that weakens an evidence gate, the permit
wording rules or the separation between people does not ship.
