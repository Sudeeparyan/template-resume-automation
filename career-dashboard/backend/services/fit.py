"""The requirement matrix: what a job asks for, and which of the candidate's registered evidence shows it.

One matrix per job, reused everywhere: discovery's fit score and gate, the tailor's plan
(services/resume_studio.py), resume coverage (backend/assessment.py), the study plan and
the chat's snapshot. The AI proposes and this module verifies:

* the ``fit_analyst`` specialist reads the posting against the candidate's evidence catalogue,
  using free plans only (Kimi, Codex, Claude; never a paid key);
* ``verify`` keeps only requirements and blockers whose excerpt is really in the posting,
  only evidence ids that exist in the candidate's registry, and never lets a never-claim skill count;
* ``score`` turns the verified matrix into the 0-100 fit, deterministically.

Without a free plan the rules path extracts requirements from the posting's own words
(``assessment.extract_requirements`` with the candidate's skill vocabulary added) and matches
them against the same catalogue, so a job is always scored, just less precisely.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from backend.assessment import _contains, _sentences, extract_requirements

FIT_VERSION = "fit-v3"
# A job is worth the candidate's time at this score, with at least half its must-haves met.
FIT_THRESHOLD = 65
MUST_HAVE_FLOOR = 0.5
MAX_REQUIREMENTS = 25
WEIGHTS = {"required": 55, "preferred": 10, "responsibility": 10}
COVERAGE_POINTS = 75
ROLE_POINTS = 15
LOCATION_POINTS = 10
# Each category's ratio starts from this many imaginary half-met requirements, so a score read
# from two requirements counts as less certain than one read from twelve: a posting whose only
# recognised must-haves are SQL and CSS no longer outranks a data role meeting 12 of 13.
PRIOR_ITEMS = 2
# Two AI calls at once at most: a shortlist is checked in parallel without crowding a plan.
PARALLEL = 2
# Evidence kinds that can prove a requirement; notes only limit what may be claimed.
PROVING = {"skill", "project", "experience", "education", "language"}
# The sponsorship gate owns work-permit decisions; an AI "blocker" about them is dropped.
PERMIT_WORDS = re.compile(
    r"sponsor|visa|work (?:authori[sz]ation|permit)|authori[sz]ed to work|right to work|green card|"
    r"permanent resident|employment eligibility|eligible to work", re.I)


# ----- the evidence catalogue ------------------------------------------------------------

def _facts(record: dict) -> list[str]:
    return [" ".join(str(f).split()) for f in record.get("approved_facts") or [] if str(f).strip()]


def _degree_only(claim: dict) -> str:
    """The degree an ``education_history`` claim records, without its coursework or notes.

    Older builds wrote only ``value`` ("degree; field; grade; dates; modules; notes"); the degree
    comes first and the field (when there is one) second. Modules never count as the degree.
    """
    if claim.get("degree_as_supplied"):
        return str(claim["degree_as_supplied"])
    parts = [p.strip() for p in str(claim.get("value") or "").split(";") if p.strip()]
    head = parts[:1] or [str(claim.get("title") or "")]
    if len(parts) > 1 and "," not in parts[1] and len(parts[1]) <= 80:
        head.append(parts[1])
    return "; ".join(head)


def catalogue(services, profile_text: str = "") -> dict:
    """Registered evidence as the analyst sees it: ``{"entries", "never", "hash"}``.

    Each entry is ``{id, kind, text, terms, name}``: ``terms`` are the tool and skill names
    it holds (for matching and coverage aliases), ``name`` a title that appears on a resume.
    """
    evidence = services.w.evidence() or {}
    entries: list[dict] = []
    never: list[str] = []
    seen: set[str] = set()

    def add(entry):
        if entry["id"] and entry["id"] not in seen and entry["text"].strip():
            seen.add(entry["id"])
            entries.append(entry)

    def from_claim(claim: dict):
        cid, category = str(claim.get("id") or ""), str(claim.get("category") or "")
        if cid == "SKILL-NEVER-001":
            never.extend(_facts(claim))
            return
        if claim.get("status") in {"hold", "missing"}:
            return
        facts = _facts(claim)
        if "skill" in category or cid.startswith("SKILL"):
            add({"id": cid, "kind": "skill", "text": ", ".join(facts), "terms": facts, "name": ""})
        elif category == "employment":
            head = " at ".join(x for x in (claim.get("title"), claim.get("employer")) if x)
            dates = f" ({claim['dates']})" if claim.get("dates") else ""
            add({"id": cid, "kind": "experience", "text": (head + dates + ": " + " ".join(facts))[:900],
                 "terms": [], "name": str(claim.get("employer") or "")})
        elif category == "education":
            degree = claim.get("degree_as_supplied") or claim.get("degree") or claim.get("value") or ""
            where = ", ".join(x for x in (claim.get("institution"), claim.get("status_text") or claim.get("dates")) if x)
            add({"id": cid, "kind": "education", "text": " ".join(f"{degree} {where}".split()), "terms": [],
                 "name": str(claim.get("institution") or "")})
        elif category == "education_history":
            # A degree the build could not tie to an institution: never printed on a resume, but still
            # the candidate's own reported degree, so it meets a posting's "bachelor's degree" (26 Sep:
            # without it every degree requirement read as missing and each job fell below the bar).
            text = " ".join(f"{_degree_only(claim)} {claim.get('dates') or ''}".split())
            add({"id": cid, "kind": "education", "text": text, "terms": [], "name": ""})
        elif category == "academic_coursework":
            add({"id": cid, "kind": "coursework", "text": ", ".join(facts), "terms": facts, "name": ""})
        elif category == "languages":
            add({"id": cid, "kind": "language", "text": str(claim.get("value") or ", ".join(facts)), "terms": [], "name": ""})
        elif category == "other":
            add({"id": cid, "kind": "note", "text": " ".join(str(claim.get("value") or claim.get("title") or "").split())[:400],
                 "terms": [], "name": ""})

    for claim in evidence.get("claims") or []:
        if isinstance(claim, dict):
            from_claim(claim)
    not_ready = {str(p.get("id")) for p in evidence.get("not_resume_ready") or [] if isinstance(p, dict)}
    for project in evidence.get("projects") or []:
        if not isinstance(project, dict) or project.get("status") in {"hold", "missing"} or project.get("id") in not_ready:
            continue
        content = project.get("resume_content") or {}
        name = content.get("title") or project.get("external_name") or project.get("canonical_name") or ""
        tech = [str(t) for t in project.get("technologies") or []]
        facts = _facts(project) or [str(b) for b in content.get("bullets") or []]
        add({"id": str(project.get("id")), "kind": "project",
             "text": (f"{name} ({', '.join(tech)}): " + " ".join(facts))[:900], "terms": tech, "name": name})
    # Entries registered on the Profile tab that the registry file does not hold yet.
    kinds = {"skill": "skill", "project": "project", "experience": "experience", "education": "education"}
    for item in services.profile_context():
        if item.get("kind") in kinds and item.get("review_state") == "registered" and item["id"] not in seen:
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            if details.get("id") == "SKILL-NEVER-001" or item["id"] == "SKILL-NEVER-001":
                continue
            if details.get("status") in {"hold", "missing"}:
                continue
            facts = _facts(details) or [item.get("title") or ""]
            kind = kinds[item["kind"]]
            text = ", ".join(facts) if kind == "skill" else f"{item.get('title', '')}: {item.get('summary', '')}"
            add({"id": item["id"], "kind": kind, "text": text[:900], "terms": facts if kind == "skill" else [],
                 "name": item.get("title") or ""})
    if profile_text.strip():
        add({"id": "PROFILE-SUMMARY", "kind": "skill", "text": profile_text[:4000], "terms": [], "name": ""})
    body = json.dumps({"entries": entries, "never": never}, sort_keys=True, ensure_ascii=False)
    return {"entries": entries, "never": never, "hash": hashlib.sha256(body.encode()).hexdigest()[:16]}


# ----- the rules path ----------------------------------------------------------------------

def aliases(term: str) -> tuple[str, ...]:
    """The lower-case ways a posting or a resume may write one tool name ("Apache Kafka" -> "kafka")."""
    low = " ".join(term.casefold().split())
    base = re.sub(r"\s*\([^)]*\)", "", low).strip()
    out = {base, *re.findall(r"\(([^)]+)\)", low)}
    for prefix in ("apache ", "amazon ", "aws ", "microsoft ", "google "):
        if base.startswith(prefix) and len(base) > len(prefix) + 2:
            out.add(base[len(prefix):])
    if "/" in base:
        out.update(part.strip() for part in base.split("/"))
    out.add(base.replace("-", " "))
    # "Go", "R": two letters and no symbol match ordinary words, so they are never aliases on their own.
    return tuple(sorted(a for a in out if len(a) > 2 or re.search(r"[^a-z0-9 ]", a)))


def vocabulary(cat: dict) -> dict[str, tuple[str, ...]]:
    """The candidate's tool names and never-claim list, as extra requirement terms."""
    vocab: dict[str, tuple[str, ...]] = {}
    for term in [t for e in cat["entries"] for t in e["terms"]] + list(cat["never"]):
        written = aliases(term)
        if written:
            vocab.setdefault(term, written)
    return vocab


