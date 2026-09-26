"""Combine what each section of the documents said into one draft profile.

Deterministic: the same degree, employer, project, skill group or certificate
mentioned in several sections becomes one entry whose lists are the union of what
each section found (duplicates removed by normalised wording), and whose `refs`
cite every block it came from. A conflict (two different date ranges for one
role) is never resolved silently: it becomes a question for the person.
Facts the auditor found missing are added where they belong, or kept as notes.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

LISTS = {
    "education": ("coursework", "facts"),
    "experience": ("bullets", "metrics", "tools"),
    "projects": ("facts", "metrics", "tools"),
    "certifications": ("details",),
}


def norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").casefold()).strip()


def _name_key(text) -> str:
    """Project/role names without list numbering or trailing punctuation: '2. Dublin ... ' -> 'dublin ...'."""
    return norm(re.sub(r"^\s*\(?\d+[.)]\s*", "", str(text or "")))


STOP = {"the", "a", "an", "of", "and", "for", "with", "using", "based", "on", "in", "to", "my", "your", "project",
        "projects", "system", "approach", "analysis"}


def _words(text) -> set:
    return {w for w in norm(text).split() if w not in STOP and len(w) > 1}


def _same(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b or a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.86:
        return True
    # "retail sales dashboard" is "Weekly Retail Sales Forecasting Dashboard": every
    # distinctive word of the shorter name appears in the longer one (two at least).
    small, large = sorted((_words(a), _words(b)), key=len)
    return len(small) >= 2 and small <= large


def _near(a: str, b: str) -> bool:
    """Two list items that say the same thing (a repeat in another section of the documents)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 25:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.9


def _union(target: list, items: list) -> list:
    seen = [norm(x) for x in target]
    for item in items or []:
        key = norm(item)
        if key and not any(_near(key, other) for other in seen):
            target.append(item)
            seen.append(key)
    return target


LEVELS = (
    ("doctorate", r"\b(ph\.?\s?d|doctor(ate)?)\b"),
    ("master", r"\b(master|m\.?\s?sc|m\.?\s?s|m\.?\s?tech|m\.?\s?eng|mba|postgraduate|post-graduate)\b"),
    ("bachelor", r"\b(bachelor|b\.?\s?tech|b\.?\s?sc|b\.?\s?e|b\.?\s?eng|undergraduate|computer science degree)\b"),
    ("diploma", r"\b(diploma|associate)\b"),
    ("secondary", r"\b(intermediate|higher secondary|leaving cert|a[- ]levels?|high school|12th|mpc)\b"),
    ("school", r"\b(school|schooling|10th|ssc)\b"),
)


def level(degree) -> str:
    import re as _re

    text = str(degree or "").casefold()
    for name, pattern in LEVELS:
        if _re.search(pattern, text):
            return name
    return "other"


def _fold_education(entries: list, questions: list) -> list:
    """A degree mentioned without its institution ("my Master's") joins the one registered
    degree of that level; one that cannot be placed stays, marked for the person."""
    full = [e for e in entries if norm(e.get("institution"))]
    out = list(full)
    for entry in entries:
        if norm(entry.get("institution")):
            continue
        same_level = [e for e in full if level(e.get("degree")) == level(entry.get("degree")) != "other"]
        if len(same_level) == 1:
            target = same_level[0]
            _fill(target, {k: v for k, v in entry.items() if k not in ("degree", "field")}, questions,
                  str(target.get("institution")))
            for field in LISTS["education"]:
                target[field] = _union(list(target.get(field) or []), entry.get(field) or [])
            _union(target.setdefault("refs", []), entry.get("refs") or [])
        else:
            out.append(entry)
    return out


DATE_KEYS = {"start", "end", "valid_until", "date"}
CONFLICT_KEYS = DATE_KEYS | {"title", "status", "full_name", "preferred_name", "email", "phone",
                             "linkedin", "github", "portfolio_url", "city", "country", "work_country",
                             "conditions", "needs_sponsorship_later"}
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
ONGOING = re.compile(r"\b(present|current|currently|now|ongoing|to date|till date|until now)\b", re.I)


