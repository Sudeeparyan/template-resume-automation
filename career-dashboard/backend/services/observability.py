"""What the agents are doing, in the shape the Agents tab draws.

Read-only. Each run comes with its step timeline (the stages it entered and
the AI calls it made, from ``agent_run_events``) and a one-line summary of each
report it produced. Full reports stay in Resume Studio; nothing here is sent to
a model.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

# Report keys a run can produce, in the order they are made.
OUTPUTS = ("research", "hiring", "comparison", "advice", "review")


def _seconds(start: str, end: str | None) -> int | None:
    try:
        a = datetime.fromisoformat(start)
        b = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
        return max(0, int((b - a).total_seconds()))
    except (TypeError, ValueError):
        return None


def activity(services, runner, limit: int = 60) -> dict:
    with services.w.connect() as db:
        runs = [dict(r) for r in db.execute(
            "SELECT id,kind,job_id,state,result,error,created_at,updated_at,provider,model "
            "FROM agent_runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))]
        events: dict = {}
        ids = [r["id"] for r in runs]
        if ids:
            marks = ",".join("?" * len(ids))
            for e in db.execute(
                f"SELECT run_id,at,kind,label,detail FROM agent_run_events WHERE run_id IN ({marks}) ORDER BY id",
                ids,
            ):
                events.setdefault(e["run_id"], []).append(
                    {"at": e["at"], "kind": e["kind"], "label": e["label"], **json.loads(e["detail"] or "{}")}
                )
        jobs = {r["id"]: dict(r) for r in db.execute("SELECT id,company,title FROM jobs")}
        calls = [dict(r) for r in db.execute(
            "SELECT provider,state,COUNT(*) AS n FROM ai_calls WHERE day=? GROUP BY provider,state",
            (services.today(),))]
        # Only a suggestion (origin 'predicted') waits for her keep/remove; registry items never do.
        review_counts = {r["decision"]: r["n"] for r in db.execute(
            "SELECT decision, COUNT(*) AS n FROM resume_items WHERE decision != 'pending' OR origin = 'predicted' "
            "GROUP BY decision")}
        tailored_jobs = db.execute("SELECT COUNT(DISTINCT job_id) AS n FROM resume_items").fetchone()["n"]
    listed = []
    for r in runs:
        result = json.loads(r["result"]) if r["result"] else {}
        job = jobs.get(r["job_id"] or "") or {}
        active = r["state"] in ("queued", "running")
        listed.append({
            "id": r["id"],
            "kind": r["kind"],
            "job_id": r["job_id"],
            "company": job.get("company"),
            "title": job.get("title"),
            "state": r["state"],
            "stage": result.get("stage"),
            "error": r["error"],
            "provider": r["provider"],
            "model": r["model"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "seconds": _seconds(r["created_at"], None if active else r["updated_at"]),
            "outputs": {
                key: str(result[key].get("summary") or "")[:500]
                for key in OUTPUTS if isinstance(result.get(key), dict)
            },
            "events": events.get(r["id"], []),
        })
    totals = {"completed": 0, "failed": 0, "running": 0}
    by_provider: dict = {}
    for row in calls:
        totals[row["state"]] = totals.get(row["state"], 0) + row["n"]
        by_provider[row["provider"]] = by_provider.get(row["provider"], 0) + row["n"]
    return {
        "runs": listed,
        "budget": runner.cache.stats(),
        "calls_today": {**totals, "by_provider": by_provider},
        "reviews": {
            "pending": review_counts.get("pending", 0),
            "kept": review_counts.get("kept", 0),
            "removed": review_counts.get("removed", 0),
            "tailored_jobs": tailored_jobs,
        },
        "ai": runner.gateway.preferences(),
        "now": services.now(),
    }