def named_terms(terms, text: str) -> list[str]:
    """The terms that ``text`` actually names."""
    return [t for t in terms if _contains(text, aliases(t))]


# A posting asks for a field ("ML", "AI"); candidate evidence names its tools and methods.
# The rules path counts those as proof of the field, as the AI path does when it reads them.
_ML_WORK = ("machine learning", "deep learning", "pytorch", "tensorflow", "keras", "scikit-learn",
            "computer vision", "neural network", "lstm", "xgboost", "object detection")
_AI_WORK = _ML_WORK + ("artificial intelligence", "llm", "large language model", "generative ai", "rag", "nlp")
FIELD_EVIDENCE = {
    "ml": _ML_WORK,
    "machine learning": _ML_WORK,
    "ai": _AI_WORK,
    "artificial intelligence": _AI_WORK,
    "deep learning": ("deep learning", "pytorch", "tensorflow", "keras", "neural network", "lstm", "cnn", "transformer"),
}


def _rules(jd: str, cat: dict) -> dict:
    never = {t.casefold() for t in cat["never"]}
    requirements = []
    for item in extract_requirements(jd, vocabulary(cat)):
        words = tuple(item.get("aliases") or ())
        if not words:
            # A sentence with no tool name: it cannot be matched by rules, so it is not scored.
            requirements.append({"text": item["requirement"][:120], "category": item["category"], "excerpt": item["excerpt"],
                                 "status": "unknown", "evidence_ids": [], "note": ""})
            continue
        if item["requirement"].casefold() in never:
            status, ids = "missing", []
        else:
            proof = words + FIELD_EVIDENCE.get(item["requirement"].casefold(), ())
            proving = [e["id"] for e in cat["entries"] if e["kind"] in PROVING and _contains(e["text"], proof)]
            studied = [e["id"] for e in cat["entries"] if e["kind"] == "coursework" and _contains(e["text"], words)]
            status, ids = ("met", proving) if proving else ("partial", studied) if studied else ("missing", [])
        requirements.append({"text": item["requirement"], "category": item["category"], "excerpt": item["excerpt"],
                             "status": status, "evidence_ids": ids[:4], "note": ""})
    return {"requirements": requirements[:40], "hard_blockers": [], "summary": ""}


