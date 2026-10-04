"""Keep every portable test off this PC's own AI apps, the live job boards and Task Scheduler.

Only tests/portable is published (the rest of tests/ stays local), so the isolation the
suite depends on lives here. The fixture names match the local tests/conftest.py, so
when both exist this nearer copy is the one that runs, never both.

Codex and Kimi Code list the models a plan offers from files in their home folders
(~/.codex/models_cache.json, ~/.kimi-code/config.toml), and the Kimi card looks for the
VS Code extension. Each test starts with empty homes and no extension, so results never
depend on what the developer has installed; a test that needs a list writes its own.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "backend/scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.fixture(autouse=True)
def spans():
    """Every test traces into memory, never into this PC's data folder; a test can read its spans."""
    from backend import telemetry

    if not telemetry.AVAILABLE:
        yield None
        return
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = telemetry.make_provider([exporter], synchronous=True)
    telemetry.use_provider(provider)
    try:
        yield exporter
    finally:
        provider.shutdown()
        telemetry.use_provider(None)


@pytest.fixture(autouse=True)
def default_markets(monkeypatch):
    """Tests see the markets countries/markets.yml offers (Ireland), never a developer's CAREER_MARKETS."""
    monkeypatch.delenv("CAREER_MARKETS", raising=False)


@pytest.fixture(autouse=True)
def private_dete_cache(tmp_path_factory, monkeypatch):
    """Public-history lookups in tests never write the real shared DETE cache.

    The cache is built from the bundled public CSV only, so one copy serves the whole session
    (it rebuilds itself if a test changes what it is built from).
    """
    from backend.permits import history

    folder = tmp_path_factory.getbasetemp() / "dete-cache"
    folder.mkdir(exist_ok=True)
    monkeypatch.setattr(history, "DETE_DB", folder / "dete.db")


@pytest.fixture(autouse=True)
def private_market(tmp_path, monkeypatch):
    """Each test reads and writes its own market database and HTTP cache, never the shared ones."""
    from backend import paths

    monkeypatch.setattr(paths, "MARKET_DB", tmp_path / "market" / "market.db")
    monkeypatch.setattr(paths, "HTTP_CACHE_DB", tmp_path / "http_cache.db")


@pytest.fixture(autouse=True)
def no_hosted_tracing(monkeypatch):
    """Inherited developer settings must never upload synthetic or candidate test data."""
    from backend import HOSTED_TRACING

    monkeypatch.delenv("CAREER_ALLOW_LANGSMITH", raising=False)
    monkeypatch.delenv("CAREER_TRACE_CONTENT", raising=False)
    monkeypatch.delenv("CAREER_OTEL_ENDPOINT", raising=False)
    monkeypatch.delenv("CAREER_FEATURES", raising=False)
    for name in HOSTED_TRACING:
        monkeypatch.setenv(name, "false")


@pytest.fixture
def us_enabled(monkeypatch):
    """The dormant US market switched back on, for US and dual-market tests."""
    monkeypatch.setenv("CAREER_MARKETS", "ie,us")


@pytest.fixture(autouse=True)
def empty_ai_app_homes(tmp_path_factory, monkeypatch):
    from backend.ai import kimi_cli

    homes = tmp_path_factory.mktemp("ai-homes")
    monkeypatch.setenv("CODEX_HOME", str(homes / "codex"))
    monkeypatch.setenv("KIMI_CODE_HOME", str(homes / "kimi-code"))
    monkeypatch.setattr(kimi_cli, "EXTENSION", str(homes / "no-vscode-extension-*"))
    return homes


@pytest.fixture(autouse=True)
def no_job_source_keys(monkeypatch):
    """Careerjet or Jooble keys on this PC must not switch the optional sources on in tests;
    a test of those sources patches keys.load itself."""
    from backend.ai import keys

    real = keys.load
    for name in keys.SOURCE_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(keys, "load", lambda root: {k: v for k, v in real(root).items() if k not in keys.SOURCE_NAMES})


@pytest.fixture(autouse=True)
def no_auto_by_default(monkeypatch):
    """Auto (the router) would reach the Kimi, Codex and Claude apps installed on this PC.
    Tests keep the older built-in default instead; a test of Auto turns it back on."""
    from backend.ai import router

    monkeypatch.setattr(router, "DEFAULT_WHEN_UNSET", False)


@pytest.fixture(autouse=True)
def private_plan_health(tmp_path_factory, monkeypatch):
    """Rests and usage windows a test records never reach this PC's real .ai-plan-health.json."""
    from backend import paths
    from backend.ai import limits

    real, app = limits._path_for, paths.APP_ROOT.resolve()
    spare = tmp_path_factory.mktemp("plan-health")

    def redirect(root):
        path = real(root)
        try:
            path.resolve().relative_to(app)
        except ValueError:
            return path
        return spare / path.name

    monkeypatch.setattr(limits, "_path_for", redirect)


@pytest.fixture(autouse=True)
def no_task_scheduler(monkeypatch):
    """Tests never register, change or remove a real Windows scheduled task."""
    from backend.services import schedule_tasks

    calls = []

    def fake(script, timeout=60):
        calls.append(script)
        return 0, ""

    monkeypatch.setattr(schedule_tasks, "_powershell", fake)
    return calls


@pytest.fixture(autouse=True)
def offline_job_boards(monkeypatch):
    """Greenhouse, Lever and Ashby feeds stay offline; a test that needs one patches portals._get_json."""
    from backend.services import portals

    monkeypatch.setattr(portals, "_get_json", lambda url: (None, "offline in tests"))


@pytest.fixture(autouse=True)
def offline_job_sources(monkeypatch):
    """The feed harvester (services/job_sources.py) stays offline; a test that needs pages
    hands a Fetcher its own opener."""
    from backend.services import job_sources

    real = job_sources.Fetcher._raw

    def offline(self, url, data=None, accept="*/*", content_type=None, headers=None):
        if self.opener is not job_sources.urlopen:
            return real(self, url, data, accept, content_type, headers)
        return None, "", url, "offline in tests"

    monkeypatch.setattr(job_sources.Fetcher, "_raw", offline)


@pytest.fixture(autouse=True)
def rules_only_fit_check(monkeypatch):
    """The requirement check looks for a free AI plan on its own; tests stay on the rules path.
    A test of the AI check hands it a stub team or patches fit.fit_team itself."""
    from backend.services import fit

    real = fit.fit_team
    monkeypatch.setattr(fit, "fit_team", lambda services: None)
    return real


@pytest.fixture(autouse=True)
def template_cover_letters(monkeypatch):
    """Cover letters never reach the AI apps installed on this PC; a test of the AI writer
    passes its own stub team."""
    from backend.services import cover_letters

    monkeypatch.setattr(cover_letters, "writer_team", lambda services: None)
