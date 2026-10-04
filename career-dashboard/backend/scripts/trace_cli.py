#!/usr/bin/env python3
"""Agent run timelines from the profile's local trace file (profiles/<id>/data/traces.db).

  career trace list [--limit N] [--profile <id>]   the latest traced runs: kind, length, AI and web calls, failures
  career trace show <run-id> [--profile <id>]      one run as a tree: every step, AI call and web request
  career trace graph <run-id> [--content]          a graph run checkpoint by checkpoint (alias: replay): which
                                                   node ran, what it changed, where a stopped run resumes
  career trace rerun <run-id> --checkpoint <id>    a research run again from that checkpoint, as a new run
                                                   (its earlier steps keep their results; the old run is unchanged)

Add --json for machine-readable output. Timelines hold no prompts or document text; graph steps show
types, sizes and hashes unless --content (this computer only) asks for the values.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

from backend.telemetry.sqlite_store import recent_runs, run_spans  # noqa: E402

SHOWN = (
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "career.ai.action",
    "career.ai.specialist",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "career.ai.cache_hit",
    "career.ai.web",
    "http.response.status_code",
    "url.full",
    "error.type",
    "career.run_state",
)


def when(ns) -> str:
    return (
        datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
        .astimezone()
        .strftime("%Y-%m-%d %H:%M:%S")
        if ns
        else "-"
    )


def length(ms) -> str:
    if ms is None:
        return "running"
    return f"{ms:.0f} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def tree(spans: list[dict]) -> list[tuple[int, dict]]:
    def identity(s):
        return s["trace_id"], s["span_id"]

    ids = {identity(s) for s in spans}
    children: dict[tuple[str, str] | str, list[dict]] = {}
    for s in spans:
        parent = (s["trace_id"], s["parent_span_id"])
        children.setdefault(parent if parent in ids else "", []).append(s)
    out: list[tuple[int, dict]] = []
    visited = set()

    def append(s, depth):
        key = identity(s)
        if key in visited:
            return
        visited.add(key)
        out.append((depth, s))
        walk(key, depth + 1)

    def walk(parent, depth: int) -> None:
        for s in children.get(parent, []):
            append(s, depth)

    walk("", 0)
    for s in spans:
        append(s, 0)
    return out


def print_graph(root, run_id: str, *, content: bool, as_json: bool) -> int:
    """A graph run's checkpoints (backend/graphs/history.py), oldest first."""
    from backend.graphs import history

    found = history.summary(root, run_id, content=content)
    if as_json:
        print(json.dumps(found, indent=2, ensure_ascii=False))
        return 0
    if not found["threads"]:
        print(f"No graph checkpoints for run {run_id} (only runs made by a graph have them).")
        return 1
    for thread in found["threads"]:
        state = "finished" if thread["finished"] else "stopped; resumes at " + ", ".join(thread["resumes_at"])
        print(f"{thread['thread']}  ({len(thread['steps'])} checkpoints, {state})")
        for step in thread["steps"]:
            changed = ", ".join(f"{key} ({value['type']}, {value['size']} chars)" for key, value in step["changed"].items())
            print(f"  {step['step']:>3}  {step['at'][11:19] if step['at'] else '':8}  "
                  f"{', '.join(step['ran']) or '(input)':<28} {changed}")
            for key, value in step["changed"].items() if content else ():
                print(f"         {key}: {value.get('preview', '')[:200]}")
            if step["next"]:
                print(f"         next: {', '.join(step['next'])}")
    print(found["note"])
    return 0


