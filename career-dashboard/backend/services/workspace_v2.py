"""Application services. SQLite owns mutable state; original evidence is preserved."""

from __future__ import annotations
import hashlib, json, re, threading, uuid
from datetime import datetime, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.ai_marks import clean_text
from backend.services.demo import demo_mode
from backend.services.planning import plan
from backend.services.postings import canonical_url, posting_key

KINDS = {
    "personal",
    "skill",
    "project",
    "experience",
    "education",
    "certification",
    "fact",
}
AGENTS = [
    {'id': 'orchestrator', 'name': 'Main orchestrator', 'reads': 'Saved instructions, job IDs, draft versions and worker states', 'profile_access': True,
     'does': 'Serializes build/score and AI work, persists progress and failures, deduplicates active runs, recovers interrupted work and enforces the shared AI budget.',
     'implementation': 'Deterministic dispatcher and monitor; no monitoring AI calls', 'guide': 'backend/services/agents.py'},
    {'id': 'assistant', 'name': 'Assistant agent', 'reads': 'Your messages, the workspace snapshot and every tool result', 'profile_access': True,
     'does': 'One place to talk to the workspace. A pasted posting goes through the market eligibility and duplicate gates, is saved, drafted and fitted to the active resume contract with the PDF handed back. Everything else is a task: the agent plans, calls the workspace tools (jobs, resume, agents, profile, goals, mail, settings), pauses for your yes on anything hard to undo, and reports what changed.',
     'implementation': 'Deterministic paths for postings and shortcuts; an agent loop (one structured decision per turn) on Claude Code, Codex or a keyed provider, over services/assistant_tools.py', 'guide': 'backend/services/assistant.py'},
    {'id': 'profile', 'name': 'Profile curator', 'reads': 'Profile entries, the evidence registry and your words about yourself', 'profile_access': True,
     'does': 'Turns what you tell it ("I finished the AWS course") into profile proposals that wait for your review, keeps the review states that gate new drafts, and records questions only you can answer in QUESTIONS-FOR-YOU.md.',
     'implementation': 'Revision-bound change sets in SQLite; the profile_curator specialist for plain-English requests; never writes data/context except the questions file', 'guide': 'backend/chat_changes.py'},
    {'id': 'instruction_tracker', 'name': 'Resume instruction thread',
     'reads': 'Your messages and the selected draft', 'profile_access': True,
     'does': 'Retains every request and applies explicit safe commands to a versioned draft. New candidate facts wait in Profile for evidence review.',
     'implementation': 'Deterministic commands and SQLite; zero AI calls',
     'guide': 'backend/services/instruction_tracker.py'},
    {'id': 'resume_advisor', 'name': 'Independent resume advisor',
     'reads': 'Saved job description and dated public company research only', 'profile_access': False,
     'does': 'Suggests role-specific resume evidence and potential portfolio projects without claiming the candidate has done them.',
     'implementation': 'Two isolated AI calls; no candidate profile access',
     'guide': 'backend/workflows/agents/resume-advisor.md'},
    {'id': 'resume_match', 'name': 'Independent resume matcher', 'reads': 'Finished PDF text and saved JD only', 'profile_access': False,
     'does': 'Scores document term coverage for free; optional cached AI review explains matches and gaps independently.',
     'implementation': 'Pure document scorer plus optional isolated Codex process', 'guide': 'backend/services/resume_match.py'},
    {
        "id": "fit", "name": "Requirement check",
        "reads": "The posting and your registered evidence (skills, projects, experience, education)", "profile_access": True,
        "does": "Lists what each job asks for, quoting the posting's own sentence, and marks each item met, partial or missing with the evidence that shows it. The fit score, the tailor, resume coverage and the study plan all use this one list.",
        "implementation": "The fit_analyst specialist on a free plan only (Kimi, Codex or Claude, never a paid key), verified in code: an excerpt must be in the posting and an evidence id in the registry; rules when no free plan is free",
        "guide": "backend/services/fit.py",
    },
    {
        "id": "resume_tracker", "name": "Agent 2 · Resume tracker",
        "reads": "User edits to resume project fields and skills", "profile_access": True,
        "does": "Tracks versions, flags missing projects, and captures new projects/skills in Profile for evidence review.",
        "implementation": "Deterministic rules in the shared database on every save",
        "guide": "backend/services/resume_studio.py",
    },
    {
        "id": "research",
        "name": "Company researcher",
        "reads": "Saved job description and public company sources",
        "profile_access": False,
        "does": "Investigates the business, role outcomes, team, skills and company projects; cites sources and labels uncertainty.",
        "implementation": "Independent Codex run with web search",
        "guide": "backend/workflows/agents/company-researcher.md",
    },
    {
        "id": "hiring",
        "name": "Independent hiring manager",
        "reads": "Job description and completed company research only",
        "profile_access": False,
        "does": "Defines expected skills, experience, convincing project evidence and interview preparation. It cannot promise a shortlist.",
        "implementation": "Fresh isolated Codex run; no profile, files, email or previous conversation",
        "guide": "backend/workflows/agents/hiring-manager.md",
    },
    {
        "id": "match",
        "name": "Profile comparison",
        "reads": "Your active profile and the independent hiring review",
        "profile_access": True,
        "does": "Separates supported strengths, partial evidence, missing skills and practical next steps.",
        "implementation": "Separate Codex run after the independent review",
        "guide": "backend/workflows/agents/profile-comparison.md",
    },
    {
        "id": "discovery",
        "name": "Job discovery",
        "reads": "Active skills, experience, role preferences and previously seen job IDs",
        "profile_access": True,
        "does": "Finds current postings in the selected markets and target roles, quotes work-permit or sponsorship wording verbatim, verifies requirements and removes previously seen postings. The 'portals' mix reads tracked companies' ATS feeds with no AI call.",
        "implementation": "Claude Code or Codex with web search, or direct Greenhouse/Lever/Ashby feeds; persistent unique posting store",
        "guide": "backend/workflows/agents/job-discovery.md",
    },
    {
        "id": "sponsorship",
        "name": "Sponsorship gate",
        "reads": "Each posting's own words, employer identity and applicable public sponsorship data",
        "profile_access": False,
        "does": "Checks the posting's market-specific work-permit and sponsorship requirements, logging the exact sentence behind a decision. Silence alone does not establish ineligibility.",
        "implementation": "Deterministic market rules and, where available, a public sponsor-history index; zero AI calls",
        "guide": "backend/services/sponsorship.py",
    },
    {
        "id": "reapply",
        "name": "Never-re-apply tracker",
        "reads": "Saved, removed, excluded and rejected jobs with their dates",
        "profile_access": False,
        "does": "Deduplicates exact postings and applies the selected profile's reapplication rules. Searches never suggest a role again that you applied for (a repost included) or removed, and learn from why you removed one: a company you ruled out, or a job title that was the wrong kind of role. Application status changes require explicit evidence; silence does not change status.",
        "implementation": "Deterministic rules over the jobs table; zero AI calls",
        "guide": "backend/services/reapply.py",
    },
    {
        "id": "study_plan",
        "name": "Study planner",
        "reads": "Saved JD, the requirement check's genuine gaps and registered skill/project titles",
        "profile_access": True,
        "does": "Writes study-plan.md for this company: what they will probe and what to learn before they call. Everything in it is a skill not yet held; it never reaches the resume.",
        "implementation": "One AI call without web; writes into the application folder",
        "guide": "backend/workflows/agents/study-planner.md",
    },
    {
        "id": "email",
        "name": "Email evidence reviewer",
        "reads": "Job-related Gmail messages and the saved company/role/URL list",
        "profile_access": False,
        "does": "Finds confirmations and status updates; uncertain matches wait for review. No sending or mailbox changes.",
        "implementation": "Connected Gmail read tools through Codex; application service validates and applies evidence",
        "guide": "backend/workflows/agents/email-reviewer.md",
    },
    {
        "id": "resume",
        "name": "Resume validator",
        "reads": "Approved registry, profile, selected JD and generated PDF",
        "profile_access": True,
        "does": "Checks evidence, the selected market's page and paper contract, relevant registered projects when present, and current artifact hashes.",
        "implementation": "Python evidence validation; versioned Resume Studio drafts and PDF previews",
        "guide": "backend/workflows/TAILORING.md",
    },
]


