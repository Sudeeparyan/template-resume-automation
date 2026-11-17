"""A graph run, checkpoint by checkpoint: which node ran, what it changed and what runs next.

Read straight from the profile's checkpoint file (graphs/checkpoint.py), so it works for every
graph without building it: the nodes that ran between two checkpoints are those whose
``versions_seen`` changed, what they changed is the difference in the state's values, and what
runs next is in the ``branch:to:<node>`` and ``join:...:<node>`` channels. A run stopped mid-way
shows the node it will resume at.

Values are redacted by default: each changed key gives its type, size and a short hash, so a trace
can be read (or shared for help) without the person's data. ``content=True`` adds a short preview
of each value; the dashboard offers that only on this computer ("Show my data").
"""

from __future__ import annotations

import hashlib
import json

from backend.graphs import checkpoint

PREVIEW = 600


INTERNAL = ("branch:", "join:", "__")


def _public(values: dict) -> dict:
    return {key: value for key, value in (values or {}).items() if not key.startswith(INTERNAL)}


def _upcoming(raw: dict) -> list[str]:
    """Nodes a checkpoint has triggered: ``branch:to:<node>``, and ``join:<a>+<b>:<node>`` once every
    parallel branch it waits for has finished (the join's value names them; it is emptied when the
    node runs, so a finished run has none)."""
    nodes = {key[len("branch:to:"):] for key in raw if key.startswith("branch:to:")}
    for key, value in raw.items():
        if key.startswith("join:"):
            sources, target = key[len("join:"):].rsplit(":", 1)
            if isinstance(value, (set, frozenset, list, tuple)) and set(sources.split("+")) <= set(value):
                nodes.add(target)
    return sorted(nodes)


def _describe(value, content: bool) -> dict:
    text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    out = {"type": type(value).__name__, "size": len(text), "sha": hashlib.sha256(text.encode()).hexdigest()[:10]}
    if content:
        out["preview"] = text[:PREVIEW] + ("…" if len(text) > PREVIEW else "")
    return out


def threads_for(root, run_id: str) -> list[str]:
    """The checkpoint threads of one run: ``<graph>:<run id>`` (and a pipeline job's ``job_prep:<run>:<job>``)."""
    return [thread for thread in checkpoint.threads(root)
            if thread.split(":", 1)[-1] == run_id or thread.split(":", 1)[-1].startswith(run_id + ":")]


def history(root, thread_id: str, *, content: bool = False) -> list[dict]:
    """The thread's checkpoints, oldest first, each with the nodes that ran, the changes and what is next."""
    saver = checkpoint.saver(root)
    items = list(saver.list({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}))
    # The current branch only: from the latest checkpoint back through its parents. A rerun copies an
    # earlier run and forks it, and the copied steps after the fork belong to that earlier run.
    by_id = {item.checkpoint["id"]: item for item in items}
    lineage, current = [], items[0] if items else None
    while current is not None and len(lineage) < len(items):
        lineage.append(current)
        parent = (current.parent_config or {}).get("configurable", {}).get("checkpoint_id")
        current = by_id.get(parent)
    items = list(reversed(lineage))
    steps, before, seen_before, tasks_before = [], {}, {}, None
    for item in items:
        raw = item.checkpoint.get("channel_values") or {}
        values, seen = _public(raw), item.checkpoint.get("versions_seen") or {}
        ran = sorted(node for node, versions in seen.items()
                     if not node.startswith("__") and seen_before.get(node) != versions)
        changed = {key: _describe(value, content) for key, value in values.items()
                   if key not in before or before[key] != value}
        upcoming = _upcoming(raw)
        # Fanned-out tasks (Send) queued at this checkpoint have no branch channel of their own.
        tasks = (item.checkpoint.get("channel_versions") or {}).get("__pregel_tasks")
        if tasks is not None and tasks != tasks_before:
            upcoming.append("(parallel tasks)")
        steps.append({"checkpoint_id": item.checkpoint["id"], "step": item.metadata.get("step"),
                      "source": item.metadata.get("source"), "at": item.checkpoint.get("ts"), "ran": ran,
                      "changed": changed, "removed": sorted(set(before) - set(values)), "next": upcoming})
        before, seen_before, tasks_before = values, seen, tasks
    return steps


def summary(root, run_id: str, *, content: bool = False) -> dict:
    """Every checkpoint thread of one run with its steps; ``finished`` is False while a node is still to run."""
    out = []
    for thread in threads_for(root, run_id):
        steps = history(root, thread, content=content)
        out.append({"thread": thread, "graph": thread.split(":", 1)[0], "steps": steps,
                    "finished": bool(steps) and not steps[-1]["next"],
                    "resumes_at": steps[-1]["next"] if steps and steps[-1]["next"] else []})
    return {"run_id": run_id, "threads": out,
            "note": "Each step is a checkpoint; a stopped run resumes at the node it names. "
                    + ("Values are shown cut short." if content else "Values are redacted: type, size and a short hash.")}
