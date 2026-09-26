"""Structured Profile fields: the one map between the Profile form and a knowledge row.

A knowledge row keeps its shape (title, summary, data) because every agent reads
title + summary, and reads data as details once an entry is reviewed. The form
edits named fields; this module writes them into the same data keys the evidence
registry uses and derives the summary from them, so the three never disagree.

The body of an entry (bullets, skills, details) is always read from the summary,
so a save that changes only the summary (the Profile chat, the assistant, the
CLI) shows up on the page unchanged.
"""

from __future__ import annotations

import copy
import json
import re

TEXT, TEXTAREA, LIST, NUMBER = "text", "textarea", "list", "number"


def _spec(key, label, type=TEXT, **extra):
    return {"key": key, "label": label, "type": type, **extra}


# The form shown when adding an entry of each kind. Existing entries use form_for().
SCHEMAS = {
    "personal": [
        _spec("title", "Label", required=True, placeholder="Email"),
        _spec("value", "Value", TEXTAREA),
    ],
    "experience": [
        _spec("title", "Role title", required=True, placeholder="Data Engineering Intern"),
        _spec("employer", "Employer"),
        _spec("dates", "Dates", placeholder="Jul 2024 - Present"),
        _spec("location", "Location"),
        _spec("bullets", "What you did", LIST, hint="One resume bullet per line, in your own words."),
    ],
    "project": [
        _spec("title", "Project name", required=True),
        _spec("timeframe", "When, and in what setting", placeholder="Graduate project, 2025"),
        _spec("ownership", "Your part", placeholder="Individual project"),
        _spec("technologies", "Technologies", LIST),
        _spec("bullets", "What you built", LIST, hint="Two or three resume bullets, one per line."),
    ],
    "skill": [
        _spec("title", "Skill, or a name for a group of skills", required=True),
        _spec("skills", "Skills in this group", LIST, hint="Leave empty when the name above is the skill."),
    ],
    "education": [
        _spec("title", "School", required=True),
        _spec("degree", "Degree"),
        _spec("dates", "Dates", placeholder="Aug 2024 - May 2026"),
        _spec("location", "Location"),
        _spec("status", "Status", placeholder="Completed May 2026"),
    ],
    "certification": [
        _spec("title", "Certificate", required=True),
        _spec("issuer", "Issued by"),
        _spec("date", "Date earned"),
        _spec("credential", "Credential ID or link"),
    ],
    "fact": [
        _spec("title", "Label", required=True),
        _spec("details", "Details", TEXTAREA),
    ],
}

# Form field -> key path inside the row's data, using the evidence registry's names.
PATHS = {
    "experience": {
        "title": ("title",),
        "employer": ("employer",),
        "dates": ("dates",),
        "location": ("location",),
        "bullets": ("approved_facts",),
    },
    "project": {
        "title": ("resume_content", "title"),
        "timeframe": ("date_context",),
        "ownership": ("ownership",),
        "technologies": ("technologies",),
        "bullets": ("resume_content", "bullets"),
    },
    "skill": {"skills": ("approved_facts",)},
    "education": {
        "title": ("institution",),
        "degree": ("degree_as_supplied",),
        "dates": ("dates",),
        "location": ("location",),
        "status": ("status_text",),
    },
    "certification": {
        "issuer": ("issuer",),
        "date": ("date",),
        "credential": ("credential",),
    },
}

# The field each kind reads from the summary rather than from data.
BODY = {"experience": "bullets", "project": "bullets", "skill": "skills", "fact": "details"}

# Registry bookkeeping, shown as status, usage and sources instead of as details.
META_KEYS = {
    "id", "status", "category", "approved_external_use", "source_refs", "field",
    "origin", "pending", "user_supplied", "job_id", "evidence_id",
}

WORDS = {
    "ai": "AI", "be": "BE", "cv": "CV", "fpga": "FPGA", "genai": "GenAI", "github": "GitHub",
    "gpa": "GPA", "iot": "IoT", "linkedin": "LinkedIn", "ml": "ML", "ms": "MS", "opt": "OPT",
    "rag": "RAG", "url": "URL", "never": "Never claim", "touched": "Touched it",
}

