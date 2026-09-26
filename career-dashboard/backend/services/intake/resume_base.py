"""The evidence-tagged base resume for a new profile, generated from its registry.

Uses the legacy-compatible layout and macros (roleheading,
Skills* lists, Coursework, the SelectedProject / SecondProject slots and their
block markers, a `% EVIDENCE:` tag above every content line), so Resume Studio,
resume_sync and the validator work on it unchanged. Only registered wording is
printed. It is sized for one page: the roles share about seven bullets (a sole
role up to seven, the newest of four or more roles four), skills four lines with
each skill once, coursework eight modules, two projects of three bullets; when
the result still runs past one page it is rendered again with `trim`.
"""

from __future__ import annotations

import re

from career import tex_escape
from backend.services.resume_sync import contact_line

PREAMBLE = r"""\documentclass[%(paper)s,10pt]{article}

\usepackage[top=0.5in,bottom=0.5in,left=0.6in,right=0.6in]{geometry}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[english]{babel}
\usepackage{enumitem}
\usepackage{xcolor}
\usepackage[hidelinks]{hyperref}
\usepackage{titlesec}
\usepackage{lmodern}

\hypersetup{
  pdfauthor={%(name)s},
  pdftitle={%(name)s - Resume},
  pdfsubject={Evidence-grounded resume},
  pdfkeywords={%(keywords)s}
}

\renewcommand{\baselinestretch}{1.0}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\setlist[itemize]{leftmargin=1.15em,itemsep=2pt,topsep=2pt,parsep=0pt,partopsep=0pt}
\pagestyle{empty}
\raggedright
\urlstyle{same}

\titleformat{\section}{\large\scshape}{}{0pt}{}[\vspace{-2pt}\titlerule]
\titlespacing*{\section}{0pt}{7pt}{4pt}

\newlength{\roleheadingright}
\newcommand{\roleheading}[4]{%%
  \textbf{#1} \hfill #2\\
  \settowidth{\roleheadingright}{\textit{#4}}%%
  \parbox[t]{\dimexpr\linewidth-\roleheadingright-1em\relax}{\raggedright\textit{#3}}%%
  \hfill\parbox[t]{\roleheadingright}{\raggedleft\textit{#4}}\vspace{1pt}
}
\newcommand{\clientheading}[1]{\textbf{#1}\vspace{1pt}}
\newenvironment{resumeitems}{\begin{itemize}}{\end{itemize}}
"""

MAX_ROLE_BULLETS = (4, 2, 2, 1)
# Fewer roles leave room for more of each: a sole role fills what four would share.
ROLE_BULLETS = {1: (7,), 2: (5, 3), 3: (4, 3, 2)}
MAX_SKILL_LINES = 4
MAX_SKILLS_PER_LINE = 14
MAX_COURSEWORK = 8


def macro_name(label: str, taken: set) -> str:
    """'Machine Learning & AI' -> 'SkillsMachineLearningAI' (letters only, unique)."""
    words = re.findall(r"[A-Za-z]+", label or "")
    base = "Skills" + ("".join(w[:1].upper() + w[1:].lower() for w in words)[:40] or "General")
    name, letters = base, iter("BCDEFGHIJKLMNOPQRSTUVWXYZ")
    while name in taken:
        name = base + next(letters)
    taken.add(name)
    return name


def resume_line(fact: str) -> str:
    """The person's sentence as a resume line: no STAR label, no leading "I"
    ("Action: I built a model" -> "Built a model"). Nothing else changes."""
    text = re.sub(r"^(situation|task|action|result|outcome)\s*:\s*", "", str(fact).strip(), flags=re.I)
    if re.match(r"^I\s+[a-z]", text):
        text = text[2:].lstrip()
        text = text[:1].upper() + text[1:]
    return text.rstrip(".")


