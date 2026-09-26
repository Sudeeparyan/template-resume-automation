"""Carry one Profile change into resume LaTeX: the base template and open drafts.

A resume source prints registry wording on lines tagged `% EVIDENCE: <ID>`. When
an entry changes, each line tagged with its ID that still prints the entry's old
registered wording is rewritten to the new wording. Wording the candidate customised in
Resume Studio no longer matches the old registry text, so it is left alone.

What follows an entry:
- role and degree headings (title, employer, school, degree, dates, location);
- experience bullets: a changed bullet is rewritten, a removed one disappears and
  a new one is printed next to its neighbour;
- list macros (skill lines, coursework): items are renamed, dropped or appended;
- the two project slots, rewritten from the registry while they print it unchanged;
- the header (name and contact line), from the canonical profile.
A removed entry disappears wherever it was the only support for a line; a new role
or degree is placed in date order in its section.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from career import tex_escape
from validate_resume import extract_zero_argument_macros

TAG = re.compile(r"^(\s*)%\s*EVIDENCE:\s*(.*?)\s*$")
ITEM = re.compile(r"^(\s*)\\item\s+(.*?)\s*$")
MACRO = re.compile(r"\\newcommand\{\\([A-Za-z@]+)\}\s*")
SECTION = re.compile(r"^\s*\\section\{([^}]*)\}")
SCALARS = ("title", "employer", "dates", "location", "institution", "degree_as_supplied", "value")
# Macros with their own rules: the header is rebuilt from the profile, project slots from the registry.
OWN_RULES = ("ResumeContact", "SelectedProject", "SecondProject")
LIST_MACROS = ("Skills", "CoreSkills", "Coursework")
ACCENTS = [
    (r'\"a', "ä"), (r'\"o', "ö"), (r'\"u', "ü"), (r'\"A', "Ä"), (r'\"O', "Ö"), (r'\"U', "Ü"),
    (r"\'e", "é"), (r"\'E", "É"), (r"\`e", "è"), (r"\~n", "ñ"), (r"\c{c}", "ç"),
]
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def norm(text) -> str:
    """Plain comparable wording for a LaTeX fragment or a registry string."""
    text = str(text or "")
    for latex, char in ACCENTS:
        text = text.replace("{" + latex + "}", char).replace(latex, char)
    for latex, char in ((r"\textbar{}", "|"), (r"\textbackslash{}", "\\"),
                        (r"\textasciitilde{}", "~"), (r"\textasciicircum{}", "^")):
        text = text.replace(latex, char)
    text = re.sub(r"\\([&%$#_{}])", r"\1", text)
    text = re.sub(r"\s*(?:--|\u2013|\u2014)\s*", " - ", text)
    return " ".join(text.split())


def tex(text) -> str:
    return tex_escape(" ".join(str(text or "").split()))


def tex_dates(text) -> str:
    """Registry date ranges print with an en dash: 'Sep 2024 - May 2026' -> 'Sep 2024 -- May 2026'."""
    return tex(re.sub(r"\s*[-\u2013\u2014]+\s*", " -- ", str(text or "")))


def _group(text, pos):
    """(open, close) offsets of the balanced {...} group at pos (after spaces), or None."""
    while pos < len(text) and text[pos].isspace():
        pos += 1
    if pos >= len(text) or text[pos] != "{":
        return None
    depth, index = 0, pos
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return pos, index
        index += 1
    return None


def _macro(line):
    """(name, open, close) for a one-line zero-argument \\newcommand, or None."""
    match = MACRO.search(line)
    if not match:
        return None
    span = _group(line, match.end())
    return (match[1], *span) if span else None


def _tagged(lines, entry_id):
    """(tag line, content line) pairs for every line an EVIDENCE tag attributes to entry_id."""
    for index, line in enumerate(lines):
        match = TAG.match(line)
        if match and entry_id in match[2].split() and index + 1 < len(lines):
            yield index, index + 1


def _split(value):
    separator = "; " if "; " in value else ", "
    return separator, [part.strip() for part in value.split(separator.strip()) if part.strip()]


def _is_list_macro(name):
    return name.startswith(LIST_MACROS)


def _skill_items(lines):
    """Case-folded items of every skills list in the source."""
    found = set()
    for line in lines:
        macro = _macro(line)
        if macro and macro[0].startswith(("Skills", "CoreSkills")):
            found |= {norm(item).casefold() for item in _split(line[macro[1] + 1:macro[2]])[1]}
    return found


def _ops(old, new):
    """Line up two wording lists: (pairs renamed, old indexes removed, (anchor index, new items) inserted)."""
    renamed, removed, inserted = [], [], []
    matcher = SequenceMatcher(None, [norm(x) for x in old], [norm(x) for x in new], autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        paired = min(i2 - i1, j2 - j1) if op == "replace" else 0
        renamed += [(i1 + k, new[j1 + k]) for k in range(paired)]
        removed += list(range(i1 + paired, i2))
        if j1 + paired < j2:
            inserted.append((i1 + paired, new[j1 + paired:j2]))
    return renamed, removed, inserted


# ---- one entry changed -----------------------------------------------------------


def _scalar_pairs(before, after):
    pairs = []
    for key in SCALARS:
        old, new = before.get(key), after.get(key)
        if isinstance(old, str) and old.strip() and old != (new if isinstance(new, str) else ""):
            pairs.append((key, old, new if isinstance(new, str) else ""))
    return pairs


def _replace_heading_args(line, pairs):
    match = re.search(r"\\(?:roleheading|clientheading)", line)
    if not match:
        return line
    spans, pos = [], match.end()
    while True:
        span = _group(line, pos)
        if not span:
            break
        spans.append(span)
        pos = span[1] + 1
    out = line
    for start, end in reversed(spans):
        inner = norm(line[start + 1:end])
        for key, old, new in pairs:
            if inner == norm(old):
                out = out[:start + 1] + (tex_dates(new) if key == "dates" else tex(new)) + out[end:]
                break
    return out


def _scalars(lines, entry_id, pairs):
    if not pairs:
        return lines
    for _, index in _tagged(lines, entry_id):
        line = lines[index]
        macro = _macro(line)
        if macro:
            name, start, end = macro
            if name.startswith(OWN_RULES):
                continue
            inner = norm(line[start + 1:end])
            for _, old, new in pairs:
                if inner == norm(old):
                    lines[index] = line[:start + 1] + tex(new) + line[end:]
                    break
        else:
            lines[index] = _replace_heading_args(line, pairs)
    return lines


def _bullets(lines, entry_id, old, new):
    """Rewrite the entry's printed bullets (\\item lines) to follow its list of facts."""
    if old == new:
        return lines
    printed, tag_of = {}, {}
    for tag, index in _tagged(lines, entry_id):
        match = ITEM.match(lines[index])
        if not match:
            continue
        text = norm(match[2])
        for k, fact in enumerate(old):
            if k not in printed and norm(fact) == text:
                printed[k], tag_of[index] = index, tag
                break
    if not printed:
        return lines
    renamed, removed, inserted = _ops(old, new)
    replace = {printed[k]: text for k, text in renamed if k in printed}
    drop = {printed[k] for k in removed if k in printed}
    after, before = {}, {}
    for anchor, items in inserted:
        previous = next((printed[k] for k in range(anchor - 1, -1, -1) if k in printed), None)
        if previous is not None:
            after.setdefault(previous, []).extend(items)
            continue
        following = next((printed[k] for k in range(anchor, len(old)) if k in printed), None)
        if following is not None:
            before.setdefault(following, []).extend(items)
    skip = drop | {tag_of[i] for i in drop}
    out = []
    for index, line in enumerate(lines):
        indent = ITEM.match(line)[1] if index in tag_of else ""
        if index + 1 in before:
            first = index + 1
            item_indent = ITEM.match(lines[first])[1]
            for text in before[first]:
                out += [item_indent + "% EVIDENCE: " + entry_id, item_indent + "\\item " + tex(text)]
        if index in skip:
            continue
        if index in replace:
            line = indent + "\\item " + tex(replace[index])
        out.append(line)
        for text in after.get(index, []):
            out += [indent + "% EVIDENCE: " + entry_id, indent + "\\item " + tex(text)]
    return out


