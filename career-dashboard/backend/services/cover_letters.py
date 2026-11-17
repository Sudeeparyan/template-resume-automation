"""Cover letters written from the person's registered evidence and verified company facts only.

When an AI plan is ready, the ``cover_letter_writer`` specialist drafts the body from three
things: the posting with its checked requirement list, the person's registered evidence (the
only source of anything said about them) and the company facts that a dossier verified word for
word on their pages. The draft is then checked here, without AI, before anything is saved:

* every evidence id it cites is registered for this person, or is one of the verified facts given;
* every number it writes appears in the evidence or facts it cites, or in the posting;
* every capitalised name it puts after "at", "with", "in", "using" (an employer, a tool, a
  place) appears in the evidence, the facts or the posting;
* it names no skill on the never-claim list and no requirement the fit check found missing;
* it says nothing about visas, permits, stamps or sponsorship: an application form asks that,
  a letter never volunteers it;
* it uses none of the banned filler, ``unsupported_claims`` is empty, and the body is three to
  five short paragraphs.

A draft that passes is then read by a second AI that did not write it (``letter_auditor``), which
lists every sentence about the person that the evidence does not state ("real data", "test-driven",
"reviewed code with others"); any such sentence fails the draft too. A draft that fails goes back
to the writer once with the problems; a second failure, an audit that cannot run, or no AI gives
the template letter, which is built only from registered sentences. Either way the saved metadata
lists the evidence ids the letter really uses. Nothing is ever sent to anyone.
"""

from __future__ import annotations

import json
import re
from datetime import date

from backend.services import fit

MIN_WORDS, MAX_WORDS = 120, 420
MIN_PARAGRAPHS, MAX_PARAGRAPHS = 3, 5
MAX_FACTS = 6
FILLER = ("passionate about", "results-oriented", "proven track record", "leveraged", "spearheaded", "synergies",
          "robust", "seamless", "cutting-edge", "dynamic professional", "team player", "hit the ground running",
          "perfect fit", "perfect candidate", "dream job", "go-getter", "think outside the box")
IMMIGRATION = re.compile(r"(?i)\b(visas?|stamp ?[0-9][a-z]?|(?:work|employment) permits?|sponsor\w*|immigration|"
                         r"right to work|work authori[sz]ation|critical skills (?:employment )?(?:permit|occupations? list|list)|"
                         r"csep|gep)\b")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# The app's own words for the person's record; a letter is in the candidate's voice ("registered nurse" is fine).
APP_WORDS = re.compile(r"(?i)\b(?:my|the) evidence\b|\bevidence (?:shows|ids?)\b|"
                       r"\b(?:registered|recorded) (?:skills?|evidence|projects?|sentences?)\b")
# A capitalised name after a preposition, also at the start of a sentence: an employer, a tool, a
# place ("at Acme", "At Acme I built", "using Power BI", "with Node.js"). It ends at a sentence's
# full stop and never takes in the pronoun "I" (evals/cases.yml keeps these cases).
_WORD = r"[A-Z][\w&+#'-]*(?:\.\w+)*"
NAMED = re.compile(r"\b(?:[Aa]t|[Ww]ith|[Ff]or|[Jj]oined|[Ff]rom|[Ii]n|[Uu]sing|[Vv]ia|[Tt]hrough)\s+"
                   r"(" + _WORD + r"(?:\s+(?:(?!I\b)" + _WORD + r"|of|and|&))*)")
HEADER_FIELDS = ("email", "phone", "linkedin", "portfolio_url", "github")


def _plain(text: str) -> str:
    return " ".join(str(text or "").replace("’", "'").split()).casefold()


def _numbers(text: str) -> set[str]:
    return {number.replace(",", "").rstrip(".") for number in NUMBER.findall(str(text or ""))}


def _named(text: str) -> list[str]:
    out = []
    for match in NAMED.finditer(text):
        words = match[1].split()
        while words and words[-1].casefold() in {"of", "and", "&"}:
            words.pop()
        name = re.sub(r"'s$", "", " ".join(words)).rstrip(".,;:")
        if name:
            out.append(name)
    return out


def _known(name: str, everything: str) -> bool:
    """Whether a name is in the plain-text sources; a list ('Python and SQL') when each item is."""
    if _plain(name) in everything:
        return True
    parts = [part for part in re.split(r"\s+(?:and|&)\s+", name) if part]
    return len(parts) > 1 and all(_plain(part) in everything for part in parts)


def _says(text: str, term: str) -> bool:
    """Whether ``text`` names ``term`` (any way a posting or resume writes it), as whole words."""
    low = _plain(text)
    return any(re.search(r"(?<![\w+#])" + re.escape(alias) + r"(?![\w+#])", low) for alias in fit.aliases(term))


