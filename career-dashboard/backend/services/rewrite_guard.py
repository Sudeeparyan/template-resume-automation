"""The check every reworded project bullet must pass: same facts, the posting's words only where
the evidence already has them.

A tailored resume may reword a registered project bullet so a recruiter sees the overlap with
the posting ("Built Power BI reports" -> "Built Power BI dashboards for stakeholders"). The new
wording is kept only when, compared with the registered bullet and the evidence it cites:

* it states exactly the same numbers (none dropped, none added) and no new month or year;
* every capitalised name and every technical term in it (a word with a capital inside, a digit or
  one of + # . / -) is in the registered bullet, the cited evidence or the registered skills;
* it uses none of the posting's requirement terms that the evidence does not hold, nothing on the
  never-claim list and none of the banned filler;
* it adds at most ``MAX_NEW_WORDS`` new content words, keeps at least ``MIN_KEPT`` of the
  original's, and stays a resume line (5 to ``MAX_CHARS`` characters, not first person).

The function is pure, so the resume validator (scripts/validate_resume.py) runs it again on the
saved source: a reworded bullet that no longer passes fails the release check. Otherwise the
registered wording is kept.
"""

from __future__ import annotations

import re

MAX_NEW_WORDS = 5
MIN_KEPT = 0.5
MAX_CHARS = 240
FILLER = ("passionate about", "results-oriented", "proven track record", "leveraged", "spearheaded", "synergies",
          "robust", "seamless", "cutting-edge", "dynamic professional", "responsible for", "worked on", "helped with",
          "world-class", "best-in-class")
MONTHS = re.compile(r"(?i)\b(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sep(t(ember)?)?|"
                    r"oct(ober)?|nov(ember)?|dec(ember)?)\b")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./'-]*[A-Za-z0-9+#]|[A-Za-z]")
# Short words that carry no fact; a rewrite may add or drop them freely.
COMMON = set("""a an the and or of to in on for with by from at as into using use used via per across over under
than that this these those which who whose its their our my your it is are was were be been being has have had
not no also both each every all any more most less fewer new key core main clear weekly daily monthly
built build building created create creating developed develop developing designed design designing made make
delivered deliver delivering wrote write writing ran run running analysed analyzed analyse analyze analysing
analyzing improved improve improving reduced reduce reducing increased increase increasing automated automate
automating cleaned clean cleaning prepared prepare preparing presented present presenting explained explain
explaining modelled modeled model modelling modeling tested test testing evaluated evaluate evaluating compared
compare comparing implemented implement implementing produced produce producing supported support supporting
managed manage managing tracked track tracking reported report reporting forecast forecasted forecasting
predicted predict predicting measured measure measuring monitored monitor monitoring
team teams stakeholders stakeholder managers manager users user clients client customers customer business
data dataset datasets records rows tables report reports reporting dashboard dashboards insight insights
analysis analyses analytics pipeline pipelines process processes workflow workflows results result findings
""".split())


def _plain(text: str) -> str:
    return " ".join(str(text or "").replace("’", "'").split())


def wording(item: dict) -> str:
    """All the registered words of one evidence entry (a claim or a project)."""
    content = item.get("resume_content") or {}
    parts = [item.get("value", ""), item.get("title", ""), item.get("employer", ""), item.get("institution", ""),
             *(item.get("approved_facts") or []), *(item.get("technologies") or []),
             content.get("title", ""), content.get("context", ""), *(content.get("bullets") or [])]
    return " ".join(str(part) for part in parts if part)


def evidence_wording(evidence: dict) -> dict[str, str]:
    """id -> registered words, for every usable claim and project (held or missing ones are left out)."""
    return {str(item["id"]): wording(item)
            for group in ("claims", "projects") for item in evidence.get(group) or []
            if isinstance(item, dict) and item.get("id") and item.get("status") not in {"hold", "missing"}
            and item.get("id") != "SKILL-NEVER-001"}


def registered_skills(evidence: dict) -> list[str]:
    return [str(fact) for claim in evidence.get("claims") or [] if isinstance(claim, dict)
            and claim.get("status") not in {"hold", "missing"} and claim.get("id") != "SKILL-NEVER-001"
            and ("skill" in str(claim.get("category", "")) or str(claim.get("id", "")).startswith("SKILL"))
            for fact in claim.get("approved_facts") or []]