# The agents a candidate can switch on and off from the Agents page. Ids are the run kinds
# in services/agents.RUN_KINDS; helpers not listed here (sponsorship gate, posting
# checks, never-re-apply) are deterministic, free and always on.
AGENT_SWITCHES = [
    {"id": "discovery", "label": "Job search", "uses_ai": True,
     "description": "Finds new postings in selected markets that pass eligibility and duplicate checks. The daily search uses this too."},
    {"id": "research", "label": "Company & hiring research", "uses_ai": True,
     "description": "Researches the employer, what the hiring manager will look for, and how your profile fits."},
    {"id": "resume_advisor", "label": "Independent resume advisor", "uses_ai": True,
     "description": "Suggests resume evidence for one role using its posting and public company research only."},
    {"id": "instruction_interpret", "label": "Resume instruction interpretation", "uses_ai": True,
     "description": "Turns an instruction in the saved Resume Studio thread into safe, reviewable draft edits."},
    {"id": "study_plan", "label": "Study plan", "uses_ai": True,
     "description": "Writes the interview preparation notes for one company. Never touches the resume."},
    {"id": "resume_match", "label": "Independent resume review", "uses_ai": True,
     "description": "Reads only the finished PDF and the posting, then reports matches and gaps."},
    {"id": "email", "label": "Gmail sync", "uses_ai": True,
     "description": "Reads Gmail for application confirmations and replies. Also runs on the mail schedule."},
    {"id": "resume_build", "label": "Resume build & ATS check", "uses_ai": False,
     "description": "Compiles the current draft into its contract-sized PDF and scores it."},
]


