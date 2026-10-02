"""The Daily Search pipeline: find jobs, then take each new job end to end.

The Daily Search page lets the candidate choose how many jobs to find, where to look,
which AI does the work and which helpers run. Finding keeps going while it is short: after
the chosen search, focused web-search passes (services/search_plan.py) look again, skipping
what was already turned away, up to ``MAX_FIND_PASSES``. Then every job goes through the
same chain, one step after the other: the posting is re-read (a closed one gets nothing
more), company research with the hiring-manager view and the fit with the profile, the
tailored resume, the contract-sized PDF, an independent review of that PDF, the study plan
and the ready-to-submit check (services/readiness.py). This module checks that choice, runs it
on one background thread and reports progress step by step. Every step goes
through the same AgentRunner and Resume Studio paths as the rest of the app, so
the sponsorship gate, duplicate checks, the daily paid-call limit and the
Assurance review all still apply. Nothing is ever submitted. The default AI is
Auto (backend/ai/router.py): signed-in CLIs first, configured hosted APIs afterward.

Time and token figures are estimates. ``FIND`` and ``STEPS`` hold first-run
numbers for a local app (Claude Code · sonnet); every finished run records how
long its steps really took, and ``speeds`` turns that into a per-AI factor so
the next estimate is closer. The page does the arithmetic (``estimatePipeline``
in the frontend) so switching a helper on or off updates it at once.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
import uuid
from pathlib import Path

from backend.services.agents import DEFAULT_DISCOVERY_JOBS, MAX_DISCOVERY_JOBS
from backend.pdf_compiler import tectonic_executable

SOURCES = [
    {"id": "default", "label": "Web search", "ai": True,
     "what": "The AI searches job sites for fresh postings that fit you, best match first."},
    {"id": "balanced_five", "label": "Balanced mix", "ai": True,
     "what": "The AI looks for a mix of startups, mid-size and large companies under each market's eligibility rules."},
    {"id": "portals", "label": "My company list", "ai": False,
     "what": "Checks only the career pages of companies on your list. The search itself uses no AI; only the best "
             "matches get their must-haves checked, on a free plan."},
    {"id": "feeds", "label": "Job boards + employer feeds", "ai": False,
     "what": "Reads your company list, a verified list of employers hiring in your market and, for Ireland, "
             "gradireland, jobs.ie and the askmanavi graduate tracker, with the full posting text. The search itself "
             "uses no AI; the best matches get their requirements checked on a free plan."},
]
SOURCE_IDS = [source["id"] for source in SOURCES]


def sources_for(root) -> list[dict]:
    """SOURCES in this profile's market and sponsorship terms."""
    from backend.countries import pack_for, target_markets_for

    markets = target_markets_for(root)
    pack = pack_for(root)
    out = [dict(source) for source in SOURCES]
    for source in out:
        if source["id"] == "default":
            adjective = "Irish and US" if len(markets) > 1 else pack.adjective
            source["what"] = source["what"].replace("fresh postings", f"fresh {adjective} postings")
    return out

# Finding jobs happens once per run; its cost grows a little with each job asked for.
FIND = {
    "find_ai": {"minutes": 5.0, "minutes_per_job": 0.5, "tokens": 40000, "tokens_per_job": 6000, "budget_calls": 1},
    "find_pages": {"minutes": 1.0, "minutes_per_job": 0.1, "tokens": 0, "tokens_per_job": 0, "budget_calls": 0},
    "find_feeds": {"minutes": 8.0, "minutes_per_job": 0.5, "tokens": 0, "tokens_per_job": 0, "budget_calls": 0},
}

# The helpers that run once per job, in the order they run. ``budget_calls`` are
# the background AI calls a step makes; they count against the daily paid-call
# limit only when a paid AI serves them (the tailor's specialists are metered
# by tokens).
STEPS = [
    {"id": "posting", "label": "Posting still open", "ai": False, "web": True,
     "minutes": 0.3, "tokens": 0, "budget_calls": 0,
     "what": "Re-reads the posting's own page before any work starts. A closed posting gets nothing more. No AI."},
    {"id": "research", "label": "Research & hiring-manager fit", "ai": True, "web": True,
     "minutes": 4.0, "tokens": 30000, "budget_calls": 3,
     "what": "Three agents: public company research, a hiring manager's view of the role (it never sees you), "
             "then your evidence against both. The resume tailor uses it."},
    {"id": "tailor", "label": "Tailor resume", "ai": True, "web": False,
     "minutes": 3.0, "tokens": 22000, "budget_calls": 0,
     "what": "Selects your evidenced Projects and Skills for this job. Learning gaps stay outside the resume."},
    {"id": "pdf", "label": "Resume PDF", "ai": False, "web": False,
     "minutes": 0.5, "tokens": 0, "budget_calls": 0,
     "what": "Builds the PDF and checks it against the profile's page contract. No AI."},
    {"id": "review", "label": "Independent review", "ai": True, "web": False,
     "minutes": 1.5, "tokens": 12000, "budget_calls": 1,
     "what": "A fresh AI reads only the finished PDF and the posting, never your profile, and says what is met, "
             "partly met or missing."},
    {"id": "study_plan", "label": "Study plan", "ai": True, "web": False,
     "minutes": 1.5, "tokens": 8000, "budget_calls": 1,
     "what": "Lists what to learn before the interview. It never goes on your resume."},
    {"id": "ready", "label": "Ready-to-submit check", "ai": False, "web": False,
     "minutes": 0.2, "tokens": 0, "budget_calls": 0,
     "what": "Checks every gate again (open posting, work permit, fit, page contract, the review, your decisions) "
             "and gives a readiness score. It is not a prediction of an interview. No AI."},
]
STEP_IDS = [step["id"] for step in STEPS]
STEP = {step["id"]: step for step in STEPS}
# Every helper by default: "find 5 jobs" means 5 jobs taken end to end. The morning run uses the same.
DEFAULT_STEPS = {step: True for step in STEP_IDS}
# One Daily Search makes at most this many search passes: the chosen one, then focused web
# passes while it is still short of the jobs asked for.
MAX_FIND_PASSES = 3


