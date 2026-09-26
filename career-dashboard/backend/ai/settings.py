"""Provider and model selection for the UI.

One place that answers: which providers exist, which have a key, which models
they offer, what is currently chosen, and does the choice actually work. Key
values never appear in anything returned here.
"""

from __future__ import annotations

from backend.ai import catalog, claude_code, codex, keys, kimi_cli, models, router
from backend.ai.agents.graph import AgentTeam, describe_provider_error
from backend.ai.agents.specialists import REGISTRY

# The free local runtime is not a LangChain provider; it is offered alongside them.
CODEX = {
    "id": "codex",
    "label": "Codex (local, free)",
    "configured": True,
    "models": ["codex-runtime"],
    "defaults": {"strong": "codex-runtime", "cheap": "codex-runtime"},
    "note": "Runs through the Codex app on this machine. No API key and no per-call cost.",
}

# The same idea on the Claude side: the signed-in Claude Code CLI, on the
# subscription's usage limits. Presence is checked live because the CLI may be
# installed or removed without a restart.
def _claude_code_entry() -> dict:
    return {
        "id": claude_code.ID,
        "label": claude_code.LABEL,
        "configured": claude_code.available(),
        "models": list(claude_code.MODELS),
        "defaults": dict(claude_code.DEFAULTS),
        "note": claude_code.NOTE,
    }


# The same idea on the Kimi side: the signed-in Kimi Code CLI, on the
# membership's usage limits.
def _kimi_cli_entry() -> dict:
    return {
        "id": kimi_cli.ID,
        "label": kimi_cli.LABEL,
        "configured": kimi_cli.available(),
        "models": list(kimi_cli.models()),
        "defaults": dict(kimi_cli.DEFAULTS),
        "note": kimi_cli.NOTE,
    }


LOCAL_DEFAULTS = {"codex": CODEX["defaults"], claude_code.ID: claude_code.DEFAULTS,
                  kimi_cli.ID: kimi_cli.DEFAULTS, router.ID: {"strong": router.ID, "cheap": router.ID}}


def _auto_entry(services, gateway=None) -> dict:
    """Auto (the router) as the page shows it: ready or not, and its route in order."""
    from backend.ai import paid_gate, ready_providers

    root = services.w.root
    ready = ready_providers(root)
    policy = router.policy_from(services.pref("ai_preferences", {}) or {})
    blocked = paid_gate(services)(catalog.AZURE)
    return {
        "id": router.ID, "label": router.LABEL, "kind": "auto", "configured": bool(ready.get(router.ID)),
        "models": [router.ID], "agent_models": [router.ID], "defaults": {"strong": router.ID, "cheap": router.ID},
        "capabilities": sorted(gateway.providers[router.ID].capabilities) if gateway is not None else ["structured", "web", "apps"],
        "note": ("Uses your Kimi, Codex and Claude plans first and moves to the next when one reaches its "
                 "usage limit. Azure (paid) is used only when all three are resting or failing."),
        "route": policy,
        "endpoints": router.status(root, policy, ready_map=ready, paid={"block": blocked}),
    }


def _preferences(services) -> dict:
    from backend.ai import main_choice, ready_providers

    stored = services.pref("ai_preferences", {}) or {}
    tiers = stored.get("tiers") or {}
    resolved = {}
    main = None
    for tier in catalog.TIERS:
        chosen = tiers.get(tier) or {}
        provider = chosen.get("provider")
        if not provider:
            # A tier nobody chose follows the main choice (Auto by default).
            main = main or main_choice(stored, ready_providers(services.w.root))[0]
            provider = main if main in LOCAL_DEFAULTS or main in catalog.PROVIDERS else "openrouter"
        fallback = LOCAL_DEFAULTS[provider][tier] if provider in LOCAL_DEFAULTS else catalog.default_model(provider, tier)
        resolved[tier] = {
            "provider": provider,
            "model": chosen.get("model") or fallback,
        }
    return {**stored, "tiers": resolved}


# What each agent run asks the gateway for, in the words the page uses.
ROUTES = (
    ("discovery", "Job search"),
    ("role_research", "Company research & resume advice"),
    ("document_review", "Independent resume review"),
    ("resume_chat", "Resume chat"),
    ("email", "Gmail sync"),
)


