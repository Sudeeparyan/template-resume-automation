"""One chat for the whole workspace, run by an agent with every feature as a tool.

Two kinds of message, in this order:

1. The deterministic paths, which need no model and never guess: a pasted job
   description (with its link) goes through the sponsorship gate and
   duplicate checks, opens the Resume Studio draft, fits the active page contract and
   scores it; a posting link is fetched first; a handful of exact shortcuts
   ("find jobs", "status", "applied to X", "open X", "excluded") map straight
   to a service call, and "applied" always waits for the candidate's confirmation.

2. Everything else goes to the agent loop. The `workspace_agent` specialist
   sees the workspace snapshot, the tool catalogue (services/assistant_tools.py)
   and the task so far, and returns one decision per turn: call a tool, ask
   the candidate something, or reply. The loop runs the tool, records it as a step the
   page shows live, appends the result and asks again, until the agent replies
   or the turn budget runs out. A tool marked `confirm` (marking applied,
   changing the profile or goals, removing a job) pauses the loop until the candidate
   says yes; the answer resumes the same task.

The model runs on whatever is ready on this computer — Claude Code, Codex or a
keyed provider — through the same tier preferences as the other specialists.
Work happens on one worker thread; the page polls the message row.

Messages belong to conversations. "New chat" starts a fresh one (the old
thread stays in the history and can be reopened or deleted); the agent's
memory of recent exchanges is scoped to the open conversation. A reply in
progress can be stopped: the worker ends it at the next step boundary and
says what it had already changed.
"""

from __future__ import annotations

import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from backend.services.assistant_tools import Toolbox, brief_job, phrase_gaps, resume_card  # noqa: F401 - phrase_gaps is re-exported for tests

URL = re.compile(r"https?://[^\s<>()\"'\]]+")
JD_MARKERS = re.compile(
    r"\b(responsibilit\w*|qualifications?|requirements?|what you(?:'|’)?ll (?:do|bring)|what you will do|"
    r"about (?:the|this) (?:role|job|team|position|opportunity)|we(?:'|’)?re looking for|we are looking for|"
    r"you will|experience (?:with|in)|bachelor(?:'|’)?s?|master(?:'|’)?s?|degree|preferred|nice to have|"
    r"must have|minimum qualifications|basic qualifications|salary|compensation|benefits|"
    r"equal opportunity|job description|skills|apply)\b",
    re.I,
)
ROLE_WORDS = re.compile(
    r"\b(engineer(?:ing)?|developer|analyst|scientist|intern(?:ship)?|programmer|specialist|associate|"
    r"researcher|consultant|technician|sde|swe|data|software|embedded|automation|machine learning|ml|ai)\b",
    re.I,
)
LABELLED = {
    "company": re.compile(r"^(?:company|employer|organization|organisation)\s*[:\-–]\s*(.+?)\s*$", re.I | re.M),
    "title": re.compile(r"^(?:job title|title|role|position)\s*[:\-–]\s*(.+?)\s*$", re.I | re.M),
    "location": re.compile(r"^(?:location|based in|office|work location)\s*[:\-–]\s*(.+?)\s*$", re.I | re.M),
}
NAMED_AFTER = re.compile(r"\b(?i:at|join|joining|about)\s+([A-Z][\w&.'’-]+(?:\s+[A-Z][\w&.'’-]+){0,2})(?=\s*(?:$|[.,!;:)]|\n))", re.M)
STOP_WORDS = {"the", "our", "a", "an", "this", "that", "you", "your", "we", "us", "least", "home", "work", "scale", "all", "every",
              "python", "java", "sql", "aws", "azure", "gcp", "kafka", "flink", "airflow", "spark", "linux", "git"}
ABOUT_COMPANY = re.compile(r"^about\s+(?!the\b|this\b|us\b|you\b)([A-Z][\w&.'’\- ]{1,60}?)\s*:?\s*$", re.M)
LOCATION_LINE = re.compile(
    r"^(?:remote(?: \(us\))?|hybrid|[A-Z][\w.' ]+,\s*[A-Z]{2}(?:\s*\(?(?:remote|hybrid|on-?site)\)?)?)\s*$", re.I | re.M
)
HUNT_START = re.compile(
    r"(?:start |run |begin )?(?:the |an? )?(?:overnight|night|all[- ]night)(?: job)? (?:hunt|search)(?: tonight| now)?\.?"
    r"|(?:hunt|search|look) (?:for jobs )?(?:overnight|all night|tonight)\.?", re.I)
HUNT_STATUS = re.compile(r"(?:the )?(?:overnight |night )?hunt (?:status|progress)\??|how is the (?:overnight )?hunt going\??", re.I)
MORNING = re.compile(r"(?:show |show me |give me |what are |open )?(?:my |the )?"
                     r"(?:today(?:'|’)?s|this morning(?:'|’)?s|morning) (?:jobs?|job list|list)\??\.?", re.I)
YES = re.compile(r"(yes|y|yes please|confirm|correct|do it|ok|okay|sure|go ahead|proceed)\.?!?", re.I)
NO = re.compile(r"(no|n|nope|not yet|skip|cancel|don'?t)\.?", re.I)
CONJUNCTION = re.compile(r"\b(and|then|also)\b", re.I)

HELP = (
    "Paste a job description here (with its link) and I run the sponsorship gate, save it, build the "
    "resume to this profile's page contract and hand you the PDF.\n\n"
    "Ask for anything else in plain words and I do it with the same tools the tabs use, for example:\n"
    "- *research Snowflake and then write the study plan*\n"
    "- *which saved jobs still have no resume? build them*\n"
    "- *I finished the AWS Data Engineer course* (goes to your Profile for review)\n"
    "- *change the Acme resume summary to lead with streaming pipelines*\n"
    "- *what did I apply to this week?* · *set my weekly target to 12*\n"
    "- *hunt overnight for 15 data analyst jobs with fit 75+* (keeps searching boards, employer feeds and the web "
    "until it has them, waiting out AI usage limits, then builds each resume)\n"
    "- *also search for insights analyst roles* · *track Stripe's careers page* (changes where the searches look)\n\n"
    "Anything hard to undo — marking a job applied, changing your profile or goals, removing a job — "
    "waits for your *yes*. Nothing is ever submitted for you.\n"
    "Shortcuts: **today's jobs** · **find jobs** · **overnight hunt** · **hunt status** · **status** · **open <company>** · "
    "**applied to <company> on YYYY-MM-DD** · **excluded**"
)

STEP_ORDER = ("Reading the posting", "Work-permit and duplicate checks", "Opening the draft",
              "Fitting the resume page contract", "Scoring against the posting")
STEP_AGENTS = ("assistant", "sponsorship", "resume", "resume", "resume_match")

# The fields of one job's document card; a reply about several jobs shows a row each instead.
CARD_FIELDS = ("pdf", "preview_png", "job_id", "company", "title", "tier", "revision", "coverage", "ats",
               "gaps", "posting_url", "page", "signature_project", "warnings")
CARD_ROW_FIELDS = ("job_id", "company", "title", "tier", "revision", "pdf", "coverage", "ats", "posting_url")

# A conversation is named after its first message, cut to this many characters.
TITLE_CHARS = 60
STOPPED_REPLY = "Stopped. Anything a finished step already saved stays; nothing after it was changed."


class Stopped(Exception):
    """The candidate pressed Stop: the worker ends the reply at the next step boundary."""


# The agent loop: how many model decisions one message may take, how much of a
# tool result the model sees, and how many results stay whole in the transcript.
MAX_TURNS = 14
RESULT_CHARS = 3500
FULL_RESULTS_KEPT = 8
# Read-only tools one decision may add beside its main call ("get_job for these three"),
# so a question that needs several lookups costs one model round trip, not one each.
MAX_EXTRA_CALLS = 5


def looks_like_posting(text: str) -> bool:
    """A pasted description: long, and it talks the way postings talk."""
    if re.match(r"^\s*(?:jd|job|posting|job description)\s*:", text, re.I):
        return True
    if len(text) < 300:
        return False
    return len(set(m.group(0).casefold() for m in JD_MARKERS.finditer(text))) >= 3


