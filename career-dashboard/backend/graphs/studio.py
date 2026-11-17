"""LangGraph Studio for these graphs, on the synthetic demo profile only.

Run from ``career-dashboard/`` (developers; see docs/OBSERVABILITY.md)::

    pip install -r backend/requirements-studio.txt
    langgraph dev

``langgraph.json`` points Studio at the graphs below. The real graphs take the app's live
services (an AgentRunner, the profile's database) through their LangGraph context, which Studio
cannot send as JSON. So each graph Studio opens is a one-node wrapper that runs the real graph
as its subgraph, with a context built here from the synthetic demo profile: Studio shows the
real graph's nodes, state and checkpoints inside that node.

The demo profile is made once in the ignored ``data/demo/studio/profiles`` (make_demo_profile)
and every run first checks it is marked synthetic (``require_synthetic_demo``); a real profile
is never opened. Its market store and HTTP cache live under ``data/demo/studio`` too, so Studio
never writes into the shared market data. AI calls use whatever AI app this computer has set
up, through the same router and limits as the app.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Callable

APP = Path(__file__).resolve().parents[2]
for _path in (str(APP), str(APP / "backend/scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from langgraph.graph import END, START, StateGraph  # noqa: E402

from backend.graphs import dossier as dossier_graph, research as research_graph  # noqa: E402
from backend.graphs.runtime import GraphContext  # noqa: E402

STUDIO = APP / "data" / "demo" / "studio"
# Test hooks: extra GraphContext fields (a page fetcher) for every run.
CONTEXT_OPTIONS: dict = {}


def demo_root(base: Path | None = None) -> Path:
    """The synthetic demo profile Studio runs on, made on first use; refused unless marked synthetic."""
    from make_demo_profile import make_demo, require_synthetic_demo

    from backend.paths import PROFILES
    from backend.profiles import ProfileStore

    base = Path(base or STUDIO / "profiles").resolve()
    if base == PROFILES.resolve() or base.is_relative_to(PROFILES.resolve()):
        raise ValueError("Studio runs on the synthetic demo only; it never opens users' profiles.")
    if not (base / "registry.json").exists():
        make_demo(base)
    store = ProfileStore(base=base, legacy_root=base.parent / "no-legacy")
    profiles = store.list()
    if len(profiles) != 1:
        raise ValueError(f"{base} must hold exactly the one synthetic demo profile.")
    root = Path(store.root_for(profiles[0]["id"]))
    require_synthetic_demo(root)
    return root


def make_runner(services):
    """The agent runner for a Studio run (tests replace it with one whose AI is a stub)."""
    from backend.services.agents import AgentRunner
    from backend.services.resume_studio import ResumeStudio

    runner = AgentRunner(services)
    runner.studio = ResumeStudio(services)
    return runner


def context(run_id: str | None = None) -> GraphContext:
    """A graph context on the demo profile, with its own market store and HTTP cache."""
    from career import Workspace

    from backend import paths
    from backend.services.workspace_v2 import CareerServices

    STUDIO.mkdir(parents=True, exist_ok=True)
    paths.MARKET_DB, paths.HTTP_CACHE_DB = STUDIO / "market.db", STUDIO / "http_cache.db"
    services = CareerServices(Workspace(demo_root()))
    return GraphContext(services=services, runner=make_runner(services), run_id=run_id or "studio-" + uuid.uuid4().hex,
                        **CONTEXT_OPTIONS)


def wrap(name: str, build: Callable, schema) -> object:
    """A compiled one-node graph whose node runs graph ``name`` as a subgraph on the demo profile."""
    inner = build().compile()

    def run(state):
        return inner.invoke(state, context=context())

    run.__name__ = name
    outer = StateGraph(schema)
    outer.add_node(name, run)
    outer.add_edge(START, name)
    outer.add_edge(name, END)
    return outer.compile()


def research():
    """Company research: dossier, hiring-manager view, requirement check, comparison (graphs/research.py)."""
    return wrap("research", research_graph.build, research_graph.ResearchState)


def dossier():
    """The verified company dossier on its own (graphs/dossier.py)."""
    return wrap("dossier", dossier_graph.build, dossier_graph.DossierState)