# ----- verification ------------------------------------------------------------------------

def _squash(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"',
                                         "–": "-", "—": "-", "•": " ", " ": " "}))
    return " ".join(text.split()).casefold()


def _grounded(excerpt: str, jd: str, jd_squashed: str) -> str | None:
    """The excerpt as the posting writes it, or None when the posting does not say it."""
    excerpt = (excerpt or "").strip()
    if len(excerpt) < 3:
        return None
    if excerpt in jd:
        return excerpt
    wanted = _squash(excerpt)
    if wanted not in jd_squashed:
        return None
    # Typography or spacing differed: quote the posting's own sentence instead.
    for sentence in _sentences(jd):
        if wanted in _squash(sentence):
            return sentence
    return excerpt


def _names_never(text: str, never: set[str]) -> bool:
    low = text.casefold()
    return any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", low) for term in never if len(term) > 2)


def verify(raw: dict, jd: str, cat: dict) -> dict:
    """The AI's matrix with everything it cannot back up removed or downgraded."""
    jd_squashed = _squash(jd)
    known = {e["id"]: e for e in cat["entries"]}
    never = {t.casefold() for t in cat["never"]}
    requirements, dropped, downgraded, seen = [], 0, 0, set()
    for item in raw.get("requirements") or []:
        excerpt = _grounded(str(item.get("excerpt") or ""), jd, jd_squashed)
        category = item.get("category") if item.get("category") in WEIGHTS else None
        if not excerpt or not category:
            dropped += 1
            continue
        text = " ".join(str(item.get("text") or "").split())[:120] or excerpt[:120]
        key = (category, text.casefold())
        if key in seen:
            continue
        seen.add(key)
        status = item.get("status") if item.get("status") in {"met", "partial", "missing"} else "missing"
        ids = [i for i in dict.fromkeys(item.get("evidence_ids") or []) if i in known and known[i]["kind"] != "note"]
        claimed = status
        if status in {"met", "partial"} and not ids:
            status = "missing"
        if status == "met" and all(known[i]["kind"] == "coursework" for i in ids):
            status = "partial"
        note = " ".join(str(item.get("note") or "").split())
        if _names_never(re.sub(r"\([^)]*\)", " ", text), never):
            status = "missing"  # the requirement itself is a skill the candidate must never claim
        elif status == "met" and _names_never(text, never):
            # Only among the examples ("web development (Python, SQL, React)"): the evidence shows the topic
            # without that tool, so at most partial, and the never-claim tool is never counted.
            status = "partial"
            note = (note + " Partial: the examples include a tool on the never-claim list.").strip()
        if status == "missing":
            ids = []
        if status != claimed:
            downgraded += 1
        requirements.append({"text": text, "category": category, "excerpt": excerpt, "status": status,
                             "evidence_ids": ids[:4], "note": note[:200]})
        if len(requirements) >= MAX_REQUIREMENTS:
            break
    blockers = []
    for item in raw.get("hard_blockers") or []:
        excerpt = _grounded(str(item.get("excerpt") or ""), jd, jd_squashed)
        reason = " ".join(str(item.get("reason") or "").split())[:160]
        if excerpt and not PERMIT_WORDS.search(excerpt + " " + reason):
            blockers.append({"excerpt": excerpt, "reason": reason or "stated requirement not supported by registered evidence"})
    return {"requirements": requirements, "hard_blockers": blockers,
            "summary": " ".join(str(raw.get("summary") or "").split())[:300],
            "checks": {"dropped_ungrounded": dropped, "downgraded": downgraded}}