# ---- what the writer gets --------------------------------------------------------------------------

def company_facts(company: str) -> list[dict]:
    """Verified claims from a recent dossier on this employer (shared market store), as citable facts."""
    from backend.market.store import MarketStore
    from backend.permits.employer_names import normalize_ie

    key = normalize_ie(company) or " ".join(company.casefold().split())
    try:
        dossier = MarketStore().dossier(key) or {}
    except Exception:  # noqa: BLE001 - no market store yet: a letter without company facts
        return []
    claims = [claim for claim in dossier.get("claims") or [] if claim.get("facet") != "news"][:MAX_FACTS]
    return [{"id": f"COMPANY-{number}", "text": claim["text"], "quote": claim["quote"], "url": claim["url"]}
            for number, claim in enumerate(claims, 1)]


def payload(job: dict, cat: dict, matrix: dict, facts: list[dict], persona: dict | None) -> dict:
    requirements = [{"text": item["text"], "category": item["category"], "status": item["status"],
                     "evidence_ids": item.get("evidence_ids") or []}
                    for item in matrix.get("requirements") or [] if item.get("status") != "unknown"]
    return {
        "role": {"company": job["company"], "title": job["title"], "location": job.get("location") or "",
                 "posting": str(job.get("description") or "")[:8000]},
        "requirements": requirements,
        "evidence": [{"id": e["id"], "kind": e["kind"], "text": e["text"]} for e in cat["entries"] if e["kind"] != "note"],
        "limits": [e["text"] for e in cat["entries"] if e["kind"] == "note"],
        "never_claim": cat["never"],
        "company_facts": [{"id": fact["id"], "text": fact["text"]} for fact in facts],
        "rules": ("Write the body only: no address block, date, salutation or sign-off. Three or four short "
                  "paragraphs, 180 to 320 words, first person, plain and specific. Open with the role and the "
                  "strongest piece of matching evidence; then one or two examples from the evidence, in its own "
                  "words and numbers, tied to what the role asks for; at most one sentence on why this company, "
                  "using only company_facts; close briefly. Cite in evidence_ids every evidence id and "
                  "company fact id you used. Never mention visas, permits, stamps, sponsorship or work "
                  "authorisation. Never name a requirement whose status is 'missing', anything in never_claim, "
                  "or any employer, tool, number or date the evidence does not state."
                  + (f" Write in {persona['spelling']}." if persona and persona.get("spelling") else "")),
    }


# ---- the check ---------------------------------------------------------------------------------------

def check(draft: dict, *, job: dict, cat: dict, matrix: dict, facts: list[dict], filler=()) -> list[str]:
    """Why this draft cannot be used ([] when it can)."""
    body = str(draft.get("body") or "").strip()
    problems = []
    if not body:
        return ["the letter is empty"]
    if draft.get("unsupported_claims"):
        problems.append("it lists claims the evidence does not support: " + "; ".join(map(str, draft["unsupported_claims"]))[:300])
    entries = {e["id"]: e for e in cat["entries"]}
    by_fact = {fact["id"]: fact for fact in facts}
    cited = [str(i) for i in draft.get("evidence_ids") or []]
    unknown = [i for i in cited if i not in entries and i not in by_fact]
    if unknown:
        problems.append("it cites evidence ids that are not registered: " + ", ".join(unknown[:6]))
    if not any(i in entries for i in cited):
        problems.append("it cites none of the person's registered evidence")
    sources = " ".join([job["company"], job["title"], str(job.get("location") or ""), str(job.get("description") or "")]
                       + [entries[i]["text"] + " " + str(entries[i].get("name") or "") for i in cited if i in entries]
                       + [by_fact[i]["text"] + " " + by_fact[i]["quote"] for i in cited if i in by_fact])
    unsupported_numbers = sorted(_numbers(body) - _numbers(sources))
    if unsupported_numbers:
        problems.append("numbers that are not in the evidence it cites or the posting: " + ", ".join(unsupported_numbers[:6]))
    everything = _plain(sources + " " + " ".join(e["text"] + " " + str(e.get("name") or "") for e in cat["entries"]))
    strangers = [name for name in dict.fromkeys(_named(body)) if not _known(name, everything)]
    if strangers:
        problems.append("names that are not in the evidence or the posting: " + ", ".join(strangers[:6]))
    never = [term for term in cat["never"] if _says(body, term)]
    if never:
        problems.append("skills the person never claims: " + ", ".join(never[:6]))
    missing = [item["text"] for item in matrix.get("requirements") or [] if item.get("status") == "missing" and _says(body, item["text"])]
    if missing:
        problems.append("requirements the evidence does not show: " + ", ".join(missing[:6]))
    if IMMIGRATION.search(body):
        problems.append("it mentions immigration status (visas, permits, stamps or sponsorship); a letter never does")
    app_words = list(dict.fromkeys(match[0] for match in APP_WORDS.finditer(body)))
    if app_words:
        problems.append("it talks about the person's record in the app's words, not theirs: " + ", ".join(app_words[:4]))
    used_filler = [phrase for phrase in (*FILLER, *filler) if phrase and _plain(phrase) in _plain(body)]
    if used_filler:
        problems.append("filler phrases: " + ", ".join(dict.fromkeys(used_filler)))
    paragraphs = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    words = len(body.split())
    if not MIN_PARAGRAPHS <= len(paragraphs) <= MAX_PARAGRAPHS or not MIN_WORDS <= words <= MAX_WORDS:
        problems.append(f"{len(paragraphs)} paragraphs and {words} words (needs {MIN_PARAGRAPHS} to {MAX_PARAGRAPHS} "
                        f"paragraphs, {MIN_WORDS} to {MAX_WORDS} words)")
    return problems


