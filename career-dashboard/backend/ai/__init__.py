"""AI provider gateway and the LangChain specialist-agent layer."""

from pathlib import Path

from .providers import AIGateway, AnthropicProvider, ClaudeCodeProvider, CodexProvider, KimiCliProvider, OpenAIProvider

__all__ = [
    "AIGateway", "AnthropicProvider", "ClaudeCodeProvider", "CodexProvider", "KimiCliProvider", "OpenAIProvider",
    "team_for", "any_provider_configured", "ready_providers", "resolve_tiers", "main_choice", "engine",
]

# Where a tier goes when its chosen provider cannot run here: the signed-in local
# runtimes first (no key, no per-call cost), then whichever hosted key exists.
FALLBACK_ORDER = ("claude_code", "codex", "kimi_cli", "openrouter", "anthropic", "openai", "azure_openai", "gemini", "kimi")


def ready_providers(root) -> dict:
    """Which providers can actually run on this machine right now, by id.

    ``auto`` (the router) is ready when any endpoint on its route is.
    """
    from backend.ai import claude_code, codex, kimi_cli, models, router

    ready = dict(models.available(Path(root)))
    ready[claude_code.ID] = claude_code.available()
    ready[codex.ID] = codex.available()
    ready[kimi_cli.ID] = kimi_cli.available()
    ready[router.ID] = router.ready_from(ready)
    return ready


def any_provider_configured(root) -> bool:
    """True when a hosted provider has a key here, or a local CLI (Claude Code, Codex, Kimi Code) is installed."""
    return any(ready_providers(root).values())


def provider_label(provider_id: str) -> str:
    from backend.ai import catalog, claude_code, codex, kimi_cli, router

    if provider_id == router.ID:
        return router.SHORT
    if provider_id == claude_code.ID:
        return "Claude Code"
    if provider_id == codex.ID:
        return "Codex"
    if provider_id == kimi_cli.ID:
        return "Kimi Code"
    return catalog.PROVIDERS.get(provider_id, {}).get("label", provider_id)


def _default_model(provider_id: str, tier: str) -> str:
    from backend.ai import catalog, claude_code, codex, kimi_cli, router

    if provider_id == router.ID:
        return router.ID  # the router picks each endpoint's model per tier itself
    if provider_id == claude_code.ID:
        return claude_code.DEFAULTS[tier]
    if provider_id == codex.ID:
        return codex.DEFAULTS[tier]
    if provider_id == kimi_cli.ID:
        return kimi_cli.DEFAULTS[tier]
    return catalog.default_model(provider_id, tier)


def main_choice(preferences: dict, ready: dict) -> tuple[str, str]:
    """The provider and model every background run starts from: the Settings choice,
    else the same built-in the gateway uses: Auto (free plans first) when any of its
    endpoints can run here, then OpenAI with a key here, otherwise Codex."""
    from backend.ai import codex, router

    stored = preferences.get("default") or {}
    if stored.get("provider"):
        return stored["provider"], stored.get("model") or _default_model(stored["provider"], "strong")
    if router.DEFAULT_WHEN_UNSET and ready.get(router.ID):
        return router.ID, router.ID
    if ready.get("openai"):
        return "openai", _default_model("openai", "strong")
    return codex.ID, codex.DEFAULTS["strong"]


def resolve_tiers(root, preferences: dict, ready: dict | None = None) -> tuple[dict, dict]:
    """(provider, model) per tier: the saved choice when it can run here, else the first runtime that can.

    A tier nobody chose follows the main choice, so the chat and the background
    runs share one engine until Settings says otherwise. Returns
    ``AgentTeam.tiers`` and a map naming the provider each tier was moved away
    from (empty when nothing moved). A tier whose provider has no key and no
    fallback keeps its choice, so the call fails with the provider's own clear
    message rather than a silent switch.
    """
    from backend.ai import catalog

    ready = ready if ready is not None else ready_providers(root)
    main, main_model = main_choice(preferences, ready)
    chosen = {}
    for tier in catalog.TIERS:
        stored = (preferences.get("tiers") or {}).get(tier) or {}
        provider = stored.get("provider") or main
        default = main_model if provider == main and tier == "strong" else _default_model(provider, tier)
        chosen[tier] = (provider, stored.get("model") or default)
    order = [p for p in ((main,) + FALLBACK_ORDER) if p]
    tiers, moved = {}, {}
    for tier, (provider, model) in chosen.items():
        if ready.get(provider):
            tiers[tier] = (provider, model)
            continue
        fallback = next((p for p in order if ready.get(p)), None)
        if fallback is None:
            tiers[tier] = (provider, model)
        else:
            chosen_model = main_model if fallback == main and tier == "strong" else _default_model(fallback, tier)
            tiers[tier] = (fallback, chosen_model)
            moved[tier] = provider
    return tiers, moved