def parse_posting_fields(text: str, labelled_only: bool = False) -> dict:
    """Cheap, no-AI extraction: labelled lines, an 'About X' heading, a title-shaped first line."""
    found = {}
    for key, pattern in LABELLED.items():
        match = pattern.search(text)
        if match and len(match[1]) <= 120:
            found[key] = match[1].strip()
    if labelled_only:
        return found
    if "company" not in found:
        about = ABOUT_COMPANY.search(text)
        if about:
            found["company"] = about[1].strip()
    if "company" not in found:
        # "Build your future at Snowflake", "Join Acme Analytics": an employer named early and
        # repeated later. One mention is a guess; the chat asks rather than guessing.
        for candidate in NAMED_AFTER.findall(text[:800]):
            name = candidate.strip().rstrip(".,")
            if name and name.split()[0].casefold() not in STOP_WORDS and text.count(name) >= 2:
                found["company"] = name
                break
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if "title" not in found and lines:
        first = re.sub(r"^(?:jd|job|posting|job description)\s*:\s*", "", lines[0], flags=re.I)
        if 3 <= len(first) <= 90 and ROLE_WORDS.search(first) and not first.endswith((".", ":")) and not URL.search(first):
            found["title"] = first
    if "location" not in found:
        for line in lines[:12]:
            if len(line) <= 60 and LOCATION_LINE.match(line):
                found["location"] = line
                break
    link = URL.search(text)
    if link:
        found["url"] = link.group(0).rstrip(".,;:)")
    return found


def conversation_title(first_message: str | None) -> str:
    """The first line of the first message, cut short; a pasted posting reads as its heading."""
    text = " ".join((first_message or "").strip().split("\n")[0].split())
    text = re.sub(r"^\s*(?:jd|job|posting|job description)\s*:\s*", "", text, flags=re.I)
    if not text:
        return "New chat"
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 1].rstrip() + "…"


def _strip_link(text: str) -> str:
    return URL.sub("", text).strip()


