"""Live model catalogues.

Model identifiers change faster than this repository does, so the selectable
models are read from each provider rather than hard-coded. Results are cached on
disk because the catalogue only moves when a provider ships something new.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from backend.ai import keys

CACHE_TTL_SECONDS = 24 * 3600

# Two service tiers. The supervisor and anything that writes candidate-facing
# prose use `strong`; extraction, verification and classification use `cheap`.
TIERS = ("strong", "cheap")

PROVIDERS = {
    "openrouter": {
        "label": "OpenRouter",
        "key": "OPENROUTER_API_KEY",
        "url": "https://openrouter.ai/api/v1/models",
        "base_url": "https://openrouter.ai/api/v1",
        "defaults": {"strong": "anthropic/claude-sonnet-5", "cheap": "google/gemini-2.5-flash-lite"},
    },
    "openai": {
        "label": "OpenAI",
        "key": "OPENAI_API_KEY",
        "url": "https://api.openai.com/v1/models",
        "base_url": None,
        "defaults": {"strong": "gpt-5.2", "cheap": "gpt-5-mini"},
    },
    "anthropic": {
        "label": "Claude",
        "key": "ANTHROPIC_API_KEY",
        "url": "https://api.anthropic.com/v1/models",
        "base_url": None,
        "defaults": {"strong": "claude-sonnet-5", "cheap": "claude-haiku-4-5"},
    },
    "gemini": {
        "label": "Gemini",
        "key": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "base_url": None,
        "defaults": {"strong": "gemini-pro-latest", "cheap": "gemini-3.5-flash-lite"},
    },
    "kimi": {
        "label": "Kimi (Moonshot)",
        "key": "MOONSHOT_API_KEY",
        "url": "https://api.moonshot.ai/v1/models",
        "base_url": "https://api.moonshot.ai/v1",
        "defaults": {"strong": "kimi-k2-0905-preview", "cheap": "kimi-k2-0905-preview"},
    },
    # Azure OpenAI / Azure AI Foundry. The model is a deployment on her own Azure
    # resource, so the endpoint and deployment names come from .env
    # (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT) rather than a fixed list.
    "azure_openai": {
        "label": "Azure OpenAI",
        "key": "AZURE_OPENAI_API_KEY",
        "url": None,
        "base_url": None,
        "defaults": {"strong": "", "cheap": ""},
    },
}
AZURE = "azure_openai"


def azure_settings(root: Path | None = None) -> dict:
    """The Azure endpoint as its OpenAI-compatible v1 base URL, and the deployment names, from .env."""
    from backend.paths import APP_ROOT

    root = Path(root) if root is not None else APP_ROOT
    endpoint = keys.secret(root, "AZURE_OPENAI_ENDPOINT").strip().rstrip("/")
    deployments = [name.strip() for name in keys.secret(root, "AZURE_OPENAI_DEPLOYMENT").split(",") if name.strip()]
    base = endpoint if endpoint.endswith("/openai/v1") else (endpoint + "/openai/v1" if endpoint else "")
    missing = [name for name, value in (("AZURE_OPENAI_ENDPOINT", endpoint), ("AZURE_OPENAI_DEPLOYMENT", deployments))
               if not value]
    return {"endpoint": endpoint, "base_url": base + "/" if base else "", "deployments": deployments, "missing": missing}


def azure_deployments(root: Path | None = None) -> list:
    """Every deployment a call may name: the .env ones first, then the ones Azure listed (cached)."""
    from backend.paths import APP_ROOT

    root = Path(root) if root is not None else APP_ROOT
    listed = (_read_cache(root).get(AZURE) or {}).get("models") or []
    return list(dict.fromkeys([*azure_settings(root)["deployments"], *listed]))


def azure_details(root: Path | None = None) -> dict:
    """Deployment name -> the model it runs (e.g. "gpt-5.1-eu" -> "gpt-5.1"), from the cached list."""
    from backend.paths import APP_ROOT

    root = Path(root) if root is not None else APP_ROOT
    return (_read_cache(root).get(AZURE) or {}).get("details") or {}


# The data-plane deployments list. Newer api-versions dropped it; this one still answers.
AZURE_DEPLOYMENTS_PATH = "/openai/deployments?api-version=2022-12-01"


def _fetch_azure(root: Path, api_key: str) -> tuple[list, dict]:
    """The deployments that finished provisioning on her Azure resource, and the model each runs."""
    endpoint = azure_settings(root)["endpoint"]
    if endpoint.endswith("/openai/v1"):
        endpoint = endpoint[: -len("/openai/v1")]
    data = _request(endpoint + AZURE_DEPLOYMENTS_PATH, {"api-key": api_key}, timeout=8)
    ready = [row for row in data.get("data", []) if row.get("id") and row.get("status", "succeeded") == "succeeded"]
    return sorted(row["id"] for row in ready), {row["id"]: row.get("model") or row["id"] for row in ready}


def _request(url: str, headers: dict, timeout: int = 20) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch(provider_id: str, api_key: str) -> list:
    spec = PROVIDERS[provider_id]
    if provider_id == "gemini":
        data = _request(spec["url"] + "?key=" + api_key, {})
        return sorted(m["name"].split("/")[-1] for m in data.get("models", []))
    if provider_id == "anthropic":
        data = _request(spec["url"], {"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        return sorted(m["id"] for m in data.get("data", []))
    data = _request(spec["url"], {"Authorization": "Bearer " + api_key})
    return sorted(m["id"] for m in data.get("data", []))


def _cache_file(root: Path) -> Path:
    # Which models an account offers is machine-wide, so every profile shares the app's list.
    from backend.paths import secrets_root_for

    return secrets_root_for(root) / "data" / "model-catalog.json"


def _read_cache(root: Path) -> dict:
    path = _cache_file(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cache(root: Path, cache: dict) -> None:
    path = _cache_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def models(root: Path, provider_id: str, refresh: bool = False) -> dict:
    """Selectable models for one provider, with the reason when none are listed."""
    spec = PROVIDERS[provider_id]
    api_key = keys.secret(root, spec["key"])
    if not api_key:
        return {"models": [], "configured": False, "error": None, "fetched_at": None}
    azure = azure_settings(root) if provider_id == AZURE else None
    if azure and azure["missing"]:
        return {"models": azure["deployments"], "configured": False,
                "error": "Add " + " and ".join(azure["missing"]) + " to career-dashboard/.env",
                "fetched_at": None}

    cache = _read_cache(root)
    entry = cache.get(provider_id) or {}

    def shown(listed: list) -> list:
        # Azure: the deployments named in .env always come first, then the ones Azure lists.
        return list(dict.fromkeys([*azure["deployments"], *listed])) if azure else listed

    fresh = entry.get("fetched_at", 0) + CACHE_TTL_SECONDS > time.time()
    if entry.get("models") and fresh and not refresh:
        return {"models": shown(entry["models"]), "configured": True, "error": None,
                "fetched_at": entry["fetched_at"], "cached": True}

    details = None
    try:
        if azure:
            listed, details = _fetch_azure(root, api_key)
        else:
            listed = _fetch(provider_id, api_key)
    except urllib.error.HTTPError as error:
        # A stale list beats no list; the caller still sees why the refresh failed.
        return {"models": shown(entry.get("models", [])), "configured": True,
                "error": f"{spec['label']} returned HTTP {error.code}",
                "fetched_at": entry.get("fetched_at")}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as error:
        return {"models": shown(entry.get("models", [])), "configured": True,
                "error": f"{spec['label']} model list unavailable ({type(error).__name__})",
                "fetched_at": entry.get("fetched_at")}

    cache[provider_id] = {"models": listed, "fetched_at": int(time.time()),
                          **({"details": details} if details is not None else {})}
    _write_cache(root, cache)
    return {"models": shown(listed), "configured": True, "error": None,
            "fetched_at": cache[provider_id]["fetched_at"], "cached": False}


def default_model(provider_id: str, tier: str, root: Path | None = None) -> str:
    if provider_id == AZURE:
        return (azure_settings(root)["deployments"] or [""])[0]
    return PROVIDERS[provider_id]["defaults"][tier]


def defaults(provider_id: str, root: Path | None = None) -> dict:
    return {tier: default_model(provider_id, tier, root) for tier in TIERS}
