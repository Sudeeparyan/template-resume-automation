"""The tracer provider, span helpers and the context they need (see backend/telemetry/__init__.py)."""

from __future__ import annotations

import atexit
import contextvars
import os
import re
import sys
import threading
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:  # The app runs without tracing when the packages are missing.
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the packages
    AVAILABLE = False
    SpanProcessor = object  # type: ignore[assignment,misc]

SERVICE = "career-workspace"
# The profile folder and agent run a piece of work belongs to; copied onto every span.
_PROFILE_ROOT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "career_profile_root", default=None
)
_RUN: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "career_run", default=None
)

_provider = None
_lock = threading.Lock()
_auto = True  # setup on first use; tests turn this off and install their own provider


class _Tagger(SpanProcessor):
    """Copies the current profile and agent run onto every span, so each one can be routed and grouped."""

    def on_start(
        self, span, parent_context=None
    ):  # noqa: D401 - OpenTelemetry interface
        root = _PROFILE_ROOT.get()
        if root:
            span.set_attribute("career.profile_root", root)
        run = _RUN.get()
        if run:
            span.set_attribute("career.run_id", str(run.get("id", "")))
            if run.get("kind"):
                span.set_attribute("career.run_kind", str(run["kind"]))

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _default_exporters() -> list:
    from backend.telemetry.sqlite_store import SQLiteSpanExporter

    exporters = [SQLiteSpanExporter()]
    endpoint = os.environ.get("CAREER_OTEL_ENDPOINT", "").strip()
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            exporters.append(_ExternalExporter(OTLPSpanExporter(endpoint=endpoint)))
        except ImportError:
            print(
                "CAREER_OTEL_ENDPOINT is set but the OTLP exporter is not installed: "
                "pip install -r career-dashboard/backend/requirements-observability.txt",
                file=sys.stderr,
            )
    return exporters


def make_provider(exporters: list, *, synchronous: bool = False):
    """A tracer provider that tags spans with the profile and run, then hands them to ``exporters``.

    Synchronous export (tests) writes each span as it ends; the app batches them on a thread.
    """
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": SERVICE}), shutdown_on_exit=False
    )
    provider.add_span_processor(_Tagger())
    for exporter in exporters:
        provider.add_span_processor(
            SimpleSpanProcessor(exporter)
            if synchronous
            else BatchSpanProcessor(exporter)
        )
    return provider


def setup_tracing(exporters: list | None = None):
    """Install this process's tracer provider once (later calls return the same one)."""
    global _provider
    if not AVAILABLE:
        return None
    with _lock:
        if _provider is None:
            provider = make_provider(
                _default_exporters() if exporters is None else exporters
            )
            _provider = provider
            # Libraries that trace through the global provider (LangGraph instrumentation) share it.
            if not isinstance(trace.get_tracer_provider(), TracerProvider):
                trace.set_tracer_provider(provider)
            atexit.register(provider.shutdown)
        return _provider


def use_provider(provider, *, auto: bool = False):
    """Tests: trace into this provider (from make_provider; None turns tracing off) and stop automatic setup."""
    global _provider, _auto
    with _lock:
        _provider, _auto = provider, auto


def _tracer():
    if _provider is None and _auto:
        setup_tracing()
    return _provider.get_tracer("career") if _provider is not None else None