class JobStopped(Exception):
    """A helper found that the job cannot go further (its posting closed); its later helpers are skipped."""


def step_entry(done: dict, seconds: float) -> dict:
    """A helper's return as its progress entry: done, or skipped when it had nothing to work on."""
    extra = {k: v for k, v in done.items() if k != "skipped"}
    return {"state": "skipped" if done.get("skipped") else "done", "seconds": seconds, **extra}


def open_draft(studio, job_id: str, step: str, opened: bool) -> bool:
    """Open the application folder and Studio draft before the first helper that writes into them; no AI.

    The posting check comes first, so a closed posting never gets a folder.
    """
    if opened or step == "posting":
        return opened
    studio.open(job_id)
    return True


def steps_for(root) -> list[dict]:
    """The helper catalogue with the current profile's exact resume shape."""
    from backend.countries import pack_for, target_markets_for
    from backend.resume_contract import contract_for

    pack = pack_for(root)
    contract = contract_for(Path(root))
    shape = f"{contract.describe_pages()} {pack.paper_label}" if len(target_markets_for(root)) == 1 else "the job market's page contract"
    out = [dict(step) for step in STEPS]
    for step in out:
        if step["id"] == "pdf":
            step["label"] = f"{shape[:1].upper()}{shape[1:]} PDF" if len(target_markets_for(root)) == 1 else "Market-specific PDF"
            step["what"] = f"Builds the PDF and checks it against {shape}. No AI."
    return out

# Signed-in local apps: their calls count against the person's plan's
# usage limit, never an API bill.
LOCAL_APPS = ("claude_code", "codex", "kimi_cli")
CLAUDE_MODELS = [
    ("sonnet", "Sonnet", "Balanced. Recommended for most days."),
    ("opus", "Opus", "Best writing. Slowest, and uses the most of your limit."),
    ("haiku", "Haiku", "Fastest and lightest on your limit. Simpler writing."),
]
# Codex and Kimi list what the person's plan offers; past the app's default and three
# more, extra choices stop helping the person decide.
MAX_LISTED_MODELS = 3
# How long an AI step takes compared with Claude Code · sonnet, before any run is measured.
MODEL_SPEED = {("claude_code", "sonnet"): 1.0, ("claude_code", "opus"): 1.5, ("claude_code", "haiku"): 0.6,
               ("codex", None): 1.2, ("kimi_cli", None): 1.3}
# A model whose name says it is the light one is guessed faster until a run is measured.
FAST_NAMES = ("luna", "highspeed", "mini", "flash", "lite")
API_SPEED = 0.6  # a hosted API answers in one call, without an agent loop
ACTIVE = ("queued", "running")


def default_speed(provider: str, model: str) -> float:
    if (provider, model) in MODEL_SPEED:
        return MODEL_SPEED[(provider, model)]
    # Auto usually lands on a local app (Kimi first), so it is guessed like one until measured.
    base = MODEL_SPEED.get((provider, None), 1.0 if provider in LOCAL_APPS or provider == "auto" else API_SPEED)
    return round(base * 0.7, 2) if any(word in (model or "").casefold() for word in FAST_NAMES) else base


def local_models(provider_id: str) -> list:
    """The model choices for one local app: Claude's three, or the app's default plus what the plan lists."""
    from backend.ai import codex, kimi_cli

    if provider_id == "claude_code":
        return [{"id": m, "label": name, "hint": hint} for m, name, hint in CLAUDE_MODELS]
    if provider_id == codex.ID:
        listed = [m for m in codex.listed_models() if m["id"] not in codex.MODELS][:MAX_LISTED_MODELS]
        return [{"id": codex.MODELS[0], "label": "Default",
                 "hint": "Codex picks its usual model. A safe choice if you are not sure."}] + listed
    listed = kimi_cli.listed_models()
    default = next((m["label"] for m in listed if m["default"]), None)
    others = [m for m in listed if not m["default"]][:MAX_LISTED_MODELS]
    return [{"id": kimi_cli.MODELS[0], "label": "Default",
             "hint": "Kimi Code's usual model" + (f" ({default})." if default else ".")}] + [
        {"id": m["id"], "label": m["label"],
         "hint": "Faster replies." if "highspeed" in m["id"].casefold() else ""} for m in others]


def find_key(source: str) -> str:
    if source == "feeds":
        return "find_feeds"
    return "find_ai" if next(s for s in SOURCES if s["id"] == source)["ai"] else "find_pages"


def find_seconds(key: str, jobs: int) -> float:
    return (FIND[key]["minutes"] + FIND[key]["minutes_per_job"] * jobs) * 60


def hunt_active(workspace) -> bool:
    """True while an overnight hunt (services/hunt.py) is queued, running or waiting for a plan to reset."""
    try:
        with workspace.connect() as db:
            table = db.execute("SELECT 1 FROM sqlite_master WHERE name='hunt_runs'").fetchone()
            return bool(table and db.execute(
                "SELECT 1 FROM hunt_runs WHERE state IN ('queued','running','waiting') LIMIT 1").fetchone())
    except Exception:
        return False


