"""Every feature of the workspace as a tool the assistant's agent loop can call.

One registry, one calling convention. Each tool wraps the same service call the
matching tab makes (the sponsorship gate, never-re-apply, Resume Studio, the
agent runner, the Profile change sets), so a tool can do nothing a tab cannot,
and whatever it does shows up on every tab from the shared database.

A tool declares its parameters (checked before the handler runs), a label for
the step timeline, and whether it needs the candidate's explicit *yes* first. The agent
loop in ``assistant.py`` pauses on those; the handler itself never asks.

Every result is a plain dict with a one-line ``summary`` (the step detail) and
may carry a ``card`` (a document to show: PDF, page image, scores). The Gmail
sync is deliberately absent: it runs through a signed-in mail connector and is
started from the Agents tab only. The Daily Search pipeline, the search report
and the Assurance check are the same calls the Daily Search and Assurance tabs
make.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

RUNNABLE_AGENTS = {
    "research": "Company research and the independent hiring review (never sees the candidate's profile)",
    "resume_build": "Compile the current draft and score it (no AI)",
    "resume_match": "Independent review of the built PDF against the posting",
    "study_plan": "Interview study plan from the honest gaps; never touches the resume",
}
JOB_STATUSES = ("saved", "prepared", "applied", "interview", "offer", "rejected", "withdrawn", "ghosted")
DISCOVERY_PRESETS = ("default", "balanced_five", "portals", "feeds")
POLICY_FILE = "AGENTS.md"
QUESTIONS_FILE = "data/context/QUESTIONS-FOR-YOU.md"
MAX_WAIT_SECONDS = 240


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    description: str
    handler: Callable[..., dict]
    parameters: dict = field(default_factory=dict)
    confirm: bool = False   # pauses the loop for the candidate's explicit yes
    writes: bool = False
    group: str = "Workspace"
    agent: str = "assistant"   # the registry entry (workspace_v2.AGENTS) this tool runs on

    def signature(self) -> str:
        """``name(job_id, status?)`` — the shape the model reads in the catalogue."""
        parts = [name + ("" if spec.get("required") else "?") for name, spec in self.parameters.items()]
        return f"{self.name}({', '.join(parts)})"


def phrase_gaps(items) -> list:
    """Requirements worth naming: short phrases, not the headings and sentences the scorer also lists."""
    kept = []
    for item in items or []:
        text = str(item).strip()
        if not text or len(text) > 40 or len(text.split()) > 5 or text.endswith((".", ":")):
            continue
        if re.search(r"qualification|responsibilit|what you|about (the|us|this)|requirements?$|preferred$|nice to have|benefits|apply", text, re.I):
            continue
        kept.append(text)
    return kept[:6]


def evidence_dict(job) -> dict:
    evidence = job.get("sponsor_evidence") or {}
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except ValueError:
            evidence = {}
    return evidence if isinstance(evidence, dict) else {}


def brief_job(job) -> dict:
    """The fields the model needs to talk about a job, never the notes."""
    evidence = evidence_dict(job)
    return {
        "id": job["id"], "company": job["company"], "title": job["title"], "location": job["location"],
        "status": job["status"], "sponsor_tier": job.get("sponsor_tier") or "C",
        "sponsor_note": evidence.get("label") or evidence.get("reason") or "",
        "application_date": job.get("application_date"), "url": job["url"],
        "has_resume_folder": bool(job.get("folder")), "posting_state": job.get("posting_state"),
        "removed": bool(job.get("deleted_at")),
    }


def resume_card(job, draft, fitted=None) -> dict:
    """The document card the chat shows: the current PDF, page image, scores and gaps."""
    preview = (fitted or draft).get("preview") or {}
    match = (fitted or {}).get("match") or draft.get("match") or {}
    layout = preview.get("layout") or {}
    page = (layout.get("pages") or [{}])[0]
    current = bool(preview.get("current", True)) and bool(preview.get("path"))
    coverage = (match.get("resume_coverage") or {}).get("score", match.get("score")) if match else None
    ats = (match.get("ats_readiness") or {}).get("score") if match else None
    return {
        "intent": "resume_ready" if fitted else "open",
        "job_id": job["id"], "company": job["company"], "title": job["title"],
        "tier": job.get("sponsor_tier") or "C", "revision": (fitted or draft).get("revision"),
        "pdf": preview["path"] + "/resume.pdf" if current else None,
        "preview_png": preview["path"] + "/page-01.png" if current else None,
        "posting_url": job["url"], "coverage": coverage, "ats": ats,
        "gaps": phrase_gaps(match.get("missing_unsupported")) if match else [],
        "page": {"count": preview.get("page_count"), "fill_percent": page.get("fill_percent"),
                 "font_pt": preview.get("body_font_pt"), "cuts": ((preview.get("ranking") or {}).get("cuts")) or []},
        "signature_project": (fitted or draft).get("fields", {}).get("SelectedProjectTitle") or "",
        "warnings": [w for w in (fitted or draft).get("warnings", []) if not w.startswith("Draft only")],
        "suggestions": [f"study plan for {job['company']}", f"research {job['company']}", f"applied to {job['company']}"],
    }


class Toolbox:
    def __init__(self, service, studio, runner, quality, chats=None, pipeline=None, hunt=None):
        from backend.chat_changes import ChatChangeService

        self.s, self.w, self.studio, self.runner, self.quality = service, service.w, studio, runner, quality
        self.chats = chats or ChatChangeService(service, studio)
        self.pipeline = pipeline  # the Daily Search pipeline (services/pipeline.py), when the app has one
        self.hunt = hunt  # the overnight hunt (services/hunt.py), when the app has one
        self.tools: dict[str, Tool] = {}
        self._register()

    # ---- Registry --------------------------------------------------------------
    def _add(self, name, label, description, handler, parameters=None, *, confirm=False, writes=False, group="Workspace", agent="assistant"):
        self.tools[name] = Tool(name, label, description, handler, parameters or {}, confirm, writes, group, agent)

    def get(self, name: str) -> Tool:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}. Use one of: " + ", ".join(sorted(self.tools)))
        return tool

    def _default_location(self) -> str:
        """Where a posting that names no place is assumed to be: the profile's country."""
        from backend.countries import pack_for

        pack = pack_for(self.w.root)
        return pack.text("default_location") or pack.name

    def catalogue(self) -> list:
        """What the model reads each turn: one line per tool, grouped by feature."""
        # Confirmations always address the selected candidate without guessing pronouns.
        marker = " Needs the candidate's yes: the loop pauses for it."
        return [{
            "name": tool.name, "signature": tool.signature(), "group": tool.group,
            "description": tool.description + (marker if tool.confirm else ""),
            "parameters": tool.parameters,
        } for tool in self.tools.values()]

    def capabilities(self) -> list:
        """The feature groups and tool labels, for the page's "what I can do" card."""
        groups: dict[str, list] = {}
        for tool in self.tools.values():
            groups.setdefault(tool.group, []).append(tool.label)
        return [{"group": name, "labels": labels} for name, labels in groups.items()]

    def coerce(self, tool: Tool, arguments: dict) -> dict:
        """Check and convert arguments so a handler never sees a wrong type or a missing value."""
        if not isinstance(arguments, dict):
            raise ValueError("Arguments must be a JSON object")
        clean = {}
        for name, spec in tool.parameters.items():
            value = arguments.get(name)
            if value in (None, ""):
                if spec.get("required"):
                    raise ValueError(f"{tool.name} needs {name}")
                continue
            kind = spec.get("type", "string")
            try:
                if kind == "integer":
                    value = int(value)
                elif kind == "number":
                    value = float(value)
                elif kind == "boolean":
                    value = value if isinstance(value, bool) else str(value).casefold() in {"true", "yes", "1"}
                elif kind == "array":
                    value = list(value) if isinstance(value, (list, tuple)) else [v.strip() for v in str(value).split(",") if v.strip()]
                else:
                    value = str(value).strip()
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a {kind}") from None
            if spec.get("enum") and value not in spec["enum"]:
                raise ValueError(f"{name} must be one of: " + ", ".join(map(str, spec["enum"])))
            clean[name] = value
        return clean

    def call(self, name: str, arguments: dict | None = None) -> dict:
        tool = self.get(name)
        return tool.handler(**self.coerce(tool, arguments or {}))

    def describe(self, name: str, arguments: dict) -> str:
        """What a pending call will do, in the candidate's words: the confirmation shows this, never raw IDs."""
        job = (lambda: self._job(arguments["job_id"])) if arguments.get("job_id") else None
        try:
            if name == "update_job":
                j = job()
                parts = []
                if arguments.get("status"):
                    parts.append(f"mark it *{arguments['status']}*")
                if arguments.get("application_date"):
                    parts.append(f"record the application date {arguments['application_date']}")
                if arguments.get("notes"):
                    parts.append("replace its notes")
                return f"**{j['company']} — {j['title']}**: " + (", ".join(parts) or "update it")
            if name == "remove_job":
                j = job()
                return f"Remove **{j['company']} — {j['title']}** from the active list" + (f" ({arguments['reason']})" if arguments.get("reason") else "") + "; it can be restored later"
            if name == "restore_excluded":
                row = next((r for r in self.s.excluded(True) if r["id"] == arguments.get("excluded_id")), None)
                return (f"Restore **{row['company']} — {row['title']}** even though the posting says “{row['sentence']}”" if row
                        else "Restore an excluded posting")
            if name == "apply_profile_change":
                with self.w.connect() as db:
                    row = db.execute("SELECT proposed_changes FROM chat_change_sets WHERE id=? AND scope='profile'", (arguments.get("change_set_id"),)).fetchone()
                changes = (json.loads(row["proposed_changes"]) if row else {}).get("changes") or []
                lines = []
                for change in changes:
                    if change["operation"] in {"add", "proposal"}:
                        lines.append(f"add {change.get('kind', 'fact')} **{change.get('title', '')}**" + (f" — {change['summary'][:160]}" if change.get("summary") else ""))
                    elif change["operation"] == "remove":
                        lines.append(f"remove entry {change['id']}")
                    else:
                        lines.append(f"update {change['id']}" + (f" to “{change['summary'][:160]}”" if change.get("summary") else ""))
                return "Change your Profile: " + ("; ".join(lines) if lines else "no changes") + ". New entries wait for your review before any resume uses them"
            if name == "reconcile_profile":
                pending = self.s.pending_knowledge()
                chosen = [p for p in pending if not arguments.get("ids") or p["id"] in set(arguments["ids"])]
                return f"Confirm {len(chosen)} pending Profile entr{'y' if len(chosen) == 1 else 'ies'} as reviewed" + (": " + ", ".join(p["title"] for p in chosen[:6]) if chosen else "")
            if name == "set_goals":
                days = arguments.get("workdays") or (self.s.pref("goals") or {}).get("workdays") or [0, 1, 2, 3, 4]
                names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                return f"Set the weekly target to **{arguments.get('weekly_target')}** applications on " + ", ".join(names[int(d)] for d in days if 0 <= int(d) <= 6)
            if name == "resolve_mail":
                mail = next((m for m in self.s.mail()["messages"] if m["id"] == arguments.get("id")), None)
                what = f"the email “{mail['subject']}” from {mail['sender']}" if mail else "that email"
                if arguments.get("action") == "dismiss":
                    return f"Dismiss {what}"
                j = job() if arguments.get("job_id") else None
                return f"Confirm {what} as evidence" + (f" for **{j['company']} — {j['title']}**" if j else "") + (", recording the application from it" if arguments.get("create_application") else "")
            if name == "update_search_plan":
                parts = []
                if arguments.get("add_titles"):
                    parts.append("also search for " + ", ".join(f"*{t}*" for t in arguments["add_titles"]))
                if arguments.get("remove_titles"):
                    parts.append("stop searching for " + ", ".join(f"*{t}*" for t in arguments["remove_titles"]))
                if arguments.get("exclude_titles"):
                    parts.append("never pursue titles with " + ", ".join(f"*{t}*" for t in arguments["exclude_titles"]))
                if arguments.get("track_company"):
                    parts.append(f"track **{arguments['track_company']}**'s careers feed")
                return "Change what your searches look for: " + ("; ".join(parts) or "no change") + \
                    ". This changes where to look, never your experience"
        except (ValueError, KeyError, TypeError):
            pass
        tool = self.get(name)
        return tool.label + " — " + (", ".join(f"{k}: {str(v)[:80]}" for k, v in arguments.items()) or "no arguments")

    def diff(self, name: str, arguments: dict) -> list:
        """Field-level before/after for a confirmation card: what changes if the candidate says yes.

        Every row is {"field", "before", "after"}; empty list means nothing
        describable (the plain-language question still stands on its own).
        """
        try:
            if name == "update_job":
                j = self._job(arguments["job_id"])
                rows = []
                if arguments.get("status"):
                    rows.append({"field": "Status", "before": j["status"], "after": arguments["status"]})
                if arguments.get("application_date"):
                    rows.append({"field": "Application date", "before": j.get("application_date") or "—", "after": arguments["application_date"]})
                if arguments.get("notes"):
                    rows.append({"field": "Notes", "before": (j.get("notes") or "—")[:200], "after": arguments["notes"][:200]})
                return rows
            if name == "remove_job":
                j = self._job(arguments["job_id"])
                return [{"field": f"{j['company']} — {j['title']}", "before": j["status"], "after": "removed (restorable)"}]
            if name == "restore_excluded":
                row = next((r for r in self.s.excluded(True) if r["id"] == arguments.get("excluded_id")), None)
                return [{"field": f"{row['company']} — {row['title']}", "before": "excluded: “" + row["sentence"] + "”", "after": "restored"}] if row else []
            if name == "apply_profile_change":
                with self.w.connect() as db:
                    row = db.execute("SELECT proposed_changes FROM chat_change_sets WHERE id=? AND scope='profile'", (arguments.get("change_set_id"),)).fetchone()
                changes = (json.loads(row["proposed_changes"]) if row else {}).get("changes") or []
                rows = []
                for change in changes:
                    op = change.get("operation", "update")
                    label = change.get("title") or change.get("id") or change.get("kind", "entry")
                    rows.append({"field": label,
                                 "before": "not in your Profile" if op in {"add", "proposal"} else "current wording",
                                 "after": (change.get("summary") or op)[:200] if op != "remove" else "removed"})
                return rows
            if name == "reconcile_profile":
                pending = self.s.pending_knowledge()
                chosen = [p for p in pending if not arguments.get("ids") or p["id"] in set(arguments["ids"])]
                return [{"field": p["title"], "before": "pending review", "after": "reviewed"} for p in chosen[:12]]
            if name == "set_goals":
                current = self.s.pref("goals") or {}
                names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
                days = arguments.get("workdays") or current.get("workdays") or [0, 1, 2, 3, 4]
                rows = []
                if arguments.get("weekly_target") is not None:
                    rows.append({"field": "Weekly target", "before": str(current.get("weekly_target", "—")), "after": str(arguments["weekly_target"])})
                rows.append({"field": "Workdays",
                             "before": ", ".join(names[int(d)] for d in current.get("workdays", [0, 1, 2, 3, 4]) if 0 <= int(d) <= 6),
                             "after": ", ".join(names[int(d)] for d in days if 0 <= int(d) <= 6)})
                return rows
            if name == "resolve_mail":
                mail = next((m for m in self.s.mail()["messages"] if m["id"] == arguments.get("id")), None)
                if not mail:
                    return []
                return [{"field": f"Email “{mail['subject']}” from {mail['sender']}",
                         "before": "pending review",
                         "after": "confirmed as evidence" + (" — records the application" if arguments.get("create_application") else "") if arguments.get("action") == "confirm" else "dismissed"}]
        except (ValueError, KeyError, TypeError):
            pass
        return []

    # ---- Helpers ---------------------------------------------------------------
    def _job(self, job_id: str) -> dict:
        try:
            return self.w.get_job(job_id)
        except ValueError:
            raise ValueError(f"No saved job has the ID {job_id!r}; call list_jobs for the real IDs") from None

    def _profile_text(self) -> str:
        return "\n".join(i["title"] + " " + i["summary"] for i in self.s.profile_context())

    def _register(self) -> None:
        from backend.countries import pack_for
        from backend.resume_contract import contract_for

        add = self._add
        S = lambda description, **extra: {"type": "string", "description": description, **extra}  # noqa: E731
        pack = pack_for(self.w.root)
        contract = contract_for(self.w.root)
        where = f"City or Remote ({pack.name}); '{self._default_location()}' if unstated"
        recheck = ("Re-evaluate a job's sponsorship tier and evidence (including public employer history)."
                   if pack.sponsor_index else "Re-evaluate a job's work-permit tier and the sentence behind it.")
        shape = f"{contract.describe_pages()} {pack.paper_label}"

        # -- Postings and the gates ---------------------------------------------
        add("list_jobs", "Listing saved jobs",
            "Saved jobs with ID, status, sponsorship tier and application date. Filter by status or a word from the company or title.",
            self.list_jobs, {"status": S("One of " + ", ".join(JOB_STATUSES), enum=list(JOB_STATUSES)),
                             "query": S("A word from the company or title"),
                             "include_removed": {"type": "boolean", "description": "Also list removed jobs"}},
            group="Jobs", agent="resume_tracker")
        add("get_job", "Reading a job",
            "Everything about one saved job: the posting text, sponsorship evidence, documents, verification and its agent runs.",
            self.get_job, {"job_id": S("The job ID from list_jobs", required=True)}, group="Jobs", agent="resume_tracker")
        add("job_fit", "Checking the job's requirements",
            "What one saved job asks for and whether the candidate's registered evidence meets each item: met, partial or missing, "
            "with the posting's own sentence and the evidence ids. Checked by AI on a free plan and verified against "
            "the registry; use it to answer why a job fits or what is missing. refresh=true checks again.",
            self.job_fit, {"job_id": S("The job ID from list_jobs", required=True),
                           "refresh": {"type": "boolean", "description": "Check again even when a current check exists"}},
            group="Jobs", agent="match")
        add("save_posting", "Saving the posting through the gates",
            "Save a job posting. Runs the sponsorship gate and never-re-apply first; a refusal is excluded with its sentence, a repeat is blocked. Then call build_resume.",
            self.save_posting, {"company": S("Employer exactly as the posting names it", required=True),
                                "title": S("Job title as posted", required=True),
                                "location": S(where),
                                "url": S("The posting link; it becomes the job's identity", required=True),
                                "description": S("The full posting text", required=True)},
            writes=True, group="Jobs", agent="sponsorship")
        add("fetch_posting", "Fetching the posting page",
            "Read a posting page by URL and return its text, for save_posting. Login walls and script-only pages return little text.",
            self.fetch_posting, {"url": S("The posting link", required=True)}, group="Jobs", agent="discovery")
        add("update_job", "Updating the job's status",
            "Change a job's status, application date (YYYY-MM-DD) or notes. 'applied' needs the actual submission date.",
            self.update_job, {"job_id": S("The job ID", required=True),
                              "status": S("New status", enum=list(JOB_STATUSES)),
                              "application_date": S("YYYY-MM-DD, the day the candidate submitted"),
                              "notes": S("Replacement notes")},
            confirm=True, writes=True, group="Jobs", agent="resume_tracker")
        add("remove_job", "Removing the job",
            "Remove a saved job from the active list (it can be restored).",
            self.remove_job, {"job_id": S("The job ID", required=True), "reason": S("Why, in a few words")},
            confirm=True, writes=True, group="Jobs", agent="resume_tracker")
        add("restore_job", "Restoring the job", "Bring a removed job back.",
            self.restore_job, {"job_id": S("The job ID", required=True)}, writes=True, group="Jobs", agent="resume_tracker")
        add("excluded_postings", "Listing excluded postings",
            "Postings the sponsorship gate cut, each with the sentence that excluded it and its excluded_id.",
            self.excluded_postings, group="Jobs", agent="sponsorship")
        add("restore_excluded", "Restoring an excluded posting",
            "Override the gate for one excluded posting (a wrong reading). Never for a posting that truly refuses sponsorship.",
            self.restore_excluded, {"excluded_id": S("From excluded_postings", required=True)},
            confirm=True, writes=True, group="Jobs", agent="sponsorship")
        add("recheck_sponsorship", "Re-running the sponsorship check",
            recheck,
            self.recheck_sponsorship, {"job_id": S("The job ID", required=True)}, writes=True, group="Jobs", agent="sponsorship")
        add("verify_posting", "Checking the posting is still open",
            "Fetch the posting page and record whether it is still live or expired.",
            self.verify_posting, {"job_id": S("The job ID", required=True)}, writes=True, group="Jobs", agent="discovery")
        add("find_jobs", "Starting job discovery",
            "Queue today's job search through the tracked career pages and portals. Every lead passes the gates before it is saved. Returns a run_id; results take minutes.",
            self.find_jobs, {"preset": S("Search mix", enum=list(DISCOVERY_PRESETS))}, writes=True, group="Jobs", agent="discovery")
        add("search_report", "Reading the last search's report",
            "The latest job search: its summary, the leads it turned away and why (legitimacy, relevance, never re-apply), "
            "and the postings the sponsorship gate excluded with the sentence that excluded them.",
            self.search_report, group="Jobs", agent="discovery")

        # -- Daily Search pipeline ------------------------------------------------
        add("run_search_pipeline", "Starting the Daily Search pipeline",
            "Run the Daily Search pipeline: find count new jobs, then for each saved job run the helpers "
            "(research, tailor, study_plan, pdf) on one AI. Anything left out uses the Daily Search page's saved "
            "choice; search_pipeline_status lists the AIs and models that are ready. Takes minutes; returns at once. "
            "Nothing is ever submitted.",
            self.run_search_pipeline, {
                "count": {"type": "integer", "description": "How many new jobs to find, 1-15"},
                "provider": S("AI id, e.g. azure_openai, codex, claude_code, kimi_cli"),
                "model": S("A model that AI offers; its first one when omitted"),
                "steps": {"type": "array", "description": "Helpers to run for each job: any of research, tailor, study_plan, pdf"},
                "source": S("Where to look", enum=list(DISCOVERY_PRESETS))},
            writes=True, group="Search", agent="discovery")
        add("search_pipeline_status", "Reading the Daily Search progress",
            "The Daily Search pipeline now: the running (or last) run with each job's steps, the page's saved choices, "
            "the AIs ready to run it with their models (Auto uses available free plans first), how much of each plan's 5-hour limit is used, and today's paid-call allowance.",
            self.search_pipeline_status, group="Search", agent="discovery")
        add("stop_search_pipeline", "Stopping the Daily Search pipeline",
            "Stop the running Daily Search pipeline after its current step. Jobs and files already saved stay.",
            self.stop_search_pipeline, writes=True, group="Search", agent="discovery")

        # -- The overnight hunt and the search plan --------------------------------
        add("start_hunt", "Starting the overnight hunt",
            "Start a goal-driven search that keeps going until it has saved `target` jobs that fit at `min_fit` or better, "
            "or `hours` run out: employer career feeds and job boards first (no AI), then focused AI web searches per "
            "role and site group (company ATS pages, " + ("gradireland, jobs.ie, IrishJobs, LinkedIn, publicjobs.ie" if pack.code == "ie"
                                                           else "LinkedIn and US boards") + "). "
            "When the free AI plans reach their usage limits it waits for the reset instead of failing, and it never uses a "
            "paid AI unless allow_paid. Then it prepares each job (research, tailored resume, study plan, PDF) and writes "
            "HUNT-REPORT.md. Use it when the candidate wants many or better jobs, or a search 'overnight'. Returns at once.",
            self.start_hunt, {
                "target": {"type": "integer", "description": "Jobs to save, 1-40 (default: the last hunt's, else 10)"},
                "hours": {"type": "number", "description": "How long it may run, 0.25-12 (default 8)"},
                "min_fit": {"type": "integer", "description": "Fit bar 50-95 (default 70); higher = fewer, closer matches"},
                "sources": S("all (default), feeds (no AI searching) or ai", enum=["all", "feeds", "ai"]),
                "steps": {"type": "array", "description": "Helpers per saved job: any of research, tailor, study_plan, pdf"},
                "allow_paid": {"type": "boolean", "description": "Allow a paid AI when every free plan rests (default false)"}},
            writes=True, group="Search", agent="discovery")
        add("hunt_status", "Reading the overnight hunt",
            "The running (or last) hunt: target and jobs saved so far, each pass with what it looked at and saved, "
            "whether it is waiting for an AI plan to reset (and until when), and the report path when done.",
            self.hunt_status, group="Search", agent="discovery")
        add("stop_hunt", "Stopping the overnight hunt",
            "Stop the running hunt at its next step. Jobs it already saved stay saved.",
            self.stop_hunt, writes=True, group="Search", agent="discovery")
        add("search_plan", "Reading the search plan",
            "What every search looks for and where: the target roles, the related titles employers use for the same work, "
            "excluded titles, cities, board keywords and each hunt pass with its exact queries.",
            self.search_plan, group="Search", agent="discovery")
        add("update_search_plan", "Changing what the searches look for",
            "Add or remove related job titles (other names employers use for the candidate's target roles), titles never to "
            "pursue, or a company to track (its careers link on Greenhouse, Lever, Ashby, Workday or SmartRecruiters). "
            "This changes where to look, never the candidate's experience.",
            self.update_search_plan, {
                "add_titles": {"type": "array", "description": "Related titles to also search for"},
                "remove_titles": {"type": "array", "description": "Related titles to stop searching for"},
                "exclude_titles": {"type": "array", "description": "Title words never to pursue, e.g. sales"},
                "track_company": S("Company name to track"),
                "careers_url": S("That company's careers or ATS link")},
            confirm=True, writes=True, group="Search", agent="discovery")

        # -- Resume ----------------------------------------------------------------
        add("build_resume", f"Building the {shape} resume",
            f"Open (or reuse) the job's Resume Studio draft, fit it to {shape} and score it against the posting. Returns the PDF path and scores. Blocked while the Profile has unreviewed edits.",
            self.build_resume, {"job_id": S("The job ID", required=True)}, writes=True, group="Resume", agent="resume")
        add("resume_status", "Reading the resume draft",
            "The job's draft: revision, current PDF if built, scores, summary, skills, selected projects and the eligible project list.",
            self.resume_status, {"job_id": S("The job ID", required=True)}, group="Resume", agent="resume")
        add("edit_resume", "Editing the resume",
            "Change the draft from a plain-English request (summary wording, skills order, which project leads, body font 10-12). Evidence-bound: wording that needs a fact the registry lacks is refused and reported. Recompiles and rescores; returns the diff.",
            self.edit_resume, {"job_id": S("The job ID", required=True),
                               "request": S("What to change, in plain English", required=True)},
            writes=True, group="Resume", agent="resume")
        add("undo_resume_change", "Undoing the last resume edit",
            "Restore the draft to how it was before an edit_resume change (by its change_set_id).",
            self.undo_resume_change, {"job_id": S("The job ID", required=True),
                                      "change_set_id": S("From edit_resume", required=True)},
            writes=True, group="Resume", agent="resume")
        add("sync_resume_projects", "Re-ranking the resume's projects from the profile",
            "Re-pick the signature and supporting projects from the registry for this job, as a new draft version.",
            self.sync_resume_projects, {"job_id": S("The job ID", required=True)}, writes=True, group="Resume", agent="resume")
        add("cover_letter", "Writing the cover letter",
            "Generate the cover letter for a job from registered evidence. It is saved for the candidate's review, never sent.",
            self.cover_letter, {"job_id": S("The job ID", required=True)}, writes=True, group="Resume", agent="resume")
        add("resume_assurance", "Checking the resume's claims",
            "The Assurance check for one job's resume: every claim line with its evidence status (verified, predicted, "
            "missing) and the candidate's keep or remove decision on each predicted item. Use it before applying.",
            self.resume_assurance, {"job_id": S("The job ID", required=True)}, group="Resume", agent="resume")
        add("application_documents", "Listing the application documents",
            "The files ready for one job: resume PDFs and the latest cover letter, with paths.",
            self.application_documents, {"job_id": S("The job ID", required=True)}, group="Resume", agent="resume")

        # -- Agents -----------------------------------------------------------------
        add("run_agent", "Starting an agent",
            "Queue one of the workspace agents for a job: " + "; ".join(f"{k} = {v}" for k, v in RUNNABLE_AGENTS.items())
            + ". Returns a run_id; use wait_for_run to collect the result.",
            self.run_agent, {"kind": S("Which agent", enum=list(RUNNABLE_AGENTS), required=True),
                             "job_id": S("The job ID", required=True)},
            writes=True, group="Agents", agent="orchestrator")
        add("wait_for_run", "Waiting for the agent",
            f"Wait up to {MAX_WAIT_SECONDS} seconds for a run to finish and return its result summary.",
            self.wait_for_run, {"run_id": S("From run_agent or find_jobs", required=True),
                                "seconds": {"type": "integer", "description": f"How long to wait, at most {MAX_WAIT_SECONDS}"}},
            group="Agents", agent="orchestrator")
        add("agent_runs", "Listing agent runs",
            "Recent agent runs with state and stage, optionally for one job.",
            self.agent_runs, {"job_id": S("Only this job's runs"), "limit": {"type": "integer", "description": "How many, default 10"}},
            group="Agents", agent="orchestrator")
        add("run_result", "Reading a run's result",
            "The full result of a finished run: the report text, sources, limitations, saved jobs.",
            self.run_result, {"run_id": S("The run ID", required=True)}, group="Agents", agent="orchestrator")

        # -- Profile ------------------------------------------------------------------
        add("profile_overview", "Reading the profile overview",
            "The candidate's profile at a glance: entries by kind, pending review (which blocks new drafts), target roles, status and open questions.",
            self.profile_overview, group="Profile", agent="profile")
        add("search_profile", "Searching the profile",
            "Find profile entries (skills, projects, experience, education, certifications, facts) by words, optionally one kind. Only registered entries may appear on a resume.",
            self.search_profile, {"query": S("Words to look for"), "kind": S("One kind", enum=["personal", "skill", "project", "experience", "education", "certification", "fact"])},
            group="Profile", agent="profile")
        add("get_profile_item", "Reading a profile entry", "One profile entry in full, with its details and review state.",
            self.get_profile_item, {"id": S("The entry ID from search_profile", required=True)}, group="Profile", agent="profile")
        add("propose_profile_change", "Proposing a profile change",
            "Turn the candidate's words ('I finished a course', 'remove the old title') into profile proposals. Nothing changes until apply_profile_change; new entries then wait for the candidate's review before any resume can use them.",
            self.propose_profile_change, {"request": S("What the candidate told you, in their words", required=True)}, group="Profile", agent="profile")
        add("apply_profile_change", "Applying the profile change",
            "Apply a proposal from propose_profile_change.",
            self.apply_profile_change, {"change_set_id": S("From propose_profile_change", required=True)},
            confirm=True, writes=True, group="Profile", agent="profile")
        add("reconcile_profile", "Confirming the pending profile entries",
            "Mark pending profile entries as reviewed so new resume drafts can start again. Only after the candidate has looked at them.",
            self.reconcile_profile, {"ids": {"type": "array", "description": "Entry IDs to confirm; all pending when omitted"}},
            confirm=True, writes=True, group="Profile", agent="profile")
        add("open_questions", "Reading the open questions",
            "The questions still waiting for the candidate's answer (QUESTIONS-FOR-YOU.md), which sharpen future resumes.",
            self.open_questions, group="Profile", agent="profile")
        add("add_question", "Noting a question for the candidate",
            "Record a question only the candidate can answer in QUESTIONS-FOR-YOU.md (the one context file the workspace may write).",
            self.add_question, {"question": S("The question, one or two sentences", required=True)}, writes=True, group="Profile", agent="profile")

        # -- Goals, mail, settings, policy ----------------------------------------
        add("status", "Reading where the search stands",
            "This week's applications against target, counts by status, resumes waiting to be sent, running agents.",
            self.status, group="Search", agent="resume_tracker")
        add("get_goals", "Reading the goals", "Weekly target, workdays, today's plan and the week schedule.", self.get_goals, group="Search", agent="resume_tracker")
        add("set_goals", "Changing the goals",
            "Set the weekly application target and workdays (0=Monday … 6=Sunday).",
            self.set_goals, {"weekly_target": {"type": "integer", "description": "Applications per week, 1-200", "required": True},
                             "workdays": {"type": "array", "description": "Weekday numbers, e.g. [0,1,2,3,4]"},
                             "start_date": S("YYYY-MM-DD; today when omitted")},
            confirm=True, writes=True, group="Search", agent="resume_tracker")
        add("list_mail", "Reading application emails",
            "Application emails already synced from Gmail (receipts, interviews, rejections) and the mailbox connection state. The sync itself runs from the Agents tab.",
            self.list_mail, {"state": S("pending, confirmed or dismissed", enum=["pending", "confirmed", "dismissed"])}, group="Search", agent="email")
        add("resolve_mail", "Resolving an email",
            "Confirm an email as evidence for a job (optionally creating the application record) or dismiss it.",
            self.resolve_mail, {"id": S("The mail ID", required=True), "action": S("confirm or dismiss", enum=["confirm", "dismiss"], required=True),
                                "job_id": S("The job it belongs to, for confirm"),
                                "create_application": {"type": "boolean", "description": "Record the application from this receipt"}},
            confirm=True, writes=True, group="Search", agent="email")
        add("ai_settings", "Reading the AI settings",
            "Which AI runtimes are ready on this machine (Auto's route: Kimi Code, Codex, Claude Code, then Azure), which plans are resting after a usage limit, what runs the assistant, and today's paid-call allowance (free plan calls never count).",
            self.ai_settings, group="Settings", agent="orchestrator")
        add("set_discovery_preset", "Changing the search mix",
            "Choose the discovery mix: default, balanced_five (five sectors) or portals.",
            self.set_discovery_preset, {"preset": S("The preset", enum=list(DISCOVERY_PRESETS), required=True)}, writes=True, group="Settings", agent="orchestrator")
        add("read_policy", "Reading the workspace rules",
            "The workspace policy (AGENTS.md): the sponsorship rule, never re-apply, evidence and scope, the resume contract. Give a topic word to get one section.",
            self.read_policy, {"topic": S("A word from a heading, e.g. sponsorship, re-apply, evidence, resume")}, group="Settings", agent="orchestrator")
        add("recent_activity", "Reading recent activity", "The latest recorded events in the workspace.",
            self.recent_activity, {"limit": {"type": "integer", "description": "How many, default 20"}}, group="Settings", agent="orchestrator")

    # ---- Jobs ------------------------------------------------------------------
    def list_jobs(self, status=None, query=None, include_removed=False) -> dict:
        jobs = self.w.jobs(include_deleted=bool(include_removed))
        if status:
            jobs = [j for j in jobs if j["status"] == status]
        if query:
            words = [w for w in re.findall(r"[a-z0-9+#]+", query.casefold()) if len(w) > 1]
            jobs = [j for j in jobs if all(w in (j["company"] + " " + j["title"]).casefold() for w in words)]
        jobs.sort(key=lambda j: (j["company"].casefold(), j["title"].casefold()))
        return {"summary": f"{len(jobs)} job(s)", "jobs": [brief_job(j) for j in jobs[:60]], "total": len(jobs)}

    def get_job(self, job_id) -> dict:
        job = self._job(job_id)
        documents = next((d for d in self.s.documents() if d["job_id"] == job_id), None)
        runs = [{"id": r["id"], "kind": r["kind"], "state": r["state"], "stage": (r.get("result") or {}).get("stage"),
                 "updated_at": r["updated_at"]} for r in self.s.runs() if r["job_id"] == job_id][:6]
        try:
            verification = self.quality.posting_history(job_id)[:3]
        except Exception:  # noqa: BLE001 - history is optional context
            verification = []
        return {
            "summary": f"{job['company']} — {job['title']} · {job['status']} · tier {job.get('sponsor_tier') or 'C'}",
            **brief_job(job), "notes": job.get("notes") or "", "created_at": job["created_at"],
            "sponsor_evidence": evidence_dict(job), "description": (job.get("description") or "")[:2500],
            "documents": documents, "runs": runs, "verification": verification,
            "legitimacy_state": job.get("legitimacy_state"), "size_category": job.get("size_category"),
        }

    def job_fit(self, job_id, refresh=False) -> dict:
        from backend.services import fit

        self._job(job_id)
        analysis = fit.for_job(self.s, job_id, refresh=bool(refresh))
        return {"summary": fit.brief(analysis) or f"fit {analysis['score']}/100", "score": analysis["score"],
                "checked_by": analysis["provider_label"] or "rules",
                "requirements": [{k: r[k] for k in ("text", "category", "status", "evidence_ids", "excerpt")}
                                 for r in analysis["matrix"]["requirements"] if r["status"] != "unknown"][:25],
                "hard_blockers": analysis["matrix"]["hard_blockers"], "rationale": analysis["rationale"]}

    def save_posting(self, company, title, url, description, location=None) -> dict:
        posting = {"company": company, "title": title, "location": location or self._default_location(), "url": url,
                   "description": description, "requisition_id": ""}
        if len(description) < 80:
            raise ValueError("The description is too short to be a posting; paste the whole text")
        result = self.s.add_posting(posting, source="chat")
        if result.get("excluded"):
            return {"summary": "Excluded: " + result["reason_label"], "saved": False, "excluded": True,
                    "sentence": result["sentence"], "reason": result["reason_label"],
                    "excluded_id": result["record"]["id"] if result.get("record") else None,
                    "note": "Not saved. It is listed under Excluded roles; a wrong reading can be restored there."}
        if result.get("blocked"):
            return {"summary": "Blocked: " + result["rule"], "saved": False, "blocked": True, "rule": result["rule"], "note": result["note"]}
        job = result["job"]
        if not result.get("duplicate"):
            try:
                self.w.track_search_job(job["id"], self.s.today())
            except ValueError:
                pass
        warnings = []
        try:
            relevance = self.quality.relevance(posting, self._profile_text())
            if not result.get("duplicate"):
                from backend.services import fit
                fit.save(self.s, job["id"], relevance["fit"])
            if relevance.get("eligible") is False:
                warnings.append(f"Fit {relevance['score']}/100 — {relevance['why']}")
        except Exception:  # noqa: BLE001 - the relevance note is advice, never a gate here
            pass
        evidence = evidence_dict(job)
        return {"summary": ("Already saved · " if result.get("duplicate") else "Saved · ") + f"tier {job.get('sponsor_tier') or 'C'}"
                + (f": {evidence.get('label') or evidence.get('reason')}" if evidence.get("label") or evidence.get("reason") else ""),
                "saved": True, "duplicate": bool(result.get("duplicate")), "job": brief_job(job), "warnings": warnings,
                "profile_has_unreviewed_edits": self.s.profile_dirty()}

    def fetch_posting(self, url) -> dict:
        page = self.quality.fetcher(url) or {}
        text = (page.get("text") or "")[:60_000]
        return {"summary": f"{len(text):,} characters" + (f" (status {page.get('status')})" if page.get("status") else ""),
                "status": page.get("status"), "final_url": page.get("final_url") or url, "text": text,
                "readable": len(text) >= 300}

    def update_job(self, job_id, status=None, application_date=None, notes=None) -> dict:
        job = self._job(job_id)
        updated = self.w.update_job(job_id, status or job["status"], notes, application_date)
        self.w.export_tracking()
        self.s.export_state()
        return {"summary": f"{updated['company']} — {updated['title']} is now {updated['status']}"
                + (f" (applied {updated['application_date']})" if updated.get("application_date") else ""),
                "job": brief_job(updated)}

    def remove_job(self, job_id, reason=None) -> dict:
        job = self._job(job_id)
        self.s.remove_job(job_id, reason or "Not suitable")
        return {"summary": f"Removed {job['company']} — {job['title']}", "job_id": job_id, "restorable": True}

    def restore_job(self, job_id) -> dict:
        self.s.restore_job(job_id)
        job = self._job(job_id)
        return {"summary": f"Restored {job['company']} — {job['title']}", "job": brief_job(job)}

    def excluded_postings(self) -> dict:
        rows = self.s.excluded()
        return {"summary": f"{len(rows)} excluded", "excluded": [
            {"excluded_id": r["id"], "company": r["company"], "title": r["title"], "sentence": r["sentence"],
             "reason": r["reason_label"], "excluded_at": r["excluded_at"], "url": r["url"]} for r in rows[:40]]}

    def restore_excluded(self, excluded_id) -> dict:
        result = self.s.restore_excluded(excluded_id)
        if result.get("blocked"):
            return {"summary": "Blocked: " + result["rule"], "restored": False, "rule": result["rule"], "note": result["note"]}
        job = result["job"]
        return {"summary": f"Restored: {job['company']} — {job['title']}", "restored": True, "job": brief_job(job)}

    def recheck_sponsorship(self, job_id) -> dict:
        result = self.s.reevaluate_sponsorship(job_id)
        if result.get("excluded"):
            return {"summary": "Now excluded: " + result["reason_label"], "excluded": True,
                    "sentence": result["sentence"], "reason": result["reason_label"]}
        job = result["job"]
        return {"summary": f"Tier {job.get('sponsor_tier') or 'C'}", "excluded": False, "job": brief_job(job),
                "note": result.get("note", "")}

    def verify_posting(self, job_id) -> dict:
        result = self.quality.verify_posting(job_id)
        return {"summary": str(result.get("state") or "checked"), **{k: result.get(k) for k in ("state", "checked_at", "final_url", "evidence")}}

    def find_jobs(self, preset=None) -> dict:
        preset = preset or (self.s.pref("discovery_preferences", {}) or {}).get("preset", "default")
        run = self.runner.enqueue("discovery", None, *self._engine_for("discovery"), preset)
        return {"summary": f"Discovery ({preset}) " + ("already running" if run.get("existing") else "queued"),
                "run_id": run["id"], "preset": preset, "existing": bool(run.get("existing")),
                "note": "Takes minutes; every lead passes the sponsorship gate and never-re-apply before it is saved."}

    def search_report(self) -> dict:
        run = next((r for r in self.s.runs() if r["kind"] == "discovery" and r["state"] in {"completed", "failed"}), None)
        if run is None:
            return {"summary": "No search has run yet", "found": False}
        result = run.get("result") if isinstance(run.get("result"), dict) else {}
        excluded = [{"company": e.get("company"), "title": e.get("title"), "sentence": e.get("sentence"), "reason": e.get("reason")}
                    for e in result.get("excluded") or []]
        rejected = [str(line)[:300] for line in result.get("rejected_leads") or []]
        saved = [brief_job(self.w.get_job(job_id)) for job_id in result.get("added_job_ids") or [] if self._exists(job_id)]
        return {"summary": f"{run['state']} · {len(saved)} saved · {len(rejected)} turned away · {len(excluded)} excluded",
                "state": run["state"], "error": run.get("error"), "finished_at": run["updated_at"],
                "provider": run.get("provider"), "model": run.get("model"),
                "report": (result.get("summary") or "")[:2000], "saved_jobs": saved,
                "turned_away": rejected[:20], "excluded_by_sponsorship_gate": excluded[:20]}

    def _exists(self, job_id) -> bool:
        try:
            self.w.get_job(job_id)
            return True
        except ValueError:
            return False

    # ---- Daily Search pipeline -----------------------------------------------
    def _pipeline(self):
        if self.pipeline is None:
            raise ValueError("The Daily Search pipeline is not available here; use find_jobs")
        return self.pipeline

    def pipeline_brief(self) -> dict | None:
        """The running or last Daily Search run in a few fields, for the agent's snapshot."""
        if self.pipeline is None:
            return None
        try:
            status = self.pipeline.status()
        except Exception:  # noqa: BLE001 - the snapshot must never fail on a side panel
            return None
        run = status.get("current") or status.get("last")
        if not run:
            return {"state": "never_run"}
        progress = run.get("progress") or {}
        config = run.get("config") or {}
        return {"run_id": run["id"], "state": run["state"], "stage": progress.get("stage"),
                "ai": f"{config.get('provider')} · {config.get('model')}",
                "jobs_target": progress.get("jobs_target"), "find": (progress.get("find") or {}).get("note"),
                "jobs": [{"company": j.get("company"), "title": j.get("title"),
                          "steps": {k: (v or {}).get("state") for k, v in (j.get("steps") or {}).items()}}
                         for j in progress.get("jobs") or []],
                "error": run.get("error"), "finished_at": run.get("finished_at")}

    def run_search_pipeline(self, count=None, provider=None, model=None, steps=None, source=None) -> dict:
        from backend.services.pipeline import STEP_IDS

        pipeline = self._pipeline()
        saved = pipeline.preferences()
        ai = {p["id"]: p for p in pipeline.providers()}
        provider = provider or saved["provider"]
        if provider not in ai:
            ready = [p["id"] for p in ai.values() if p.get("ready")]
            raise ValueError(f"Unknown AI {provider!r}; ready here: " + ", ".join(ready))
        listed = [m["id"] for m in ai[provider]["models"]]
        if not model:
            model = saved["model"] if provider == saved["provider"] and saved["model"] in listed else (listed[0] if listed else "")
        if steps:
            unknown = [step for step in steps if step not in STEP_IDS]
            if unknown:
                raise ValueError("Unknown helper(s): " + ", ".join(unknown) + "; choose from " + ", ".join(STEP_IDS))
            chosen = {step: step in steps for step in STEP_IDS}
        else:
            chosen = saved["steps"]
        run = pipeline.start({"count": int(count or saved["count"]), "source": source or saved["source"],
                              "provider": provider, "model": model, "steps": chosen})
        helpers = [step for step in STEP_IDS if chosen.get(step)]
        return {"summary": f"Started: {run['progress'].get('jobs_target')} job(s) on {ai[provider]['label']} · {model}"
                           + (" · " + ", ".join(helpers) if helpers else " · find only"),
                "pipeline_run_id": run["id"], "provider": provider, "model": model, "steps": helpers,
                "jobs_target": run["progress"].get("jobs_target"),
                "note": "Takes minutes. Tell the candidate it is running; they can watch Daily Search or ask for progress."}

    def search_pipeline_status(self) -> dict:
        pipeline = self._pipeline()
        status = pipeline.status()
        brief = self.pipeline_brief() or {}
        ready = [{"provider": p["id"], "label": p["label"], "models": [m["id"] for m in p["models"]][:6]}
                 for p in pipeline.providers() if p.get("ready")]
        return {"summary": (str(brief.get("state")) + (f" · {brief['stage']}" if brief.get("stage") else "")) if brief else "unknown",
                "run": brief, "saved_choices": pipeline.preferences(), "ready_ais": ready,
                "budget": status.get("budget"), "remaining_today": (status.get("plan") or {}).get("remaining_today")}

    def stop_search_pipeline(self) -> dict:
        pipeline = self._pipeline()
        current = pipeline.status().get("current")
        if not current:
            return {"summary": "Nothing is running", "stopped": False}
        pipeline.stop(current["id"])
        return {"summary": "Stopping after the current step", "stopped": True, "pipeline_run_id": current["id"]}

    # ---- The overnight hunt and the search plan --------------------------------
    def _hunt(self):
        if self.hunt is None:
            raise ValueError("The overnight hunt is not available here; use run_search_pipeline")
        return self.hunt

    def hunt_brief(self) -> dict | None:
        """The running or last hunt in a few fields, for the agent's snapshot."""
        if self.hunt is None:
            return None
        try:
            status = self.hunt.status()
        except Exception:  # noqa: BLE001 - the snapshot must never fail on a side panel
            return None
        run = status.get("current") or status.get("last")
        if not run:
            return {"state": "never_run"}
        progress, config = run.get("progress") or {}, run.get("config") or {}
        passes = progress.get("passes") or []
        return {"hunt_id": run["id"], "state": run["state"], "stage": progress.get("stage"),
                "target": config.get("target"), "min_fit": config.get("min_fit"),
                "saved": [f"{j['company']} — {j['title']} (fit {j.get('fit')})" for j in progress.get("saved") or []],
                "passes_done": sum(p.get("state") == "done" for p in passes), "passes": len(passes),
                "waiting": progress.get("waiting"), "report": progress.get("report"), "error": run.get("error"),
                "finished_at": run.get("finished_at")}

    def start_hunt(self, target=None, hours=None, min_fit=None, sources=None, steps=None, allow_paid=None) -> dict:
        from backend.services.pipeline import STEP_IDS

        hunt = self._hunt()
        values = {"target": target, "hours": hours, "min_fit": min_fit, "sources": sources, "allow_paid": allow_paid}
        if steps is not None:
            unknown = [step for step in steps if step not in STEP_IDS]
            if unknown:
                raise ValueError("Unknown helper(s): " + ", ".join(unknown) + "; choose from " + ", ".join(STEP_IDS))
            values["steps"] = {step: step in steps for step in STEP_IDS}
        run = hunt.start({k: v for k, v in values.items() if v is not None})
        config = run["config"]
        helpers = [step for step, on in config["steps"].items() if on]
        return {"summary": f"Hunt started: {config['target']} job(s) at fit {config['min_fit']}+ within {config['hours']:g} h",
                "hunt_id": run["id"], "config": config, "helpers": helpers,
                "note": "It runs in the background (the app must stay open) and waits out AI usage limits. "
                        "Tell the candidate they can follow it on Daily Search or ask for the hunt status; "
                        "the report lands in daily-job-search/<date>/HUNT-REPORT.md."}

    def hunt_status(self) -> dict:
        brief = self.hunt_brief() or {}
        if not brief or brief.get("state") == "never_run":
            return {"summary": "No hunt has run yet", "hunt": brief}
        run = self._hunt().get(brief["hunt_id"]) or {}
        passes = [{k: p.get(k) for k in ("label", "state", "looked", "saved", "turned_away", "held", "error", "note")}
                  for p in (run.get("progress") or {}).get("passes") or []][-12:]
        waiting = brief.get("waiting") or {}
        return {"summary": f"{brief['state']} · {len(brief['saved'])} of {brief['target']} saved"
                           + (f" · waiting until {waiting.get('until_text')}" if waiting else ""),
                "hunt": brief, "recent_passes": passes}

    def stop_hunt(self) -> dict:
        hunt = self._hunt()
        current = hunt.status().get("current")
        if not current:
            return {"summary": "No hunt is running", "stopped": False}
        hunt.stop(current["id"])
        return {"summary": "Stopping the hunt at its next step", "stopped": True, "hunt_id": current["id"]}

    def search_plan(self) -> dict:
        from backend.services.search_plan import describe

        plan = describe(self.w.root)
        return {"summary": f"{len(plan['roles'])} target role(s), {len(plan['related_titles'])} related titles, "
                           f"{len(plan['strategies'])} passes per hunt cycle",
                **{k: plan[k] for k in ("roles", "related_titles", "excluded_titles", "markets", "cities",
                                        "board_keywords", "early_career")},
                "passes": plan["strategies"][:24]}

    def update_search_plan(self, add_titles=None, remove_titles=None, exclude_titles=None, track_company=None,
                           careers_url=None) -> dict:
        import yaml

        from career import atomic_write
        from backend.services.profile_sync import yaml_set

        changes = []
        profile_path = self.w.root / "data/config/profile.yml"
        text = profile_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        targets = dict(data.get("target_roles") or {})
        clean = lambda values: [" ".join(str(v).split()) for v in values or [] if str(v).strip()]  # noqa: E731
        related = clean(targets.get("related_titles"))
        suppressed = clean(targets.get("suppressed_related"))
        excluded = clean(targets.get("excluded_titles"))
        for title in clean(add_titles):
            if title.casefold() not in {t.casefold() for t in related}:
                related.append(title)
                changes.append(f"search for {title}")
            suppressed = [t for t in suppressed if t.casefold() != title.casefold()]
        for title in clean(remove_titles):
            related = [t for t in related if t.casefold() != title.casefold()]
            if title.casefold() not in {t.casefold() for t in suppressed}:
                suppressed.append(title)
            changes.append(f"stop searching for {title}")
        for title in clean(exclude_titles):
            if title.casefold() not in {t.casefold() for t in excluded}:
                excluded.append(title)
                changes.append(f"never pursue '{title}' titles")
        if add_titles or remove_titles or exclude_titles:
            targets.update(related_titles=related, suppressed_related=suppressed, excluded_titles=excluded)
            try:
                for key in ("related_titles", "suppressed_related", "excluded_titles"):
                    text = yaml_set(text, ["target_roles", key], targets[key])
            except ValueError:
                text = yaml_set(text, ["target_roles"], targets)
            atomic_write(profile_path, text if text.endswith("\n") else text + "\n")
        if track_company:
            from backend.services.job_sources import tracked_row

            if not careers_url:
                raise ValueError("Give the company's careers or ATS link to track it")
            row = tracked_row(track_company, careers_url)
            portals_path = self.w.root / "data/config/portals.yml"
            portals_text = portals_path.read_text(encoding="utf-8") if portals_path.exists() else ""
            rows = list((yaml.safe_load(portals_text) or {}).get("tracked_companies") or [])
            if any(str(r.get("name", "")).casefold() == row["name"].casefold() for r in rows if isinstance(r, dict)):
                raise ValueError(f"{row['name']} is already tracked")
            rows.append(row)
            atomic_write(portals_path, yaml_set(portals_text, ["tracked_companies"], rows).rstrip("\n") + "\n")
            changes.append(f"track {row['name']} ({row['ats']})")
        if not changes:
            raise ValueError("Say which titles to add, remove or exclude, or which company to track")
        with self.w.connect() as db:
            self.w.record_event(db, "search_plan_updated", changes=changes)
        return {"summary": "Search plan updated: " + "; ".join(changes), "changes": changes,
                "plan": self.search_plan()}

    # ---- Resume ----------------------------------------------------------------
    def resume_assurance(self, job_id) -> dict:
        from backend.services.assurance import build_assurance

        job = self._job(job_id)
        report = build_assurance(self.s, self.studio, job_id)
        counts = report["summary"]
        flagged = [{"text": c["text"][:200], "section": c["section"], "status": c["evidence_status"],
                    "decision": c.get("decision")} for c in report["claims"] if c["evidence_status"] != "verified"]
        return {"summary": f"{counts['verified']} verified · {counts['predicted']} predicted · {counts['missing']} missing"
                           f" · {counts['pending']} awaiting the candidate's decision",
                "job": brief_job(job), "counts": counts, "ats_readiness": report.get("score"),
                "needs_attention": flagged[:25], "note": report.get("note") or
                "Predicted items are suggestions the candidate keeps or removes in Assurance before applying; missing means no evidence."}

    def build_resume(self, job_id) -> dict:
        job = self._job(job_id)
        draft = self.studio.open(job_id)
        fitted = self.studio.fit(job_id, draft["revision"])
        card = resume_card(job, draft, fitted)
        page = card["page"]
        return {"summary": f"{page['count'] or 1} page · {page['fill_percent']}% filled · {page['font_pt']}pt · coverage {card['coverage']} · ATS {card['ats']}",
                "card": card, "pdf": card["pdf"], "revision": card["revision"], "coverage": card["coverage"], "ats": card["ats"],
                "gaps": card["gaps"], "cuts": page["cuts"], "signature_project": card["signature_project"], "warnings": card["warnings"],
                "note": "Nothing is submitted: the candidate reviews the PDF and applies through the posting link if they choose."}

    def resume_status(self, job_id) -> dict:
        job = self._job(job_id)
        try:
            draft = self.studio.get(job_id)
        except ValueError:
            return {"summary": "No draft yet", "has_draft": False, "job": brief_job(job),
                    "note": "Call build_resume to start the contract-sized draft."}
        card = resume_card(job, draft)
        fields = draft.get("fields") or {}
        return {"summary": f"v{draft['revision']}" + (" · PDF current" if card["pdf"] else " · PDF not built for this version")
                + (f" · coverage {card['coverage']} · ATS {card['ats']}" if card["coverage"] is not None else ""),
                "has_draft": True, "card": card, "revision": draft["revision"], "pdf": card["pdf"],
                "fields": {"summary": fields.get("ResumeSummary", ""), "skills": fields.get("CoreSkills", ""),
                           "signature_project": fields.get("SelectedProjectTitle", ""), "second_project": fields.get("SecondProjectTitle", "")},
                "eligible_projects": [{"id": p["id"], "title": p["title"], "rank": p["rank"]} for p in draft.get("project_library", []) if p.get("eligible")],
                "body_font_pt": self.s.pref("resume_font:" + job_id), "warnings": card["warnings"],
                "scores": {k: v for k, v in (draft.get("match") or {}).items() if k in {"resume_coverage", "ats_readiness", "missing_unsupported", "current"}}}

    def edit_resume(self, job_id, request) -> dict:
        self._job(job_id)
        draft = self.studio.get(job_id)
        request_id = "chat-" + uuid.uuid4().hex[:12]
        preview = self.chats.resume_preview(job_id, request, request_id, draft["revision"])
        proposal = preview["proposed_changes"]
        if not proposal.get("applies_resume_change"):
            return {"summary": "No change could be made", "applied": False, "diff": proposal.get("diff", ""),
                    "unsupported": [p["summary"] for p in proposal.get("pending_profile_proposals", [])],
                    "note": proposal.get("ai_note") or "The request needs a fact the evidence registry does not hold, or is outside the editable fields."}
        applied = self.chats.resume_apply(job_id, preview["id"], request_id, draft["revision"])
        job = self._job(job_id)
        card = resume_card(job, applied.get("draft") or self.studio.get(job_id))
        assessment = applied.get("assessment") or {}
        card["coverage"] = (assessment.get("resume_coverage") or {}).get("score", card["coverage"])
        card["ats"] = (assessment.get("ats_readiness") or {}).get("score", card["ats"])
        return {"summary": f"Applied as v{card['revision']}", "applied": True, "diff": proposal.get("diff", ""),
                "change_set_id": preview["id"], "request_id": request_id, "source_revision": draft["revision"],
                "card": card, "coverage": card["coverage"], "ats": card["ats"], "note": proposal.get("ai_note") or ""}

    def undo_resume_change(self, job_id, change_set_id) -> dict:
        with self.w.connect() as db:
            row = db.execute("SELECT request_id FROM chat_change_sets WHERE id=? AND scope='resume' AND job_id=?", (change_set_id, job_id)).fetchone()
        if not row:
            raise ValueError("No such resume change for this job")
        draft = self.studio.get(job_id)
        restored = self.chats.resume_undo(job_id, change_set_id, row["request_id"], draft["revision"])
        return {"summary": f"Restored as v{restored.get('undo_revision')}", "revision": restored.get("undo_revision")}

    def sync_resume_projects(self, job_id) -> dict:
        draft = self.studio.get(job_id)
        synced = self.studio.sync_profile(job_id, draft["revision"])
        fields = synced.get("fields") or {}
        return {"summary": f"v{synced['revision']} · {fields.get('SelectedProjectTitle', '')} + {fields.get('SecondProjectTitle', '')}",
                "revision": synced["revision"], "note": "Call build_resume to fit and score the new version."}

    def cover_letter(self, job_id) -> dict:
        result = self.s.generate_cover_letter(job_id)
        return {"summary": f"Cover letter v{result['version']} saved for review", "path": result["path"], "version": result["version"],
                "excerpt": (result.get("content") or "")[:1200], "review_required": True}

    def application_documents(self, job_id) -> dict:
        job = self._job(job_id)
        documents = next((d for d in self.s.documents() if d["job_id"] == job_id), None) or {"resumes": [], "cover_letter": None}
        return {"summary": f"{len(documents['resumes'])} resume file(s)" + (", cover letter" if documents.get("cover_letter") else ""),
                "job": brief_job(job), **{k: documents.get(k) for k in ("resumes", "cover_letter")}}

    # ---- Agents ------------------------------------------------------------------
    def run_agent(self, kind, job_id) -> dict:
        if kind not in RUNNABLE_AGENTS:
            raise ValueError("Choose one of: " + ", ".join(RUNNABLE_AGENTS))
        job = self._job(job_id)
        run = self.runner.enqueue(kind, job_id, *self._engine_for(kind))
        return {"summary": f"{kind} for {job['company']} " + ("already running" if run.get("existing") else "queued"),
                "run_id": run["id"], "kind": kind, "job_id": job_id, "existing": bool(run.get("existing")),
                "agent": "resume" if kind == "resume_build" else kind}

    def _engine_for(self, kind) -> tuple:
        """The chat's own engine for a run it starts, so what the page says runs is what runs.

        Falls back to the gateway's routing when Settings gave the action its own
        provider, or when the chat's runtime cannot do the work (no web search).
        """
        from backend.ai import resolve_tiers
        from backend.services.agents import RUN_ACTIONS

        gateway = getattr(self.runner, "gateway", None)
        action = RUN_ACTIONS.get(kind)
        if gateway is None or action is None or action in (gateway.preferences().get("actions") or {}):
            return None, None
        provider, model = resolve_tiers(self.w.root, self.s.pref("ai_preferences", {}) or {})[0]["strong"]
        try:
            gateway.resolve(action, provider, model)
        except ValueError:
            return None, None
        return provider, model

    def _run(self, run_id):
        return next((r for r in self.s.runs() if r["id"] == run_id), None)

    @staticmethod
    def _run_brief(run) -> dict:
        result = run.get("result") if isinstance(run.get("result"), dict) else {}
        return {"run_id": run["id"], "kind": run["kind"], "job_id": run["job_id"], "state": run["state"],
                "stage": result.get("stage"), "error": run.get("error"), "updated_at": run["updated_at"],
                "result_summary": (result.get("summary") or "")[:600]}

    def wait_for_run(self, run_id, seconds=None) -> dict:
        limit = max(1, min(int(seconds or MAX_WAIT_SECONDS), MAX_WAIT_SECONDS))
        deadline = time.time() + limit
        run = self._run(run_id)
        if run is None:
            raise ValueError("No such run")
        while run["state"] in {"queued", "running"} and time.time() < deadline:
            time.sleep(2)
            run = self._run(run_id)
        brief = self._run_brief(run)
        if run["state"] in {"queued", "running"}:
            return {"summary": f"Still {run['state']} after {limit}s", **brief, "finished": False,
                    "note": "Reply now and tell the candidate it is still running; they can ask again in a few minutes."}
        return {"summary": run["state"] + (": " + (brief["result_summary"] or brief["error"] or "")[:120] if brief["result_summary"] or brief["error"] else ""),
                **brief, "finished": True}

    def agent_runs(self, job_id=None, limit=None) -> dict:
        runs = [r for r in self.s.runs() if not job_id or r["job_id"] == job_id][: max(1, min(int(limit or 10), 40))]
        return {"summary": f"{len(runs)} run(s)", "runs": [self._run_brief(r) for r in runs]}

    def run_result(self, run_id) -> dict:
        run = self._run(run_id)
        if run is None:
            raise ValueError("No such run")
        result = run.get("result") if isinstance(run.get("result"), dict) else {}
        shown = {}
        for key, value in result.items():
            if isinstance(value, str):
                shown[key] = value[:8000]
            elif isinstance(value, (int, float, bool)) or value is None:
                shown[key] = value
            elif isinstance(value, list):
                shown[key] = value[:20]
            else:
                shown[key] = json.dumps(value, default=str)[:2000]
        return {"summary": run["state"], **self._run_brief(run), "result": shown}

    # ---- Profile -------------------------------------------------------------------
    def profile_overview(self) -> dict:
        items = self.s.knowledge()
        by_kind: dict[str, int] = {}
        for item in items:
            by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        personal = {i["id"]: i["summary"] for i in items if i["kind"] == "personal"}
        pending = self.s.pending_knowledge()
        questions = self.w.root / QUESTIONS_FILE
        open_questions = len(re.findall(r"^- \[ \]", questions.read_text(encoding="utf-8"), re.M)) if questions.exists() else 0
        return {"summary": f"{len(items)} entries · {len(pending)} pending review", "entries_by_kind": by_kind,
                "registered": sum(i["review_state"] == "registered" for i in items),
                "pending_review": pending, "profile_has_unreviewed_edits": self.s.profile_dirty(),
                "target_roles": personal.get("personal:target_roles", ""), "current_status": personal.get("personal:current_status", ""),
                "location_preferences": personal.get("personal:location_preferences", ""), "most_recent_role": personal.get("personal:most_recent_role", ""),
                "open_questions": open_questions, "evidence_revision": self.w.evidence().get("candidate_revision")}

    def search_profile(self, query=None, kind=None) -> dict:
        items = [i for i in self.s.knowledge() if not kind or i["kind"] == kind]
        if query:
            words = [w for w in re.findall(r"[a-z0-9+#.]+", query.casefold()) if len(w) > 1]
            items = [i for i in items if any(w in (i["title"] + " " + i["summary"]).casefold() for w in words)]
        return {"summary": f"{len(items)} entr(ies)", "items": [
            {"id": i["id"], "kind": i["kind"], "title": i["title"], "summary": i["summary"][:300],
             "review_state": i["review_state"]} for i in items[:40]]}

    def get_profile_item(self, id) -> dict:
        item = next((i for i in self.s.knowledge(True) if i["id"] == id), None)
        if not item:
            raise ValueError("No profile entry has that ID; call search_profile")
        return {"summary": f"{item['kind']}: {item['title']} ({item['review_state']})", **{k: item[k] for k in ("id", "kind", "title", "summary", "review_state", "source", "revision")},
                "deleted": bool(item["deleted"]), "details": json.loads(json.dumps(item["data"], default=str))}

    def propose_profile_change(self, request) -> dict:
        request_id = "chat-" + uuid.uuid4().hex[:12]
        preview = self.chats.profile_preview(request, request_id, self.s.profile_revision())
        proposed = preview["proposed_changes"]
        return {"summary": f"{len(proposed['changes'])} proposal(s)", "change_set_id": preview["id"], "changes": proposed["changes"],
                "note": proposed.get("ai_note") or "", "next": "Show the candidate the proposals; apply_profile_change needs their yes."}

    def apply_profile_change(self, change_set_id) -> dict:
        with self.w.connect() as db:
            row = db.execute("SELECT request_id, source_revision FROM chat_change_sets WHERE id=? AND scope='profile'", (change_set_id,)).fetchone()
        if not row:
            raise ValueError("No such profile proposal")
        applied = self.chats.profile_apply(change_set_id, row["request_id"], row["source_revision"])
        return {"summary": f"{len(applied['evidence_ids'])} entr(ies) changed; new entries wait for the candidate's review",
                "entry_ids": applied["evidence_ids"], "profile_has_unreviewed_edits": self.s.profile_dirty()}

    def reconcile_profile(self, ids=None) -> dict:
        result = self.s.reconcile_knowledge(ids or None)
        return {"summary": f"{len(result['reconciled'])} entr(ies) confirmed", **result}

    def open_questions(self) -> dict:
        path = self.w.root / QUESTIONS_FILE
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return {"summary": f"{len(re.findall(r'^- \\[ \\]', text, re.M))} open", "text": text[:8000]}

    def add_question(self, question) -> dict:
        from career import atomic_write

        path = self.w.root / QUESTIONS_FILE
        text = path.read_text(encoding="utf-8") if path.exists() else "# Questions for you\n"
        heading = "## Asked from the chat"
        entry = f"- [ ] **{self.s.today()}** — {question.strip()}\n"
        if heading not in text:
            text = text.rstrip("\n") + "\n\n---\n\n" + heading + "\n\n" + entry
        else:
            text = text.rstrip("\n") + "\n" + entry
        atomic_write(path, text)
        with self.w.connect() as db:
            self.w.record_event(db, "question_recorded", file=QUESTIONS_FILE, question=question.strip()[:300])
        return {"summary": "Recorded in QUESTIONS-FOR-YOU.md", "file": QUESTIONS_FILE}

    # ---- Goals, mail, settings, policy ------------------------------------------------
    def status(self) -> dict:
        summary = self.s.summary()
        goals, counts = summary["goals"], summary["counts"]
        ready = [brief_job(j) for j in summary["jobs"] if j["status"] in {"saved", "prepared"}][:12]
        active = [self._run_brief(r) for r in summary["runs"] if r["state"] in {"queued", "running"}]
        return {"summary": f"Week {goals['week_completed']}/{goals['current_week_target']} · today {goals['today_completed']}/{goals['today_target']}",
                "goals": {k: goals[k] for k in ("weekly_target", "current_week_target", "week_completed", "today_target", "today_completed", "remaining_today")},
                "counts": counts, "resumes_waiting_to_be_sent": ready, "active_runs": active,
                "profile_has_unreviewed_edits": summary["profile_dirty"], "today": self.s.today()}

    def get_goals(self) -> dict:
        goals = self.s.goals()
        return {"summary": f"{goals['weekly_target']} per week", **goals}

    def set_goals(self, weekly_target, workdays=None, start_date=None) -> dict:
        current = self.s.pref("goals") or {}
        days = [int(d) for d in (workdays or current.get("workdays") or [0, 1, 2, 3, 4])]
        saved = self.s.save_goals({"weekly_target": int(weekly_target), "workdays": days, "start_date": start_date or current.get("start_date") or self.s.today()})
        return {"summary": f"{saved['weekly_target']} per week on {len(saved['settings']['workdays'])} days", "goals": saved["settings"]}

    def list_mail(self, state=None) -> dict:
        mail = self.s.mail()
        messages = [m for m in mail["messages"] if not state or m["state"] == state]
        return {"summary": f"{len(messages)} email(s)", "connection": mail["connection"], "messages": [
            {k: m.get(k) for k in ("id", "job_id", "company", "role", "kind", "subject", "received_at", "submission_date", "confidence", "state", "excerpt")}
            for m in messages[:30]]}

    def resolve_mail(self, id, action, job_id=None, create_application=False) -> dict:
        result = self.s.resolve_mail(id, job_id=job_id, action=action, create_application=bool(create_application))
        return {"summary": f"{action}: {result.get('state', 'done')}", "result": result}

    def ai_settings(self) -> dict:
        from backend.ai import engine, paid_gate, ready_providers, router

        chosen = engine(self.s)
        ready = ready_providers(self.w.root)
        # Auto's route as the Settings page shows it: order, resting plans, each plan's 5-hour window.
        route = [{k: row[k] for k in ("label", "enabled", "ready", "paid", "models", "resting", "usage")}
                 for row in router.status(self.w.root, router.policy_from(self.s.pref("ai_preferences", {}) or {}),
                                          ready_map=ready, paid={"block": paid_gate(self.s)("azure_openai")})]
        return {"summary": "Assistant runs on " + chosen["label"], "assistant_engine": chosen,
                "ready_providers": [name for name, ok in ready.items() if ok], "auto_route": route,
                "budget": self.runner.cache.stats(), "discovery_preset": (self.s.pref("discovery_preferences", {}) or {}).get("preset", "default")}

    def set_discovery_preset(self, preset) -> dict:
        self.s.set_pref("discovery_preferences", {"preset": preset})
        self.s.sync_projections()
        return {"summary": "Search mix: " + preset, "preset": preset}

    def read_policy(self, topic=None) -> dict:
        path = self.w.root / POLICY_FILE
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        sections, current, title = {}, [], "Overview"
        for line in text.splitlines():
            if line.startswith("## "):
                sections[title] = "\n".join(current).strip()
                title, current = line[3:].strip(), []
            else:
                current.append(line)
        sections[title] = "\n".join(current).strip()
        if topic:
            words = topic.casefold().replace("reapply", "re-apply")
            hit = next((name for name in sections if words in name.casefold()), None)
            if hit:
                return {"summary": hit, "section": hit, "text": sections[hit][:6000]}
        return {"summary": f"{len(sections)} sections", "sections": list(sections), "text": sections.get("Overview", "")[:3000]}

    def recent_activity(self, limit=None) -> dict:
        rows = self.w.activity(max(1, min(int(limit or 20), 60)))
        return {"summary": f"{len(rows)} event(s)", "events": [
            {"action": r["action"], "at": r["occurred_at"], "job_id": r.get("job_id"),
             "details": json.dumps(r.get("details") or {}, default=str)[:300]} for r in rows]}
