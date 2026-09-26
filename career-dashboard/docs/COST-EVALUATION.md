# Cost and token evaluation: one end-to-end run

> Historical source-app evaluation. Its one-page and model/pricing assumptions are not the current
> Chetan contract. Check live Settings and `data/config/profile.yml` before using any estimate.

Written 19 September 2026. Covers one run of the whole loop, **find jobs → gate → verify → research → tailor → validate → study plan → ready-to-submit resume**, on every runtime this workspace can use: Claude Code, Codex CLI, Gemini, Kimi, and OpenRouter. Then a recommendation for the OpenRouter setup you are planning: which model runs which agent when accuracy comes first and cost second.

> **Status: estimate, not measurement.** The workspace does not yet record token counts. `ai_calls` has `input_tokens` / `output_tokens` columns, but every one of the 18 rows from 18 September is NULL, and `AgentTeam.on_usage` has no consumer. Every token figure below is derived from file sizes in this repo and the payload caps in `backend/ai/agents/graph.py`. Section 8 says how to replace them with real numbers after one measured run; the calculator that produced the tables is reproduced in section 9 so you can re-run it with measured inputs.

> **Superseded in part (24 Sep 2026).** The four posting specialists costed below (`requirement_extractor`, `relevance_judge`, `company_investigator`, `posting_verifier`) were never called in production and have been removed. A job's fit is now one `fit_analyst` call (reading tier) on a **free plan only**, and only for the discovery shortlist (`limit + DISCOVERY_SPARES` per round, at most three rounds), cached per posting in `job_fit`; with no free plan free it is checked by rules at no cost. Posting and employer checks run in code. See `docs/AI-AGENTS.md`, *The requirement matrix*. The per-call estimates for the writing agents still stand.

---

## 1. What one run is

`/hunt` (default 10 companies) or "give me 10 companies". Steps, with what each one costs in model calls:

| # | Step | Done by | Model calls |
|---|---|---|---|
| 1 | Load the facts (`AGENTS.md`, 11 context files, `evidence.yml`, 4 config files, 3 workflow modes) | reading | none on its own, but it is the fixed context every later call carries |
| 2 | Age the tracker (`workspace.py age`, `summary`) | code | 0 |
| 3 | Find roles: tracked portals (`--preset portals`), AI discovery, Indeed/ATS search | code + model | discovery: 1 call with web; ~15 leads for a target of 10 |
| 4 | Pull the full JD, sponsorship gate, never-re-apply check | code (`sponsorship.py`, `reapply.py`) | 0, the gate is deterministic |
| 5 | Verify the link | code + model | `posting_verifier` per lead |
| 6 | Rank and pick, assign signature projects | code + model | `relevance_judge`, `requirement_extractor`, `company_investigator` per lead |
| 7a | `career.py prepare JOB_ID` → deterministic draft from registry wording | code | 0 |
| 7b | Company research → `company-research.md` | model, web | 1 per application |
| 7c | Tailor → `resume.tex` (registry facts only, evidence tags, recruiter audit) | model | `resume_tailor` 1–2 per application |
| 7d | Validate, compile, render, fit to one page | code (`validate_resume.py`, Tectonic) | 0; one optional visual-review call on `page-01.png` |
| 7e | Honest evaluation (hiring-manager view, profile comparison) | model | `hiring_manager` (isolated) + `profile_comparison` per application |
| 7f | Study plan → `study-plan.md` | model | 1 per application |
| 8 | `SUMMARY.md` regenerates, report table | code | 0 |

Roughly half the pipeline is deterministic code and costs nothing. That matters for the recommendation: the gates that decide *whether Chetan can lawfully be hired* and *whether she already applied* never touch a model, so a cheap model in the wrong place cannot cause the two worst failures.

---

## 2. Two ways to execute the same run

The cost of a run depends far more on **how** the agents are driven than on which model you pick.

**Path A, agentic CLI.** `/hunt` inside Claude Code, Codex CLI, Gemini CLI or Kimi Code. One long tool loop: read files, run scripts, fetch pages, write LaTeX, look at the rendered PNG, fix, repeat. Every turn resends the entire growing context (system prompt, tool schemas, all files read so far, all tool output). Cost grows with roughly the square of the turn count; auto-compaction caps the context but the loop still runs 300+ turns for ten companies.