EXTRA_LABELS = {
    "approved_facts": "Full wording in the evidence registry",
    "canonical_name": "Registered name",
    "external_name": "Name on resumes",
    "evidence_type": "Evidence type",
    "evidence_note": "Where you used it",
    "problem_patterns": "Problems it shows",
    "role_tracks": "Fits these roles",
    "prohibited": "Never claim",
    "signature_eligible": "Can lead the Projects section",
    "superseded_dates": "Earlier dates (superseded)",
    "status_text": "Status",
    "value": "Value",
}

PERSONAL_GROUPS = [
    ("Contact & identity", {"full_name", "preferred_name", "email", "phone", "github", "linkedin",
                            "portfolio_url", "location", "city_as_inferred", "timezone"}),
    ("Work authorization", {"work_authorization", "sponsorship_need", "citizenship", "availability"}),
    ("Career snapshot", {"current_status", "most_recent_role", "education_summary",
                         "dated_professional_experience"}),
    ("Job search", {"target_roles", "location_preferences"}),
]

SKILL_TIER = re.compile(r"05-skills\.md\s*>\s*(.+)$")


def humanize(text: str) -> str:
    words = [w for w in re.split(r"[\s_\-]+", str(text).strip()) if w]
    out = []
    for index, word in enumerate(words):
        known = WORDS.get(word.casefold())
        if known:
            out.append(known)
        else:
            out.append(word.capitalize() if index == 0 else word.casefold())
    return " ".join(out)


def lines(text) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _get(data, path):
    for part in path:
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def _set(data, path, value):
    for part in path[:-1]:
        if not isinstance(data.get(part), dict):
            data[part] = {}
        data = data[part]
    if value in ("", [], None):
        data.pop(path[-1], None)
    else:
        data[path[-1]] = value


def _as_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return lines(value)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _typed(value, type):
    if type == LIST:
        return _as_list(value)
    if type == NUMBER:
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return _as_text(value)


def describe_value(value) -> str:
    """Readable text for a personal value, which may be a string, a list or a mapping."""
    if isinstance(value, dict):
        rows = []
        for key, item in value.items():
            if isinstance(item, list):
                item = ", ".join(str(v) for v in item)
            if item not in ("", None):
                rows.append(f"{humanize(key)}: {item}")
        return "\n".join(rows)
    return _as_text(value)


def _noted_skill(item) -> bool:
    note = (item.get("data") or {}).get("evidence_note")
    return item["kind"] == "skill" and bool(note) and _normalized(item["summary"]) == _normalized(note)


def _structured_personal(item) -> bool:
    return item["kind"] == "personal" and isinstance((item.get("data") or {}).get("value"), dict)


def _value_type(value):
    if isinstance(value, list):
        return LIST
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return NUMBER
    return TEXTAREA if len(_as_text(value)) > 90 or "\n" in _as_text(value) else TEXT


def form_for(item) -> list[dict]:
    """The form for an existing entry: its kind's form, or one field per part of a mapping."""
    if _structured_personal(item):
        value = item["data"]["value"]
        return [SCHEMAS["personal"][0]] + [
            _spec("value." + key, humanize(key), _value_type(part)) for key, part in value.items()
        ]
    if item["kind"] == "personal":
        value = (item.get("data") or {}).get("value", item["summary"])
        return [SCHEMAS["personal"][0], _spec("value", "Value", _value_type(value))]
    return SCHEMAS[item["kind"]]