# ----- scoring -----------------------------------------------------------------------------

def score(matrix: dict, posting: dict, root) -> dict:
    """The 0-100 fit from a verified matrix, plus the parts it was made of."""
    from backend.countries import pack_for
    from backend.job_quality import SENIORITY_BLOCK, ProfileRules

    title, location = str(posting.get("title") or ""), str(posting.get("location") or "")
    rules = ProfileRules.of(root)
    role_ok = bool(rules.roles.search(title)) and (not rules.block_seniority or not SENIORITY_BLOCK.search(title))
    from backend.countries import market_for_location
    market = market_for_location(root, location, requested=posting.get("market"))
    place_ok = pack_for(root, market).location_ok(location)
    parts = {}
    for category in WEIGHTS:
        items = [r for r in matrix["requirements"] if r["category"] == category and r["status"] != "unknown"]
        met = sum(r["status"] == "met" for r in items)
        partial = sum(r["status"] == "partial" for r in items)
        parts[category] = {"total": len(items), "met": met, "partial": partial,
                           "ratio": (met + 0.5 * partial) / len(items) if items else None,
                           "weighed": (met + 0.5 * partial + 0.5 * PRIOR_ITEMS) / (len(items) + PRIOR_ITEMS)}
    present = [c for c in WEIGHTS if parts[c]["ratio"] is not None]
    coverage = (sum(WEIGHTS[c] * parts[c]["weighed"] for c in present) / sum(WEIGHTS[c] for c in present)) if present else 0.5
    requirement_points = round(COVERAGE_POINTS * coverage)
    must = parts["required"]
    return {
        "score": requirement_points + (ROLE_POINTS if role_ok else 0) + (LOCATION_POINTS if place_ok else 0),
        "components": {"requirements": requirement_points, "role_seniority": ROLE_POINTS if role_ok else 0,
                       "location": LOCATION_POINTS if place_ok else 0},
        "parts": parts,
        "must_have_ok": must["total"] == 0 or must["ratio"] >= MUST_HAVE_FLOOR,
    }


