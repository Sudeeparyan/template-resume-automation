"""The last step for every job: is its resume ready to submit, and what is left if not?

Daily Search, the overnight hunt and Resume Studio ask the same question and get the same
answer. The check reads what the earlier steps produced and adds nothing of its own (no AI,
no web):

* the posting was re-read recently and is still open, and the work-permit gate still passes;
* the employer passed the legitimacy check;
* the requirement check (services/fit.py) is current and meets the fit bar;
* the current PDF was built from the current draft, fills the market's page contract and has
  its deterministic assessment (coverage of the posting's requirements, ATS readability);
* an independent review (a resume_match run that sees only the PDF text and the posting)
  read this exact PDF against this exact posting;
* the candidate kept or removed every item the tailor predicted (Assurance);
* the profile has no unreconciled edits.

The verdict is "ready", "review" (built, but the candidate must act first) or "blocked" (it
cannot be sent as it is). The readiness score combines the measured parts (fit, coverage,
readability) with fixed weights. It is not a prediction of an interview or a shortlist:
nothing the app holds can measure that, so nothing here claims it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from backend.services.demo import demo_mode

# How the readiness score weighs its measured parts (each 0-100).
WEIGHTS = {"fit": 50, "coverage": 35, "ats": 15}
# A posting checked longer ago than this is re-checked before it counts as open.
POSTING_FRESH_HOURS = 48
LABELS = {"ready": "Ready to submit", "review": "Needs your review", "blocked": "Not ready"}
NOTE = ("A readiness score, not a prediction of an interview: it measures fit, coverage and readability. "
        "Nothing is submitted; you read the resume and apply yourself.")


def _when(stamp) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _independent_review(services, job_id: str, pdf_sha256: str | None, jd_sha256: str) -> tuple[str, str]:
    """The latest verdict for this exact PDF/posting, or "running"/"missing".

    A completed run proves the check happened, not that its findings passed.
    Older prose-only reviews need a new structured check before release.
    """
    with services.w.connect() as db:
        rows = db.execute("SELECT state, result FROM agent_runs WHERE kind='resume_match' AND job_id=? "
                          "ORDER BY created_at DESC, rowid DESC LIMIT 10", (job_id,)).fetchall()
    for row in rows:
        if row["state"] in ("queued", "running"):
            return "running", ""
        if row["state"] != "completed" or not pdf_sha256:
            continue
        try:
            result = json.loads(row["result"] or "{}")
        except ValueError:
            continue
        if not isinstance(result, dict):
            continue
        if result.get("pdf_sha256") == pdf_sha256 and result.get("jd_sha256") == jd_sha256:
            review = result.get("review")
            if not isinstance(review, dict):
                return "review", "The saved review has no usable verdict. Run the independent review again."
            summary = str(review.get("summary") or "")
            verdict, issues = review.get("verdict"), review.get("issues")
            if (not isinstance(verdict, str) or verdict not in {"pass", "review", "blocked"} or not isinstance(issues, list)
                    or any(not isinstance(issue, str) or not issue.strip() for issue in issues)
                    or verdict != "pass" and not issues):
                return "review", "The saved review has no usable verdict. Run the independent review again."
            issues = [issue.strip() for issue in issues if issue.strip()]
            if verdict == "pass" and issues:
                verdict = "review"
            details = "; ".join([summary, *issues[:3]]).strip("; ")
            return verdict, details
    return "missing", ""


def check(services, studio, job_id: str, *, fit_bar: int | None = None) -> dict:
    """The verdict, readiness score and each check for one saved job."""
    from backend.services import fit, sponsorship

    job = services.w.get_job(job_id)
    demo = demo_mode(services)
    checks: list[dict] = []

    def add(key, label, state, note):
        checks.append({"id": key, "label": label, "state": state, "note": note})

    # 1. The posting itself.
    posting = job.get("posting_state")
    checked = _when(job.get("last_verified_at"))
    if posting == "expired":
        add("posting", "Posting", "fail", "The posting is closed, so this application cannot be sent.")
    elif posting == "active" and checked and datetime.now(timezone.utc) - checked <= timedelta(hours=POSTING_FRESH_HOURS):
        add("posting", "Posting", "pass", "Still open when it was last checked.")
    elif posting == "active":
        add("posting", "Posting", "warn", "Last checked more than two days ago; check it is still open before applying.")
    elif posting == "needs_review":
        add("posting", "Posting", "warn", "The site blocked the automatic check; open the link to confirm it is still open.")
    else:
        add("posting", "Posting", "warn", "Not checked yet; check the posting is still open before applying.")

    # 2. Permission to work: the gate on the saved posting text.
    try:
        verdict = services.gate(job["company"], job.get("description") or "", job.get("url") or "",
                                job.get("location") or "", market=job.get("market") or "")
        if verdict.excluded and not sponsorship.overridden(job.get("sponsor_evidence"), verdict):
            add("permit", "Work permit", "fail", f"{verdict.screen.reason_label}: \"{verdict.screen.sentence}\"")
        else:
            evidence = job.get("sponsor_evidence") if isinstance(job.get("sponsor_evidence"), dict) else {}
            sentence = evidence.get("sentence")
            add("permit", "Work permit", "pass",
                f"Posting says: \"{sentence}\"" if sentence else "The posting does not refuse your work permit.")
    except ValueError as error:
        add("permit", "Work permit", "warn", str(error))

    # 2b. Pay: a market estimate or researched figure is not this vacancy's salary.
    from backend.services.opportunities import pay_note

    note = pay_note(job)
    if note:
        add("pay", "Salary", "warn", note)
    elif (job.get("opportunity") or {}).get("section") == "salary_matches":
        add("pay", "Salary", "pass", "The advertised salary meets your floor.")

    # 3. The employer.
    if job.get("legitimacy_state") == "verified":
        add("employer", "Employer", "pass", "Employer passed the legitimacy check.")
    else:
        add("employer", "Employer", "warn", "The employer check is still pending.")

    # 4. Fit with the role, from the verified requirement matrix.
    bar = fit_bar or fit.FIT_THRESHOLD
    analysis = None
    try:
        analysis = fit.cached(services, job_id)
    except Exception:  # noqa: BLE001 - a stale or missing matrix reads as "not checked"
        analysis = None
    fit_score = analysis["score"] if analysis else job.get("fit_score")
    if analysis and analysis["matrix"].get("hard_blockers"):
        reason = analysis["matrix"]["hard_blockers"][0].get("reason") or "a hard requirement"
        add("fit", "Fit with the role", "fail", f"The posting has a requirement your evidence cannot meet: {reason}.")
    elif not analysis:
        add("fit", "Fit with the role", "warn", "The requirement check is missing or out of date.")
    elif fit_score < bar or not analysis["must_have_ok"]:
        must = analysis["parts"]["required"]
        add("fit", "Fit with the role", "warn",
            f"Fit {fit_score}/100 meets {must['met']} of {must['total']} must-haves, below the bar of {bar}.")
    else:
        add("fit", "Fit with the role", "pass", f"Fit {fit_score}/100. {fit.brief(analysis)}")

    # 5. The resume PDF and its assessment.
    draft = None
    try:
        draft = studio.get(job_id)
    except ValueError:
        add("pdf", "Resume PDF", "fail", "No tailored resume yet.")
    pdf_sha256, match = None, None
    if draft is not None:
        preview = draft.get("preview") or {}
        contract = studio.contract(job_id)
        shape = contract.describe_pages()
        from career import safe_child

        pdf = None
        try:
            if preview.get("path"):
                pdf = safe_child(services.w.root / "data/output", str(preview["path"]) + "/resume.pdf")
        except ValueError:
            pass
        if pdf is None or not pdf.is_file():
            add("pdf", "Resume PDF", "fail", "The tailored PDF is missing. Build it in Resume Studio.")
        elif not preview.get("current") or preview.get("revision") != draft["revision"]:
            add("pdf", "Resume PDF", "fail", "The PDF is not built from the current draft. Build it in Resume Studio.")
        elif preview.get("page_count") != contract.pages:
            add("pdf", "Resume PDF", "fail", f"The PDF is {preview.get('page_count')} pages; the contract is {shape}.")
        elif not ((preview.get("layout") or {}).get("full_pages") or contract.relaxed_min_words):
            add("pdf", "Resume PDF", "warn", f"The PDF does not fill {shape}. Use Fit to {shape} in Resume Studio.")
        else:
            add("pdf", "Resume PDF", "pass", f"Built from the current draft and fits {shape}.")
        if demo:
            add("evidence", "Profile evidence", "pass", "demo mode: skipped")
        elif draft.get("profile_revision") != services.w.evidence().get("candidate_revision"):
            add("evidence", "Profile evidence", "warn", "This draft was made from an older profile; sync it with the current one.")
        # A degree with no university recorded is never printed (services/intake/build.py), so the
        # resume would go out with no Education section at all.
        claims = [c for c in services.w.evidence().get("claims") or [] if isinstance(c, dict)]
        if any(c.get("category") == "education_history" for c in claims) and not any(
                c.get("category") == "education" and c.get("status") not in {"hold", "missing"} for c in claims):
            add("education", "Education", "warn", "Your degree has no university recorded, so the resume has no "
                                                  "Education section. Add the university in Profile, then rebuild.")
        evidence_problem = getattr(studio, "resume_evidence_problem", lambda *_: "")(job_id, draft.get("source") or "")
        if evidence_problem:
            add("claims", "Resume claims", "fail", evidence_problem)
        if pdf is not None and pdf.is_file():
            pdf_sha256 = hashlib.sha256(pdf.read_bytes()).hexdigest()
        match = draft.get("match")
        if match and match.get("current"):
            add("assessment", "Coverage and readability", "pass",
                f"Covers {match.get('resume_coverage', {}).get('score', match.get('score'))}/100 of the posting's "
                f"requirements; readability {match.get('ats_readiness', {}).get('score')}/100.")
        else:
            match = None
            add("assessment", "Coverage and readability", "warn", "The current PDF has not been assessed yet.")

    # 6. The independent review of this exact PDF.
    jd_sha256 = hashlib.sha256((job.get("description") or "").encode()).hexdigest()
    review_state, review_summary = _independent_review(services, job_id, pdf_sha256, jd_sha256)
    if review_state == "pass":
        add("review", "Independent review", "pass", review_summary[:400] or "Reviewed against the posting.")
    elif review_state == "blocked":
        add("review", "Independent review", "fail",
            "The independent review found an error: " + (review_summary[:400] or "Run the review again for details."))
    elif review_state == "review":
        add("review", "Independent review", "warn",
            "The independent review needs attention: " + (review_summary[:400] or "Review its findings before sending."))
    elif review_state == "running":
        add("review", "Independent review", "warn", "The independent review is still running.")
    else:
        add("review", "Independent review", "warn", "No independent review of this PDF yet.")

    # 7. The candidate's own decisions.
    with services.w.connect() as db:
        pending = db.execute("SELECT COUNT(*) FROM resume_items WHERE job_id=? AND origin='predicted' "
                             "AND decision='pending'", (job_id,)).fetchone()[0]
    if demo:
        add("assurance", "Your decisions", "pass",
            "demo mode: skipped" + (f" ({pending} suggested item{'s' if pending != 1 else ''} still undecided)" if pending else ""))
    elif pending:
        add("assurance", "Your decisions", "warn",
            f"{pending} suggested item{'s' if pending != 1 else ''} to keep or remove in Assurance before sending.")
    else:
        add("assurance", "Your decisions", "pass", "No suggestion waits for your decision.")
    if demo:
        add("profile", "Profile", "pass", "demo mode: skipped")
    elif services.profile_dirty():
        add("profile", "Profile", "warn", "Reconcile your pending profile edits before using this resume.")

    if demo:
        # Nothing may block in demo mode; the messages still say what would normally gate.
        for item in checks:
            if item["state"] == "fail" and item["id"] != "claims":
                item["state"] = "warn"
                item["note"] += " (demo mode: advisory only — this would normally block)"

    states = {item["state"] for item in checks}
    verdict_key = "blocked" if "fail" in states else "review" if "warn" in states else "ready"
    parts = {
        "fit": fit_score if isinstance(fit_score, (int, float)) else None,
        "coverage": ((match or {}).get("resume_coverage") or {}).get("score"),
        "ats": ((match or {}).get("ats_readiness") or {}).get("score"),
    }
    score = (round(sum(WEIGHTS[k] * parts[k] for k in WEIGHTS) / sum(WEIGHTS.values()))
             if all(isinstance(parts[k], (int, float)) for k in WEIGHTS) else None)
    return {
        "job_id": job_id, "verdict": verdict_key, "label": LABELS[verdict_key],
        "score": score, "parts": parts, "weights": WEIGHTS, "checks": checks,
        "next": [item["note"] for item in checks if item["state"] != "pass"],
        "review_summary": review_summary or None, "note": NOTE, "checked_at": services.now(),
        "demo_mode": demo,
    }
