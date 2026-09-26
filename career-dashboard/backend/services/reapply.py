"""Profile-configured duplicate and reapplication policy over the shared job tracker.

Silence never changes application status. A person or verified employer message must
establish rejection or any other outcome.

Automatic searches (``automatic=True``) also never suggest again what the person already
answered: a role they applied for (a repost under a new link included), a role they removed,
and whatever their removal reason teaches (REMOVAL_REASONS): every role at a company they
ruled out, or the same job title anywhere when it was the wrong kind of role. A job the
person adds by hand is never blocked by these; it only gets a note.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

DEFAULTS = {
    "block_same_role": False,
    "auto_ghost": False,
    "ghost_after_days": 21,
    "reject_cooldown_days": 180,
    "ghost_cooldown_days": 90,
}

# Why a saved role was removed, as the Dashboard offers it. The removal reason starts with
# one of these labels; what each one teaches the next automatic search is in check().
REMOVAL_REASONS = {
    "role": "Wrong kind of role",
    "senior": "Too senior for me",
    "company": "Not this company",
    "permit": "Needs a work permit or citizenship I don't have",
    "location": "Wrong location",
    "other": "Not suitable",
}
# Removed for these: skip every later role at that company.
COMPANY_LESSONS = {"company", "permit"}
# Removed for these: skip the same job title at any company.
TITLE_LESSONS = {"role", "senior"}
# A role in one of these states was applied for; the same role is never suggested again.
APPLIED_STATES = {"applied", "interview", "offer", "withdrawn"}


def removal_kind(reason: str | None) -> str | None:
    """Which REMOVAL_REASONS entry a removal reason starts with, or None."""
    text = str(reason or "").strip().casefold()
    for kind, label in REMOVAL_REASONS.items():
        if kind != "other" and text.startswith(label.casefold()):
            return kind
    return None


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())


def _day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def settings(profile: dict[str, Any] | None) -> dict[str, Any]:
    raw = (profile or {}).get("reapply") or {}
    return {
        "block_same_role": bool(raw.get("block_same_role", DEFAULTS["block_same_role"])),
        # Ignore older profile switches that inferred an outcome from silence.
        "auto_ghost": False,
        "ghost_after_days": max(0, int(raw.get("ghost_after_days", DEFAULTS["ghost_after_days"]))),
        "reject_cooldown_days": max(0, int(raw.get("reject_cooldown_days", DEFAULTS["reject_cooldown_days"]))),
        "ghost_cooldown_days": max(0, int(raw.get("ghost_cooldown_days", DEFAULTS["ghost_cooldown_days"]))),
    }


def check(company: str, title: str, jobs: Iterable[dict[str, Any]], excluded: Iterable[dict[str, Any]] = (),
          profile: dict[str, Any] | None = None, today: date | None = None, *,
          automatic: bool = False) -> dict[str, Any]:
    """Return {blocked, rule, note}. `jobs` should include removed rows (deleted_at set).

    ``automatic`` is a search deciding on its own; it also applies what the person already
    answered (see the module notes). By hand, those only add a note.
    """
    rules = settings(profile)
    today = today or datetime.now(timezone.utc).date()
    company_key, title_key = normalize(company), normalize(title)
    notes: list[str] = []
    rows = list(jobs) + list(excluded)
    learned = _learned(company_key, title_key, rows)
    if learned:
        if automatic:
            return {"blocked": True, **learned}
        notes.append(learned["note"])
    for row in rows:
        if normalize(row.get("company")) != company_key:
            continue
        same_role = normalize(row.get("title") or row.get("role")) == title_key
        status = str(row.get("status") or "").lower()
        changed = _day(row.get("updated_at")) or _day(row.get("created_at")) or _day(row.get("excluded_at"))
        if rules["block_same_role"] and same_role and (status or row.get("excluded_at")):
            label = "excluded" if row.get("excluded_at") else status
            return {"blocked": True, "rule": "same_role", "note": f"Same company and role already {label} ({row.get('url') or row.get('id')})."}
        if rules["reject_cooldown_days"] and status == "rejected" and changed and (today - changed).days < rules["reject_cooldown_days"]:
            left = rules["reject_cooldown_days"] - (today - changed).days
            return {"blocked": True, "rule": "rejected_180", "note": f"{company} rejected you {(today - changed).days} days ago; suppressed for {left} more days."}
        if rules["ghost_cooldown_days"] and status == "ghosted" and changed and (today - changed).days < rules["ghost_cooldown_days"]:
            left = rules["ghost_cooldown_days"] - (today - changed).days
            return {"blocked": True, "rule": "ghosted_90", "note": f"{company} went quiet {(today - changed).days} days ago; wait {left} more days before a different role."}
        if status == "ghosted":
            notes.append(f"{company} ghosted an earlier application; a different role is allowed.")
    return {"blocked": False, "rule": None, "note": " ".join(notes)}


def _learned(company_key: str, title_key: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """{rule, note} when the person already answered this role (applied, or removed it), else None."""
    for row in rows:
        same_company = normalize(row.get("company")) == company_key
        same_title = normalize(row.get("title") or row.get("role")) == title_key
        status = str(row.get("status") or "").lower()
        who = f"{row.get('company')} · {row.get('title') or row.get('role')}"
        if same_company and same_title and status in APPLIED_STATES and not row.get("deleted_at"):
            when = row.get("application_date") or str(row.get("updated_at") or "")[:10]
            verb = "withdrew from" if status == "withdrawn" else "already applied for"
            return {"rule": "already_applied",
                    "note": f"You {verb} this role ({who}, {status}{', ' + when if when else ''})."}
        if not row.get("deleted_at"):
            continue
        reason = str(row.get("deletion_reason") or "").strip()
        kind = removal_kind(reason)
        if same_company and same_title:
            return {"rule": "removed_before", "note": f"You removed this role before ({who}): {reason or 'not suitable'}."}
        if same_company and kind in COMPANY_LESSONS:
            return {"rule": "removed_company", "note": f"You ruled out {row.get('company')} before ({who}): {reason}."}
        if same_title and kind in TITLE_LESSONS:
            return {"rule": "removed_title", "note": f"You removed this job title before ({who}): {reason}."}
    return None


def due_for_ghosting(jobs: Iterable[dict[str, Any]], profile: dict[str, Any] | None = None,
                     today: date | None = None) -> list[dict[str, Any]]:
    """Retained for old callers; elapsed time cannot establish an outcome."""
    return []


def quiet_days(row: dict[str, Any], today: date | None = None) -> int | None:
    """How long an open application has waited, for the Pipeline view."""
    today = today or datetime.now(timezone.utc).date()
    anchor = _day(row.get("application_date")) or _day(row.get("updated_at"))
    return (today - anchor).days if anchor else None
