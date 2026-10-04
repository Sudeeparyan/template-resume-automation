"""Where everything lives.

One module answers "where is X", so a directory move is a change here rather
than in every caller. APP_ROOT is the career-dashboard folder regardless of how
deep the importing module sits.
"""

from __future__ import annotations

from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]

BACKEND = APP_ROOT / "backend"
FRONTEND = APP_ROOT / "frontend"
DATA = APP_ROOT / "data"
TESTS = APP_ROOT / "tests"
DOCS = APP_ROOT / "docs"

SCRIPTS = BACKEND / "scripts"
SERVICES = BACKEND / "services"
WORKFLOWS = BACKEND / "workflows"

CONFIG = DATA / "config"
CONTEXT = DATA / "context"
TEMPLATES = DATA / "templates"
OUTPUT = DATA / "output"

# Repository root, one level above the app: daily-job-search/ sits there.
REPO_ROOT = APP_ROOT.parent

# Every person's private workspace lives under profiles/<id>/data/.
# App-level data contains public reference assets only.
PROFILES = APP_ROOT / "profiles"
# Country packs: location terms, work-authorization gate, paper and spelling per country.
COUNTRIES = BACKEND / "countries"
# Public job-market data shared by every profile on this computer: postings read from
# public sources, never a person's fit, status or documents (backend/market/).
MARKET = DATA / "market"
MARKET_DB = MARKET / "market.db"
# Conditional-request cache (ETag / Last-Modified) for polite re-reads of public sources.
HTTP_CACHE_DB = DATA / "http_cache.db"

# The first-run market is Ireland. A profile's own `candidate.timezone` wins.
TIMEZONE = "Europe/Dublin"


def app_root_for(root) -> Path:
    """The folder holding code and secrets for a workspace rooted at `root`.

    A test workspace can be the app itself: it has a backend/
    folder. A profile folder holds only data, so its code, .env and keys are the app's.
    """
    root = Path(root).resolve()
    return root if (root / "backend").is_dir() else APP_ROOT


def secrets_root_for(root) -> Path:
    """Where the AI keys (.env, keys.txt) for a workspace are read.

    A profile folder (under profiles/) uses the app's local keys: they belong to
    the installation, not to a person. Other test roots keep their own.
    """
    root = Path(root).resolve()
    try:
        root.relative_to(PROFILES.resolve())
    except ValueError:
        return root
    return APP_ROOT

APP_ID = "portable-career-workspace"
APP_TITLE = "Career Workspace"
# Loopback port for run.py and the Vite dev proxy. The launcher verifies the
# health identity before opening or restarting anything on this port.
DEFAULT_PORT = 8000
