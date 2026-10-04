"""Tracing: every AI call, routing decision and web request becomes a span, without content.

The conftest ``spans`` fixture traces each test into memory. These tests check the
attributes a person needs to debug a run (provider, model, tokens, cache, HTTP status,
why a route moved on) and that prompts, CV text and secrets never reach a span.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import keep_traces_local, telemetry
from backend.ai import router
from backend.ai.providers import AIGateway
from backend.services import job_sources
from backend.telemetry.sqlite_store import SQLiteSpanExporter, recent_runs, run_spans

SECRET = "PRIVATE CV TEXT: Example Person, CONTACT-MARKER-0000"


def everything(spans) -> str:
    """All attributes and event attributes of the finished spans, as one string."""
    out = []
    for item in spans.get_finished_spans():
        out.append(json.dumps({k: str(v) for k, v in (item.attributes or {}).items()}))
        for event in item.events:
            out.append(
                json.dumps({k: str(v) for k, v in (event.attributes or {}).items()})
            )
    return "\n".join(out)


def finished(spans, name):
    return [s for s in spans.get_finished_spans() if s.name == name]


class Endpoint:
    id = "codex"

    def generate(self, prompt, schema, **options):
        telemetry.record_usage(120, 45)
        return {"summary": "done"}


def test_an_ai_call_is_one_span_with_provider_model_tokens_and_no_prompt(spans):
    gateway = object.__new__(AIGateway)
    gateway.s = SimpleNamespace()
    assert AIGateway.invoke_endpoint(
        gateway, Endpoint(), "sample", "role_research", SECRET, {"type": "object"}
    ) == {"summary": "done"}
    [call] = finished(spans, "chat sample")
    attributes = dict(call.attributes)
    assert (
        attributes["gen_ai.provider.name"] == "codex"
        and attributes["gen_ai.request.model"] == "sample"
    )
    assert (
        attributes["career.ai.action"] == "role_research"
        and attributes["career.ai.web"] is True
    )
    assert (
        attributes["gen_ai.usage.input_tokens"] == 120
        and attributes["llm.token_count.completion"] == 45
    )
    assert (
        attributes["career.ai.prompt_chars"] == len(SECRET)
        and attributes["career.ai.paid"] is False
    )
    assert [e.name for e in call.events] == ["slot_acquired"]
    assert "PRIVATE CV TEXT" not in everything(
        spans
    ) and "CONTACT-MARKER" not in everything(spans)


def test_a_failed_call_and_its_backup_are_both_on_the_timeline(spans, tmp_path):
    from test_hunt_paid_policy import gateway_stub

    gateway, calls = gateway_stub(tmp_path)
    assert gateway.generate(
        "document_review", "sample", {}, provider="codex", model="sample"
    ) == {"provider": "azure_openai"}
    attempts = finished(spans, "chat sample")
    assert [s.attributes["gen_ai.provider.name"] for s in attempts] == [
        "codex",
        "azure_openai",
    ]
    assert (
        attempts[0].status.status_code.name == "ERROR"
        and attempts[1].status.status_code.name == "UNSET"
    )
    assert attempts[1].attributes["career.ai.paid"] is True


class Book:
    def __init__(self):
        self.rested = []

    def resting(self, provider):
        return None

    def record(self, provider, ok, message=""):
        pass

    def rest_for_limit(self, provider, message, paid=False):
        self.rested.append(provider)


def test_the_route_records_why_it_moved_on(spans, tmp_path):
    def attempt(provider, model):
        if provider == "kimi_cli":
            raise ValueError(
                "Your Kimi membership's usage limit is reached. Wait for the window to reset."
            )
        return {"served": provider}

    with telemetry.span("ai.route"):
        result, served = router.route(
            tmp_path,
            tier="strong",
            attempt=attempt,
            book=Book(),
            ready_map={"kimi_cli": True, "codex": True},
        )
    assert served[0] == "codex" and result == {"served": "codex"}
    [route] = finished(spans, "ai.route")
    events = {e.name: dict(e.attributes) for e in route.events}
    assert events["route.plan"]["candidates"][:2] == ("kimi_cli", "codex")
    assert (
        events["route.failed"]["provider"] == "kimi_cli"
        and events["route.failed"]["limit"] is True
    )
    assert events["route.served"] == {
        "provider": "codex",
        "model": served[1],
        "after_failures": 1,
    }


def test_web_requests_are_spans_with_status_size_and_robots_refusals(spans):
    pages = {
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs": json.dumps(
            {"jobs": []}
        ),
        "https://example.ie/robots.txt": "User-agent: *\nDisallow: /careers\n",
    }

    class Response:
        def __init__(self, url, body):
            self.url, self.body, self.status, self.headers = (
                url,
                body.encode(),
                200,
                None,
            )

        def read(self, limit):
            return self.body

        def geturl(self):
            return self.url

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def opener(request, timeout):
        url = request.full_url
        if url not in pages:
            from urllib.error import HTTPError

            raise HTTPError(url, 404, "Not Found", {}, None)
        return Response(url, pages[url])

    fetcher = job_sources.Fetcher(opener=opener, min_interval=0, sleep=lambda s: None)
    assert fetcher.json("https://boards-api.greenhouse.io/v1/boards/acme/jobs") == (
        {"jobs": []},
        None,
    )
    with telemetry.span("feed.read"):
        assert fetcher.text("https://example.ie/careers/graduate")[0] is None
    [api] = finished(spans, "GET boards-api.greenhouse.io")
    assert api.attributes["http.response.status_code"] == 200 and api.attributes[
        "career.http.chars"
    ] == len('{"jobs": []}')
    assert (
        api.attributes["url.full"]
        == "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
    )
    [feed] = finished(spans, "feed.read")
    assert [e.name for e in feed.events] == ["robots_refused"]


def test_secrets_never_reach_a_span_url():
    assert (
        telemetry.safe_url("https://ie.jooble.org/api/0123456789abcdef0123456789abcdef")
        == "https://ie.jooble.org/api/REDACTED"
    )
    assert (
        telemetry.safe_url(
            "https://user:pass@example.com/v1/jobs?api_key=XYZ&q=data+analyst&page=2"
        )
        == "https://example.com/v1/jobs?api_key=REDACTED&q=data+analyst&page=2"
    )
    assert (
        telemetry.safe_url(
            "https://search.api.careerjet.net/v4/query?affid=abc&keywords=sql"
        )
        == "https://search.api.careerjet.net/v4/query?affid=REDACTED&keywords=sql"
    )
    assert (
        telemetry.safe_url("https://user:password@[::1]:6006/jobs?token=secret#secret")
        == "https://[::1]:6006/jobs?token=REDACTED"
    )
    assert telemetry.safe_url("https://example.ie:invalid/jobs") == ""
    assert telemetry.safe_url("file:///private/cv.txt") == ""


def test_failed_spans_never_record_the_exception_message_or_stacktrace(spans):
    with pytest.raises(ValueError, match="PRIVATE CV TEXT"):
        with telemetry.span("ai.request"):
            raise ValueError(SECRET + " https://example.ie/?api_key=PRIVATE-KEY")
    [failed] = finished(spans, "ai.request")
    assert failed.status.status_code.name == "ERROR"
    assert failed.status.description == "ValueError"
    assert dict(failed.events[0].attributes) == {
        "exception.type": "ValueError",
        "exception.message": "ValueError",
    }
    assert "PRIVATE" not in everything(spans) and "api_key" not in everything(spans)
    assert "exception.stacktrace" not in everything(spans)


def test_usage_metadata_records_only_nonnegative_integer_counts(spans):
    with telemetry.span("chat sample"):
        telemetry.record_usage(SECRET, -1)
    [call] = finished(spans, "chat sample")
    assert "gen_ai.usage.input_tokens" not in call.attributes
    assert "gen_ai.usage.output_tokens" not in call.attributes
    assert "PRIVATE" not in everything(spans)


def test_failed_routes_keep_safe_categories_instead_of_provider_content(
    spans, tmp_path
):
    def attempt(provider, model):
        if provider == "kimi_cli":
            raise ValueError("Usage limit reached. " + SECRET)
        return {"served": provider}

    with telemetry.span("ai.route"):
        router.route(
            tmp_path,
            tier="strong",
            attempt=attempt,
            book=Book(),
            ready_map={"kimi_cli": True, "codex": True},
        )
    [route] = finished(spans, "ai.route")
    failed = next(e for e in route.events if e.name == "route.failed")
    assert failed.attributes["reason"] == "AI plan limit reached"
    assert "PRIVATE" not in everything(spans)


def test_external_export_keeps_absolute_profile_paths_local(tmp_path):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from backend.telemetry.core import _ExternalExporter

    local, external = InMemorySpanExporter(), InMemorySpanExporter()
    provider = telemetry.make_provider(
        [local, _ExternalExporter(external)], synchronous=True
    )
    telemetry.use_provider(provider)
    try:
        with telemetry.bind(
            profile_root=tmp_path / "private-profile",
            run={"id": "run-1", "kind": "research"},
        ):
            with telemetry.span("ai.request"):
                pass
        assert "career.profile_root" in local.get_finished_spans()[0].attributes
        assert "career.profile_root" not in external.get_finished_spans()[0].attributes
        assert external.get_finished_spans()[0].attributes["career.run_id"] == "run-1"
    finally:
        provider.shutdown()


def test_cache_hits_are_visible_without_storing_the_prompt_or_answer(spans, tmp_path):
    from backend.services.agent_cache import AgentCache
    from test_hunt import ireland_profile

    cache = AgentCache(ireland_profile(tmp_path))
    calls = []

    def invoke(prompt, schema, **options):
        calls.append(prompt)
        return {"summary": SECRET}

    for _ in range(2):
        assert cache.execute(
            invoke,
            SECRET,
            {"required": ["summary"]},
            managed_budget=True,
            provider="codex",
            model="sample",
            action="document_review",
            web=False,
        ) == {"summary": SECRET}
    requests = finished(spans, "ai.request")
    assert len(calls) == 1
    assert [s.attributes["career.ai.cache_hit"] for s in requests] == [False, True]
    assert len({s.attributes["career.ai.cache_key"] for s in requests}) == 1
    assert "PRIVATE" not in everything(spans)


def test_root_spans_mark_saved_failures_without_exporting_private_errors(
    spans, tmp_path
):
    import sqlite3
    from backend.services.pipeline import Pipeline
    from backend.services.hunt import Hunt
    from backend.services.assistant import Assistant
    from backend.services.agents import AgentRunner

    root = tmp_path / "profile"
    root.mkdir()

    def connect():
        db = sqlite3.connect(root / "test.db")
        db.row_factory = sqlite3.Row
        return db

    workspace = SimpleNamespace(root=root, connect=connect)
    with connect() as db:
        for table in ("pipeline_runs", "hunt_runs", "agent_runs"):
            db.execute(f"CREATE TABLE {table}(id TEXT,kind TEXT,state TEXT,error TEXT)")
            db.execute(
                f"INSERT INTO {table} VALUES(?,?,?,?)",
                ("synthetic", "research", "failed", SECRET),
            )
    for cls in (Pipeline, Hunt):
        target = SimpleNamespace(w=workspace, _run=lambda run_id: None)
        cls._traced(target, "synthetic")
    Assistant._traced(
        SimpleNamespace(
            w=workspace,
            _process=lambda run_id: None,
            get=lambda run_id: {"state": "failed", "response": SECRET},
        ),
        "synthetic",
    )
    AgentRunner.run(
        SimpleNamespace(w=workspace, _run_worker=lambda run_id: None), "synthetic"
    )
    recorded = spans.get_finished_spans()
    assert len(recorded) == 4
    assert all(s.status.status_code.name == "ERROR" for s in recorded)
    assert all(s.attributes["career.run_state"] == "failed" for s in recorded)
    assert all(s.status.description == "Operation failed" for s in recorded)
    assert "PRIVATE" not in everything(spans)


def test_a_profile_and_run_follow_the_work_into_worker_threads(spans):
    seen = []

    def work(_item=None):
        with telemetry.span("worker.step"):
            seen.append(
                (
                    telemetry.current_profile_root(),
                    (telemetry.current_run() or {}).get("id"),
                    threading.current_thread().name,
                )
            )

    with telemetry.bind(
        profile_root="/profiles/example", run={"id": "run-1", "kind": "discovery"}
    ):
        with telemetry.span("agent.run discovery"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                telemetry.submit(pool, work).result()
                list(pool.map(telemetry.carry(work), [1, 2]))
    assert (
        all(root == "/profiles/example" and run == "run-1" for root, run, _ in seen)
        and len(seen) == 3
    )
    [parent] = finished(spans, "agent.run discovery")
    for step in finished(spans, "worker.step"):
        assert step.parent.span_id == parent.context.span_id
        assert (
            step.attributes["career.run_id"] == "run-1"
            and step.attributes["career.profile_root"] == "/profiles/example"
        )


def test_spans_are_stored_in_the_profiles_own_file_and_read_back_as_a_timeline(
    tmp_path,
):
    root = tmp_path / "profile"
    (root / "data").mkdir(parents=True)
    exporter = SQLiteSpanExporter(app_db=tmp_path / "app.db")
    telemetry.use_provider(telemetry.make_provider([exporter], synchronous=True))
    with telemetry.bind(profile_root=root, run={"id": "run-9", "kind": "research"}):
        with telemetry.span("agent.run research"):
            with telemetry.span("chat sample", **{"gen_ai.provider.name": "codex"}):
                telemetry.event("slot_acquired", wait_ms=3)
    with telemetry.bind(profile_root=tmp_path / "deleted-profile", run={"id": "gone"}):
        with telemetry.span("agent.run research"):
            pass
    timeline = run_spans(root, "run-9")
    assert [s["name"] for s in timeline] == ["agent.run research", "chat sample"]
    assert timeline[1]["parent_span_id"] == timeline[0]["span_id"]
    assert (
        timeline[1]["events"][0]["name"] == "slot_acquired"
        and "career.profile_root" not in timeline[1]["attributes"]
    )
    [summary] = recent_runs(root)
    assert (
        summary["run_id"] == "run-9"
        and summary["kind"] == "research"
        and summary["ai_calls"] == 1
    )
    # A deleted profile's folder is never recreated for its spans.
    assert (
        not (tmp_path / "deleted-profile").exists()
        and not (tmp_path / "app.db").exists()
    )


def test_the_trace_api_serves_a_runs_spans_and_its_stored_answer(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_hunt import ireland_profile

    from backend.dashboard.app import create_app

    services = ireland_profile(tmp_path)
    root = services.w.root
    telemetry.use_provider(
        telemetry.make_provider(
            [SQLiteSpanExporter(app_db=tmp_path / "app.db")], synchronous=True
        )
    )
    key = "a" * 64
    with telemetry.bind(profile_root=root, run={"id": "run-7", "kind": "research"}):
        with telemetry.span("ai.request", **{"career.ai.cache_key": key}):
            pass
    with services.w.connect() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS ai_cache(key TEXT PRIMARY KEY, result TEXT NOT NULL, created_at TEXT NOT NULL,"
            " web INTEGER NOT NULL, hits INTEGER NOT NULL DEFAULT 0)"
        )
        db.execute(
            "INSERT INTO ai_cache VALUES(?,?,?,?,0)",
            (key, json.dumps({"summary": "stored"}), "2026-10-03T10:00:00+00:00", 1),
        )
    with TestClient(create_app(root), base_url="http://127.0.0.1") as client:
        spans = client.get("/api/v2/agents/runs/run-7/spans").json()["spans"]
        assert [s["name"] for s in spans] == ["ai.request"]
        assert client.get("/api/v2/traces/runs").json()["runs"][0]["run_id"] == "run-7"
        assert client.get(f"/api/v2/traces/ai-result/{key}").json()["result"] == {
            "summary": "stored"
        }
        assert client.get("/api/v2/traces/ai-result/not-a-key").status_code == 400


def test_career_trace_lists_runs_and_shows_one_as_a_tree(tmp_path, capsys):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/scripts"))
    import trace_cli
    from backend.profiles import ProfileStore

    base = tmp_path / "profiles"
    store = ProfileStore(base=base, legacy_root=tmp_path / "no-legacy")
    root = store.root_for(store.create("Example Person")["id"])
    telemetry.use_provider(
        telemetry.make_provider(
            [SQLiteSpanExporter(app_db=tmp_path / "app.db")], synchronous=True
        )
    )
    with telemetry.bind(profile_root=root, run={"id": "run-3", "kind": "discovery"}):
        with telemetry.span("agent.run discovery"):
            with telemetry.span(
                "GET boards-api.greenhouse.io", **{"http.response.status_code": 404}
            ) as failing:
                telemetry.mark_failed(failing, "HTTP 404")
                telemetry.event("paced", host="boards-api.greenhouse.io", seconds=1.0)
    assert trace_cli.main(["list", "--profiles-dir", str(base)]) == 0
    listed = capsys.readouterr().out
    assert (
        "run-3" in listed
        and "discovery" in listed
        and "0 AI · 1 web · 1 failed" in listed
    )
    assert trace_cli.main(["show", "run-3", "--profiles-dir", str(base)]) == 0
    shown = capsys.readouterr().out.splitlines()
    assert shown[0].startswith("- agent.run discovery")
    assert (
        shown[1].startswith("  x GET boards-api.greenhouse.io")
        and "status_code=404" in shown[1]
    )
    assert "failed: HTTP 404" in shown[2] and "paced" in shown[3]
    assert trace_cli.main(["show", "missing", "--profiles-dir", str(base)]) == 1


def test_trace_cli_requires_a_profile_when_the_workspace_has_several(tmp_path):
    import trace_cli
    from backend.profiles import ProfileStore

    base = tmp_path / "profiles"
    profiles = ProfileStore(base=base, legacy_root=tmp_path / "no-legacy")
    first = profiles.create("Example Person")["id"]
    profiles.create("Other Example")["id"]
    with pytest.raises(SystemExit) as refusal:
        trace_cli.main(["list", "--profiles-dir", str(base)])
    assert refusal.value.code == 2
    assert (
        trace_cli.main(["list", "--profile", first, "--profiles-dir", str(base)]) == 0
    )


def test_hosted_llm_tracing_stays_off_unless_explicitly_allowed():
    env = {"LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true"}
    assert (
        keep_traces_local(env)["LANGSMITH_TRACING"] == "false"
        and env["LANGCHAIN_TRACING_V2"] == "false"
    )
    allowed = {"LANGSMITH_TRACING": "true", "CAREER_ALLOW_LANGSMITH": "1"}
    assert keep_traces_local(allowed)["LANGSMITH_TRACING"] == "true"
    import os

    assert (
        os.environ.get("LANGSMITH_TRACING") == "false"
    )  # importing the backend already did it