def _list_macros(lines, entry_id, old, new, others):
    """Follow a list entry (skills, coursework) in the list macros tagged with it."""
    if old == new:
        return lines
    renamed, removed, inserted = _ops(old, new)
    rename = {norm(old[k]): text for k, text in renamed}
    drop = {norm(old[k]) for k in removed if norm(old[k]).casefold() not in others}
    additions = [text for _, items in inserted for text in items]
    known = {norm(x) for x in old}
    present = _skill_items(lines)
    placed = False
    for _, index in _tagged(lines, entry_id):
        line = lines[index]
        macro = _macro(line)
        if not macro or not _is_list_macro(macro[0]):
            continue
        name, start, end = macro
        separator, items = _split(line[start + 1:end])
        if not any(norm(item) in known for item in items):
            continue
        kept = []
        for item in items:
            key = norm(item)
            if key in rename:
                kept.append(tex(rename[key]))
            elif key not in drop:
                kept.append(item)
        if additions and not placed:
            for text in additions:
                if norm(text).casefold() not in present:
                    kept.append(tex(text))
                    present.add(norm(text).casefold())
            placed = True
        lines[index] = line[:start + 1] + separator.join(kept) + line[end:]
    return lines


def drop_skills(source, names):
    """Remove never-claim skills from every skills list, whatever tags them."""
    banned = {norm(name).casefold() for name in names}
    lines = source.split("\n")
    for index, line in enumerate(lines):
        macro = _macro(line)
        if macro and macro[0].startswith(("Skills", "CoreSkills")):
            separator, items = _split(line[macro[1] + 1:macro[2]])
            kept = [item for item in items if norm(item).casefold() not in banned]
            if len(kept) != len(items):
                lines[index] = line[:macro[1] + 1] + separator.join(kept) + line[macro[2]:]
    return "\n".join(lines)


