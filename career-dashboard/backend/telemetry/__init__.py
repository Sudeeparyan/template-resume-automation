"""Tracing for every agent run, AI call and web request (OpenTelemetry).

Spans are written to a local SQLite file: profiles/<id>/data/traces.db for a profile's
work, data/traces/app.db for anything else. The Agents tab and ``career trace`` show
them as a timeline. Setting CAREER_OTEL_ENDPOINT (for example
http://127.0.0.1:6006/v1/traces for a local Arize Phoenix) also exports them over OTLP;
see docs/OBSERVABILITY.md.

Spans never carry prompt or response text, CV content or contact details: only IDs,
sizes, hashes, timings, providers, models and public posting fields. A span's
``career.ai.cache_key`` lets the person's own viewer open the stored response locally.

Without the opentelemetry packages every helper here is a no-op, so the app still runs.
"""

from backend.telemetry.core import (  # noqa: F401
    AVAILABLE,
    bind,
    carry,
    current_ids,
    current_profile_root,
    current_run,
    event,
    flush,
    make_provider,
    mark_failed,
    record_usage,
    safe_error,
    safe_url,
    set_attributes,
    setup_tracing,
    span,
    submit,
    use_provider,
)
