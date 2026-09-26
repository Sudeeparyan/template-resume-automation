"""A Profile save reaches every place that reads the candidate's facts.

The Profile page saves to career.db, but most of the workspace reads the two YAML
files the database was seeded from: the resume builder, the validators, job
screening, and the Claude Code skills and Copilot agents that open
data/context/evidence.yml. career.db is gitignored, so those files are also what a
new machine re-imports. A Profile page edit is the candidate's own record
of her facts, so it is written through:

- data/config/profile.yml: personal details, target roles, location preferences;
- data/context/evidence.yml: experience, projects, skills, education, other facts;
- the linked twin (profile.yml's email and the registry's CONTACT-EMAIL-001 are one fact);
- data/templates/resume-base.tex and the drafts of jobs not yet applied to (resume_sync);
- candidate_revision in both YAML files, which marks older PDFs for a rebuild.

Only the changed entry's lines are rewritten, and every file is re-read and compared
with the intended result before anything is saved. Drafts for jobs already applied
to are the record of what was sent and are never touched.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

from career import atomic_write, safe_child
from backend.services import resume_sync
from backend.services.profile_fields import describe_value, lines as text_lines

PROFILE = "data/config/profile.yml"
EVIDENCE = "data/context/evidence.yml"
TEMPLATE = "data/templates/resume-base.tex"
OPEN_STATUSES = ("saved", "prepared")
# One fact kept in two places: profile.yml's candidate field and its registry claim.
TWINS = {
    "full_name": "IDENTITY-001",
    "email": "CONTACT-EMAIL-001",
    "phone": "CONTACT-PHONE-001",
    "github": "CONTACT-GITHUB-001",
    "portfolio_url": "CONTACT-PORTFOLIO-001",
    "linkedin": "CONTACT-LINKEDIN-001",
}
# Settings every resume or the job search depends on: editable, never removable.
REQUIRED_PERSONAL = {"full_name", "email", "phone", "timezone", "portfolio_url", "github",
                     "target_roles", "location_preferences"}
PREFIX = {"experience": "EXP", "education": "EDU", "certification": "CERT", "skill": "SKILL",
          "fact": "FACT", "project": "PROJ"}
CATEGORY = {"experience": "employment", "education": "education", "certification": "certification",
            "skill": "skill", "fact": "other"}
# Row bookkeeping that never belongs in the registry.
BOOKKEEPING = ("origin", "pending", "user_supplied", "job_id", "evidence_id")
MISSING = object()
LOCKED = "{label} is printed on every resume or used by the job search, so it can't be removed. Edit it instead."


# ---- where a row lives ------------------------------------------------------------


def locate(row):
    """("profile", key path) or ("evidence", registry id) for a knowledge row."""
    source, data = str(row.get("source") or ""), row.get("data") or {}
    if row["kind"] == "personal":
        if source.startswith("data/config/profile.yml > candidate > "):
            return "profile", ("candidate", source.rsplit(" > ", 1)[1])
        if source.startswith("data/config/profile.yml > "):
            return "profile", (source.split(" > ", 1)[1],)
        field = data.get("field") or re.sub(r"[^a-z0-9]+", "_", row["title"].casefold()).strip("_")
        return "profile", ("candidate", field)
    if source.startswith("data/context/evidence.yml > ") and source.split(" > ", 1)[1] == row["id"]:
        return "evidence", row["id"]
    if isinstance(data.get("id"), str) and re.fullmatch(r"[A-Z0-9-]+", data["id"]):
        return "evidence", data["id"]
    digest = hashlib.sha256(row["id"].encode()).hexdigest()[:6].upper()
    return "evidence", PREFIX.get(row["kind"], "FACT") + "-USER-" + digest


def lock_reason(row, evidence, label):
    """Why a row can't be removed, or None."""
    where, key = locate(row)
    if where == "profile":
        return LOCKED.format(label=label) if key[-1] in REQUIRED_PERSONAL else None
    immutable = (evidence.get("immutable_across_resumes") or {}).get("claim_ids") or []
    return LOCKED.format(label=label) if key in immutable else None


def _clean(data):
    data = copy.deepcopy(data or {})
    for key in BOOKKEEPING:
        data.pop(key, None)
    return data


