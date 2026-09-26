"""The overnight hunt: keep searching until enough good jobs are found, then prepare each one.

A Daily Search run is one pass: one search, whatever it finds, done. The hunt is a goal
with a deadline ("find 10 jobs that fit at 70 or better by 7 AM") and it loops:

1. Every no-AI source first (services/job_sources.py): the tracked companies, the
   country's verified employer directory and, for Ireland, gradireland, jobs.ie and the
   askmanavi graduate tracker. These give full posting text for free.
2. Then focused AI web-search passes (services/search_plan.py), one site group and one
   target role at a time: employer careers and ATS pages, Irish job boards, LinkedIn and
   the public sector, graduate programmes.
3. Every posting from every pass goes through the same discovery gates as any other lead
   (services/agents.py): the posting's own text and location, the market, the work-permit
   gate, never-re-apply and duplicates, legitimacy, then the requirement check by AI on a
   free plan with the hunt's own fit bar.
4. When every plan is resting after its usage limit, the hunt does the no-AI work that is
   left and then waits for the earliest reset instead of failing, and never falls through to a
   paid API unless allowed. Postings that need an AI requirement check while no plan is free are
   held (services/search_memory.py) and checked when one is back.
5. The search memory skips postings an earlier pass (or an earlier night) already decided.
6. When the target is met, the search time is used up or every source is exhausted, each saved
   job gets the enabled helpers (research, tailored resume, study plan, PDF) through the
   Daily Search pipeline, and HUNT-REPORT.md is written.

Nothing is ever submitted and no one is contacted. The hunt only saves and prepares.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from backend.services import search_memory

MAX_TARGET = 40
MAX_HOURS = 12.0
DEFAULTS = {"target": 10, "hours": 8.0, "min_fit": 70, "sources": "all", "allow_paid": False,
            "require_ai_fit": True, "steps": {"research": True, "tailor": True, "study_plan": True, "pdf": True}}
SOURCE_CHOICES = ("all", "feeds", "ai")
# At most this many cycles through every strategy; between cycles the hunt pauses so boards can refresh.
MAX_CYCLES = 3
CYCLE_PAUSE_MINUTES = 40
# One pass (one discovery run) may take this long before the hunt moves on.
PASS_TIMEOUT_MINUTES = 75
# Seconds one feeds pass may spend reading sources.
FEED_SECONDS = 1200
# Requirement-check rounds per pass: more than a Daily Search, since time is not the constraint here.
FIT_ROUNDS = 6
# The share of the time kept for preparing the jobs found (research, tailoring, PDFs).
PREPARE_SHARE = 0.3
# The longest single sleep, so Stop is noticed quickly.
NAP_SECONDS = 20
# After the app restarts mid-hunt, it carries on by itself while at least this much time is left,
# at most this many times per hunt.
RESUME_MIN_MINUTES = 20
MAX_RESUMES = 3
# A helper step that failed for one of these reasons is tried once more after a short pause.
TRANSIENT = re.compile(r"time(?:d)? ?out|timeout|connection|temporar|unavailable|overloaded|network|"
                       r"\b50[234]\b|reset by peer|try again", re.IGNORECASE)
TRANSIENT_PAUSE_SECONDS = 60
ACTIVE = ("queued", "running", "waiting")


class Stopped(Exception):
    pass


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _clock_text(epoch: float) -> str:
    from backend.ai.router import when_text

    return when_text(_iso(epoch))


class Hunt:
    def __init__(self, services, runner, pipeline, *, poll: float = 2.0, sleep=time.sleep, clock=time.time):
        self.s, self.w, self.runner, self.pipeline = services, services.w, runner, pipeline
        self.poll, self.sleep, self.clock = poll, sleep, clock
        self.lock = threading.Lock()
        self.thread = None
        with self.w.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS hunt_runs(
                id TEXT PRIMARY KEY, state TEXT NOT NULL, config TEXT NOT NULL,
                progress TEXT NOT NULL DEFAULT '{}', error TEXT,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT);
            CREATE INDEX IF NOT EXISTS idx_hunt_runs_created ON hunt_runs(created_at);
            """)
            search_memory.ensure(db)

    # ----- what the page and the chat show ----------------------------------------------

    def overview(self) -> dict:
        from backend.services.search_plan import describe

        try:
            plan = describe(self.w.root)
        except Exception as exc:  # noqa: BLE001 - the page still shows the controls
            plan = {"error": str(exc)[:300], "strategies": []}
        with self.w.connect() as db:
            memory = search_memory.summary(db)
            held = search_memory.held_count(db)
        config = self.preferences()
        ready, wake, why = self._ai_state(config)
        return {"defaults": config, "limits": {"max_target": MAX_TARGET, "max_hours": MAX_HOURS},
                "plan": plan, "memory": {**memory, "held": held},
                "ai": {"ready": ready, "wakes_at": _iso(wake) if wake else None,
                       "wakes_text": _clock_text(wake) if wake else None, "note": why},
                **self.status()}

    def status(self) -> dict:
        return {"current": self._latest(active=True), "last": self._latest(active=False)}

    def preferences(self) -> dict:
        stored = self.s.pref("hunt_preferences", {}) or {}
        chosen = self.pipeline.preferences()
        out = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
        out["steps"] = {**DEFAULTS["steps"], **(stored.get("steps") or {})}
        out["provider"], out["model"] = chosen["provider"], chosen["model"]
        return out

    # ----- choosing and starting ------------------------------------------------------------

    def validate(self, values: dict) -> dict:
        from backend.services.pipeline import STEP_IDS

        merged = {**self.preferences(), **{k: v for k, v in values.items() if v is not None}}
        target = merged["target"]
        if isinstance(target, bool) or not isinstance(target, int) or not 1 <= target <= MAX_TARGET:
            raise ValueError(f"Choose between 1 and {MAX_TARGET} jobs to find.")
        try:
            hours = float(merged["hours"])
        except (TypeError, ValueError):
            raise ValueError("Choose how many hours the hunt may run.") from None
        if not 0.25 <= hours <= MAX_HOURS:
            raise ValueError(f"Choose between 15 minutes and {MAX_HOURS:g} hours.")
        min_fit = merged["min_fit"]
        if isinstance(min_fit, bool) or not isinstance(min_fit, int) or not 50 <= min_fit <= 95:
            raise ValueError("Choose a fit bar between 50 and 95.")
        if merged["sources"] not in SOURCE_CHOICES:
            raise ValueError("Choose where to look: all, feeds (no AI search) or ai.")
        steps = merged.get("steps") or {}
        unknown = [step for step in steps if step not in STEP_IDS]
        if unknown:
            raise ValueError("Unknown helper(s): " + ", ".join(unknown))
        from backend.ai import router

        providers = {p["id"]: p for p in self.pipeline.providers()}
        provider = providers.get(merged.get("provider") or "")
        if provider is None or not provider["ready"]:
            # The AI chosen in Settings is not ready: any AI that is (Auto comes first) takes over.
            provider = next((p for p in providers.values() if p["ready"]), None)
        sources, no_ai = merged["sources"], provider is None
        if no_ai:
            # No AI at all on this PC right now: the hunt still reads the job boards and employer
            # feeds and holds what it finds for the AI requirement check, instead of refusing to start.
            provider, model, sources = {"id": router.ID}, router.ID, "feeds"
        else:
            model = merged.get("model") or provider["models"][0]["id"]
            if model not in [m["id"] for m in provider["models"]]:
                model = provider["models"][0]["id"]
        return {"target": target, "hours": round(hours, 2), "min_fit": min_fit, "sources": sources,
                "requested_sources": merged["sources"], "no_ai": no_ai,
                "allow_paid": bool(merged.get("allow_paid")), "require_ai_fit": bool(merged.get("require_ai_fit", True)),
                "steps": {step: bool(steps.get(step, DEFAULTS["steps"][step])) for step in STEP_IDS},
                "provider": provider["id"], "model": model}

    def start(self, values: dict, *, carry: dict | None = None) -> dict:
        """Start a hunt. ``carry`` continues an interrupted one: its saved jobs and finished helper steps."""
        from backend.countries import require_known_authorization

        require_known_authorization(self.w.root)
        config = self.validate(values)
        if not self.s.agent_enabled("discovery"):
            raise ValueError("The job search agent is paused. Turn it on in Agents first.")
        with self.lock:
            if self._latest(active=True):
                raise ValueError("A hunt is already running. Wait for it, or stop it first.")
            if self.pipeline.status().get("current"):
                raise ValueError("A Daily Search is running. Wait for it to finish, or stop it first.")
            with self.w.connect() as db:
                if db.execute("SELECT 1 FROM agent_runs WHERE kind='discovery' AND state IN ('queued','running')").fetchone():
                    raise ValueError("A job search started elsewhere is still running. Try again when it finishes.")
            now = self.clock()
            search_end = now + config["hours"] * 3600 * (1 - (PREPARE_SHARE if any(config["steps"].values()) else 0))
            progress = {"stage": "Waiting to start", "started_epoch": now, "search_until_epoch": search_end,
                        "deadline_epoch": now + config["hours"] * 3600, "target": config["target"], "cycle": 0,
                        "saved": [], "passes": [], "waiting": None, "prepare": {"state": "waiting", "jobs": []}}
            if carry:
                progress.update(saved=list(carry["saved"]), done_steps=carry["done_steps"],
                                resumed_from=carry["id"], resumes=carry["resumes"])
            id = uuid.uuid4().hex
            stamp = self.s.now()
            with self.w.connect() as db:
                db.execute("INSERT INTO hunt_runs(id,state,config,progress,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                           (id, "queued", json.dumps(config), json.dumps(progress), stamp, stamp))
                self.w.record_event(db, "hunt_resumed" if carry else "hunt_started", run_id=id,
                                    **{k: config[k] for k in ("target", "hours", "min_fit", "sources")})
            if not carry:
                # The person's own choices are remembered, not a fallback made for this run only.
                self.s.set_pref("hunt_preferences", {**{k: config[k] for k in ("target", "hours", "min_fit", "allow_paid",
                                                                              "require_ai_fit", "steps")},
                                                     "sources": config["requested_sources"]})
            self.thread = threading.Thread(target=self._run, args=(id,), name="career-hunt", daemon=True)
            self.thread.start()
        return self.get(id)

    def stop(self, id: str) -> dict | None:
        with self.w.connect() as db:
            db.execute("UPDATE hunt_runs SET stop_requested=1, updated_at=? WHERE id=? AND state IN ('queued','running','waiting')",
                       (self.s.now(), id))
        return self.get(id)

    def recover(self) -> None:
        with self.w.connect() as db:
            db.execute("UPDATE hunt_runs SET state='interrupted', error=?, updated_at=?, finished_at=? "
                       "WHERE state IN ('queued','running','waiting')",
                       ("The app stopped during this hunt. Jobs it saved stay saved; start it again to continue "
                        "(the search memory skips what it already checked).", self.s.now(), self.s.now()))

    def resume_interrupted(self) -> dict | None:
        """After the app restarted mid-hunt: carry on with the latest interrupted hunt while its time lasts.

        The new run keeps the jobs already saved and skips the helper steps already done; the
        search memory skips the postings already checked. At most MAX_RESUMES times per hunt,
        so a hunt that keeps stopping the app is not started again and again.
        """
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM hunt_runs WHERE state='interrupted' "
                             "ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
        if not row or self._latest(active=True):
            return None
        old = self._public(row)
        progress = old["progress"]
        left = float(progress.get("deadline_epoch") or 0) - self.clock()
        resumes = int(progress.get("resumes") or 0)
        if progress.get("resumed_into") or left < RESUME_MIN_MINUTES * 60 or resumes >= MAX_RESUMES:
            return None
        config = old["config"]
        values = {k: config.get(k) for k in ("target", "min_fit", "allow_paid", "require_ai_fit", "steps")}
        values.update(sources=config.get("requested_sources") or config.get("sources") or "all",
                      hours=round(min(MAX_HOURS, max(0.25, left / 3600)), 2))
        done = {job_id: list(steps) for job_id, steps in (progress.get("done_steps") or {}).items()}
        for job in (progress.get("prepare") or {}).get("jobs") or []:
            finished = [step for step, state in (job.get("steps") or {}).items() if state.get("state") == "done"]
            done[job["id"]] = sorted(set(done.get(job["id"], [])) | set(finished))
        try:
            new = self.start(values, carry={"id": old["id"], "saved": progress.get("saved") or [],
                                            "done_steps": done, "resumes": resumes + 1})
        except Exception:  # noqa: BLE001 - the person can still start one by hand
            return None
        progress["resumed_into"] = new["id"]
        with self.w.connect() as db:
            db.execute("UPDATE hunt_runs SET progress=?, error=? WHERE id=?",
                       (json.dumps(progress, ensure_ascii=False),
                        "The app stopped during this hunt, so it carried on by itself in a new run "
                        "with the jobs already saved.", old["id"]))
        return new

    def get(self, id: str) -> dict | None:
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM hunt_runs WHERE id=?", (id,)).fetchone()
        return self._public(row) if row else None

    def _latest(self, active: bool) -> dict | None:
        states = ACTIVE if active else ("completed", "failed", "stopped", "interrupted")
        with self.w.connect() as db:
            row = db.execute(f"SELECT * FROM hunt_runs WHERE state IN ({','.join('?' * len(states))}) "
                             "ORDER BY created_at DESC, rowid DESC LIMIT 1", states).fetchone()
        return self._public(row) if row else None

    @staticmethod
    def _public(row) -> dict:
        return {"id": row["id"], "state": row["state"], "config": json.loads(row["config"]),
                "progress": json.loads(row["progress"] or "{}"), "error": row["error"],
                "stop_requested": bool(row["stop_requested"]), "created_at": row["created_at"],
                "finished_at": row["finished_at"]}

    # ----- running ----------------------------------------------------------------------------

    def _save(self, id, progress, state="running", error=None, finished=False):
        now = self.s.now()
        with self.w.connect() as db:
            db.execute("UPDATE hunt_runs SET state=?, progress=?, error=?, updated_at=?, "
                       "finished_at=COALESCE(?, finished_at) WHERE id=?",
                       (state, json.dumps(progress, ensure_ascii=False), error, now, now if finished else None, id))

    def _stop_requested(self, id) -> bool:
        with self.w.connect() as db:
            row = db.execute("SELECT stop_requested FROM hunt_runs WHERE id=?", (id,)).fetchone()
        return bool(row and row[0])

    def _nap(self, id, seconds: float) -> None:
        end = self.clock() + seconds
        while self.clock() < end:
            if self._stop_requested(id):
                raise Stopped()
            self.sleep(min(NAP_SECONDS, max(0.0, end - self.clock())))

    def _run(self, id: str) -> None:
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM hunt_runs WHERE id=?", (id,)).fetchone()
        config, progress = json.loads(row["config"]), json.loads(row["progress"])
        config["free_only"] = not config.get("allow_paid")
        state, error = "completed", None
        try:
            self._search(id, config, progress)
            self._prepare(id, config, progress)
        except Stopped:
            state = "stopped"
            progress["stage"] = "Stopped"
        except Exception as exc:  # the page must always learn how the hunt ended
            state, error = "failed", str(exc)[:1500]
            progress["stage"] = "Stopped by a problem"
        progress["waiting"] = None
        progress["finished_epoch"] = self.clock()
        if state == "completed":
            progress["stage"] = "Done"
        try:
            progress["report"] = self._report(config, progress, state, error)
        except Exception:  # noqa: BLE001 - a report failure never hides the result
            pass
        self._save(id, progress, state, error=error, finished=True)
        try:
            with self.w.connect() as db:
                self.w.record_event(db, "hunt_finished", run_id=id, state=state, saved=len(progress["saved"]))
            self.s.export_state()
        except Exception:  # noqa: BLE001
            pass

    # ----- the search loop ------------------------------------------------------------------

    def _search(self, id, config, progress) -> None:
        from backend.services.search_plan import strategies

        plan = strategies(self.w.root, sources=config["sources"])
        if not plan:
            raise ValueError("There is nothing to search yet: the profile has no target roles. "
                             "Add them in Profile (or tell the Assistant), then start the hunt again.")
        progress["strategy_count"] = len(plan)
        for cycle in range(1, MAX_CYCLES + 1):
            progress["cycle"] = cycle
            saved_before = len(progress["saved"])
            candidates_seen = 0
            queue = list(plan)
            while queue:
                if self._stop_requested(id):
                    raise Stopped()
                if len(progress["saved"]) >= config["target"] or self.clock() >= progress["search_until_epoch"]:
                    return
                strategy = queue.pop(0)
                if strategy["kind"] == "ai":
                    ready, wake, why = self._ai_state(config)
                    if not ready:
                        # No plan is free: do any no-AI pass first, then wait for the earliest reset.
                        feeds_left = [s for s in queue if s["kind"] == "feeds"]
                        if feeds_left:
                            queue.remove(feeds_left[0])
                            queue[:0] = [feeds_left[0], strategy]
                            continue
                        if not self._wait_for_ai(id, progress, wake, why):
                            self._skip(progress, strategy, "No AI plan was free before the search time ran out" +
                                       (f" ({why})" if why else "") + ".")
                            continue
                    # A plan is free: postings held for their requirement check go first.
                    candidates_seen += self._held_pass(id, config, progress)
                    if len(progress["saved"]) >= config["target"]:
                        return
                candidates_seen += self._pass(id, config, progress, strategy)
            # Held postings get their AI check before the cycle ends, when a plan is free.
            if self._ai_state(config)[0]:
                candidates_seen += self._held_pass(id, config, progress)
            if len(progress["saved"]) >= config["target"]:
                return
            if cycle == MAX_CYCLES or (candidates_seen == 0 and len(progress["saved"]) == saved_before):
                progress["exhausted"] = cycle < MAX_CYCLES
                return
            pause = min(CYCLE_PAUSE_MINUTES * 60, progress["search_until_epoch"] - self.clock())
            if pause <= 60:
                return
            progress["stage"] = (f"Cycle {cycle} done ({len(progress['saved'])} of {config['target']} saved). "
                                 f"Looking again at {_clock_text(self.clock() + pause)} for newly posted roles")
            progress["waiting"] = {"until": _iso(self.clock() + pause), "until_text": _clock_text(self.clock() + pause),
                                   "why": "Pausing between cycles so boards can post new roles"}
            self._save(id, progress, "waiting")
            self._nap(id, pause)
            progress["waiting"] = None

    def _ai_state(self, config) -> tuple[bool, float | None, str]:
        """(a plan can take an AI step now, when the first resting one comes back, why not)."""
        from backend.ai import limits, ready_providers, router

        root = self.w.root
        try:
            ready = ready_providers(root)
        except Exception:  # noqa: BLE001
            return False, None, "Could not check which AI apps are set up"
        book = limits.HealthBook(root)
        provider = config.get("provider") or router.ID

        def wake_of(p):
            rest = book.resting(p)
            when = limits._parse(rest["until"]) if rest else None
            return when.timestamp() if when else None

        if provider != router.ID:
            if not ready.get(provider):
                return False, None, f"{router.label(provider)} is not set up on this PC"
            wake = wake_of(provider)
            return (wake is None), wake, (f"{router.label(provider)} rests until {_clock_text(wake)}" if wake else "")
        policy = router.policy_from(self.s.pref("ai_preferences", {}) or {})
        if not config.get("allow_paid"):
            policy = router.free_only(policy)
        if router.available(root, policy, "strong", ready_map=ready, book=book):
            return True, None, ""
        wakes = sorted((w, p) for p in router.ordered(policy)
                       if policy["enabled"].get(p) and ready.get(p) and (w := wake_of(p)))
        if not wakes:
            return False, None, "No free AI plan is set up on this PC" if not config.get("allow_paid") else "No AI is set up"
        when, name = wakes[0]
        return False, when, f"{router.label(name)} rests until {_clock_text(when)}"

    def _wait_for_ai(self, id, progress, wake, why) -> bool:
        """Sleep until the earliest plan reset, if it comes before the search time ends."""
        if not wake or wake >= progress["search_until_epoch"]:
            return False
        progress["stage"] = f"Waiting for an AI plan: {why}"
        progress["waiting"] = {"until": _iso(wake), "until_text": _clock_text(wake), "why": why}
        self._save(id, progress, "waiting")
        self._nap(id, max(0.0, wake - self.clock()) + 30)
        progress["waiting"] = None
        self._save(id, progress)
        return True

    def _skip(self, progress, strategy, note) -> None:
        progress["passes"].append({"id": strategy["id"], "label": strategy["label"], "kind": strategy["kind"],
                                   "state": "skipped", "note": note, "cycle": progress.get("cycle")})

    def _held_pass(self, id, config, progress) -> int:
        with self.w.connect() as db:
            waiting = search_memory.held_count(db)
        if not waiting:
            return 0
        strategy = {"id": "feeds:held", "kind": "feeds", "label": f"AI requirement check for {waiting} held postings",
                    "sources": ["held"]}
        return self._pass(id, config, progress, strategy)

    def _pass(self, id, config, progress, strategy) -> int:
        """One discovery run for one strategy; returns how many postings it looked at."""
        from backend.services.agents import MAX_DISCOVERY_JOBS

        remaining = config["target"] - len(progress["saved"])
        if remaining <= 0:
            return 0
        count = max(1, min(MAX_DISCOVERY_JOBS, remaining))
        focus = {"hunt": True, "label": strategy["label"], "min_fit": config["min_fit"],
                 "require_ai_fit": config["require_ai_fit"], "fit_rounds": FIT_ROUNDS}
        if strategy["kind"] == "feeds":
            preset = "feeds"
            focus.update(sources=strategy["sources"], seconds=FEED_SECONDS)
            provider = model = None
        else:
            preset = "default"
            focus.update(queries=strategy["queries"], max_age_days=strategy.get("max_age_days", 30))
            provider, model = self.pipeline._web_choice(config)
        entry = {"id": strategy["id"], "label": strategy["label"], "kind": strategy["kind"], "state": "running",
                 "cycle": progress.get("cycle"), "started_epoch": self.clock()}
        progress["passes"].append(entry)
        progress["stage"] = (f"{strategy['label']} (cycle {progress.get('cycle')}, "
                             f"{len(progress['saved'])} of {config['target']} saved)")
        self._save(id, progress)
        try:
            record = self._discovery(id, preset, count, focus, provider, model, free_only=config.get("free_only", True))
        except Stopped:
            entry.update(state="stopped")
            raise
        except Exception as exc:  # one failed pass never ends the hunt
            entry.update(state="failed", error=str(exc)[:500], seconds=round(self.clock() - entry["started_epoch"], 1))
            self._save(id, progress)
            return 0
        result = record.get("result") or {}
        added = result.get("added_job_ids") or []
        for job_id in added:
            try:
                job = self.w.get_job(job_id)
            except ValueError:
                continue
            progress["saved"].append({"id": job_id, "company": job["company"], "title": job["title"],
                                      "location": job.get("location") or "", "fit": job.get("fit_score"),
                                      "url": job.get("url") or "", "pass": strategy["label"]})
        looked = len(result.get("jobs") or [])
        fit_check = result.get("fit_check") or {}
        entry.update(state="done", seconds=round(self.clock() - entry["started_epoch"], 1), run_id=record.get("id"),
                     looked=looked, saved=len(added), turned_away=len(result.get("rejected_leads") or []),
                     excluded=len(result.get("excluded") or []), held=int(result.get("held") or 0),
                     ai_checked=int(fit_check.get("by_ai") or 0))
        self._save(id, progress)
        return looked

    def _discovery(self, id, preset, count, focus, provider, model, *, free_only) -> dict:
        queued = self.runner.enqueue("discovery", None, provider, model, preset, count=count, focus=focus,
                                     free_only=free_only)
        deadline = self.clock() + PASS_TIMEOUT_MINUTES * 60
        while self.clock() < deadline:
            with self.w.connect() as db:
                row = db.execute("SELECT id, state, result, error FROM agent_runs WHERE id=?", (queued["id"],)).fetchone()
            if row and row["state"] == "completed":
                return {"id": row["id"], "result": json.loads(row["result"] or "{}")}
            if row and row["state"] == "failed":
                raise ValueError(row["error"] or "The pass failed without a reason.")
            if self._stop_requested(id):
                raise Stopped()
            self.sleep(self.poll)
        raise ValueError(f"This pass did not finish within {PASS_TIMEOUT_MINUTES} minutes.")

    # ----- preparing what was found --------------------------------------------------------

    def _prepare(self, id, config, progress) -> None:
        from backend.ai import limits
        from backend.services.pipeline import STEP, STEP_IDS

        steps = [step for step in STEP_IDS if config["steps"].get(step)]
        saved = progress["saved"]
        if not steps or not saved:
            progress["prepare"] = {"state": "skipped", "jobs": []}
            return
        jobs = [{"id": job["id"], "company": job["company"], "title": job["title"],
                 "steps": {step: {"state": "waiting"} for step in steps}} for job in saved]
        progress["prepare"] = {"state": "running", "jobs": jobs}
        done_steps = progress.get("done_steps") or {}
        for number, job in enumerate(jobs, 1):
            done_before = set(done_steps.get(job["id"]) or [])
            try:
                self.pipeline.studio.open(job["id"])
            except Exception as exc:  # noqa: BLE001
                for step in steps:
                    job["steps"][step] = {"state": "failed", "error": "Could not open this job's resume: " + str(exc)[:300]}
                self._save(id, progress)
                continue
            for step in steps:
                if self._stop_requested(id):
                    raise Stopped()
                if step in done_before:
                    job["steps"][step] = {"state": "done", "note": "Done before the app restarted."}
                    continue
                if self.clock() >= progress["deadline_epoch"]:
                    job["steps"][step] = {"state": "skipped", "note": "The hunt's time ran out."}
                    continue
                progress["stage"] = f"{STEP[step]['label']} for {job['company']} (job {number} of {len(jobs)})"
                job["steps"][step] = {"state": "running"}
                self._save(id, progress)
                started = self.clock()
                for attempt in (1, 2):
                    try:
                        done = self.pipeline.run_step(step, job["id"], config)
                        job["steps"][step] = {"state": "done", "seconds": round(self.clock() - started, 1), **done}
                        break
                    except Exception as exc:  # noqa: BLE001
                        message = str(exc)
                        job["steps"][step] = {"state": "failed", "seconds": round(self.clock() - started, 1),
                                              "error": message[:600]}
                        # A usage limit mid-step: wait for the reset (inside the hunt's time) and try once more.
                        if attempt == 1 and STEP[step]["ai"] and limits.is_limit(message):
                            ready, wake, why = self._ai_state(config)
                            if not ready and wake and wake < progress["deadline_epoch"]:
                                progress["waiting"] = {"until": _iso(wake), "until_text": _clock_text(wake), "why": why}
                                progress["stage"] = f"Waiting for an AI plan before {STEP[step]['label'].lower()}: {why}"
                                self._save(id, progress, "waiting")
                                self._nap(id, max(0.0, wake - self.clock()) + 30)
                                progress["waiting"] = None
                                continue
                        # A dropped connection or a timeout: pause, then try once more.
                        if attempt == 1 and TRANSIENT.search(message) and not limits.is_limit(message) \
                                and self.clock() + TRANSIENT_PAUSE_SECONDS < progress["deadline_epoch"]:
                            progress["stage"] = (f"{STEP[step]['label']} for {job['company']} did not finish "
                                                 "(the AI app did not answer); trying again in a minute")
                            self._save(id, progress)
                            self._nap(id, TRANSIENT_PAUSE_SECONDS)
                            continue
                        break
                self._save(id, progress)
        progress["prepare"]["state"] = "done"

    # ----- the morning report -------------------------------------------------------------------

    def _report(self, config, progress, state, error) -> str:
        from career import atomic_write

        today = self.s.today()
        folder = self.w.daily_dir / today
        folder.mkdir(parents=True, exist_ok=True)
        with self.w.connect() as db:
            memory = search_memory.summary(db, since=_iso(progress.get("started_epoch") or self.clock()))
            held = search_memory.held_count(db)
        lines = [f"# Hunt report — {today}", ""]
        saved = progress.get("saved") or []
        lines.append(f"**{len(saved)} of {config['target']} jobs saved** at a fit bar of {config['min_fit']} "
                     f"({state}{': ' + error if error else ''}).")
        if progress.get("exhausted"):
            lines.append("Every source was searched and nothing new passed the checks, so the hunt ended early.")
        if progress.get("resumed_from"):
            lines.append("The app stopped during the night; the hunt carried on by itself with the jobs already saved.")
        if config.get("no_ai"):
            lines.append("No AI app was ready, so the hunt read the job boards and employer feeds only; what it found "
                         "waits for the AI requirement check. Sign in to Kimi Code, Codex or Claude Code to finish it.")
        lines.append("")
        prepared = {job["id"]: job for job in (progress.get("prepare") or {}).get("jobs") or []}
        for job in saved:
            row = {}
            try:
                row = self.w.get_job(job["id"])
            except ValueError:
                pass
            lines.append(f"## {job['company']} — {job['title']}")
            lines.append(f"- Location: {job.get('location') or row.get('location', '')} · Fit: {row.get('fit_score') or job.get('fit')}"
                         f" · Found by: {job.get('pass')}")
            if row.get("fit_rationale"):
                lines.append(f"- Why it fits: {row['fit_rationale']}")
            if job.get("url"):
                lines.append(f"- Apply: {job['url']}")
            steps = (prepared.get(job["id"]) or {}).get("steps") or {}
            done = [name for name, step in steps.items() if step.get("state") == "done"]
            problems = [f"{name}: {step.get('error') or step.get('note')}" for name, step in steps.items()
                        if step.get("state") in ("failed", "skipped")]
            if done:
                lines.append("- Prepared: " + ", ".join(done))
            for problem in problems:
                lines.append(f"- ⚠ {problem}")
            lines.append("")
        lines += ["## Where it looked", ""]
        for entry in progress.get("passes") or []:
            detail = entry.get("error") or entry.get("note") or (
                f"looked at {entry.get('looked', 0)}, saved {entry.get('saved', 0)}, turned away {entry.get('turned_away', 0)}"
                + (f", held {entry['held']}" if entry.get("held") else "")
                + (f", AI-checked {entry['ai_checked']}" if entry.get("ai_checked") else ""))
            lines.append(f"- {entry['label']} (cycle {entry.get('cycle')}): {entry['state']} — {detail}")
        stages = memory.get("rejected_by_stage") or {}
        if stages:
            lines += ["", "Turned away by: " + ", ".join(f"{stage or 'other'} {count}" for stage, count in
                                                         sorted(stages.items(), key=lambda item: -item[1]))]
        if held:
            lines += ["", f"{held} plausible postings are still waiting for an AI requirement check; the next hunt checks them first."]
        lines += ["", "Nothing was submitted and no one was contacted. Review each job and its resume, then apply "
                      "through the posting link.", ""]
        path = folder / "HUNT-REPORT.md"
        atomic_write(path, "\n".join(lines))
        return str(path.relative_to(self.w.root))
