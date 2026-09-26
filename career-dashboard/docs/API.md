# API v2

> Source-app reference. US/one-page examples below are not Chetan's active policy. For current
> candidate and resume rules use `AGENTS.md`, `DATA_CONTRACT.md` and `data/config/profile.yml`.

Every profile is its own app: all paths below are under **`/p/<profile>/api/v2`**
(Chetan: `/p/chetan/api/v2`), served on loopback only. Cross-origin requests are
refused. Nothing here returns a key value. A request reaches a profile only
through its URL prefix; no endpoint takes a profile id in its body.

## Profiles (the shell, `backend/dashboard/shell.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | The launcher's identity check (`app`, `root`, `pid`, `busy` across every profile). |
| GET | `/api/profiles` | Every profile (`id`, `name`, `state` onboarding/ready, `locked`, `initials`, `market` {code, name, paper, timezone, default_location}, `schedule`) and `last_used`. |
| POST | `/api/profiles` | `{name}` → a new, empty profile folder (`profiles/<id>/`) in the `onboarding` state. |
| POST | `/api/profiles/{id}/reset` | `{confirm: "<exact name>"}` → stops its work and daily task, erases its folder, recreates it empty (`onboarding`). 400 for the locked backup profile or a wrong name. Irreversible. |
| DELETE | `/api/profiles/{id}` | `{confirm: "<exact name>"}` → the same, then removes the profile. Irreversible. |
| GET/PUT | `/api/profiles/{id}/schedule` | Its Windows daily-search task (`{enabled}` to switch it on or off). |
| GET | `/api/profiles/{id}/intake` | Intake progress, uploaded files and the draft to review (counts, coverage, questions). |
| POST | `/api/profiles/{id}/intake/files?name=…` | Upload one document: the raw file is the request body (.docx, .pdf, .txt, .md; 20 MB). |
| DELETE | `/api/profiles/{id}/intake/files/{name}` | Remove one. |
| POST | `/api/profiles/{id}/intake/start` / `stop` | Read the documents with the AI (per section: `profile_extractor`, then `intake_auditor`), merge, and run the coverage ledger. |
| PUT | `/api/profiles/{id}/intake/draft` | The review card's corrections: contacts, `country_pack`, `roles`, `authorization`. |
| POST | `/api/profiles/{id}/intake/build` | Write the profile's file set (`services/intake/build.py`), mark it ready, seed its database, compile its base resume, install its daily task. |
| GET | `/api/profiles/{id}/intake/chat` | The setup chat (`services/intake/interview.py`, the default page of an onboarding profile): `messages` [{id, role you/assistant, text, kind greeting/files/progress/found/question/answer/built/error, question?, files?}], the open `pending` question {id, key, text, why, field, options [{label, description, value}], multi, other, skippable, placeholder, defaults, resolves}, `thinking`, and `intake` (as above). Moving on after reading or a build happens here. |
| POST | `/api/profiles/{id}/intake/chat` | `{text}`: a typed answer to the open question (an option's name counts as clicking it), "read"/"stop"/"build", a correction for the interviewer, or, before reading, notes kept as `notes-from-chat.md` and read with the documents. |
| POST | `/api/profiles/{id}/intake/chat/answer` | `{question_id, choices: [option values], other, skip}`. Essentials are asked first (name if missing, country, roles, right to work, sponsorship, email, phone), then up to five questions from the `intake_interviewer` specialist (open questions from the documents, then preferences; its `updates` fill listed fields), then "Build my workspace". Answers go into the draft (`IntakeJob.update` / `record_answer`) and, at build, into `QUESTIONS-FOR-YOU.md` and `09-anything-else.md`. 400 for a stale question id. |
| POST | `/api/profiles/{id}/intake/chat/files?name=…` | Attach one document from the chat (raw body, as `/intake/files`); the chat offers to read. |

`GET /p/<id>/` serves the React client for that profile; `/` redirects to the last-used one.
Inside a profile, `POST /p/<id>/api/v2/documents/text?name=…` (raw body) reads one more
document so the Assistant can propose what is new; nothing changes without a yes.

## AI settings

| Method | Path | Purpose |
|---|---|---|
| GET | `/ai/settings` | Providers, whether each has a key, its live model list, tier choices, and the agent roster. `?refresh=true` refetches model lists. |
| PUT | `/ai/settings` | Save the writing and reading tier choices: `{"tiers": {"strong": {provider, model}, "cheap": {...}}}`. |
| POST | `/ai/settings/test` | Make the smallest real call with `{provider, model}`; returns `ok` and a plain-language `detail`. |
| PUT | `/ai/fallback` | Save the backup provider (`{provider, model}`) used when the main provider fails a call; `{provider: ""}` clears it. A real fallback is recorded as a `provider_fallback` activity row with the action that moved. |

The older `/ai/providers`, `/ai/preferences` and `/ai/test` remain for the Codex path. Kimi is a first-class hosted provider (`kimi`, Moonshot base URL); only the named `MOONSHOT_API_KEY=…` form is picked up, never a bare `sk-` token. Kimi has no web tool here, so web actions (discovery, research) are routed to a provider that can browse rather than failing.

Azure OpenAI (`azure_openai`) is a hosted provider for a deployment on Chetan's own Azure resource: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_DEPLOYMENT` (comma-separated names) in `career-dashboard/.env`. The deployment names are its models: the `.env` ones first, then every deployment the resource lists (`GET /openai/deployments?api-version=2022-12-01`, cached a day in `data/model-catalog.json` with the model each runs). It is reached through the resource's OpenAI-compatible `/openai/v1` Responses API with no temperature, which reasoning deployments such as `gpt-6-luna` require (verified live 23 Sep 2026). An 84-character Azure key pasted as `OPENAI_API_KEY` or on a bare line is filed as the Azure key, so it never makes OpenAI look ready. Azure searches the web: calls with the web on (discovery, research) go straight to `/openai/v1/responses` with the built-in `web_search` tool and a strict JSON schema (verified live 23 Sep 2026 with the real discovery and research schemas); calls without the web keep the LangChain path.

## Postings and employers

| Method | Path | Purpose |
|---|---|---|
| POST | `/jobs` | Save a posting. Order: sponsorship gate → duplicate check → never-re-apply check → save. Returns `{"excluded": true, "reason", "reason_label", "sentence", "record"}` when the posting refuses sponsorship or requires citizenship/clearance (nothing is saved; it goes to the excluded log); `{"blocked": true, "rule", "note"}` for `same_role`, `rejected_180` or `ghosted_90`; `{"job", "duplicate": true}` for a repeat; otherwise the saved job (with `sponsor_tier` and `sponsor_evidence`) plus `relevance` (score, eligibility, blockers). |
| GET | `/excluded` | The excluded log, newest first, each with the triggering sentence and source (`manual`, `discovery`, `portals`, `sweep`, `recheck`, `cli`). `?include_restored=true` includes restored rows. |
| POST | `/excluded/{id}/restore` | A wrong exclusion is correctable: saves the posting as a tier C job (or brings back a swept job) and marks the row restored. Later sweeps and re-checks will not exclude it again for the same sentence. 400 if already restored. |
| POST | `/jobs/{job_id}/sponsorship` | Re-run the gate on the saved description; a refusal moves the job to the excluded log. |
| POST | `/jobs/age` | Mark applications quiet for 21 days as `ghosted` (the scheduler also runs this hourly). |
| POST | `/jobs/verify-due` | Re-check open postings unchecked for 24 hours (expired ones weekly); live wording is re-gated. |
| POST | `/jobs/{job_id}/verify` | Re-check one posting now. |
| GET | `/jobs/{job_id}/verification` | Evidence history for the posting. |
| POST | `/companies/{job_id}/check` | Assess the employer: legitimacy, size, sponsorship evidence, red flags. |
| GET/PUT | `/discovery/preferences` | `default`, `balanced_five` or `portals`, persisted and read by the Daily Search screen. |
| GET | `/jobs/{job_id}/fit` | The verified requirement matrix (`services/fit.py`): each requirement with its category, verbatim excerpt, status (`met`, `partial`, `missing`, `unknown`) and evidence ids, the hard blockers, `score` with its `components`, `must_have_ok`, `method` (`ai` or `rules`), `provider_label`, `rationale`. Read from the `job_fit` cache when the posting, her evidence and `FIT_VERSION` are unchanged; otherwise checked by rules. Never calls an AI. |
| POST | `/jobs/{job_id}/fit` | Check again, by AI on a free plan when one is free (never a paid key), else by rules; saves the result and the job's `fit_score`/`fit_rationale`. |
| GET | `/rejected-leads` | Postings the gates turned away during discovery (`rejected_leads` table): company, title, url, stage (`reapply`, `relevance`, `legitimacy`, `save`), reason and when. |

Job rows include `sponsor_tier` (`S`, `A`, `B`, `C`), `sponsor_evidence` (label, reason, sentence, cap-exempt reason, H-1B approvals and years, E-Verify, restored flag), `posting_state`, `legitimacy_state`, `size_category` and `sponsorship_state`. `/summary` adds `excluded_jobs`, `counts.excluded` and `counts.ghosted`.

Every saved posting also carries `fit_score` (0–100 from the verified requirement matrix), `fit_rationale` ("Fit 82/100. Meets 6 of 7 must-haves (…). Missing: … Checked by AI (Kimi Code)…") and `raw_jd` (the description as first seen). Every discovery preset returns its postings best-fit-first: after the sponsorship, never-re-apply, relevance and legitimacy gates, the survivors are ranked by the rules matrix, the best `limit + DISCOVERY_SPARES` get the AI check on a free plan, and a job is saved only when it has no blocker, scores at least 65 and meets at least half its must-haves (`docs/AI-AGENTS.md`, *The requirement matrix*). The run result's `fit_check` says how many were checked and by which plan.

## Resume Studio

| Method | Path | Purpose |
|---|---|---|
| GET | `/studio/{job_id}/assessment` | ATS readiness, resume coverage, opportunity fit, and keywords split into `missing_supported` and `missing_unsupported`. |
| GET | `/studio/{job_id}/download?format=pdf\|tex` | Attachment download. PDF requires the current compiled revision. |
| POST | `/studio/{job_id}/chat/preview` | Preview a change set. Exact commands apply directly; plain English goes to the resume tailor agent. `applies_resume_change` and `ai_note` say what will happen and why. |
| POST | `/studio/{job_id}/chat/apply` | Apply a preview at its expected revision, compile, and rescore. |
| POST | `/studio/{job_id}/chat/undo` | Restore the source revision as a new version. |
| POST | `/studio/{job_id}/tailor` | The 60/40 tailor: the `job_tailor` specialist rewrites only the Projects and Skills fields — verified registry content copied unchanged plus company-aligned predicted items — then refits the page. 400 when no AI runtime is set up; a failure leaves the saved draft untouched (`studio_tailor_failed` event). |
| GET | `/studio/{job_id}/items` | The job's `resume_items` review rows, in order: `section` (`projects`/`skills`), `content` (decoded project object or skill name), `origin` (`verified`/`predicted`), `evidence_id`, `decision` (`pending`/`kept`/`removed`). |
| POST | `/studio/{job_id}/items/{item_id}/decision` | `{decision: "kept" \| "removed"}`. `removed` repairs the draft (the skill is stripped, or the project slot returns to the best unused registry project) and refits; `kept` changes only the review state. |
| POST | `/profile/chat/preview` | Preview profile changes; plain English goes to the profile curator agent. Always requires confirmation. |
| POST | `/profile/chat/apply` | Confirm and apply a profile preview. |

Chat mutations require `request_id` and `expected_revision`, plus
`change_set_id` for apply and undo. Reusing a request ID returns the existing
change set; a stale revision fails rather than silently rebasing.

## Profile

| Method | Path | Purpose |
|---|---|---|
| GET | `/profile` | Active entries plus `schema` (the add form per kind), `pending` (suggested changes waiting for review, each with a readable `label`), the registry, `profile.yml` and the source documents. Each entry keeps its stored row (`id`, `kind`, `title`, `summary`, `data`, `revision`, `review_state`) and adds the readable view from `backend/services/profile_fields.py`: `label`, `group`, `status`, `usage`, `sources`, `fields` (form values), `form` (field specs), `extra` (other registry details as labelled values), `in_sync`, `locked` (why it can't be removed, or null) and, for projects, `off_resumes` (why it is kept off resumes for now, or null). |
| POST | `/profile/items` | Add an entry. |
| PUT | `/profile/items/{id}` | Edit an entry at its `revision`; a stale revision fails. |
| DELETE | `/profile/items/{id}` | Remove an entry everywhere at once. Entries every resume prints (the registry's `immutable_across_resumes`, and the name, contacts, time zone, target roles and location preferences) are refused (400): edit them instead. |
| POST | `/profile/items/{id}/restore` | *Keep it*: undo a removal that was suggested (chat, assistant, CLI) and not yet confirmed. |
| POST | `/profile/reconcile` | Confirm suggested changes, all or `{"ids": [...]}`. |

A save with `fields` is a form save: the fields decide `title`, `summary` and `data` together, written into the evidence registry's own key names (`employer`, `dates`, `approved_facts`, `technologies`, `resume_content`, `degree_as_supplied`, …), and unknown fields or a category change are refused.

**A form save, a Profile-page removal and a confirmed suggestion apply everywhere at once** (`backend/services/profile_sync.py`, with the LaTeX side in `resume_sync.py`), in one database transaction:

- the knowledge row is saved `registered`, so agents read the wording and its details straight away;
- `data/config/profile.yml` (personal details, target roles, location preferences) and `data/context/evidence.yml` (every other kind) are rewritten, one block at a time: only that entry's lines change, and each file is re-read and compared with the intended result before anything is saved. A new entry gets a registry ID such as `EXP-USER-3F9A1C`, status `user_reported` and the source `Profile page (dashboard) > added <date>`; an edit adds `… > edited <date>`; a removal keeps the entry on `hold` with `profile_removed: <date>`, so a fresh `career.db` imports it as removed;
- a linked twin follows: `profile.yml`'s `full_name`, `email`, `phone`, `github`, `portfolio_url`, `linkedin` and the registry's `IDENTITY-001` / `CONTACT-*` claims are one fact;
- `data/templates/resume-base.tex` and the drafts of jobs still `saved` or `prepared` follow: role and degree headings, experience bullets (reworded, removed, or added next to their neighbour), skill and coursework lists, the two project slots while they print the registry unchanged, and the header. Wording customised in Resume Studio is left alone, a new role or degree is placed in date order, and drafts of jobs already applied to are never touched. A changed draft gets a new Studio version;
- `candidate_revision` moves to `<today>.<n>` in both YAML files, which marks older PDFs for a rebuild, and the cached resume contract is rebuilt.

If any write fails, every file is put back and the database transaction rolls back. The response carries `synced`: `updated` (the places written), `drafts` (`job_id`, `company`, `title`), `notes` and `revision`. Refused with a plain message: a skill on the never-claim list, and an edit that would leave a registered project without a technology line or with other than two or three bullets. A new project that isn't complete yet is saved to the profile and kept off resumes (`off_resumes` says why).

A save without `fields` (the Profile chat, the assistant, the CLI) behaves as before: `title`, `summary` and `data` as given, marked `user_updated`, and no file changes until it is confirmed. The page reads each entry's body (bullets, skills, details) from `summary`, so both paths show the same thing. A confirmed suggested skill ("name | where it was used") is recorded with the name as the skill and the rest as `evidence_note`.

## Assurance

| Method | Path | Purpose |
|---|---|---|
| GET | `/assurance/{job_id}` | The pre-apply review report for the job's current draft. Per visible claim: `text`, `section`, `origin`, `evidence_status` (`verified`/`predicted`/`missing`), `confidence` (100/60/0), the linked `items` (review row ids), `evidence_ids`, source `line` and `note`. A tailored item the page does not print (a third project the fit left out) is listed too, with its own row id in `items`, `line: null` and a note saying so; every claim has an `items` list. The `summary` counts claims by status and review rows by decision (`kept`, `removed`; `pending` counts only suggestions, origin `predicted`, still waiting for her keep or remove: registry items never need a decision); `score` is the cached studio score. A job with no draft returns an empty report with a `note` instead of an error. The same per-claim scan powers `validate_resume.py --report`. |

The validator's evidence gate is soft exactly where predicted content lives: an
unresolvable or missing EVIDENCE tag in Projects or Technical Skills is a
warning, while Experience, Education and the header stay hard failures, as do
the one-page rule and the banned-claims list.

## Assistant chat

| Method | Path | Purpose |
|---|---|---|
| GET | `/assistant` | The open conversation (`conversation_id`, its `messages` newest last, and `conversations`, the list above), the question it is waiting on (`pending`), whether a message is still `busy`, `ai_configured`, the `engine` the agent runs on (`provider`, `model`, `label`, `ready`, `moved_from`, `runs` — what the runs it starts go through — a `note` when Codex is installed but not signed in, and the `options` ready on this Mac), the agent registry with what the chat reaches (`agents`: `id`, `name`, `linked`, `tools`) and the tool groups (`capabilities`). |
| POST | `/assistant/messages` | Send `{message, request_id}` (up to 120,000 characters, so a whole posting fits). Returns 202 with the row in state `processing`; the reply, its `steps` and `data` fill in on the worker thread. The same `request_id` returns the same row. |
| GET | `/assistant/messages/{id}` | One message: `state` (`processing`, `done`, `needs_input`, `failed`), `response`, `steps` (`label`, `state`, `detail`, the `agent` it ran on, a `run_id` when it queued a run) and `data`. |
| POST | `/assistant/messages/{id}/stop` | Stop a reply in progress. The worker ends it at its next step boundary: a compile or one model call that is mid-flight finishes first, but a model decision that comes back after the stop is dropped (no tool runs, no reply shows). The running step reads *Stopping…* until then, and the message ends `failed` with `data.intent: stopped` and a reply saying that anything a finished step saved stays. The question the chat was waiting on is dropped. |
| GET | `/assistant/conversations` | Every conversation with messages, newest activity first: `id`, `title` (the first line of its first message), `count`, `started_at`, `updated_at`, `busy`, `current`. |
| POST | `/assistant/conversations` | New chat: opens a fresh conversation and returns the overview. An open conversation with nothing in it is reused. Refused (400) while a reply is being worked on. |
| PUT | `/assistant/conversations/{id}` | Open an earlier conversation (returns the overview). The question the current one was waiting on is dropped. Refused while a reply is being worked on. |
| DELETE | `/assistant/conversations` | Clear the chat history: every conversation and its messages, for good; a fresh conversation opens. Refused (400) while a reply is being worked on. Returns the overview. |
| DELETE | `/assistant/conversations/{id}` | Delete a conversation and its messages for good; the jobs, resumes and profile changes it produced stay. Deleting the open one opens the most recent other conversation, or a fresh one. Returns the overview. |
| PUT | `/assistant/auto-apply` | `{enabled: bool}`, per conversation. When on, confirm-gated tools run without the yes/no pause; each auto-applied step is recorded with its diff (`assistant_auto_applied` event). |

Two paths. A pasted posting (or a lone link, fetched first) is handled without a model: fields from labelled lines or the cheap `posting_parser` (values not in the text are dropped), then `save_posting` (sponsorship gate, duplicates, never-re-apply), `studio.open`, `studio.fit` and the score. A `resume_ready` reply carries `job_id`, `pdf` and `preview_png` (paths under `/api/files/`), `posting_url`, `tier`, `coverage`, `ats`, `gaps` and `suggestions`. The shortcuts `find jobs`, `status`, `excluded`, `study plan for X`, `research X`, `open X` and `applied to X` (recorded only after the next message is `yes`) are also deterministic.

Everything else runs the agent loop (`services/assistant.py`): the `workspace_agent` specialist receives the workspace snapshot, the tool catalogue, the last five exchanges of the open conversation and the task transcript and returns one decision per turn — `call` a tool, `ask` her something, or `reply` — up to 14 turns per message. Tools (`services/assistant_tools.py`) wrap every feature: jobs (`list_jobs`, `get_job`, `save_posting`, `fetch_posting`, `update_job`, `remove_job`, `restore_job`, `excluded_postings`, `restore_excluded`, `recheck_sponsorship`, `verify_posting`, `find_jobs`), resume (`build_resume`, `resume_status`, `resume_assurance` (the Assurance report: claims by evidence status and her keep/remove decisions), `edit_resume`, `undo_resume_change`, `sync_resume_projects`, `cover_letter`, `application_documents`), the Daily Search pipeline (`run_search_pipeline` with optional `count`, `provider`, `model`, `steps` and `source`, anything left out taken from the page's saved choices and the AI from Settings; a named AI is for that one run; `search_pipeline_status`, which also lists the AIs ready to run it; `stop_search_pipeline`) and the last search's report (`search_report`: summary, saved jobs, the leads turned away with reasons, the gate's exclusions), agents (`run_agent` for research, resume_build, resume_match and study_plan; `job_fit` for a job's verified requirement check; `wait_for_run`, `agent_runs`, `run_result`), profile (`profile_overview`, `search_profile`, `get_profile_item`, `propose_profile_change`, `apply_profile_change`, `reconcile_profile`, `open_questions`, `add_question`), search and mail (`status`, `get_goals`, `set_goals`, `list_mail`, `resolve_mail`) and settings (`ai_settings`, `set_discovery_preset`, `read_policy`, `recent_activity`). The Gmail sync is not a tool. Tools marked *needs her yes* (`update_job`, `remove_job`, `restore_excluded`, `apply_profile_change`, `reconcile_profile`, `set_goals`, `resolve_mail`) pause the loop with `state: needs_input` and `pending.kind: confirm_tool`; the next `yes` runs the tool and the task continues, `no` leaves it undone. A question from the agent sets `pending.kind: agent`; the answer continues the same task. A tool result that carries a document fills `data` the same way as `resume_ready`; when one reply read the documents of several jobs (a comparison), `data.cards` holds one row per job (`job_id`, `company`, `title`, `tier`, `revision`, `pdf`, `coverage`, `ats`, `posting_url`) instead of a single card.

To stay quick, the snapshot gives each job `resume_pdf`, `research_done`, `study_plan_done` and `fit_score`, plus `daily_search` (the running or last pipeline run with each job's steps), so questions about those need no tool; and a `call` decision may list up to five more read-only tools in `more_calls`, which run in the same turn as their own steps (a tool that changes anything listed there is refused and reported back to the model).

The engine is the strong tier from `ai_preferences.tiers`; a tier nobody chose follows the Settings main choice (`ai_preferences.default`, else the gateway's built-in: OpenAI with a key, otherwise Codex), so the chat and the background runs share one engine by default. When the chosen provider cannot run on this Mac, the loop moves to the first runtime that is ready (the main choice, then Claude Code, Codex, then any keyed provider) and `engine.moved_from` names what it moved away from. A run the chat starts (`find_jobs`, `run_agent`, the `find jobs` and research/study-plan shortcuts) is enqueued on the chat's engine when that runtime can do the work and Settings gave the action no provider of its own; otherwise the gateway routes it as usual. `PUT /ai/main` changes everything at once (the rail's *Runs on* control calls it). When a model call fails (quota, outage, output that does not fit the schema) and Settings names a *Backup provider* that is ready here, the call is retried once there, as the gateway does for background runs; the reply shows a *Switched to the backup AI* step and a `provider_fallback` event is recorded with `ai_action: assistant_chat`. With no backup, the failure says to choose another AI or set a backup.

Every other tab has an *Ask the assistant* menu (Dashboard, Daily Search, Resume Studio, Assurance, Profile and the job details window) with questions about what is on screen. A question that only reads is sent at once; one that would start work or change something opens the chat with the text ready to send.

Confirm pauses carry `data.diff` (each field's before and after) and every finished reply carries `data.trace` (the plan, tool calls and results in order), so what the agent changed is reviewable line by line.

The page polls `GET /assistant` every 1.2 s while a message is processing, every 8 s when idle and every 30 s in a hidden tab, with a 20 s request timeout; a poll that lands after a newer one is dropped, and a send is echoed at once and followed by an immediate poll.

## Agent runs

`POST /agents/run` takes `kind` (`research`, `email`, `discovery`, `resume_build`, `resume_match`, `study_plan`), optional `job_id`, `provider`, `model` and a discovery `preset` (`default`, `balanced_five`, `portals`). A discovery result lists `added`, `duplicates` and `excluded` (company, title, URL, reason and sentence). A balanced-five run adds `balanced_shortages`. The `portals` preset reads tracked Greenhouse/Lever/Ashby feeds and makes no AI call. `study_plan` writes `study-plan.md` into the job's application folder. A discovery run also takes `count` (1–15, default 5): how many jobs to save, still capped by what is left in today's plan; the balanced mix scales its 2/1/2 split to the count.

## Daily Search pipeline

`backend/services/pipeline.py`. Finds jobs, then runs only the helpers Chetan switched on for each new job, in order: company research → tailor → study plan → one-page PDF. Each step uses the same runner and Resume Studio paths as the rest of the app, so every gate, the daily AI-call budget and the Assurance review still apply. One run at a time; nothing is ever submitted.

| Method | Path | Purpose |
|---|---|---|
| GET | `/pipeline` | Everything the page needs: `sources`, `find` and `steps` (first-run minutes, tokens and budget calls), `providers` (the three local apps always, with `ready` and a plain `note`; keyed APIs when ready), `speeds` (per `provider:model` and step: a factor, learned from finished runs when there are any), saved `preferences`, `tools.pdf` (Tectonic present), plus the status below. The status refreshes compiler availability without restarting the app. |
| GET | `/pipeline/status` | `budget` (daily AI calls), `plan.remaining_today`, `current` (the active run or null) and `last` (the latest finished run). Polled every 3 s while a run is active. |
| PUT | `/pipeline/preferences` | Save `{count, source, steps}`; also sets `/discovery/preferences`. The AI is never remembered here: `preferences` always carries the AI chosen in Settings (Auto by default). |
| POST | `/pipeline/run` | Start a run (202) with `{count, source, steps}`; `provider` and `model` are optional and, when left out (the page never sends them), the Settings choice does the work. `include_unprepared: true` (the 7 AM run) also prepares saved jobs an earlier run left without a resume, up to the count. Refused while another run or a discovery is active, when today's plan is complete, or when the chosen AI is not ready. |
| POST | `/pipeline/{id}/stop` | Stop after the current step; the remaining steps are marked `skipped`. |

A run's `progress` holds `stage`, `jobs_target`, `find` and, per job, a state for each switched-on step (`waiting`, `running`, `done`, `failed`, `skipped`) with `seconds` and a `note` or `error`. A failed step does not stop the next one. Research is saved to the job's `company-research.md`, which the tailor reads. Steps that need the web (finding with AI, research, the study plan's action) run on the chosen AI when it can browse, otherwise on the first ready one that can. Estimates are first-run numbers until a run finishes; each finished run's step times become per-AI speed factors (median of the last 20 runs). Token figures are estimates: the local apps do not report exact usage.

Model choices per AI: Claude Code offers Sonnet, Opus and Haiku. Codex offers `codex-runtime` ("Default": no `-m`, Codex picks) plus the first three models her ChatGPT plan lists in `~/.codex/models_cache.json` (passed as `codex exec -m <slug>`). Kimi Code offers `kimi-runtime` ("Default": the CLI's `default_model`) plus up to three more from `~/.kimi-code/config.toml` (passed as `kimi --model <alias>`). Azure offers its deployments. When only Kimi Code for VS Code is installed, the Kimi card's `note` gives the terminal-app install command (`irm https://code.kimi.com/kimi-code/install.ps1 | iex`), which shares the extension's sign-in.

`POST /studio/{job_id}/fill` fits the draft to exactly one US Letter page (11 → 10.5 → 10pt, then the documented cuts) and returns the new version with its measured layout.