def rationale(analysis: dict) -> str:
    """One plain paragraph: supported, partial and missing requirements, with the check method."""
    reqs = analysis["matrix"]["requirements"]
    must = [r for r in reqs if r["category"] == "required" and r["status"] != "unknown"]
    met = [r["text"] for r in must if r["status"] == "met"]
    partial = [r["text"] for r in must if r["status"] == "partial"]
    missing = [r["text"] for r in must if r["status"] == "missing"]
    lines = [f"Fit {analysis['score']}/100."]
    if must:
        lines.append(f"Meets {len(met)} of {len(must)} must-haves" + (f" ({', '.join(met[:5])}{'…' if len(met) > 5 else ''})" if met else "") + ".")
        if partial:
            lines.append("Partly: " + ", ".join(partial[:4]) + ".")
        if missing:
            lines.append("Missing: " + ", ".join(missing[:5]) + ".")
    for blocker in analysis["matrix"]["hard_blockers"]:
        lines.append(f"Blocker: {blocker['reason']} (“{blocker['excerpt'][:140]}”).")
    how = (f"Checked by AI ({analysis['provider_label']}) against your registered evidence."
           if analysis["method"] == "ai"
           else "Checked by rules against your registered skills; an AI check on a free plan replaces it when one is free.")
    return " ".join(lines + [how])


def brief(analysis: dict | None) -> str:
    """"meets 7/9 must-haves; missing: Kubernetes" for the chat's snapshot."""
    if not analysis:
        return ""
    must = [r for r in analysis["matrix"]["requirements"] if r["category"] == "required" and r["status"] != "unknown"]
    if not must:
        return f"fit {analysis['score']}/100"
    met = sum(r["status"] == "met" for r in must)
    missing = [r["text"] for r in must if r["status"] == "missing"]
    return f"meets {met}/{len(must)} must-haves" + (f"; missing: {', '.join(missing[:3])}" if missing else "")


# ----- the AI path ---------------------------------------------------------------------------

def fit_team(services):
    """An AgentTeam that routes over the candidate's free plans, when one is available."""
    from backend.ai import ready_providers, route_options, router, usage_recorder
    from backend.ai.agents.graph import AgentTeam
    from backend.ai.persona import persona_for

    root = services.w.root
    options = route_options(services, "fit_check")
    policy = router.free_only(options.get("policy"))
    ready = ready_providers(root)
    if not router.available(root, policy, "cheap", ready_map=ready):
        return None
    return AgentTeam(root, {"strong": (router.ID, router.ID), "cheap": (router.ID, router.ID)},
                     usage_recorder(services), persona=persona_for(root),
                     route={**options, "policy": policy, "ready": ready})