def _slot_prints_registry(macros, prefix, content):
    """Does the slot still print this registry content (bullets may have been cut, never reworded)?"""
    if not content:
        return False
    if norm(macros.get(prefix + "Title")) != norm(content.get("title")):
        return False
    if norm(macros.get(prefix + "Context")) != norm(content.get("context")):
        return False
    bullets = content.get("bullets") or []
    for k, suffix in enumerate(("BulletOne", "BulletTwo", "BulletThree")):
        printed = norm(macros.get(prefix + suffix))
        if printed and (k >= len(bullets) or printed != norm(bullets[k])):
            return False
    return True


def _project_slots(source, entry_id, before, after, fallback):
    from backend.services.resume_projects import install_project

    for prefix, second in (("SelectedProject", False), ("SecondProject", True)):
        macros = extract_zero_argument_macros(source)
        if macros.get(prefix + "ID") != entry_id:
            continue
        # The slot keeps the number of bullets it printed, so the page layout holds.
        count = max(2, sum(1 for s in ("BulletOne", "BulletTwo", "BulletThree") if norm(macros.get(prefix + s))))
        if after is not None:
            if not _slot_prints_registry(macros, prefix, (before or {}).get("resume_content")):
                continue  # customised in Resume Studio: preserve the candidate's edit
            content = dict(after["resume_content"])
        else:
            other = macros.get(("SelectedProject" if second else "SecondProject") + "ID")
            choice = next((p for p in fallback if p["id"] not in (entry_id, other)
                           and (second or p.get("signature_eligible", True) is not False)), None)
            if choice is None:
                raise ValueError("A resume needs two projects, so this project can't be removed "
                                 "until another project is added.")
            entry_id, content = choice["id"], {k: choice[k] for k in ("title", "context", "bullets")}
        content["bullets"] = list(content.get("bullets") or [])[:count]
        source = install_project(source, {"id": entry_id, "resume_content": content}, second)
        if len(content["bullets"]) < 3:
            # An unprinted bullet has no macro at all, as after the one-page cut (validators count macros).
            source = re.sub(r"(?m)^% EVIDENCE: [^\n]+\n\\newcommand\{\\" + prefix + r"BulletThree\}\{\}\n", "", source)
        entry_id = (after or before)["id"]
    return source


def _month(text):
    if re.search(r"(?i)present|current|now", text):
        return (9999, 12)
    match = re.search(r"([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{4})", text)
    if match:
        return (int(match[2]), MONTHS.get(match[1].casefold(), 0))
    match = re.search(r"(\d{4})", text)
    return (int(match[1]), 0) if match else (0, 0)