def never_claimed(evidence: dict) -> list[str]:
    return [str(fact) for claim in evidence.get("claims") or [] if isinstance(claim, dict)
            and claim.get("id") == "SKILL-NEVER-001" for fact in claim.get("approved_facts") or []]


def numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER.findall(_plain(text))}


def _months(text: str) -> set[str]:
    return {match[0].casefold()[:3] for match in MONTHS.finditer(_plain(text))}


def _words(text: str) -> list[str]:
    return [w.strip(".'-").casefold() for w in WORD.findall(_plain(text)) if w.strip(".'-")]


def _content(text: str) -> set[str]:
    return {w for w in _words(text) if len(w) > 2 and w not in COMMON}


def _technical(text: str) -> set[str]:
    """Names and technical terms: capitalised words (not sentence-initial), and words with a capital
    inside, a digit or + # . / -  ("SQL", "Power BI", "scikit-learn", "C#", "Node.js")."""
    found = set()
    tokens = WORD.findall(_plain(text))
    for position, token in enumerate(tokens):
        word = token.strip(".'-")
        if not word:
            continue
        inner_capital = any(c.isupper() for c in word[1:])
        symbol = bool(re.search(r"[0-9+#/]|[A-Za-z][.-][A-Za-z]", word))
        capitalised = word[0].isupper() and position > 0
        if inner_capital or symbol or capitalised:
            found.add(word.casefold())
    return found


def _says(text: str, term: str) -> bool:
    low = " " + " ".join(_words(text)) + " "
    phrase = " ".join(_words(term))
    return bool(phrase) and f" {phrase} " in low


def check(original: str, rewrite: str, *, sources: list[str] = (), skills=(), never=(), posting_terms=(),
          filler=()) -> list[str]:
    """Why ``rewrite`` may not replace ``original`` ([] when it may)."""
    original, rewrite = _plain(original), _plain(rewrite)
    if not rewrite:
        return ["the new wording is empty"]
    if rewrite == original:
        return ["the new wording is the same as the registered one"]
    problems = []
    if len(rewrite.split()) < 5 or len(rewrite) > MAX_CHARS or len(rewrite) > len(original) * 1.35 + 40:
        problems.append(f"length {len(rewrite)} characters (the registered line has {len(original)})")
    if re.match(r"(?i)^(i|we|my|our)\b", rewrite):
        problems.append("a resume line does not start with I, we, my or our")
    if numbers(rewrite) != numbers(original):
        added, dropped = sorted(numbers(rewrite) - numbers(original)), sorted(numbers(original) - numbers(rewrite))
        problems.append("numbers changed" + (f": added {', '.join(added)}" if added else "")
                        + (f"; dropped {', '.join(dropped)}" if dropped else ""))
    if _months(rewrite) - _months(original):
        problems.append("a month that the registered line does not state")
    allowed = " ".join([original, *map(str, sources), *map(str, skills)])
    allowed_terms = _technical(allowed) | {w for w in _words(allowed)}
    new_terms = sorted(term for term in _technical(rewrite) if term not in allowed_terms and term not in COMMON)
    if new_terms:
        problems.append("names or tools the evidence does not hold: " + ", ".join(new_terms[:6]))
    posting = [term for term in posting_terms if _says(rewrite, term) and not _says(allowed, term)]
    if posting:
        problems.append("posting terms the evidence does not hold: " + ", ".join(posting[:6]))
    never_used = [term for term in never if _says(rewrite, term)]
    if never_used:
        problems.append("never-claim skills: " + ", ".join(never_used[:6]))
    used_filler = [phrase for phrase in (*FILLER, *filler) if phrase and _says(rewrite, phrase)]
    if used_filler:
        problems.append("filler: " + ", ".join(dict.fromkeys(used_filler)))
    before, after = _content(original), _content(rewrite)
    source_words = {w for text in sources for w in _content(text)} | {w for text in skills for w in _content(text)}
    added_words = after - before - source_words
    if len(added_words) > MAX_NEW_WORDS:
        problems.append(f"{len(added_words)} new words (at most {MAX_NEW_WORDS}): " + ", ".join(sorted(added_words)[:8]))
    if before and len(before & after) / len(before) < MIN_KEPT:
        problems.append("it keeps too little of the registered line to be the same fact")
    return problems