def pick(facts, limit: int, banned=(), longest: int = 240) -> list[str]:
    """Up to `limit` registered sentences for a resume, in the person's own order: none with
    banned filler, none too long for one line pair, and no two saying the same thing.
    Measured results come first, then things the person did, then other numbers."""
    patterns = [re.compile(r"(?i)\b" + re.escape(str(word)) + r"\b") for word in banned]
    # A resume line is a sentence: it starts with a capital letter and says something
    # (a bare "95 percent improvement" fragment stays in the registry, off the page).
    fit = [str(f).strip() for f in facts or []
           if str(f).strip()[:1].isupper() and len(str(f).split()) >= 5 and len(str(f)) <= longest
           and not any(p.search(str(f)) for p in patterns) and not META.search(str(f))]

    def score(fact):
        # A year is not a result ("Worked for two years, from June 2022" is not chosen for its dates).
        numbers = _numbers(fact)
        result = bool(numbers) and bool(RESULT.search(fact))
        action = bool(ACTION.match(fact)) and not WEAK_START.match(fact)
        return 3 * result + 2 * action + 0.5 * min(len(numbers), 3)

    chosen: list[int] = []
    for i in sorted(range(len(fit)), key=lambda i: (-score(fit[i]), i)):
        if len(chosen) == limit:
            break
        if not any(_repeats(fit[i], fit[j]) for j in chosen):
            chosen.append(i)
    return [fit[i] for i in sorted(chosen)]


def _numbers(text: str) -> set:
    """'157,824', '7.28', '62.69' (not a year, not a trailing comma)."""
    return set(re.findall(r"\d(?:[\d,]*\d)?(?:\.\d+)?", re.sub(r"\b(?:19|20)\d{2}\b", "", text)))


def _words(text: str) -> set:
    words = (w.strip(".-") for w in re.findall(r"[a-z0-9][a-z0-9.%²-]*", text.casefold()))
    return {w for w in words if len(w) > 2 and w not in STOP}


def _repeats(fact: str, other: str) -> bool:
    """`fact` adds nothing to `other`: every number it states is already there, or it is
    mostly the same words ("The selected model achieved MAE 7.28" after "... achieved an MAE of 7.28, RMSE ...")."""
    numbers = _numbers(fact)
    if numbers and numbers <= _numbers(other):
        return True
    a, b = _words(fact), _words(other)
    return bool(a and b) and len(a & b) / min(len(a), len(b)) >= 0.6


STOP = {"the", "and", "for", "with", "from", "that", "this", "was", "were", "which", "into", "using", "used", "over", "their", "its"}
# A measured outcome, not just a number ("accuracy rose to 64%", "errors reduced by 12 percent").
RESULT = re.compile(r"(?i)\b(?:achiev\w*|improv\w*|reduc\w*|increas\w*|decreas\w*|cut|sav(?:ed|ing)|grew|rose|rais\w*|"
                    r"boost\w*|accuracy|precision|recall|f1|auc|r²|r2|mae|rmse|mape|wape|percent|faster|fewer)\b|%")
# The extraction's own voice ("the text says ...") is profile context, never a resume line.
META = re.compile(r"(?i)\bthe (?:text|document|documents|source|author) (?:says|states|describes|mentions|notes)\b")


ACTION = re.compile(
    r"^(?:[A-Z][a-z]+ed|Built|Led|Ran|Wrote|Made|Drove|Won|Taught|Grew|Cut|Set up|Designed|Created|Developed|"
    r"Automated|Analy[sz]ed|Implemented|Delivered|Engineered|Integrated|Benchmarked|Reduced|Improved|Increased)\b")


# Openers that say little about what was achieved.
WEAK_START = re.compile(r"^(?:Worked|Helped|Assisted|Involved|Tasked|Used|Learned|Studied|Was|Were)\b")


def fit_line(items, budget: int = 95) -> list:
    """As many items as fit on about one printed line (the first always stays)."""
    out, length = [], 0
    for item in items:
        length += len(str(item)) + 2
        if out and length > budget:
            break
        out.append(str(item))
    return out


def _tex_dates(dates: str) -> str:
    return tex_escape(re.sub(r"\s*[-–—]+\s*", " -- ", str(dates or "")))


def role_limit(count: int, index: int, trim: int = 0) -> int:
    """Bullets for the index-th newest of `count` roles; each `trim` step takes one away (never below one)."""
    limits = ROLE_BULLETS.get(count, MAX_ROLE_BULLETS)
    return max(1, limits[min(index, len(limits) - 1)] - trim)