**Path B, in-app specialists.** `backend/ai/agents/graph.py`: nine narrow specialists (`requirement_extractor`, `relevance_judge`, `company_investigator`, `posting_verifier`, `resume_tailor`, `profile_curator`, `hiring_manager`, `cover_letter_writer`, `mail_classifier`) plus the `discovery`, `research` and `study_plan` runs in `services/agents.py`. Each is one short structured call with a fixed payload; routing is code, not a model; there is no loop. This is the path that goes through OpenRouter (`backend/ai/models.py`).

Same deliverables, very different bills:

| | Path A (agentic CLI) | Path B (in-app specialists) |
|---|---|---|
| Model calls per 10-app run | ~345 turns | ~121 calls |
| Input tokens billed (sum over calls) | ~52.7M (≈153k average context per turn, compaction-capped) | ~0.78M |
| Output tokens | ~130k | ~122k |
| Cost on Claude Sonnet 5, 90% cache hits | **≈ $24** | **≈ $2.80** (mixed config B below) |
| Cost on Claude Opus 5 | ≈ $60 | ≈ $7 (Opus for everything) |
| Failure mode observed 18 Sep | subscription usage limit reached mid-batch | app's own daily call budget reached |

Path B is 10–20× cheaper for the same output because it never resends the conversation. If cost matters at all, the batch should run through the specialists, and the CLI should be reserved for the interactive work (Chetan's chat, fixes, judgement calls) where a human is in the loop anyway.

### 2.1 Token profile, Path A (assumptions)

| Quantity | Value | Where it comes from |
|---|---|---|
| Base context after step 1 | 60k | system prompt + tool schemas ≈ 18k; `AGENTS.md` ×2 2.9k+0.7k; context files 13.8k; `evidence.yml` 9.7k; `profile.yml` 3.7k; `portals/regions/sponsorship.yml` 6.3k; `_shared/_profile/batch-resumes.md` 5.4k; `hunt.md` 1k (bytes ÷ 4) |
| Discovery + gating phase | 45 turns, +90k tool output, 10k output tokens | search results 5–8k each, JDs 1–4k each, check outputs |
| Per application | 30 turns, +35k tool output, 12k output tokens | 4–6 research fetches, read draft (2.3k), write `resume.tex` (2.3k, usually twice), validate, view PNG (~1.5k), evidence map, study plan (1.1k) |
| Compaction ceiling | 160k | Claude Code auto-compacts; other CLIs similar |
| Cache hit rate | 90% | stable prefix; Claude Code caches automatically. Set to 0% for the worst case column |

Per application: ~9.5M input-token-turns, ~22k output. Ten applications: ~52.7M / ~130k.

### 2.2 Token profile, Path B (assumptions)

Per lead evaluated (15 leads to end with 10), cheap tier, payload caps from `graph.py`:

| Agent | Input | Output | Note |
|---|---:|---:|---|
| `posting_verifier` | 5,500 | 300 | `page_text[:20000]` chars |
| `relevance_judge` | 3,000 | 300 | posting + candidate summary |
| `company_investigator` | 6,500 | 600 | `posting_text[:8000]` + ~4k web results |
| `requirement_extractor` | 5,500 | 1,200 | `description[:20000]`; excerpts not found verbatim are discarded in code |

Per run: `discovery` 21,000 in / 3,000 out (guide + profile + portals + ~15k web results).

Per application, strong tier:

| Agent | Input | Output | Note |
|---|---:|---:|---|
| `company_research` | 10,000 | 1,500 | guide 0.3k + job + ~8k fetched pages |
| `hiring_manager` | 3,500 | 1,000 | isolated: JD + public research only, never the profile |
| `profile_comparison` | 10,000 | 1,500 | job + research + hiring + profile |
| `resume_tailor` | 15,000 | 2,500 | evidence registry 9.7k + draft + JD + requirements + audit rules |
| `visual_review` | 2,000 | 300 | one look at `page-01.png`, optional |
| `study_plan` | 5,000 | 1,500 | guide 0.7k + job + gaps + registered skills + profile |

Totals for 10 applications / 15 leads: cheap 307.5k in / 36k out; strong 476k in / 86k out; **783.5k in / 122k out, 121 calls**. One application with 2 leads: ~107k in / 16k out.

Web search on top: ~30–40 searches per run. OpenRouter Exa `:online` $0.007/request ≈ $0.25; OpenAI native $10/1k calls ≈ $0.35; Gemini grounding is free for the first 5,000/month, then $14/1k. Negligible next to the model cost, but real.

---

## 3. Price sheet (verified 19 September 2026)

Per 1M tokens, USD, standard tier. Cached-input is the read price for a prompt-cache hit.

| Model | Input | Cached input | Output | Notes |
|---|---:|---:|---:|---|
| `anthropic/claude-fable-5-1` | 10.00 | 0.25 | 50.00 | cache write 1.25× (5-min) / 2× (1-h); batch −50% |
| `anthropic/claude-opus-5` | 5.00 | 0.50 | 25.00 | same cache/batch rules; 1M context |
| `anthropic/claude-sonnet-5` | 2.00 | 0.20 | 10.00 | |
| `anthropic/claude-haiku-4-5` | 1.00 | 0.10 | 5.00 | 200k context |
| `openai/gpt-5.6-sol` | 4.00 | 0.40 | 20.00 | promotional; list rate 5.00 / 30.00 |
| `openai/gpt-5.6-terra` | 2.00 | 0.20 | 12.00 | |
| `openai/gpt-5.6-luna` | 0.20 | 0.02 | 1.20 | |
| `openai/gpt-5.5` | 5.00 | 0.50 | 30.00 | |
| `openai/gpt-5.4-mini` | 0.75 | 0.075 | 4.50 | |
| `openai/gpt-5.3-codex` | 1.75 | 0.175 | 14.00 | the model behind Codex CLI when billed by API |
| `google/gemini-3.1-pro` | 2.00 | 0.20 | 12.00 | 4.00 / 18.00 above 200k context |
| `google/gemini-3.7-flash` | 0.75 | 0.075 | 3.75 | **doubles on 1 Jan 2027** (1.50 / 7.50) |
| `google/gemini-3.5-flash-lite` | 0.30 | 0.03 | 2.50 | |
| `google/gemini-2.5-flash-lite` | 0.10 | 0.01 | 0.40 | legacy; current `catalog.py` cheap default |
| `moonshotai/kimi-k3` | 3.00 | 0.30 | 15.00 | 1M context |
| `moonshotai/kimi-k2.6` | 0.95 | 0.16 | 4.00 | 262k context |

OpenRouter adds **no per-token markup**; it charges 5.5% when you buy credits (5% crypto, $0.80 minimum) and nothing for BYOK under $25k/month. Batch APIs (Anthropic, OpenAI, Gemini) are −50% but only for work nobody is waiting on; the 21-day ghost sweep and email classification qualify, a resume Chetan is waiting for does not.

---

## 4. Cost per run, Path A (agentic CLI, API-metered)

What a `/hunt 10` costs if the CLI is billed per token. Subscription plans are in section 6.

| Model | 10 apps, 90% cache | 10 apps, no cache | 1 app, 90% cache |
|---|---:|---:|---:|
| `anthropic/claude-fable-5-1` | $84.27 | $665.56 | $15.15 |
| `anthropic/claude-opus-5` | $59.93 | $332.78 | $10.79 |
| `anthropic/claude-sonnet-5` | $23.97 | $133.11 | $4.32 |
| `anthropic/claude-haiku-4-5` | $11.99 | $66.56 | $2.16 |
| `openai/gpt-5.6-sol` | $42.67 | $213.50 | $7.68 |
| `openai/gpt-5.6-terra` | $21.60 | $107.01 | $3.88 |
| `openai/gpt-5.6-luna` | $2.16 | $10.70 | $0.39 |
| `openai/gpt-5.5` | $53.99 | $267.52 | $9.71 |
| `openai/gpt-5.3-codex` | $19.35 | $94.09 | $3.48 |
| `google/gemini-3.1-pro` | $21.60 | $107.01 | $3.88 |
| `google/gemini-3.7-flash` | $8.00 | $40.03 | $1.44 |
| `google/gemini-3.5-flash-lite` | $3.33 | $16.14 | $0.60 |
| `moonshotai/kimi-k3` | $32.00 | $160.12 | $5.76 |
| `moonshotai/kimi-k2.6` | $13.12 | $50.61 | $2.36 |

Read the middle column as "what a cache miss storm costs": if a CLI's caching breaks (a timestamp in the system prompt, a tool list that changes order), the same run costs 5–8× more. Haiku, Luna and Flash-Lite are listed for completeness; none of them should drive a 300-turn agentic loop that writes candidate-facing prose.

By runtime, as you named them:

| Runtime | Model it would run | 10 apps | Comment |
|---|---|---:|---|
| **Claude Code** | Sonnet 5 / Opus 5 | $24 / $60 | best-in-class prompt caching, 1M context; the runtime this workspace is built around |
| **Codex CLI** | gpt-5.3-codex / gpt-5.6-terra | $19 / $22 | the app's `codex-runtime` provider; free on a ChatGPT plan, see section 6 |
| **Gemini** (CLI or API) | gemini-3.7-flash / 3.1-pro | $8 / $22 | the free Google-login CLI path ended 18 June 2026; individuals now pay by API key |
| **Kimi Code** | kimi-k2.6 / k3 | $13 / $32 | CLI is free and open source; usage from a membership or the API |
| **OpenRouter** | any of the above | same as the row it routes to | no markup; you also get the Path B numbers below, which is the point |

---

## 5. Cost per run, Path B (in-app specialists via OpenRouter)

### 5.1 Single model for everything

| Model | 10 apps | 1 app |
|---|---:|---:|
| `anthropic/claude-fable-5-1` | $13.94 | $1.88 |
| `anthropic/claude-opus-5` | $6.97 | $0.94 |
| `anthropic/claude-sonnet-5` | $2.79 | $0.38 |
| `anthropic/claude-haiku-4-5` | $1.39 | $0.19 |
| `openai/gpt-5.6-sol` | $5.57 | $0.75 |
| `openai/gpt-5.6-terra` | $3.03 | $0.41 |
| `openai/gpt-5.6-luna` | $0.30 | $0.04 |
| `openai/gpt-5.5` | $7.58 | $1.02 |
| `openai/gpt-5.3-codex` | $3.08 | $0.41 |
| `google/gemini-3.1-pro` | $3.03 | $0.41 |
| `google/gemini-3.7-flash` | $1.05 | $0.14 |
| `google/gemini-3.5-flash-lite` | $0.54 | $0.07 |
| `moonshotai/kimi-k3` | $4.18 | $0.56 |
| `moonshotai/kimi-k2.6` | $1.23 | $0.17 |

### 5.2 Mixed configurations (one model per role)

Roles: **writer** = `resume_tailor` (and `cover_letter_writer`, `profile_curator`); **judge** = `hiring_manager`, `profile_comparison`, `visual_review`; **research** = `discovery`, `company_research` (web); **cheap** = the four per-lead extractors; **other** = `study_plan`.

| Config | writer | judge | research | cheap | other | 10 apps | 10 apps, registry cached | 1 app |
|---|---|---|---|---|---|---:|---:|---:|
| **A. Max accuracy** | Opus 5 | gpt-5.6-sol | gemini-3.7-flash | gemini-3.7-flash | Opus 5 | $3.70 | $3.15 | $0.41 |
| **B. Recommended** | Opus 5 | gpt-5.6-terra | gemini-3.7-flash | gemini-3.7-flash | Sonnet 5 | **$2.79** | **$2.39** | **$0.32** |
| C. One-family Claude | Opus 5 | Opus 5 | Sonnet 5 | Haiku 4.5 | Sonnet 5 | $4.01 | $3.55 | $0.48 |
| D. Budget | Sonnet 5 | gemini-3.7-flash | gemini-3.7-flash | gpt-5.6-luna | gemini-3.7-flash | $1.13 | $0.95 | $0.14 |
| E. Current `catalog.py` defaults | Sonnet 5 | Sonnet 5 | Sonnet 5 | gemini-2.5-flash-lite | Sonnet 5 | $1.86 | $1.65 | $0.25 |

"Registry cached" assumes the evidence registry and system prompts (~40% of strong-tier input) hit the prompt cache across the ten `resume_tailor` calls in one batch.

### 5.3 Per-agent breakdown, config B, 10 apps / 15 leads

| Agent | Model | Calls | Input | Output | Cost |
|---|---|---:|---:|---:|---:|
| `posting_verifier` | gemini-3.7-flash | 15 | 82,500 | 4,500 | $0.08 |
| `relevance_judge` | gemini-3.7-flash | 15 | 45,000 | 4,500 | $0.05 |
| `company_investigator` | gemini-3.7-flash | 15 | 97,500 | 9,000 | $0.11 |
| `requirement_extractor` | gemini-3.7-flash | 15 | 82,500 | 18,000 | $0.13 |
| `discovery` | gemini-3.7-flash | 1 | 21,000 | 3,000 | $0.03 |
| `company_research` | gemini-3.7-flash | 10 | 100,000 | 15,000 | $0.13 |
| `hiring_manager` | gpt-5.6-terra | 10 | 35,000 | 10,000 | $0.19 |
| `profile_comparison` | gpt-5.6-terra | 10 | 100,000 | 15,000 | $0.38 |
| `resume_tailor` | **claude-opus-5** | 10 | 150,000 | 25,000 | **$1.38** |
| `visual_review` | gpt-5.6-terra | 10 | 20,000 | 3,000 | $0.08 |
| `study_plan` | claude-sonnet-5 | 10 | 50,000 | 15,000 | $0.25 |
| **Total** | | **121** | **783,500** | **122,000** | **$2.79** |

Half the bill is the one agent whose words Chetan actually sends. That is where the money should be.

---

## 6. Subscription view (Claude Code, Codex, Gemini, Kimi)

If the CLI runs on a plan instead of an API key, the marginal cost is $0 and the constraint is the plan's quota. Plan prices as of September 2026; **quotas are not published in tokens, so verify against your account**:

| Runtime | Plans | What one 10-app agentic run (~$24–60 API-equivalent) means |
|---|---|---|
| Claude Code | Pro $20; Max 5× $100; Max 20× $200. One 5-hour session cap plus a weekly cap shared with the Claude apps | On Pro, one batch will not fit in a single 5-hour window. Evidence: on 18 Sep, 18 short in-app calls plus interactive work already produced 2 "usage limit reached" failures (`ai_calls`) and 2 more in `agent_runs`. Max 5× is the realistic floor for a daily batch; Max 20× for several |
| Codex CLI | ChatGPT Plus $20; Pro $100 (5× Plus); Pro $200 (20×) | same shape as Claude; the app's `codex-runtime` provider is "free" only while the plan quota lasts |
| Gemini CLI | free Google-login path discontinued 18 June 2026; individuals pay by API key, teams need a Code Assist licence | effectively the API rows in section 4 |
| Kimi Code | memberships $19 / $39 / $99 / $199; the CLI itself is free | usage draws from the membership or from the API rows above |

Also note the app's own gate: on 18 Sep three `agent_runs` failed with "Daily AI call budget reached" (Agent control). Raise it before a batch or the run stops at the cheapest step.

---

## 7. Recommendation for the OpenRouter setup

You said accuracy first, then cost. Where accuracy actually lives in this pipeline:

1. **The resume text** (`resume_tailor`). The one place a hallucination reaches an employer. Strongest model, no compromise.
2. **The judgement** (`hiring_manager`, `profile_comparison`, visual review). Decides which gaps are real and whether the page is release-ready. Needs a strong model **from a different family than the writer**: a judge from the same family as the writer systematically prefers its own phrasing (self-preference bias), which is exactly what an honest match assessment must not do.
3. **Extraction and verification** (`requirement_extractor`, `posting_verifier`, `relevance_judge`, `company_investigator`, `mail_classifier`). All of these are protected by deterministic guards in code: requirement excerpts that are not verbatim substrings are discarded; the sponsorship gate and re-apply rules run without a model; "expired" needs quoted closure wording. Because the code catches the failure, a cheap model here is safe and the volume (15 leads × 4 agents) is where the savings are.
4. **Research** (`discovery`, `company_research`). Needs web access and a long context more than it needs reasoning depth.

### 7.1 Agent → model map (config B)

| Role | Agents | Model on OpenRouter | Why this one | Fallback (`models: [...]`) |
|---|---|---|---|---|
| writer | `resume_tailor`, `cover_letter_writer`, `profile_curator` | `anthropic/claude-opus-5` | best evidence discipline for constrained rewriting; Anthropic's own measurements put Opus 5 level with Fable 5 on a coding benchmark (91.7% vs 91.3%) at ~60% of the cost, and Fable 5.1 buys nothing here for 2× the price | `anthropic/claude-sonnet-5` |
| judge | `hiring_manager`, `profile_comparison`, `visual_review` | `openai/gpt-5.6-terra` | strong, cross-family, $2/$12; step up to `gpt-5.6-sol` (config A, +$0.90 per batch) if you see the judge missing real gaps | `google/gemini-3.1-pro` |
| research (web) | `discovery`, `company_research`, `company_investigator` | `google/gemini-3.7-flash` | 1M context, native grounding, $0.75/$3.75 through 31 Dec 2026 | `openai/gpt-5.6-terra:online` |
| cheap | `requirement_extractor`, `posting_verifier`, `relevance_judge`, `mail_classifier` | `google/gemini-3.7-flash` | reliable JSON-schema output, cheaper than Haiku 4.5 with a 1M context; `gpt-5.6-luna` if you want to go lower (config D) | `anthropic/claude-haiku-4-5` |
| other | `study_plan` | `anthropic/claude-sonnet-5` | prose Chetan reads but never sends; Sonnet is enough | `google/gemini-3.7-flash` |

Cost: about **$2.80 per 10-application batch**, ~$0.32 per single application, before caching. Thirty batches a month is under $90.

What I would **not** do:
- Fable 5.1 anywhere in this pipeline. The tasks are short and schema-bound; the extra reasoning depth does not show up in a one-page resume, and cache reads at $0.25/M only pay off in long loops you are not running.
- Kimi on the writer or judge. K2.6 is a good cheap extractor (config D alternative), but through OpenRouter its hosts vary in quantization and tool-calling reliability; if you use it, pin `provider.order: ["moonshotai"]` and `require_parameters: true`.
- One model for everything at Opus prices ($6.97) when config B gets Opus on the only agent that needs it for $2.79.

### 7.2 Changes in the app to get there

The app currently has two tiers (`strong`, `cheap`) in `backend/ai/catalog.py` with OpenRouter defaults `anthropic/claude-sonnet-5` and `google/gemini-2.5-flash-lite`. To run config B:

1. **Defaults.** Move the cheap default to `google/gemini-3.7-flash` (2.5-flash-lite is a legacy model and the weakest structured-output model in the price sheet) and the strong default to `anthropic/claude-opus-5`.
2. **A third tier.** Add `judge` to `catalog.TIERS` and mark `hiring_manager` and the comparison step `tier="judge"` in `specialists.py` / `services/agents.py`, so the writer and the judge can be different families. Without this, config C (all-Claude, $4.01) is the best you can express today.
3. **Provider preferences on every OpenRouter request** (`backend/ai/models.py`, `ChatOpenAI(extra_body=...)`):

```json
{
  "provider": {
    "order": ["anthropic"],
    "allow_fallbacks": true,
    "require_parameters": true,
    "data_collection": "deny"
  },
  "usage": { "include": true }
}
```

   - `require_parameters: true` refuses any host that ignores `response_format` / tool schemas, which is what `with_structured_output` depends on. Without it a request can land on a provider that silently drops the schema and you get a parsing error from `graph.py`.
   - `data_collection: "deny"` because every strong-tier prompt carries Chetan's profile. OpenRouter itself does not log prompts by default; this extends that to the upstream host.
   - `order` per model family: `["anthropic"]` for Claude, `["openai"]` for GPT, `["google-ai-studio"]` or `["google-vertex"]` for Gemini (confirm the slug on the model page), so a Claude call is never served by a third-party re-host.
   - `usage.include` returns token counts in the response so `AgentTeam._record` can finally fill `ai_calls.input_tokens` / `output_tokens`.
4. **Fallback models**, not fallback providers, for resilience: pass `models: ["anthropic/claude-opus-5", "anthropic/claude-sonnet-5"]` for the writer, and so on per row above. A rate limit or outage then degrades one tier, not the batch.
5. **Prompt caching on the writer.** The evidence registry (~9.7k tokens) and the system text are identical across all ten `resume_tailor` calls in a batch. Put them first in the message, mark the block with `cache_control: {"type": "ephemeral"}` (OpenRouter forwards it to Anthropic; OpenAI and Gemini cache prefixes automatically). Saves ~15% of the batch (the "registry cached" column) and more as the registry grows.
6. **Web search.** Either append `:online` to the research models (Exa, $0.007/request) or, cheaper and more controllable, keep fetching pages in code as `posting_verifier` already does and hand the text to Gemini 3.7 Flash. The pipeline already sanitises fetched text as untrusted data (`GROUNDING`), so the second option adds no new risk.
7. **Housekeeping.** `providers.py` still hard-codes `claude-sonnet-4-6` and `gpt-5.4` model lists that are out of date with `catalog.py`; they only matter on the direct-key path, but they will reject a valid current model.

### 7.3 How to know the accuracy is actually there

Cost tables are easy; the accuracy claim needs a check that survives a model swap. The cheapest useful eval for this workspace:

- **Writer:** 20 saved JDs × the current registry → `resume_tailor` → run `validate_resume.py` and the evidence-tag check. Metrics: share of lines with a valid `% EVIDENCE:` tag (must be 100%), banned-filler hits (0), one-page pass rate, and a human read of five for voice. Compare Opus 5, Sonnet 5, gpt-5.6-terra on the same 20; keep the one with zero unsupported claims first, then cheapest.
- **Judge:** the same 20, hand-label the real gaps once, score each judge model on gap recall. A judge that misses a "must have Power BI" gap is worse than an expensive one.
- **Extractors:** the verbatim-excerpt discard rate per model. A cheap model with a 30% discard rate is costing you requirements; Gemini 3.7 Flash vs gpt-5.6-luna vs Haiku 4.5 on 30 JDs settles it for under a dollar.

Run it once before switching, and again whenever a default model changes. Never decide from one run.

---

## 8. Turning estimates into measurements

1. Wire `AgentTeam(on_usage=...)` to `INSERT`/`UPDATE` `ai_calls.input_tokens`, `output_tokens` (columns already exist). With OpenRouter add `"usage": {"include": true}`; the Claude Code path already returns `usage` from `claude -p --output-format json` and currently drops it.
2. Add `cached_input_tokens` and `cost_usd` columns so cache behaviour is visible; a zero cached count across a batch means a silent invalidator.
3. Run one `/hunt 10` on each path, then `SELECT action, model, SUM(input_tokens), SUM(output_tokens), COUNT(*) FROM ai_calls WHERE day = ? GROUP BY 1,2` and paste the numbers into section 2. For Claude Code interactive runs, `/cost` at the end of the session gives the same total.
4. Replace the assumption tables above with the measured per-call medians and re-run the calculator in section 9.

---

## 9. Calculator

The tables in sections 2, 4 and 5 were produced by this script. Change the `PRICES` and the assumption blocks and re-run.

```python
PRICES = {  # $/1M: (input, cached-input read, output)
    "anthropic/claude-fable-5-1": (10.00, 0.25, 50.00),
    "anthropic/claude-opus-5":    (5.00, 0.50, 25.00),
    "anthropic/claude-sonnet-5":  (2.00, 0.20, 10.00),
    "anthropic/claude-haiku-4-5": (1.00, 0.10, 5.00),
    "openai/gpt-5.6-sol":         (4.00, 0.40, 20.00),
    "openai/gpt-5.6-terra":       (2.00, 0.20, 12.00),
    "openai/gpt-5.6-luna":        (0.20, 0.02, 1.20),
    "openai/gpt-5.3-codex":       (1.75, 0.175, 14.00),
    "google/gemini-3.1-pro":      (2.00, 0.20, 12.00),
    "google/gemini-3.7-flash":    (0.75, 0.075, 3.75),
    "google/gemini-2.5-flash-lite": (0.10, 0.01, 0.40),
    "moonshotai/kimi-k3":         (3.00, 0.30, 15.00),
    "moonshotai/kimi-k2.6":       (0.95, 0.16, 4.00),
}

# Path A: agentic CLI. Every turn resends the whole context.
BASE_CTX, COMPACT_CEILING, CACHE_HIT = 60_000, 160_000, 0.90
DISC_TURNS, DISC_ADDED, DISC_OUT = 45, 90_000, 10_000
APP_TURNS, APP_ADDED, APP_OUT = 30, 35_000, 12_000

def path_a_tokens(n_apps=10):
    inp = DISC_TURNS * min(BASE_CTX + DISC_ADDED / 2, COMPACT_CEILING)
    out, ctx = DISC_OUT, min(BASE_CTX + DISC_ADDED, COMPACT_CEILING)
    for _ in range(n_apps):
        inp += APP_TURNS * min(ctx + APP_ADDED / 2, COMPACT_CEILING)
        out += APP_OUT
        ctx = min(ctx + APP_ADDED, COMPACT_CEILING)
    return int(inp), int(out)

def price_a(model, n_apps=10, cache_hit=CACHE_HIT):
    pin, pcache, pout = PRICES[model]
    inp, out = path_a_tokens(n_apps)
    write = 1.25 if model.startswith("anthropic/") else 1.0   # cache-write premium
    return (inp * (1 - cache_hit) / 1e6 * pin * write
            + inp * cache_hit / 1e6 * pcache + out / 1e6 * pout)

# Path B: in-app specialists. (input, output) per call.
CHEAP_PER_LEAD = {"posting_verifier": (5_500, 300), "relevance_judge": (3_000, 300),
                  "company_investigator": (6_500, 600), "requirement_extractor": (5_500, 1_200)}
DISCOVERY = (21_000, 3_000)
PER_APP = {"company_research": (10_000, 1_500, "research"), "hiring_manager": (3_500, 1_000, "judge"),
           "profile_comparison": (10_000, 1_500, "judge"), "resume_tailor": (15_000, 2_500, "writer"),
           "visual_review": (2_000, 300, "judge"), "study_plan": (5_000, 1_500, "other")}

def cost(model, inp, out, cached=0.0):
    pin, pcache, pout = PRICES[model]
    write = 1.25 if model.startswith("anthropic/") and cached else 1.0
    return inp * (1 - cached) / 1e6 * pin * write + inp * cached / 1e6 * pcache + out / 1e6 * pout

def price_b(cfg, n_apps=10, leads=15, cached=0.0):
    total = sum(cost(cfg["cheap"], i * leads, o * leads, cached) for i, o in CHEAP_PER_LEAD.values())
    total += cost(cfg["research"], *DISCOVERY, cached)
    total += sum(cost(cfg[role], i * n_apps, o * n_apps, cached) for i, o, role in PER_APP.values())
    return total

CONFIG_B = {"writer": "anthropic/claude-opus-5", "judge": "openai/gpt-5.6-terra",
            "research": "google/gemini-3.7-flash", "cheap": "google/gemini-3.7-flash",
            "other": "anthropic/claude-sonnet-5"}
print(f"Path A, Sonnet 5, 10 apps: ${price_a('anthropic/claude-sonnet-5'):.2f}")
print(f"Path B, config B, 10 apps: ${price_b(CONFIG_B):.2f}")
```

---

## Sources

Prices and plan facts were fetched on 19 September 2026 from:

- Anthropic: model table in the bundled `claude-api` reference (cached 24 June 2026); prompt-cache multipliers (1.25× / 2× write, 0.1× read, 0.025× on Fable 5.1) and the 50% batch discount from the same reference
- OpenAI: https://developers.openai.com/api/docs/pricing
- Google: https://ai.google.dev/gemini-api/docs/pricing
- Moonshot: https://platform.kimi.ai/docs/pricing/chat
- OpenRouter fees and routing: https://openrouter.ai/docs/faq, https://openrouter.ai/docs/features/provider-routing, https://openrouter.ai/docs/features/web-search
- Plans: https://www.morphllm.com/claude-code-pricing, https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers, https://geminicli.com/docs/resources/quota-and-pricing/, https://www.codeagentswarm.com/en/guides/kimi-code-plans-and-pricing
- Workspace evidence: `career-dashboard/data/career.db` tables `ai_calls` (18 rows, 18 Sep 2026, 2 usage-limit failures) and `agent_runs` (21 rows, 5 failures); file sizes under `data/context/`, `data/config/`, `backend/workflows/`; payload caps in `backend/ai/agents/graph.py`
