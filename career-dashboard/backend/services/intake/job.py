"""One profile's intake, from uploaded files to a built workspace.

    upload  -> data/context/files/<name>             (the person's own files, kept)
    analyse -> data/intake/blocks.json               every block, numbered
               data/context/sources/<name>.md        the readable copy each entry cites
               data/intake/sections/NN.json          what the AI found per section
               data/intake/audits/NN.json            what the completeness check added
               data/intake/draft.json                merged, with the coverage ledger
    review  -> the person checks names, contacts, country, right to work, roles
    build   -> the file set written in one step (build.py), then the profile is ready

State lives in data/intake/state.json so the page can follow progress and a restart
never loses finished sections. Nothing is written outside the profile's folder.
"""

from __future__ import annotations

import json
import hashlib
import re
import shutil
import threading
import time
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from backend.services.intake.extract import MAX_BYTES, SUPPORTED, Block, blocks_for, chunk_text, chunks, source_markdown
from backend.services.source_library import SourceLibrary

STATE_EMPTY, STATE_UPLOADED, STATE_READING, STATE_REVIEW, STATE_BUILDING, STATE_BUILT, STATE_FAILED = (
    "empty", "uploaded", "reading", "review", "building", "built", "failed")
PARALLEL_SECTIONS = 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(name: str) -> str:
    stem = Path(str(name or "document")).name
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .") or "document"
    return stem[:120]


class Cancelled(Exception):
    pass


