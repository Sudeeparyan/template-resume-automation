"""Keep every test off this PC's own AI apps and off the live job boards.

Codex and Kimi Code list the models a plan offers from files in their home
folders (~/.codex/models_cache.json, ~/.kimi-code/config.toml), and the Kimi
card looks for the VS Code extension. Each test starts with empty homes and no
extension, so results never depend on what the developer has installed; a test
that needs a list writes its own.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def empty_ai_app_homes(tmp_path_factory, monkeypatch):
    from backend.ai import kimi_cli

    homes = tmp_path_factory.mktemp("ai-homes")
    monkeypatch.setenv("CODEX_HOME", str(homes / "codex"))
    monkeypatch.setenv("KIMI_CODE_HOME", str(homes / "kimi-code"))
    monkeypatch.setattr(kimi_cli, "EXTENSION", str(homes / "no-vscode-extension-*"))
    return homes


@pytest.fixture(autouse=True)
def no_auto_by_default(monkeypatch):
    """Auto (the router) is the app's default when Settings holds no choice, and it would
    reach the Kimi, Codex and Claude apps installed on this PC. Tests keep the older
    built-in default instead; a test of Auto chooses it, or turns this back on."""
    from backend.ai import router

    monkeypatch.setattr(router, "DEFAULT_WHEN_UNSET", False)


@pytest.fixture(autouse=True)
def private_plan_health(tmp_path_factory, monkeypatch):
    """Rests and usage windows a test records never reach this PC's real
    .ai-plan-health.json: a test working on the app folder gets a throwaway copy."""
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
    """Tests never register, change or remove a real Windows scheduled task.
    Every PowerShell script the code would run is recorded instead."""
    from backend.services import schedule_tasks

    calls = []

    def fake(script, timeout=60):
        calls.append(script)
        return 0, ""

    monkeypatch.setattr(schedule_tasks, "_powershell", fake)
    return calls


@pytest.fixture(autouse=True)
def offline_job_boards(monkeypatch):
    """Discovery reads each found posting's full text from its Greenhouse, Lever or Ashby feed;
    tests never go online for it. A test that needs a feed patches portals._get_json itself."""
    from backend.services import portals

    monkeypatch.setattr(portals, "_get_json", lambda url: (None, "offline in tests"))


@pytest.fixture(autouse=True)
def offline_job_sources(monkeypatch):
    """The feed harvester (services/job_sources.py) reads Workday, SmartRecruiters and the job
    boards over HTTP; tests never go online. A test that needs a page hands a Fetcher its own opener."""
    from backend.services import job_sources

    real = job_sources.Fetcher._raw

    def offline(self, url, data=None, accept="*/*", content_type=None):
        if self.opener is not job_sources.urlopen:
            return real(self, url, data, accept, content_type)
        return None, "", url, "offline in tests"

    monkeypatch.setattr(job_sources.Fetcher, "_raw", offline)


@pytest.fixture(autouse=True)
def rules_only_fit_check(monkeypatch):
    """The requirement check (services/fit.py) looks for a free AI plan on its own, whatever
    Settings says, so it would reach the apps installed on this PC. Tests keep it on the
    rules path; a test of the AI check hands it a stub team or patches fit.fit_team itself."""
    from backend.services import fit

    real = fit.fit_team
    monkeypatch.setattr(fit, "fit_team", lambda services: None)
    return real