def render(profile: dict, evidence: dict, trim: int = 0) -> str:
    """The base resume. `trim` (0-3) shortens it when the first version runs past one page."""
    candidate = profile.get("candidate") or {}
    contract = profile.get("resume_contract") or {}
    name = str(candidate.get("full_name") or "Candidate")
    paper = {"a4": "a4paper", "letter": "letterpaper"}[str(contract.get("paper") or "letter").lower()]
    usable = [c for c in evidence.get("claims") or [] if c.get("status") not in {"hold", "missing"}]
    header_keys = [k for k in contract.get("header_fields") or [] if k != "full_name"]
    contact_ids = [c["id"] for c in usable if c.get("category") == "contact"]
    keywords = ", ".join((profile.get("target_roles") or {}).get("primary") or [])[:200]

    out = [f"% Evidence-grounded one-page {'A4' if paper == 'a4paper' else 'US Letter'} base resume for {name}.",
           "% Candidate claims are governed by data/context/evidence.yml. Every content line is",
           "% preceded by a \"% EVIDENCE:\" tag naming the registry entries that support it.",
           "% Generated from the candidate's own documents at intake; edit it in Resume Studio.", ""]
    out.append(PREAMBLE % {"paper": paper, "name": tex_escape(name), "keywords": tex_escape(keywords)})
    out += ["% ---- Header -------------------------------------------------------------",
            "% EVIDENCE: IDENTITY-001",
            "\\newcommand{\\ResumeName}{" + tex_escape(name) + "}",
            "% EVIDENCE: " + (" ".join(contact_ids) or "IDENTITY-001"),
            "\\newcommand{\\ResumeContact}{" + contact_line(candidate, header_keys) + "}", ""]

    # Skills: one macro per registered skill group, four lines at most (three when trimmed).
    # Each skill is printed once; a group that would only repeat earlier lines is left off.
    taken: set = set()
    printed: set = set()
    skill_lines = []
    out.append("% ---- Skills (one macro per bold category; items ranked per JD by Resume Studio) --")
    for claim in [c for c in usable if c.get("category") == "skill" and c.get("approved_facts")]:
        if len(skill_lines) == MAX_SKILL_LINES - (1 if trim >= 2 else 0):
            break
        fresh = [str(s) for s in claim["approved_facts"] if str(s).strip().casefold() not in printed]
        if len(fresh) < min(3, len(claim["approved_facts"])):
            continue
        label = str(claim.get("title") or "Skills")
        items = fit_line(fresh[:MAX_SKILLS_PER_LINE], budget=100 - len(label))
        printed |= {i.strip().casefold() for i in items}
        macro = macro_name(claim.get("title") or claim["id"], taken)
        out += ["% EVIDENCE: " + claim["id"], "\\newcommand{\\" + macro + "}{" + tex_escape(", ".join(items)) + "}"]
        skill_lines.append((claim, macro))
    out.append("")

    # Coursework: the most recent degree's modules.
    coursework = next((c for c in usable if c.get("category") == "academic_coursework" and c.get("approved_facts")), None)
    if coursework:
        out += ["% ---- Coursework -----------------------------------------------------------",
                "% EVIDENCE: " + coursework["id"],
                "\\newcommand{\\Coursework}{" + tex_escape(", ".join(map(str, coursework["approved_facts"][:MAX_COURSEWORK]))) + "}", ""]

    # The two project slots: the first two resume-ready projects (the signature is chosen per company later).
    per_project = 3 if trim < 3 else 2
    projects = [p for p in evidence.get("projects") or []
                if p.get("resume_content") and p.get("status") not in {"hold", "missing"}
                and len((p.get("resume_content") or {}).get("bullets") or []) >= 2][:2]
    for prefix, label, project in (("SelectedProject", "Signature project (first in Projects; unique per company)", projects[0] if projects else None),
                                   ("SecondProject", "Supporting project (may repeat across companies)", projects[1] if len(projects) > 1 else None)):
        if not project:
            continue
        content = project["resume_content"]
        out.append(f"% ---- {label} " + "-" * max(4, 60 - len(label)))
        values = [("ID", project["id"]), ("Title", content["title"]), ("Context", content.get("context", ""))]
        values += list(zip(("BulletOne", "BulletTwo", "BulletThree"), content["bullets"][:per_project]))
        for suffix, value in values:
            out += ["% EVIDENCE: " + project["id"], "\\newcommand{\\" + prefix + suffix + "}{" + tex_escape(str(value)) + "}"]
        out.append("")

    out += ["\\begin{document}", "", "\\begin{center}", "  % EVIDENCE: IDENTITY-001",
            "  {\\LARGE\\bfseries \\ResumeName}\\\\[3pt]",
            "  % EVIDENCE: " + (" ".join(contact_ids) or "IDENTITY-001"), "  \\ResumeContact", "\\end{center}", ""]

    sections = {}
    # Education, newest first as registered.
    lines = ["\\section{Education}", ""]
    for claim in [c for c in usable if c.get("category") == "education"]:
        lines += ["% EVIDENCE: " + claim["id"],
                  "\\roleheading{" + tex_escape(claim.get("institution", "")) + "}{" + _tex_dates(claim.get("dates", "")) + "}{"
                  + tex_escape(claim.get("degree_as_supplied", "")) + "}{" + tex_escape(claim.get("location", "")) + "}"]
        if coursework and coursework.get("degree_id") == claim["id"]:
            lines += ["% EVIDENCE: " + coursework["id"], "\\textbf{Coursework:} \\Coursework"]
        lines.append("")
    sections["Education"] = lines

    skill_heading = "Skills" if "Skills" in contract.get("required_sections", ()) else "Technical Skills"
    lines = ["\\section{" + skill_heading + "}"]
    for index, (claim, macro) in enumerate(skill_lines):
        end = "\\\\[1pt]" if index < len(skill_lines) - 1 else ""
        lines += ["% EVIDENCE: " + claim["id"], "\\textbf{" + tex_escape(claim.get("title") or "Skills") + ":} \\" + macro + end]
    lines.append("")
    sections[skill_heading] = lines

    roles = [c for c in usable if c.get("category") == "employment"]
    if roles:
        lines = ["\\section{Professional Experience}", ""]
        for index, claim in enumerate(roles):
            limit = role_limit(len(roles), index, trim)
            bullets = pick(claim.get("approved_facts"), limit, contract.get("prohibited_filler") or ())
            lines += ["% EVIDENCE: " + claim["id"],
                      "\\roleheading{" + tex_escape(claim.get("title", "")) + "}{" + _tex_dates(claim.get("dates", "")) + "}{"
                      + tex_escape(claim.get("employer", "")) + "}{" + tex_escape(claim.get("location", "")) + "}"]
            if bullets:
                lines.append("\\begin{resumeitems}")
                for bullet in bullets:
                    lines += ["  % EVIDENCE: " + claim["id"], "  \\item " + tex_escape(str(bullet))]
                lines.append("\\end{resumeitems}")
            lines.append("")
        sections["Professional Experience"] = lines

    lines = ["\\section{Projects}"]
    for prefix, marker, project in (("SelectedProject", "SELECTED_PROJECT", projects[0] if projects else None),
                                    ("SecondProject", "SECOND_PROJECT", projects[1] if len(projects) > 1 else None)):
        if not project:
            continue
        count = len(project["resume_content"]["bullets"][:per_project])
        lines += [f"% {marker}_BLOCK_START", f"\\textbf{{\\{prefix}Title}} \\hfill \\textit{{\\{prefix}Context}}", "\\begin{resumeitems}"]
        for suffix in ("One", "Two", "Three")[:count]:
            lines += ["  % EVIDENCE: " + project["id"], f"  \\item \\{prefix}Bullet{suffix}"]
        lines += ["\\end{resumeitems}", f"% {marker}_BLOCK_END", ""]
    sections["Projects"] = lines

    order = list((contract.get("section_order_by_track") or {}).get("A") or contract.get("required_sections") or sections)
    for section in order:
        out += sections.get(section, [])
    out += ["\\end{document}", ""]
    return "\n".join(out)