def _payload(posting: dict, cat: dict) -> dict:
    return {
        "job": {k: str(posting.get(k) or "")[:16000 if k == "description" else 300]
                for k in ("title", "company", "location", "description")},
        "evidence": [{"id": e["id"], "kind": e["kind"], "text": e["text"]} for e in cat["entries"]],
        "never_claim": cat["never"],
    }


def analyse(services, posting: dict, *, team=None, cat: dict | None = None, profile_text: str = "") -> dict:
    """The verified matrix and fit for one posting (saved or not), by AI when a team is given."""
    from backend.ai import router

    cat = cat or catalogue(services, profile_text)
    jd = str(posting.get("description") or "")
    method, provider, model, error, matrix = "rules", "", "", "", None
    if team is not None and jd.strip():
        try:
            raw = team.run("fit_analyst", _payload(posting, cat))
            matrix = verify(raw.model_dump() if hasattr(raw, "model_dump") else dict(raw), jd, cat)
            method = "ai"
            provider, model = team.served or ("", "")
            if not matrix["requirements"]:
                # Nothing it said could be found in the posting: trust the posting's own words instead.
                method, matrix, error = "rules", None, "the AI's requirements were not in the posting"
        except Exception as exc:  # noqa: BLE001 - any AI failure falls back to the rules path
            error = " ".join(str(exc).split())[:300]
    if matrix is None:
        matrix = _rules(jd, cat)
    scored = score(matrix, posting, services.w.root)
    analysis = {
        "matrix": matrix, "method": method, "provider": provider, "model": model,
        "provider_label": router.label(provider) if provider else "",
        "fit_version": FIT_VERSION, "jd_hash": jd_hash(jd), "evidence_hash": cat["hash"],
        "ai_error": error, **scored,
    }
    analysis["rationale"] = rationale(analysis)
    return analysis


def analyse_many(services, postings: list[dict], *, team=None) -> list[dict]:
    """``analyse`` for a shortlist, at most PARALLEL AI calls at a time, in the same order."""
    from concurrent.futures import ThreadPoolExecutor

    from backend.ai.agents.graph import AgentTeam

    cat = catalogue(services)
    if team is None or len(postings) < 2:
        return [analyse(services, p, team=team, cat=cat) for p in postings]
    # One team per call: AgentTeam keeps per-call state (served, fell_back).
    teams = [AgentTeam(team.root, team.tiers, team.on_usage, persona=team.persona, route=team.route) for _ in postings]
    with ThreadPoolExecutor(max_workers=PARALLEL, thread_name_prefix="fit") as pool:
        return list(pool.map(lambda pair: analyse(services, pair[0], team=pair[1], cat=cat), zip(postings, teams)))


# ----- the cache ----------------------------------------------------------------------------

def jd_hash(jd: str) -> str:
    return hashlib.sha256((jd or "").encode()).hexdigest()[:16]


def save(services, job_id: str, analysis: dict) -> None:
    """Keep the matrix for this job and show its fit on the job itself."""
    with services.w.connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO job_fit(job_id,jd_hash,evidence_hash,fit_version,method,provider,model,matrix,score,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, analysis["jd_hash"], analysis["evidence_hash"], analysis["fit_version"], analysis["method"],
             analysis["provider"], analysis["model"], json.dumps(analysis["matrix"], ensure_ascii=False),
             analysis["score"], services.now()))
        db.execute("UPDATE jobs SET fit_score=?, fit_rationale=? WHERE id=?", (analysis["score"], analysis["rationale"], job_id))


