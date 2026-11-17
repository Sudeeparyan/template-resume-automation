"""JobPrepGraph (v1): one saved job taken through the Daily Search helpers as a LangGraph graph.

Each helper the run switched on (services/pipeline.py ``STEP_IDS``: posting check, research,
tailor, PDF, review, study plan, ready check) is one node, in the pipeline's order, and each
node runs ``Pipeline._job_step``, the very code the classic loop runs, so both paths write the
same progress and stop a job for the same reasons. What the graph adds:

* a checkpoint after every helper (``data/agents.db``, thread ``job_prep:<pipeline run>:<job>``),
  so a run the app stopped resumes after the job's last finished helper instead of repeating it;
* one ``graph.node`` span per helper for the trace viewer.

Switched on with the ``graph_pipeline`` feature; tests run the pipeline both ways and compare.
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.graphs.executor import node, run_graph
from backend.graphs.runtime import GraphContext

NAME = "job_prep"


class PrepState(TypedDict, total=False):
    job_id: str
    stopped: str          # why the job's later helpers are skipped ("" while it goes on)
    opened: bool          # its application folder and Studio draft are open
    done: Annotated[list, operator.add]


def _helper(step: str):
    def run(state: PrepState, runtime) -> dict:
        hooks = runtime.context.flags
        after = hooks["pipeline"]._job_step(hooks["pipeline_id"], hooks["config"], hooks["progress"], hooks["job"],
                                            step, hooks["number"], hooks["total"],
                                            {"stopped": state.get("stopped") or None, "opened": bool(state.get("opened"))})
        return {"stopped": after["stopped"] or "", "opened": bool(after["opened"]), "done": [step]}
    return run


def build(steps: list[str]) -> StateGraph:
    from backend.services.pipeline import STEP

    graph = StateGraph(PrepState, context_schema=GraphContext)
    previous = START
    for step in steps:
        graph.add_node(step, node(step, STEP[step]["label"], _helper(step)))
        graph.add_edge(previous, step)
        previous = step
    graph.add_edge(previous, END)
    return graph


def run(pipeline, pipeline_id: str, config: dict, progress: dict, job: dict, steps: list[str], number: int,
        total: int) -> dict:
    """Run (or resume) one job's helpers; the job's progress entry is updated as the classic loop does."""
    hooks = {"pipeline": pipeline, "pipeline_id": pipeline_id, "config": config, "progress": progress, "job": job,
             "number": number, "total": total}
    # No agent run behind it: node spans only, no agent-run trace lines.
    context = GraphContext(services=pipeline.s, runner=None, run_id=f"{pipeline_id}:{job['id']}", flags=hooks)
    return run_graph(NAME, lambda: build(steps), context, {"job_id": job["id"], "stopped": "", "opened": False, "done": []},
                     root=pipeline.w.root)