def _routes(gateway) -> list:
    """Which provider each kind of work will actually use, and why."""
    preferences = gateway.preferences()
    default, overrides = preferences["default"], preferences.get("actions") or {}
    routes = []
    for action, label in ROUTES:
        try:
            provider, model = gateway.resolve(action, require_configured=False)
            routes.append({"action": action, "label": label, "provider": provider.id, "model": model,
                           "moved": provider.id != default.get("provider"),
                           "override": action in overrides, "error": None})
        except ValueError as error:
            routes.append({"action": action, "label": label, "provider": None, "model": None,
                           "moved": False, "error": str(error)})
    return routes


def overview(services, refresh: bool = False, gateway=None) -> dict:
    """Everything the settings screen needs in one call."""
    from backend.services.agents import mail_available

    root = services.w.root
    providers = []
    for provider_id, spec in catalog.PROVIDERS.items():
        listed = catalog.models(root, provider_id, refresh=refresh)
        providers.append({
            "id": provider_id,
            "label": spec["label"],
            "kind": "api",
            "configured": listed["configured"],
            "models": listed["models"],
            "defaults": catalog.defaults(provider_id, root),
            "key_name": spec["key"],
            "key_source": keys.source(root, spec["key"]),
            "error": listed["error"],
            "fetched_at": listed.get("fetched_at"),
        })
    providers.append({**CODEX, "models": list(codex.models()), "kind": "local"})
    providers.append({**_claude_code_entry(), "kind": "local"})
    providers.append({**_kimi_cli_entry(), "kind": "local"})
    if gateway is not None:
        for entry in providers:
            engine = gateway.providers.get(entry["id"])
            entry["capabilities"] = sorted(engine.capabilities) if engine else []
            entry["agent_models"] = list(engine.models) if engine else []
    providers.insert(0, _auto_entry(services, gateway))
    return {
        "main": gateway.preferences()["default"] if gateway is not None else None,
        "routes": [r for r in (_routes(gateway) if gateway is not None else [])
                   if r["action"] != "email" or mail_available(root, services.pref("gmail", {}))],
        "providers": providers,
        "tiers": {
            "strong": "Writes text you will send: resume wording, cover letters, role review.",
            "cheap": "Reads and classifies: requirement extraction, relevance, verification, email.",
        },
        "agents": [
            {"name": name, "tier": agent.tier, "isolated": agent.isolated}
            for name, agent in sorted(REGISTRY.items())
        ],
        "preferences": _preferences(services),
    }


def save(services, values: dict) -> dict:
    """Persist the tier choices after checking each one is selectable."""
    tiers = values.get("tiers") or {}
    known = set(catalog.PROVIDERS) | set(LOCAL_DEFAULTS)
    for tier, chosen in tiers.items():
        if tier not in catalog.TIERS:
            raise ValueError(f"Unknown tier: {tier}")
        provider = (chosen or {}).get("provider")
        if provider not in known:
            raise ValueError(f"Unknown AI provider: {provider}")
        if not (chosen or {}).get("model"):
            raise ValueError(f"Choose a model for the {tier} tier")
    stored = services.pref("ai_preferences", {}) or {}
    stored["tiers"] = tiers
    services.set_pref("ai_preferences", stored)
    with services.w.connect() as db:
        services.w.record_event(db, "ai_preferences_updated", tiers=tiers)
    services.sync_projections()
    return _preferences(services)


