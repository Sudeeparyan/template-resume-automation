"""Opt-in switches for the staged Ireland engine rollout.

Profile preference ``features`` is a mapping of names to booleans. The environment
``CAREER_FEATURES=tracker,-graph_pipeline`` overrides it for this process. Unknown
names fail closed: a typo must never enable a different feature.

A finished, tested part defaults to on, so the switch is its off switch (``-tracker``);
an unfinished one defaults to off, and switching it on does not finish it.
"""

from __future__ import annotations

import os
from types import MappingProxyType

REGISTRY = MappingProxyType(
    {
        "market_store": True,   # M3: postings recorded in data/market/market.db
        "tracker": True,        # M4: the Tracker page and its API
        "graph_pipeline": False,
        "graph_research": True,  # M5: verified company dossier + research graph (live-tested on Kimi, 2026-10-04)
        "graph_tracker_refresh": True,  # M6: six-hourly market refresh, no AI (live-tested, 2026-10-04)
        "evidence_rewrites": True,  # M5: guarded project-bullet rewording (services/rewrite_guard.py)
        "docx_export": True,    # M5: Word copies of the checked CV and cover letter
    }
)


def flags(services=None, *, environ=None) -> dict[str, bool]:
    """Resolve the profile's switches without mutating its saved preferences."""
    result = dict(REGISTRY)
    preferences = services.pref("features", {}) if services is not None else {}
    if isinstance(preferences, dict):
        for name, value in preferences.items():
            if name in result and isinstance(value, bool):
                result[name] = value
    env = os.environ if environ is None else environ
    for token in env.get("CAREER_FEATURES", "").split(","):
        token = token.strip()
        if not token:
            continue
        value = not token.startswith("-")
        name = token[1:] if token[0] in "+-" else token
        if name not in result:
            raise ValueError(f"Unknown CAREER_FEATURES switch: {name}")
        result[name] = value
    return result


def enabled(name: str, services=None, *, environ=None) -> bool:
    if name not in REGISTRY:
        raise ValueError(f"Unknown career feature: {name}")
    return flags(services, environ=environ)[name]
