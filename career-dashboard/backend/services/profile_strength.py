"""How ready a profile is to make good resumes, and what to add next.

It counts only what a tailored Irish CV needs, read from the evidence registry the resumes
are built from (data/context/evidence.yml) and the profile settings: contact details,
permission to work, target roles, education with dates, real experience or projects with
bullets, enough skills, and no profile entries still waiting for review. A certification is
a bonus suggestion, never a penalty. Nothing here judges the person; it only says which
facts are missing for the documents.
"""

from __future__ import annotations

KNOWN_AUTH = {"authorized", "needs_sponsorship", "citizen"}


def _claims(registry: dict, *categories: str) -> list[dict]:
    return [c for c in (registry.get("claims") or []) if isinstance(c, dict) and c.get("category") in categories]


def _facts(claim: dict) -> list:
    facts = claim.get("approved_facts")
    return facts if isinstance(facts, list) else []


def score(registry: dict, configuration: dict, pending: int = 0, market: str = "ie") -> dict:
    """{score 0-100, checks: [{id, label, done, why, where, weight}], next: [the three to do]}"""
    registry = registry or {}
    configuration = configuration or {}
    candidate = configuration.get("candidate") or {}
    contact = {str(c.get("value") or "") for c in _claims(registry, "contact")}
    name = candidate.get("full_name") or any(c.get("value") for c in _claims(registry, "identity"))
    email = candidate.get("email") or any("@" in value for value in contact)
    reach = candidate.get("phone") or candidate.get("linkedin") or len(contact) >= 2

    auth = ((configuration.get("work_authorization_by_market") or {}).get(market) or {})
    roles = (configuration.get("target_roles") or {}).get("primary") or []

    education = _claims(registry, "education")
    dated_education = [e for e in education if e.get("dates") and (e.get("degree_as_supplied") or e.get("degree"))]
    jobs = [e for e in _claims(registry, "employment", "experience") if len(_facts(e)) >= 2]
    projects = [p for p in registry.get("projects") or []
                if isinstance(p, dict) and len(((p.get("resume_content") or {}).get("bullets")) or []) >= 2]
    skills = sum(len(_facts(s)) or 1 for s in _claims(registry, "skill"))
    certs = _claims(registry, "certification")

    checks = [
        ("contact", 15, bool(name and email and reach), "Name, email and a phone number or LinkedIn",
         "Every CV starts with how to reach you.", "profile"),
        ("work_authorization", 15, auth.get("status") in KNOWN_AUTH or auth.get("citizenship") == "citizen",
         "Permission to work in Ireland", "It decides which jobs are possible and what the CV says about permits.", "profile"),
        ("target_roles", 10, bool(roles), "The roles you are looking for",
         "Searches and tailoring aim at these titles.", "profile"),
        ("education", 15, bool(dated_education), "Education with degree and dates",
         "Irish graduate CVs lead with this; dates must be exact.", "profile"),
        ("experience", 20, bool(jobs) or len(projects) >= 2, "A role or two projects with at least two bullets each",
         "The bullets are what a tailored resume is made from.", "profile"),
        ("skills", 15, skills >= 5, "At least five skills",
         "Requirement checks match a posting's must-haves against these.", "profile"),
        ("reviewed", 10, pending == 0, "Every profile entry reviewed",
         "Entries waiting for your review are kept off resumes.", "profile"),
    ]
    out = [{"id": cid, "weight": weight, "done": done, "label": label, "why": why, "where": where}
           for cid, weight, done, label, why, where in checks]
    total = sum(c["weight"] for c in out if c["done"])
    todo = sorted((c for c in out if not c["done"]), key=lambda c: -c["weight"])
    tips = [] if certs else [{"id": "certification", "label": "A certificate or course, if you have one",
                              "why": "Optional: it can support a requirement, and is never required.", "where": "profile"}]
    return {"score": total, "checks": out, "next": todo[:3], "tips": tips}
