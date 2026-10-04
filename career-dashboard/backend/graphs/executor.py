"""Run one graph for one agent run, durably.

Each run is one checkpoint thread, ``<graph>:<run id>``. Starting a run whose thread already has
an unfinished checkpoint resumes it (input ``None``) instead of starting over, so a run stopped
by a restart or a retried attempt continues after its last finished node; a finished thread
returns its saved result without running again. A checkpoint is written after every step
(``durability="sync"``).

Every node is a span (``graph.node <name>``) for the trace viewer's waterfall, and announces
itself on the run's live trace (``agent_run_events``) from its own thread, so the lines of its
AI calls stay under it, also when parallel branches run at once. The Agents tab and the morning
run read graph runs like the older steps.
"""

from __future__ import annotations

from typing import Callable

from backend import telemetry
from backend.graphs import checkpoint


def thread_id(graph: str, run_id: str) -> str:
    return f"{graph}:{run_id}"


# Parallel branches at once. Each AI app takes one call at a time (services/task_execution.py), so
# a wider fan-out (four dossier facets) would only queue more calls behind one slot.
MAX_CONCURRENCY = 2


def config_for(graph: str, run_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id(graph, run_id)}, "recursion_limit": 60,
            "max_concurrency": MAX_CONCURRENCY}


def node(name: str, label: str, fn: Callable) -> Callable:
    """A graph node that is a span and a stage line on the run's trace (``label`` is in plain words)."""
    def run(state, runtime):
        context = runtime.context
        with telemetry.span(f"graph.node {name}", **{"career.graph.node": name, "career.run_id": context.run_id}):
            if context.runner is not None and context.run_id:
                context.runner.trace.stage = label
                context.runner.trace_event("stage", label, run_id=context.run_id)
            return fn(state, runtime)
    run.__name__ = name
    return run


def note(context, label: str, **detail) -> None:
    """A line for the run's live trace from inside a node."""
    if context.runner is not None and context.run_id:
        context.runner.trace_event("note", label, run_id=context.run_id, **detail)


def run_graph(name: str, build: Callable, context, start: dict, *, root, rerun_from: dict | None = None) -> dict:
    """Run (or resume) graph ``name`` for ``context.run_id``; returns the final state.

    ``rerun_from`` ({run_id, checkpoint_id}) starts this run as a copy of an earlier run of the same
    graph, forked at that checkpoint: the steps before it keep their saved results and the steps from
    it on run again ("Rerun from this step"). The earlier run and its checkpoints are left unchanged.
    """
    graph = build().compile(checkpointer=checkpoint.saver(root))
    config = config_for(name, context.run_id)
    if rerun_from and not graph.get_state(config).values:
        _fork(graph, name, context, root, rerun_from)
    saved = graph.get_state(config)
    if saved and saved.values and not saved.next:
        return dict(saved.values)  # finished before: never run twice
    resuming = bool(saved and saved.next)
    if resuming:
        note(context, "Resuming after the last finished step.", next=list(saved.next))
    with telemetry.span(f"graph.run {name}", **{"career.graph": name, "career.run_id": context.run_id,
                                                 "career.graph.resumed": resuming}):
        graph.invoke(None if resuming else start, config, context=context, durability="sync")
    if context.runner is not None:
        context.runner.trace_flush(context.run_id)
    return dict(graph.get_state(config).values)


def _fork(graph, name: str, context, root, rerun_from: dict) -> None:
    """Copy the earlier run's thread to this run's and fork it at the chosen checkpoint (LangGraph's
    ``__copy__`` update: the same state without that step's saved results, so its nodes run again)."""
    source = thread_id(name, str(rerun_from.get("run_id") or ""))
    point = str(rerun_from.get("checkpoint_id") or "")
    found = graph.get_state({"configurable": {"thread_id": source, "checkpoint_id": point}})
    if not found or not found.values or found.config["configurable"].get("checkpoint_id") != point:
        raise ValueError("That step is not among the earlier run's checkpoints (they are kept for 30 days).")
    if not found.next:
        raise ValueError("Nothing runs after that step; choose an earlier one.")
    target = thread_id(name, context.run_id)
    checkpoint.copy_thread(root, source, target)
    graph.update_state({"configurable": {"thread_id": target, "checkpoint_id": point}}, None, as_node="__copy__")
    note(context, f"Running again from {', '.join(found.next)}: a copy of run {rerun_from['run_id'][:8]}, whose "
                  "earlier steps keep their results. That run is unchanged.", source_run=rerun_from["run_id"])
