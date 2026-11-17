"""What the agents are doing, in the shape the Agents tab draws.

Read-only. Each run comes with its step timeline (the stages it entered and
the AI calls it made, from ``agent_run_events``) and a one-line summary of each
report it produced. ``trace`` gives one run's whole record for the side panel:
also each search, each site read, each posting's outcome and each note. Full
reports stay in Resume Studio; nothing here is sent to a model.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

# Report keys a run can produce, in the order they are made.
OUTPUTS = ("research", "hiring", "comparison", "advice", "review", "plan")
# The timeline the activity list carries; the rest (a search can check hundreds of
# postings) comes only with one run's trace.
TIMELINE = ("stage", "ai_call", "completed", "failed")
TRACE_LIMIT = 3000
RUN_COLUMNS = "id,kind,job_id,state,result,error,created_at,updated_at,provider,model"


def _seconds(start: str, end: str | None) -> int | None:
    try:
        a = datetime.fromisoformat(start)
        b = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
        return max(0, int((b - a).total_seconds()))
    except (TypeError, ValueError):
        return None


def _event(e) -> dict:
    return {"at": e["at"], "kind": e["kind"], "label": e["label"], **json.loads(e["detail"] or "{}")}


def _run(r: dict, jobs: dict, events: list) -> dict:
    """One agent_runs row in the shape the page draws."""
    result = json.loads(r["result"]) if r["result"] else {}
    job = jobs.get(r["job_id"] or "") or {}
    active = r["state"] in ("queued", "running")
    return {
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
        "events": events,
    }


def activity(services, runner, limit: int = 60) -> dict:
    with services.w.connect() as db:
        runs = [dict(r) for r in db.execute(
            f"SELECT {RUN_COLUMNS} FROM agent_runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))]
        events: dict = {}
        ids = [r["id"] for r in runs]
        if ids:
            marks = ",".join("?" * len(ids))
            kinds = ",".join("?" * len(TIMELINE))
            for e in db.execute(
                f"SELECT run_id,at,kind,label,detail FROM agent_run_events WHERE run_id IN ({marks}) "
                f"AND kind IN ({kinds}) ORDER BY id",
                [*ids, *TIMELINE],
            ):
                events.setdefault(e["run_id"], []).append(_event(e))
        jobs = {r["id"]: dict(r) for r in db.execute("SELECT id,company,title FROM jobs")}
        calls = [dict(r) for r in db.execute(
            "SELECT provider,state,COUNT(*) AS n FROM ai_calls WHERE day=? GROUP BY provider,state",
            (services.today(),))]
        # Only a suggestion (origin 'predicted') waits for her keep/remove; registry items never do.
        review_counts = {r["decision"]: r["n"] for r in db.execute(
            "SELECT decision, COUNT(*) AS n FROM resume_items WHERE decision != 'pending' OR origin = 'predicted' "
            "GROUP BY decision")}
        tailored_jobs = db.execute("SELECT COUNT(DISTINCT job_id) AS n FROM resume_items").fetchone()["n"]
    listed = [_run(r, jobs, events.get(r["id"], [])) for r in runs]
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


def trace(services, run_id: str) -> dict | None:
    """Everything one run has recorded, oldest first, for the Agents tab's side panel."""
    with services.w.connect() as db:
        row = db.execute(f"SELECT {RUN_COLUMNS} FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        row = dict(row)
        jobs = {r["id"]: dict(r) for r in db.execute("SELECT id,company,title FROM jobs WHERE id=?", (row["job_id"] or "",))}
        total = db.execute("SELECT COUNT(*) FROM agent_run_events WHERE run_id=?", (run_id,)).fetchone()[0]
        events = [_event(e) for e in db.execute(
            "SELECT at,kind,label,detail FROM agent_run_events WHERE run_id=? ORDER BY id LIMIT ?", (run_id, TRACE_LIMIT))]
    result = json.loads(row["result"]) if row["result"] else {}
    sources, seen = [], set()

    def source(label, url="", **detail):
        key = url or label
        if key and key not in seen:
            seen.add(key)
            sources.append({"label": str(label or url), "url": url, **detail})

    for e in events:
        if e["kind"] == "source":
            source(e["label"], e.get("url") or "", found=e.get("found"), error=e.get("error") or "", via=e.get("via") or "")
    # A run from before the trace recorded its sources still names the pages its reports cite.
    for key in OUTPUTS:
        report = result.get(key)
        for item in (report.get("sources") or []) if isinstance(report, dict) else []:
            if isinstance(item, dict) and str(item.get("url") or "").startswith(("http://", "https://")):
                source(item.get("title") or item["url"], item["url"], via="ai")
    checks: dict = {}
    for e in events:
        if e["kind"] == "check":
            checks[e.get("outcome") or "other"] = checks.get(e.get("outcome") or "other", 0) + 1
    return {
        "run": _run(row, jobs, []),
        "events": events,
        "truncated": max(0, total - len(events)),
        "sources": sources,
        "checks": checks,
        "summary": str(result.get("summary") or "")[:4000],
        "added_job_ids": list(result.get("added_job_ids") or []),
        "now": services.now(),
    }
