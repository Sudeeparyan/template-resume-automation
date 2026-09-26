"""Server-only OpenAI, Anthropic, Codex and Claude Code structured generation providers."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.ai import claude_code, codex, kimi_cli, router


ACTIONS = {
    "resume_chat": {"structured": True, "web": False, "apps": False},
    "profile_chat": {"structured": True, "web": False, "apps": False},
    "discovery": {"structured": True, "web": True, "apps": False},
    "role_research": {"structured": True, "web": True, "apps": False},
    "requirement_extraction": {"structured": True, "web": False, "apps": False},
    "document_review": {"structured": True, "web": False, "apps": False},
    "email": {"structured": True, "web": False, "apps": True},
}


def _project_env(root: Path) -> dict[str, str]:
    from backend.paths import secrets_root_for

    path = secrets_root_for(root) / ".env"
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _secret(root: Path, name: str) -> str:
    # The same lookup as the Settings page: environment, then career-dashboard/.env
    # (where keys typed on that page go), then the root .env and keys.txt.
    from backend.ai import keys

    value = os.environ.get(name) or _project_env(root).get(name, "") or keys.secret(root, name)
    # An Azure key filed as OPENAI_API_KEY belongs to Azure OpenAI, never api.openai.com.
    return "" if name == "OPENAI_API_KEY" and keys.AZURE_KEY.fullmatch(value) else value


def _json_request(url: str, payload: dict, headers: dict, timeout: int = 90) -> dict:
    request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"AI provider returned HTTP {exc.code}") from None
    except (URLError, TimeoutError):
        raise ValueError("AI provider could not be reached before the timeout") from None


class OpenAIProvider:
    id = "openai"
    models = ("gpt-5.4", "gpt-5.4-mini")
    capabilities = {"structured", "web"}

    def __init__(self, root: Path, transport=_json_request):
        self.root, self.transport = root, transport

    @property
    def configured(self) -> bool:
        return bool(_secret(self.root, "OPENAI_API_KEY"))

    def generate(self, prompt: str, schema: dict, *, model: str, web=False, **_options) -> dict:
        key = _secret(self.root, "OPENAI_API_KEY")
        if not key:
            raise ValueError("OpenAI is not configured on the server")
        if model not in self.models:
            raise ValueError("Unsupported OpenAI model")
        payload = {"model": model, "input": prompt, "text": {"format": {"type": "json_schema", "name": "career_result", "schema": schema, "strict": True}}}
        if web:
            payload["tools"] = [{"type": "web_search"}]
        data = self.transport("https://api.openai.com/v1/responses", payload, {"Authorization": "Bearer " + key})
        text = data.get("output_text")
        if not text:
            for output in data.get("output", []):
                for content in output.get("content", []):
                    if content.get("type") == "output_text":
                        text = content.get("text")
                        break
        try:
            return json.loads(text or "")
        except json.JSONDecodeError:
            raise ValueError("OpenAI returned invalid structured output") from None


class AnthropicProvider:
    id = "anthropic"
    models = ("claude-sonnet-4-6", "claude-haiku-4-5")
    capabilities = {"structured"}

    def __init__(self, root: Path, transport=_json_request):
        self.root, self.transport = root, transport

    @property
    def configured(self) -> bool:
        return bool(_secret(self.root, "ANTHROPIC_API_KEY"))

    def generate(self, prompt: str, schema: dict, *, model: str, web=False, **_options) -> dict:
        key = _secret(self.root, "ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("Claude is not configured on the server")
        if web:
            raise ValueError("Claude is not enabled for actions requiring built-in web access")
        if model not in self.models:
            raise ValueError("Unsupported Claude model")
        payload = {"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": prompt}], "output_config": {"format": {"type": "json_schema", "schema": schema}}}
        data = self.transport("https://api.anthropic.com/v1/messages", payload, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        text = "".join(item.get("text", "") for item in data.get("content", []) if item.get("type") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise ValueError("Claude returned invalid structured output") from None


class CodexProvider:
    id = "codex"
    capabilities = {"structured", "web", "apps"}

    def __init__(self, invoke):
        self.invoke = invoke

    @property
    def configured(self) -> bool:
        return True

    @property
    def models(self) -> tuple:
        # "codex-runtime" (Codex's own default) plus the models her plan lists.
        return codex.models()

    def generate(self, prompt: str, schema: dict, *, model="codex-runtime", **options) -> dict:
        if model not in self.models:
            raise ValueError("Unsupported Codex model")
        chosen = {key: options[key] for key in ("web", "apps") if key in options}
        if model not in codex.MODELS:  # a named plan model; the default needs no flag
            chosen["model"] = model
        return self.invoke(prompt, schema, **chosen)


class ClaudeCodeProvider:
    """The local Claude Code CLI, signed in to the user's subscription.

    Mirrors CodexProvider: no key, runs on the plan's usage limits. It has web
    search but no Gmail access, so the mailbox worker stays on Codex.
    """

    id = claude_code.ID
    models = claude_code.MODELS
    capabilities = {"structured", "web"}

    def __init__(self, invoke=claude_code.invoke):
        self.invoke = invoke

    @property
    def configured(self) -> bool:
        return claude_code.available()

    # Same convention as the Codex runtime: the web is on unless an action
    # (hiring review, resume chat) switches it off.
    def generate(self, prompt: str, schema: dict, *, model="sonnet", web=True, **_options) -> dict:
        if model not in self.models:
            raise ValueError("Unsupported Claude Code model")
        return self.invoke(prompt, schema, model=model, web=web)


class KimiCliProvider:
    """The local Kimi Code CLI, signed in to the user's membership.

    Mirrors ClaudeCodeProvider: no key, calls count against the membership's
    usage limits. Web search comes from the CLI's built-in tools; it has no
    Gmail access, so the mailbox worker stays on Codex.
    """

    id = kimi_cli.ID
    capabilities = {"structured", "web"}

    def __init__(self, invoke=kimi_cli.invoke):
        self.invoke = invoke

    @property
    def configured(self) -> bool:
        return kimi_cli.available()

    @property
    def models(self) -> tuple:
        # "kimi-runtime" (the CLI's default) plus the models in its config.toml.
        return kimi_cli.models()

    # Same convention as the Codex runtime: the web is on unless an action
    # (hiring review, resume chat) switches it off.
    def generate(self, prompt: str, schema: dict, *, model="kimi-runtime", web=True, **_options) -> dict:
        if model not in self.models:
            raise ValueError("Unsupported Kimi Code model")
        return self.invoke(prompt, schema, model=model, web=web)


class HostedProvider:
    """Any other provider in the Settings catalogue, reached through LangChain.

    Adding a key on the Settings page is enough to make OpenRouter, Gemini or
    Kimi run the agents. These APIs have no built-in web search here, so the
    gateway sends research and discovery to a provider that has one (Azure
    OpenAI has one: see AzureOpenAIProvider).
    """

    capabilities = {"structured"}

    def __init__(self, root: Path, provider_id: str):
        from backend.ai import catalog

        self.root, self.id, self.spec = root, provider_id, catalog.PROVIDERS[provider_id]

    @property
    def configured(self) -> bool:
        from backend.ai import catalog

        if self.id == catalog.AZURE:
            return bool(_secret(self.root, self.spec["key"])) and not catalog.azure_settings(self.root)["missing"]
        return bool(_secret(self.root, self.spec["key"]))

    @property
    def models(self) -> tuple:
        # The cached live list, never a network call: resolve() runs on every request.
        from backend.ai import catalog

        if self.id == catalog.AZURE:
            return tuple(catalog.azure_deployments(self.root))
        listed = (catalog._read_cache(self.root).get(self.id) or {}).get("models") or []
        return tuple(dict.fromkeys([*self.spec["defaults"].values(), *listed]))

    def generate(self, prompt: str, schema: dict, *, model: str, **_options) -> dict:
        from backend.ai import models as chat_models
        from backend.ai.agents.graph import describe_provider_error

        if model not in self.models:
            raise ValueError(f"Unsupported {self.spec['label']} model")
        try:
            llm = chat_models.build(self.root, self.id, model, max_tokens=8192, timeout=300)
            shaped = {"title": "career_result", "description": "The requested result.", **schema}
            method = {} if self.id == "gemini" else {"method": "function_calling"}
            result = llm.with_structured_output(shaped, **method).invoke(prompt)
        except chat_models.ProviderNotConfigured as error:
            raise ValueError(str(error)) from None
        except Exception as error:  # provider, network or quota failure
            raise ValueError(f"{self.spec['label']}: {describe_provider_error(error)}") from None
        if not isinstance(result, dict):
            raise ValueError(f"{self.spec['label']} returned output that did not match the requested schema")
        return result


class AzureOpenAIProvider(HostedProvider):
    """Azure OpenAI: a hosted API that can also search the web.

    Azure's Responses API carries the same built-in ``web_search`` tool as
    OpenAI's, so job search and company research call it directly, with the
    schema enforced by the API. Every call without the web keeps the LangChain
    path. Web is on unless the action switches it off, the same convention as
    the local runtimes.
    """

    capabilities = {"structured", "web"}
    TIMEOUT = 600  # a web research turn searches several times before it answers

    def __init__(self, root: Path, transport=None):
        from backend.ai import catalog

        super().__init__(root, catalog.AZURE)
        self.transport = transport or _azure_request

    def generate(self, prompt: str, schema: dict, *, model: str, web=True, **options) -> dict:
        from backend.ai import catalog

        if not web:
            return super().generate(prompt, schema, model=model, **options)
        key = _secret(self.root, self.spec["key"])
        azure = catalog.azure_settings(self.root)
        if not key or azure["missing"]:
            raise ValueError("Azure OpenAI is not configured: add "
                             + " and ".join(["AZURE_OPENAI_API_KEY"] * (not key) + azure["missing"])
                             + " to career-dashboard/.env")
        if model not in self.models:
            raise ValueError(f"Unsupported {self.spec['label']} model")
        payload = {
            "model": model, "input": prompt, "tools": [{"type": "web_search"}],
            "text": {"format": {"type": "json_schema", "name": "career_result",
                                "schema": codex.strict_schema(schema), "strict": True}},
        }
        data = self.transport(azure["base_url"] + "responses", payload, {"api-key": key}, self.TIMEOUT)
        if data.get("status") == "incomplete":
            reason = (data.get("incomplete_details") or {}).get("reason") or "unknown"
            raise ValueError(f"Azure OpenAI stopped before finishing ({reason}). Retry, or pick fewer jobs.")
        text = data.get("output_text") or next(
            (part.get("text") for item in data.get("output") or [] for part in item.get("content") or []
             if part.get("type") == "output_text"), "")
        try:
            result = json.loads(text or "")
        except json.JSONDecodeError:
            raise ValueError("Azure OpenAI returned output that did not match the requested schema") from None
        if not isinstance(result, dict):
            raise ValueError("Azure OpenAI returned output that did not match the requested schema")
        return result


# Azure meters tokens per minute, and a web search re-reads every page it opened, so a
# second call within the same minute (research straight after finding jobs) is often
# refused with 429. Azure says how long to wait; waiting beats failing the whole run.
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_MAX_WAIT = 60
# Azure often says "retry after 1 s" when a deployment's tokens-per-minute budget is spent,
# but that budget refills over a 60 s window, and one web-search job search can use most
# of it. On 23 Sep four quick retries all failed within 51 s; waits now grow to at least
# 15, 30, 45 and 60 s, about two and a half minutes of patience in all.
RATE_LIMIT_FLOOR = 15


def _retry_after(headers, attempt: int) -> float:
    hinted = 0.0
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1),
                        ("x-ratelimit-reset-tokens", 1), ("x-ratelimit-reset-requests", 1)):
        try:
            hinted = max(hinted, float((headers or {}).get(name)) * scale)
        except (TypeError, ValueError):
            continue
    return min(max(hinted + 1, RATE_LIMIT_FLOOR * (attempt + 1)), RATE_LIMIT_MAX_WAIT)


def _azure_request(url: str, payload: dict, headers: dict, timeout: int, sleep=time.sleep) -> dict:
    """POST to Azure, keeping Azure's own error message (it names the fix: a quota, a bad deployment)."""
    request = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers})
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429 and attempt < RATE_LIMIT_RETRIES:
                sleep(_retry_after(exc.headers, attempt))
                continue
            try:
                message = json.loads(exc.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
            except (ValueError, AttributeError, OSError):
                message = ""
            detail = " ".join(str(message).split())[:300]
            raise ValueError(f"Azure OpenAI returned HTTP {exc.code}" + (f": {detail}" if detail else "")) from None
        except (URLError, TimeoutError):
            raise ValueError("Azure OpenAI could not be reached before the timeout") from None


# Where a web- or Gmail-needing action goes when the chosen provider cannot do it.
CAPABLE_ORDER = ("claude_code", "codex", "kimi_cli", "openai", "azure_openai")

# Which of each plan's models an action runs on under Auto: reading and sorting on
# the lighter one, everything that writes or researches on the best one.
ACTION_TIER = {"requirement_extraction": "cheap", "email": "cheap"}


class RouterProvider:
    """Auto: the router as one more provider (backend/ai/router.py).

    Each call walks the saved route (Kimi, Codex, Claude, then Azure) over the
    gateway's own endpoint objects, so every runtime keeps its usual command.
    `served()` names the endpoint that answered the last call on this thread.
    """

    id = router.ID
    models = (router.ID,)
    capabilities = {"structured", "web", "apps"}

    def __init__(self, gateway: "AIGateway"):
        self.gateway = gateway
        self.local = threading.local()

    @property
    def configured(self) -> bool:
        return router.ready(self.gateway.s.w.root)

    def served(self) -> tuple | None:
        return getattr(self.local, "served", None)

    def route(self, action: str, prompt: str, schema: dict, **options) -> dict:
        from backend.ai import paid_gate, switch_recorder

        needs = ACTIONS.get(action) or {}
        wanted = {"web": bool(needs.get("web")) and options.get("web", True) is not False,
                  "apps": bool(needs.get("apps") or options.get("apps"))}
        services = self.gateway.s
        preferences = services.pref("ai_preferences", {}) or {}
        self.local.served = None

        def attempt(provider_id, model):
            self.local.served = (provider_id, model)  # the last endpoint tried, until one answers
            return self.gateway.providers[provider_id].generate(prompt, schema, model=model, **options)

        result, served = router.route(
            services.w.root, tier=ACTION_TIER.get(action, "strong"), needs=wanted,
            policy=router.policy_from(preferences), attempt=attempt,
            paid_gate=paid_gate(services), on_switch=switch_recorder(services, action),
        )
        self.local.served = served
        return result

    def generate(self, prompt: str, schema: dict, *, model=router.ID, **options) -> dict:
        return self.route(options.pop("action", "document_review"), prompt, schema, **options)


class AIGateway:
    def __init__(self, service, codex_invoke):
        from backend.ai import catalog

        self.s = service
        self.providers = {provider.id: provider for provider in (OpenAIProvider(service.w.root), AnthropicProvider(service.w.root), CodexProvider(codex_invoke), ClaudeCodeProvider(), KimiCliProvider(), AzureOpenAIProvider(service.w.root))}
        for provider_id in catalog.PROVIDERS:
            self.providers.setdefault(provider_id, HostedProvider(service.w.root, provider_id))
        self.providers[router.ID] = RouterProvider(self)

    def _builtin_default(self) -> str:
        # Auto when any plan on its route can run here; the older built-in otherwise.
        if router.DEFAULT_WHEN_UNSET and self.providers[router.ID].configured:
            return router.ID
        return "openai" if self.providers["openai"].configured else "codex"

    def preferences(self) -> dict:
        # The Settings tab keeps its tier choices under the same key, so read
        # only this gateway's part of it and fill in what is missing.
        stored = self.s.pref("ai_preferences", {}) or {}
        configured_default = None if stored.get("default") else self._builtin_default()
        return {
            "default": stored.get("default") or {"provider": configured_default, "model": self.providers[configured_default].models[0]},
            "actions": stored.get("actions") or {},
            "fallback": stored.get("fallback"),
        }

    def catalog(self, action: str | None = None) -> dict:
        needs = ACTIONS.get(action or "", {})
        providers = []
        for provider in self.providers.values():
            compatible = all(not needed or capability in provider.capabilities for capability, needed in needs.items())
            providers.append({"id": provider.id, "configured": provider.configured, "compatible": compatible, "models": list(provider.models), "capabilities": sorted(provider.capabilities)})
        return {"providers": providers, "actions": ACTIONS, "preferences": self.preferences()}

    def save_preferences(self, values: dict) -> dict:
        default = values.get("default", {})
        self.resolve("requirement_extraction", default.get("provider"), default.get("model"), require_configured=False)
        for action, choice in values.get("actions", {}).items():
            if action not in ACTIONS:
                raise ValueError("Unknown AI action")
            self.resolve(action, choice.get("provider"), choice.get("model"), require_configured=False)
        fallback = values.get("fallback")
        if fallback:
            self.resolve("requirement_extraction", fallback.get("provider"), fallback.get("model"), require_configured=False)
        # Keep the Settings tab's tiers, which share this key.
        stored = self.s.pref("ai_preferences", {}) or {}
        stored.update({"default": default, "actions": values.get("actions", {}), "fallback": fallback})
        self.s.set_pref("ai_preferences", stored)
        with self.s.w.connect() as db:
            self.s.w.record_event(db, "ai_preferences_updated", preferences=values)
        self.s.sync_projections()
        return self.preferences()

    def resolve(self, action: str, provider_id=None, model=None, *, require_configured=True):
        if action not in ACTIONS:
            raise ValueError("Unknown AI action")
        preferences = self.preferences()
        override = preferences.get("actions", {}).get(action)
        choice = override or preferences.get("default", {})
        explicit = bool(provider_id or override)
        provider_id = provider_id or choice.get("provider")
        provider = self.providers.get(provider_id)
        if not provider:
            raise ValueError("Unknown AI provider")
        needs = ACTIONS[action]
        if not explicit and any(needed and name not in provider.capabilities for name, needed in needs.items()):
            # The main choice cannot do this action (no web search, no Gmail):
            # use the first ready provider that can, rather than fail the run.
            for candidate in CAPABLE_ORDER:
                other = self.providers[candidate]
                if other.configured and all(not needed or name in other.capabilities for name, needed in needs.items()):
                    provider, provider_id, model = other, candidate, other.models[0]
                    break
        model = model or choice.get("model") or provider.models[0]
        if model not in provider.models:
            raise ValueError("This model is not available for the selected provider")
        needs = ACTIONS[action]
        missing = [name for name, needed in needs.items() if needed and name not in provider.capabilities]
        if missing:
            raise ValueError(f"{provider_id} is not compatible with {action}: missing {', '.join(missing)}")
        if require_configured and not provider.configured:
            raise ValueError(f"{provider_id} is not configured on the server")
        return provider, model

    def generate(self, action: str, prompt: str, schema: dict, *, provider=None, model=None, **options) -> dict:
        selected, model = self.resolve(action, provider, model)
        if selected.id == router.ID:
            # Auto falls back through its whole route; the single backup below is not needed.
            return selected.route(action, prompt, schema, **options)
        # A paid provider chosen by name is held to the paid limit by AgentCache, which
        # reserves the call before it reaches here.
        try:
            return selected.generate(prompt, schema, model=model, **options)
        except ValueError as error:
            # Graceful fallback: the Settings page can name a backup provider.
            # One retry, recorded, and the UI surfaces that it happened.
            fallback = self.preferences().get("fallback") or {}
            fallback_id = fallback.get("provider")
            if not fallback_id or fallback_id == selected.id:
                raise
            try:
                backup, backup_model = self.resolve(action, fallback_id, fallback.get("model"))
            except ValueError:
                raise error from None
            from backend.ai import paid_gate

            if router.is_paid(backup.id) and paid_gate(self.s)(backup.id):
                raise error  # today's paid limit is used up: no paid backup either
            try:
                result = backup.generate(prompt, schema, model=backup_model, **options)
            except ValueError:
                raise error from None
            with self.s.w.connect() as db:
                self.s.w.record_event(
                    db, "provider_fallback", ai_action=action,
                    from_provider=selected.id, to_provider=backup.id,
                    reason=str(error)[:300],
                )
            return result