def test_provider(services, provider_id: str, model: str) -> dict:
    """Make the smallest possible real call, so a bad key fails here not mid-run."""
    if provider_id == router.ID:
        # Auto: test the endpoint the route would use next (the first one not resting).
        from backend.ai import paid_gate, ready_providers

        policy = router.policy_from(services.pref("ai_preferences", {}) or {})
        blocked = paid_gate(services)(catalog.AZURE)
        rows = router.status(services.w.root, policy, ready_map=ready_providers(services.w.root))
        first = next((row for row in rows if row["enabled"] and row["ready"] and not row["resting"]
                      and not (row["paid"] and blocked)), None)
        if first is None:
            return {"ok": False, "provider": provider_id, "model": model,
                    "detail": "Nothing on the route can run right now: every plan is resting, switched off or not set up."}
        result = test_provider(services, first["provider"], first["models"]["strong"])
        return {**result, "provider": provider_id, "model": model,
                "detail": f"Next on the route: {first['label']} · {first['models']['strong']}. " + result["detail"]}
    if provider_id == "codex":
        from backend.ai import codex

        if not codex.available():
            return {"ok": False, "provider": provider_id, "model": model,
                    "detail": "Codex is not installed on this machine. Install the ChatGPT app and sign in."}
        # A real call: being installed says nothing about sign-in or the plan's usage limit.
        ping = {"type": "object", "properties": {"word": {"type": "string"}},
                "required": ["word"], "additionalProperties": False}
        try:
            answer = codex.invoke("Reply with the single word: ready", ping, model=model or "codex-runtime")
        except ValueError as error:
            return {"ok": False, "provider": provider_id, "model": model, "detail": str(error)}
        return {"ok": True, "provider": provider_id, "model": model,
                "detail": f"Answered: {str(answer.get('word', ''))[:40]}"}
    if provider_id == claude_code.ID:
        ping = {"type": "object", "properties": {"word": {"type": "string"}},
                "required": ["word"], "additionalProperties": False}
        try:
            answer = claude_code.invoke("Reply with the single word: ready", ping, model=model)
        except ValueError as error:
            return {"ok": False, "provider": provider_id, "model": model, "detail": str(error)}
        return {"ok": True, "provider": provider_id, "model": model,
                "detail": f"Answered: {str(answer.get('word', ''))[:40]}"}
    if provider_id == kimi_cli.ID:
        if not kimi_cli.available():
            return {"ok": False, "provider": provider_id, "model": model,
                    "detail": "Kimi Code is not installed on this machine. Install it from https://www.kimi.com/code and sign in."}
        if not kimi_cli.signed_in():
            return {"ok": False, "provider": provider_id, "model": model,
                    "detail": "Kimi Code is installed but not signed in. Run `kimi login`, then retry."}
        ping = {"type": "object", "properties": {"word": {"type": "string"}},
                "required": ["word"], "additionalProperties": False}
        try:
            answer = kimi_cli.invoke("Reply with the single word: ready", ping, model=model)
        except ValueError as error:
            return {"ok": False, "provider": provider_id, "model": model, "detail": str(error)}
        return {"ok": True, "provider": provider_id, "model": model,
                "detail": f"Answered: {str(answer.get('word', ''))[:40]}"}
    if provider_id not in catalog.PROVIDERS:
        raise ValueError(f"Unknown AI provider: {provider_id}")
    from pydantic import BaseModel

    class Ping(BaseModel):
        word: str

    try:
        llm = models.build(services.w.root, provider_id, model, max_tokens=64)
        answer = llm.with_structured_output(Ping).invoke(
            "Reply with the single word: ready"
        )
    except models.ProviderNotConfigured as error:
        return {"ok": False, "provider": provider_id, "model": model, "detail": str(error)}
    except Exception as error:
        return {"ok": False, "provider": provider_id, "model": model,
                "detail": f"{catalog.PROVIDERS[provider_id]['label']}: {describe_provider_error(error)}"}
    return {"ok": True, "provider": provider_id, "model": model,
            "detail": f"Answered: {answer.word[:40]}"}


def team(services, on_usage=None) -> AgentTeam:
    from backend.ai import usage_recorder

    from backend.ai.persona import persona_for

    return AgentTeam.from_preferences(services.w.root, _preferences(services), on_usage or usage_recorder(services),
                                      persona=persona_for(services.w.root))


