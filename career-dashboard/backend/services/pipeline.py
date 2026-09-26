"""The Daily Search pipeline: find jobs, then run enabled helpers for each new job.

The Daily Search page lets the candidate choose how many jobs to find, where to look,
which AI does the work and which helpers run (company research, resume
tailoring, study plan, contract-compliant PDF). This module checks that choice, runs it
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
    {"id": "research", "label": "Company research", "ai": True, "web": True,
     "minutes": 4.0, "tokens": 30000, "budget_calls": 3,
     "what": "Reads about the company and how a hiring manager would see the role. The resume tailor uses it."},
    {"id": "tailor", "label": "Tailor resume", "ai": True, "web": False,
     "minutes": 3.0, "tokens": 22000, "budget_calls": 0,
     "what": "Rewrites your Projects and Skills for this job. You keep or remove each suggestion in Assurance."},
    {"id": "study_plan", "label": "Study plan", "ai": True, "web": False,
     "minutes": 1.5, "tokens": 8000, "budget_calls": 1,
     "what": "Lists what to learn before the interview. It never goes on your resume."},
    {"id": "pdf", "label": "Resume PDF", "ai": False, "web": False,
     "minutes": 0.5, "tokens": 0, "budget_calls": 0,
     "what": "Builds the PDF and checks it against the profile's page contract. No AI."},
]
STEP_IDS = [step["id"] for step in STEPS]
STEP = {step["id"]: step for step in STEPS}
# The same helpers the optional morning run uses. Research is off by default.
DEFAULT_STEPS = {"research": False, "tailor": True, "study_plan": True, "pdf": True}


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
                    ai = step not in ("find_pages", "find_feeds", "pdf")
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
                add(step, found.get("seconds"), find_seconds(step, progress.get("jobs_target") or config.get("count", 5)))
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
        with self.w.connect() as db:
            db.execute(
                "UPDATE pipeline_runs SET state='failed', error=?, updated_at=?, finished_at=? WHERE state IN ('queued','running')",
                ("The app stopped during this run. Start the search again.", self.s.now(), self.s.now()))

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
            self._find(id, config, progress)
            steps = [step for step in STEP_IDS if config["steps"].get(step)]
            total = len(progress["jobs"])
            for number, job in enumerate(progress["jobs"], 1):
                self._job(id, config, progress, job, steps, number, total)
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

    def _find(self, id, config, progress) -> None:
        progress["find"] = {"state": "running", "started_epoch": time.time()}
        progress["stage"] = "Finding jobs"
        self._save(id, progress)
        provider, model = self._web_choice(config)
        started = time.time()
        try:
            record = self._agent("discovery", None, provider, model, preset=config["source"], count=config["count"])
        except Exception as exc:
            progress["find"] = {"state": "failed", "seconds": round(time.time() - started, 1), "error": str(exc)[:600]}
            self._save(id, progress)
            raise ValueError("Finding jobs did not finish: " + str(exc)) from None
        result = record.get("result") or {}
        added = result.get("added_job_ids") or []
        note = f"Found {len(added)} new job{'s' if len(added) != 1 else ''}"
        if len(added) < progress["jobs_target"]:
            note += f" (asked for {progress['jobs_target']}; only jobs that pass every check are saved)"
        progress["find"] = {"state": "done", "seconds": round(time.time() - started, 1), "found": len(added),
                            "note": note, "run_id": record.get("id")}
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
            jobs.append({"id": job_id, "company": job["company"], "title": job["title"],
                         "steps": {step: {"state": "waiting"} for step in STEP_IDS if config["steps"].get(step)}})
        progress["jobs"] = jobs
        self._save(id, progress)

    def _job(self, id, config, progress, job, steps, number, total) -> None:
        if not steps:
            return
        if self._stop_requested(id):
            for step in steps:
                job["steps"][step] = {"state": "skipped"}
            self._save(id, progress)
            return
        try:
            # The application folder and Studio draft every helper writes into; no AI.
            self.studio.open(job["id"])
        except Exception as exc:
            for step in steps:
                job["steps"][step] = {"state": "failed", "error": "Could not open this job's resume: " + str(exc)[:500]}
            self._save(id, progress)
            return
        for step in steps:
            if self._stop_requested(id):
                job["steps"][step] = {"state": "skipped"}
                continue
            progress["stage"] = f"{STEP[step]['label']} for {job['company']} (job {number} of {total})"
            job["steps"][step] = {"state": "running", "started_epoch": time.time()}
            self._save(id, progress)
            started = time.time()
            try:
                done = getattr(self, "_" + step)(job["id"], config)
                job["steps"][step] = {"state": "done", "seconds": round(time.time() - started, 1), **done}
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
            route = {**route, "policy": router.free_only(route.get("policy"))}
        return AgentTeam(self.w.root, {"strong": (provider, model), "cheap": (provider, cheap)}, usage_recorder(self.s),
                         persona=persona_for(self.w.root), route=route)

    def run_step(self, step: str, job_id: str, config: dict) -> dict:
        """One helper (research, tailor, study_plan, pdf) for one saved job, outside a Daily Search run.

        The overnight hunt (services/hunt.py) prepares the jobs it saves through the same
        helpers, so its resumes get the same research, tailoring and page contract. ``config``
        needs provider and model; ``free_only`` keeps Auto on the free plans.
        """
        if step not in STEP:
            raise ValueError("Unknown helper: " + step)
        return getattr(self, "_" + step)(job_id, config)

    def _agent(self, kind, job_id, provider, model, preset="default", count=None, timeout_minutes=45, config=None) -> dict:
        queued = self.runner.enqueue(kind, job_id, provider, model, preset, count=count,
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
        note = (f"{items.get('verified', 0)} items from your profile + {items.get('predicted', 0)} "
                "suggestions to keep or remove in Assurance")
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