def audit_request(draft: dict, *, job: dict, cat: dict, facts: list[dict]) -> dict:
    """What the independent auditor reads: the letter, the person's evidence, the company facts and the posting."""
    return {"letter": str(draft.get("body") or "").strip(),
            "evidence": [{"id": e["id"], "kind": e["kind"], "text": e["text"]} for e in cat["entries"] if e["kind"] != "note"],
            "company_facts": [{"id": fact["id"], "quote": fact["quote"]} for fact in facts],
            "posting": str(job.get("description") or "")[:6000]}


def audit(team, draft: dict, *, job: dict, cat: dict, facts: list[dict]) -> list[str]:
    """The independent auditor's problems with a draft; tried twice (an AI app can crash once), then raises."""
    request = audit_request(draft, job=job, cat=cat, facts=facts)
    try:
        return audit_problems(team.run("letter_auditor", request))
    except Exception:  # noqa: BLE001 - once more, then the caller falls back to the template
        return audit_problems(team.run("letter_auditor", request))


def audit_problems(verdict) -> list[str]:
    """The auditor's unsupported sentences as problems ([] when it found none)."""
    data = verdict.model_dump() if hasattr(verdict, "model_dump") else dict(verdict or {})
    found = [item for item in data.get("unsupported") or [] if isinstance(item, dict) and str(item.get("sentence") or "").strip()]
    if not found:
        return []
    return ["sentences the evidence does not support: " + "; ".join(
        f"\"{' '.join(str(item['sentence']).split())[:160]}\" ({' '.join(str(item.get('why') or '').split())[:120]})"
        for item in found[:4])]


# ---- the template letter (no AI) -----------------------------------------------------------------

def _sentence(text: str) -> str:
    text = " ".join(str(text or "").split()).rstrip()
    return text if not text or text[-1] in ".!?" else text + "."


def examples(services, job: dict) -> list[dict]:
    """Registered projects (best match first), then registered roles: {id, title, sentences}."""
    registered = {item["id"] for item in services.knowledge() if item["kind"] == "project" and item["review_state"] == "registered"}
    out = [{"id": p["id"], "title": p["title"], "sentences": [_sentence(b) for b in p["bullets"][:2] if str(b).strip()]}
           for p in services.w.rank_projects(job["title"] + " " + job["description"]) if p["id"] in registered]
    words = set(re.findall(r"[a-z][a-z+#.]{2,}", _plain(job["description"])))
    roles = [claim for claim in (services.w.evidence().get("claims") or [])
             if claim.get("category") == "employment" and claim.get("status") not in {"hold", "missing"} and claim.get("approved_facts")]
    roles.sort(key=lambda claim: -len(words & set(re.findall(r"[a-z][a-z+#.]{2,}", _plain(" ".join(map(str, claim["approved_facts"])))))))
    for claim in roles:
        title = " at ".join(str(x) for x in (claim.get("title"), claim.get("employer")) if x)
        out.append({"id": claim["id"], "title": f"my work as {title}" if title else "my work",
                    "sentences": [_sentence(fact) for fact in claim["approved_facts"][:2]]})
    return [example for example in out if example["sentences"]]


