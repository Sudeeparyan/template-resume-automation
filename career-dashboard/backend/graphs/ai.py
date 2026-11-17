"""AI calls from graph nodes, through the agent runner.

``call_ai`` goes through ``AgentRunner.cached``: the answer cache, the gateway (Auto's route over
the free plans first, the paid gate), usage metering and the run's live trace all apply, exactly
as for the older hand-written steps. Each call is also a span named after its node; the trace
shows it under the node's own stage line.
"""

from __future__ import annotations

from backend import telemetry


def call_ai(context, node: str, prompt: str, schema: dict, *, action: str | None = None, web: bool = False,
            **options) -> dict:
    """One structured AI call for a graph node; ``action`` picks the route (backend/ai/providers.ACTIONS)."""
    if action:
        options["action"] = action
    if getattr(context, "fresh", False):
        options["refresh"] = True
    with telemetry.span(f"graph.ai {node}", **{"career.graph.node": node, "career.ai.web": bool(web)}):
        return context.runner.cached(prompt, schema, web=web, **options)
