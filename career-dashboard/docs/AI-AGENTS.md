# The agent layer

Built on LangChain and LangGraph. One general-purpose prompt was replaced by
narrow specialists, each with a brief, its own output schema, and a service
tier (`backend/ai/agents/specialists.py`).

## Specialists

| Agent | Job | Tier |
|---|---|---|
| `fit_analyst` | A posting's requirements, each quoted from it, matched to her registered evidence (see *The requirement matrix*) | reading |
| `posting_parser` | A pasted posting to company, title, location and text | reading |
| `mail_classifier` | What one job-related email is | reading |
| `intake_auditor` | Checks a new profile's extracted facts against its documents | reading |
| `job_tailor` | Per-company Projects and Skills for one role, from the verified requirements | writing |
| `resume_tailor` | Evidence-bound resume edits for one role | writing |
| `profile_curator` | Proposed profile additions and corrections | writing |
| `cover_letter_writer` | A covering letter from registered evidence | writing |
| `hiring_manager` | What a strong application must show — **isolated** | writing |
| `workspace_agent` | The chat's agent loop: one tool call, question or reply per turn | writing |
| `profile_extractor`, `intake_interviewer` | Building a new profile from its documents | writing |

The earlier posting graph (`requirement_extractor`, `relevance_judge`,
`company_investigator`, `posting_verifier`, `evaluate_posting`) was never
called in production and was removed on 24 Sep 2026: discovery verifies
postings and employers in code (`job_quality.verify_posting`,
`assess_company`), and `fit_analyst` replaced the extractor and the judge.

## Two tiers, not a model per agent

The reading tier does extraction and classification; the writing tier produces
text that reaches an employer. Choosing a model per tier in Settings is the
cost control.

## Routing is deterministic

The workflow decides which specialist runs, not a model. A run's cost is
therefore predictable, and a routing mistake is a code bug rather than a prompt
bug. When a specialist's answer does not match its schema, `AgentTeam` asks the
same endpoint once more with the first validation errors
(`graph._repairing`); under Auto a failure also moves the step to the next plan.

## The requirement matrix (`services/fit.py`)

One verified list per job answers "does she have what this job asks for?", and
every feature reads the same list:

1. **The AI proposes.** `fit_analyst` (reading tier, free plans only:
   `fit.fit_team` copies the Auto route with every paid endpoint switched off,
   and is `None` when no free plan is ready) returns each requirement with its
   category (required, preferred, responsibility), a verbatim excerpt, a status
   (met, partial, missing) and the evidence ids that meet it, plus hard
   blockers such as a clearance or a required licence.