def template_body(services, job: dict, matrix: dict) -> tuple[str, list[str]]:
    """A plain letter from registered sentences only, and the evidence ids it uses."""
    found = examples(services, job)
    if not found:
        raise ValueError("A registered project or role with approved wording is required for a cover letter")
    first, second = found[0], (found[1] if len(found) > 1 else None)
    headline = str((services.w.profile().get("narrative") or {}).get("headline") or "").strip().rstrip(".")
    met = [item for item in matrix.get("requirements") or [] if item.get("status") == "met" and item.get("evidence_ids")
           and item.get("category") in {"required", "responsibility"}][:3]
    opening = f"I am applying for the {job['title']} role at {job['company']}."
    if headline:
        opening += f" I am a {headline[0].lower() + headline[1:]}."
    used = []
    if met:
        names = [item["text"] for item in met]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        opening += f" The role asks for {listed}, and the examples below show this work."
        used += [i for item in met for i in item["evidence_ids"]]
    paragraphs = [opening, f"A relevant example is {first['title']}. " + " ".join(first["sentences"])]
    if second:
        paragraphs.append(f"I can also point to {second['title']}. " + second["sentences"][0])
    paragraphs.append(f"I would welcome the chance to discuss how this work could support the {job['title']} team at "
                      f"{job['company']}. Thank you for considering my application.")
    used += [first["id"]] + ([second["id"]] if second else [])
    return "\n\n".join(paragraphs), list(dict.fromkeys(used))


# ---- the letter ----------------------------------------------------------------------------------

def long_date(iso: str) -> str:
    day = date.fromisoformat(iso)
    return f"{day.day} {day:%B %Y}"


def compose(profile: dict, evidence: dict, job: dict, body: str, today: str) -> tuple[str, list[str]]:
    """The full letter (contact block, date, addressee, body, sign-off) and the identity/contact ids it prints."""
    candidate = profile.get("candidate") or {}
    claims = [c for c in evidence.get("claims") or [] if isinstance(c, dict) and c.get("status") not in {"hold", "missing"}]
    name = " ".join(str(candidate.get("full_name") or "").split())
    ids = [c["id"] for c in claims if c.get("category") == "identity"][:1] if name else []
    fields = [k for k in ((profile.get("resume_contract") or {}).get("header_fields") or HEADER_FIELDS)
              if k not in {"full_name", "location", "city", "address"}]
    contact = []
    for key in fields:
        value = " ".join(str(candidate.get(key) or "").split())
        if value:
            contact.append(value)
            ids += [c["id"] for c in claims if c.get("category") == "contact" and " ".join(str(c.get("value") or "").split()) == value][:1]
    lines = ([name] if name else []) + contact + ["", long_date(today), "", "Hiring Team", job["company"], "",
                                                 f"Re: {job['title']}", "", "Dear Hiring Team,", "", body.strip(), "",
                                                 "Kind regards,"] + ([name] if name else [])
    return "\n".join(lines), ids


def writer_team(services):
    """The AI team when a plan is set up on this computer, else None (the template letter)."""
    from backend.ai import any_provider_configured, team_for

    return team_for(services) if any_provider_configured(services.w.root) else None