def choose_main(services, gateway, provider_id: str, model: str) -> dict:
    """One choice for every agent: the gateway default and both specialist tiers.

    Per-action overrides are cleared so nothing silently keeps an older choice.
    Work the chosen provider cannot do (web search, Gmail) is routed by the
    gateway to one that can, and the page shows where it went.
    """
    provider = gateway.providers.get(provider_id)
    if provider is None:
        raise ValueError(f"Unknown AI provider: {provider_id}")
    if not provider.configured:
        raise ValueError("That provider is not ready yet. Add its API key (or install it) first.")
    if model not in provider.models:
        raise ValueError("That model is not offered by this provider")
    stored = services.pref("ai_preferences", {}) or {}
    stored["default"] = {"provider": provider_id, "model": model}
    stored["actions"] = {}
    if provider_id in LOCAL_DEFAULTS or provider_id in catalog.PROVIDERS:
        cheap = LOCAL_DEFAULTS[provider_id]["cheap"] if provider_id in LOCAL_DEFAULTS else catalog.default_model(provider_id, "cheap")
        stored["tiers"] = {"strong": {"provider": provider_id, "model": model},
                           "cheap": {"provider": provider_id, "model": cheap}}
    services.set_pref("ai_preferences", stored)
    with services.w.connect() as db:
        services.w.record_event(db, "ai_main_provider_chosen", provider=provider_id, model=model)
    services.sync_projections()
    return overview(services, gateway=gateway)


def save_route(services, gateway, values: dict) -> dict:
    """Save Auto's route: the order, which endpoints are on, and whether it falls back at all."""
    policy = router.validate(values or {})
    stored = services.pref("ai_preferences", {}) or {}
    stored["route"] = policy
    services.set_pref("ai_preferences", stored)
    with services.w.connect() as db:
        services.w.record_event(db, "ai_route_saved", order=policy["order"],
                                off=[p for p, on in policy["enabled"].items() if not on],
                                allow_fallbacks=policy["allow_fallbacks"])
    services.sync_projections()
    return overview(services, gateway=gateway)


def wake(services, gateway, provider_id: str) -> dict:
    """Clear a plan's rest by hand ("it reset early, try it now")."""
    from backend.ai import limits

    if provider_id not in router.DEFAULT_ORDER:
        raise ValueError(f"Unknown AI on the route: {provider_id}")
    limits.HealthBook(services.w.root).wake(provider_id)
    with services.w.connect() as db:
        services.w.record_event(db, "ai_plan_woken", provider=provider_id)
    return overview(services, gateway=gateway)


def save_fallback(services, gateway, provider_id: str, model: str) -> dict:
    """Name the backup provider used when the main one fails a call. Empty clears it."""
    if provider_id:
        provider = gateway.providers.get(provider_id)
        if provider is None:
            raise ValueError(f"Unknown AI provider: {provider_id}")
        if not provider.configured:
            raise ValueError("That provider is not ready yet. Add its API key (or install it) first.")
        if model not in provider.models:
            raise ValueError("That model is not offered by this provider")
    stored = services.pref("ai_preferences", {}) or {}
    stored["fallback"] = {"provider": provider_id, "model": model} if provider_id else None
    services.set_pref("ai_preferences", stored)
    with services.w.connect() as db:
        services.w.record_event(db, "ai_fallback_chosen", provider=provider_id or "none", model=model)
    services.sync_projections()
    return overview(services, gateway=gateway)


def save_key(services, provider_id: str, value: str) -> dict:
    """Store a key typed on the page, then prove it with a free model-list call."""
    if provider_id not in catalog.PROVIDERS:
        raise ValueError("This provider does not use an API key")
    spec = catalog.PROVIDERS[provider_id]
    keys.save(services.w.root, spec["key"], value)
    with services.w.connect() as db:
        # The provider only. The key itself is never logged or stored in SQLite.
        services.w.record_event(db, "ai_key_saved", provider=provider_id)
    services.sync_projections()
    listed = catalog.models(services.w.root, provider_id, refresh=True)
    ok = not listed["error"] and bool(listed["models"])
    return {"provider": provider_id, "ok": ok,
            "detail": (f"Key saved and working: {len(listed['models'])} models available."
                       if ok else f"Key saved, but the check failed: {listed['error'] or 'no models were listed'}."),
            "source": keys.source(services.w.root, spec["key"])}


def remove_key(services, provider_id: str) -> dict:
    if provider_id not in catalog.PROVIDERS:
        raise ValueError("This provider does not use an API key")
    spec = catalog.PROVIDERS[provider_id]
    still = keys.remove(services.w.root, spec["key"])
    with services.w.connect() as db:
        services.w.record_event(db, "ai_key_removed", provider=provider_id)
    services.sync_projections()
    return {"provider": provider_id, "source": still,
            "detail": (f"Removed from the app. Another {spec['key']} is still set in {still}; edit that file to remove it."
                       if still else "Key removed.")}
