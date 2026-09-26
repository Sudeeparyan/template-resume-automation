"""Build a LangChain chat model for a chosen provider, model and tier.

Every hosted provider is reached through LangChain so the agent graph is written
once and the provider becomes a runtime choice. The local Codex runtime is not a
LangChain model and stays on its own path in ``providers.py``; it remains the
free option and the fallback when no key is configured.
"""

from __future__ import annotations

from pathlib import Path

from backend.ai import catalog, keys
from backend.paths import DEFAULT_PORT

# OpenRouter sends these on to the upstream provider for attribution.
OPENROUTER_HEADERS = {
    "HTTP-Referer": f"http://127.0.0.1:{DEFAULT_PORT}",
    "X-Title": "Portable career workspace",
}


class ProviderNotConfigured(ValueError):
    """Raised when a provider is selected but its key is absent."""


def available(root: Path) -> dict:
    """Which hosted providers have a usable key, without exposing any value."""
    present = keys.configured(root)
    found = {pid: present.get(spec["key"], False) for pid, spec in catalog.PROVIDERS.items()}
    # Azure also needs its endpoint and a deployment name before a call can work.
    found[catalog.AZURE] = found.get(catalog.AZURE, False) and not catalog.azure_settings(root)["missing"]
    return found


def build(root: Path, provider_id: str, model: str, *, temperature: float = 0.0,
          max_tokens: int = 4096, timeout: int = 120):
    """Return a configured LangChain chat model.

    Imports are local so that installing the app without the optional provider
    packages still allows the Codex path to run.
    """
    if provider_id not in catalog.PROVIDERS:
        raise ValueError(f"Unknown AI provider: {provider_id}")
    spec = catalog.PROVIDERS[provider_id]
    api_key = keys.secret(root, spec["key"])
    if not api_key:
        raise ProviderNotConfigured(
            f"{spec['label']} has no API key on this machine. Add {spec['key']} to .env."
        )

    if provider_id == catalog.AZURE:
        # Azure's v1 API is OpenAI-compatible: the key as the bearer token, the deployment as the model.
        from langchain_openai import ChatOpenAI

        azure = catalog.azure_settings(root)
        if azure["missing"]:
            raise ProviderNotConfigured(
                f"Azure OpenAI needs {' and '.join(azure['missing'])} in career-dashboard/.env."
            )
        # Reasoning deployments (GPT-6 Luna) accept only their default temperature, and take
        # structured-output tools only through the Responses API, not chat completions.
        return ChatOpenAI(model=model, api_key=api_key, base_url=azure["base_url"], use_responses_api=True,
                          max_tokens=max_tokens, timeout=timeout, max_retries=2)

    if provider_id in ("openrouter", "openai", "kimi"):
        from langchain_openai import ChatOpenAI

        options = {
            "model": model,
            "api_key": api_key,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout,
            "max_retries": 2,
        }
        if spec["base_url"]:
            options["base_url"] = spec["base_url"]
        if provider_id == "openrouter":
            options["default_headers"] = OPENROUTER_HEADERS
        return ChatOpenAI(**options)

    if provider_id == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, api_key=api_key, temperature=temperature,
                             max_tokens=max_tokens, timeout=timeout, max_retries=2)

    if provider_id == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, google_api_key=api_key,
                                      temperature=temperature, max_output_tokens=max_tokens,
                                      timeout=timeout, max_retries=2)

    raise ValueError(f"No LangChain binding for provider {provider_id}")


def structured(root: Path, provider_id: str, model: str, schema, **options):
    """A chat model that returns an instance of ``schema`` instead of free text."""
    return build(root, provider_id, model, **options).with_structured_output(schema)