def _trim(value, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"… (+{len(text) - limit:,} chars)"


class Assistant:
    def __init__(self, service, studio, runner, quality=None, background=True, tools=None):
        from backend.job_quality import JobQualityService

        self.s, self.w, self.studio, self.runner = service, service.w, studio, runner
        self.quality = quality or JobQualityService(service)
        self.tools = tools or Toolbox(service, studio, runner, self.quality)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="career-assistant") if background else None
        # Message IDs whose reply the candidate asked to stop; check at each step.
        self.stopping: set[str] = set()
        with self.w.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS assistant_messages(id TEXT PRIMARY KEY, message TEXT NOT NULL, response TEXT NOT NULL, state TEXT NOT NULL, steps TEXT NOT NULL DEFAULT '[]', data TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            ''')
            if "conversation_id" not in {r[1] for r in db.execute("PRAGMA table_info(assistant_messages)")}:
                db.execute("ALTER TABLE assistant_messages ADD COLUMN conversation_id TEXT")
            db.execute("UPDATE assistant_messages SET state='failed',response=?,updated_at=? WHERE state='processing'",
                       ("The app stopped before this finished. Send it again.", self.s.now()))
            # Messages from before conversations existed join the open one.
            db.execute("UPDATE assistant_messages SET conversation_id=? WHERE conversation_id IS NULL", (self.conversation_id(db),))

    # ---- Storage -------------------------------------------------------------
    @staticmethod
    def _decode(row) -> dict:
        return {**dict(row), "steps": json.loads(row["steps"]), "data": json.loads(row["data"])}

    def get(self, id: str) -> dict:
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM assistant_messages WHERE id=?", (id,)).fetchone()
        if not row:
            raise ValueError("Message not found")
        return self._decode(row)

    def history(self, limit: int = 60, conversation_id: str | None = None) -> list:
        """The open conversation (or the named one), oldest first."""
        with self.w.connect() as db:
            cid = conversation_id or self.conversation_id(db)
            rows = db.execute("SELECT * FROM assistant_messages WHERE conversation_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                              (cid, limit)).fetchall()
        return [self._decode(r) for r in reversed(rows)]

    # ---- Conversations -------------------------------------------------------
    def conversation_id(self, db=None) -> str:
        """The open conversation; the first call on a workspace creates it."""
        if db is None:
            with self.w.connect() as db:
                return self.conversation_id(db)
        row = db.execute("SELECT value FROM preferences WHERE key='assistant_conversation'").fetchone()
        current = json.loads(row[0]) if row else None
        if not current:
            current = uuid.uuid4().hex
            self.s.set_pref("assistant_conversation", current, db)
        return current

    def conversations(self) -> list:
        """Every conversation with messages, newest activity first; the open one is flagged."""
        with self.w.connect() as db:
            current = self.conversation_id(db)
            rows = db.execute(
                "SELECT conversation_id AS id, COUNT(*) AS count, MIN(created_at) AS started_at, MAX(updated_at) AS updated_at, "
                "(SELECT message FROM assistant_messages m2 WHERE m2.conversation_id=m.conversation_id ORDER BY created_at, rowid LIMIT 1) AS first, "
                "SUM(state='processing') AS busy FROM assistant_messages m GROUP BY conversation_id ORDER BY updated_at DESC, MAX(rowid) DESC").fetchall()
        return [{"id": r["id"], "title": conversation_title(r["first"]), "count": r["count"], "started_at": r["started_at"],
                 "updated_at": r["updated_at"], "busy": bool(r["busy"]), "current": r["id"] == current} for r in rows]

    def new_conversation(self) -> str:
        """Start a fresh thread. An open conversation with nothing in it is reused, so
        pressing New chat twice never leaves empty threads behind; the question the
        old thread was waiting on is dropped with it."""
        with self.w.connect() as db:
            current = self.conversation_id(db)
            if db.execute("SELECT 1 FROM assistant_messages WHERE conversation_id=? AND state='processing'", (current,)).fetchone():
                raise ValueError("A reply is still being worked on. Stop it or wait for it before starting a new chat.")
            if not db.execute("SELECT 1 FROM assistant_messages WHERE conversation_id=?", (current,)).fetchone():
                self.s.set_pref("assistant_pending", None, db)
                return current
            fresh = uuid.uuid4().hex
            self.s.set_pref("assistant_conversation", fresh, db)
            self.s.set_pref("assistant_pending", None, db)
            self.w.record_event(db, "assistant_conversation_started", None, conversation_id=fresh)
        return fresh

    def open_conversation(self, conversation_id: str) -> str:
        """Make an earlier thread the open one; the question the current thread was waiting on is dropped."""
        with self.w.connect() as db:
            if conversation_id == self.conversation_id(db):
                return conversation_id
            if not db.execute("SELECT 1 FROM assistant_messages WHERE conversation_id=?", (conversation_id,)).fetchone():
                raise ValueError("Conversation not found")
            if db.execute("SELECT 1 FROM assistant_messages WHERE state='processing'").fetchone():
                raise ValueError("A reply is still being worked on. Stop it or wait for it before switching chats.")
            self.s.set_pref("assistant_conversation", conversation_id, db)
            self.s.set_pref("assistant_pending", None, db)
        return conversation_id

    def delete_conversation(self, conversation_id: str) -> dict:
        """Delete a thread and its messages for good. The jobs, resumes and profile changes
        it produced are not touched: they live in the workspace, not in the chat."""
        with self.w.connect() as db:
            if db.execute("SELECT 1 FROM assistant_messages WHERE conversation_id=? AND state='processing'", (conversation_id,)).fetchone():
                raise ValueError("A reply in this chat is still being worked on. Stop it first.")
            deleted = db.execute("DELETE FROM assistant_messages WHERE conversation_id=?", (conversation_id,)).rowcount
            if conversation_id == self.conversation_id(db):
                self.s.set_pref("assistant_pending", None, db)
                latest = db.execute("SELECT conversation_id FROM assistant_messages ORDER BY updated_at DESC, rowid DESC LIMIT 1").fetchone()
                self.s.set_pref("assistant_conversation", latest[0] if latest else uuid.uuid4().hex, db)
            self.w.record_event(db, "assistant_conversation_deleted", None, conversation_id=conversation_id, messages=deleted)
        return {"deleted": deleted, "conversation_id": self.conversation_id()}

    def clear_history(self) -> dict:
        """Delete every conversation and its messages for good, and open a fresh one. As with a
        single delete, the jobs, resumes and profile changes the chats produced are not touched."""
        with self.w.connect() as db:
            if db.execute("SELECT 1 FROM assistant_messages WHERE state='processing'").fetchone():
                raise ValueError("A reply is still being worked on. Stop it or wait for it before clearing the history.")
            chats = db.execute("SELECT COUNT(DISTINCT conversation_id) FROM assistant_messages").fetchone()[0]
            deleted = db.execute("DELETE FROM assistant_messages").rowcount
            self.s.set_pref("assistant_pending", None, db)
            self.s.set_pref("assistant_conversation", uuid.uuid4().hex, db)
            self.w.record_event(db, "assistant_history_cleared", None, conversations=chats, messages=deleted)
        return {"deleted": deleted, "conversations": chats, "conversation_id": self.conversation_id()}

    def stop(self, id: str) -> dict:
        """End a reply in progress. The worker notices at its next step and leaves the message
        failed with what it had already done; a step that is mid-flight (a compile, one model
        call) finishes first, so the page may show "Stopping…" for a few seconds."""
        row = self.get(id)
        if row["state"] != "processing":
            return row
        self.stopping.add(id)
        steps = row["steps"]
        for step in steps:
            if step["state"] == "running":
                step["detail"] = "Stopping…"
        self._write(id, steps=steps)
        self.s.set_pref("assistant_pending", None)
        return self.get(id)

    def auto_apply(self, enabled: bool | None = None) -> bool:
        """Per-conversation: may confirmation-gated tools run without the yes/no pause?

        Default is off. The flag lives with the conversation so an old thread
        never inherits a blanket permission given in a new one.
        """
        with self.w.connect() as db:
            cid = self.conversation_id(db)
            flags = self.s.pref("assistant_auto_apply", {}) or {}
            if enabled is None:
                return bool(flags.get(cid))
            flags[cid] = bool(enabled)
            self.s.set_pref("assistant_auto_apply", flags, db)
            self.w.record_event(db, "assistant_auto_apply_set", None, conversation_id=cid, enabled=bool(enabled))
        return bool(enabled)

    def agents(self) -> list:
        """The agent registry with what the chat can reach: every tool names the agent it runs on."""
        from backend.services.workspace_v2 import agents_for

        reach: dict[str, list] = {}
        for tool in self.tools.tools.values():
            reach.setdefault(tool.agent, []).append(tool.label)
        # The gates and Studio run inside the paste path; the runnable agents (and the hiring
        # review and profile comparison a research run chains) start through run_agent.
        from backend.services.assistant_tools import RUNNABLE_AGENTS

        for agent in STEP_AGENTS + ("reapply", "hiring", "match") + tuple(RUNNABLE_AGENTS):
            reach.setdefault("resume" if agent == "resume_build" else agent, [])
        return [{"id": a["id"], "name": a["name"], "does": a["does"], "implementation": a["implementation"],
                 "linked": a["id"] in reach, "tools": reach.get(a["id"], [])} for a in agents_for(self.w.root)]

    def overview(self) -> dict:
        from backend.ai import any_provider_configured, engine

        messages = self.history()
        return {
            "conversation_id": self.conversation_id(),
            "conversations": self.conversations(),
            "messages": messages,
            "pending": self.s.pref("assistant_pending"),
            "busy": any(m["state"] == "processing" for m in messages),
            "auto_apply": self.auto_apply(),
            "ai_configured": any_provider_configured(self.w.root),
            "engine": engine(self.s),
            "agents": self.agents(),
            "capabilities": self.tools.capabilities(),
        }

    def _write(self, id: str, *, state=None, response=None, steps=None, data=None):
        sets, values = ["updated_at=?"], [self.s.now()]
        for column, value in (("state", state), ("response", response)):
            if value is not None:
                sets.append(column + "=?")
                values.append(value)
        for column, value in (("steps", steps), ("data", data)):
            if value is not None:
                sets.append(column + "=?")
                values.append(json.dumps(value, ensure_ascii=False, default=str))
        with self.w.connect() as db:
            db.execute("UPDATE assistant_messages SET " + ",".join(sets) + " WHERE id=?", (*values, id))

    # ---- Entry point ---------------------------------------------------------
    def send(self, message: str, request_id: str | None = None) -> dict:
        message = message.strip()
        if not message or len(message) > 120_000:
            raise ValueError("Enter 1–120,000 characters")
        key = request_id or uuid.uuid4().hex
        if len(key) > 100:
            raise ValueError("Invalid message ID")
        stamp = self.s.now()
        with self.w.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM assistant_messages WHERE id=?", (key,)).fetchone()
            if old:
                if old["message"] != message:
                    raise ValueError("Message ID already belongs to another request")
                return self._decode(old)
            db.execute("INSERT INTO assistant_messages(id,message,response,state,steps,data,created_at,updated_at,conversation_id) VALUES(?,?,?,?,?,?,?,?,?)",
                       (key, message, "Working on it.", "processing", "[]", "{}", stamp, stamp, self.conversation_id(db)))
        if self.pool:
            self.pool.submit(self._process, key)
        else:
            self._process(key)
        return self.get(key)

    def _process(self, id: str) -> None:
        row = self.get(id)
        try:
            state, response, data = self._handle(id, row["message"])
        except Stopped:
            state, response, data = "failed", STOPPED_REPLY, {"intent": "stopped"}
        except ValueError as error:
            state, response, data = "failed", str(error), {}
        except Exception as error:  # noqa: BLE001 - the row must never stay "processing"
            state, response, data = "failed", "Something went wrong: " + type(error).__name__ + ": " + str(error)[:500], {}
        self.stopping.discard(id)
        steps = self.get(id)["steps"]
        for step in steps:
            if step["state"] == "running":
                step["state"] = "failed" if state == "failed" else "done"
                if data.get("intent") == "stopped":
                    step["detail"] = "Stopped before it finished"
        if data.get("intent") == "stopped":
            steps.append({"at": self.s.now(), "label": "Stopped", "state": "failed", "detail": "Nothing after this was changed", "agent": "assistant"})
        self._write(id, state=state, response=response, steps=steps, data=data)
        with self.w.connect() as db:
            self.w.record_event(db, "assistant_replied", data.get("job_id") if isinstance(data, dict) else None,
                                message_id=id, state=state, intent=(data or {}).get("intent"))
        try:
            self.s.sync_projections()
        except Exception:  # noqa: BLE001 - projections are a convenience; the reply is already saved
            pass

    # ---- Steps ---------------------------------------------------------------
    def _check_stop(self, id: str) -> None:
        if id in self.stopping:
            raise Stopped()

    def _default_location(self) -> str:
        """Where a pasted posting is assumed to be when it names no place: the profile's country."""
        from backend.countries import pack_for

        pack = pack_for(self.w.root)
        return pack.text("default_location") or pack.name

    def _resume_shape(self) -> str:
        """A human-readable page shape from the active profile contract and country."""
        from backend.ai.persona import persona_for

        persona = persona_for(self.w.root)
        return persona["resume_shape"] if persona else "the active resume contract"

    def _application_follow_up(self) -> str:
        from backend.services.reapply import settings

        rules = settings(self.w.profile())
        if rules["auto_ghost"]:
            return f" If they go quiet for {rules['ghost_after_days']} days it will show as ghosted; a reply or status change takes it out."
        return " Silence does not change its status; only your confirmation or verified matching evidence does."

    def _step(self, id: str, label: str, state: str = "running", detail: str = "", agent: str = "assistant", **extra) -> None:
        self._check_stop(id)
        steps = self.get(id)["steps"]
        for step in steps:
            if step["state"] == "running":
                step["state"] = "done"
        steps.append({"at": self.s.now(), "label": label, "state": state, "detail": detail, "agent": agent, **extra})
        self._write(id, steps=steps)

    def _finish_step(self, id: str, state: str = "done", detail: str | None = None, **extra) -> None:
        steps = self.get(id)["steps"]
        if steps:
            steps[-1]["state"] = state
            if detail is not None:
                steps[-1]["detail"] = detail
            steps[-1].update(extra)
        self._write(id, steps=steps)

    def _relabel_step(self, id: str, label: str, detail: str = "", agent: str | None = None) -> None:
        steps = self.get(id)["steps"]
        if steps and steps[-1]["state"] == "running":
            steps[-1].update({"label": label, "detail": detail, **({"agent": agent} if agent else {})})
            self._write(id, steps=steps)

    # ---- Routing -------------------------------------------------------------
    def _handle(self, id: str, message: str):
        text = message.strip()
        low = text.casefold()
        if re.fullmatch(r"(help|what can you do|\?)\??", low):
            return "done", HELP, {"intent": "help"}
        if re.fullmatch(r"(cancel|never ?mind|stop|forget it|start over)\.?", low):
            self.s.set_pref("assistant_pending", None)
            return "done", "Cleared. Paste a posting or ask me anything.", {"intent": "cancel"}

        pending = self.s.pref("assistant_pending")
        if pending:
            resolved = self._resume_pending(id, pending, text)
            if resolved is not None:
                return resolved

        if looks_like_posting(text):
            return self._prepare_posting(id, text)
        lone = URL.fullmatch(text.rstrip(".,;)")) or (URL.search(text) and len(_strip_link(text)) < 40)
        if lone:
            return self._from_link(id, URL.search(text).group(0).rstrip(".,;:)"))

        # Exact shortcuts: instant, no model, and "applied" waits for confirmation.
        # A request with "and"/"then" in it is a task for the agent, not a shortcut.
        if HUNT_START.fullmatch(low):
            return self._start_hunt(id)
        if HUNT_STATUS.fullmatch(low):
            result = self.tools.hunt_status() if self.tools.hunt else {"summary": "The overnight hunt is not available here."}
            return "done", self._hunt_text(result), {"intent": "hunt_status", "suggestions": ["stop the hunt"]}
        if MORNING.fullmatch(low):
            return "done", self._morning_text(self.tools.morning_list()), {"intent": "morning_list",
                                                                           "suggestions": ["hunt status", "status"]}
        if re.fullmatch(r"(find|search|look for|discover|hunt)( me)?( some| new| more)? (jobs?|roles?|postings?|openings?)( for (me|my profile|today))?\.?", low) \
                or re.fullmatch(r"(daily search|run (the )?search|search)\.?", low):
            return self._discover(id)
        if re.fullmatch(r"(status|summary|progress|pipeline|where (are|am) (we|i)|what(’|')?s (next|new|going on)|how am i doing)\??\.?", low):
            return "done", self._status(), {"intent": "status", "suggestions": ["find jobs", "which saved jobs still have no resume? build them"]}
        if re.fullmatch(r"(excluded( roles| postings)?|what was excluded)\??", low):
            return "done", self._excluded(), {"intent": "excluded"}
        match = re.fullmatch(r"(?:write |make |create )?(?:a |the )?study[- ]plan(?: for)?\s+([^,;]+?)\.?", text, re.I)
        if match and not CONJUNCTION.search(match[1]):
            return self._run_for_job(id, "study_plan", match[1])
        match = re.fullmatch(r"(?:run |do )?(?:company )?(?:research|hiring review|company & hiring review)(?: for| on)?\s+([^,;]+?)\.?", text, re.I)
        if match and not CONJUNCTION.search(match[1]):
            return self._run_for_job(id, "research", match[1])
        match = re.fullmatch(
            r"(?:i (?:have |just |’ve |'ve )?)?(?:applied|submitted(?: my application)?|sent(?: my application| it)?)"
            r"(?: to| for| at)?\s+([^,;]+?)(?:\s+(?:on|today|yesterday)\b\s*(.*?))?\.?",
            text, re.I)
        if match and not CONJUNCTION.search(match[1]):
            when = (match[2] or "").strip()
            phrase = match[0].casefold()
            if "today" in phrase and not when:
                when = self.s.today()
            elif "yesterday" in phrase and not when:
                when = (date.fromisoformat(self.s.today()) - timedelta(days=1)).isoformat()
            return self._propose_applied(id, match[1], when)
        match = re.fullmatch(r"(?:open|show|show me|go to|resume for|the resume for)\s+([^,;]+?)\.?", text, re.I)
        if match and not re.search(r"\b(and|then|also|jobs?|resumes?|profile|questions?|runs?|agents?|status|pending|entries)\b", match[1], re.I):
            return self._open(id, match[1])
        return self._agent(id, text)

    # ---- Pending questions ---------------------------------------------------
    def _resume_pending(self, id, pending, text):
        """Continue a flow that stopped to ask something. None means the message is unrelated."""
        kind = pending.get("kind")
        link = URL.search(text)
        if kind == "posting_link":
            if link and len(_strip_link(text)) < 200:
                self.s.set_pref("assistant_pending", None)
                return self._prepare_posting(id, pending["description"], url=link.group(0).rstrip(".,;:)"), fields=pending.get("fields"))
            if looks_like_posting(text):
                return None  # a new posting replaces the old question
            return "needs_input", "I still need the link to that posting so it can be saved and never applied to twice. Paste the URL, or say *cancel*.", {"intent": "posting_link"}
        if kind == "posting_fields":
            parts = [p.strip() for p in re.split(r"\s*[|;]\s*|\s*\n\s*", text) if p.strip()]
            fields = dict(pending.get("fields") or {})
            labelled = parse_posting_fields(text, labelled_only=True)
            for key in ("company", "title", "location"):
                if labelled.get(key):
                    fields[key] = labelled[key]
            missing = [k for k in ("company", "title", "location") if not fields.get(k)]
            if not any(labelled.get(k) for k in ("company", "title", "location")) and len(parts) >= 1 and len(parts) <= 3 and not looks_like_posting(text):
                for key, value in zip(missing, parts):
                    fields[key] = value
            if all(fields.get(k) for k in ("company", "title")):
                self.s.set_pref("assistant_pending", None)
                fields.setdefault("location", self._default_location())
                return self._prepare_posting(id, pending["description"], url=pending.get("url"), fields=fields)
            if looks_like_posting(text):
                return None
            return "needs_input", "Reply with **Company | Job title | Location** for that posting (for example *Snowflake | Software Engineer | Menlo Park, CA*), or say *cancel*.", {"intent": "posting_fields"}
        if kind == "confirm_applied":
            if YES.fullmatch(text):
                self.s.set_pref("assistant_pending", None)
                job = self.w.update_job(pending["job_id"], "applied", None, pending["date"])
                self.w.export_tracking()
                return "done", (f"Recorded: **{job['company']} — {job['title']}** applied on {pending['date']}. "
                                + self._application_follow_up()), \
                    {"intent": "mark_applied", "job_id": job["id"]}
            if NO.fullmatch(text):
                self.s.set_pref("assistant_pending", None)
                return "done", "Not recorded. Tell me when it is sent.", {"intent": "mark_applied_cancelled"}
            return None
        if kind == "choose_job":
            choice = re.fullmatch(r"\s*(\d{1,2})\s*\.?", text)
            candidates = pending.get("candidates") or []
            picked = None
            if choice and 1 <= int(choice[1]) <= len(candidates):
                picked = candidates[int(choice[1]) - 1]
            else:
                matches = [c for c in candidates if self._job_matches(c, text)]
                picked = matches[0] if len(matches) == 1 else None
            if picked:
                self.s.set_pref("assistant_pending", None)
                return self._dispatch_job_intent(id, pending["intent"], picked, pending.get("extra") or {})
            return None
        if kind == "agent":
            # The agent asked the candidate something; their answer continues the same task.
            self.s.set_pref("assistant_pending", None)
            if looks_like_posting(text):
                return None
            return self._agent(id, text, transcript=pending.get("transcript") or [])
        if kind == "confirm_tool":
            self.s.set_pref("assistant_pending", None)
            transcript = list(pending.get("transcript") or [])
            tool = self.tools.get(pending["tool"])
            arguments = pending.get("arguments") or {}
            if YES.fullmatch(text):
                self._step(id, tool.label, detail="Confirmed by the candidate", agent=tool.agent)
                transcript.append(self._run_tool(id, tool, arguments))
                return self._agent(id, None, transcript=transcript)
            if NO.fullmatch(text):
                transcript.append({"role": "tool", "tool": tool.name, "declined": True,
                                   "note": "The candidate said no. Do not run it; explain what was left undone and reply."})
                return self._agent(id, None, transcript=transcript)
            transcript.append({"role": "tool", "tool": tool.name, "declined": True,
                               "note": "The candidate did not confirm; their next message follows. Treat it as their answer."})
            return self._agent(id, text, transcript=transcript)
        return None

    # ---- Paste a posting -> resume --------------------------------------------
    def _from_link(self, id, url):
        self._step(id, "Fetching the posting page", agent="discovery")
        page = self.quality.fetcher(url)
        text = (page or {}).get("text") or ""
        if len(text) < 300:
            self._finish_step(id, "failed", "The page did not return readable text (status " + str((page or {}).get("status")) + ")")
            self.s.set_pref("assistant_pending", None)
            return "needs_input", ("I could not read that page (a login wall or a script-only page, most likely). "
                                   "Paste the job description text here and I will use this link for it."), \
                {"intent": "posting_link_unreadable", "url": url}
        self._finish_step(id, "done", f"{len(text):,} characters of page text")
        return self._prepare_posting(id, text[:100_000], url=url)

    def _extract_fields(self, id, text, url, fields):
        """Heuristics first (free), then the cheap parser for what is still missing, both grounded in the text."""
        from backend.ai import any_provider_configured, team_for

        found = {**parse_posting_fields(text), **{k: v for k, v in (fields or {}).items() if v}}
        if url:
            found["url"] = url
        missing = [k for k in ("company", "title", "location") if not found.get(k)]
        note = ""
        if missing and any_provider_configured(self.w.root):
            try:
                parsed = team_for(self.s).run("posting_parser", {"posting_text": text[:20_000]})
                lowered = text.casefold()
                for key in missing:
                    value = (getattr(parsed, key, "") or "").strip()
                    # Grounding: a value the text does not contain is a guess, and a guess is dropped.
                    if value and value.casefold() in lowered:
                        found[key] = value
                if not found.get("url") and parsed.url and parsed.url in text:
                    found["url"] = parsed.url
            except Exception as error:  # noqa: BLE001 - no key, no network, bad output: the chat asks instead
                note = "(The posting parser could not run: " + str(error).rstrip(".") + ".)"
        return found, note

    def _prepare_posting(self, id, text, url=None, fields=None):
        text = re.sub(r"^\s*(?:jd|job|posting|job description)\s*:\s*", "", text, flags=re.I).strip()
        self._step(id, STEP_ORDER[0], agent=STEP_AGENTS[0])
        found, note = self._extract_fields(id, text, url, fields)
        if not found.get("url"):
            self._finish_step(id, "done", "No link in the text")
            self.s.set_pref("assistant_pending", {"kind": "posting_link", "description": text, "fields": found, "asked_at": self.s.now()})
            return "needs_input", ("I have the description" + (f" for **{found['company']} — {found['title']}**" if found.get("company") and found.get("title") else "") +
                                   ". Paste the posting link too, so the job is saved under its own URL and never applied to twice."), \
                {"intent": "posting_link", "fields": found}
        if not found.get("company") or not found.get("title"):
            self._finish_step(id, "done", "Employer or title not stated in the text")
            self.s.set_pref("assistant_pending", {"kind": "posting_fields", "description": text, "url": found["url"], "fields": found, "asked_at": self.s.now()})
            known = ", ".join(f"{k}: {found[k]}" for k in ("company", "title", "location") if found.get(k))
            return "needs_input", ("I could not find the employer or the job title in the text" + (f" (I have {known})" if known else "") +
                                   ". Reply with **Company | Job title | Location**." + (" " + note if note else "")), \
                {"intent": "posting_fields", "fields": found}
        found.setdefault("location", self._default_location())
        self._finish_step(id, "done", f"{found['company']} — {found['title']} · {found['location']}")

        self._step(id, STEP_ORDER[1], agent=STEP_AGENTS[1])
        saved = self.tools.save_posting(found["company"], found["title"], found["url"], text, found["location"])
        if saved.get("excluded"):
            self._finish_step(id, "failed", saved["reason"])
            return "done", (f"Not saved. The posting says **“{saved['sentence']}”** — {saved['reason']}. "
                            "It is listed under Excluded roles on the Dashboard; if that reading is wrong, restore it there and paste it again."), \
                {"intent": "posting_excluded", "excluded_id": saved.get("excluded_id"), "sentence": saved["sentence"]}
        if saved.get("blocked"):
            self._finish_step(id, "failed", saved["rule"])
            return "done", "Not saved: " + saved["note"], {"intent": "posting_blocked", "rule": saved["rule"]}
        job = self.w.get_job(saved["job"]["id"])
        tier = job.get("sponsor_tier") or "C"
        tier_label = saved["job"]["sponsor_note"]
        self._finish_step(id, "done", saved["summary"])
        warnings = list(saved["warnings"])

        self._step(id, STEP_ORDER[2], agent=STEP_AGENTS[2])
        if self.s.profile_dirty():
            self._finish_step(id, "failed", "Profile has unreviewed edits")
            return "done", (f"**{job['company']} — {job['title']}** is saved (tier {tier}), but the Profile has unreviewed edits, "
                            "so no new draft can start until they are reconciled with the evidence registry. Open Profile, confirm the pending entries, then say *open "
                            + job["company"] + "* and I will build the resume."), \
                {"intent": "posting_saved_profile_dirty", "job_id": job["id"], "warnings": warnings,
                 "suggestions": ["show my pending profile entries", "open " + job["company"]]}
        draft = self.studio.open(job["id"])
        self._finish_step(id, "done", f"Draft version {draft['revision']} · signature project: {draft['fields'].get('SelectedProjectTitle') or '—'}")

        self._step(id, "Fitting " + self._resume_shape(), agent=STEP_AGENTS[3])
        fitted = self.studio.fit(job["id"], draft["revision"])
        card = resume_card(job, draft, fitted)
        page = card["page"]
        fill = page["fill_percent"] if page["fill_percent"] is not None else "?"
        self._finish_step(id, "done", f"{page['count'] or 1} page · {fill}% filled · {page['font_pt']}pt" + (" · cut: " + ", ".join(page["cuts"]) if page["cuts"] else ""))

        self._step(id, STEP_ORDER[4], agent=STEP_AGENTS[4])
        match = fitted.get("match") or {}
        self._finish_step(id, "done" if match else "failed",
                          (f"JD coverage {card['coverage']} · ATS readiness {card['ats']}" if match else "Score unavailable; the page is fitted and saved"))

        lines = [f"**{job['company']} — {job['title']}** is saved (sponsorship tier {tier}" + (f": {tier_label}" if tier_label else "") + ")."]
        signature = card["signature_project"] or "the signature project"
        lines.append(f"The resume is fitted to {self._resume_shape()} at {page['font_pt']}pt ({fill}% filled), "
                     f"leading with **{signature}**" + (f", after cutting the {' and the '.join(page['cuts'])}." if page["cuts"] else "."))
        if match:
            lines.append(f"JD term coverage {card['coverage']} · ATS readiness {card['ats']}." + (f" Requirements the evidence does not cover: {', '.join(card['gaps'])}." if card["gaps"] else ""))
        for warning in warnings:
            lines.append("⚠ " + warning)
        lines.append("Download the PDF, look at the page once, apply through the posting link, then tell me *applied to " + job["company"] + "*. "
                     "A visual review is still pending on the Studio side; nothing here has been submitted.")
        data = {**card, "warnings": warnings + card["warnings"], "duplicate": bool(saved.get("duplicate"))}
        return "done", "\n".join(lines), data

    # ---- Shortcuts ---------------------------------------------------------------
    def _discover(self, id):
        preset = (self.s.pref("discovery_preferences", {}) or {}).get("preset", "default")
        self._step(id, "Starting job discovery (" + preset + ")", agent="discovery")
        try:
            run = self.runner.enqueue("discovery", None, *self.tools._engine_for("discovery"), preset)
        except ValueError as error:
            self._finish_step(id, "failed", str(error))
            return "done", str(error), {"intent": "discovery_refused"}
        self._finish_step(id, "done", "Run " + run["id"][:8] + (" was already running" if run.get("existing") else " queued"), run_id=run["id"])
        return "done", ("Job discovery is running with the *" + preset + "* mix. Every lead passes the sponsorship gate and the never-re-apply check "
                        "before it is saved; watch the Agents rail or ask me *status* in a few minutes."), \
            {"intent": "discovery", "run_id": run["id"], "suggestions": ["status", "overnight hunt"]}

    def _start_hunt(self, id):
        if self.tools.hunt is None:
            return "done", "The overnight hunt is not available here; try *find jobs*.", {"intent": "hunt_refused"}
        self._step(id, "Starting the overnight hunt", agent="discovery")
        try:
            result = self.tools.start_hunt()
        except ValueError as error:
            self._finish_step(id, "failed", str(error))
            return "done", str(error), {"intent": "hunt_refused", "suggestions": ["hunt status"]}
        self._finish_step(id, "done", result["summary"])
        config = result["config"]
        helpers = ", ".join(result["helpers"]) or "no preparation"
        text = (f"The overnight hunt is running: it keeps searching until it has saved **{config['target']}** jobs that fit "
                f"at **{config['min_fit']}+**, or {config['hours']:g} hours pass. It reads employer career feeds and job boards "
                "first, then runs focused AI searches role by role, and waits out AI usage limits instead of stopping"
                + ("" if config["allow_paid"] else " (it never uses a paid AI)") + f". Each job it saves gets: {helpers}. "
                "Keep the app open; follow it on **Daily Search** or ask *hunt status*. Nothing is ever submitted.")
        return "done", text, {"intent": "hunt", "hunt_id": result["hunt_id"], "suggestions": ["hunt status", "stop the hunt"]}

    @staticmethod
    def _hunt_text(result: dict) -> str:
        hunt = result.get("hunt") or {}
        if not hunt or hunt.get("state") == "never_run":
            return "No hunt has run yet. Say *overnight hunt* to start one with your saved choices."
        lines = [f"Hunt **{hunt['state']}**: {len(hunt.get('saved') or [])} of {hunt.get('target')} saved"
                 f" at fit {hunt.get('min_fit')}+ · {hunt.get('passes_done', 0)} passes done."]
        if hunt.get("stage"):
            lines.append("Now: " + hunt["stage"])
        waiting = hunt.get("waiting") or {}
        if waiting:
            lines.append(f"Waiting until {waiting.get('until_text')}: {waiting.get('why')}.")
        for saved in (hunt.get("saved") or [])[:10]:
            lines.append(f"- {saved}")
        if hunt.get("report"):
            lines.append(f"Report: `{hunt['report']}`")
        if hunt.get("error"):
            lines.append("Problem: " + hunt["error"])
        return "\n".join(lines)

    @staticmethod
    def _morning_text(result: dict) -> str:
        data = result.get("list")
        if not data:
            return ("There is no morning list yet. Switch on **Morning jobs** in Settings → This profile: the search runs "
                    "overnight and the list is ready by the time you choose. Or say *overnight hunt* to search now.")
        new = data.get("new") or []
        lines = [f"**Morning jobs, {data.get('date')}**: {len(new)} new · {data.get('to_apply_total', 0)} still to apply."]
        for text in data.get("needs_you") or []:
            lines.append(f"Needs you: {text}")
        for job in new[:10]:
            fit = f"fit {job['fit']}" if job.get("fit") is not None else "fit not scored yet"
            line = f"- **{job['company']} — {job['title']}** · {job.get('location') or ''} · {fit}"
            if job.get("url"):
                line += f" · [apply]({job['url']})"
            if job.get("resume"):
                line += f" · resume `{job['resume']}`"
            lines.append(line)
        if not new:
            lines.append("Nothing new passed every check this time; the jobs still to apply for are on the Dashboard.")
        if data.get("held"):
            lines.append(f"{data['held']} more are waiting for the AI requirement check; the next run checks them first.")
        for text in (data.get("fixed") or [])[:3]:
            lines.append(f"Fixed by itself: {text}")
        return "\n".join(lines)

    def _status(self):
        summary = self.s.summary()
        goals = summary["goals"]
        jobs = summary["jobs"]
        counts = summary["counts"]
        lines = [f"This week: {goals['week_completed']} of {goals['current_week_target']} applications; today {goals['today_completed']} of {goals['today_target']} ({goals['remaining_today']} left)."]
        lines.append(f"Saved jobs {counts['saved']} · applied {counts['applied']} · interviews {counts['interviews']} · offers {counts['offers']} · ghosted {counts['ghosted']} · excluded {counts['excluded']}.")
        ready = [j for j in jobs if j["status"] in {"saved", "prepared"}]
        if ready:
            lines.append("Resumes waiting to be sent: " + "; ".join(f"**{j['company']} — {j['title']}** (tier {j.get('sponsor_tier') or 'C'})" for j in ready[:8]) + ".")
        active = [r for r in summary["runs"] if r["state"] in {"queued", "running"}]
        if active:
            lines.append("Working now: " + ", ".join(r["kind"] + ((" — " + ((r.get("result") or {}).get("stage") or "")) if r.get("result") else "") for r in active) + ".")
        if summary["profile_dirty"]:
            lines.append("⚠ The Profile has unreviewed edits; new drafts wait until they are reconciled.")
        return "\n".join(lines)

    def _excluded(self):
        rows = self.s.excluded()
        if not rows:
            return "Nothing has been excluded."
        lines = [f"{len(rows)} postings were cut by the sponsorship gate. Most recent:"]
        for row in rows[:10]:
            lines.append(f"- **{row['company']} — {row['title']}**: “{row['sentence']}” ({row['reason_label']})")
        lines.append("Restore any wrong call from Dashboard → Excluded roles, or tell me which one was read wrongly.")
        return "\n".join(lines)

    def _job_matches(self, job, text):
        low = text.casefold()
        company = job["company"].casefold()
        if job["id"].casefold() == low or company in low or low in company:
            return True
        words = [w for w in re.findall(r"[a-z0-9+#]+", low) if len(w) > 2]
        return bool(words) and all(w in company + " " + job["title"].casefold() for w in words)

    def _find_jobs(self, phrase):
        jobs = sorted((j for j in self.w.jobs() if not j.get("deleted_at")), key=lambda j: (j["company"].casefold(), j["title"].casefold()))
        exact = [j for j in jobs if j["id"] == phrase.strip()]
        if exact:
            return exact
        return [j for j in jobs if self._job_matches(j, phrase.strip())]

    def _run_for_job(self, id, kind, phrase):
        found = self._find_jobs(phrase)
        if len(found) == 1:
            return self._dispatch_job_intent(id, kind, found[0], {})
        if not found:
            # Not a saved job by that name ("research the newest one"): the agent resolves the candidate's intent.
            return self._agent(id, self.get(id)["message"])
        return self._ask_which(id, kind, found, phrase, {})

    def _ask_which(self, id, intent, found, phrase, extra):
        if not found:
            jobs = [j for j in self.w.jobs() if not j.get("deleted_at")][:10]
            listing = "; ".join(f"**{j['company']} — {j['title']}**" for j in jobs) or "none saved yet"
            return "done", f"I can't find a saved job matching “{phrase}”. Saved: {listing}.", {"intent": intent + "_not_found"}
        self.s.set_pref("assistant_pending", {"kind": "choose_job", "intent": intent, "candidates": [{"id": j["id"], "company": j["company"], "title": j["title"]} for j in found], "extra": extra, "asked_at": self.s.now()})
        options = "\n".join(f"{n}. {j['company']} — {j['title']}" for n, j in enumerate(found, 1))
        return "needs_input", "Which posting?\n" + options + "\nReply with the number.", {"intent": intent + "_choose", "candidates": [j["id"] for j in found]}

    def _dispatch_job_intent(self, id, intent, job, extra):
        job = self.w.get_job(job["id"])
        if intent == "mark_applied":
            return self._confirm_applied(job, extra.get("date"))
        if intent == "open":
            return self._open_reply(job)
        label = {"study_plan": "Writing the study plan", "research": "Company research and hiring review"}.get(intent, intent)
        self._step(id, label + " for " + job["company"], agent=intent)
        try:
            run = self.runner.enqueue(intent, job["id"], *self.tools._engine_for(intent))
        except ValueError as error:
            self._finish_step(id, "failed", str(error))
            return "done", str(error), {"intent": intent + "_refused", "job_id": job["id"]}
        self._finish_step(id, "done", "Run " + run["id"][:8] + (" was already running" if run.get("existing") else " queued"), run_id=run["id"])
        if intent == "study_plan":
            text = (f"Writing the study plan for **{job['company']} — {job['title']}** from documented gaps. Proposed skills stay off the resume "
                    "until the candidate learns them and records supporting evidence in the profile. The plan lands in the application folder; watch the Agents rail.")
        else:
            text = f"Researching **{job['company']}** and running the independent hiring review (it does not read the candidate's profile). Results appear on the job's card when done."
        return "done", text, {"intent": intent, "job_id": job["id"], "run_id": run["id"], "suggestions": ["status", f"open {job['company']}"]}

    def _propose_applied(self, id, phrase, when):
        when = when.strip()
        if when:
            try:
                when = datetime.strptime(when[:10], "%Y-%m-%d").date().isoformat()
            except ValueError:
                return "done", "Give the date as YYYY-MM-DD, for example *applied to Snowflake on 2026-09-19*.", {"intent": "mark_applied_bad_date"}
        found = self._find_jobs(phrase)
        if len(found) == 1:
            return self._confirm_applied(self.w.get_job(found[0]["id"]), when)
        if not found:
            return self._agent(id, self.get(id)["message"])
        return self._ask_which(id, "mark_applied", found, phrase, {"date": when})

    def _confirm_applied(self, job, when):
        when = when or self.s.today()
        self.s.set_pref("assistant_pending", {"kind": "confirm_applied", "job_id": job["id"], "date": when, "asked_at": self.s.now()})
        return "needs_input", f"Record **{job['company']} — {job['title']}** as applied on {when}? Reply *yes* to confirm (I only record what you have actually sent).", \
            {"intent": "confirm_applied", "job_id": job["id"], "date": when, "suggestions": ["yes", "no"]}

    def _open(self, id, phrase):
        found = self._find_jobs(phrase)
        if len(found) == 1:
            return self._open_reply(self.w.get_job(found[0]["id"]))
        if not found:
            return self._agent(id, self.get(id)["message"])
        return self._ask_which(id, "open", found, phrase, {})

    def _open_reply(self, job):
        try:
            draft = self.studio.get(job["id"])
        except ValueError:
            return "done", f"**{job['company']} — {job['title']}** · status {job['status']}. No resume draft yet; say *build the resume for {job['company']}* to start one.", \
                {"intent": "open", "job_id": job["id"], "company": job["company"], "title": job["title"], "posting_url": job["url"],
                 "tier": job.get("sponsor_tier") or "C", "suggestions": [f"build the resume for {job['company']}"]}
        data = resume_card(job, draft)
        text = f"**{job['company']} — {job['title']}** · status {job['status']} · draft version {draft['revision']}" + (" with a current PDF." if data.get("pdf") else "; the PDF is not built for this version yet.")
        return "done", text, data

    # ---- The agent loop ------------------------------------------------------------
    def _snapshot(self) -> dict:
        summary = self.s.summary()
        goals = summary["goals"]
        # What each job already has, so "which jobs still need a resume / a study plan?"
        # is answered from the snapshot instead of one lookup per job.
        documents = {d["job_id"]: d for d in self.s.documents()}
        finished = {(r["job_id"], r["kind"]) for r in self.s.runs() if r["state"] == "completed" and r.get("job_id")}
        # Each job's verified requirement check (services/fit.py), read from the cache only:
        # "why is this a fit?" is answered from checked data, and a snapshot never calls an AI.
        from backend.services import fit
        try:
            evidence = fit.catalogue(self.s)
        except Exception:  # noqa: BLE001 - the snapshot must never fail on the fit line
            evidence = None

        def fit_line(job_id):
            try:
                return fit.brief(fit.cached(self.s, job_id, cat=evidence)) if evidence else ""
            except Exception:  # noqa: BLE001
                return ""

        def job_row(j):
            row = {k: v for k, v in brief_job(j).items() if k in {"id", "company", "title", "status", "sponsor_tier", "application_date"}}
            row.update({"resume_pdf": bool((documents.get(j["id"]) or {}).get("resumes")),
                        "research_done": (j["id"], "research") in finished,
                        "study_plan_done": (j["id"], "study_plan") in finished,
                        "fit_score": j.get("fit_score"),
                        "fit": fit_line(j["id"])})
            return row

        return {
            "today": self.s.today(),
            "goals": {k: goals[k] for k in ("weekly_target", "current_week_target", "week_completed", "today_target", "today_completed", "remaining_today")},
            "counts": summary["counts"],
            "jobs": [job_row(j) for j in summary["jobs"][:40]],
            "daily_search": self.tools.pipeline_brief(),
            "overnight_hunt": self.tools.hunt_brief(),
            "active_runs": [{"run_id": r["id"], "kind": r["kind"], "job_id": r["job_id"], "stage": (r.get("result") or {}).get("stage")}
                            for r in summary["runs"] if r["state"] in {"queued", "running"}],
            "profile_has_unreviewed_edits": summary["profile_dirty"],
            "pending_profile_entries": len(self.s.pending_knowledge()),
            "excluded_count": len(summary["excluded_jobs"]),
        }

    def _payload(self, id, transcript, turn_no) -> dict:
        recent = [{"you": m["message"][:500], "assistant": m["response"][:500]} for m in self.history(8) if m["id"] != id][-5:]
        # Older tool results shrink to their summary so the transcript stays within budget.
        results = [i for i, entry in enumerate(transcript) if entry.get("role") == "tool" and "result" in entry]
        keep = set(results[-FULL_RESULTS_KEPT:])
        task = []
        for index, entry in enumerate(transcript):
            if entry.get("role") == "tool" and "result" in entry and index not in keep:
                entry = {"role": "tool", "tool": entry["tool"], "summary": entry.get("summary", ""), "result": "(omitted: older result)"}
            task.append({k: v for k, v in entry.items() if k != "card"})
        return {
            "workspace": self._snapshot(),
            "tools": self.tools.catalogue(),
            "recent_conversation": recent,
            "task": task,
            "turns": {"used": turn_no, "max": MAX_TURNS},
        }

    def _run_tool(self, id, tool, arguments) -> dict:
        """Run one tool inside the current step; the transcript entry is the return value."""
        try:
            result = tool.handler(**arguments)
        except ValueError as error:
            self._finish_step(id, "failed", str(error))
            return {"role": "tool", "tool": tool.name, "arguments": arguments, "error": str(error)}
        except Exception as error:  # noqa: BLE001 - a broken tool is a failed step, never a stuck message
            detail = type(error).__name__ + ": " + str(error)[:300]
            self._finish_step(id, "failed", detail)
            return {"role": "tool", "tool": tool.name, "arguments": arguments, "error": detail}
        summary = str(result.get("summary") or "done")
        self._finish_step(id, "done", summary, **{k: result[k] for k in ("run_id", "agent") if result.get(k)})
        shown = {k: v for k, v in result.items() if k != "card"}
        text = json.dumps(shown, ensure_ascii=False, default=str)
        entry = {"role": "tool", "tool": tool.name, "arguments": arguments, "summary": summary,
                 "result": shown if len(text) <= RESULT_CHARS else _trim(text, RESULT_CHARS)}
        if result.get("card"):
            entry["card"] = result["card"]
        return entry

    @staticmethod
    def _with_cards(data: dict, transcript) -> dict:
        """One job's document is the full card; a reply that read several (a comparison) shows
        one compact row per job instead of whichever card came last."""
        cards: dict = {}
        for entry in transcript:
            card = entry.get("card")
            if card and card.get("job_id"):
                cards.pop(card["job_id"], None)
                cards[card["job_id"]] = card
        if len(cards) <= 1:
            return data
        out = {k: v for k, v in data.items() if k not in CARD_FIELDS}
        if out.get("intent") in {"open", "resume_ready"}:
            out["intent"] = "agent"
        out["cards"] = [{k: card.get(k) for k in CARD_ROW_FIELDS} for card in cards.values()]
        return out

    @staticmethod
    def _trace(transcript) -> list:
        """The plan→calls→results record of an agent run, kept with the message
        so the "what I did" view survives a reload."""
        out = []
        for e in transcript:
            if e.get("role") != "tool":
                continue
            row = {"tool": e.get("tool"), "summary": (e.get("summary") or e.get("error") or "")[:200]}
            if e.get("error"):
                row["error"] = True
            if e.get("auto_applied"):
                row["auto_applied"] = True
            out.append(row)
        return out

    def _note_fallback(self, id, team, noted: set) -> None:
        """The chosen AI failed and the backup Settings names answered: say so once per message."""
        from backend.ai import provider_label

        switched = getattr(team, "fell_back", None)
        if not switched or (switched["from_provider"], switched["to_provider"]) in noted:
            return
        noted.add((switched["from_provider"], switched["to_provider"]))
        detail = (f"{provider_label(switched['from_provider'])} could not answer ({switched['reason'].rstrip('.')}), "
                  f"so the backup, {provider_label(switched['to_provider'])} · {switched['to_model']}, did.")
        self._step(id, "Switched to the backup AI", state="done", detail=detail[:400], agent="orchestrator")
        if switched.get("recorded"):
            return  # Auto's router already wrote this switch to the activity log
        with self.w.connect() as db:
            self.w.record_event(db, "provider_fallback", None, ai_action="assistant_chat",
                                from_provider=switched["from_provider"], to_provider=switched["to_provider"],
                                reason=switched["reason"][:300])

    def _extra_calls(self, id, turn, transcript, data) -> None:
        """Run the read-only tools a decision listed beside its main call, each as its own step."""
        for extra in list(getattr(turn, "more_calls", None) or [])[:MAX_EXTRA_CALLS]:
            self._check_stop(id)
            try:
                tool = self.tools.get(extra.tool)
                arguments = self.tools.coerce(tool, json.loads(extra.arguments or "{}"))
            except (ValueError, json.JSONDecodeError) as error:
                transcript.append({"role": "tool", "tool": extra.tool, "error": str(error)[:400]})
                continue
            if tool.writes or tool.confirm:
                transcript.append({"role": "tool", "tool": tool.name, "error": "Not run: more_calls only takes tools that read. "
                                   "Call a tool that changes something as the main call of its own turn."})
                continue
            self._step(id, tool.label, agent=tool.agent)
            entry = self._run_tool(id, tool, arguments)
            transcript.append(entry)
            if entry.get("card"):
                data.update(entry["card"])

    def _agent(self, id, text, transcript=None):
        from backend.ai import any_provider_configured, team_for
        from backend.ai.agents.graph import AgentError

        if not any_provider_configured(self.w.root):
            return "done", ("No AI runtime is set up, so I can only run the exact commands. Install Claude Code or the ChatGPT app "
                            "(Codex), or add a provider key in Settings, and I can do the rest.\n\n" + HELP), {"intent": "answer_unavailable"}
        transcript = list(transcript or [])
        if text is not None:
            transcript.append({"role": "user", "content": text})
        team = team_for(self.s)
        data: dict = {"intent": "agent"}
        for entry in transcript:
            if entry.get("card"):
                data.update(entry["card"])
        used = sum(1 for e in transcript if e.get("role") == "tool")
        noted: set = set()
        for turn_no in range(used, used + MAX_TURNS):
            self._step(id, "Thinking", agent="assistant")
            try:
                turn = team.run("workspace_agent", self._payload(id, transcript, turn_no))
            except (AgentError, ValueError) as error:
                self._finish_step(id, "failed", str(error)[:300])
                advice = ("Choose another AI in the menu above the chat" + ("" if getattr(team, "fallback", None) else
                          ", or choose a Backup provider in Settings so the chat switches by itself next time") + ".")
                response = ("I could not reach the AI runtime, so nothing more was changed. " + str(error).rstrip(".")
                            + ". " + advice + " The exact commands (say *help*) still work without it.")
                return "failed", response, {**self._with_cards(data, transcript), "intent": "agent_failed",
                                            "trace": self._trace(transcript), "suggestions": ["status", "help"]}
            # A decision that arrives after the candidate pressed Stop is dropped.
            self._check_stop(id)
            if getattr(team, "fell_back", None):
                self._finish_step(id, "done")
                self._note_fallback(id, team, noted)
                self._step(id, "Thinking", agent="assistant")
            thought = " ".join(turn.thought.split())[:200]
            suggestions = [s.strip() for s in turn.suggestions if isinstance(s, str) and 0 < len(s.strip()) <= 80][:4]
            if turn.action == "reply":
                self._finish_step(id, "done", thought)
                return "done", turn.reply.strip() or "Done.", {**self._with_cards(data, transcript), "trace": self._trace(transcript), "suggestions": suggestions}
            if turn.action == "ask":
                self._finish_step(id, "done", thought)
                question = turn.reply.strip() or "What would you like me to do?"
                self.s.set_pref("assistant_pending", {"kind": "agent", "transcript": transcript + [{"role": "assistant", "asked": question}], "asked_at": self.s.now()})
                return "needs_input", question, {**self._with_cards(data, transcript), "intent": "agent_question", "trace": self._trace(transcript), "suggestions": suggestions}
            try:
                tool = self.tools.get(turn.tool)
                arguments = self.tools.coerce(tool, json.loads(turn.arguments or "{}"))
            except (ValueError, json.JSONDecodeError) as error:
                self._finish_step(id, "failed", str(error)[:200])
                transcript.append({"role": "tool", "tool": turn.tool, "error": str(error)[:400]})
                continue
            if tool.confirm and not self.auto_apply():
                diff = self.tools.diff(tool.name, arguments)
                self._finish_step(id, "done", thought)
                self.s.set_pref("assistant_pending", {"kind": "confirm_tool", "tool": tool.name, "arguments": arguments,
                                                      "diff": diff, "transcript": transcript, "asked_at": self.s.now()})
                lead = turn.reply.strip()
                question = (lead + "\n\n" if lead else "") + f"Ready to: {self.tools.describe(tool.name, arguments)}. Reply *yes* to go ahead or *no* to skip it."
                return "needs_input", question, {**self._with_cards(data, transcript), "intent": "confirm_tool", "tool": tool.name, "arguments": arguments,
                                                 "diff": diff, "trace": self._trace(transcript), "suggestions": ["yes", "no"]}
            self._relabel_step(id, tool.label, thought, agent=tool.agent)
            entry = self._run_tool(id, tool, arguments)
            if tool.confirm:
                # Auto-apply is on for this conversation: the gated tool ran without
                # the pause. The trace and the event log still record exactly what ran.
                entry["auto_applied"] = True
                entry["diff"] = self.tools.diff(tool.name, arguments)
                with self.w.connect() as db:
                    self.w.record_event(db, "assistant_auto_applied", None, tool=tool.name, arguments=arguments)
            transcript.append(entry)
            if entry.get("card"):
                data.update(entry["card"])
            self._extra_calls(id, turn, transcript, data)
        return "failed", (f"I stopped after {MAX_TURNS} steps without finishing. What was done is listed above; "
                          "tell me how to continue, or split the request."), {**self._with_cards(data, transcript), "intent": "agent_exhausted", "trace": self._trace(transcript)}