2. **The code verifies** (`fit.verify`). An excerpt that is not in the posting
   (after whitespace and typography are normalised) is dropped; an evidence id
   not in her registry (`fit.catalogue`: registered skills, resume-ready
   projects, employment, education, coursework, languages) is removed, and an
   item left without proof becomes missing; coursework alone is at most
   partial; a requirement that is itself a never-claim skill is always missing,
   and one that only names such a tool among its bracketed examples ("web
   development (Python, SQL, React)") is at most partial; work-permit wording is
   dropped from the blockers (the sponsorship gate owns it). The prompt keeps
   personality lines out and turns an "including X, Y, Z" list into one item.
3. **Rules when no free plan is free.** `fit._rules` reads the posting with the
   fixed vocabulary plus her own skill names and the never-claim list; a field
   (ML, AI, deep learning) counts as met by the tools that prove it
   (`FIELD_EVIDENCE`: PyTorch, scikit-learn, computer vision, …); an item
   with no nameable skill is `unknown` and not scored. The page and the
   rationale say which method checked it.
4. **The score is arithmetic** (`fit.score`, 0–100): requirement coverage 75
   (required 55, preferred 10, responsibilities 10, re-weighted over the
   categories present; met 1, partial 0.5, and each category counts
   `PRIOR_ITEMS = 2` imaginary half-met items so a score read from two items is
   less certain than one read from twelve), role family and seniority 15,
   location 10. A job is eligible with no blocker, a score of at least
   `FIT_THRESHOLD` (65) and at least half its must-haves met.
   `backend/scripts/eval_fit.py` prints the score of every saved job, every job
   she removed and recent exclusions, without writing anything, to calibrate the
   threshold.
5. **Cached** in `job_fit` (migration 8), keyed by the posting's hash, her
   evidence hash and `FIT_VERSION`; a rules check is upgraded to an AI one when
   a free plan is back. `jobs.fit_score` and `fit_rationale` carry the result.

Where it is used: discovery (the hard gates first, then the rules score picks a
shortlist of `limit + DISCOVERY_SPARES`, and only the shortlist gets the AI
check, two at a time, at most three rounds), tailoring (the `job_tailor`
payload's `requirements`; verified skills proving a met must-have are topped up,
at most `TOP_UP_SKILLS`), resume coverage (`AssessmentService.requirements`,
`SCORING_VERSION` v6: the aliases are the requirement and the evidence terms
that met it), the study plan's genuine gaps, the research comparison
(`verified_requirement_check`), the chat's snapshot and its `job_fit` tool, and
Resume Studio's *Check match* step (`GET`/`POST /api/v2/jobs/{id}/fit`).

## The isolation boundary

`hiring_manager` must never see candidate information. `AgentTeam.run()`
refuses it, and `run_isolated()` builds a payload of exactly
`job_description` and `public_company_research`. `tests/test_ai_agents.py`
asserts both halves.

## Providers

OpenRouter reaches GPT, Claude, Gemini and Kimi with one key and is the
default. Each provider can also be used directly with its own key. Three local
runtimes need no key at all and run on a subscription's usage limits instead:
Codex (the ChatGPT app's `codex exec`), Claude Code (the `claude` CLI that
ships with the Claude app and the Claude Code extension) and Kimi Code (the
`kimi` CLI from the Kimi Code app, `backend/ai/kimi_cli.py`). Codex and Claude
Code take the prompt on stdin and return a schema-checked JSON object. Kimi
Code takes the prompt as an argument (`kimi -p … --output-format stream-json`),
reads no stdin, and answers in its default model; prompts over 20,000
characters are written to `prompt.md` in its fresh working directory and the
argument points at that file. Its stdout is captured to a file rather than a
pipe, because on Windows the CLI can lose buffered pipe output on long runs;
when a turn fails without output, the error is read back from the CLI's own
session log. All three CLIs run one-shot in restricted mode with no session,
settings, MCP servers or CLAUDE.md files, and allow only web search and fetch
when the action needs the web. Neither Claude Code nor Kimi Code has Gmail
access, so the email worker stays on Codex. Claude Code and Kimi Code serve
both the agent runs (discovery, research, match, study plan) and the
specialists above, chosen per tier in Settings.

One choice on the Settings page drives every agent: it becomes the gateway
default and, for providers the specialist team can run on, both tiers. Keys
pasted on that page are written to `career-dashboard/.env` (0600, git-ignored),
checked with a free model-list call, and never returned to the browser. With a
key, OpenRouter, Gemini and Kimi join the gateway through LangChain
(`HostedProvider` in `providers.py`). Work the chosen provider cannot do is not
failed but routed: `resolve()` sends web research to the first ready provider
with web search (Claude Code, Codex, Kimi Code, OpenAI) and Gmail to Codex, and Settings
lists where each kind of work will run. A provider named explicitly is still
refused when it lacks the capability.

## Auto: free plans first (the default)

`backend/ai/router.py` routes like OpenRouter across her own plans. The
default choice, `auto`, sends each step down an ordered route, **Kimi Code
(K3) → Codex (GPT-6-Astra to write, GPT-6-Luna to read) → Claude Code (Opus to
write, Sonnet to read) → Azure OpenAI**, to the first endpoint that is switched
on, set up here, able to do the step (Gmail: Codex only), not resting, and, for
Azure, inside the daily paid-call limit. Paid endpoints always sort last. A
failure moves the step to the next endpoint; a usage-limit failure also rests
that plan until the reset time the CLI printed (`limits.py` reads Claude's
"resets 5:40pm (zone)", Codex's "try again in 2 hours 13 minutes", Claude's
epoch form; an hour when it says nothing, two minutes for an Azure 429), and
three failures in a row rest it for 15 minutes. The rest state lives in
`.ai-plan-health.json` beside the shared `.env`, so every profile sees it. The
gateway (`RouterProvider`), the specialist team (`AgentTeam` tiers set to
`auto`) and Daily Search all use the same `router.route`; each call records the
endpoint that served it (`ai_calls`, the Agents trace "Auto → Codex · …") and
each switch as a `provider_fallback` activity event.

Every call on a free plan is also metered against that plan's 5-hour window:
real tokens when the CLI reports them (Claude Code), otherwise an estimate from
the text plus a per-call overhead. Each plan's limit per window is what she
typed in Settings, else the one learned when the plan last ran out (the tokens
metered in that window), else a starting guess. Settings shows each plan's
percentage used; Daily Search shows how a search's estimated tokens will fill
the plans in route order before it starts.

The daily limit (`ai_policy.daily_call_limit`) counts **paid** calls only; free
plan calls never count, since each plan has its own usage window. When it is
used up, Auto skips Azure until tomorrow and keeps working on the free plans;
a paid provider chosen by name is stopped. `workspace.py ai-status` and
`ai-wake --provider X` show and clear plan state from any AI app.

Every run records its stages and AI calls in `agent_run_events` (provider,
model, web or not, seconds, new or reused, error); `GET /api/v2/agents/activity`
serves them to the Agents tab. Nothing in the trace is sent to a model.

Model lists are fetched from each provider and cached for a day, so a new
release appears in the dropdown without a code change. Test connection makes
the smallest real call, so a bad key or an exhausted quota fails there rather
than in the middle of a run. Provider errors are reported by class — "the
account is out of credits" — because the raw text embeds key-management URLs.