def fields_for(item) -> dict:
    """Current form values for an entry, read from the row exactly as agents see it.

    The title is the name the page shows, so an imported ID-style title is replaced
    by its readable name when the entry is next saved.
    """
    kind, data = item["kind"], item.get("data") or {}
    values = {"title": label_for(item)}
    if _structured_personal(item):
        for key, part in data["value"].items():
            values["value." + key] = _typed(part, _value_type(part))
        return values
    if kind == "personal":
        values["value"] = data["value"] if isinstance(data.get("value"), list) else item["summary"]
        return values
    for key, path in PATHS.get(kind, {}).items():
        if key != "title":
            values[key] = _get(data, path)
    body = BODY.get(kind)
    if body:
        values[body] = item["summary"]
    if _noted_skill(item):
        # A confirmed "name | where it was used" skill: the summary is the note, the name the skill.
        values["skills"] = [s for s in data.get("approved_facts") or [] if s != values["title"]]
    types = {spec["key"]: spec["type"] for spec in SCHEMAS[kind]}
    return {key: _typed(value, types.get(key, TEXT)) for key, value in values.items()}


def derive_summary(kind, values) -> str:
    if kind in ("experience", "project"):
        return "\n".join(values.get("bullets") or [])
    if kind == "skill":
        return "\n".join(values.get("skills") or [])
    if kind == "education":
        return " · ".join(v for v in (values.get("dates"), values.get("degree"), values.get("status")) if v)
    if kind == "certification":
        return " · ".join(v for v in (values.get("issuer"), values.get("date"), values.get("credential")) if v)
    if kind == "fact":
        return values.get("details") or ""
    return _as_text(values.get("value"))


def _clean(value, spec):
    if spec["type"] == LIST:
        if not isinstance(value, (list, str)):
            raise ValueError(f"{spec['label']} must be a list of lines")
        items = _as_list(value)
        if len(items) > 80 or any(len(v) > 2000 for v in items):
            raise ValueError(f"{spec['label']} is too long")
        return items
    if spec["type"] == NUMBER:
        if value in ("", None):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{spec['label']} must be a number") from None
        return int(number) if number.is_integer() else number
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError(f"{spec['label']} must be text")
    text = str(value).strip()
    limit = 30000 if spec["type"] == TEXTAREA else 1000
    if len(text) > limit:
        raise ValueError(f"{spec['label']} is too long")
    return text


def apply_fields(kind, fields, old=None):
    """Return (title, summary, data) for a form save; old is the current entry or None.

    Only the named fields change. Everything else in data (registry status, usage,
    sources, patterns) is carried over as it was.
    """
    if not isinstance(fields, dict):
        raise ValueError("Entry details must be an object")
    form = {spec["key"]: spec for spec in (form_for(old) if old else SCHEMAS[kind])}
    unknown = sorted(set(fields) - set(form))
    if unknown:
        raise ValueError("Unknown profile field: " + ", ".join(unknown))
    values = fields_for(old) if old else {}
    for key, value in fields.items():
        values[key] = _clean(value, form[key])
    data = copy.deepcopy(old["data"]) if old else {}
    title = _as_text(values.get("title")).strip()
    if kind == "personal":
        if old and _structured_personal(old):
            value = {key: values["value." + key] for key in old["data"]["value"]}
        elif isinstance(values.get("value"), list):
            value = values["value"]
        else:
            value = _as_text(values.get("value"))
        data["value"] = value
        if not data.get("field") and title:
            data["field"] = re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_")
        return title, describe_value(value), data
    for key, path in PATHS.get(kind, {}).items():
        if key == "title":
            # Mirror the name only where the registry keeps one (a placeholder claim has none).
            if old is None or _get(data, path) is not None:
                _set(data, path, title)
        else:
            _set(data, path, values.get(key))
    if kind == "project":
        technologies = values.get("technologies") or []
        # The resume's technology line may list more than the registry list; rewrite it only on change.
        if old is None or technologies != _as_list(_get(old["data"], ("technologies",))):
            _set(data, ("resume_content", "context"), ", ".join(technologies))
    if kind == "skill":
        skills = values.get("skills") or []
        if not skills and not (old and _registry(old)):
            data["approved_facts"] = [title]  # "Leave empty when the name above is the skill."
        if not skills and data.get("evidence_note"):
            return title, data["evidence_note"], data
    if kind == "fact":
        details = values.get("details") or ""
        if isinstance(data.get("value"), str):
            data["value"] = details
        elif isinstance(data.get("approved_facts"), list):
            data["approved_facts"] = lines(details)
    return title, derive_summary(kind, values), data