def generate(services, job_id: str, *, team=None) -> dict:
    """Write, check and save a new version of this job's cover letter."""
    from career import atomic_write

    from backend.ai_marks import clean_text

    job = services.w.get_job(job_id)
    if job.get("deleted_at"):
        raise ValueError("Restore this removed role before generating documents")
    if job.get("record_source") == "gmail" or not str(job.get("description") or "").strip():
        raise ValueError("Add the original posting and full job description before generating a cover letter")
    if services.profile_dirty():
        raise ValueError("Open Profile and confirm the pending entries before generating a cover letter")
    cat = fit.catalogue(services)
    matrix = fit.for_job(services, job_id, use_ai=False)["matrix"]
    facts = company_facts(job["company"])
    profile = services.w.profile()
    filler = tuple(str(f) for f in (profile.get("resume_contract") or {}).get("prohibited_filler") or ())
    method, note, provider, model, cited = "template", "", "", "", []
    body = ""
    if team is not None:
        from backend.ai.persona import persona_for

        request = payload(job, cat, matrix, facts, persona_for(services.w.root))
        first_problems = ""
        for attempt in range(2):
            try:
                raw = team.run("cover_letter_writer", request)
            except Exception as error:  # noqa: BLE001 - no AI answer: the template letter
                note = "The AI could not write the letter (" + " ".join(str(error).split())[:200] + "); this one uses your registered sentences."
                break
            served = getattr(team, "served", None) or ("", "")
            draft = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
            problems = check(draft, job=job, cat=cat, matrix=matrix, facts=facts, filler=filler)
            if not problems:
                try:
                    problems = audit(team, draft, job=job, cat=cat, facts=facts)
                except Exception as error:  # noqa: BLE001 - an unchecked draft is never used
                    note = ("The independent claim check could not run (" + " ".join(str(error).split())[:200]
                            + "); this one uses your registered sentences.")
                    break
            if not problems:
                body, cited, method = draft["body"].strip(), list(dict.fromkeys(map(str, draft["evidence_ids"]))), "ai"
                provider, model = served
                note = ("" if attempt == 0 else "The AI's first draft was set aside (" + first_problems[:400]
                        + "); its corrected draft passed every check.")
                break
            first_problems = "; ".join(problems)
            note = ("The AI's draft was set aside (" + "; ".join(problems)[:400] + "); this one uses your registered sentences.")
            request = {**request, "previous_attempt_problem": "; ".join(problems)}
    if method == "template":
        body, cited = template_body(services, job, matrix)
    letter, header_ids = compose(profile, services.w.evidence(), job, body, services.today())
    letter = clean_text(letter)  # posting text can carry hidden characters (AI marks)
    evidence_ids = list(dict.fromkeys(header_ids + [i for i in cited if not i.startswith("COMPANY-")]))
    used_facts = [fact for fact in facts if fact["id"] in cited]
    revision = services.w.evidence()["candidate_revision"]
    with services.w.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM cover_letters WHERE job_id=?", (job_id,)).fetchone()[0]
        root = services._document_root(job)
        path = root / f"cover-letter-v{version}.md"
        atomic_write(path, letter + "\n")
        metadata = {"job_id": job_id, "company": job["company"], "title": job["title"], "candidate_revision": revision,
                    "method": method, "provider": provider, "model": model, "evidence_ids": evidence_ids,
                    "company_facts": [{"text": f["text"], "quote": f["quote"], "url": f["url"]} for f in used_facts],
                    "note": note, "review_required": True}
        atomic_write(root / f"cover-letter-v{version}.json", json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
        relative = path.relative_to(services.w.root / "data/output").as_posix()
        stamp = services.now()
        db.execute("INSERT INTO cover_letters VALUES(?,?,?,?,?,?)", (job_id, version, letter, relative, stamp, revision))
        services.w.record_event(db, "cover_letter_generated", job_id, company=job["company"], title=job["title"],
                                version=version, path=relative, method=method, review_required=True)
    docx = word_copy(services, job, version, letter)
    services.w.export_tracking()
    services.export_state()
    return {"job_id": job_id, "company": job["company"], "title": job["title"], "version": version, "content": letter,
            "path": relative, "docx_path": docx, "created_at": stamp, "method": method, "provider": provider,
            "model": model, "evidence_ids": evidence_ids, "company_facts": metadata["company_facts"], "note": note,
            "review_required": True}


def word_copy(services, job: dict, version: int, letter: str) -> str:
    """Write the .docx beside the letter (when Word copies are on); its path under data/output, or ""."""
    from backend import features

    if not features.enabled("docx_export", services):
        return ""
    from backend.services.docx_export import letter_document

    path = services._document_root(job) / f"cover-letter-v{version}.docx"
    name = " ".join(str((services.w.profile().get("candidate") or {}).get("full_name") or "").split())
    path.write_bytes(letter_document(letter, title=f"Cover letter for {job['title']} at {job['company']}", author=name))
    return path.relative_to(services.w.root / "data/output").as_posix()


def latest(services, job_id: str) -> dict | None:
    with services.w.connect() as db:
        row = db.execute("SELECT * FROM cover_letters WHERE job_id=? ORDER BY version DESC LIMIT 1", (job_id,)).fetchone()
    return dict(row) if row else None


def download(services, job_id: str, format: str):
    """(path, file name) of the latest letter as .md or .docx (the Word copy is made when missing)."""
    from career import safe_child

    if format not in {"md", "docx"}:
        raise ValueError("Choose md or docx")
    row = latest(services, job_id)
    if not row:
        raise ValueError("Generate a cover letter for this job first")
    job = services.w.get_job(job_id)
    path = safe_child(services.w.root / "data/output", row["path"])
    if format == "docx":
        from backend import features

        if not features.enabled("docx_export", services):
            raise ValueError("Word downloads are switched off on this computer (CAREER_FEATURES).")
        path = path.with_suffix(".docx")
        if not path.is_file():
            word_copy(services, job, row["version"], row["content"])
    slug = re.sub(r"[^a-z0-9]+", "-", (job["company"] + "-" + job["title"]).casefold()).strip("-")
    return path, f"{slug}-cover-letter-v{row['version']}.{format}"