def cached(services, job_id: str, *, cat: dict | None = None) -> dict | None:
    """The saved matrix for this job while the posting, candidate evidence and rules are unchanged."""
    job = services.w.get_job(job_id)
    with services.w.connect() as db:
        row = db.execute("SELECT * FROM job_fit WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return None
    cat = cat or catalogue(services)
    if (row["jd_hash"], row["evidence_hash"], row["fit_version"]) != (jd_hash(job.get("description") or ""), cat["hash"], FIT_VERSION):
        return None
    from backend.ai import router

    matrix = json.loads(row["matrix"])
    analysis = {"matrix": matrix, "method": row["method"], "provider": row["provider"], "model": row["model"],
                "provider_label": router.label(row["provider"]) if row["provider"] else "",
                "fit_version": row["fit_version"], "jd_hash": row["jd_hash"], "evidence_hash": row["evidence_hash"],
                "ai_error": "", "checked_at": row["created_at"], **score(matrix, job, services.w.root)}
    analysis["rationale"] = rationale(analysis)
    return analysis


def for_job(services, job_id: str, *, team=None, use_ai: bool = True, refresh: bool = False) -> dict:
    """This job's matrix: the saved one when current, else a new check (AI on a free plan when one is free)."""
    cat = catalogue(services)
    current = None if refresh else cached(services, job_id, cat=cat)
    if current and (current["method"] == "ai" or not use_ai):
        return current
    if use_ai and team is None:
        team = fit_team(services)
    if current and team is None:
        return current  # the rules check is as good as it gets until a free plan is back
    analysis = analyse(services, services.w.get_job(job_id), team=team, cat=cat)
    save(services, job_id, analysis)
    return analysis


def backfill(services) -> int:
    """Bring every saved job's shown fit up to date, by rules only (no AI call).

    A job saved before the requirement check, or checked under older rules, gets the rules
    check; one whose saved check is current but whose shown score came from an older formula
    gets the new score. A free plan upgrades a job to an AI check when it is tailored or
    checked again. Returns how many jobs changed.
    """
    cat = catalogue(services)
    changed = 0
    for job in services.w.jobs():
        if not str(job.get("description") or "").strip():
            continue
        current = cached(services, job["id"], cat=cat)
        if current is None:
            save(services, job["id"], analyse(services, job, cat=cat))
        elif job.get("fit_score") != current["score"]:
            with services.w.connect() as db:
                db.execute("UPDATE jobs SET fit_score=?, fit_rationale=? WHERE id=?",
                           (current["score"], current["rationale"], job["id"]))
        else:
            continue
        changed += 1
    return changed


def coverage_requirements(analysis: dict, cat: dict) -> list[dict]:
    """The matrix as resume-coverage requirements: each one with the words that show it on a page."""
    known = {e["id"]: e for e in cat["entries"]}
    out = []
    for item in analysis["matrix"]["requirements"]:
        if item["status"] == "unknown":
            continue
        words = [item["text"]]
        for evidence_id in item["evidence_ids"]:
            entry = known.get(evidence_id) or {}
            words += named_terms(entry.get("terms", []), item["excerpt"] + " " + item["text"]) or entry.get("terms", [])
            if entry.get("kind") in {"project", "experience", "education"} and entry.get("name"):
                words.append(entry["name"])
        out.append({"category": item["category"], "requirement": item["text"], "excerpt": item["excerpt"],
                    "aliases": list(dict.fromkeys(w.casefold() for w in words if w and len(w) > 1)),
                    "fit_status": item["status"]})
    return out


def gaps(analysis: dict | None) -> list[str]:
    """The genuine gaps: must-haves (then nice-to-haves) missing from candidate evidence."""
    if not analysis:
        return []
    reqs = analysis["matrix"]["requirements"]
    return ([r["text"] for r in reqs if r["category"] == "required" and r["status"] == "missing"]
            + [r["text"] for r in reqs if r["category"] == "preferred" and r["status"] == "missing"])