def _normalized(text) -> str:
    return "\n".join(lines(text))


def in_sync(item) -> bool:
    """Does the stored summary say what the form fields say?

    False only when a summary-only save (the chat, the CLI) changed wording the
    form keeps elsewhere, such as an education line. The page then shows it.
    """
    if _structured_personal(item):
        value = item["data"]["value"]
        return item["summary"] in (describe_value(value), json.dumps(value, ensure_ascii=False, indent=2))
    if _noted_skill(item):
        return True
    derived = derive_summary(item["kind"], fields_for(item))
    return _normalized(derived) == _normalized(item["summary"])


def _registry(item) -> bool:
    return str(item.get("source") or "").startswith("data/context/evidence.yml")


def label_for(item) -> str:
    """A readable name, correcting labels the first import generated from IDs or first lines."""
    title, key, data = item["title"].strip(), item["id"], item.get("data") or {}
    if item["kind"] == "personal":
        field = data.get("field")
        if field and title == field.replace("_", " ").capitalize():
            if field.endswith("_external_resume_policy"):
                return humanize(field[: -len("_external_resume_policy")]) + " on resumes"
            return humanize(field)
        return title
    body = lines(item["summary"])
    # The import titled registry claims with their ID, their value or their first line.
    imported = _registry(item) and (not title or title == key or (body and title == body[0]))
    if not imported:
        return title or humanize(item["kind"])
    tokens = [t for t in key.split("-")[1:] if not t.isdigit() and t != "NONE"]
    if len(body) > 1 and item["kind"] != "skill" and data.get("category"):
        return humanize(data["category"])
    if tokens:
        return humanize(" ".join(tokens))
    return humanize(data.get("category") or item["kind"])


def group_for(item):
    """The heading an entry sits under inside its section, or None."""
    data = item.get("data") or {}
    if item["kind"] == "personal":
        field = data.get("field") or ""
        for name, members in PERSONAL_GROUPS:
            if field in members:
                return name
        if field.endswith("_policy"):
            return "How resumes show these"
        return "Other details"
    if item["kind"] == "skill":
        for ref in data.get("source_refs") or []:
            match = SKILL_TIER.search(str(ref))
            if match:
                return match[1].strip()
        return "Added by you" if not _registry(item) else "Other skills"
    if item["kind"] == "fact":
        if not _registry(item):
            return "Added by you"
        return humanize(data.get("category") or "fact")
    return None


def extra_for(item) -> list[dict]:
    """Remaining registry details as labelled values, for the entry's 'About' panel."""
    data = item.get("data") or {}
    kind = item["kind"]
    used = {path[0] for path in PATHS.get(kind, {}).values()}
    if kind == "project":
        used.add("resume_content")
    if kind in ("personal", "fact"):
        used.add("value")
    if kind == "fact" and not isinstance(data.get("value"), str):
        used.add("approved_facts")
    rows = []
    for key, value in data.items():
        if key in META_KEYS or key in used or value in ("", None, [], {}) or value == item["title"]:
            continue
        rows.append({"key": key, "label": EXTRA_LABELS.get(key, humanize(key)), "value": value})
    return rows


def view(item) -> dict:
    """An entry as the Profile page shows it; the stored row is included unchanged."""
    data = item.get("data") or {}
    sources = data.get("source_refs")
    return {
        **item,
        "label": label_for(item),
        "group": group_for(item),
        "status": data.get("status") if isinstance(data.get("status"), str) else None,
        "usage": data.get("approved_external_use") if isinstance(data.get("approved_external_use"), str) else "",
        "sources": [str(s) for s in sources] if isinstance(sources, list) else [],
        "fields": fields_for(item),
        "form": form_for(item),
        "extra": extra_for(item),
        "in_sync": in_sync(item),
    }
