"""One opportunity view shared by jobs, CLI, Assistant and readiness."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import date
from backend.services import salary, sponsorship, permit_assessment
from backend.countries import load_pack


def digest(text):
    return hashlib.sha256(str(text or "").encode()).hexdigest()


def permit_statement(text):
    """What a posting itself says about permits: refuses, supports, ambiguous or silent, with its sentence.

    The country's rules only (never a person's), so the shared market store records the same view.
    """
    text = str(text or "")
    rules = sponsorship.load_rules(str(load_pack("ie").template("sponsorship.yml")))
    checked = sponsorship.screen(text, rules)
    statement = {"state": "refuses" if checked.verdict == "EXCLUDED" else "supports" if checked.reason == "explicit_sponsorship" else "silent",
                 "quote": checked.sentence}
    own_words = sponsorship.strip_notices(text, rules)  # a board's statutory permit notice is not the employer's
    accepted = re.search(r"[^.\n]*stamp\s*1\s*g\s+(?:holders?\s+)?(?:are\s+)?(?:welcome|considered|accepted|eligible)[^.\n]*", own_words, re.I)
    if accepted:
        statement["current_permission_quote"] = accepted[0].strip()
    if statement["state"] == "silent" and re.search(r"\b(sponsorship|employment permit|work permit)\b", own_words, re.I):
        statement["state"] = "ambiguous"
    return statement


def evaluate(job, profile=None):
    if job.get("market") != "ie":
        return None
    text = job.get("description") or ""
    meta = job.get("posting_metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    current = meta.get("jd_hash") == digest(text)
    pay = salary.extract(text, raw_salary=meta.get("raw_salary") if current else None,
                         url=job.get("url", ""), observed_at=meta.get("observed_at", ""))
    if pay["kind"] == "unknown" and current:
        pay = salary.research(meta.get("salary_research") or [])
    statement = permit_statement(text)
    floor = salary.floor_for(profile)
    policy = salary.policy_for(profile)
    state = salary.assess(pay, floor)
    section = "below_floor" if state == "below_floor" else "salary_matches" if state == "meets_floor" and pay["kind"] == "advertised" else "researched_leads" if state == "meets_floor" and pay["kind"] == "researched" else "needs_research"
    estimate = None
    if pay["kind"] == "unknown":
        from backend.market import normalize
        from backend.market.salary_estimates import estimate as market_estimate

        estimate = market_estimate(normalize.role_family(job.get("title") or ""), normalize.level(job.get("title") or "", text))
        if estimate and estimate["median"] >= floor and section == "needs_research":
            section = "estimated_matches"
    from backend.permits.assessment import needs_permit

    result = {"salary": pay, "salary_state": state, "section": section, "floor": floor, "salary_policy": policy,
              "needs_permit": needs_permit(profile or {}),
              "estimate": estimate, "sponsorship": statement, "permit": permit_assessment.assess(job, pay, profile or {}, statement),
              "valid_through": meta.get("valid_through") if current else None, "policy_version": salary.VERSION}
    from backend.permits import path_score

    # Scored against the permit threshold that applies to the person, not their own (higher) preference.
    result["permit_path"] = path_score.score({**job, "posting_metadata": meta}, result, result["permit"]["personal_floor_eur"])
    return result


def record(services, job_id, values):
    job = services.w.get_job(job_id)
    meta = job.get("posting_metadata") or {}
    current_hash = digest(job["description"])
    if meta.get("jd_hash") != current_hash:
        meta = {}  # Neither old JSON-LD nor old research describes a changed vacancy.
    meta = {**meta, "jd_hash": current_hash, "observed_at": services.now()}
    for key in ("raw_salary", "valid_through", "posted_at", "source"):
        if values.get(key) is not None:
            meta[key] = values[key]
    if values.get("on_eures"):
        meta["on_eures"] = True  # where it was advertised: a permit-route signal (permits/path_score.py)
    # Raw JSON-LD is retained; an unvalidated model-produced salary object is never trusted.
    with services.w.connect() as db:
        db.execute("UPDATE jobs SET posting_metadata=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), job_id))


def preparation_issue(job):
    info = job.get("opportunity")
    if not info:
        return None
    if info["sponsorship"]["state"] == "refuses":
        return "The posting explicitly refuses the required permit support."
    if info["section"] == "below_floor":
        if (info.get("salary") or {}).get("kind") == "researched":
            return (f"Comparable published pay for this role is below the EUR {info['floor']:,.0f} discovery floor; "
                    "the vacancy's own pay is unconfirmed.")
        return f"Advertised pay is below the EUR {info['floor']:,.0f} discovery floor."
    policy = info.get("salary_policy")
    if info["section"] == "researched_leads":
        # Comparable pay from two independent dated sources (services/salary_research.py) is an estimate too.
        if policy in ESTIMATES_ALLOWED:
            return None  # prepared, with a note to confirm the actual pay (pay_note)
        return "Only researched comparable pay shows this role's pay; your settings prepare advertised pay only."
    if info["section"] == "estimated_matches":
        if policy in ESTIMATES_ALLOWED:
            return None  # prepared, with a note to confirm the actual pay (pay_note)
        return "Only a market estimate shows this role's pay; your settings prepare advertised pay only."
    if info["section"] != "salary_matches":
        if policy == "include_unstated":
            return None  # no pay stated (or a range that starts below the floor): prepared, flagged (pay_note)
        return f"The vacancy's annual pay is not confirmed at EUR {info['floor']:,.0f} or above."
    return None


# Policies that prepare a job whose pay is only a labelled estimate or researched comparable pay.
ESTIMATES_ALLOWED = ("include_unstated", "confirmed_or_estimated")


def pay_known(job) -> bool:
    """The posting's pay, a market estimate or researched comparable pay reaches the floor."""
    return (job.get("opportunity") or {}).get("section") in {"salary_matches", "estimated_matches", "researched_leads"}


def pay_note(job):
    """What to confirm about pay before applying, when the posting itself does not settle it ('' otherwise)."""
    info = job.get("opportunity") or {}
    if info.get("section") == "estimated_matches" and info.get("estimate"):
        estimate = info["estimate"]
        return (f"This posting states no salary. {estimate['label']} Typical range EUR {estimate['p25']:,}-{estimate['p75']:,} "
                f"(median EUR {estimate['median']:,}). Confirm with the recruiter that the base salary is at least "
                f"EUR {info['floor']:,.0f} before you apply.")
    if info.get("section") == "researched_leads":
        return (f"The salary here is from comparable published pay, not this vacancy. Confirm with the recruiter that "
                f"the base salary is at least EUR {info['floor']:,.0f} before you apply.")
    if info.get("section") == "needs_research" and info.get("salary_policy") == "include_unstated" \
            and info.get("needs_permit", True):  # unstated pay matters when a permit's salary threshold applies
        pay = info.get("salary") or {}
        if pay.get("kind") == "advertised" and pay.get("currency") == "EUR" and pay.get("annual_min"):
            return (f"The advertised pay starts at EUR {pay['annual_min']:,.0f}, below your EUR {info['floor']:,.0f} floor. "
                    f"Confirm with the recruiter that the base salary is at least EUR {info['floor']:,.0f} before you apply.")
        return (f"This posting states no salary in euro. Confirm with the recruiter that the base salary is at least "
                f"EUR {info['floor']:,.0f} before you apply.")
    return ""
