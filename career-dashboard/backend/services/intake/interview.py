"""The setup chat: a new profile is built from a conversation on its Assistant page.

    attach documents (or paste text) -> "read them now?" -> reading, shown as it happens
    -> what was found -> questions one at a time: the essentials first (country, roles,
       right to work, contacts), then the AI interviewer's (the questions the documents left
       open, then preferences) -> "build it now?" -> the workspace is built, the full app opens

It drives the same IntakeJob as the onboarding form (job.py): answers reach the draft through
`IntakeJob.update` and `record_answer`, and the build is the form's build. Every question has
clickable options where the answer is predictable, and can always be answered in free text or
skipped. The conversation lives in data/intake/chat.json, inside the profile's own folder.
"""

from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable

from backend.services.intake.job import (STATE_BUILDING, STATE_BUILT, STATE_FAILED, STATE_READING,
                                         STATE_REVIEW, IntakeJob)

MAX_AI_QUESTIONS = 5
NOTES_FILE = "notes-from-chat.md"

# What the interviewer may fill from an answer, and where IntakeJob.update keeps it.
LIST_FIELDS = {"roles", "cities", "arrangements", "countries"}
TEXT_FIELDS = {
    "full_name": "contact", "preferred_name": "contact", "email": "contact", "phone": "contact",
    "linkedin": "contact", "github": "contact", "portfolio_url": "contact", "city": "contact",
    "seniority": "targets", "salary": "targets", "availability": "targets",
    "authorization_status": "authorization", "valid_until": "authorization", "conditions": "authorization",
}
AUTH_KEYS = {"authorization_status": "status", "valid_until": "valid_until", "conditions": "conditions"}
FIELDS = {
    "roles": "job titles to search for (list)",
    "cities": "cities they want to work in (list)",
    "arrangements": "on-site / hybrid / remote / relocation (list)",
    "countries": "countries they want to work in (list)",
    "seniority": "level they are aiming for, e.g. graduate, junior",
    "availability": "when they can start",
    "salary": "their salary expectation, verbatim",
    "city": "the city they live in now",
    "linkedin": "LinkedIn URL", "github": "GitHub URL", "portfolio_url": "portfolio URL",
    "email": "email for resumes", "phone": "phone for resumes",
    "authorization_status": "their permission to work, e.g. 'Stamp 1G' or 'F-1 OPT'",
    "valid_until": "when that permission ends", "conditions": "limits on it, e.g. '40 hours a week'",
}