def _date_rank(dates):
    """Newest first: by end date, then by start date (two current roles: the later start leads)."""
    parts = norm(dates).split(" - ")
    return (_month(parts[-1]), _month(parts[0]))


def _insert_role(lines, entry):
    """Place a new role or degree in date order within its section."""
    category = entry.get("category")
    # A heading needs its anchor: a role its title and employer, a degree its school.
    if category == "employment" and not (entry.get("title") and entry.get("employer")):
        return lines
    if category == "education" and not entry.get("institution"):
        return lines
    if category == "employment":
        section = "Professional Experience"
        heading = [entry.get("title"), entry.get("dates"), entry.get("employer"), entry.get("location")]
        bullets = [b for b in entry.get("approved_facts") or [] if str(b).strip()]
    elif category == "education":
        section = "Education"
        heading = [entry.get("institution"), entry.get("dates"), entry.get("degree_as_supplied"), entry.get("location")]
        bullets = []
    else:
        return lines
    start = next((i for i, line in enumerate(lines) if (m := SECTION.match(line)) and m[1] == section), None)
    if start is None:
        return lines
    end = next((i for i in range(start + 1, len(lines))
                if SECTION.match(lines[i]) or lines[i].strip().startswith("\\end{document}")), len(lines))
    rank = _date_rank(entry.get("dates"))
    position = None
    for index in range(start + 1, end):
        if TAG.match(lines[index]) and index + 1 < end and "\\roleheading" in lines[index + 1]:
            args = []
            pos = lines[index + 1].index("\\roleheading") + len("\\roleheading")
            for _ in range(2):
                span = _group(lines[index + 1], pos)
                if not span:
                    break
                args.append(lines[index + 1][span[0] + 1:span[1]])
                pos = span[1] + 1
            if len(args) == 2 and _date_rank(args[1]) < rank:
                position = index
                break
    if position is None:
        position = end
        while position > start + 1 and not lines[position - 1].strip():
            position -= 1
    block = [
        "% EVIDENCE: " + entry["id"],
        "\\roleheading{" + tex(heading[0]) + "}{" + tex_dates(heading[1]) + "}{" + tex(heading[2]) + "}{" + tex(heading[3]) + "}",
    ]
    if bullets:
        block.append("\\begin{resumeitems}")
        for bullet in bullets:
            block += ["  % EVIDENCE: " + entry["id"], "  \\item " + tex(bullet)]
        block.append("\\end{resumeitems}")
    block.append("")
    if lines[position - 1].strip():
        block.insert(0, "")
    return lines[:position] + block + lines[position:]


def _remove(lines, entry_id, before, owners):
    """Drop what only this entry supported; lines other entries also support keep their other tags."""
    items = [norm(x) for x in (before.get("approved_facts") or [])]
    skip = set()
    for tag, index in list(_tagged(lines, entry_id)):
        indent, ids = TAG.match(lines[tag])[1], TAG.match(lines[tag])[2].split()
        rest = [i for i in ids if i != entry_id]
        line = lines[index]
        macro = _macro(line)
        if macro and _is_list_macro(macro[0]) and items:
            name, start, end = macro
            separator, printed = _split(line[start + 1:end])
            kept = [p for p in printed
                    if norm(p) not in items or owners.get(norm(p).casefold(), set()) - {entry_id}]
            supporters = (set(rest) | {o for p in kept for o in owners.get(norm(p).casefold(), set())}) - {entry_id}
            if kept and supporters:
                lines[index] = line[:start + 1] + separator.join(kept) + line[end:]
                order = {i: n for n, i in enumerate(ids)}
                lines[tag] = indent + "% EVIDENCE: " + " ".join(sorted(supporters, key=lambda i: (order.get(i, len(ids)), i)))
                continue
            # Nothing a registered entry supports is left in this list: it goes (below).
        elif rest:
            lines[tag] = indent + "% EVIDENCE: " + " ".join(rest)
            continue
        skip |= {tag, index}
        if "\\roleheading" in line:
            # The role's bullet list goes with its heading.
            follow = index + 1
            while follow < len(lines) and not lines[follow].strip():
                follow += 1
            if follow < len(lines) and lines[follow].strip().startswith("\\begin{resumeitems}"):
                close = follow
                while close < len(lines) and not lines[close].strip().startswith("\\end{resumeitems}"):
                    close += 1
                skip |= set(range(index + 1, close + 1))
        if macro:
            # A macro that is gone takes the lines printing it along.
            use = re.compile(r"\\" + re.escape(macro[0]) + r"(?![A-Za-z])")
            for other, text in enumerate(lines):
                if other != index and use.search(text) and not MACRO.search(text):
                    skip.add(other)
                    if other > 0 and TAG.match(lines[other - 1]):
                        skip.add(other - 1)
    return [line for index, line in enumerate(lines) if index not in skip]


