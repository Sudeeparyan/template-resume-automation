"""Actual agent submissions retain their parent span and link the local event log."""

from concurrent.futures import ThreadPoolExecutor

from backend import telemetry
from backend.services.agents import AgentRunner


def test_agent_submission_keeps_parent_and_event_ids(spans, tmp_path):
    from test_hunt import ireland_profile

    services = ireland_profile(tmp_path)
    runner = AgentRunner(services)

    # Replace only execution, so enqueue still uses the real profile repository
    # and context-carrying worker path without calling any AI provider.
    def worker(run_id):
        runner.trace.stage = "Checking posting"
        runner.trace_event("stage", "Checking posting", run_id=run_id)
        runner.update(run_id, "completed", {"summary": "synthetic"})

    runner._run_worker = worker
    try:
        with telemetry.span("pipeline.run") as parent:
            task = runner.enqueue("discovery", preset="feeds", focus={"hunt": True})
            # Join the submitted agent rather than polling a real profile.
            runner.pool.shutdown(wait=True)
        calls = [
            s for s in spans.get_finished_spans() if s.name == "agent.run discovery"
        ]
        assert (
            len(calls) == 1
            and calls[0].parent.span_id == parent.get_span_context().span_id
        )
        assert calls[0].attributes["career.run_id"] == task["id"]
        with services.w.connect() as db:
            event = db.execute(
                "SELECT trace_id,span_id,node FROM agent_run_events WHERE run_id=? AND kind='stage'",
                (task["id"],),
            ).fetchone()
        assert event["trace_id"] == f"{calls[0].context.trace_id:032x}"
        assert event["span_id"] == f"{calls[0].context.span_id:016x}"
        assert event["node"] == "Checking posting"
    finally:
        runner.pool.shutdown(wait=True)


def test_worker_context_is_reset_before_the_next_profile(spans):
    def work():
        with telemetry.span("worker"):
            return telemetry.current_profile_root(), telemetry.current_run()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with telemetry.bind(profile_root="profile-one", run={"id": "one"}):
            assert telemetry.submit(pool, work).result() == (
                "profile-one",
                {"id": "one"},
            )
        with telemetry.bind(profile_root="profile-two", run={"id": "two"}):
            assert telemetry.submit(pool, work).result() == (
                "profile-two",
                {"id": "two"},
            )
        assert pool.submit(work).result() == (None, None)
