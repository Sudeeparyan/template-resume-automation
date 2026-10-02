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
    checked = sponsorship.screen(text, sponsorship.load_rules(str(load_pack("ie").template("sponsorship.yml"))))
    statement = {"state": "refuses" if checked.verdict == "EXCLUDED" else "supports" if checked.reason == "explicit_sponsorship" else "silent",
                 "quote": checked.sentence}
    accepted = re.search(r"[^.\n]*stamp\s*1\s*g\s+(?:holders?\s+)?(?:are\s+)?(?:welcome|considered|accepted|eligible)[^.\n]*", text, re.I)
    if accepted:
        statement["current_permission_quote"] = accepted[0].strip()
    if statement["state"] == "silent" and re.search(r"\b(sponsorship|employment permit|work permit)\b", text, re.I):
        statement["state"] = "ambiguous"
    state = salary.assess(pay)
    section = "below_floor" if state == "below_floor" else "salary_matches" if state == "meets_floor" and pay["kind"] == "advertised" else "researched_leads" if state == "meets_floor" and pay["kind"] == "researched" else "needs_research"
    return {"salary": pay, "salary_state": state, "section": section, "floor": salary.DEFAULT_FLOOR,
            "sponsorship": statement, "permit": permit_assessment.assess(job, pay, profile or {}, statement),
            "valid_through": meta.get("valid_through") if current else None, "policy_version": salary.VERSION}


def record(services, job_id, values):
    job = services.w.get_job(job_id)
    meta = job.get("posting_metadata") or {}
    current_hash = digest(job["description"])
    if meta.get("jd_hash") != current_hash:
        meta = {}  # Neither old JSON-LD nor old research describes a changed vacancy.
    meta = {**meta, "jd_hash": current_hash, "observed_at": services.now()}
    for key in ("raw_salary", "valid_through", "posted_at"):
        if values.get(key) is not None:
            meta[key] = values[key]
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
        return "Advertised pay is below the EUR 36,000 discovery floor."
    if info["section"] == "researched_leads":
        return "This is a researched salary lead; confirm the vacancy's actual pay before automatic preparation."
    if info["section"] != "salary_matches":
        return "The vacancy's annual pay is not confirmed at EUR 36,000 or above."
    return None
