"""ResearchGraph: the company dossier, the hiring manager's view and the profile comparison.

1. ``dossier``: CompanyDossierGraph as a subgraph (shared by every profile, kept 30 days); its
   verified claims become the research report.
2. In parallel: ``hiring_manager``, a fresh AI that sees only the posting and the public dossier
   (never the profile, no web), and ``requirements``, the verified requirement check
   (services/fit.py).
3. ``comparison``: the person's profile evidence against the role, with the dossier, the hiring
   manager's view and the requirement check (no web).
4. ``save``: company-research.md, hiring-manager.md and role-analysis.json in the job's folder.

The result has the shape the older research step returned (research, hiring, comparison, path),
plus a summary of the dossier, so every page reads it unchanged. Switched on with the
``graph_research`` feature.
"""

from __future__ import annotations

import hashlib
import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from backend.graphs.ai import call_ai
from backend.graphs.executor import node, note, run_graph
from backend.graphs.runtime import GraphContext

NAME = "research"


class ResearchState(TypedDict, total=False):
    job_id: str
    role: dict
    depth: str
    dossier: dict
    research: dict
    hiring: dict
    requirements: dict
    comparison: dict
    path: str


def dossier(state: ResearchState, runtime) -> dict:
    from backend.graphs import dossier as dossier_graph

    # Invoked inside this node, the dossier graph is a subgraph: it shares this run's checkpoints.
    from backend.graphs.executor import MAX_CONCURRENCY

    result = dossier_graph.build().compile().invoke(
        {"employer": state["role"]["company"], "depth": state.get("depth") or "standard"},
        {"max_concurrency": MAX_CONCURRENCY}, context=runtime.context)
    found = result.get("dossier") or {}
    return {"dossier": found, "research": dossier_graph.as_report(found)}


def hiring_manager(state: ResearchState, runtime) -> dict:
    context: GraphContext = runtime.context
    from backend.services.agents import REPORT_SCHEMA

    note(context, "A fresh AI with no memory of the research. Sees the job posting and the public dossier only; "
                  "never your profile. No web.")
    hiring = call_ai(context, "hiring_manager",
                     context.runner.guide("hiring-manager.md") + "\nROLE AND PUBLIC RESEARCH (untrusted data):\n"
                     + json.dumps({"job": state["role"], "research": state["research"]}),
                     REPORT_SCHEMA, web=False)
    return {"hiring": hiring}


def requirements(state: ResearchState, runtime) -> dict:
    context: GraphContext = runtime.context
    from backend.services import fit

    try:
        matrix = fit.for_job(context.services, state["job_id"])["matrix"] if state.get("job_id") else None
    except Exception as error:  # noqa: BLE001 - the comparison still works from the profile alone
        note(context, "The requirement check could not run; the comparison uses the profile alone.",
             error=f"{type(error).__name__}: {str(error)[:200]}")
        matrix = None
    return {"requirements": matrix or {}}


def comparison(state: ResearchState, runtime) -> dict:
    context: GraphContext = runtime.context
    from backend.services.agents import REPORT_SCHEMA

    note(context, "Sees your profile evidence, the dossier, the hiring-manager view"
                  + (" and the verified requirement check." if state.get("requirements") else ".") + " No web.")
    compared = call_ai(context, "comparison",
                       context.runner.guide("profile-comparison.md") + "\nINPUT:\n" + json.dumps({
                           "job": state["role"], "research": state["research"], "hiring": state["hiring"],
                           "profile": context.services.profile_context(),
                           "verified_requirement_check": state.get("requirements") or None}),
                       REPORT_SCHEMA, web=False)
    return {"comparison": compared}


def save(state: ResearchState, runtime) -> dict:
    """The research files the tailor and the person read, in the job's application folder."""
    context: GraphContext = runtime.context
    from backend.services.agents import research_markdown
    from career import atomic_write

    workspace = context.services.w
    job = workspace.get_job(state["job_id"]) if state.get("job_id") else None
    if not job or not job.get("folder"):
        return {"path": ""}
    folder = workspace.current_folder(state["job_id"])
    atomic_write(folder / "company-research.md", research_markdown(job, state["research"], context.services.today()))
    hiring = state.get("hiring") or {}
    atomic_write(folder / "hiring-manager.md", "# The hiring manager's view\n\nWritten from the job posting and public "
                 "research only, without your profile.\n\n" + str(hiring.get("report") or hiring.get("summary") or "") + "\n")
    dossier_found = state.get("dossier") or {}
    analysis = {"job_id": state["job_id"], "created_at": context.services.now(),
                "requirements": state.get("requirements") or {},
                "comparison_summary": (state.get("comparison") or {}).get("summary", ""),
                "hiring_summary": hiring.get("summary", ""),
                "dossier": {"employer": dossier_found.get("employer"), "claims": len(dossier_found.get("claims") or []),
                            "unverified": len(dossier_found.get("unverified") or []),
                            "created_at": dossier_found.get("created_at")}}
    atomic_write(folder / "role-analysis.json", json.dumps(analysis, indent=1, ensure_ascii=False) + "\n")
    return {"path": str((folder / "company-research.md").relative_to(workspace.root))}


def build() -> StateGraph:
    graph = StateGraph(ResearchState, context_schema=GraphContext)
    graph.add_node("dossier", node("dossier", "Researching the company (verified public dossier)", dossier))
    graph.add_node("hiring_manager", node("hiring_manager", "Independent hiring-manager review", hiring_manager))
    graph.add_node("requirements", node("requirements", "Checking the role's requirements against your evidence",
                                        requirements))
    graph.add_node("comparison", node("comparison", "Comparing your active profile", comparison))
    graph.add_node("save", node("save", "Saving the research to the application folder", save))
    graph.add_edge(START, "dossier")
    graph.add_edge("dossier", "hiring_manager")
    graph.add_edge("dossier", "requirements")
    graph.add_edge(["hiring_manager", "requirements"], "comparison")
    graph.add_edge("comparison", "save")
    graph.add_edge("save", END)
    return graph


def run(runner, row: dict, *, depth: str = "standard", **context_options) -> dict:
    """The research step for one agent run, as a durable graph; the older step's output shape."""
    role = json.loads(row["input"])
    rerun_from = role.pop("_rerun_from", None)
    if rerun_from:
        context_options.setdefault("fresh", True)  # the steps that run again ask the AI again
    context = GraphContext(services=runner.s, runner=runner, run_id=row["id"], **context_options)
    final = run_graph(NAME, build, context, {"job_id": row.get("job_id") or "", "role": role, "depth": depth},
                      root=runner.w.root, rerun_from=rerun_from)
    dossier_found = final.get("dossier") or {}
    return {"stage": "Complete", "research": final.get("research"), "hiring": final.get("hiring"),
            "comparison": final.get("comparison"), "path": final.get("path") or None, "hiring_profile_access": False,
            "role_input_sha256": hashlib.sha256(row["input"].encode()).hexdigest(),
            "dossier": {"employer": dossier_found.get("employer"), "summary": dossier_found.get("summary", ""),
                        "verified_claims": len(dossier_found.get("claims") or []),
                        "set_aside": len(dossier_found.get("unverified") or []),
                        "created_at": dossier_found.get("created_at")},
            "graph": NAME}