def _tidy(source):
    from backend.services.resume_studio import drop_empty_item_blocks

    source = drop_empty_item_blocks(source)
    return re.sub(r"\n{3,}", "\n\n", source)


def apply_change(source, group, before, after, owners=None, fallback=()):
    """Return source with one registry entry's change applied.

    group is "claims" or "projects"; before/after are the registry entries (None
    when the entry is new or removed). owners maps a case-folded list item to the
    IDs of the other active entries that list it, so a shared skill stays printed.
    fallback is the ranked project list used to refill a slot whose project goes.
    """
    owners = owners or {}
    entry = after or before
    entry_id = entry["id"]
    if group == "projects":
        source = _project_slots(source, entry_id, before, after, fallback)
        if after is not None:
            return source
    if after is None:
        return _tidy("\n".join(_remove(source.split("\n"), entry_id, before, owners)))
    if before is None:
        return _tidy("\n".join(_insert_role(source.split("\n"), after)))
    lines = source.split("\n")
    lines = _scalars(lines, entry_id, _scalar_pairs(before, after))
    old, new = before.get("approved_facts") or [], after.get("approved_facts") or []
    if isinstance(old, list) and isinstance(new, list):
        lines = _bullets(lines, entry_id, [str(x) for x in old], [str(x) for x in new])
        others = {key for key, ids in owners.items() if ids - {entry_id}}
        lines = _list_macros(lines, entry_id, [str(x) for x in old], [str(x) for x in new], others)
    return _tidy("\n".join(lines))


# ---- the header --------------------------------------------------------------------


def _href_target(url):
    return url.replace("%", r"\%").replace("#", r"\#")


def contact_line(candidate, keys):
    parts = []
    for key in keys:
        value = str(candidate.get(key) or "").strip()
        if not value:
            continue
        if re.match(r"https?://", value):
            parts.append(r"\href{" + _href_target(value) + "}{" + tex_escape(value) + "}")
        elif "@" in value:
            parts.append(r"\href{mailto:" + _href_target(value) + "}{" + tex_escape(value) + "}")
        else:
            parts.append(tex_escape(value))
    return r" \textbar{} ".join(parts)


def apply_header(source, old, new, header_fields):
    """Follow a changed name or contact detail in the resume header."""
    name_old, name_new = str(old.get("full_name") or ""), str(new.get("full_name") or "")
    if name_old and name_new and name_old != name_new:
        lines = source.split("\n")
        for index, line in enumerate(lines):
            macro = _macro(line)
            if macro and macro[0] == "ResumeName" and norm(line[macro[1] + 1:macro[2]]) == norm(name_old):
                lines[index] = line[:macro[1] + 1] + tex(name_new) + line[macro[2]:]
        source = "\n".join(lines)
        source = source.replace("pdfauthor={" + name_old + "}", "pdfauthor={" + name_new + "}")
        source = source.replace("pdftitle={" + name_old + " - Resume}", "pdftitle={" + name_new + " - Resume}")
    keys = [key for key in header_fields if key != "full_name"]
    if all(old.get(key) == new.get(key) for key in keys):
        return source
    lines = source.split("\n")
    for index, line in enumerate(lines):
        macro = _macro(line)
        if not macro or macro[0] != "ResumeContact":
            continue
        value = line[macro[1] + 1:macro[2]]
        if value == contact_line(old, keys):
            value = contact_line(new, keys)
        else:
            # A hand-edited contact line: swap each changed value where it appears.
            for key in keys:
                before, after = str(old.get(key) or ""), str(new.get(key) or "")
                if before and before != after:
                    value = value.replace(before, after).replace(tex_escape(before), tex_escape(after))
        lines[index] = line[:macro[1] + 1] + value + line[macro[2]:]
    return "\n".join(lines)