def busy(workspace) -> bool:
    """True while a pipeline, a hunt or any agent run is queued or running (the launcher will not restart then)."""
    try:
        with workspace.connect() as db:
            if db.execute("SELECT 1 FROM agent_runs WHERE state IN ('queued','running') LIMIT 1").fetchone():
                return True
            table = db.execute("SELECT 1 FROM sqlite_master WHERE name='pipeline_runs'").fetchone()
            if table and db.execute("SELECT 1 FROM pipeline_runs WHERE state IN ('queued','running') LIMIT 1").fetchone():
                return True
    except Exception:
        return False
    return hunt_active(workspace)


class Stopped(Exception):
    pass


class Pipeline:
    def __init__(self, services, runner, studio, poll_seconds: float = 2.0):
        self.s, self.w, self.runner, self.studio = services, services.w, runner, studio
        self.poll = poll_seconds
        self.lock = threading.Lock()
        self.thread = None
        with self.w.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_runs(
                id TEXT PRIMARY KEY, state TEXT NOT NULL, config TEXT NOT NULL,
                progress TEXT NOT NULL DEFAULT '{}', error TEXT,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT);
            CREATE INDEX IF NOT EXISTS idx_pipeline_runs_created ON pipeline_runs(created_at);
            """)

    # ----- what the page shows -------------------------------------------------

    def providers(self) -> list:
        """Every AI the page can offer: the local apps always (ready or not), hosted APIs only when keyed."""
        from backend.ai import catalog, codex, kimi_cli, provider_label, ready_providers

        ready = ready_providers(self.w.root)
        signed_in = {codex.ID: codex.signed_in, kimi_cli.ID: kimi_cli.signed_in}
        listed = [self._auto_entry(ready)]
        for provider_id in LOCAL_APPS:
            label = provider_label(provider_id)
            installed = bool(ready.get(provider_id))
            note = None
            if provider_id == kimi_cli.ID and not installed and kimi_cli.extension_only():
                note = ("Kimi Code for VS Code is installed, but this app needs the Kimi Code terminal app. "
                        f"In PowerShell run: {kimi_cli.INSTALL_COMMAND} (it uses the same sign-in), "
                        "then reload this page.")
            elif not installed:
                note = f"{label} is not installed on this PC. Install it and sign in, then reload this page."
            elif provider_id in signed_in and not signed_in[provider_id]():
                note = f"{label} may not be signed in. If a step fails, open {label}, sign in and try again."
            listed.append({
                "id": provider_id, "label": label, "kind": "local", "ready": installed, "web": True,
                "cost": "Uses your plan's usage limit. No extra bill.", "note": note,
                "models": local_models(provider_id),
            })
        for provider_id in catalog.PROVIDERS:
            engine = self.runner.gateway.providers.get(provider_id)
            if not ready.get(provider_id) or engine is None:
                continue
            azure = provider_id == catalog.AZURE
            listed.append({
                "id": provider_id, "label": provider_label(provider_id), "kind": "api", "ready": True,
                "web": "web" in engine.capabilities,
                "cost": "Pay per use on your Azure account." if azure else "Pay per use on your API key.",
                "note": None if "web" in engine.capabilities else
                "Cannot search the web, so finding jobs and company research use another AI that can.",
                "models": self._azure_models() if azure else
                [{"id": m, "label": m, "hint": ""} for m in list(engine.models)[:8]],
            })
        return listed

    def _auto_entry(self, ready: dict) -> dict:
        """Auto on the page: the route in order, which plans rest and until when."""
        from backend.ai import paid_gate, router

        policy = router.policy_from(self.s.pref("ai_preferences", {}) or {})
        blocked = paid_gate(self.s)("azure_openai")
        route = router.status(self.w.root, policy, ready_map=ready, paid={"block": blocked})
        usable = [row for row in route if row["enabled"] and row["ready"]]
        resting = [f"{row['label']} rests until {row['resting']['until_text']}" for row in usable if row["resting"]]
        steps = " → ".join(row["label"] + (" (paid)" if row["paid"] else "") for row in usable) or "nothing set up yet"
        return {
            "id": router.ID, "label": router.LABEL, "kind": "auto", "ready": bool(ready.get(router.ID)), "web": True,
            "cost": "Uses your Kimi, Codex and Claude plans first. Azure (paid) only when all three are resting.",
            "note": ("; ".join(resting) + ". The next plan takes over." if resting else None),
            "models": [{"id": router.ID, "label": "Auto", "hint": steps}],
            "route": route,
        }

    def _azure_models(self) -> list:
        """Her Azure deployments, each with what it runs (and Codex's description of that model when known)."""
        from backend.ai import catalog, codex

        details = catalog.azure_details(self.w.root)
        described = {m["id"]: m["hint"] for m in codex.listed_models()}
        models = []
        for name in catalog.azure_deployments(self.w.root)[:8]:
            runs = details.get(name) or name
            hint = described.get(runs) or f"Runs {runs} on your Azure resource."
            models.append({"id": name, "label": name, "hint": hint})
        return models

    def _refresh_model_lists(self) -> None:
        """Once a day, ask Azure which deployments exist; the list is cached and a failure keeps the old one."""
        from backend.ai import catalog

        try:
            catalog.models(self.w.root, catalog.AZURE)
        except Exception:
            pass

    def speeds(self, providers: list) -> dict:
        """Per AI and model, how long each step takes compared with the first-run estimate."""
        learned = self._learned()
        tailor_tokens = self._tailor_tokens()
        speeds = {}
        for provider in providers:
            for model in provider["models"]:
                key = provider["id"] + ":" + model["id"]
                base = default_speed(provider["id"], model["id"])
                speeds[key] = {}
                for step in (*FIND, *STEP_IDS):
                    ratios = learned.get((key, step))
                    ai = step == "find_ai" or (step in STEP and STEP[step]["ai"])
                    speeds[key][step] = (
                        {"factor": round(statistics.median(ratios), 2), "learned": True, "runs": len(ratios)}
                        if ratios else {"factor": base if ai else 1.0, "learned": False, "runs": 0}
                    )
                if key in tailor_tokens:
                    speeds[key]["tailor"]["tokens"] = tailor_tokens[key]
        return speeds

    def _tailor_tokens(self) -> dict:
        """Measured tokens per tailored resume, per provider:model (the tailor's specialist reports usage)."""
        with self.w.connect() as db:
            rows = db.execute(
                "SELECT provider, model, input_tokens + COALESCE(output_tokens, 0) FROM ai_calls "
                "WHERE cache_key='usage' AND action='specialist:job_tailor' AND input_tokens IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 200").fetchall()
        seen = {}
        for provider, model, tokens in rows:
            seen.setdefault(f"{provider}:{model}", []).append(tokens)
        return {key: int(statistics.median(values[:10])) for key, values in seen.items()}

    def _learned(self) -> dict:
        """Measured seconds ÷ first-run estimate, per (provider:model, step), from recent finished runs."""
        ratios = {}
        with self.w.connect() as db:
            rows = db.execute(
                "SELECT config, progress FROM pipeline_runs WHERE state IN ('completed','stopped') "
                "ORDER BY created_at DESC LIMIT 20").fetchall()
        for row in rows:
            try:
                config, progress = json.loads(row["config"]), json.loads(row["progress"])
            except (TypeError, ValueError):
                continue
            key = config.get("provider", "") + ":" + config.get("model", "")

            def add(step, seconds, expected):
                if seconds and expected:
                    ratios.setdefault((key, step), []).append(min(5.0, max(0.1, seconds / expected)))

            found = progress.get("find") or {}
            if found.get("state") == "done" and config.get("source") in SOURCE_IDS:
                step = find_key(config["source"])
                # The estimate is for one pass; later passes of a short search would inflate it.
                add(step, found.get("first_pass_seconds") or found.get("seconds"),
                    find_seconds(step, progress.get("jobs_target") or config.get("count", 5)))
            for job in progress.get("jobs") or []:
                for step, state in (job.get("steps") or {}).items():
                    if step in STEP and state.get("state") == "done" and not state.get("quick"):
                        add(step, state.get("seconds"), STEP[step]["minutes"] * 60)
        return ratios

    def preferences(self, providers: list | None = None) -> dict:
        """The saved search (count, where to look, helpers) with the AI chosen in Settings.

        The AI is not a Daily Search setting: Settings is the one place it is chosen, and the
        search follows it (Auto by default). The chat can still name an AI for one run.
        """
        from backend.ai import main_choice, ready_providers

        providers = providers if providers is not None else self.providers()
        stored = self.s.pref("pipeline_preferences", {}) or {}
        by_id = {p["id"]: p for p in providers}
        preferences = self.s.pref("ai_preferences", {}) or {}
        provider, stored_model = main_choice(preferences, ready_providers(self.w.root))
        if provider not in by_id or not by_id[provider]["ready"]:
            first = next((p for p in providers if p["ready"]), providers[0] if providers else None)
            provider, stored_model = (first["id"], first["models"][0]["id"]) if first else ("", "")
        models = [m["id"] for m in by_id.get(provider, {}).get("models", [])]
        model = stored_model if stored_model in models else (models[0] if models else "")
        source = stored.get("source") or (self.s.pref("discovery_preferences", {}) or {}).get("preset") or "default"
        return {
            "count": stored.get("count") or DEFAULT_DISCOVERY_JOBS,
            "source": source if source in SOURCE_IDS else "default",
            "provider": provider, "model": model,
            "steps": {step: bool((stored.get("steps") or {}).get(step, DEFAULT_STEPS[step])) for step in STEP_IDS},
        }

    def overview(self) -> dict:
        self._refresh_model_lists()
        providers = self.providers()
        return {
            "sources": sources_for(self.w.root), "find": FIND, "steps": steps_for(self.w.root), "max_jobs": MAX_DISCOVERY_JOBS,
            "providers": providers, "speeds": self.speeds(providers),
            # The PDF step (and the tailor's own page fit) need the Tectonic compiler.
            "preferences": self.preferences(providers),
            **self.status(),
        }

    def status(self) -> dict:
        budget = self.runner.cache.stats()
        return {
            "tools": {"pdf": bool(tectonic_executable())},
            # The daily limit counts paid calls only; free plan calls are shown beside it.
            "budget": {"limit": budget["daily_call_limit"], "used": budget["paid_calls_today"],
                       "remaining": budget["remaining_calls"], "paid_only": True,
                       "free_used": budget["free_calls_today"]},
            "plan": {"remaining_today": self.s.goals()["remaining_today"]},
            "current": self._latest(active=True),
            "last": self._latest(active=False),
        }

    # ----- choosing and starting ---------------------------------------------

    def validate(self, values: dict, require_ready: bool = True) -> dict:
        count = values.get("count", DEFAULT_DISCOVERY_JOBS)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_DISCOVERY_JOBS:
            raise ValueError(f"Choose between 1 and {MAX_DISCOVERY_JOBS} jobs.")
        source = values.get("source") or "default"
        if source not in SOURCE_IDS:
            raise ValueError("Choose where to look: web search, balanced mix, your company list or job boards + employer feeds.")
        providers = {p["id"]: p for p in self.providers()}
        if not values.get("provider"):
            # No AI named: the one chosen in Settings does the work.
            chosen = self.preferences(list(providers.values()))
            values = {**values, "provider": chosen["provider"], "model": values.get("model") or chosen["model"]}
        provider = providers.get(values.get("provider") or "")
        if provider is None:
            raise ValueError("Choose one of the AI apps listed on the page.")
        model = values.get("model") or ""
        if model not in [m["id"] for m in provider["models"]]:
            raise ValueError(f"Choose a model for {provider['label']}.")
        if require_ready and not provider["ready"]:
            raise ValueError(provider["note"] or f"{provider['label']} is not ready on this PC. Choose another AI.")
        steps = values.get("steps") or {}
        return {
            "count": count, "source": source, "provider": provider["id"], "model": model,
            "steps": {step: bool(steps.get(step, DEFAULT_STEPS[step])) for step in STEP_IDS},
            "include_unprepared": bool(values.get("include_unprepared")),
        }

    def save_preferences(self, values: dict) -> dict:
        config = self.validate(values, require_ready=False)
        self._remember(config)
        return config

    def _remember(self, config: dict) -> None:
        # The AI is Settings' choice, never remembered here; a one-run choice from the chat stays one run.
        self.s.set_pref("pipeline_preferences", {k: config[k] for k in ("count", "source", "steps")})
        # One "where to look" everywhere: the chat's "find jobs" reads this preference too.
        self.s.set_pref("discovery_preferences", {"preset": config["source"]})

    def start(self, values: dict) -> dict:
        from backend.countries import require_known_authorization
        require_known_authorization(self.w.root)
        config = self.validate(values)
        with self.lock:
            if self._latest(active=True):
                raise ValueError("A search is already running. Wait for it to finish, or stop it first.")
            if hunt_active(self.w):
                raise ValueError("An overnight hunt is running. Stop it on Daily Search, or wait for it to finish.")
            with self.w.connect() as db:
                if db.execute("SELECT 1 FROM agent_runs WHERE kind='discovery' AND state IN ('queued','running')").fetchone():
                    raise ValueError("A job search started elsewhere is still running. Try again when it finishes.")
            remaining = self.s.goals()["remaining_today"]
            if remaining <= 0:
                raise ValueError("Today's plan is already complete, so there is nothing left to search for. "
                                 "Raise your plan with Edit plan to search for more.")
            target = min(config["count"], remaining)
            id = uuid.uuid4().hex
            progress = {
                "stage": "Waiting to start", "jobs_target": target, "started_epoch": time.time(),
                "find": {"state": "waiting"}, "jobs": [],
            }
            now = self.s.now()
            with self.w.connect() as db:
                db.execute(
                    "INSERT INTO pipeline_runs(id,state,config,progress,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (id, "queued", json.dumps(config), json.dumps(progress), now, now))
                self.w.record_event(db, "pipeline_started", run_id=id, **config)
            self._remember(config)
            self.thread = threading.Thread(target=self._run, args=(id,), name="career-pipeline", daemon=True)
            self.thread.start()
        return self.get(id)

    def stop(self, id: str) -> dict:
        with self.w.connect() as db:
            db.execute("UPDATE pipeline_runs SET stop_requested=1, updated_at=? WHERE id=? AND state IN ('queued','running')",
                       (self.s.now(), id))
        return self.get(id)

    def recover(self) -> None:
        from backend.services.task_execution import TaskRepository
        TaskRepository(self.s).recover_expired()
        with self.w.connect() as db:
            rows = db.execute("SELECT id FROM pipeline_runs WHERE state IN ('queued','running') ORDER BY created_at").fetchall()
        if rows:
            def resume():
                for row in rows:
                    self._run(row["id"])
            self.thread = threading.Thread(target=resume, name="career-pipeline-recovery", daemon=True)
            self.thread.start()

    def get(self, id: str) -> dict | None:
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM pipeline_runs WHERE id=?", (id,)).fetchone()
        return self._public(row) if row else None

    def _latest(self, active: bool) -> dict | None:
        states = ACTIVE if active else ("completed", "failed", "stopped")
        with self.w.connect() as db:
            row = db.execute(
                f"SELECT * FROM pipeline_runs WHERE state IN ({','.join('?' * len(states))}) ORDER BY created_at DESC, rowid DESC LIMIT 1",
                states).fetchone()
        return self._public(row) if row else None

    @staticmethod
    def _public(row) -> dict:
        return {
            "id": row["id"], "state": row["state"], "config": json.loads(row["config"]),
            "progress": json.loads(row["progress"] or "{}"), "error": row["error"],
            "stop_requested": bool(row["stop_requested"]), "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }

    # ----- running -------------------------------------------------------------

    def _save(self, id, progress, state="running", error=None, finished=False):
        now = self.s.now()
        with self.w.connect() as db:
            db.execute(
                "UPDATE pipeline_runs SET state=?, progress=?, error=?, updated_at=?, finished_at=COALESCE(?, finished_at) WHERE id=?",
                (state, json.dumps(progress, ensure_ascii=False), error, now, now if finished else None, id))

    def _stop_requested(self, id) -> bool:
        with self.w.connect() as db:
            row = db.execute("SELECT stop_requested FROM pipeline_runs WHERE id=?", (id,)).fetchone()
        return bool(row and row[0])

    def _run(self, id: str) -> None:
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM pipeline_runs WHERE id=?", (id,)).fetchone()
        config, progress = json.loads(row["config"]), json.loads(row["progress"])
        try:
            if (progress.get("find") or {}).get("state") != "done":
                self._find(id, config, progress)
            steps = [step for step in STEP_IDS if config["steps"].get(step)]
            total = len(progress["jobs"])
            for number, job in enumerate(progress["jobs"], 1):
                self._job(id, config, progress, job, steps, number, total)
            verdicts = [(job["steps"].get("ready") or {}).get("verdict") for job in progress["jobs"]]
            if any(verdicts):
                progress["ready"] = {key: verdicts.count(key) for key in ("ready", "review", "blocked")}
            stopped = self._stop_requested(id)
            progress["stage"] = "Stopped" if stopped else "Done"
            progress["finished_epoch"] = time.time()
            self._save(id, progress, "stopped" if stopped else "completed", finished=True)
            with self.w.connect() as db:
                self.w.record_event(db, "pipeline_finished", run_id=id, state="stopped" if stopped else "completed",
                                    jobs=total)
        except Stopped:
            progress["stage"] = "Stopped"
            progress["finished_epoch"] = time.time()
            self._save(id, progress, "stopped", finished=True)
        except Exception as exc:  # the page must always learn how the run ended
            progress["stage"] = "Stopped by a problem"
            progress["finished_epoch"] = time.time()
            self._save(id, progress, "failed", error=str(exc)[:1500], finished=True)
        finally:
            try:
                self.s.export_state()
            except Exception:
                pass

    def _follow_ups(self, config) -> list[dict]:
        """Focused web passes for a search that came back short; none for the no-AI sources."""
        if not next(s for s in SOURCES if s["id"] == config["source"])["ai"]:
            return []  # "My company list" and "Job boards + employer feeds" promise no AI search
        from backend.services.search_plan import strategies

        try:
            plan = strategies(self.w.root, sources="ai")
        except Exception:  # noqa: BLE001 - no target roles yet: the first pass is all there is
            return []
        return [{"label": s["label"], "preset": "default",
                 "focus": {"id": s["id"], "label": s["label"], "queries": s["queries"], "max_age_days": s.get("max_age_days", 30)}}
                for s in plan[:MAX_FIND_PASSES - 1]]

    def _find(self, id, config, progress) -> None:
        progress["find"] = {"state": "running", "started_epoch": time.time(), "passes": []}
        progress["stage"] = "Finding jobs"
        self._save(id, progress)
        provider, model = self._web_choice(config)
        target = progress["jobs_target"]
        started = time.time()
        added, skip = [], []
        passes = [{"label": "Your search", "preset": config["source"], "focus": None}] + self._follow_ups(config)
        for number, one in enumerate(passes, 1):
            if len(added) >= target or (number > 1 and self._stop_requested(id)):
                break
            if number > 1:
                progress["stage"] = f"Finding more jobs ({len(added)} of {target} so far): {one['label']}"
                self._save(id, progress)
            # A follow-up pass skips every posting this run already looked at.
            focus = {**one["focus"], "skip_urls": skip[-150:]} if one["focus"] else None
            pass_started = time.time()
            try:
                record = self._agent("discovery", None, provider, model, preset=one["preset"],
                                     count=target - len(added), focus=focus)
            except Exception as exc:
                if number == 1:
                    progress["find"] = {"state": "failed", "seconds": round(time.time() - started, 1), "error": str(exc)[:600]}
                    self._save(id, progress)
                    raise ValueError("Finding jobs did not finish: " + str(exc)) from None
                # A later pass failing keeps what the earlier ones found.
                progress["find"]["passes"].append({"label": one["label"], "state": "failed", "error": str(exc)[:300],
                                                   "seconds": round(time.time() - pass_started, 1)})
                break
            result = record.get("result") or {}
            new = [job_id for job_id in result.get("added_job_ids") or [] if job_id not in added]
            added += new
            skip += [str(job["url"]) for job in result.get("jobs") or [] if isinstance(job, dict) and job.get("url")]
            skip += [str(line).split(": ", 1)[0] for line in result.get("rejected_leads") or [] if str(line).startswith("http")]
            progress["find"]["passes"].append({"label": one["label"], "state": "done", "found": len(new),
                                               "looked": len(result.get("jobs") or []), "run_id": record.get("id"),
                                               "seconds": round(time.time() - pass_started, 1)})
            self._save(id, progress)
        runs = progress["find"]["passes"]
        note = f"Found {len(added)} new job{'s' if len(added) != 1 else ''}"
        if len(runs) > 1:
            note += f" in {len(runs)} searches"
        if len(added) < target:
            note += f" (asked for {target}; only jobs that pass every check are saved)"
        progress["find"] = {"state": "done", "seconds": round(time.time() - started, 1),
                            "first_pass_seconds": runs[0].get("seconds"), "found": len(added),
                            "note": note, "run_id": runs[-1].get("run_id") or runs[0].get("run_id"), "passes": runs}
        ids = list(added)
        if config.get("include_unprepared"):
            # Saved jobs an earlier run found but never prepared (it stopped midway) join this one.
            left = [j["id"] for j in self.w.jobs() if j.get("status") == "saved" and not j.get("folder") and j["id"] not in ids]
            ids += left[:max(0, progress["jobs_target"] - len(ids))]
            if len(ids) > len(added):
                progress["find"]["note"] += f"; {len(ids) - len(added)} saved earlier and not yet prepared"
        jobs = []
        for job_id in ids:
            job = self.w.get_job(job_id)
            jobs.append({"id": job_id, "company": job["company"], "title": job["title"], "fit": job.get("fit_score"),
                         "steps": {step: {"state": "waiting"} for step in STEP_IDS if config["steps"].get(step)}})
        # Ranked: the best fit is prepared first, whichever pass found it.
        jobs.sort(key=lambda job: -(job["fit"] if isinstance(job["fit"], (int, float)) else -1))
        progress["jobs"] = jobs
        self._save(id, progress)

    def _job(self, id, config, progress, job, steps, number, total) -> None:
        stopped, opened = None, False
        for step in steps:
            if self._stop_requested(id):
                job["steps"][step] = {"state": "skipped"}
                continue
            if stopped:
                job["steps"][step] = {"state": "skipped", "note": stopped}
                continue
            try:
                from backend.services.opportunities import preparation_issue
                issue = preparation_issue(self.w.get_job(job["id"]))
                if step != "posting" and issue:
                    raise JobStopped(issue)
                opened = open_draft(self.studio, job["id"], step, opened)
            except Exception as exc:
                stopped = "Could not open this job's resume: " + str(exc)[:500]
                job["steps"][step] = {"state": "failed", "error": stopped}
                self._save(id, progress)
                continue
            progress["stage"] = f"{STEP[step]['label']} for {job['company']} (job {number} of {total})"
            job["steps"][step] = {"state": "running", "started_epoch": time.time()}
            self._save(id, progress)
            started = time.time()
            try:
                done = self.run_step(step, job["id"], config)
                job["steps"][step] = step_entry(done, round(time.time() - started, 1))
            except JobStopped as exc:
                stopped = str(exc)
                job["steps"][step] = {"state": "failed", "seconds": round(time.time() - started, 1), "error": stopped}
            except Exception as exc:
                job["steps"][step] = {"state": "failed", "seconds": round(time.time() - started, 1), "error": str(exc)[:600]}
            self._save(id, progress)

    def _web_choice(self, config):
        """Steps that search the web run on the chosen AI when it can; otherwise the app picks one that can."""
        engine = self.runner.gateway.providers.get(config["provider"])
        if engine is not None and "web" in engine.capabilities:
            return config["provider"], config["model"]
        return None, None

    def _team(self, config):
        from backend.ai import _default_model, route_options, router, usage_recorder
        from backend.ai.agents.graph import AgentTeam

        provider, model = config["provider"], config["model"]
        # Reading and classifying runs on the provider's light model; writing on the chosen one.
        # Auto picks each plan's writing and reading model itself.
        cheap = model if provider in ("codex", "kimi_cli", "auto") else _default_model(provider, "cheap")
        from backend.ai.persona import persona_for

        route = route_options(self.s, "daily_search")
        if config.get("free_only"):
            paid_gate = route.get("paid_gate")

            def free_gate(provider_id):
                if router.is_paid(provider_id):
                    return "Paid AI is disabled for this hunt."
                return paid_gate(provider_id) if paid_gate else None

            route = {**route, "policy": router.free_only(route.get("policy")), "paid_gate": free_gate}
        return AgentTeam(self.w.root, {"strong": (provider, model), "cheap": (provider, cheap)}, usage_recorder(self.s),
                         persona=persona_for(self.w.root), route=route)

    def run_step(self, step: str, job_id: str, config: dict) -> dict:
        """One helper (any of STEP_IDS) for one saved job, outside a Daily Search run.

        The overnight hunt (services/hunt.py) prepares the jobs it saves through the same
        helpers, so its resumes get the same research, tailoring and page contract. ``config``
        needs provider and model; ``free_only`` keeps Auto on the free plans.
        """
        if step not in STEP:
            raise ValueError("Unknown helper: " + step)
        from backend.services.opportunities import preparation_issue, digest
        from backend.services.task_execution import TaskRepository, job_write_slot
        job = self.w.get_job(job_id)
        issue = preparation_issue(job)
        if step != "posting" and issue:
            raise JobStopped(issue)
        # Freshness and final checks always read current state; never reuse an old verdict.
        if step in {"posting", "ready"}:
            return getattr(self, "_" + step)(job_id, config)
        evidence = self.w.evidence()
        inputs = {"version": "preparation-v2", "jd": digest(job.get("description")),
                  "profile": self.w.profile(), "evidence_revision": evidence.get("candidate_revision"),
                  "config": {k: config.get(k) for k in ("provider", "model", "free_only")},
                  "salary": job.get("opportunity")}
        if step in {"pdf", "review"} and hasattr(self.studio, "get"):
            draft = self.studio.get(job_id)
            inputs["draft"] = digest(draft.get("source"))
            if step == "review":
                inputs["artifact"] = self._artifact(job_id)
        def execute():
            if step in {"tailor", "pdf"}:
                with job_write_slot(self.w.root, job_id):
                    result = getattr(self, "_" + step)(job_id, config)
            else:
                result = getattr(self, "_" + step)(job_id, config)
            return {"result": result, "artifact": self._artifact(job_id, step)}
        result = TaskRepository(self.s).run("prepare:" + step, inputs, execute, job_id=job_id,
            validate_result=lambda saved: saved.get("artifact") == self._artifact(job_id, step),
            retryable=lambda exc: any(x in str(exc).lower() for x in ("timeout", "connection", "temporar", "rate limit", "usage limit")))
        return result["result"]

    def _artifact(self, job_id, step=None):
        from backend.services.opportunities import digest
        from career import safe_child
        job = self.w.get_job(job_id)
        artifact = {}
        if hasattr(self.studio, "get"):
            try:
                draft = self.studio.get(job_id)
                if step not in {"research", "study_plan"}:
                    artifact.update(revision=draft["revision"], source=digest(draft.get("source")))
                preview = draft.get("preview") or {}
                if step in {None, "pdf", "review"} and preview.get("path"):
                    pdf = safe_child(self.w.root / "data/output", str(preview["path"]) + "/resume.pdf")
                    import hashlib
                    artifact["pdf"] = hashlib.sha256(pdf.read_bytes()).hexdigest() if pdf.is_file() else None
            except ValueError:
                artifact["draft_missing"] = True
        if step in {"research", "study_plan"} and job.get("folder"):
            name = "company-research.md" if step == "research" else "study-plan.md"
            folder = Path(job["folder"]).relative_to("data/output")
            path = safe_child(self.w.root / "data/output", str(folder / name))
            artifact["report"] = digest(path.read_text(encoding="utf-8")) if path.is_file() else None
        return artifact

    def _agent(self, kind, job_id, provider, model, preset="default", count=None, timeout_minutes=45, config=None,
               focus=None) -> dict:
        queued = self.runner.enqueue(kind, job_id, provider, model, preset, count=count, focus=focus,
                                     free_only=bool((config or {}).get("free_only")))
        deadline = time.monotonic() + timeout_minutes * 60
        while time.monotonic() < deadline:
            with self.w.connect() as db:
                row = db.execute("SELECT id, state, result, error FROM agent_runs WHERE id=?", (queued["id"],)).fetchone()
            if row and row["state"] == "completed":
                return {"id": row["id"], "result": json.loads(row["result"] or "{}")}
            if row and row["state"] == "failed":
                raise ValueError(row["error"] or "The step failed without a reason.")
            time.sleep(self.poll)
        raise ValueError(f"This step did not finish within {timeout_minutes} minutes.")

    def _posting(self, job_id, config) -> dict:
        from backend.job_quality import JobQualityService

        if self.w.get_job(job_id).get("record_source") == "gmail":
            return {"note": "This record has no public posting to check.", "skipped": True}
        checked = JobQualityService(self.s).verify_posting(job_id)
        if checked.get("excluded"):
            raise JobStopped("The posting now refuses your work permit, so it moved to Excluded roles "
                             "and nothing more was prepared.")
        if checked["state"] == "expired":
            raise JobStopped("The posting has closed (" + " ".join(checked["evidence"])[:200]
                             + "), so nothing more was prepared.")
        from backend.services.opportunities import preparation_issue
        issue = preparation_issue(self.w.get_job(job_id))
        if issue:
            raise JobStopped(issue)
        if checked["state"] == "needs_review":
            return {"note": "The site blocked the automatic check; the other steps still run. "
                            "Open the link to confirm it is open before applying.", "posting": "needs_review"}
        return {"note": "Still open.", "posting": "active"}

    def _research(self, job_id, config) -> dict:
        provider, model = self._web_choice(config)
        record = self._agent("research", job_id, provider, model, config=config)
        # The research run itself saves company-research.md into the application folder.
        if not (record["result"] or {}).get("path"):
            return {"note": "Research finished (no application folder to save it in)."}
        return {"note": "Saved to company-research.md"}

    def _tailor(self, job_id, config) -> dict:
        result = self.studio.tailor(job_id, self._team(config))
        items = result.get("items") or {}
        note = f"{items.get('verified', 0)} items from your profile evidence"
        warnings = result.get("warnings") or []
        return {"note": note + (". " + warnings[0] if warnings else "")}

    def _study_plan(self, job_id, config) -> dict:
        provider, model = self._web_choice(config)
        self._agent("study_plan", job_id, provider, model, config=config)
        return {"note": "Saved to study-plan.md"}

    def _pdf(self, job_id, config) -> dict:
        from backend.resume_contract import contract_for

        contract = contract_for(self.w.root, self.w.get_job(job_id).get("market") or None)
        draft = self.studio.get(job_id)
        preview = draft.get("preview") or {}
        if preview.get("current") and preview.get("revision") == draft["revision"] and preview.get("page_count") == contract.pages:
            return {"note": f"{contract.describe_pages().capitalize()}. The PDF made while tailoring is current.", "quick": True}
        fitted = self.studio.fit(job_id, draft["revision"])
        pages = (fitted.get("preview") or {}).get("page_count")
        if pages != contract.pages:
            raise ValueError(f"The PDF came out at {pages} pages. Open Resume Studio and use Fit to {contract.describe_pages()}.")
        return {"note": f"{contract.describe_pages().capitalize()} PDF ready"}

    def _review(self, job_id, config) -> dict:
        draft = self.studio.get(job_id)
        preview = draft.get("preview") or {}
        if not preview.get("current") or preview.get("revision") != draft["revision"]:
            return {"note": "No current PDF to review: the resume steps did not finish.", "skipped": True}
        # A fresh AI with the PDF text and the posting only (services/agents.py resume_match); no web.
        record = self._agent("resume_match", job_id, config["provider"], config["model"], config=config)
        result = record.get("result")
        review = result.get("review") if isinstance(result, dict) else None
        review = review if isinstance(review, dict) else {}
        summary = " ".join(str(review.get("summary") or "").split())
        verdict = review.get("verdict")
        issues = review.get("issues")
        if (not isinstance(verdict, str) or verdict not in {"pass", "review", "blocked"}
                or not isinstance(issues, list) or any(not isinstance(issue, str) or not issue.strip() for issue in issues)
                or verdict != "pass" and not issues):
            verdict, issues = "review", ["Run a new independent review with a recorded verdict."]
        elif verdict == "pass" and issues:
            verdict = "review"
        label = {"pass": "Independent check passed", "review": "Independent review needs your attention",
                 "blocked": "Independent review found a blocking issue"}[verdict]
        return {"note": label + (": " + summary[:300] if summary else "."), "review_verdict": verdict, "issues": issues}

    def _ready(self, job_id, config) -> dict:
        from backend.services import readiness

        try:
            self.studio.score(job_id)  # the deterministic assessment of the current PDF; a no-op when it has one
        except ValueError:
            pass  # no current PDF: the check below says so
        result = readiness.check(self.s, self.studio, job_id)
        head = result["label"] + (f" · readiness {result['score']}/100" if result["score"] is not None else "")
        return {"note": head + (". Next: " + result["next"][0] if result["next"] else "."),
                "verdict": result["verdict"], "score": result["score"], "next": result["next"][:3]}