def agents_for(root) -> list[dict]:
    """The agent list in this profile's country, resume and tracking terms."""
    from backend.countries import pack_for, target_markets_for
    from backend.resume_contract import contract_for

    pack = pack_for(root)
    contract = contract_for(Path(root))
    markets = target_markets_for(root)
    default_profile = (Path(root) / "backend").is_dir()
    out = [dict(agent) for agent in AGENTS if default_profile or agent["id"] != "email"]
    profile = {}
    try:
        import yaml
        profile = yaml.safe_load((Path(root) / "data/config/profile.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        pass
    reapply = profile.get("reapply") or {}
    shape = (f"the {contract.describe_pages()} {pack.paper_label} layout" if len(markets) == 1 else
             "the job market's resume contract")
    for agent in out:
        if agent["id"] == "assistant":
            agent["does"] = agent["does"].replace("the active resume contract", shape)
        elif agent["id"] == "discovery":
            names = ["Ireland" if market == "ie" else "the US" for market in markets]
            agent["does"] = agent["does"].replace("the selected markets", " and ".join(names))
        elif agent["id"] == "sponsorship" and len(markets) == 1 and pack.sponsor_index != "uscis":
            cannot = (pack.data.get("discovery") or {}).get("cannot_hire") or "citizenship or a clearance"
            agent.update(
                name="Work-permit gate",
                reads="Each posting's own words, the employer name and domain",
                does=f"Excludes postings that refuse to support a work permit or require {cannot}, logging the exact "
                     "sentence; ranks the rest A (says yes) or C (silent). Never excludes for silence.",
                implementation="Deterministic patterns in data/config/sponsorship.yml; zero AI calls",
            )
        elif agent["id"] == "reapply" and not reapply.get("block_same_role", True):
            agent.update(
                name="Duplicate and status guard",
                reads="Saved, removed, excluded and rejected jobs with their recorded dates",
                does="Deduplicates exact postings and preserves recorded application states. Searches never suggest a role again that you applied for or removed, and learn from why you removed one. It never turns silence into a rejection or ghosted status.",
                implementation="Deterministic posting identities and explicit status evidence; zero AI calls",
            )
        elif agent["id"] == "resume":
            agent["does"] = agent["does"].replace("the selected market's page and paper contract", shape)
    return out


def agent_switches_for(root) -> list[dict]:
    """AGENT_SWITCHES in this profile's terms (see agents_for)."""
    from backend.countries import pack_for, target_markets_for
    from backend.resume_contract import contract_for

    pack = pack_for(root)
    contract = contract_for(Path(root))
    markets = target_markets_for(root)
    default_profile = (Path(root) / "backend").is_dir()
    out = [dict(switch) for switch in AGENT_SWITCHES if default_profile or switch["id"] != "email"]
    for switch in out:
        if switch["id"] == "discovery":
            location = pack.adjective if len(markets) == 1 else "Ireland and US"
            switch["description"] = (f"Finds new {location} postings that pass the work-permit and duplicate "
                                     "gates. The daily search uses this too.")
        elif switch["id"] == "resume_build":
            if len(markets) == 1:
                switch["description"] = f"Compiles the current draft into a {contract.describe_pages()} {pack.paper_label} PDF and scores it."
            else:
                switch["description"] = "Compiles the current draft into a PDF for the job's market and scores it."
    return out


def agent_switch_label(kind):
    for switch in AGENT_SWITCHES:
        if switch["id"] == kind:
            return switch["label"]
    return str(kind).replace("_", " ")


class CareerServices:
    def __init__(self, workspace):
        self.w = workspace
        self._posting_lock = threading.RLock()
        projections_changed = False
        with self.w.connect() as db:
            db.executescript(
                """
            CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS knowledge(id TEXT PRIMARY KEY,kind TEXT NOT NULL,title TEXT NOT NULL,summary TEXT NOT NULL,data TEXT NOT NULL,source TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,deleted INTEGER NOT NULL DEFAULT 0,review_state TEXT NOT NULL DEFAULT 'registered',updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mail_evidence(id TEXT PRIMARY KEY,job_id TEXT REFERENCES jobs(id),company TEXT NOT NULL,role TEXT NOT NULL,kind TEXT NOT NULL,subject TEXT NOT NULL,sender TEXT NOT NULL,received_at TEXT NOT NULL,submission_date TEXT,excerpt TEXT NOT NULL,reason TEXT NOT NULL,confidence TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,job_id TEXT REFERENCES jobs(id),state TEXT NOT NULL,input TEXT NOT NULL,result TEXT,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_agent_runs_state ON agent_runs(state,created_at);
            CREATE TABLE IF NOT EXISTS posting_identities(identity TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id));
            CREATE TABLE IF NOT EXISTS application_evidence(job_id TEXT PRIMARY KEY REFERENCES jobs(id),source TEXT NOT NULL,confirmed_at TEXT NOT NULL,submission_date TEXT,message_id TEXT);
            CREATE TABLE IF NOT EXISTS cover_letters(job_id TEXT NOT NULL REFERENCES jobs(id),version INTEGER NOT NULL,content TEXT NOT NULL,path TEXT NOT NULL,created_at TEXT NOT NULL,evidence_revision TEXT NOT NULL,PRIMARY KEY(job_id,version));
            """
            )
            for j in self.w.jobs():
                db.execute(
                    "INSERT OR IGNORE INTO posting_identities VALUES(?,?)",
                    (posting_key(j["url"]), j["id"]),
                )
                from backend.job_quality import company_id, normalize_company
                cid = company_id(j["company"])
                db.execute(
                    "INSERT OR IGNORE INTO companies(id,normalized_name,display_name,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (cid, normalize_company(j["company"]), j["company"], self.now(), self.now()),
                )
                db.execute("UPDATE jobs SET company_id=COALESCE(company_id,?) WHERE id=?", (cid, j["id"]))
            if not db.execute("SELECT 1 FROM preferences WHERE key='goals'").fetchone():
                self.set_pref(
                    "goals",
                    {
                        "weekly_target": 30,
                        "workdays": [0, 1, 2, 3, 4, 5],
                        "start_date": self.today(),
                    },
                    db,
                )
            if not db.execute(
                "SELECT 1 FROM preferences WHERE key='profile_initialized'"
            ).fetchone():
                self.seed_profile(db)
            for field in ("target_roles", "location_preferences"):
                config = self.w.profile().get(field, {})
                db.execute(
                    "INSERT OR IGNORE INTO knowledge(id,kind,title,summary,data,source,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        "personal:" + field,
                        "personal",
                        field.replace("_", " ").capitalize(),
                        json.dumps(config, ensure_ascii=False, indent=2),
                        json.dumps({"field": field, "value": config}),
                        "data/config/profile.yml > " + field,
                        self.now(),
                    ),
                )
            # Enrich only untouched imported entries; user edits always win.
            for claim in self.w.evidence()["claims"]:
                facts = claim.get("approved_facts", [])
                if facts:
                    db.execute(
                        "UPDATE knowledge SET title=?,summary=? WHERE id=? AND review_state='registered' AND summary=''",
                        (
                            claim.get("title") or facts[0][:150],
                            "\n".join(facts),
                            claim["id"],
                        ),
                    )
            projections_changed = self.retire_malformed_skill_fragments(db)
        if projections_changed:
            self.sync_projections()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def today(self):
        # The profile's own time zone (profile.yml candidate.timezone).
        return datetime.now(ZoneInfo(self.w.timezone)).date().isoformat()

    def gate(self, company, text, url="", location="", extra_sentences=None, employer_type="", market=""):
        """The sponsorship / work-permit gate with this profile's own rules and employer index."""
        from backend.countries import market_for_location, require_known_authorization
        from backend.services import sponsorship

        market = market_for_location(self.w.root, location, market)
        require_known_authorization(self.w.root, market)
        return sponsorship.evaluate(
            company, text, url, location, extra_sentences=extra_sentences, employer_type=employer_type,
            rules=sponsorship.rules_for(self.w.root, market), sponsor_index=sponsorship.index_for(self.w.root, market),
        )

    def retire_malformed_skill_fragments(self, db):
        """Soft-retire fragments created by the former comma-based parser."""
        if db.execute("SELECT 1 FROM preferences WHERE key='skill_fragment_cleanup_v1'").fetchone():
            return False
        rows = db.execute(
            "SELECT * FROM knowledge WHERE kind='skill' AND deleted=0 AND source LIKE 'User edit in Resume Studio%'"
        ).fetchall()
        retired = []
        for row in rows:
            title = row["title"]
            if title.count("(") == title.count(")"):
                continue
            db.execute(
                "UPDATE knowledge SET deleted=1,revision=revision+1,review_state='retired_malformed',updated_at=? WHERE id=?",
                (self.now(), row["id"]),
            )
            retired.append(row["id"])
        self.set_pref("skill_fragment_cleanup_v1", {"retired_ids": retired, "applied_at": self.now()}, db)
        if retired:
            self.w.record_event(db, "malformed_skill_fragments_retired", entry_ids=retired, recoverable=True)
        return bool(retired)

    def pref(self, key, default=None):
        with self.w.connect() as db:
            r = db.execute(
                "SELECT value FROM preferences WHERE key=?", (key,)
            ).fetchone()
        return json.loads(r[0]) if r else default

    def set_pref(self, key, value, db=None):
        if db is None:
            with self.w.connect() as db:
                self.set_pref(key, value, db)
        else:
            db.execute(
                "INSERT INTO preferences VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def agent_enabled(self, kind):
        """An agent runs only when its switch on the Agents page is on (the default)."""
        switches = self.pref("agent_enabled", {}) or {}
        return switches.get(kind, True) is not False

    def set_agent_enabled(self, kind, enabled):
        from backend.services.agents import RUN_KINDS

        if kind not in RUN_KINDS:
            raise ValueError("Unknown agent")
        switches = self.pref("agent_enabled", {}) or {}
        switches[kind] = bool(enabled)
        self.set_pref("agent_enabled", switches)
        with self.w.connect() as db:
            self.w.record_event(db, "agent_switch_updated", kind=kind, enabled=bool(enabled))
        self.export_state()
        return self.agent_switch_settings()

    def agent_switch_settings(self):
        """The Agents page switches: on/off state plus what a run usually costs.

        Token counts come from ai_calls where the runtime reported them; local
        runtimes often record none, so est_tokens stays None and the page says so.
        """
        from backend.services.agents import RUN_ACTIONS

        with self.w.connect() as db:
            usage = {
                row["kind"]: dict(row)
                for row in db.execute(
                    """SELECT r.kind AS kind, COUNT(DISTINCT r.id) AS runs,
                       SUM(CASE WHEN json_extract(e.detail,'$.fresh') THEN 1 ELSE 0 END) AS fresh
                       FROM agent_runs r
                       LEFT JOIN agent_run_events e ON e.run_id=r.id AND e.kind='ai_call'
                       WHERE r.state='completed' GROUP BY r.kind"""
                )
            }
            try:
                tokens = {
                    row[0]: row[1]
                    for row in db.execute(
                        """SELECT action, AVG(input_tokens + output_tokens) FROM ai_calls
                        WHERE state='completed' AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL
                        GROUP BY action"""
                    )
                }
            except Exception:  # noqa: BLE001 - an older database may lack the token columns
                tokens = {}
        settings = []
        for switch in agent_switches_for(self.w.root):
            kind = switch["id"]
            stats = usage.get(kind) or {}
            runs = stats.get("runs") or 0
            fresh = stats.get("fresh") or 0
            avg_calls = round(fresh / runs, 1) if runs else None
            per_call = tokens.get(RUN_ACTIONS.get(kind))
            est_tokens = round(per_call * avg_calls) if per_call and avg_calls else None
            settings.append(
                {
                    **switch,
                    "enabled": self.agent_enabled(kind),
                    "runs": runs,
                    "avg_ai_calls": avg_calls if switch["uses_ai"] else 0,
                    "est_tokens": est_tokens,
                }
            )
        return settings

    def seed_profile(self, db):
        profile = self.w.profile()
        evidence = self.w.evidence()

        def insert(id, kind, title, summary, data, source):
            # An entry removed on the Profile page stays in the registry on hold as the record.
            db.execute(
                "INSERT OR IGNORE INTO knowledge(id,kind,title,summary,data,source,deleted,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    id,
                    kind,
                    str(title),
                    str(summary),
                    json.dumps(data, ensure_ascii=False),
                    source,
                    1 if isinstance(data, dict) and data.get("profile_removed") else 0,
                    self.now(),
                ),
            )

        for key, value in profile["candidate"].items():
            insert(
                "personal:" + key,
                "personal",
                key.replace("_", " ").capitalize(),
                value,
                {"field": key, "value": value},
                "data/config/profile.yml > candidate > " + key,
            )
        for claim in evidence["claims"]:
            cat = claim.get("category", "fact")
            kind = {
                "employment": "experience",
                "education": "education",
                "certification": "certification",
                "skill": "skill",
            }.get(cat, "fact")
            title = (
                claim.get("title")
                or claim.get("institution")
                or claim.get("value")
                or (claim.get("approved_facts") or [claim["id"]])[0][:150]
            )
            summary = (
                claim.get("value")
                or claim.get("value_as_supplied")
                or "\n".join(claim.get("approved_facts", []))
                or " · ".join(
                    str(claim[k])
                    for k in ("employer", "dates", "degree_as_supplied", "status_text")
                    if k in claim
                )
            )
            insert(
                claim["id"],
                kind,
                title,
                summary,
                claim,
                "data/context/evidence.yml > " + claim["id"],
            )
        for project in evidence["projects"]:
            content = project.get("resume_content", {})
            insert(
                project["id"],
                "project",
                content.get("title") or project.get("name") or project["id"],
                "\n".join(content.get("bullets", [])),
                project,
                "data/context/evidence.yml > " + project["id"],
            )
        # Seed known technologies only from the existing explicit skills claims.
        for claim in evidence["claims"]:
            if "skill" in claim.get("category", "") or claim["id"].startswith("SKILL"):
                for skill in claim.get("values", claim.get("skills", [])):
                    if isinstance(skill, str):
                        insert(
                            "skill:" + hashlib.sha256(skill.encode()).hexdigest()[:12],
                            "skill",
                            skill,
                            claim.get("approved_external_use", ""),
                            {"value": skill, "evidence_id": claim["id"]},
                            "data/context/evidence.yml > " + claim["id"],
                        )
        self.set_pref("profile_initialized", True, db)

    def knowledge(self, include_deleted=False):
        with self.w.connect() as db:
            rows = db.execute(
                "SELECT * FROM knowledge "
                + ("" if include_deleted else "WHERE deleted=0 ")
                + "ORDER BY kind,title"
            ).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def save_knowledge(self, item, id=None):
        kind = item["kind"]
        fields = item.get("fields")
        title = (item.get("title") or "").strip()
        summary = (item.get("summary") or "").strip()
        if kind not in KINDS or (fields is None and not title):
            raise ValueError("Choose a category and enter a title")
        if len(title) > 250 or len(summary) > 30000:
            raise ValueError("Profile entry is too long")
        sync = synced = None
        try:
            with self.w.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                old = (
                    db.execute("SELECT * FROM knowledge WHERE id=?", (id,)).fetchone()
                    if id
                    else None
                )
                if id and not old:
                    raise ValueError("Profile entry not found")
                if old and item.get("revision") != old["revision"]:
                    raise ValueError("This entry changed elsewhere. Reload before saving.")
                key = id or "user:" + uuid.uuid4().hex[:16]
                source = old["source"] if old else "User supplied in Profile"
                review_state = "user_updated"
                if fields is not None:
                    # A Profile form save: the named fields decide title, summary and data together.
                    from backend.services.profile_fields import apply_fields
                    from backend.services.profile_sync import ProfileSync

                    if old and old["kind"] != kind:
                        raise ValueError("A saved entry keeps its category. Add a new entry instead.")
                    current = {**dict(old), "data": json.loads(old["data"])} if old else None
                    title, summary, data = apply_fields(kind, fields, current)
                    if not title:
                        raise ValueError("Choose a category and enter a title")
                    if len(title) > 250 or len(summary) > 30000:
                        raise ValueError("Profile entry is too long")
                    # The candidate's own form save is their record of the fact: it goes everywhere now,
                    # with no review step. Changes from the chat or Studio still wait for Confirm.
                    sync = ProfileSync(self, db)
                    data = sync.row_saved(
                        {"id": key, "kind": kind, "title": title, "summary": summary, "data": data, "source": source},
                        current["data"] if current else None,
                    )
                    review_state = "registered"
                else:
                    data = item.get("data", json.loads(old["data"]) if old else {})
                if not isinstance(data, dict):
                    raise ValueError("Entry details must be an object")
                if fields is None and old and old["kind"] == "personal":
                    data = {**data, "value": summary}
                revision = old["revision"] + 1 if old else 1
                db.execute(
                    "INSERT INTO knowledge VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,title=excluded.title,summary=excluded.summary,data=excluded.data,revision=excluded.revision,deleted=0,review_state=excluded.review_state,updated_at=excluded.updated_at",
                    (
                        key,
                        kind,
                        title,
                        summary,
                        json.dumps(data, ensure_ascii=False),
                        source,
                        revision,
                        0,
                        review_state,
                        self.now(),
                    ),
                )
                self.w.record_event(
                    db,
                    "profile_entry_saved",
                    entry_id=key,
                    before=dict(old) if old else None,
                    after=item,
                )
                self.bump_profile_revision(db)
                if sync:
                    synced = sync.finish()
                    sync.write()
        except Exception:
            if sync:
                sync.restore()
            raise
        if sync:
            sync.done()
        self.export_profile()
        self.w.export_tracking()
        saved = next(i for i in self.knowledge() if i["id"] == key)
        return {**saved, "synced": synced} if sync else saved

    def delete_knowledge(self, id, propagate=False):
        """Remove an entry. From the Profile page (propagate) it leaves every file and draft
        at once; from the chat or the CLI it waits for Confirm like any other suggestion."""
        sync = synced = None
        try:
            with self.w.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                old = db.execute(
                    "SELECT * FROM knowledge WHERE id=? AND deleted=0", (id,)
                ).fetchone()
                if not old:
                    raise ValueError("Profile entry not found")
                if propagate:
                    from backend.services.profile_fields import label_for
                    from backend.services.profile_sync import ProfileSync

                    row = {**dict(old), "data": json.loads(old["data"])}
                    sync = ProfileSync(self, db)
                    sync.row_removed(row, label_for(row))
                db.execute(
                    "UPDATE knowledge SET deleted=1,revision=revision+1,review_state=?,updated_at=? WHERE id=?",
                    ("registered" if propagate else "user_updated", self.now(), id),
                )
                self.w.record_event(
                    db, "profile_entry_removed", entry_id=id, before=dict(old)
                )
                self.bump_profile_revision(db)
                if sync:
                    synced = sync.finish()
                    sync.write()
        except Exception:
            if sync:
                sync.restore()
            raise
        if sync:
            sync.done()
        self.export_profile()
        self.w.export_tracking()
        return {"deleted": True, "synced": synced}

    def restore_knowledge(self, id):
        """Keep an entry whose removal was suggested but not confirmed."""
        with self.w.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM knowledge WHERE id=? AND deleted=1 AND review_state='user_updated'", (id,)
            ).fetchone()
            if not old:
                raise ValueError("This entry is not waiting to be removed. Reload the Profile.")
            db.execute(
                "UPDATE knowledge SET deleted=0,revision=revision+1,updated_at=? WHERE id=?",
                (self.now(), id),
            )
            self.w.record_event(db, "profile_entry_kept", entry_id=id)
        # Kept as it stands now: confirming it writes any other pending wording through too.
        return self.reconcile_knowledge([id])

    def pending_knowledge(self):
        """Entries edited by the user (including removals) that still block new drafts."""
        return [
            {"id": i["id"], "kind": i["kind"], "title": i["title"], "deleted": bool(i["deleted"]), "source": i["source"]}
            for i in self.knowledge(True)
            if i["review_state"] == "user_updated"
        ]

    def reconcile_knowledge(self, ids=None):
        """Confirm suggested profile changes so they apply everywhere.

        Changes from the Profile chat, the assistant, Resume Studio or the CLI park an
        entry in 'user_updated', and profile_dirty() blocks new resumes until the candidate has
        looked at it. Confirming is their approval: the entry, as the Profile page shows
        it, is written through to the YAML files, the base resume and open drafts
        (profile_sync), exactly like a save from the Profile form.
        """
        from backend.services.profile_fields import apply_fields, fields_for, label_for
        from backend.services.profile_sync import ProfileSync

        pending = self.pending_knowledge()
        chosen = [p for p in pending if ids is None or p["id"] in set(ids)]
        if ids is not None and len(chosen) != len(set(ids)):
            raise ValueError("Some entries are no longer pending. Reload the Profile and try again.")
        if not chosen:
            return {"reconciled": [], "profile_dirty": self.profile_dirty(), "revision": self.profile_revision()}
        sync = synced = None
        try:
            with self.w.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                sync = ProfileSync(self, db)
                for entry in chosen:
                    row = db.execute("SELECT * FROM knowledge WHERE id=?", (entry["id"],)).fetchone()
                    item = {**dict(row), "data": json.loads(row["data"])}
                    label = label_for(item)
                    try:
                        if item["deleted"]:
                            sync.row_removed(item, label)
                            data = item["data"]
                        else:
                            # What the page shows for the entry is what gets recorded.
                            _, _, shown = apply_fields(item["kind"], fields_for(item), item)
                            data = sync.row_saved({**item, "data": shown}, item["data"], from_form=False)
                    except ValueError as error:
                        message = str(error)
                        raise ValueError(message if message.startswith(label) else f"{label}: {message}") from None
                    db.execute(
                        "UPDATE knowledge SET data=?,review_state='registered',updated_at=? WHERE id=? AND review_state='user_updated'",
                        (json.dumps(data, ensure_ascii=False), self.now(), entry["id"]),
                    )
                self.w.record_event(db, "profile_reconciled", entry_ids=[e["id"] for e in chosen])
                self.bump_profile_revision(db)
                synced = sync.finish()
                sync.write()
        except Exception:
            if sync:
                sync.restore()
            raise
        sync.done()
        self.export_profile()
        self.w.export_tracking()
        return {"reconciled": [e["id"] for e in chosen], "profile_dirty": self.profile_dirty(),
                "revision": self.profile_revision(), "synced": synced}

    def export_profile(self):
        from career import atomic_write

        atomic_write(
            self.w.root / "data/active-profile.json",
            json.dumps(self.knowledge(True), indent=2, ensure_ascii=False) + "\n",
        )

    def profile_revision(self):
        return int(self.pref("profile_state_revision", 1))

    def bump_profile_revision(self, db):
        row = db.execute("SELECT value FROM preferences WHERE key='profile_state_revision'").fetchone()
        revision = int(json.loads(row[0])) + 1 if row else 2
        self.set_pref("profile_state_revision", revision, db)
        return revision

    def export_state(self):
        from career import atomic_write

        # A readable audit projection; SQLite remains authoritative.
        with self.w.connect() as db:
            preferences = {
                r["key"]: json.loads(r["value"])
                for r in db.execute("SELECT * FROM preferences")
            }
        atomic_write(
            self.w.root / "data/workspace-state.json",
            json.dumps(
                {"preferences": preferences, "mail": self.mail(), "runs": self.runs()},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
        )

    def sync_projections(self):
        """Regenerate every readable projection from the committed SQLite state."""
        self.w.export_tracking()
        self.export_profile()
        self.export_state()

    def profile_context(self):
        return [
            {
                "id": i["id"],
                "kind": i["kind"],
                "title": i["title"],
                "summary": i["summary"],
                "details": (
                    i["data"]
                    if i["review_state"] == "registered"
                    else {"user_supplied": True}
                ),
                "review_state": i["review_state"],
            }
            for i in self.knowledge()
            if i["kind"] != "personal"
            or i["id"]
            in {
                "personal:location",
                "personal:current_status",
                "personal:most_recent_role",
                "personal:target_roles",
                "personal:location_preferences",
            }
        ]

    def profile_dirty(self):
        if demo_mode(self):
            return False  # demo mode: pending profile edits never block new drafts
        return any(i["review_state"] == "user_updated" for i in self.knowledge(True))

    def goals(self, on=None):
        with self.w.connect() as db:
            dates = [
                r[0]
                for r in db.execute(
                    "SELECT COALESCE(j.application_date,a.submission_date,substr(a.confirmed_at,1,10)) FROM jobs j LEFT JOIN application_evidence a ON a.job_id=j.id WHERE j.application_date IS NOT NULL OR a.job_id IS NOT NULL"
                )
            ]
        return plan(self.pref("goals"), dates, on or date.fromisoformat(self.today()))

    def save_goals(self, values):
        if (
            not isinstance(values["weekly_target"], int)
            or not 1 <= values["weekly_target"] <= 200
        ):
            raise ValueError("Choose a weekly target from 1 to 200")
        days = values["workdays"]
        if (
            not days
            or len(set(days)) != len(days)
            or any(not isinstance(d, int) or not 0 <= d <= 6 for d in days)
        ):
            raise ValueError("Choose at least one distinct workday")
        start = date.fromisoformat(values["start_date"])
        if start > date.fromisoformat(self.today()):
            raise ValueError("Goal tracking cannot start in the future")
        self.set_pref("goals", {**values, "workdays": sorted(days)})
        with self.w.connect() as db:
            self.w.record_event(db, "goal_updated", settings=values)
        self.w.export_tracking()
        self.export_state()
        return self.goals()

    def add_posting(self, values, source="manual", verdict=None):
        """Serialize posting saves so every caller gets the correct duplicate status."""
        with self._posting_lock:
            return self._add_posting(values, source, verdict)

    def _add_posting(self, values, source="manual", verdict=None):
        """Save a posting unless the sponsorship gate or the never-re-apply rules say no.

        Returns {"job", "duplicate"} on success, {"excluded": True, ...} when the posting's
        own words exclude it (it is recorded in excluded_postings, never in jobs), or
        {"blocked": True, ...} when this person already applied to / was rejected by this company.
        """
        from backend.countries import market_for_location
        from backend.services import reapply, sponsorship
        title = values.get("title", values.get("role", ""))
        demo = demo_mode(self)
        market = market_for_location(self.w.root, values.get("location", ""), values.get("market"), require_match=not demo)
        verdict = verdict or self.gate(
            values["company"], values.get("description", ""), values.get("url", ""), values.get("location", ""),
            extra_sentences=[values.get("restriction_quote", "")] if values.get("restriction_quote") else None,
            employer_type=values.get("employer_type", ""), market=market,
        )
        if verdict.excluded and not demo:
            record = self.record_excluded(values, verdict, source)
            return {"excluded": True, "job": None, "duplicate": False, "reason": verdict.screen.reason,
                    "reason_label": verdict.screen.reason_label, "sentence": verdict.screen.sentence, "record": record}
        clean = canonical_url(values["url"])
        keys = {
            posting_key(clean, values["company"], values.get("requisition_id", "")),
            posting_key(clean),
        }
        normalize = lambda text: re.sub(r"[^a-z0-9]+", "", text.casefold())
        # The same posting again is a duplicate of its existing record, not a re-apply.
        with self.w.connect() as db:
            for key in keys:
                found = db.execute("SELECT job_id FROM posting_identities WHERE identity=?", (key,)).fetchone()
                if found:
                    return {"job": self.w.get_job(found[0]), "duplicate": True}
        # A Gmail-tracked application being completed with its posting is an upgrade, not a new role.
        upgrading = any(
            job.get("record_source") == "gmail"
            and normalize(job["company"]) == normalize(values["company"])
            and normalize(job["title"]) == normalize(title)
            for job in self.w.jobs()
        )
        gate = {"blocked": False, "note": ""} if upgrading else reapply.check(
            values["company"], title, self.reapply_memory(), self.excluded(), self.w.profile()
        )
        if gate["blocked"]:
            return {"blocked": True, "job": None, "duplicate": False, "rule": gate["rule"], "note": gate["note"]}
        values = {**values, "market": market, "_sponsor": verdict, "_reapply_note": gate.get("note", "")}
        email_only = [
            job
            for job in self.w.jobs()
            if job.get("record_source") == "gmail"
            and normalize(job["company"]) == normalize(values["company"])
            and normalize(job["title"])
            == normalize(values.get("title", values.get("role", "")))
        ]
        if len(email_only) == 1:
            job = email_only[0]
            with self.w.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                duplicate = next(
                    (
                        found
                        for key in keys
                        if (
                            found := db.execute(
                                "SELECT job_id FROM posting_identities WHERE identity=? AND job_id<>?",
                                (key, job["id"]),
                            ).fetchone()
                        )
                    ),
                    None,
                )
                if duplicate:
                    raise ValueError("This posting is already saved")
                db.execute(
                    "UPDATE jobs SET company=?,title=?,location=?,url=?,description=?,market=?,record_source='posting+gmail',verification='email_verified',updated_at=? WHERE id=?",
                    (
                        values["company"].strip(),
                        values.get("title", values.get("role", "")).strip(),
                        values["location"].strip(),
                        clean,
                        values["description"].strip(),
                        market,
                        self.now(),
                        job["id"],
                    ),
                )
                for key in keys:
                    db.execute(
                        "INSERT OR IGNORE INTO posting_identities VALUES(?,?)",
                        (key, job["id"]),
                    )
                self.w.record_event(
                    db,
                    "email_application_posting_added",
                    job["id"],
                    company=values["company"],
                    title=values.get("title", values.get("role", "")),
                    url=clean,
                )
            self.w.export_tracking()
            from backend.services.opportunities import record
            record(self, job["id"], values)
            return {"job": self.w.get_job(job["id"]), "duplicate": False, "upgraded": True}
        try:
            job = self.w.add_job(
                values["company"],
                values.get("title", values.get("role", "")),
                values["location"],
                clean,
                values["description"],
                values.get("requisition_id", ""),
                market,
            )
            from backend.job_quality import JobQualityService
            cid = JobQualityService(self).ensure_company(job["company"])
            with self.w.connect() as db:
                db.execute("UPDATE jobs SET company_id=? WHERE id=?", (cid, job["id"]))
            self.store_sponsorship(job["id"], values["_sponsor"])
            from backend.services.opportunities import record
            record(self, job["id"], values)
            if values.get("_reapply_note"):
                self.w.update_job(job["id"], job["status"], notes=values["_reapply_note"])
            self.w.export_tracking()  # the front page must show the tier that was just stored
            job = self.w.get_job(job["id"])
            return {"job": job, "duplicate": False}
        except ValueError as exc:
            if "already saved" not in str(exc):
                raise
            with self.w.connect() as db:
                for key in keys:
                    duplicate = db.execute(
                        "SELECT job_id FROM posting_identities WHERE identity=?", (key,)
                    ).fetchone()
                    if duplicate:
                        return {"job": self.w.get_job(duplicate[0]), "duplicate": True}
            raise

    # ---- Sponsorship gate bookkeeping -------------------------------------------
    def store_sponsorship(self, job_id, verdict):
        """Write the tier and its evidence onto the job row so every list can show it."""
        with self.w.connect() as db:
            db.execute(
                "UPDATE jobs SET sponsor_tier=?, sponsor_evidence=?, updated_at=? WHERE id=?",
                (verdict.tier if verdict.tier != "EXCLUDED" else None, json.dumps(verdict.evidence_json(), ensure_ascii=False), self.now(), job_id),
            )
            self.w.record_event(db, "sponsorship_evaluated", job_id, tier=verdict.tier, label=verdict.label())
        return verdict

    def reevaluate_sponsorship(self, job_id):
        """Re-run the gate on a saved job. If the posting now refuses, it moves to Excluded."""
        from backend.services import sponsorship
        job = self.w.get_job(job_id)
        verdict = self.gate(job["company"], job.get("description", ""), job.get("url", ""), job.get("location", ""), market=job.get("market") or "")
        if sponsorship.overridden(job.get("sponsor_evidence"), verdict):
            return {"excluded": False, "job": job, "note": "You restored this posting after reviewing that sentence, so it stays."}
        if verdict.excluded:
            self.record_excluded(job, verdict, "recheck")
            self.remove_job(job_id, "Sponsorship gate: " + verdict.screen.reason_label)
            return {"excluded": True, "sentence": verdict.screen.sentence, "reason_label": verdict.screen.reason_label}
        self.store_sponsorship(job_id, verdict)
        return {"excluded": False, "job": self.w.get_job(job_id)}

    def record_excluded(self, values, verdict, source="manual"):
        clean = canonical_url(values["url"]) if values.get("url") else ""
        record = {
            "id": hashlib.sha256((clean or values["company"] + values.get("title", "")).encode()).hexdigest()[:16],
            "company": str(values["company"]).strip(),
            "title": str(values.get("title", values.get("role", ""))).strip(),
            "location": str(values.get("location", "")).strip(),
            "url": clean,
            "description": str(values.get("description", "")),
            "reason": verdict.screen.reason,
            "reason_label": verdict.screen.reason_label,
            "sentence": verdict.screen.sentence,
            "pattern": verdict.screen.pattern,
            "source": source,
            "excluded_at": self.now(),
        }
        with self.w.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO excluded_postings(id,company,title,location,url,description,reason,reason_label,sentence,pattern,source,excluded_at,restored_at)
                   VALUES(:id,:company,:title,:location,:url,:description,:reason,:reason_label,:sentence,:pattern,:source,:excluded_at,NULL)""",
                record,
            )
            self.w.record_event(db, "posting_excluded", None, company=record["company"], title=record["title"], url=record["url"], reason=record["reason"], sentence=record["sentence"], source=source)
        self.w.export_tracking()
        return {k: v for k, v in record.items() if k != "description"}

    def reapply_memory(self):
        """Every job ever saved, plus the history a fresh start archived, for the never-re-apply rules."""
        with self.w.connect() as db:
            archived = [dict(r) for r in db.execute(
                "SELECT company, title, status, url, application_date, updated_at FROM reapply_history"
            )]
        return self.w.jobs(include_deleted=True) + archived

    def excluded(self, include_restored=False):
        with self.w.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM excluded_postings ORDER BY excluded_at DESC")]
        return [
            {k: v for k, v in row.items() if k != "description"}
            for row in rows
            if include_restored or not row.get("restored_at")
        ]

    def restore_excluded(self, excluded_id):
        """A wrong exclusion is correctable: save the posting as a normal job and keep the audit trail."""
        from backend.services.sponsorship import Screen, Verdict
        with self.w.connect() as db:
            row = db.execute("SELECT * FROM excluded_postings WHERE id=?", (excluded_id,)).fetchone()
        if not row:
            raise ValueError("Excluded posting not found")
        row = dict(row)
        if row.get("restored_at"):
            raise ValueError("This posting was already restored")
        from backend.services.sponsorship import resolve_tier, rules_for

        # The exclusion is overruled, but the employer's ranking signals still count: a university
        # stays S (cap-exempt), a proven sponsor stays B. (Restores used to land as C every time.)
        signals = self.gate(row["company"], row["description"] or "", row["url"] or "", row["location"] or "")
        screen = Screen("KEEP", "silent", "Restored after review; the exclusion was judged wrong", sentence=row["sentence"])
        override = Verdict(tier=resolve_tier(screen, signals.cap_exempt, signals.h1b_approvals), screen=screen,
                           cap_exempt=signals.cap_exempt, cap_exempt_reason=signals.cap_exempt_reason,
                           h1b_found=signals.h1b_found, h1b_matched_name=signals.h1b_matched_name,
                           h1b_approvals=signals.h1b_approvals, h1b_years=signals.h1b_years, everify=signals.everify,
                           restored=True, tier_labels=rules_for(self.w.root).get("tier_labels") or {})
        # Mark it restored first so the never-re-apply check does not see its own record.
        with self.w.connect() as db:
            db.execute("UPDATE excluded_postings SET restored_at=? WHERE id=?", (self.now(), excluded_id))
        try:
            result = self.add_posting({"company": row["company"], "title": row["title"], "location": row["location"], "url": row["url"], "description": row["description"]}, source="restored", verdict=override)
        except Exception:
            with self.w.connect() as db:
                db.execute("UPDATE excluded_postings SET restored_at=NULL WHERE id=?", (excluded_id,))
            raise
        if result.get("blocked"):
            with self.w.connect() as db:
                db.execute("UPDATE excluded_postings SET restored_at=NULL WHERE id=?", (excluded_id,))
            return result
        if result.get("duplicate") and (result.get("job") or {}).get("deleted_at"):
            # The posting sweep removed a saved job when its wording changed; restoring brings that job back.
            self.w.restore_job(result["job"]["id"])
            self.store_sponsorship(result["job"]["id"], override)
            result = {"job": self.w.get_job(result["job"]["id"]), "duplicate": False}
        with self.w.connect() as db:
            self.w.record_event(db, "exclusion_restored", (result.get("job") or {}).get("id"), company=row["company"], title=row["title"])
        self.w.export_tracking()
        return result

    def age_applications(self):
        """Compatibility hook for the scheduler; silence never changes an outcome.

        The Pipeline view uses ``reapply.quiet_days`` for waiting-time reminders.
        Only the person's instruction or confirmed mail evidence changes status.
        """
        return []

    def mail(self):
        with self.w.connect() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM mail_evidence ORDER BY received_at DESC"
                )
            ]
        rows.sort(
            key=lambda m: datetime.fromisoformat(
                m["received_at"].replace("Z", "+00:00")
            ),
            reverse=True,
        )
        from backend.services.agents import GMAIL_ONLY_BACKUP, mail_available

        connection = self.pref("gmail", {"connected": False}) or {}
        available = mail_available(self.w.root, connection)
        return {
            "connection": connection,
            "messages": rows,
            "available": available,
            **({} if available else {"note": GMAIL_ONLY_BACKUP}),
        }

    def save_mail_connection(self, connector_id: str, expected_email: str):
        """Opt in this profile to one mailbox; verification occurs at sync time."""
        connector_id = connector_id.strip()
        expected_email = expected_email.strip().casefold()
        if bool(connector_id) != bool(expected_email):
            raise ValueError("Enter both the Gmail connector ID and mailbox email, or clear both")
        if connector_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", connector_id):
            raise ValueError("Gmail connector ID contains unsupported characters")
        if expected_email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", expected_email):
            raise ValueError("Enter a valid mailbox email address")
        previous = self.pref("gmail", {}) or {}
        previous_email = str(previous.get("email") or "").strip().casefold()
        if expected_email and previous_email and expected_email != previous_email:
            with self.w.connect() as db:
                has_evidence = db.execute("SELECT 1 FROM mail_evidence LIMIT 1").fetchone()
            if has_evidence:
                raise ValueError("This profile already has evidence from another mailbox. Use a separate profile for a different person.")
        changed = (connector_id != previous.get("connector_id") or
                   expected_email != previous.get("expected_email"))
        connection = {
            **previous,
            "connector_id": connector_id,
            "expected_email": expected_email,
            "connected": bool(previous.get("connected")) if not changed else False,
            "status": (previous.get("status", "ready") if not changed else
                       "ready_to_verify" if connector_id else "disconnected"),
            "last_error": previous.get("last_error", "") if not changed else "",
        }
        if changed and expected_email != previous.get("expected_email"):
            connection.pop("last_synced_at", None)
            connection.pop("email", None)
            connection.pop("coverage", None)
        with self.w.connect() as db:
            self.set_pref("gmail", connection, db)
            self.w.record_event(db, "mail_connection_updated", configured=bool(connector_id))
        self.sync_projections()
        return connection

    def ingest_mail(self, batch):
        # Excerpts only: do not store entire inbox bodies, HTML, attachments or tracking URLs.
        email = str(batch.get("email", "")).strip()
        if not email:
            raise ValueError(
                "Gmail account verification is required before email evidence can be saved"
            )
        connection = self.pref("gmail", {}) or {}
        from backend.services.agents import mail_available
        if not mail_available(self.w.root, connection):
            raise ValueError("Connect this profile's own mail account in Settings before syncing")
        if not batch.get("connection_verified") or not batch.get("search_completed"):
            raise ValueError("Gmail account verification and mailbox search must finish before email evidence can be saved")
        if email.casefold() != connection["expected_email"].casefold():
            raise ValueError("Connected Gmail account does not match this profile's expected mailbox")
        count = 0
        with self.w.connect() as db:
            for m in batch["messages"]:
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,150}", m["id"]):
                    raise ValueError("Invalid message ID")
                if m["kind"] not in {
                    "applied",
                    "interview",
                    "offer",
                    "rejected",
                    "reminder",
                    "uncertain",
                }:
                    raise ValueError("Invalid email classification")
                timestamp = datetime.fromisoformat(
                    m["received_at"].replace("Z", "+00:00")
                )
                if timestamp.tzinfo is None or timestamp > datetime.now(timezone.utc):
                    raise ValueError(
                        "Email must have a valid timezone and cannot be from the future"
                    )
                if (
                    m.get("submission_date")
                    and date.fromisoformat(m["submission_date"])
                    > timestamp.astimezone(ZoneInfo(self.w.timezone)).date()
                ):
                    raise ValueError("Submission date cannot be after the message")
                job_id = m.get("job_id") or None
                if job_id:
                    self.w.get_job(job_id)
                count += db.execute(
                    "INSERT OR IGNORE INTO mail_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        m["id"],
                        job_id,
                        m.get("company", ""),
                        m.get("role", ""),
                        m["kind"],
                        m["subject"],
                        m["sender"],
                        m["received_at"],
                        m.get("submission_date"),
                        m["excerpt"][:3000],
                        m.get("reason", ""),
                        m.get("confidence", "needs_review"),
                        "pending",
                        self.now(),
                    ),
                ).rowcount
            self.set_pref(
                "gmail",
                {
                    **connection,
                    "connected": True,
                    "email": email,
                    "last_synced_at": self.now(),
                    "last_attempt_at": self.now(),
                    "status": "ready",
                    "last_error": "",
                    "coverage": batch.get("coverage", "Job-related messages"),
                    "mode": "Connected Gmail via Codex",
                },
                db,
            )
            self.w.record_event(db, "email_synced", new_messages=count)
        # Automatic updates require exact company AND role evidence. When no saved
        # posting exists, a high-confidence status email creates a minimal application
        # record; no posting URL, job description or submission date is invented.
        normalize = lambda text: re.sub(r"[^a-z0-9]+", "", text.casefold())
        for m in self.mail()["messages"]:
            if (
                m["state"] != "pending"
                or m["confidence"] != "high"
                or m["kind"] in {"reminder", "uncertain"}
            ):
                continue
            matches = [
                j
                for j in self.w.jobs()
                if normalize(j["company"]) == normalize(m["company"])
                and normalize(j["title"]) == normalize(m["role"])
            ]
            if len(matches) == 1 and m["job_id"] in {None, matches[0]["id"]}:
                self.resolve_mail(m["id"], matches[0]["id"])
            elif (
                not matches
                and not m["job_id"]
                and m["company"].strip()
                and m["role"].strip()
            ):
                self.resolve_mail(m["id"], create_application=True)
        self.w.export_tracking()
        self.export_state()
        return {"imported": count}

    def record_mail_sync_failure(self, error):
        """Preserve verified evidence and the last successful timestamp on access failure."""
        with self.w.connect() as db:
            row = db.execute(
                "SELECT value FROM preferences WHERE key='gmail'"
            ).fetchone()
            previous = json.loads(row[0]) if row else {}
            previous_coverage = str(previous.get("coverage", "")).casefold()
            if not previous.get("email") or any(
                phrase in previous_coverage
                for phrase in ("unable to verify", "inaccessible coverage")
            ):
                for saved in db.execute(
                    "SELECT result,updated_at FROM agent_runs WHERE kind='email' AND state='completed' "
                    "AND result IS NOT NULL ORDER BY created_at DESC"
                ):
                    try:
                        result = json.loads(saved[0])
                        verified_email = result.get("email", "").strip()
                    except (AttributeError, json.JSONDecodeError):
                        continue
                    if verified_email:
                        previous["email"] = verified_email
                        previous["last_synced_at"] = saved[1]
                        previous["coverage"] = result.get(
                            "coverage", previous.get("coverage", "")
                        )
                        break
            self.set_pref(
                "gmail",
                {
                    **previous,
                    "connected": False,
                    "status": "needs_attention",
                    "last_attempt_at": self.now(),
                    "last_error": str(error)[:500],
                },
                db,
            )
            self.w.record_event(db, "email_sync_failed", error=str(error)[:500])
        self.sync_projections()

    def _create_mail_application(self, db, message):
        company = message["company"].strip()
        role = message["role"].strip()
        if not company or not role:
            raise ValueError("Confirm the exact company and role before tracking it")
        job_id = hashlib.sha256(("gmail:" + message["id"]).encode()).hexdigest()[:16]
        url = "https://mail.google.com/mail/u/0/#all/" + message["id"]
        description = (
            "Job description not available. This application was tracked from verified Gmail evidence. "
            "Add the original posting and full description before generating a resume or cover letter."
        )
        from backend.countries import target_markets_for

        db.execute(
            "INSERT INTO jobs(id,company,title,location,url,description,status,created_at,updated_at,application_date,verification,record_source,market) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                company,
                role,
                "Not recorded",
                url,
                description,
                message["kind"],
                self.now(),
                self.now(),
                message["submission_date"],
                "email_verified",
                "gmail",
                target_markets_for(self.w.root)[0],
            ),
        )
        db.execute(
            "INSERT OR IGNORE INTO posting_identities VALUES(?,?)",
            (
                "mail-application:"
                + re.sub(r"[^a-z0-9]+", "", company.casefold())
                + ":"
                + re.sub(r"[^a-z0-9]+", "", role.casefold()),
                job_id,
            ),
        )
        self.w.record_event(
            db,
            "email_application_tracked",
            job_id,
            message_id=message["id"],
            company=company,
            title=role,
            submission_date=message["submission_date"],
        )
        return job_id

    def resolve_mail(
        self, id, job_id=None, action="confirm", create_application=False
    ):
        with self.w.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            m = db.execute("SELECT * FROM mail_evidence WHERE id=?", (id,)).fetchone()
            if not m:
                raise ValueError("Email evidence not found")
            if m["state"] != "pending":
                return {"state": m["state"]}
            if action not in {"confirm", "dismiss"}:
                raise ValueError("Choose confirm or dismiss")
            if action == "dismiss":
                db.execute(
                    "UPDATE mail_evidence SET state='dismissed' WHERE id=?", (id,)
                )
                self.w.record_event(db, "email_dismissed", message_id=id)
                db.commit()
                self.w.export_tracking()
                self.export_state()
                return {"state": "dismissed"}
            if m["kind"] in {"reminder", "uncertain"}:
                raise ValueError("This email does not establish an application status")
            job_id = job_id or m["job_id"]
            if not job_id and create_application:
                job_id = self._create_mail_application(db, m)
                job = dict(
                    db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                )
            elif job_id:
                job = self.w.get_job(job_id)
            else:
                raise ValueError(
                    "Choose a saved role or track this verified application from the email"
                )
            if job.get("deleted_at"):
                raise ValueError("Restore the removed role before linking email evidence")
            # A late old confirmation must never regress an interview, offer or rejection.
            status = m["kind"]
            if status == "applied" and job["status"] in {
                "interview",
                "offer",
                "rejected",
                "withdrawn",
            }:
                status = job["status"]
            timestamps = db.execute(
                "SELECT received_at FROM mail_evidence WHERE job_id=? AND state='confirmed'",
                (job_id,),
            ).fetchall()
            newest = max(
                (
                    datetime.fromisoformat(r[0].replace("Z", "+00:00"))
                    for r in timestamps
                ),
                default=None,
            )
            if newest and newest > datetime.fromisoformat(
                m["received_at"].replace("Z", "+00:00")
            ):
                status = job["status"]
            received = (
                datetime.fromisoformat(m["received_at"].replace("Z", "+00:00"))
                .astimezone(ZoneInfo(self.w.timezone))
                .isoformat()
            )
            # Every confirmed outcome establishes an application record, but only an
            # explicitly stated date is stored as the submission date.
            db.execute(
                "INSERT INTO application_evidence VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET confirmed_at=MIN(application_evidence.confirmed_at,excluded.confirmed_at),submission_date=COALESCE(application_evidence.submission_date,excluded.submission_date)",
                (job_id, "gmail", received, m["submission_date"], id),
            )
            db.execute(
                "UPDATE jobs SET status=?,application_date=COALESCE(application_date,?),updated_at=? WHERE id=?",
                (status, m["submission_date"], self.now(), job_id),
            )
            db.execute(
                "UPDATE mail_evidence SET state='confirmed',job_id=? WHERE id=?",
                (job_id, id),
            )
            self.w.record_event(
                db,
                "email_status_confirmed",
                job_id,
                message_id=id,
                status=status,
                submission_date=m["submission_date"],
            )
        self.w.export_tracking()
        self.export_state()
        return {"state": "confirmed", "job": self.w.get_job(job_id)}

    def remove_job(self, job_id, reason="Not suitable"):
        result = self.w.remove_job(job_id, reason)
        self.export_state()
        return result

    def restore_job(self, job_id):
        result = self.w.restore_job(job_id)
        self.export_state()
        return result

    def _document_root(self, job):
        from career import safe_child

        if job.get("folder"):
            return safe_child(
                self.w.root / "data/output",
                str(Path(job["folder"]).relative_to("data/output")),
            )
        root = self.w.root / "data/output" / "applications" / (job["id"] + "-documents")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def generate_cover_letter(self, job_id):
        from career import atomic_write

        job = self.w.get_job(job_id)
        if job.get("deleted_at"):
            raise ValueError("Restore this removed role before generating documents")
        if job.get("record_source") == "gmail":
            raise ValueError(
                "Add the original posting and full job description before generating a cover letter"
            )
        if self.profile_dirty():
            raise ValueError(
                "Open Profile and confirm the pending entries before generating a cover letter"
            )
        active_projects = {
            item["id"]
            for item in self.knowledge()
            if item["kind"] == "project" and item["review_state"] == "registered"
        }
        ranked = [
            project
            for project in self.w.rank_projects(
                job["title"] + " " + job["description"]
            )
            if project["id"] in active_projects
        ]
        if len(ranked) < 2:
            raise ValueError("Two registered evidence examples are required")
        first, second = ranked[:2]
        focus = list(dict.fromkeys(first["matched_terms"] + second["matched_terms"]))[:4]
        focus_text = (
            " The role's focus on " + ", ".join(focus) + " aligns with evidence from my work and projects."
            if focus
            else ""
        )
        profile = self.w.profile()
        candidate = profile["candidate"]
        headline = (profile.get("narrative") or {}).get("headline", "").strip().rstrip(".")
        # Contact block mirrors the active resume header; city is omitted here.
        contact = [candidate["full_name"], candidate["email"], candidate["phone"]]
        for key in ("portfolio_url", "github"):
            if candidate.get(key):
                contact.append(candidate[key])
        # Only the profile's own headline describes the candidate; no degree or school is assumed.
        opening = f"I am writing to apply for the {job['title']} role at {job['company']}."
        if headline:
            opening += f" I am a {headline[0].lower() + headline[1:]}."
        opening += focus_text
        letter = "\n".join(
            [
                *contact,
                "",
                self.today(),
                "",
                "Hiring Team",
                job["company"],
                "",
                f"Re: {job['title']}",
                "",
                "Dear Hiring Team,",
                "",
                opening,
                "",
                f"A relevant example is {first['title']}. {first['bullets'][0]} {first['bullets'][1] if len(first['bullets']) > 1 else ''}".strip(),
                "",
                f"I can also bring experience from {second['title']}. {second['bullets'][0]}".strip(),
                "",
                f"I would welcome the opportunity to discuss how this work could support the {job['title']} team at {job['company']}. Thank you for considering my application.",
                "",
                "Sincerely,",
                candidate["full_name"],
            ]
        )
        letter = clean_text(letter)  # posting text can carry hidden characters (AI marks)
        with self.w.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = (
                db.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM cover_letters WHERE job_id=?",
                    (job_id,),
                ).fetchone()[0]
            )
            root = self._document_root(job)
            path = root / f"cover-letter-v{version}.md"
            atomic_write(path, letter + "\n")
            atomic_write(
                root / f"cover-letter-v{version}.json",
                json.dumps(
                    {
                        "job_id": job_id,
                        "company": job["company"],
                        "title": job["title"],
                        "candidate_revision": self.w.evidence()[
                            "candidate_revision"
                        ],
                        "evidence_ids": [
                            "IDENTITY-001",
                            "CONTACT-EMAIL-001",
                            "CONTACT-PHONE-001",
                            "CONTACT-PORTFOLIO-001",
                            "CONTACT-GITHUB-001",
                            "EDU-MS-001",
                            first["id"],
                            second["id"],
                        ],
                        "review_required": True,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
            )
            relative = str(path.relative_to(self.w.root / "data/output"))
            stamp = self.now()
            db.execute(
                "INSERT INTO cover_letters VALUES(?,?,?,?,?,?)",
                (
                    job_id,
                    version,
                    letter,
                    relative,
                    stamp,
                    self.w.evidence()["candidate_revision"],
                ),
            )
            self.w.record_event(
                db,
                "cover_letter_generated",
                job_id,
                company=job["company"],
                title=job["title"],
                version=version,
                path=relative,
                review_required=True,
            )
        self.w.export_tracking()
        self.export_state()
        return {
            "job_id": job_id,
            "company": job["company"],
            "title": job["title"],
            "version": version,
            "content": letter,
            "path": relative,
            "created_at": stamp,
            "review_required": True,
        }

    def documents(self):
        documents = []
        with self.w.connect() as db:
            has_studio = bool(
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='studio_drafts'"
                ).fetchone()
            )
            latest_letters = {
                r["job_id"]: dict(r)
                for r in db.execute(
                    "SELECT c.* FROM cover_letters c JOIN (SELECT job_id,MAX(version) version FROM cover_letters GROUP BY job_id) latest ON latest.job_id=c.job_id AND latest.version=c.version"
                )
            }
            studio = (
                {r["job_id"]: dict(r) for r in db.execute("SELECT * FROM studio_drafts")}
                if has_studio
                else {}
            )
        for job in self.w.jobs():
            resumes = []
            if job.get("folder"):
                root = self.w.root / job["folder"]
                original = root / "resume.pdf"
                if original.exists():
                    resumes.append(
                        {
                            "label": "Prepared resume",
                            "path": str(original.relative_to(self.w.root / "data/output")),
                        }
                    )
            draft = studio.get(job["id"])
            if draft:
                from backend.services.resume_studio import read_saved_preview

                preview, pdf, _ = read_saved_preview(self.w.root, self.w.root / draft["folder"],
                                                    draft["source"], draft["revision"])
                if preview is not None:
                    if pdf.is_file():
                        path = str(pdf.relative_to(self.w.root / "data/output"))
                        if not any(item["path"] == path for item in resumes):
                            resumes.insert(
                                0,
                                {
                                    "label": f"Resume Studio v{preview['revision']}",
                                    "path": path,
                                },
                            )
            letter = latest_letters.get(job["id"])
            fallback_letter = None
            if not letter and job.get("folder"):
                root = self.w.root / job["folder"]
                candidates = sorted(
                    root.glob("cover-letter*.md"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                if candidates:
                    fallback_letter = {
                        "version": 1,
                        "path": str(
                            candidates[0].relative_to(self.w.root / "data/output")
                        ),
                        "created_at": datetime.fromtimestamp(
                            candidates[0].stat().st_mtime, timezone.utc
                        ).isoformat(timespec="seconds"),
                    }
            if resumes or letter or fallback_letter:
                documents.append(
                    {
                        "job_id": job["id"],
                        "company": job["company"],
                        "title": job["title"],
                        "resumes": resumes,
                        "cover_letter": (
                            {
                                "version": letter["version"],
                                "path": letter["path"],
                                "created_at": letter["created_at"],
                            }
                            if letter
                            else fallback_letter
                        ),
                    }
                )
        return documents

    def runs(self):
        with self.w.connect() as db:
            rows = db.execute(
                "SELECT id,kind,job_id,state,result,error,created_at,updated_at,provider,model,preset FROM agent_runs ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        return [
            {**dict(r), "result": json.loads(r["result"]) if r["result"] else None}
            for r in rows
        ]

    def summary(self):
        jobs = self.w.jobs()
        with self.w.connect() as db:
            confirmed = {
                r[0]
                for r in db.execute(
                    'SELECT job_id FROM application_evidence UNION SELECT job_id FROM mail_evidence WHERE state="confirmed"'
                )
            }
        return {
            "jobs": jobs,
            "expired_jobs": [
                j for j in self.w.jobs(include_deleted=True)
                if not j.get("deleted_at") and j.get("posting_state") == "expired" and j["status"] in {"saved", "prepared"}
            ],
            "removed_jobs": [
                j for j in self.w.jobs(include_deleted=True) if j.get("deleted_at")
            ],
            "excluded_jobs": self.excluded(),
            "documents": self.documents(),
            "goals": self.goals(),
            "mail": self.mail(),
            "runs": self.runs(),
            "agents": agents_for(self.w.root),
            "profile_dirty": self.profile_dirty(),
            "counts": {
                "saved": len(jobs),
                "applied": sum(
                    bool(j["application_date"])
                    or j["id"] in confirmed
                    or j["status"] in {"applied", "interview", "offer"}
                    for j in jobs
                ),
                "interviews": sum(j["status"] == "interview" for j in jobs),
                "offers": sum(j["status"] == "offer" for j in jobs),
                "excluded": len(self.excluded()),
                "ghosted": sum(j["status"] == "ghosted" for j in jobs),
            },
            "activity": self.w.activity(15),
        }