READ_NOW, ADD_MORE = "Read them now", "I'll add more first"
TRY_AGAIN = "Try again"
BUILD_NOW, CHANGE = "Build my workspace", "Change something first"
RE_READ = re.compile(r"^\s*(read|start|go|go ahead|yes|ok|okay|read (them|it|my documents)( now)?)\W*$", re.I)
RE_BUILD = re.compile(r"^\s*(build( it| my workspace)?( now)?|done|finish|that'?s all|skip( the rest)?)\W*$", re.I)
RE_STOP = re.compile(r"^\s*(stop|cancel)\W*$", re.I)
RE_ADDING = re.compile(r"\b(also|add|adding|include|including|as well|too|plus)\b", re.I)
RE_REPLACING = re.compile(r"\b(only|instead|remove|drop|replace|not|no longer|rather than)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def _plain(text: str) -> str:
    """Text for the person: no block ids ("[P133]", "around P133") from the reading stage."""
    text = re.sub(r"\s*[\[(]\s*(?:around\s+)?P\d{3,4}(?:\s*[,–-]\s*P?\d{3,4})*\s*[\])]", "", str(text or ""))
    text = re.sub(r"\b(?:in|at|see|from|around)\s+P\d{3,4}\b", "in your documents", text)
    text = re.sub(r"\bP\d{3,4}\b", "your document", text)
    return _clean(re.sub(r"(in your documents)(?:\s+in your documents)+", r"\1", text))


def _items(text: str) -> list[str]:
    """A typed list: commas, semicolons, '|' or new lines separate the items."""
    return [part.strip() for part in re.split(r"\s*[,;|\n]\s*", text or "") if part.strip()]


def _option(label: str, description: str = "", value: str | None = None) -> dict:
    return {"label": label, "description": description, "value": label if value is None else value}


# ---- Irish permit facts ----------------------------------------------------------------------
# Every normalized permit fact comes from the person's own choice here (or the Build settings
# form); the documents only suggest which option to show first.
IRISH_PERMISSIONS = (
    ("stamp_1g", "Stamp 1G", "Third Level Graduate Programme"),
    ("stamp_4", "Stamp 4", "Live and work in Ireland without an employment permit"),
    ("csep_holder", "Critical Skills permit", "I hold a Critical Skills Employment Permit"),
    ("gep_holder", "General Employment Permit", "I hold a General Employment Permit"),
    ("stamp_2", "Stamp 2", "Student permission"),
    ("irish_or_eea_citizen", "Irish, EU/EEA, UK or Swiss citizen", "No employment permit is needed"),
    ("unknown", "Not sure", "Jobs that depend on it wait until you confirm"),
)
# Permissions that allow full-time work while they are valid; the type alone settles it.
FULL_TIME_PERMISSIONS = {"stamp_1g", "stamp_4", "csep_holder", "gep_holder"}
DEGREE_CHOICES = {"ie:9": (9, True), "ie:8": (8, True), "ie:10": (10, True), "abroad": (None, False), "unknown": (None, None)}
_PERMISSION_HINTS = (("stamp_1g", r"\bstamp\s*1\s*g\b|graduate programme"), ("stamp_4", r"\bstamp\s*4\b"),
                     ("stamp_2", r"\bstamp\s*2\b"), ("csep_holder", r"critical skills"),
                     ("gep_holder", r"general employment permit"))


def suggested_permission(text: str) -> str | None:
    """The option a person's own wording points to ("Stamp 1G until DEC2027" -> stamp_1g); a suggestion only."""
    text = str(text or "").casefold()
    for value, pattern in _PERMISSION_HINTS:
        if re.search(pattern, text):
            return value
    if re.search(r"\bcitizen", text) and not re.search(r"\bnon[- ]?citizen|\bnot an? \w*\s*citizen", text):
        return "irish_or_eea_citizen"
    return None


def _irish_permission_questions(auth: dict, facts: dict) -> list[dict]:
    questions = []
    if facts.get("permission_type", "unknown") == "unknown":
        hint = suggested_permission(auth.get("status"))
        ordered = sorted(IRISH_PERMISSIONS, key=lambda item: item[0] != hint)
        questions.append({"key": "permit_type", "field": "permit.permission_type", "skippable": False,
                          "text": "Which permission do you have to live and work in Ireland now?",
                          "why": "It decides which postings you can take and which permit rules apply to them.",
                          "placeholder": "Something else? Describe it in your own words",
                          "options": [_option(label, "From your documents" if value == hint else description, value)
                                      for value, label, description in ordered]})
    elif facts.get("permission_type") in {"stamp_2", "other"} and facts.get("status", "unknown") == "unknown":
        questions.append({"key": "permit_status", "field": "permit.status", "other": False, "skippable": False,
                          "text": "Can you work full-time in Ireland now, without an employer getting you an employment permit?",
                          "why": "Postings you cannot lawfully take are set aside, with the sentence that ruled them out.",
                          "options": [_option("Yes", "My current permission allows full-time work", "authorized"),
                                      _option("No", "An employer would need to get me a permit", "needs_sponsorship"),
                                      _option("Not sure", "Jobs that depend on it wait until you confirm", "unknown")]})
    return questions


def _irish_graduate_questions(draft: dict, auth: dict, facts: dict) -> list[dict]:
    """Stamp 1G expiry, then the degree facts behind the graduate salary thresholds."""
    from backend.services.intake.authorization import date_proposal

    questions = []
    if facts.get("permission_type") == "stamp_1g" and not facts.get("valid_until_confirmed"):
        raw = facts.get("valid_until_raw") or _clean(auth.get("valid_until"))
        proposal = date_proposal(raw) if raw else {"proposed_date": None}
        day = proposal["proposed_date"]
        questions.append({"key": "permit_expiry", "field": "permit.expiry",
                          "text": (f"Your documents give your Stamp 1G expiry as “{raw}”. What is the exact day on "
                                   "your permission?" if raw else
                                   "On what day does your Stamp 1G permission expire? Use the date on your IRP card."),
                          "why": proposal.get("explanation") or "Searches and permit dates use the day you confirm here.",
                          "placeholder": "YYYY-MM-DD, for example 2027-12-31",
                          "options": [_option(f"{day}, as shown", "Confirm this exact day", day)] if day else []})
    may_need_permit = (facts.get("needs_sponsorship_later") != "no" and facts.get("citizenship") != "citizen"
                       and facts.get("permission_type") != "stamp_4")
    education = draft.get("education_for_permits") or {}
    if may_need_permit and draft.get("education"):
        if not education.get("award_date_confirmed"):
            raw = education.get("award_date_raw") or next(
                (e.get("award_date_as_supplied") for e in draft["education"] if e.get("award_date_as_supplied")), "")
            proposal = date_proposal(raw)
            day = proposal["proposed_date"]
            questions.append({"key": "permit_award", "field": "permit_education.award",
                              "text": "On what day was your most recent degree awarded?",
                              "why": "Lower graduate salary thresholds apply for 12 months from the award date on your "
                                     "award letter or parchment (not the expected graduation date). Skip it if you don't know yet.",
                              "placeholder": "YYYY-MM-DD",
                              "options": [_option(f"{day}, as shown", "Confirm this exact day", day)] if day else []})
        if education.get("nfq_level") is None and education.get("irish_institution") is None:
            questions.append({"key": "permit_degree", "field": "permit_education.degree", "other": False,
                              "text": "Which describes that degree?",
                              "why": "The graduate thresholds depend on the award's level and, for a General Employment "
                                     "Permit, on an Irish institution.",
                              "options": [_option("Irish master's or postgraduate diploma", "NFQ Level 9", "ie:9"),
                                          _option("Irish honours bachelor's degree", "NFQ Level 8", "ie:8"),
                                          _option("Irish doctorate", "NFQ Level 10", "ie:10"),
                                          _option("A degree from outside Ireland", "", "abroad"),
                                          _option("Not sure", "", "unknown")]})
        if education.get("relevant_degree") is None:
            questions.append({"key": "permit_relevance", "field": "permit_education.relevant_degree", "other": False,
                              "text": "Is that degree in the same field as the jobs you want, for example computing for software roles?",
                              "why": "The graduate thresholds apply to a degree relevant to the job.",
                              "options": [_option("Yes", value="yes"), _option("No", value="no"),
                                          _option("Not sure", value="unknown")]})
    if "graduate_search_confirmed" not in (draft.get("job_search") or {}):
        questions.append({"key": "graduate_search", "field": "graduate_search_confirmed", "other": False,
                          "text": "Should I focus on graduate and entry-level jobs that ask for up to 3 years of experience?",
                          "why": "Senior roles are set aside, so your list is jobs you can realistically get.",
                          "options": [_option("Yes", "Graduate, junior and entry-level roles", "yes"),
                                      _option("No", "Keep the search open to any level", "no")]})
    return questions


class SetupChat:
    """One onboarding profile's conversation. Answers are applied to the job's draft at once."""

    def __init__(self, job: IntakeJob, name: str, packs: list[dict], make_team: Callable,
                 review: Callable[[IntakeJob], dict], build: Callable[[], dict]):
        self.job = job
        self.name = _clean(name) or "you"
        self.first = self.name.split()[0]
        self.packs = packs
        self.make_team = make_team
        self.review = review
        self.build = build
        self.lock = threading.RLock()
        self.worker: threading.Thread | None = None

    # ---- storage -----------------------------------------------------------------
    def _load(self) -> dict:
        chat = self.job._read("chat.json", None) or {}
        chat.setdefault("messages", [])
        chat.setdefault("pending", None)
        chat.setdefault("asked", [])
        chat.setdefault("ai_asked", 0)
        chat.setdefault("flags", {})
        return chat

    def _save(self, chat: dict) -> None:
        self.job._write("chat.json", chat)

    def _post(self, chat: dict, role: str, text: str, kind: str = "text", **extra) -> dict:
        message = {"id": uuid.uuid4().hex[:12], "role": role, "text": text, "kind": kind, "at": _now(), **extra}
        chat["messages"].append(message)
        return message

    def _ask(self, chat: dict, question: dict, lead: str = "") -> None:
        question = {"id": uuid.uuid4().hex[:12], "options": [], "multi": False, "other": True, "skippable": True,
                    "placeholder": "Type your answer", "why": "", "field": "note", "resolves": "", "defaults": [],
                    **question}
        chat["pending"] = question
        chat["asked"].append(question.get("key") or question["text"])
        self._post(chat, "assistant", (lead + "\n\n" if lead else "") + question["text"], "question", question=question)

    def thinking(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    # ---- what the page shows ------------------------------------------------------
    def view(self) -> dict:
        with self.lock:
            chat = self._load()
            before = len(chat["messages"])
            if not chat["messages"]:
                self._post(chat, "assistant", self._greeting(), "greeting")
            follow_up = self._catch_up(chat)
            if len(chat["messages"]) != before or follow_up:
                self._save(chat)
            if follow_up and not self.thinking():
                self._spawn(follow_up)
            return {"messages": chat["messages"], "pending": None if self.thinking() else chat["pending"],
                    "thinking": self.thinking(), "intake": self.review(self.job)}

    def _greeting(self) -> str:
        return (f"Hi! Let's build {self.first}'s job-search workspace together.\n\n"
                "Attach resumes, an \"about me\", project write-ups or prepared interview answers with the paperclip "
                "(Word, PDF, text or Markdown), or paste text straight into this chat. I read every line, ask you a "
                "few questions about what the documents leave open, and then build everything: the profile, the "
                "evidence that resumes may use, the job-search rules for your chosen market, the agents, and "
                f"{self._base_resume_label()}. Nothing here is shared with any other profile.")

    def _base_resume_label(self) -> str:
        """Use a saved page contract when available; otherwise leave the page count open."""
        path = self.job.root / "data/config/profile.yml"
        if path.is_file():
            import yaml

            try:
                profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                profile = {}
            contract = profile.get("resume_contract") if isinstance(profile, dict) else None
            pages = contract.get("required_pages") if isinstance(contract, dict) else None
            if isinstance(pages, int) and not isinstance(pages, bool) and pages > 0:
                count = {1: "one", 2: "two"}.get(pages, str(pages))
                return f"a {count}-page base resume"
        return "a base resume sized for your chosen market"

    def _catch_up(self, chat: dict):
        """Turn what happened in the background (reading finished or failed, a build) into messages."""
        state = self.job.state()
        flags = chat["flags"]
        if state["state"] == STATE_READING and flags.get("reading_for") != state.get("started_at"):
            flags["reading_for"] = state.get("started_at")
            self._post(chat, "assistant", "Reading your documents now. You can watch each step here; it takes a few "
                                          "minutes for a long document.", "progress")
        if state["state"] == STATE_REVIEW and flags.get("found_for") != state.get("finished_at"):
            flags["found_for"] = state.get("finished_at")
            # A new reading (more documents) is a new draft: confirm the essentials against it.
            chat["pending"], chat["asked"], chat["ai_asked"] = None, [], 0
            self._post(chat, "assistant", self._found_text(), "found")
            return self._next
        if state["state"] == STATE_FAILED and flags.get("failed_for") != state.get("error"):
            flags["failed_for"] = state.get("error")
            self._ask(chat, {"key": "retry", "text": "Shall I try again?", "options": [
                _option(TRY_AGAIN, "Read the same documents again"),
                _option(ADD_MORE, "Change the documents first")], "other": False, "skippable": False},
                lead=f"Reading stopped: {state.get('error') or 'an unknown error'}")
        # A build this chat started announces itself once the profile is open (see _build); this
        # covers a build from the form. Posting earlier would reload the page before it is ready.
        if state["state"] == STATE_BUILT and not flags.get("built") and not self.thinking():
            flags["built"] = True
            chat["pending"] = None
            self._post(chat, "assistant", f"{self.first}'s workspace is built. Opening it now.", "built")
        return None

    def _found_text(self) -> str:
        draft = self.job.draft() or {}
        n = lambda key: len(draft.get(key) or [])  # noqa: E731
        skills = sum(len(g.get("skills") or []) for g in draft.get("skills") or [])
        coverage = draft.get("coverage") or {}
        roles = ", ".join((draft.get("targets") or {}).get("roles") or []) or "none named yet"
        pack = next((p["name"] for p in self.packs if p["code"] == draft.get("country_pack")), draft.get("country_pack") or "")
        auth = draft.get("authorization") or {}
        right = " ".join(p for p in (_clean(auth.get("status")), ("until " + _clean(auth["valid_until"])) if _clean(auth.get("valid_until")) else "") if p)
        lines = [f"Done reading. I found {n('education')} education entr{'y' if n('education') == 1 else 'ies'}, "
                 f"{n('experience')} role(s), {n('projects')} project(s), {skills} skills, {n('certifications')} "
                 f"certification(s) and {n('interview_answers')} prepared interview answer(s).",
                 "",
                 f"- **Country:** {pack}",
                 f"- **Roles:** {roles}",
                 f"- **Right to work:** {right or 'not stated'}"]
        if coverage.get("blocks"):
            lines.append(f"- **Coverage:** {coverage.get('accounted')} of {coverage.get('blocks')} parts of your documents "
                         "are accounted for; nothing is dropped.")
        open_questions = len(draft.get("questions") or [])
        lines += ["", "A few questions now, one at a time. Click an answer, type your own, or skip any of them."
                  + (f" Your documents left {open_questions} question(s) open." if open_questions else "")]
        return "\n".join(lines)

    # ---- the person's side ----------------------------------------------------------
    def uploaded(self, names: list[str]) -> dict:
        """Files were added with the paperclip: say so and offer to read them."""
        with self.lock:
            chat = self._load()
            last = chat["messages"][-1] if chat["messages"] else None
            if last and last["role"] == "you" and last["kind"] == "files":
                last["files"] = sorted(set(last.get("files", [])) | set(names))
                last["text"] = "Attached " + ", ".join(last["files"])
            else:
                self._post(chat, "you", "Attached " + ", ".join(names), "files", files=list(names))
            self._offer_read(chat)
            self._save(chat)
        return self.view()

    def _offer_read(self, chat: dict) -> None:
        # Only the newest offer stays open; an older one is simply superseded.
        files = self.job.files()
        self._ask(chat, {"key": "read", "text": "Shall I read them now?", "options": [
            _option(READ_NOW, f"{len(files)} document(s); I ask my questions when I'm done"),
            _option(ADD_MORE, "Attach more with the paperclip, or paste text here")], "other": False, "skippable": False},
            lead=f"Got it: {len(files)} document(s) ready ({', '.join(f['name'] for f in files)}).")

    def message(self, text: str) -> dict:
        text = str(text or "").strip()
        if not text:
            raise ValueError("Type a message first.")
        with self.lock:
            if self.thinking():
                raise ValueError("One moment: I'm still working on your last answer.")
            chat = self._load()
            pending = chat.get("pending")
            if pending:
                # Typing an option's name counts as clicking it ("Ireland", "yes", "build my workspace").
                typed = text.casefold().strip(" .!")
                picked = [o["value"] for o in pending.get("options") or []
                          if typed in (o["label"].casefold(), str(o["value"]).casefold())]
                if picked:
                    return self._answer(chat, pending, picked[:1], "")
                if pending.get("other"):
                    return self._answer(chat, pending, [], text)
                if pending.get("key") not in ("read", "retry"):
                    self._post(chat, "you", text)
                    self._post(chat, "assistant", "Pick one of the answers above, or type its name.")
                    self._save(chat)
                    return self.view()
            state = self.job.state()["state"]
            self._post(chat, "you", text)
            if state == STATE_READING:
                if RE_STOP.match(text):
                    self.job.cancel()
                    self._post(chat, "assistant", "Stopping after the sections already in progress.")
                else:
                    self._post(chat, "assistant", "I'm still reading your documents; I'll ask my questions as soon as I'm "
                                                  "done. Type \"stop\" to stop reading.")
            elif state in (STATE_BUILDING, STATE_BUILT):
                self._post(chat, "assistant", "The workspace is being built; it opens by itself in a moment.")
            elif state == STATE_REVIEW:
                chat["pending"] = None
                if RE_BUILD.match(text):
                    self._offer_build(chat)
                else:
                    self._save(chat)
                    self._spawn(lambda: self._next(note=text))
                    return self.view()
            elif RE_READ.match(text) and self.job.files():
                self._start_reading(chat)
            else:
                self._note(text)
                chat["pending"] = None
                self._post(chat, "assistant", f"Saved that to {NOTES_FILE}; I read it together with your documents.")
                self._offer_read(chat)
            self._save(chat)
        return self.view()

    def answer(self, question_id: str, choices: list[str], other: str = "", skip: bool = False) -> dict:
        with self.lock:
            if self.thinking():
                raise ValueError("One moment: I'm still working on your last answer.")
            chat = self._load()
            pending = chat.get("pending")
            if not pending or pending.get("id") != question_id:
                raise ValueError("That question was already answered. Scroll down to the latest one.")
            if skip:
                if not pending.get("skippable", True):
                    raise ValueError("Pick one of the answers to go on.")
                return self._answer(chat, pending, [], "", skipped=True)
            return self._answer(chat, pending, [str(c) for c in choices if str(c).strip()], str(other or "").strip())

    def _answer(self, chat: dict, question: dict, choices: list[str], other: str, skipped: bool = False) -> dict:
        options = {o["value"]: o["label"] for o in question.get("options") or []}
        chosen = [c for c in choices if c in options]
        shown = ", ".join([options[c] for c in chosen] + ([other] if other else []))
        if not skipped and not shown:
            raise ValueError("Pick an answer, type one, or skip the question.")
        self._post(chat, "you", "Skipped" if skipped else shown, "answer", answers=question["id"])
        chat["pending"] = None
        key = question.get("key")
        if key == "read":
            if READ_NOW in chosen or RE_READ.match(other or ""):
                self._start_reading(chat)
            else:
                if other:
                    self._note(other)
                self._post(chat, "assistant", "Sure. Attach the rest with the paperclip or paste text here; I'll ask "
                                              "again when they're in.")
            self._save(chat)
            return self.view()
        if key == "retry":
            if TRY_AGAIN in chosen:
                self._start_reading(chat)
            else:
                self._post(chat, "assistant", "Remove or add documents with the paperclip, then I'll read them again.")
            self._save(chat)
            return self.view()
        if key == "build":
            if BUILD_NOW in chosen:
                self._post(chat, "assistant", "Building the workspace: the profile, evidence registry, context files, "
                                              f"job-search rules and agents, then {self._base_resume_label()}.", "progress")
                self._save(chat)
                self._spawn(self._build)
                return self.view()
            if other:
                self._save(chat)
                self._spawn(lambda: self._next(note=other, force=True))
                return self.view()
            self._post(chat, "assistant", "Tell me what to change: a role, a city, your right to work, a contact "
                                          "detail, or anything the documents got wrong.")
            self._save(chat)
            return self.view()
        if not skipped:
            self._apply(question, chosen, other, shown)
        self._save(chat)
        self._spawn(self._next)
        return self.view()

    # ---- applying answers -----------------------------------------------------------
    def _apply(self, question: dict, chosen: list[str], other: str, shown: str) -> None:
        field = question.get("field") or "note"
        if field == "country_pack" and chosen:
            self.job.update({"country_pack": chosen[0]})
        elif field == "needs_sponsorship_later":
            value = chosen[0] if chosen else "unknown"
            self.job.update({"authorization": {"needs_sponsorship_later": value if value in ("yes", "no") else "unknown"}})
            draft = self.job.draft() or {}
            if (draft.get("country_pack") or "ie") == "ie":
                auth = dict((draft.get("work_authorization_by_market") or {}).get("ie") or {})
                auth["needs_sponsorship_later"] = value
                self.job.update({"work_authorization_by_market": {"ie": auth}})
        elif field.startswith("permit."):
            self._apply_permission(field.split(".", 1)[1], chosen, other, shown)
        elif field.startswith("permit_education."):
            self._apply_degree(field.split(".", 1)[1], chosen, other)
        elif field == "graduate_search_confirmed":
            preferences = dict((self.job.draft() or {}).get("job_search") or {})
            preferences.update(graduate_search_confirmed=bool(chosen and chosen[0] == "yes"))
            if preferences["graduate_search_confirmed"]:
                preferences.update(seniority=["graduate", "junior", "entry"], max_years_required=3)
            self.job.update({"job_search": preferences})
        elif field in LIST_FIELDS:
            self.job.update({field: list(dict.fromkeys(chosen + _items(other)))})
        elif field in TEXT_FIELDS:
            self._set(field, other or (chosen[0] if chosen else ""))
        self.job.record_answer(question["text"], shown, question.get("resolves") or "")

    def _apply_permission(self, key: str, chosen: list[str], other: str, shown: str) -> None:
        """Irish permission facts from the person's answer; the type alone sets only what it defines."""
        from backend.services.intake.authorization import date_proposal, iso_date

        draft = self.job.draft() or {}
        facts = dict((draft.get("work_authorization_by_market") or {}).get("ie") or {})
        if key == "permission_type":
            value = chosen[0] if chosen else ("other" if other else "unknown")
            facts["permission_type"] = value
            if other:
                facts["permission_wording"] = other
            if value == "irish_or_eea_citizen":
                facts.update(status="authorized", citizenship="citizen", needs_sponsorship_later="no")
                self.job.update({"authorization": {"needs_sponsorship_later": "no"}})
            elif value in FULL_TIME_PERMISSIONS:
                facts.update(status="authorized", citizenship="noncitizen")
            elif value in {"stamp_2", "other"}:
                facts["citizenship"] = "noncitizen"  # they hold an Irish permission, so they are not a citizen
            if value != "unknown" and not _clean((draft.get("authorization") or {}).get("status")):
                # Their own answer is the wording the profile shows ("who holds Stamp 1G").
                self.job.update({"authorization": {"status": shown}})
        elif key == "status":
            facts["status"] = chosen[0] if chosen else "unknown"
        elif key == "expiry":
            typed = other or (chosen[0] if chosen else "")
            proposal = date_proposal(typed)
            if proposal["precision"] != "day" or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", typed.strip()):
                raise ValueError("Type the exact expiry day as YYYY-MM-DD, for example 2027-12-31.")
            facts.update(valid_until=iso_date(typed.strip(), "Permission expiry"), valid_until_confirmed=True,
                         valid_until_raw=facts.get("valid_until_raw") or _clean((draft.get("authorization") or {}).get("valid_until")) or typed.strip())
            if not _clean((draft.get("authorization") or {}).get("valid_until")):
                self.job.update({"authorization": {"valid_until": typed.strip()}})
        self.job.update({"work_authorization_by_market": {"ie": facts}})

    def _apply_degree(self, key: str, chosen: list[str], other: str) -> None:
        from backend.services.intake.authorization import iso_date, optional_bool

        facts = dict((self.job.draft() or {}).get("education_for_permits") or {})
        if key == "award":
            typed = (other or (chosen[0] if chosen else "")).strip()
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", typed):
                raise ValueError("Type the exact award day as YYYY-MM-DD, or skip the question.")
            facts.update(award_date=iso_date(typed, "Degree award date"), award_date_confirmed=True,
                         award_date_raw=facts.get("award_date_raw") or typed)
        elif key == "degree":
            facts["nfq_level"], facts["irish_institution"] = DEGREE_CHOICES.get(chosen[0] if chosen else "unknown", (None, None))
        elif key == "relevant_degree":
            facts["relevant_degree"] = optional_bool(chosen[0] if chosen else "unknown", "Degree relevance")
        self.job.update({"education_for_permits": facts})

    def _set(self, field: str, value: str) -> None:
        if not value:
            return
        if field in LIST_FIELDS:
            self.job.update({field: _items(value.replace(" | ", ","))})
        elif TEXT_FIELDS.get(field) == "authorization":
            self.job.update({"authorization": {AUTH_KEYS[field]: value}})
        elif field in TEXT_FIELDS:
            self.job.update({field: value})

    def _note(self, text: str) -> None:
        """Free text before reading is kept as a document of its own, read with the others."""
        path = self.job.files_dir / NOTES_FILE
        earlier = path.read_text(encoding="utf-8") if path.is_file() else f"# Notes {self.first} typed in the setup chat\n"
        self.job.add_file(NOTES_FILE, (earlier.rstrip() + "\n\n" + text.strip() + "\n").encode("utf-8"))

    def _start_reading(self, chat: dict) -> None:
        chat["pending"] = None
        self.job.start()
        self._catch_up(chat)

    # ---- the next question ------------------------------------------------------------
    def _spawn(self, work: Callable) -> None:
        def run():
            try:
                work()
            except Exception as error:  # noqa: BLE001 - the chat says what went wrong and stays usable
                with self.lock:
                    chat = self._load()
                    self._post(chat, "assistant", f"Something went wrong: {str(error)[:400]}", "error")
                    self._offer_build(chat)
                    self._save(chat)

        self.worker = threading.Thread(target=run, name="setup-chat", daemon=True)
        self.worker.start()

    def _next(self, note: str = "", force: bool = False) -> None:
        """Ask the next question: an essential one, the interviewer's, or finally the build."""
        with self.lock:
            chat = self._load()
            draft = self.job.draft() or {}
        essential = None if note else self._essential(draft, chat["asked"])
        if essential:
            with self.lock:
                chat = self._load()
                self._ask(chat, essential)
                self._save(chat)
            return
        turn, failure = None, ""
        if chat["ai_asked"] < MAX_AI_QUESTIONS or note or force:
            try:
                turn = self._interview(draft, chat, note)
            except Exception as error:  # noqa: BLE001 - fall back to the documents' own open questions
                failure = str(error)[:300]
        with self.lock:
            chat = self._load()
            if turn:
                targets = (self.job.draft() or {}).get("targets") or {}
                adding = bool(RE_ADDING.search(note)) and not RE_REPLACING.search(note)
                for update in turn.get("updates") or []:
                    field, value = update.get("field"), _clean(update.get("value"))
                    if field in LIST_FIELDS and adding:
                        # "Also include Limerick" adds to the list, whatever list the model returned.
                        value = " | ".join(dict.fromkeys([*(targets.get(field) or []), *_items(value.replace(" | ", ","))]))
                    if field in FIELDS:
                        self._set(field, value)
                if note:
                    self.job.record_answer("Note", note)
                question = _plain(turn.get("question"))
                if (turn.get("action") == "ask" and question and question not in chat["asked"]
                        and chat["ai_asked"] < MAX_AI_QUESTIONS):
                    chat["ai_asked"] += 1
                    options = [_option(_plain(o.get("label"))[:60], _plain(o.get("description"))[:160])
                               for o in (turn.get("options") or [])[:5] if _plain(o.get("label"))]
                    field = turn.get("field") if turn.get("field") in FIELDS else "note"
                    self._ask(chat, {"text": question, "why": _plain(turn.get("why")), "options": options,
                                     "multi": bool(turn.get("multi_select")) and len(options) > 1, "field": field,
                                     "resolves": turn.get("resolves") if turn.get("resolves") in (draft.get("questions") or []) else ""},
                              lead=_plain(turn.get("say")))
                    self._save(chat)
                    return
                if _plain(turn.get("say")):
                    self._post(chat, "assistant", _plain(turn.get("say")))
            else:
                if note:
                    self.job.record_answer("Note", note)
                    self._post(chat, "assistant", "Noted; it is kept with your profile in your own words.")
                open_question = next((q for q in (self.job.draft() or {}).get("questions") or [] if q not in chat["asked"]), None)
                if open_question and chat["ai_asked"] < MAX_AI_QUESTIONS:
                    chat["ai_asked"] += 1
                    lead = (f"(I could not reach the AI for a tailored question: {failure}) " if failure and not chat["flags"].get("ai_failed") else "")
                    chat["flags"]["ai_failed"] = bool(failure) or chat["flags"].get("ai_failed", False)
                    self._ask(chat, {"text": _plain(open_question), "why": "Your documents left this open.", "resolves": open_question},
                              lead=lead + "Your documents left this open:")
                    self._save(chat)
                    return
            self._offer_build(chat)
            self._save(chat)

    def _essential(self, draft: dict, asked: list) -> dict | None:
        """The questions every workspace needs answered, in order; each is asked once."""
        contact, auth, targets = draft.get("contact") or {}, draft.get("authorization") or {}, draft.get("targets") or {}
        guessed = draft.get("country_pack") or (self.packs[0]["code"] if self.packs else "ie")
        pack = next((p for p in self.packs if p["code"] == guessed), {"name": guessed, "code": guessed, "paper": ""})
        roles = [r for r in targets.get("roles") or [] if _clean(r)][:6]
        questions = []
        if not _clean(contact.get("full_name")):
            questions.append({"key": "full_name", "field": "full_name", "text": "What is your full name, as it should appear on your resume?",
                              "skippable": False, "placeholder": "First and last name"})
        packs = sorted(self.packs, key=lambda p: p["code"] != guessed)
        if len(packs) > 1:  # a copy that offers one market (countries/markets.yml) has nothing to ask
            questions.append({"key": "country", "field": "country_pack", "other": False, "skippable": False,
                              "text": "Which country should I search for jobs in?",
                              "why": "It decides the resume's paper and spelling, the time zone, and the work-permit rules that screen every posting.",
                              "options": [_option(p["name"], (f"From your documents · {p['paper']} resumes" if p["code"] == guessed
                                                             else f"{p['paper']} resumes"), p["code"]) for p in packs]})
        questions.append({"key": "roles", "field": "roles", "multi": True, "skippable": False,
                          "text": "Which roles should I search for?" if roles else "Which roles should I search for? Your documents don't name one.",
                          "why": "Daily Search looks for these titles, and every resume is tailored to one of them.",
                          "options": [_option(r) for r in roles], "defaults": roles,
                          "placeholder": "Other roles, separated by commas"})
        if not [c for c in targets.get("cities") or [] if _clean(c)]:
            # Where to look matters as much as what to look for; the interviewer tends to spend its
            # few questions on the documents' open points, so this one is asked here.
            home = _clean(contact.get("city"))
            questions.append({"key": "cities", "field": "cities", "multi": True,
                              "text": "Where would you like to work?",
                              "why": "Daily Search looks in these places first.",
                              "placeholder": "Other cities, separated by commas",
                              "options": ([_option(home, "Where you live, from your documents")] if home else [])
                              + [_option(f"Anywhere in {pack['name']}", "Search the whole country"),
                                 _option("Remote", "Fully remote roles")]})
        irish = pack["code"] == "ie"
        facts = (draft.get("work_authorization_by_market") or {}).get(pack["code"]) or {}
        if irish:
            # One structured question replaces the free-text one: it records the permission type
            # and, where the type itself defines them, the status and citizenship answers.
            questions += _irish_permission_questions(auth, facts)
        elif not _clean(auth.get("status")):
            questions.append({"key": "auth", "field": "authorization_status",
                              "text": f"What is your current permission to work in {pack['name']}, and until when is it valid?",
                              "why": "Postings you cannot lawfully take are set aside, with the sentence that ruled them out.",
                              "placeholder": "For example: F-1 OPT until June 2027, green card, citizen"})
        citizen = irish and facts.get("citizenship") == "citizen"
        if not citizen and (auth.get("needs_sponsorship_later") not in ("yes", "no")
                            or (irish and facts.get("needs_sponsorship_later") not in ("yes", "no"))):
            questions.append({"key": "sponsor", "field": "needs_sponsorship_later", "other": False, "skippable": False,
                              "text": "Will you need an employer to sponsor a work permit, now or later?",
                              "why": "If yes, postings that refuse to sponsor are set aside; you can restore any wrong call.",
                              "options": [_option("Yes", "Now or in the future", "yes"),
                                          _option("No, never", "I can work there without an employer's permit", "no"),
                                          _option("Not sure", "Treated as yes, so no posting is missed by mistake", "unknown")]})
        if irish:
            questions += _irish_graduate_questions(draft, auth, facts)
        for key, label in (("email", "email address"), ("phone", "phone number")):
            if not _clean(contact.get(key)):
                questions.append({"key": key, "field": key, "text": f"Which {label} should go on your resume?",
                                  "why": "Every resume carries it in the header.", "placeholder": label.capitalize()})
        return next((q for q in questions if q["key"] not in asked), None)

    def _interview(self, draft: dict, chat: dict, note: str) -> dict:
        team = self.make_team(self.job._usage)
        pack = next((p["name"] for p in self.packs if p["code"] == draft.get("country_pack")), draft.get("country_pack"))
        conversation = [{"who": "you" if m["role"] == "you" else "assistant", "text": m["text"][:800]}
                        for m in chat["messages"][-16:] if m["kind"] not in ("progress", "greeting")]
        payload = {
            "person": self.first,
            "country": pack,
            "found": {
                "contact": {k: v for k, v in (draft.get("contact") or {}).items() if k != "refs"},
                "authorization": {k: v for k, v in (draft.get("authorization") or {}).items() if k != "refs"},
                "targets": {k: v for k, v in (draft.get("targets") or {}).items() if k != "refs"},
                "education": [{k: e.get(k) for k in ("degree", "field", "institution", "start", "end")} for e in draft.get("education") or []],
                "experience": [{k: r.get(k) for k in ("title", "employer", "start", "end", "location")} for r in draft.get("experience") or []],
                "projects": [p.get("name") for p in draft.get("projects") or []],
                "skills": [g.get("name") for g in draft.get("skills") or []],
                "certifications": [c.get("name") for c in draft.get("certifications") or []],
            },
            "open_questions": draft.get("questions") or [],
            "answers_so_far": [{"question": a.get("question"), "answer": a.get("answer")} for a in draft.get("answers") or []],
            "conversation": conversation,
            "last_message": note,
            "fields": FIELDS,
            "questions_left": max(0, MAX_AI_QUESTIONS - chat["ai_asked"]),
        }
        return team.run("intake_interviewer", payload).model_dump()

    def _offer_build(self, chat: dict) -> None:
        draft = self.job.draft() or {}
        targets = draft.get("targets") or {}
        pack = next((p["name"] for p in self.packs if p["code"] == draft.get("country_pack")), draft.get("country_pack"))
        open_count = len(draft.get("questions") or [])
        lines = ["Here's what I'll build:", "",
                 f"- **Search:** {', '.join(targets.get('roles') or []) or 'no roles yet'} in {pack}"
                 + (f" ({', '.join(targets['cities'])})" if targets.get("cities") else ""),
                 f"- **Right to work:** {_clean((draft.get('authorization') or {}).get('status')) or 'not stated'}",
                 f"- **Answers you gave here:** {len(draft.get('answers') or [])}, kept in your own words"]
        if open_count:
            lines.append(f"- **Still open:** {open_count} question(s), saved so you can answer them later in the Assistant")
        if not (targets.get("roles") and _clean((draft.get("contact") or {}).get("full_name"))):
            self._post(chat, "assistant", "\n".join(lines) + "\n\nI still need your full name and at least one role before I can build.")
            chat["asked"] = [k for k in chat["asked"] if k not in ("full_name", "roles")]
            chat["pending"] = None
            essential = self._essential(draft, chat["asked"])
            if essential:
                self._ask(chat, essential)
            return
        self._ask(chat, {"key": "build", "text": "Shall I build the workspace now?", "other": True, "skippable": False,
                         "placeholder": "Or tell me what to change",
                         "options": [_option(BUILD_NOW, "Builds the workspace and checks the base PDF before opening it"),
                                     _option(CHANGE, "Correct a role, a city, a contact detail…")]},
                  lead="\n".join(lines))

    def _build(self) -> None:
        try:
            self.build()
        except Exception as error:  # noqa: BLE001 - said in the chat, with a retry
            with self.lock:
                chat = self._load()
                self._post(chat, "assistant", f"Building stopped: {str(error)[:400]}", "error")
                self._offer_build(chat)
                self._save(chat)
            return
        with self.lock:
            chat = self._load()
            if chat["flags"].get("built"):
                return
            chat["flags"]["built"] = True
            chat["pending"] = None
            self._post(chat, "assistant", f"{self.first}'s workspace is ready: the profile, evidence registry, context "
                                          "files, job-search rules and agents are written, and the base resume passed "
                                          "automated checks. Opening the full workspace now.", "built")
            self._save(chat)
