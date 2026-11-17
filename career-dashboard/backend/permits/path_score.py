"""The permit-path evidence score: how much public evidence points to an employment-permit route.

0 to 100 from five parts, each with the reason it scored what it did:

=====================  ===  ==============================================================
employer history        30  DETE permits issued to the employer in the last 24 months
posting's own words     25  the posting offers permit support, is silent, or refuses
pay                     25  advertised (or estimated) pay against the person's threshold
occupation list         10  the Critical Skills or Ineligible list entry the duties match
EURES / JobsIreland     10  advertised where the Labour Market Needs Test requires
=====================  ===  ==============================================================

It is an evidence score, not the likelihood of a permit being approved, and it is kept apart
from how well the job fits the person. Not immigration advice.
"""

from __future__ import annotations

VERSION = "path-1"
LABEL = "Evidence score, not approval likelihood"


def _history(record: dict) -> tuple[int, str]:
    if record.get("match_type") == "ambiguous":
        return 0, "The employer name matches several DETE legal entities, so no history is counted."
    if not record.get("found"):
        return 0, "No DETE permit record matched this employer (absence is not a refusal)."
    recent = int(record.get("permits_24_months") or 0)
    names = "; ".join(record.get("matched_names") or [])
    points = 30 if recent >= 20 else 22 if recent >= 5 else 15 if recent >= 1 else 5
    return points, f"DETE lists {recent:,} permits issued to {names} in the last 24 months."


def _statement(statement: dict) -> tuple[int, str]:
    state, quote = statement.get("state"), statement.get("quote") or ""
    if state == "supports":
        return 25, f'The posting offers permit support: "{quote}"'
    if state == "refuses":
        return 0, f'The posting refuses permit support: "{quote}"'
    if state == "ambiguous":
        return 8, "The posting mentions permits without saying whether it supports one."
    return 10, "The posting says nothing about permits (the usual case); its employer may still support one."


def _pay(opportunity: dict, threshold: float) -> tuple[int, str]:
    pay, estimate = opportunity.get("salary") or {}, opportunity.get("estimate")
    low, high = pay.get("annual_min"), pay.get("annual_max")
    if pay.get("kind") == "advertised" and pay.get("currency") == "EUR" and (low or high):
        if low is not None and low >= threshold:
            return 25, f"Advertised pay from EUR {low:,.0f} reaches the EUR {threshold:,.0f} threshold."
        if high is not None and high < threshold:
            return 0, f"Advertised pay up to EUR {high:,.0f} is below the EUR {threshold:,.0f} threshold."
        return 12, f"The advertised range spans the EUR {threshold:,.0f} threshold; the offer decides."
    if pay.get("kind") == "researched" and (low or 0) >= threshold:
        return 12, f"Researched comparable pay reaches EUR {threshold:,.0f}; this vacancy's pay is unconfirmed."
    if estimate and estimate.get("median", 0) >= threshold:
        return 12, f"{estimate['label']} Its median reaches EUR {threshold:,.0f}."
    return 5, f"The pay is not stated; confirm it reaches EUR {threshold:,.0f}."


def _occupation(occupation: dict) -> tuple[int, str]:
    state = occupation.get("classification") or occupation.get("status") or "unknown"
    if state == "critical":
        return 10, "The duties appear to match a Critical Skills Occupations List entry."
    if state == "ineligible":
        return 0, "The duties appear to match an Ineligible Occupations List entry."
    if state == "neither":
        return 6, "The occupation appears on neither list (a General Employment Permit route)."
    return 3, "The occupation list entry is not established from the duties."


def score(job: dict, opportunity: dict, threshold: float) -> dict:
    """The score with its parts; ``threshold`` is the person's applicable permit salary threshold."""
    from backend.permits.assessment import on_eures

    permit = opportunity.get("permit") or {}
    parts = [
        ("employer_history", 30, *_history(permit.get("permit_record") or {})),
        ("posting_statement", 25, *_statement(opportunity.get("sponsorship") or {})),
        ("pay", 25, *_pay(opportunity, threshold)),
        ("occupation", 10, *_occupation(permit.get("occupation") or {})),
        ("eures", 10, *((10, "Advertised on EURES / JobsIreland, the channels the Labour Market Needs Test uses.")
                        if on_eures(job) else (0, "No EURES or JobsIreland listing is recorded."))),
    ]
    return {"score": sum(points for _, _, points, _ in parts), "label": LABEL, "version": VERSION,
            "threshold": threshold,
            "parts": [{"id": key, "points": points, "max": most, "reason": reason} for key, most, points, reason in parts],
            "disclaimer": "Not immigration advice"}