def _changes(old, new):
    """[(path, value)] turning old into new; nested mappings are compared one level down."""
    out = []
    for key in list(old) + [k for k in new if k not in old]:
        a, b = old.get(key, MISSING), new.get(key, MISSING)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            out += [((key, sub), b.get(sub, MISSING)) for sub in list(a) + [k for k in b if k not in a]
                    if a.get(sub, MISSING) != b.get(sub, MISSING)]
        else:
            out.append(((key,), b))
    return out


def _apply(entry, changes):
    entry = copy.deepcopy(entry)
    for path, value in changes:
        target = entry
        for part in path[:-1]:
            if not isinstance(target.get(part), dict):
                target[part] = {}
            target = target[part]
        if value is MISSING:
            target.pop(path[-1], None)
        else:
            target[path[-1]] = copy.deepcopy(value)
    return entry


def resume_gap(content, name):
    """Why a project can't go on a resume yet (the registry's shape), or None."""
    content = content or {}
    bullets = [b for b in content.get("bullets") or [] if str(b).strip()]
    if not str(content.get("title") or "").strip():
        return "Give the project a name."
    if not str(content.get("context") or "").strip():
        return f"{name}: add at least one technology. The list is printed next to the project name on resumes."
    if not 2 <= len(bullets) <= 3:
        return f"{name}: a project needs two or three concise bullets to fit the resume contract."
    return None


def _is_skill(entry):
    return "skill" in str(entry.get("category", "")) or str(entry.get("id", "")).startswith("SKILL")


def _never(entry):
    return str(entry.get("id", "")).startswith("SKILL-NEVER")


# ---- YAML files, rewritten one block at a time -------------------------------------


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _block_end(lines, start, indent, limit):
    """End (exclusive) of the value of the key on lines[start]."""
    end = start + 1
    while end < limit:
        line = lines[end]
        if line.strip() and not (_indent(line) > indent or
                                 (_indent(line) == indent and line.lstrip(" ").startswith("- "))):
            break
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _find_key(lines, key, start, end, indent):
    prefix = " " * indent + key + ":"
    for index in range(start, end):
        line = lines[index]
        if line.startswith(prefix) and _indent(line) == indent and (len(line) == len(prefix) or line[len(prefix)] == " "):
            return index, _block_end(lines, index, indent, end)
    return None


def _dump(value, indent):
    text = yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=4096, default_flow_style=False)
    return [(" " * indent + line) if line else line for line in text.rstrip("\n").split("\n")]


def yaml_set(text, path, value):
    """Set, or with MISSING remove, a key one or two levels deep, rewriting only its lines."""
    lines = text.split("\n")
    if len(path) == 1:
        span = _find_key(lines, path[0], 0, len(lines), 0)
        new = [] if value is MISSING else _dump({path[0]: value}, 0)
        if span:
            lines[span[0]:span[1]] = new
        else:
            end = len(lines) - 1 if lines and not lines[-1] else len(lines)
            lines[end:end] = new
        return "\n".join(lines)
    parent = _find_key(lines, path[0], 0, len(lines), 0)
    if not parent:
        raise ValueError(f"{path[0]} is missing from the file")
    indent = next((_indent(line) for line in lines[parent[0] + 1:parent[1]] if line.strip()), 2)
    span = _find_key(lines, path[1], parent[0] + 1, parent[1], indent)
    new = [] if value is MISSING else _dump({path[1]: value}, indent)
    if span:
        lines[span[0]:span[1]] = new
    else:
        lines[parent[1]:parent[1]] = new
    return "\n".join(lines)


def yaml_put_entry(text, section, entry):
    """Replace the registry entry with this id in section, or append it."""
    lines = text.split("\n")
    parent = _find_key(lines, section, 0, len(lines), 0)
    if not parent:
        raise ValueError(f"The evidence registry has no {section} section")
    pattern = re.compile(r"^- id: ['\"]?" + re.escape(entry["id"]) + r"['\"]?\s*$")
    new = _dump([entry], 0)
    for index in range(parent[0] + 1, parent[1]):
        if pattern.match(lines[index]):
            end = index + 1
            while end < parent[1] and (not lines[end].strip() or _indent(lines[end]) > 0):
                end += 1
            while end > index + 1 and not lines[end - 1].strip():
                end -= 1
            lines[index:end] = new
            return "\n".join(lines)
    lines[parent[1]:parent[1]] = new
    return "\n".join(lines)


def yaml_revision(text, revision):
    new, count = re.subn(r"(?m)^candidate_revision:.*$", "candidate_revision: " + revision, text, count=1)
    if not count:
        raise ValueError("candidate_revision is missing from the file")
    return new