def _date_parts(value) -> set:
    """The dated parts of a value: years, month names and day numbers ('Issued June 11, 2026' -> {2026, jun, 11})."""
    text = str(value or "").casefold()
    # Digits may touch letters ("DEC2027"), so digit boundaries, not word boundaries.
    parts = set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", text))
    parts |= {m for m in MONTHS if re.search(r"\b" + m, text)}
    if parts:
        parts |= {d.lstrip("0") for d in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", text)}
    return parts


def _refined(current, value):
    """The more precise of two dates that agree ('2023' and 'August 2023' -> 'August 2023';
    'Recently completed' and 'January 2026' -> 'January 2026'), or None when they disagree."""
    a, b = _date_parts(current), _date_parts(value)
    if ONGOING.search(str(current)) or ONGOING.search(str(value)):
        return None  # "Present" against a date is a real difference: still there, or left?
    if not a and b:
        return value
    if a and not b:
        return current
    if a <= b:
        return value
    if b <= a:
        return current
    return None


def _fill(target: dict, source: dict, questions: list, label: str) -> None:
    """Copy non-empty scalars; a different non-empty value becomes a question."""
    for key, value in source.items():
        if isinstance(value, list) or key == "refs":
            continue
        if value in ("", None, "unknown"):
            continue
        current = target.get(key)
        if current in ("", None, "unknown"):
            target[key] = value
        elif norm(current) != norm(value) and key in CONFLICT_KEYS:
            if key in DATE_KEYS and (better := _refined(current, value)) is not None:
                # One says the same date more precisely; nothing to ask.
                target[key] = better
                continue
            question = f"{label}: the documents give both “{current}” and “{value}” for {key.replace('_', ' ')}. Which is right?"
            if question not in questions:
                questions.append(question)


# A section's question that the rest of the documents answer ("What were the dates of the
# Bachelor's degree?" asked in one section, when another section gives them) is not asked.
ASKS_FOR = {
    "institution": ("institution",), "university": ("institution",), "college": ("institution",),
    "field": ("field",), "dates": ("start", "end"), "date": ("start", "end"), "start": ("start",), "end": ("end",),
    "grade": ("grade",), "title": ("title",), "employer": ("employer",),
}
QUESTION_LEVELS = {
    "bachelor": r"\b(bachelor|b\.?\s?tech|b\.?\s?sc|undergraduate)",
    "master": r"\b(master|m\.?\s?sc|m\.?\s?tech|mba|postgraduate)",
    "doctorate": r"\b(ph\.?\s?d|doctorate)",
}


def _answered(question: str, draft: dict) -> bool:
    text = str(question or "").casefold()
    if not re.search(r"\bwhat (?:was|were|is|are)\b", text):
        return False  # only "what was the ..." requests for a missing detail; checks and choices stay
    asked = {key for word, keys in ASKS_FOR.items() if re.search(r"\b" + word + r"\b", text) for key in keys}
    if not asked:
        return False
    words = set(norm(text).split())
    mentioned = []
    for entry in draft.get("education") or []:
        lvl = level(entry.get("degree"))
        named = norm(entry.get("institution")) and norm(entry["institution"]) in norm(text)
        if named or (lvl in QUESTION_LEVELS and re.search(QUESTION_LEVELS[lvl], text)):
            mentioned.append((entry, asked & {"institution", "field", "start", "end", "grade"}))
    for role in draft.get("experience") or []:
        if {w for w in norm(role.get("employer")).split() if len(w) >= 3 and w not in STOP} & words:
            mentioned.append((role, asked & {"title", "employer", "start", "end"}))
    if not mentioned:
        return False
    return all(keys and all(norm(entry.get(k)) for k in keys) for entry, keys in mentioned)


def _merge_list(entries: list, found: list, key_fn, kind: str, questions: list) -> None:
    for item in found:
        key = key_fn(item)
        match = next((e for e in entries if _same(key_fn(e), key)), None)
        if match is None and kind == "projects" and len(_words(key)) == 1:
            # "my cycling project": one distinctive word, named by exactly one other project.
            word = next(iter(_words(key)))
            owners = [e for e in entries if word in _words(key_fn(e))]
            match = owners[0] if len(owners) == 1 else None
        if match is None:
            entries.append({**item, "refs": list(item.get("refs") or [])})
            continue
        label = match.get("name") or match.get("employer") or match.get("institution") or kind
        _fill(match, item, questions, str(label))
        for field in LISTS.get(kind, ()):
            match[field] = _union(list(match.get(field) or []), item.get(field) or [])
        _union(match["refs"], item.get("refs") or [])


def merge(sections: list[dict], audits: list[dict] | None = None) -> dict:
    """sections: IntakeFacts dicts in document order; audits: IntakeAudit dicts (same order)."""
    draft = {
        "contact": {"refs": [], "languages": []}, "authorization": {"refs": []},
        "targets": {"roles": [], "countries": [], "cities": [], "arrangements": [], "refs": []},
        "education": [], "experience": [], "projects": [], "skills": [], "certifications": [],
        "statements": [], "interview_answers": [], "narrative_only": [], "questions": [], "missed": [],
    }
    questions = draft["questions"]
    for section in sections:
        for block in ("contact", "authorization", "targets"):
            part = section.get(block) or {}
            _fill(draft[block], part, questions, block.capitalize())
            for key, value in part.items():
                if isinstance(value, list) and key != "refs":
                    draft[block][key] = _union(list(draft[block].get(key) or []), value)
            _union(draft[block]["refs"], part.get("refs") or [])
        _merge_list(draft["education"], section.get("education") or [],
                    lambda e: norm(e.get("institution")) + " " + level(e.get("degree")) if norm(e.get("institution"))
                    else norm(e.get("degree")), "education", questions)
        _merge_list(draft["experience"], section.get("experience") or [],
                    lambda e: norm(e.get("employer")) + " " + norm(e.get("title")), "experience", questions)
        _merge_list(draft["projects"], section.get("projects") or [],
                    lambda e: _name_key(e.get("name")), "projects", questions)
        _merge_list(draft["certifications"], section.get("certifications") or [],
                    lambda e: norm(e.get("name")), "certifications", questions)
        for group in section.get("skills") or []:
            match = next((g for g in draft["skills"] if norm(g["name"]) == norm(group.get("name"))), None)
            if match is None:
                draft["skills"].append({**group, "skills": _union([], group.get("skills") or []),
                                        "refs": list(group.get("refs") or [])})
            else:
                _union(match["skills"], group.get("skills") or [])
                _union(match["refs"], group.get("refs") or [])
        for statement in section.get("statements") or []:
            if not any(norm(s["text"]) == norm(statement.get("text")) for s in draft["statements"]):
                draft["statements"].append(dict(statement))
        for answer in section.get("interview_answers") or []:
            match = next((a for a in draft["interview_answers"] if norm(a["question"]) == norm(answer.get("question"))), None)
            if match is None:
                draft["interview_answers"].append(dict(answer))
            elif norm(answer.get("answer")) not in norm(match["answer"]):
                match["answer"] = match["answer"] + "\n\n" + answer.get("answer", "")
                _union(match.setdefault("refs", []), answer.get("refs") or [])
        _union(draft["narrative_only"], section.get("narrative_only") or [])
        _union(questions, section.get("questions") or [])
    draft["education"] = _fold_education(draft["education"], questions)
    for entry in draft["education"]:
        if not norm(entry.get("degree")) and not norm(entry.get("institution")):
            # Modules listed without saying which programme they belong to: kept, labelled, asked about.
            entry["degree"] = "Coursework (programme not stated)"
            entry["unplaced"] = True
            if not any(re.search(r"(?i)\b(subjects|modules|coursework|courses)\b", q) and re.search(r"(?i)\b(degree|programme|program)\b", q)
                       for q in questions):
                first = ", ".join(str(c).split(":")[0] for c in (entry.get("coursework") or [])[:3])
                questions.append(f"Which degree or programme were these modules part of ({first})?")
    # Drop what another section already answered; keep the list, so nothing is silently lost.
    draft["resolved_questions"] = [q for q in questions if _answered(q, draft)]
    draft["questions"] = questions = [q for q in questions if q not in draft["resolved_questions"]]
    # A project the documents describe without a name keeps its facts; the person names it.
    for project in draft["projects"]:
        if not norm(project.get("name")):
            first = (project.get("refs") or ["?"])[0]
            project["name"] = f"Unnamed project (around {first})"
            project["unnamed"] = True
            # Asked in the setup chat, so it names what the project is about, never a block id.
            about = " ".join(str(project.get("summary") or (project.get("facts") or [""])[0] or "").split())
            about = (about[:90].rsplit(" ", 1)[0] + "…") if len(about) > 90 else about
            questions.append(f"One project in your documents has no name{f' (it is about: {about})' if about else ''}. What is it called?")
    # Omissions the auditor found are never lost: each becomes a statement (shown on the
    # Profile page and written to the context files) under its category.
    for audit in audits or []:
        for missed in (audit or {}).get("missed") or []:
            text = str(missed.get("text") or "").strip()
            if not text or any(norm(text) in norm(s["text"]) for s in draft["statements"]):
                continue
            draft["missed"].append(dict(missed))
            draft["statements"].append({"topic": "from the completeness check: " + str(missed.get("category") or "other"),
                                        "text": text, "refs": list(missed.get("refs") or [])})
    return draft