def _clean(value: Any):
    """OpenTelemetry attribute values: str, bool, int, float or a list of one of those."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set)):
        items = [v for v in value if isinstance(v, (bool, int, float, str))]
        return items or None
    return str(value)


@contextmanager
def span(name: str, **attributes) -> Iterator[Any]:
    """A span around a block; failures record a safe category, never exception content."""
    tracer = _tracer() if AVAILABLE else None
    if tracer is None:
        yield None
        return
    # SDK exception events contain the message and full traceback, either of which can
    # include an AI answer, a candidate's text or a request URL with credentials.
    with tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as current:
        set_attributes(current, **attributes)
        try:
            yield current
        except BaseException as error:
            category = safe_error(error)
            current.add_event(
                "exception",
                {"exception.type": type(error).__name__, "exception.message": category},
            )
            mark_failed(current, error)
            raise


def set_attributes(target=None, **attributes) -> None:
    """Attributes on ``target`` (default: the current span); None values are left out."""
    if not AVAILABLE:
        return
    target = target if target is not None else trace.get_current_span()
    if not target.is_recording():
        return
    for key, value in attributes.items():
        value = _clean(value)
        if value is not None:
            target.set_attribute(key.replace("__", "."), value)


def event(name: str, **attributes) -> None:
    """A timestamped event on the current span (a robots refusal, a cache hit, a route switch)."""
    if not AVAILABLE:
        return
    current = trace.get_current_span()
    if current.is_recording():
        current.add_event(
            name,
            {
                k: v
                for k, v in ((k, _clean(v)) for k, v in attributes.items())
                if v is not None
            },
        )


def record_usage(input_tokens=None, output_tokens=None, **extra) -> None:
    """Token counts an AI runtime reported, on the current AI span (GenAI and OpenInference names)."""
    # Usage is provider-controlled metadata; accept counts only, never a string
    # that a malformed provider response could use to smuggle answer content.
    input_tokens = (
        input_tokens if type(input_tokens) is int and input_tokens >= 0 else None
    )
    output_tokens = (
        output_tokens if type(output_tokens) is int and output_tokens >= 0 else None
    )
    set_attributes(
        **{
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "llm.token_count.prompt": input_tokens,
            "llm.token_count.completion": output_tokens,
        },
        **{"career.ai." + k: v for k, v in extra.items()},
    )


@contextmanager
def bind(profile_root=None, run: dict | None = None):
    """Work inside this block belongs to this profile folder and agent run."""
    tokens = []
    if profile_root is not None:
        tokens.append((_PROFILE_ROOT, _PROFILE_ROOT.set(str(profile_root))))
    if run is not None:
        tokens.append((_RUN, _RUN.set(dict(run))))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def current_profile_root() -> str | None:
    return _PROFILE_ROOT.get()


def current_run() -> dict | None:
    return _RUN.get()


def current_ids() -> tuple[str | None, str | None]:
    """The current trace/span IDs, for the local run-event log."""
    if not AVAILABLE:
        return None, None
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


def flush(timeout_millis: int = 500) -> bool:
    """Make completed spans visible to the local viewer without an unbounded wait."""
    return (
        bool(_provider.force_flush(timeout_millis)) if _provider is not None else True
    )


def submit(pool, fn, *args, **kwargs):
    """``pool.submit`` that carries the current profile, run and span into the worker thread."""
    return pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)


def carry(fn):
    """``fn`` for ``pool.map``: every call runs in its own copy of the caller's context."""
    parent = contextvars.copy_context()

    def run(*args, **kwargs):
        return parent.copy().run(fn, *args, **kwargs)

    return run


def mark_failed(target, message: Any) -> None:
    """A span whose work failed without raising (a run recorded as failed)."""
    if not AVAILABLE or target is None or not target.is_recording():
        return
    from opentelemetry.trace import Status, StatusCode

    target.set_status(Status(StatusCode.ERROR, safe_error(message)))


def safe_error(error: Any) -> str:
    """An error category from fixed wording only; provider output never enters a trace."""
    text = str(error or "").lower()
    http = re.search(r"\bhttp\s+([45]\d{2})\b", text)
    if http:
        return "HTTP " + http[1]
    for markers, category in (
        (
            ("rate limit", "quota", "usage limit", "out of credits"),
            "AI plan limit reached",
        ),
        (("paid", "budget"), "Paid AI budget blocked"),
        (
            ("timeout", "timed out", "could not be reached", "unreachable"),
            "Request unavailable or timed out",
        ),
        (("schema", "structured output"), "Structured output validation failed"),
        (("robots.txt",), "Robots policy refused the request"),
    ):
        if any(marker in text for marker in markers):
            return category
    return (
        type(error).__name__ if isinstance(error, BaseException) else "Operation failed"
    )


class _ExternalExporter:
    """Keep the absolute profile path (used only for local routing) off OTLP."""

    def __init__(self, exporter):
        self.exporter = exporter

    def export(self, spans):
        from opentelemetry.sdk.trace import ReadableSpan

        redacted = [
            ReadableSpan(
                name=s.name,
                context=s.context,
                parent=s.parent,
                resource=s.resource,
                attributes={
                    k: v
                    for k, v in (s.attributes or {}).items()
                    if k != "career.profile_root"
                },
                events=s.events,
                links=s.links,
                kind=s.kind,
                status=s.status,
                start_time=s.start_time,
                end_time=s.end_time,
                instrumentation_scope=s.instrumentation_scope,
            )
            for s in spans
        ]
        return self.exporter.export(redacted)

    def shutdown(self):
        return self.exporter.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self.exporter.force_flush(timeout_millis)


# Query parameters that carry credentials (never a search term such as "keywords").
_SECRET_PARAM = re.compile(
    r"(?i)^(key|apikey|api[_-]?key|token|access[_-]?token|secret|client[_-]?secret|password|passwd"
    r"|auth|authorization|signature|sig|credentials?|code|affid|user[_-]?ip|.*[_-](key|token|secret))$"
)
# Hosts that put an API key in the path (Jooble: /api/<key>).
_KEY_IN_PATH = re.compile(r"(?i)^(/api/)[^/]+")
_KEY_PATH_HOSTS = ("jooble.org",)


def safe_url(url: str) -> str:
    """A URL fit for a span: no user info, no secret query values, no key in a known API path."""
    try:
        parts = urlsplit(str(url or ""))
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https"):
        return ""
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    query = urlencode(
        [
            (k, "REDACTED" if _SECRET_PARAM.match(k) else v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    path = parts.path
    if any(host == h or host.endswith("." + h) for h in _KEY_PATH_HOSTS):
        path = _KEY_IN_PATH.sub(r"\1REDACTED", path)
    return urlunsplit((parts.scheme, netloc, path, query, ""))
