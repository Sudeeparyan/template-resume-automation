"""What only the person can do now: one list for the Dashboard and the morning list.

Each item says what to do and where, in plain words. Nothing is done for them, and dates are the
facts they confirmed with the official page beside them, never immigration advice:

* the work-authorization facts searches need (``require_known_authorization``'s own message);
* a recorded Stamp 1G expiry that has passed or falls within ``SOON_DAYS``;
* pending profile entries, which new resumes and cover letters wait for;
* no AI app or key set up on this computer;
* saved jobs whose posting states no pay and that no estimate puts at their floor;
* the last overnight hunt skipping AI searches because every free plan was resting while paid AI
  was off for hunts.
"""

from __future__ import annotations

import json

SOON_DAYS = 60


def items(services) -> list[dict]:
    """[{id, text, where, url?}], most urgent first; ``where`` is the dashboard page that resolves it."""
    root = services.w.root
    out: list[dict] = []
    from backend.countries import require_known_authorization, target_markets_for

    try:
        require_known_authorization(root)
    except ValueError as problem:
        out.append({"id": "work_authorization", "text": str(problem), "where": "profile"})
    if "ie" in target_markets_for(root):
        from backend.permits.timeline import timeline

        for event in timeline(services.w.profile())["events"]:
            left = event.get("days_left")
            if event["id"] != "stamp_1g_expiry" or not isinstance(left, int) or left > SOON_DAYS:
                continue
            text = (f"Your recorded Stamp 1G expiry date, {event['date']}, has passed. Update your permission in Profile "
                    "before new jobs are searched or prepared." if left < 0 else
                    f"Your recorded Stamp 1G expiry is {event['date']}, in {left} days. The official page has the "
                    "conditions that apply to you.")
            out.append({"id": "stamp_1g_expiry", "text": text, "where": "profile", "url": event.get("url") or ""})
    if services.profile_dirty():
        out.append({"id": "profile_pending", "where": "profile",
                    "text": "Confirm the pending entries in Profile; new resumes and cover letters wait for them."})
    from backend.ai import any_provider_configured

    if not any_provider_configured(root):
        out.append({"id": "ai_setup", "where": "settings",
                    "text": "Set up an AI app (Kimi Code, Codex or Claude Code) or a key in Settings; finding jobs "
                            "and tailoring resumes need one."})
    waiting = [job for job in services.w.jobs() if job["status"] == "saved"
               and (job.get("opportunity") or {}).get("section") == "needs_research"]
    if waiting:
        names = "; ".join(f"{job['title']} at {job['company']}" for job in waiting[:3])
        out.append({"id": "pay_unconfirmed", "where": "daily", "job_ids": [job["id"] for job in waiting],
                    "text": f"{len(waiting)} saved job{'s' if len(waiting) != 1 else ''} state{'' if len(waiting) != 1 else 's'} "
                            f"no pay and no estimate reaches your floor ({names}{'…' if len(waiting) > 3 else ''}). "
                            "Ask the employer for the salary, or remove the job."})
    skipped = _hunt_skips(services)
    if skipped:
        out.append({"id": "hunt_waited_for_free_ai", "where": "daily",
                    "text": f"The last overnight hunt skipped {skipped} AI search{'es' if skipped != 1 else ''} because "
                            "every free AI plan was resting. Paid AI is off for hunts; switch it on there if you want "
                            "it used, or let the next hunt wait for the plans to reset."})
    return out


def _hunt_skips(services) -> int:
    """AI searches the latest finished hunt skipped for want of a free plan while paid AI was off."""
    try:
        with services.w.connect() as db:
            row = db.execute("SELECT config, progress FROM hunt_runs WHERE state IN ('completed','stopped','failed') "
                             "ORDER BY created_at DESC LIMIT 1").fetchone()
    except Exception:  # noqa: BLE001 - no hunt has ever run in this profile
        return 0
    if not row:
        return 0
    try:
        config, progress = json.loads(row["config"] or "{}"), json.loads(row["progress"] or "{}")
    except ValueError:
        return 0
    if config.get("allow_paid"):
        return 0
    return sum(1 for item in progress.get("passes") or [] if item.get("state") == "skipped"
               and str(item.get("note") or "").startswith("No AI plan was free"))