def usage_recorder(services):
    """Persist specialist token usage into ``ai_calls`` so costs become real.

    Rows are marked with ``cache_key='usage'`` so the daily call *budget* (which
    counts reserved invocations) is not inflated by telemetry. Never raises:
    usage recording must not break a run.
    """

    def record(usage: dict) -> None:
        try:
            import uuid

            with services.w.connect() as db:
                db.execute(
                    """INSERT INTO ai_calls(id,cache_key,day,state,created_at,error,provider,model,action,cache_version,input_tokens,output_tokens)
                    VALUES(?,?,?,?,?,NULL,?,?,?,?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        "usage",
                        services.today(),
                        "completed",
                        services.now(),
                        usage.get("provider") or "unknown",
                        usage.get("model") or "unknown",
                        "specialist:" + str(usage.get("agent") or "unknown"),
                        "v1",
                        usage.get("input_tokens"),
                        usage.get("output_tokens"),
                    ),
                )
        except Exception:
            pass

    return record


def choice_label(provider_id: str, model: str) -> str:
    """"Kimi Code · kimi-code/k3", or just "Auto · free plans first" for the router."""
    from backend.ai import router

    return router.LABEL if provider_id == router.ID else provider_label(provider_id) + " · " + model


def paid_gate(services):
    """What the router asks before each paid call: None while today's paid limit has room."""
    from backend.services.agent_cache import paid_block

    return lambda _provider: paid_block(services)


def switch_recorder(services, action: str = "specialist"):
    """Records each route switch (a failed endpoint replaced by the next) in the activity log."""

    def record(event: dict) -> None:
        try:
            with services.w.connect() as db:
                services.w.record_event(
                    db, "provider_fallback", ai_action=event.get("agent") and f"{action}:{event['agent']}" or action,
                    from_provider=event["from_provider"], to_provider=event["to_provider"],
                    reason=str(event.get("reason") or "")[:300],
                )
        except Exception:
            pass  # telemetry; a failed write must never fail the step that just succeeded

    return record


def route_options(services, action: str = "specialist") -> dict:
    """The router settings a caller hands to AgentTeam: the saved route, the paid gate, the switch log."""
    from backend.ai import router

    preferences = services.pref("ai_preferences", {}) or {}
    return {"policy": router.policy_from(preferences), "paid_gate": paid_gate(services),
            "on_switch": switch_recorder(services, action)}


def team_for(services, on_usage=None):
    """An AgentTeam built from the saved tier preferences, on a provider that is ready.

    Kept here so callers need no knowledge of where preferences are stored.
    """
    from backend.ai import router
    from backend.ai.agents.graph import AgentTeam

    preferences = services.pref("ai_preferences", {}) or {}
    ready = ready_providers(services.w.root)
    tiers, _moved = resolve_tiers(services.w.root, preferences, ready)
    # The backup Settings names, when it can run here: the chat and the specialists
    # get the same one retry the gateway gives background runs. Auto needs none:
    # it already falls back through its whole route.
    backup = preferences.get("fallback") or {}
    fallback = None
    routed = all(provider == router.ID for provider, _model in tiers.values())
    if not routed and backup.get("provider") and ready.get(backup["provider"]):
        fallback = (backup["provider"], backup.get("model") or _default_model(backup["provider"], "strong"))
    from backend.ai.persona import persona_for

    return AgentTeam(services.w.root, tiers, on_usage or usage_recorder(services), fallback=fallback,
                     persona=persona_for(services.w.root), route=route_options(services))


def engine(services) -> dict:
    """What the assistant's agent loop will run on, in words the page can show.

    ``options`` lists every runtime that is ready here so the person can switch
    from the chat; choosing one goes through ``settings.choose_main`` like the
    Settings tab.
    """
    from backend.ai import catalog, claude_code, codex, kimi_cli, router

    root = services.w.root
    preferences = services.pref("ai_preferences", {}) or {}
    ready = ready_providers(root)
    tiers, moved = resolve_tiers(root, preferences, ready)
    provider, model = tiers["strong"]
    moved_from = moved.get("strong")
    options = []
    if ready.get(router.ID):
        options.append({"provider": router.ID, "model": router.ID, "label": router.LABEL})
    for provider_id in FALLBACK_ORDER:
        if not ready.get(provider_id):
            continue
        if provider_id == claude_code.ID:
            listed = list(claude_code.MODELS)
        elif provider_id == codex.ID:
            listed = list(codex.models())
        elif provider_id == kimi_cli.ID:
            listed = list(kimi_cli.models())
        else:
            cached = (catalog._read_cache(root).get(provider_id) or {}).get("models") or []
            defaults = catalog.defaults(provider_id, root)
            listed = [m for m in dict.fromkeys([defaults["strong"], defaults["cheap"], *cached]) if m][:12]
        for name in listed:
            options.append({"provider": provider_id, "model": name, "label": provider_label(provider_id) + " · " + name})
    runs_provider, runs_model = main_choice(preferences, ready)
    note = None
    if codex.ID in (provider, runs_provider) and ready.get(codex.ID) and not codex.signed_in():
        note = "Codex is installed but not signed in: open the ChatGPT app and sign in, or choose Claude Code."
    stored_fallback = preferences.get("fallback") or None
    last_fallback = None
    try:
        import json as _json

        with services.w.connect() as db:
            row = db.execute(
                "SELECT details, occurred_at FROM activity WHERE action='provider_fallback' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row:
            last_fallback = {**_json.loads(row[0]), "at": row[1]}
    except Exception:
        last_fallback = None  # telemetry only; never block the page on it
    return {
        "provider": provider, "model": model, "label": choice_label(provider, model),
        "ready": bool(ready.get(provider)),
        "moved_from": provider_label(moved_from) if moved_from else None,
        # What the runs the chat starts (discovery, research, builds) go through
        # unless Settings gave an action its own provider.
        "runs": {"provider": runs_provider, "model": runs_model,
                 "label": choice_label(runs_provider, runs_model),
                 "ready": bool(ready.get(runs_provider))},
        "fallback": stored_fallback,
        "last_fallback": last_fallback,
        "note": note,
        "options": options,
    }
