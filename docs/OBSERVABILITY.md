# Observability: seeing what the agents did

Every agent run is traced on your own computer, so you can follow what happened step by step
and debug a run that went wrong. A trace is the run's timeline: each step, each AI call and
each web request is one *span*, nested under the step that started it.

## What is recorded

| Span | What it tells you |
|---|---|
| `agent.run <kind>`, `pipeline.run`, `hunt.run`, `assistant.turn` | one run of an agent, a Daily Search, an overnight hunt or an Assistant reply; the root of its timeline |
| `ai.request` | one AI step: its action, whether the answer came from the cache (`career.ai.cache_hit`) and the cache entry (`career.ai.cache_key`) |
| `ai.route` | Auto choosing an AI: the planned route, each endpoint skipped or failed (and why), and the one that answered |
| `chat <model>` | one real call to a model: provider, model, action or specialist, web search on/off, free or paid plan, token counts, time spent waiting for a free AI slot, prompt and answer *sizes* |
| `GET <host>`, `POST <host>` | one web request: the URL (secrets removed), status code, size, time waited for politeness, robots.txt refusals |
| `graph.run <name>`, `graph.node <node>` | one LangGraph run (research, company dossier, job preparation, Tracker refresh) and each of its nodes, with `career.graph.resumed` when it continued after a stop |

Failed spans show an error category (such as HTTP 429, a plan limit or the exception type), so
the timeline identifies the call or request that broke a run. Raw provider messages and stack
traces are omitted because they can contain private text or credentials.

## What is never recorded

- Prompts, AI answers, CV or cover-letter text, chat messages, contact details.
- API keys or tokens: user names and passwords are removed from URLs, credential query values
  (`api_key`, `token`, `affid`, …) are replaced by `REDACTED`, and keys in an API path
  (Jooble) are masked.

The timeline does not store the prompt. The answer of a cached AI step stays in the
profile's own database; the timeline can show it to you on request (below).

## Where traces are kept

- A profile's runs: `career-dashboard/profiles/<id>/data/traces.db` (inside the profile, deleted
  with it).
- Work outside a profile: `career-dashboard/data/traces/app.db`.
- Spans older than 14 days are removed automatically. Both locations are git-ignored.

## How to look at a run

- **Dashboard:** Agents → open a run → **Timeline**. Click a row for its details (provider,
  model, tokens, HTTP status, why a route moved on). On an AI step, **Show the stored answer**
  opens the cached answer from your own database (it never leaves your computer).
- **Command line:**

  ```text
  career trace list --profile <id>          the latest runs: kind, length, AI and web calls, failures
  career trace show <run-id> --profile <id> one run as a tree with every step, call and request
  ```

  Add `--json` for machine-readable output.

## Graph runs: checkpoints, resuming and the step-by-step view

The workflows built with LangGraph (`backend/graphs/`) save a **checkpoint** after every node in
the profile's `data/agents.db` (30 days, then pruned): company research and its verified
dossier (`graph_research`), each job's preparation in a Daily Search (`graph_pipeline`), and
the Tracker refresh (`graph_tracker_refresh`, kept under `data/market/refresh/`). A run stopped
by a restart or an error continues after its last finished node, so finished web research or AI
calls are not repeated; a finished run is never run twice.

- **Dashboard:** Agents → open a run → **Checkpoints**: each node that ran, which state values it
  changed (type and size), what runs next, and where a stopped run will resume. **Show my data**
  adds short previews of the values; it is a local view and nothing is sent anywhere.
- **Command line:** `career trace graph <run-id> --profile <id>` (alias `replay`) prints the
  same steps; `--content` adds the previews and `--json` the raw history (with checkpoint ids).
- **Run again from a step** (research runs): the Checkpoints tab's **Run again from …** button, or
  `career trace rerun <run-id> --checkpoint <id> --profile <id>`, starts a new run that copies the
  finished one, keeps the results before that step and runs the rest again with fresh AI answers
  (and a fresh dossier when it starts at the dossier). The earlier run is left unchanged; the new
  one's Checkpoints show its own branch, marked where it was forked.

Checkpoint values are the run's working state (for research: the posting, public company
facts and the comparison with the profile), so `data/agents.db` is private like the rest of the
profile and is never committed or uploaded.

**LangGraph Studio** (developers, synthetic demo only): from `career-dashboard/`, install
`backend/requirements-studio.txt` into the backend Python and run `langgraph dev`;
`langgraph.json` lists the research and dossier graphs. The graphs take the app's live services
through their context, which Studio cannot send, so each Studio graph is a one-node wrapper that
runs the real graph as its subgraph on the synthetic demo profile (`backend/graphs/studio.py`,
made once in the ignored `data/demo/studio/`, with its own market store). It refuses users'
profiles and never loads `.env`; AI calls use the AI apps set up on the computer. The Timeline,
Checkpoints and Phoenix (below) cover the same needs for real runs.

## A full trace viewer: Arize Phoenix (optional, local)

[Arize Phoenix](https://github.com/Arize-ai/phoenix) is an open-source trace viewer that runs on
your computer, so the same redacted spans never leave it.

1. In a separate Python environment: `pip install arize-phoenix`, then `phoenix serve`
   (it listens on <http://127.0.0.1:6006>).
2. Into the app's environment: `career-dashboard\backend\.venv\Scripts\python -m pip install -r
   career-dashboard\backend\requirements-observability.txt`.
3. Set `CAREER_OTEL_ENDPOINT=http://127.0.0.1:6006/v1/traces` and restart the dashboard.

Spans are then written to the local file *and* sent to Phoenix. AI calls carry OpenInference
attributes (`openinference.span.kind=LLM`, `llm.model_name`, `llm.token_count.*`), so Phoenix
shows them as LLM calls.

The absolute profile-folder path is used only to select the local trace file and is removed
before OTLP export.

## LangSmith: off by default

LangChain can upload traces to LangSmith, a hosted service. Those traces would contain prompts,
CVs and immigration status, so importing the backend switches LangSmith tracing off in every
process (`LANGSMITH_TRACING=false`), whatever the environment says. A maintainer who wants it
for a **synthetic test profile only** sets `CAREER_ALLOW_LANGSMITH=1` as well as the usual
LangSmith variables. Never do this with a real person's profile.

## For developers: adding spans

Use `backend.telemetry`, never the OpenTelemetry API directly:

```python
from backend import telemetry

with telemetry.span("market.refresh", **{"career.source": "eures"}) as current:
    ...
    telemetry.event("source.page", page=3, items=50)
    telemetry.set_attributes(current, **{"career.items": total})
```

- Attributes are IDs, counts, sizes, hashes, timings and public posting fields. Never a prompt,
  an answer, document text or a contact detail; pass URLs through `telemetry.safe_url()`.
- Work handed to a thread pool keeps its profile, run and parent span with
  `telemetry.submit(pool, fn, ...)` or `pool.map(telemetry.carry(fn), items)`.
- A run that records a failure without raising marks its span with `telemetry.mark_failed()`.
- Tests trace into memory (the `spans` fixture in `tests/portable/conftest.py`) and can assert
  on `spans.get_finished_spans()`.
- Without the `opentelemetry` packages every helper is a no-op; the app keeps working.