def rerun(root, run_id: str, checkpoint_id: str, *, as_json: bool) -> int:
    """Run a research graph run again from one checkpoint, here, and wait for it (graphs/executor.py)."""
    from career import Workspace

    from backend.services.agents import AgentRunner
    from backend.services.resume_studio import ResumeStudio
    from backend.services.workspace_v2 import CareerServices

    services = CareerServices(Workspace(root))
    with services.w.connect() as db:
        row = db.execute("SELECT kind, job_id FROM agent_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        print(f"No run {run_id} in this profile.")
        return 1
    runner = AgentRunner(services)
    runner.studio = ResumeStudio(services)
    try:
        queued = runner.enqueue(row["kind"], row["job_id"], rerun_from={"run_id": run_id, "checkpoint_id": checkpoint_id})
    except ValueError as problem:
        print(problem)
        return 1
    runner.pool.shutdown(wait=True)
    done = next(r for r in services.runs() if r["id"] == queued["id"])
    if as_json:
        print(json.dumps({"run_id": done["id"], "state": done["state"], "error": done.get("error")}, indent=2))
    else:
        print(f"Run {done['id']} {done['state']}" + (f": {done['error']}" if done.get("error") else "")
              + f". See it with  career trace graph {done['id']}")
    return 0 if done["state"] == "completed" else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["list", "show", "graph", "replay", "rerun"])
    parser.add_argument(
        "run_id", nargs="?", help="show, graph: the run ID from `career trace list` or the Agents tab"
    )
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--content", action="store_true", help="graph: include short previews of the values")
    parser.add_argument("--checkpoint", help="rerun: the checkpoint id from `career trace graph <run-id> --json`")
    parser.add_argument(
        "--profile", help="profile ID (required when several profiles exist)"
    )
    parser.add_argument(
        "--profiles-dir", help=argparse.SUPPRESS
    )  # tests: a disposable profiles folder
    args = parser.parse_args(argv)
    from backend.profiles import ProfileStore, store

    if args.profiles_dir:
        base = Path(args.profiles_dir).resolve()
        profiles = ProfileStore(base=base, legacy_root=base.parent / "no-legacy")
    else:
        profiles = store()
    choices = profiles.list() if not args.profile else []
    if not args.profile and len(choices) > 1:
        parser.error(
            "Several profiles exist. Pass --profile <id> to choose whose timeline to read."
        )
    profile_id = args.profile or (choices[0]["id"] if choices else None)
    if not profile_id:
        parser.error(
            'No profile exists yet. Run  career setup --name "Full Name"  or create one in the dashboard.'
        )
    root = profiles.root_for(profile_id)
    if args.command == "list":
        runs = recent_runs(root, max(1, min(args.limit, 200)))
        if args.json:
            print(json.dumps(runs, indent=2))
        elif not runs:
            print(
                "No traced runs yet. Runs are traced from now on; older runs show their steps in the Agents tab."
            )
        else:
            for run in runs:
                print(
                    f"{when(run['started_ns'])}  {run['run_id']:<34} {str(run['kind'] or '-'):<16} "
                    f"{length(run['duration_ms']):>9}  {run['ai_calls']} AI · {run['http_calls']} web · "
                    f"{run['errors']} failed"
                )
        return 0
    if not args.run_id:
        parser.error(f"{args.command} needs a run ID (see `career trace list`)")
    if args.command in {"graph", "replay"}:
        return print_graph(root, args.run_id, content=args.content, as_json=args.json)
    if args.command == "rerun":
        if not args.checkpoint:
            parser.error("rerun needs --checkpoint <id> (see `career trace graph <run-id> --json`)")
        return rerun(root, args.run_id, args.checkpoint, as_json=args.json)
    spans = run_spans(root, args.run_id)
    if args.json:
        print(json.dumps(spans, indent=2))
        return 0
    if not spans:
        print(f"No timeline for run {args.run_id}.")
        return 1
    for depth, span in tree(spans):
        mark = "x" if span["status"] == "ERROR" else "-"
        facts = ", ".join(
            f"{k.split('.')[-1]}={span['attributes'][k]}"
            for k in SHOWN
            if k in span["attributes"]
        )
        print(
            f"{'  ' * depth}{mark} {span['name']}  [{length(span['duration_ms'])}]"
            + (f"  {facts}" if facts else "")
        )
        if span["status"] == "ERROR" and span["status_message"]:
            print(f"{'  ' * depth}    failed: {span['status_message']}")
        for event in span["events"]:
            detail = ", ".join(f"{k}={v}" for k, v in event["attributes"].items())
            print(
                f"{'  ' * depth}    · {event['name']}"
                + (f" ({detail})" if detail else "")
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