class IntakeJob:
    def __init__(self, root: Path, team_factory=None):
        self.root = Path(root)
        self.dir = self.root / "data/intake"
        self.files_dir = self.root / "data/context/files"
        self.library = SourceLibrary(self.root)
        self.team_factory = team_factory
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.cancelled = threading.Event()

    # ---- state ---------------------------------------------------------------
    # Reads and writes share one lock: the page polls state.json while the analysis thread
    # rewrites it, and on Windows a read during the replace fails (it would show "uploaded"
    # mid-analysis) or the replace fails while a read holds the file.
    def _read(self, name: str, default):
        path = self.dir / name
        with self.lock:
            for attempt in range(5):
                if not path.is_file():
                    return default
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    return default  # a damaged file (a cached section is simply read again)
                except OSError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))  # held for a moment by another program
        return default

    def _write(self, name: str, value) -> None:
        path = self.dir / name
        with self.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
            for attempt in range(5):
                try:
                    temp.replace(path)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))

    def state(self) -> dict:
        state = self._read("state.json", {})
        state.setdefault("state", STATE_UPLOADED if self.files() else STATE_EMPTY)
        state.setdefault("steps", [])
        state["files"] = self.files()
        state["running"] = bool(self.thread and self.thread.is_alive())
        return state

    def _save(self, **changes) -> dict:
        with self.lock:
            state = self._read("state.json", {})
            state.update(changes, updated_at=_now())
            self._write("state.json", state)
            return state

    def _step(self, label: str, status: str = "running", detail: str = "") -> None:
        with self.lock:
            state = self._read("state.json", {})
            steps = state.setdefault("steps", [])
            for step in steps:
                if step["label"] == label:
                    step.update(status=status, detail=detail, at=_now())
                    break
            else:
                steps.append({"label": label, "status": status, "detail": detail, "at": _now()})
            state["updated_at"] = _now()
            self._write("state.json", state)

    # ---- files -----------------------------------------------------------------
    def files(self) -> list[dict]:
        self.library.import_existing()
        if not self.files_dir.is_dir():
            return []
        return [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(self.files_dir.iterdir())
                if p.is_file() and p.suffix.lower() in SUPPORTED]

    def add_file(self, name: str, data: bytes) -> dict:
        name = safe_name(name)
        if Path(name).suffix.lower() not in SUPPORTED:
            raise ValueError(f"{name}: upload a Word (.docx), PDF, text (.txt) or Markdown (.md) file.")
        if not data:
            raise ValueError(f"{name} is empty.")
        if len(data) > MAX_BYTES:
            raise ValueError(f"{name} is larger than 20 MB. Upload a smaller copy.")
        with self.lock:
            if self.state()["state"] in (STATE_READING, STATE_BUILDING):
                raise ValueError("Wait for the current step to finish before adding another document.")
            self.library.add_upload(name, data)
            # A new document means the draft must be read again.
            return self._save(state=STATE_UPLOADED, steps=[], error=None)

    def remove_file(self, name: str) -> dict:
        with self.lock:
            if self.state()["state"] in (STATE_READING, STATE_BUILDING):
                raise ValueError("Wait for the current step to finish first.")
            source = next((s for s in self.library.list() if s["name"] == safe_name(name)), None)
            if source:
                self.library.set_active(source["id"], False)
            return self._save(state=STATE_UPLOADED if self.files() else STATE_EMPTY, steps=[], error=None)

    def add_note(self, name: str, text: str) -> dict:
        with self.lock:
            if self.state()["state"] in (STATE_READING, STATE_BUILDING):
                raise ValueError("Wait for the current build to finish before adding a note.")
            source = self.library.add_note(name, text)
            self._save(state=STATE_UPLOADED, steps=[], error=None)
            return source

    def add_version(self, source_id: str, data: bytes) -> dict:
        with self.lock:
            if self.state()["state"] in (STATE_READING, STATE_BUILDING):
                raise ValueError("Wait for the current build to finish before changing a source.")
            source = self.library.add_version(source_id, data)
            self._save(state=STATE_UPLOADED, steps=[], error=None)
            return source

    def set_source_active(self, source_id: str, active: bool) -> dict:
        with self.lock:
            if self.state()["state"] in (STATE_READING, STATE_BUILDING):
                raise ValueError("Wait for the current build to finish before changing sources.")
            source = self.library.set_active(source_id, active)
            self._save(state=STATE_UPLOADED if self.files() else STATE_EMPTY, steps=[], error=None)
            return source

    # ---- analyse ----------------------------------------------------------------
    def start(self) -> dict:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.state()
            if not self.files():
                raise ValueError("Upload at least one document about you first.")
            if self.team_factory is None:
                raise ValueError("No AI is set up to read documents.")
            self.cancelled.clear()
            self._save(state=STATE_READING, steps=[], error=None, started_at=_now(), usage={"calls": 0, "input_tokens": 0, "output_tokens": 0})
            self.thread = threading.Thread(target=self._run, name="profile-intake", daemon=True)
            self.thread.start()
            return self.state()

    def cancel(self) -> None:
        self.cancelled.set()

    def _usage(self, usage: dict) -> None:
        with self.lock:
            state = self._read("state.json", {})
            total = state.setdefault("usage", {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            total["calls"] = total.get("calls", 0) + 1
            for key in ("input_tokens", "output_tokens"):
                total[key] = total.get(key, 0) + int(usage.get(key) or 0)
            self._write("state.json", state)

    def _run(self) -> None:
        try:
            self._analyse()
        except Cancelled:
            self._save(state=STATE_UPLOADED, error="Stopped.")
        except Exception as error:  # noqa: BLE001 - the page shows the reason and offers a retry
            self._save(state=STATE_FAILED, error=str(error)[:600])

    def _analyse(self) -> None:
        from backend.services.intake.coverage import ledger
        from backend.services.intake.merge import merge

        self._step("Reading your documents")
        files = [(self.files_dir / f["name"], f["name"]) for f in self.files()]
        blocks = blocks_for(files)
        if not blocks:
            raise ValueError("No text could be read from the documents. Check the scanned PDF and retry with the bundled OCR support.")
        self.library.capture_extraction(blocks)
        self._write("blocks.json", [b.as_dict() for b in blocks])
        sources_dir = self.root / "data/context/sources"
        sources_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().date().isoformat()
        for _, name in files:
            (sources_dir / (Path(name).stem + ".md")).write_text(source_markdown(blocks, name, today), encoding="utf-8")
        groups = chunks(blocks)
        self._step("Reading your documents", "done",
                   f"{len(files)} document(s), {len(blocks)} blocks, {sum(len(b.text) for b in blocks):,} characters, {len(groups)} sections")
        team = self.team_factory(self._usage)
        results: list = [None] * len(groups)
        audits: list = [None] * len(groups)
        label = "Finding every fact, section by section"
        self._step(label)

        def one(index: int):
            if self.cancelled.is_set():
                raise Cancelled()
            group = groups[index]
            cached = self._read(f"sections/{index:02d}.json", None)
            cached_audit = self._read(f"audits/{index:02d}.json", None)
            fingerprint = [hashlib.sha256(f"{b.id}\0{b.source}\0{b.kind}\0{b.text}".encode("utf-8")).hexdigest()
                           for b in group]
            if cached and cached.get("fingerprint") == fingerprint and cached_audit is not None:
                return index, cached["facts"], cached_audit
            payload = {"section": f"{index + 1} of {len(groups)}", "documents": sorted({b.source for b in group}),
                       "text": chunk_text(group)}
            facts = self._retry(lambda: team.run("profile_extractor", payload).model_dump())
            valid = {b.id for b in group}
            _clean_refs(facts, valid)
            self._write(f"sections/{index:02d}.json", {"fingerprint": fingerprint, "facts": facts})
            if self.cancelled.is_set():
                raise Cancelled()
            try:
                audit = self._retry(lambda: team.run("intake_auditor", {"text": payload["text"], "extracted": facts}).model_dump())
                _clean_refs(audit, valid)
            except Exception as error:  # noqa: BLE001 - the ledger still keeps every block verbatim
                audit = {"missed": [], "error": str(error)[:300]}
            self._write(f"audits/{index:02d}.json", audit)
            return index, facts, audit

        done = 0
        with ThreadPoolExecutor(max_workers=PARALLEL_SECTIONS, thread_name_prefix="intake-section") as pool:
            for index, facts, audit in pool.map(one, range(len(groups))):
                results[index], audits[index] = facts, audit
                done += 1
                self._step(label, "running", f"{done} of {len(groups)} sections read")
        self._step(label, "done", f"{len(groups)} of {len(groups)} sections read")

        self._step("Putting it together and checking nothing was missed")
        draft = merge(results, audits)
        coverage = ledger([b.as_dict() for b in blocks], draft)
        draft["coverage"] = coverage
        draft["country_pack"] = guess_pack(draft)
        draft["target_markets"] = [draft["country_pack"]]
        from backend.services.intake.build import clean_roles

        draft["targets"]["roles"] = clean_roles(draft["targets"].get("roles"))
        draft["authorization"].setdefault("needs_sponsorship_later", "unknown")
        # Answers typed in the setup chat are the person's words: reading again keeps them.
        earlier = self.draft() or {}
        if earlier.get("answers"):
            draft["answers"] = earlier["answers"]
        self._write("draft.json", draft)
        found = (f"{len(draft['education'])} education entr{'y' if len(draft['education']) == 1 else 'ies'}, {len(draft['experience'])} role(s), {len(draft['projects'])} project(s), "
                 f"{sum(len(g.get('skills') or []) for g in draft['skills'])} skills, {len(draft['certifications'])} certification(s), "
                 f"{len(draft['interview_answers'])} interview answer(s)")
        self._step("Putting it together and checking nothing was missed", "done",
                   f"{found}. {coverage['accounted']} of {coverage['blocks']} blocks accounted for.")
        self._save(state=STATE_REVIEW, finished_at=_now())

    def _retry(self, call, attempts: int = 2):
        for attempt in range(attempts):
            try:
                return call()
            except Exception:  # noqa: BLE001 - one retry, then the error reaches the page
                if attempt == attempts - 1 or self.cancelled.is_set():
                    raise
                time.sleep(2)

    # ---- review --------------------------------------------------------------
    def draft(self) -> dict | None:
        return self._read("draft.json", None)

    def update(self, changes: dict) -> dict:
        """The person's corrections on the review card: contacts, country, right to work, roles."""
        with self.lock:
            draft = self.draft()
            if draft is None:
                raise ValueError("Read the documents first.")
            contact = draft.setdefault("contact", {})
            for key in ("full_name", "preferred_name", "email", "phone", "linkedin", "github", "portfolio_url", "city", "country"):
                if key in changes:
                    contact[key] = str(changes[key] or "").strip()[:200]
            auth = draft.setdefault("authorization", {})
            for key in ("status", "valid_until", "conditions", "work_country"):
                if key in changes.get("authorization", {}):
                    auth[key] = str(changes["authorization"][key] or "").strip()[:200]
            if changes.get("authorization", {}).get("needs_sponsorship_later") in ("yes", "no", "unknown"):
                auth["needs_sponsorship_later"] = changes["authorization"]["needs_sponsorship_later"]
            if isinstance(changes.get("roles"), list):
                draft.setdefault("targets", {})["roles"] = [str(r).strip()[:80] for r in changes["roles"] if str(r).strip()][:12]
            # Preferences the setup chat asks about (cities, working arrangement, seniority...).
            targets = draft.setdefault("targets", {})
            for key in ("cities", "arrangements", "countries"):
                if isinstance(changes.get(key), list):
                    targets[key] = [str(v).strip()[:80] for v in changes[key] if str(v).strip()][:12]
            for key in ("seniority", "salary", "availability"):
                if key in changes:
                    targets[key] = str(changes[key] or "").strip()[:200]
            if changes.get("country_pack"):
                from backend.countries import load_pack

                load_pack(str(changes["country_pack"]))  # refuses an unknown pack
                draft["country_pack"] = str(changes["country_pack"]).lower()
            if "target_markets" in changes:
                markets = [str(item).lower() for item in changes["target_markets"]]
                if not markets or len(markets) != len(set(markets)) or any(item not in {"ie", "us"} for item in markets):
                    raise ValueError("Choose Ireland, the US, or both markets.")
                draft["target_markets"] = markets
                draft["country_pack"] = markets[0]
            if "work_authorization_by_market" in changes:
                values = changes["work_authorization_by_market"]
                if not isinstance(values, dict) or any(k not in {"ie", "us"} or not isinstance(v, dict) for k, v in values.items()):
                    raise ValueError("Work authorization must be keyed by ie or us.")
                normalized = {}
                for market, value in values.items():
                    status = value.get("status", "unknown")
                    citizenship = value.get("citizenship", "unknown")
                    later = value.get("needs_sponsorship_later", "unknown")
                    if status not in {"authorized", "needs_sponsorship", "unknown"} or citizenship not in {"citizen", "noncitizen", "unknown"} or later not in {"yes", "no", "unknown"}:
                        raise ValueError("Choose valid work-authorization, citizenship and future sponsorship answers, or leave them unknown.")
                    normalized[market] = {"status": status, "citizenship": citizenship,
                                          "needs_sponsorship_later": later}
                draft["work_authorization_by_market"] = normalized
            self._write("draft.json", draft)
            return draft

    def record_answer(self, question: str, answer: str, resolves: str = "") -> dict:
        """An answer given in the setup chat, in the person's own words.

        It is kept with the draft (the build writes it to QUESTIONS-FOR-YOU.md and
        09-anything-else.md), and the open question it settles leaves the open list.
        """
        with self.lock:
            draft = self.draft()
            if draft is None:
                raise ValueError("Read the documents first.")
            entry = {"question": str(question).strip()[:500], "answer": str(answer).strip()[:2000],
                     "at": datetime.now().date().isoformat()}
            draft.setdefault("answers", []).append(entry)
            if resolves and resolves in (draft.get("questions") or []):
                draft["questions"].remove(resolves)
                draft.setdefault("resolved_questions", []).append(resolves)
            self._write("draft.json", draft)
            return draft

    # ---- build ---------------------------------------------------------------
    def build(self) -> dict:
        """Write the whole file set (build.py) into the profile folder in one step."""
        from backend.countries import load_pack
        from backend.services.intake.build import file_set

        with self.lock:
            draft = self.draft()
            if draft is None or self.state()["state"] not in (STATE_REVIEW, STATE_BUILT, STATE_FAILED):
                raise ValueError("Read the documents and review what was found first.")
            if not str(draft.get("contact", {}).get("full_name") or "").strip():
                raise ValueError("Add your full name on the review card before building.")
            self._save(state=STATE_BUILDING, error=None)
            try:
                blocks = self._read("blocks.json", [])
                pack = load_pack(draft.get("country_pack") or "ie")
                today = datetime.now().date().isoformat()
                files = [f["name"] for f in self.files()]
                revision = self._next_revision(today)
                texts = file_set(draft, blocks, draft.get("coverage") or {}, pack, files, today, revision=revision,
                                 source_snapshot=self.library.active_snapshot())
                self._commit_file_set(texts)
            except Exception as error:
                self._save(state=STATE_FAILED, error="Building the workspace failed: " + str(error)[:500])
                raise
            summary = {"files": sorted(texts), "pack": pack.code, "built_at": _now(), "revision": revision}
            self._save(state=STATE_BUILT, built=summary)
            return summary

    def _next_revision(self, today: str) -> str:
        path = self.root / "data/config/profile.yml"
        if path.is_file():
            try:
                import yaml

                before = str((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("candidate_revision") or "")
                match = re.fullmatch(re.escape(today) + r"\.(\d+)", before)
                if match:
                    return f"{today}.{int(match[1]) + 1}"
            except (OSError, ValueError):
                pass
        return today + ".1"

    def _commit_file_set(self, texts: dict[str, str]) -> None:
        """Stage every new file and roll back all replacements if any swap fails."""
        temporary = Path(tempfile.mkdtemp(prefix=".build-", dir=self.root))
        staged = temporary / "new"
        backup = temporary / "before"
        changed: list[str] = []
        try:
            for relative, text in texts.items():
                path = staged / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            for relative in texts:
                target = self.root / relative
                if target.exists():
                    old = backup / relative
                    old.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, old)
                target.parent.mkdir(parents=True, exist_ok=True)
                (staged / relative).replace(target)
                changed.append(relative)
        except Exception:
            for relative in reversed(changed):
                target = self.root / relative
                old = backup / relative
                if old.exists():
                    shutil.copy2(old, target)
                else:
                    target.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def _clean_refs(value, valid: set) -> None:
    """Keep only block ids that exist in the section (a model sometimes invents one)."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("refs", "narrative_only") and isinstance(item, list):
                value[key] = [str(r).strip("[] ") for r in item if str(r).strip("[] ") in valid]
            else:
                _clean_refs(item, valid)
    elif isinstance(value, list):
        for item in value:
            _clean_refs(item, valid)


def guess_pack(draft: dict) -> str:
    """The country the person wants to work in: their targets, then their permission, then where they live."""
    from backend.countries import code_for

    candidates = list((draft.get("targets") or {}).get("countries") or [])
    candidates += [(draft.get("authorization") or {}).get("work_country"), (draft.get("contact") or {}).get("country")]
    for country in candidates:
        if country:
            code = code_for({"location_preferences": {"country": country}})
            if code != "us" or str(country).strip().casefold() in {"us", "usa", "united states", "united states of america", "america"}:
                return code
    return "ie"