def refresh_contract():
    """Validators keep the contract derived from the YAML files; rebuild it after a change."""
    from backend.resume_contract import load_contract

    load_contract.cache_clear()
    contract = None
    for name in ("validate_resume", "validate_batch", "validate_workspace"):
        module = sys.modules.get(name)
        if module is None or not hasattr(module, "CONTRACT"):
            continue
        contract = contract or load_contract()
        module.CONTRACT = contract
        if hasattr(module, "UNSAFE_PATTERNS"):
            module.UNSAFE_PATTERNS = dict(contract.unsafe_patterns)
        if hasattr(module, "ALLOWED_VISIBLE_NUMBERS"):
            module.ALLOWED_VISIBLE_NUMBERS = set(contract.allowed_visible_numbers)
        if hasattr(module, "REQUIRED_SECTIONS"):
            module.REQUIRED_SECTIONS = tuple(contract.required_sections)


class _Registry:
    """Stands in for the workspace so its project ranking reads the updated registry."""

    def __init__(self, evidence, profile=None):
        self._evidence = evidence
        self._profile = profile or {}

    def evidence(self):
        return self._evidence

    def profile(self):
        return self._profile


# ---- one sync: every change made in one database transaction -----------------------


class ProfileSync:
    def __init__(self, service, db):
        self.s, self.w, self.db = service, service.w, db
        self.root = Path(self.w.root)
        self.today = service.today()
        self.texts, self.originals = {}, {}
        self.profile = yaml.safe_load(self._read(PROFILE)) or {}
        self.evidence = yaml.safe_load(self._read(EVIDENCE)) or {}
        self.candidate_before = copy.deepcopy(self.profile.get("candidate") or {})
        self.changes = []  # (group, before, after) registry entries, in order
        self.never_added = []
        self.notes = []  # things the candidate should know, such as a project kept off resumes
        self.saved = set()  # rows the caller is saving, so a twin never overwrites them
        self.writes = {}  # absolute path -> new text
        self.restore_points = {}
        self.summary = None

    # -- files --

    def _read(self, relative):
        if relative not in self.texts:
            path = self.root / relative
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            self.texts[relative] = self.originals[relative] = text
        return self.texts[relative]

    def _profile_value(self, path):
        value = self.profile
        for part in path:
            if not isinstance(value, dict) or part not in value:
                return MISSING
            value = value[part]
        return value

    def _set_profile(self, path, value):
        if self._profile_value(path) == value:
            return
        target = self.profile
        for part in path[:-1]:
            target = target.setdefault(part, {})
        if value is MISSING:
            target.pop(path[-1], None)
        else:
            target[path[-1]] = copy.deepcopy(value)
        self.texts[PROFILE] = yaml_set(self.texts[PROFILE], path, value)

    def _find(self, registry_id):
        for group in ("claims", "projects"):
            for index, item in enumerate(self.evidence.get(group) or []):
                if isinstance(item, dict) and item.get("id") == registry_id:
                    return group, index, item
        return None, None, None

    def _put(self, group, entry):
        items = self.evidence.setdefault(group, [])
        _, index, _ = self._find(entry["id"])
        if index is None:
            items.append(entry)
        else:
            items[index] = entry
        self.texts[EVIDENCE] = yaml_put_entry(self.texts[EVIDENCE], group, entry)

    def _stamp(self, entry, verb):
        refs = [r for r in entry.get("source_refs") or [] if isinstance(r, str)]
        note = f"Profile page (dashboard) > {verb} {self.today}"
        if note not in refs:
            refs.append(note)
        entry["source_refs"] = refs
        return entry

    # -- rows --

    def row_saved(self, row, previous, from_form=True):
        """Write one saved row into the files. Returns the row's data as it should now be stored.

        from_form is False for a suggestion being confirmed: the chat, the assistant
        and the CLI add a skill as "name | where it was used", so its summary is a
        note about the skill rather than a list of skills.
        """
        self.saved.add(row["id"])
        where, key = locate(row)
        if where == "profile":
            return self._personal(row, key, previous)
        return self._claim(row, key, previous, from_form)

    def row_removed(self, row, label):
        """Remove one row's fact from the files; its registry entry is kept on hold as the record."""
        reason = lock_reason(row, self.evidence, label)
        if reason:
            raise ValueError(reason)
        self.saved.add(row["id"])
        where, key = locate(row)
        if where == "profile":
            self._set_profile(key, MISSING)
            if key[0] == "candidate" and key[-1] in TWINS:
                self._twin_claim(TWINS[key[-1]], "")
            return
        group, _, current = self._find(key)
        if current is None:
            return
        entry = self._stamp(copy.deepcopy(current), "removed")
        entry["status"] = "hold"
        entry["profile_removed"] = self.today
        self._put(group, entry)
        self.changes.append((group, current, None))
        for field, claim_id in TWINS.items():
            if claim_id == key and self._profile_value(("candidate", field)) not in (MISSING, ""):
                self._set_profile(("candidate", field), "")
                self._update_personal(field, "")

    def _personal(self, row, path, previous):
        data = row["data"] or {}
        value = data.get("value", row["summary"])
        others = [r for r in self.db.execute(
            "SELECT id,kind,title,source,data FROM knowledge WHERE kind='personal' AND deleted=0 AND id<>?", (row["id"],))
            if locate({**dict(r), "data": json.loads(r["data"])}) == ("profile", path)]
        if others:
            raise ValueError(f"You already have an entry for {row['title']}. Edit that one instead.")
        current = self._profile_value(path)
        if isinstance(current, dict):
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = None
            if not isinstance(value, dict):
                raise ValueError(f"{row['title']} has several parts. Open it with Edit and save the form to change it.")
            before = (previous or {}).get("value")
            value = _apply(current, _changes(before, value)) if isinstance(before, dict) else value
        self._set_profile(path, value)
        field = path[-1] if path[0] == "candidate" else None
        if field in TWINS:
            self._twin_claim(TWINS[field], value)
        return {**data, "value": value}

    def _claim(self, row, registry_id, previous, from_form=True):
        group, _, current = self._find(registry_id)
        data = _clean(row["data"])
        if current is None:
            before, entry = None, self._new_entry(row, registry_id, data, from_form)
            group = "projects" if row["kind"] == "project" else "claims"
        else:
            before = current
            entry = _apply(current, _changes(_clean(previous), data))
            entry["id"] = registry_id
        if entry == before:
            return before
        if group == "projects" and before is None and resume_gap(entry.get("resume_content"), row["title"]):
            # Saved to the profile, kept off resumes until it has a technology line and 2-3 bullets.
            self.notes.append(resume_gap(entry.get("resume_content"), row["title"]) + " Until then it stays off resumes.")
            return row["data"]
        if entry.get("status") == "missing" and (entry.get("value") or entry.get("approved_facts")):
            entry["status"] = "user_reported"  # supplied now, so no longer missing
        entry = self._stamp(entry, "added" if before is None else "edited")
        self._check(group, entry, before, row)
        self._put(group, entry)
        self.changes.append((group, before, entry))
        if _never(entry):
            old = {resume_sync.norm(x).casefold() for x in (before or {}).get("approved_facts") or []}
            self.never_added += [x for x in entry.get("approved_facts") or [] if resume_sync.norm(x).casefold() not in old]
        for field, claim_id in TWINS.items():
            if claim_id == registry_id and isinstance(entry.get("value", ""), str):
                self._set_profile(("candidate", field), entry.get("value", ""))
                self._update_personal(field, entry.get("value", ""))
        return entry

    def _new_entry(self, row, registry_id, data, from_form=True):
        kind = row["kind"]
        entry = {"id": registry_id, "status": "user_reported"}
        if kind != "project":
            entry["category"] = CATEGORY.get(kind, kind)
        entry["approved_external_use"] = "always; added by the candidate on the Profile page"
        entry["source_refs"] = []
        if kind == "project":
            content = data.get("resume_content") or {}
            entry["resume_content"] = {
                "title": content.get("title") or row["title"],
                "context": content.get("context") or "",
                "bullets": content.get("bullets") or text_lines(row["summary"]),
            }
            for key in ("technologies", "date_context", "ownership"):
                if data.get(key):
                    entry[key] = data[key]
            return entry
        # A name for every entry, so a re-import on another machine shows the same title.
        entry["title"] = data.get("title") or row["title"]
        for key, value in data.items():
            if key not in entry and key not in ("id", "status", "category", "source_refs", "approved_external_use"):
                entry[key] = value
        if kind == "skill" and (not from_form or not entry.get("approved_facts")):
            note = str(row["summary"] or "").strip()
            entry["approved_facts"] = [row["title"]]
            if not from_form and note and note != row["title"]:
                entry["evidence_note"] = note
        if kind == "fact" and not entry.get("value") and not entry.get("approved_facts"):
            entry["value"] = row["summary"]
        return entry

    def _check(self, group, entry, before, row):
        """Refuse what would break a resume, with the fix in plain words."""
        if group == "projects":
            gap = resume_gap(entry.get("resume_content"), row["title"])
            if gap:
                raise ValueError(gap)
        if _is_skill(entry) and not _never(entry):
            never = next((c for c in self.evidence.get("claims") or [] if _never(c)), {})
            banned = {resume_sync.norm(x).casefold(): x for x in never.get("approved_facts") or []}
            old = {resume_sync.norm(x).casefold() for x in (before or {}).get("approved_facts") or []}
            for skill in entry.get("approved_facts") or []:
                key = resume_sync.norm(skill).casefold()
                if key in banned and key not in old:
                    raise ValueError(f"{skill} is on your never-claim list. Remove it from that list first.")

    def _twin_claim(self, claim_id, value):
        group, _, current = self._find(claim_id)
        if current is None or current.get("value", "") == value:
            return
        entry = copy.deepcopy(current)
        if value:
            entry["value"] = value
            if entry.get("status") == "missing":
                entry["status"] = "user_reported"
        else:
            entry.pop("value", None)
        entry = self._stamp(entry, "edited")
        self._put(group, entry)
        self.changes.append((group, current, entry))
        old = self.db.execute("SELECT * FROM knowledge WHERE id=? AND deleted=0", (claim_id,)).fetchone()
        if old and claim_id not in self.saved:
            title = value if old["title"] == old["summary"] and value else old["title"]
            self._write_row(old, title, value, entry)

    def _update_personal(self, field, value):
        old = self.db.execute("SELECT * FROM knowledge WHERE id=? AND deleted=0", ("personal:" + field,)).fetchone()
        if old and old["id"] not in self.saved:
            self._write_row(old, old["title"], describe_value(value), {**json.loads(old["data"]), "value": value})

    def _write_row(self, old, title, summary, data):
        self.db.execute(
            "UPDATE knowledge SET title=?,summary=?,data=?,revision=revision+1,updated_at=? WHERE id=?",
            (title, summary, json.dumps(data, ensure_ascii=False), self.s.now(), old["id"]),
        )
        self.w.record_event(self.db, "profile_entry_saved", entry_id=old["id"], origin="linked_entry",
                            before=dict(old), summary=summary)

    # -- resumes --

    def _owners(self, entry):
        """Case-folded list item -> ids of the active entries (of the same family) listing it."""
        owners = {}
        family = _is_skill(entry)
        for claim in self.evidence.get("claims") or []:
            if claim.get("status") in ("hold", "missing") or _never(claim) or _is_skill(claim) != family:
                continue
            for fact in claim.get("approved_facts") or []:
                owners.setdefault(resume_sync.norm(fact).casefold(), set()).add(claim["id"])
        return owners

    def _ranked(self, description):
        from career import Workspace

        ranked = Workspace.rank_projects(_Registry(self.evidence, self.profile), description)
        eligible = {p.get("id"): p.get("signature_eligible") for p in self.evidence.get("projects") or []}
        return [{**p, "signature_eligible": eligible.get(p["id"])} for p in ranked]

    def _update_source(self, source, description):
        header = (self.profile.get("resume_contract") or {}).get("header_fields") or \
            ["full_name", "phone", "email", "portfolio_url", "github"]
        fallback = None
        for group, before, after in self.changes:
            if group == "projects" and after is None and fallback is None:
                fallback = self._ranked(description)
            source = resume_sync.apply_change(source, group, before, after, self._owners(after or before), fallback or ())
        source = resume_sync.apply_header(source, self.candidate_before, self.profile.get("candidate") or {}, header)
        if self.never_added:
            source = resume_sync.drop_skills(source, self.never_added)
        return source

    def _drafts(self):
        has_studio = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='studio_drafts'").fetchone()
        jobs = self.db.execute(
            "SELECT id,company,title,description,folder FROM jobs WHERE folder IS NOT NULL AND folder<>'' "
            "AND status IN ('saved','prepared') AND deleted_at IS NULL ORDER BY id").fetchall()
        for job in jobs:
            draft = self.db.execute("SELECT * FROM studio_drafts WHERE job_id=?", (job["id"],)).fetchone() if has_studio else None
            folder = safe_child(self.root / "data/output", str(Path(job["folder"]).relative_to("data/output")))
            if draft:
                path = safe_child(self.root / "data/output", str(Path(draft["folder"]).relative_to("data/output"))) / "resume.tex"
                yield job, draft, folder, path, draft["source"]
            elif (folder / "resume.tex").exists():
                yield job, None, folder, folder / "resume.tex", (folder / "resume.tex").read_text(encoding="utf-8")

    def _next_revision(self):
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.(\d+)", str(self.evidence.get("candidate_revision") or ""))
        number = int(match[2]) + 1 if match and match[1] == self.today else 1
        return f"{self.today}.{number}"

    # -- finishing --

    def finish(self):
        """Carry the collected changes into resume sources, bump the revision and check every file."""
        changed_files = [r for r in (PROFILE, EVIDENCE) if self.texts[r] != self.originals[r]]
        if not changed_files:
            self.summary = {"updated": [], "drafts": [], "notes": self.notes,
                            "revision": self.evidence.get("candidate_revision")}
            return self.summary
        from validate_resume import latex_braces_balanced

        revision = self._next_revision()
        for relative, data in ((PROFILE, self.profile), (EVIDENCE, self.evidence)):
            data["candidate_revision"] = revision
            self.texts[relative] = yaml_revision(self.texts[relative], revision)
            if yaml.safe_load(self.texts[relative]) != data:
                raise ValueError(f"{relative} could not be updated safely, so nothing was saved.")
            self.writes[self.root / relative] = self.texts[relative]
        updated = ["Profile settings (profile.yml)", "Evidence registry (evidence.yml)"]

        template = self.root / TEMPLATE
        if template.exists():
            before = template.read_text(encoding="utf-8")
            after = self._update_source(before, "")
            if after != before:
                if not latex_braces_balanced(after):
                    raise ValueError("The base resume could not be updated safely, so nothing was saved.")
                self.writes[template] = after
                updated.append("Base resume")

        drafts, stamp = [], self.s.now()
        for job, draft, folder, path, source in list(self._drafts()):
            after = self._update_source(source, job["title"] + " " + (job["description"] or ""))
            if after != source:
                if not latex_braces_balanced(after):
                    raise ValueError(f"The draft for {job['company']} could not be updated safely, so nothing was saved.")
                self.writes[path] = after
                drafts.append({"job_id": job["id"], "company": job["company"], "title": job["title"]})
                if draft:
                    self.db.execute("UPDATE studio_drafts SET source=?,revision=revision+1,profile_revision=?,updated_at=? WHERE job_id=?",
                                    (after, revision, stamp, job["id"]))
                    self.db.execute("INSERT INTO studio_versions VALUES(?,?,?,?)", (job["id"], draft["revision"] + 1, after, stamp))
                    self.w.record_event(self.db, "studio_profile_synced", job["id"], revision=draft["revision"] + 1,
                                        profile_revision=revision, origin="profile_page")
            elif draft:
                # Nothing on this draft changed, so it already matches the new revision.
                self.db.execute("UPDATE studio_drafts SET profile_revision=? WHERE job_id=?", (revision, job["id"]))
            mapping = folder / "evidence-map.yml"
            if mapping.exists():
                values = yaml.safe_load(mapping.read_text(encoding="utf-8")) or {}
                values["candidate_revision"] = revision
                if after != source:
                    ids = {i for m in re.finditer(r"(?m)^\s*% EVIDENCE:\s*(.+)$", after) for i in m[1].split()}
                    values["resume_claim_ids"] = sorted(i for i in ids if not i.startswith("resume_items:"))
                self.writes[mapping] = yaml.safe_dump(values, sort_keys=False, allow_unicode=True)
        if drafts:
            updated.append("Draft resumes")
        self.w.record_event(self.db, "profile_synced", revision=revision,
                            files=sorted(str(p.relative_to(self.root)) for p in self.writes), drafts=[d["job_id"] for d in drafts])
        self.summary = {"updated": updated, "drafts": drafts, "notes": self.notes, "revision": revision}
        return self.summary

    def write(self):
        for path, text in self.writes.items():
            self.restore_points[path] = path.read_text(encoding="utf-8") if path.exists() else None
            atomic_write(path, text)

    def restore(self):
        """Put every file back after a failed save, so files and database never disagree."""
        for path, text in self.restore_points.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, text)
        self.restore_points = {}

    def done(self):
        if self.writes:
            refresh_contract()
