"""TrackerRefresh: keeps the shared market store, which the Tracker reads, current. No AI.

Once per computer every ``REFRESH_HOURS`` the public sources are read for the roles the ready
profiles on this computer target, and what they list is recorded in the market store
(data/market/market.db): new postings, last-seen times, permit statements and pay. Then postings
past their closing date are closed and long-unseen ones marked stale. No profile's jobs, search
memory or coverage change: saving a posting stays the person's choice on the Tracker page,
through the usual gates. The keyed aggregators (Careerjet, Jooble) are left to searches, which
need their small request allowances more.

Built with LangGraph's functional API: the refresh is an ``@entrypoint`` and each source a
``@task``, read one after another so hosts are never hit in parallel. A refresh stopped by a
restart resumes without reading its finished sources again (thread ``tracker_refresh:<run id>``,
checkpointed under data/market/refresh/). ``MarketStore.claim_run`` keeps two apps on one
computer from refreshing at once. On by default; ``CAREER_FEATURES=-graph_tracker_refresh`` (or
the profile's ``features`` preference) turns it off. ``career market refresh`` runs it by hand.
"""

from __future__ import annotations

from pathlib import Path

from langgraph.func import entrypoint, task

from backend import paths, telemetry
from backend.graphs import checkpoint

NAME = "tracker_refresh"
SOURCES = ("directory", "registry", "eures", "gradireland", "jobs_ie")
REFRESH_HOURS = 6
SECONDS_PER_SOURCE = 600
WORK_ROOT = "refresh"  # its own coverage state, under data/market/, never a profile's


def ready_profiles() -> list[str]:
    """Roots of the profiles on this computer that finished setup (their roles shape the refresh)."""
    from backend.profiles import store

    profiles = store()
    roots = []
    for entry in profiles.list():
        if entry.get("state") == "ready" and not entry.get("locked"):
            try:
                roots.append(str(profiles.root_for(entry["id"])))
            except (KeyError, ValueError):
                continue
    return roots


def _work_root() -> Path:
    return paths.MARKET_DB.parent / WORK_ROOT


@task
def read_source(source: str, roots: list[str], seconds: float) -> dict:
    """One public source for every ready profile's roles; postings go to the market store."""
    from backend.countries import load_pack
    from backend.job_quality import ProfileRules
    from backend.services import job_sources
    from backend.services.search_plan import plan_for

    matchers = [ProfileRules.of(root).roles for root in roots]
    keywords = list(dict.fromkeys(k for root in roots for k in plan_for(root)["board_keywords"]))
    ireland = load_pack("ie")
    with telemetry.span(f"tracker_refresh.source {source}", **{"career.source": source}):
        postings, coverage = job_sources.harvest(
            str(_work_root()), [source], title_ok=lambda title: any(m.search(title) for m in matchers),
            place_ok=ireland.location_ok, keywords=keywords, tz="Europe/Dublin", seconds=seconds)
    failed = [line for line in coverage if "FETCH FAILED" in line]
    return {"source": source, "found": len(postings), "coverage": coverage[:20], "failed": len(failed)}


def build(saver):
    @entrypoint(checkpointer=saver)
    def refresh(spec: dict) -> dict:
        results = []
        for source in spec["sources"]:
            results.append(read_source(source, spec["roots"], spec["seconds"]).result())  # one at a time
        return {"sources": results, "found": sum(r["found"] for r in results)}
    return refresh


def due(*, hours: float = REFRESH_HOURS) -> bool:
    """No refresh yet, or the last one ended (however it ended) at least ``hours`` ago."""
    from datetime import datetime, timedelta, timezone

    from backend.market.store import MarketStore

    last = MarketStore().last_run(NAME)
    if not last:
        return True
    ended = last.get("finished_at")
    return bool(ended) and datetime.now(timezone.utc) - datetime.fromisoformat(ended) >= timedelta(hours=hours)


def run(*, roots: list[str] | None = None, sources=SOURCES, force: bool = False, seconds: float = SECONDS_PER_SOURCE) -> dict:
    """Refresh the market store now (or say why not); returns what each source found."""
    from backend.market.store import MarketStore

    roots = ready_profiles() if roots is None else roots
    if not roots:
        return {"state": "skipped", "reason": "No profile on this computer has finished setup, so there are no roles to refresh."}
    store = MarketStore()
    run_id = store.claim_run(NAME, fresh_hours=0 if force else REFRESH_HOURS)
    if run_id is None:
        last = store.last_run(NAME) or {}
        return {"state": "skipped", "reason": ("Another refresh is running." if last.get("state") == "running"
                                               else f"Refreshed at {last.get('finished_at')}; the next one is due "
                                                    f"{REFRESH_HOURS} hours after that.")}
    graph = build(checkpoint.saver(_work_root()))
    config = {"configurable": {"thread_id": f"{NAME}:{run_id}"}}
    try:
        with telemetry.span("graph.run tracker_refresh", **{"career.graph": NAME, "career.run_id": str(run_id)}):
            result = graph.invoke({"roots": roots, "sources": list(sources), "seconds": seconds}, config)
        states = store.refresh_states()
        failed = sum(r["failed"] for r in result["sources"])
        store.finish_run(run_id, state="completed", found=result["found"],
                         error=f"{failed} feeds failed" if failed else "")
        return {"state": "completed", "run_id": run_id, **result, **states, "counts": store.counts()}
    except Exception as error:
        store.finish_run(run_id, state="failed", error=f"{type(error).__name__}: {error}")
        raise


def resume(run_id: int) -> dict:
    """Continue a refresh the app stopped, after its last finished source (input None)."""
    graph = build(checkpoint.saver(_work_root()))
    return graph.invoke(None, {"configurable": {"thread_id": f"{NAME}:{run_id}"}})
