"""Auto: one route across her AI plans, the way OpenRouter routes across providers.

Every AI step names a tier (writing or reading) and what it needs (web search,
Gmail). The router walks an ordered list of endpoints and sends the step to the
first one that is switched on, set up on this PC, able to do the job, not
resting after a usage limit, and, when it is a paid API, still inside today's
paid-call limit. If that endpoint fails, the next one takes the step:

    Kimi Code (K3) -> Codex (GPT-6-Astra / Luna) -> Claude Code (Opus / Sonnet) -> Azure OpenAI (paid)

Kimi, Codex and Claude run on paid-for plans with a rolling ~5-hour usage window,
so they cost nothing extra; Azure bills per call, so it always sorts last and is
reached only when every free plan is resting or failing. A usage-limit error
rests that plan until the reset time it printed (``limits.py``), so later steps
skip it instead of hitting the limit again.

OpenRouter ideas, as they apply here: ``order`` is the route, ``enabled`` is
only/ignore, ``allow_fallbacks`` stops after the first endpoint when False, the
paid-call limit plays the part of ``max_price``, capability filtering is
``require_parameters``, and the health book is uptime-aware routing. Every call
reports which endpoint served it.
"""

from __future__ import annotations

from datetime import datetime

from backend.ai import limits

ID = "auto"
LABEL = "Auto · free plans first"
SHORT = "Auto"

# The plans that cost nothing per call; everything else is billed.
FREE = ("kimi_cli", "codex", "claude_code")
DEFAULT_ORDER = ("kimi_cli", "codex", "claude_code", "azure_openai")
# Writing ("strong") gets each plan's best model; reading and sorting ("cheap")
# a lighter one where the plan has it, so the usage window lasts longer. A model
# the plan no longer offers falls back to the runtime's own default.
PREFERRED = {
    "kimi_cli": {"strong": "kimi-code/k3", "cheap": "kimi-code/k3"},
    "codex": {"strong": "gpt-6-astra", "cheap": "gpt-6-luna"},
    "claude_code": {"strong": "opus", "cheap": "sonnet"},
    "azure_openai": {"strong": None, "cheap": None},  # the first deployment in .env
}
CAPABILITIES = {
    "kimi_cli": {"structured", "web"},
    "codex": {"structured", "web", "apps"},
    "claude_code": {"structured", "web"},
    "azure_openai": {"structured", "web"},
}
NEED_WORDS = {"web": "cannot search the web", "apps": "has no Gmail access"}

# Auto is what runs when Settings holds no choice. tests/conftest.py turns this
# off so older tests keep their named providers and never reach this PC's real
# AI apps; tests of the default turn it back on.
DEFAULT_WHEN_UNSET = True


class RouteError(ValueError):
    """No endpoint on the route could take the step. The message says why, per endpoint."""


def is_paid(provider_id: str) -> bool:
    return bool(provider_id) and provider_id not in FREE and provider_id != ID


def label(provider_id: str) -> str:
    from backend.ai import provider_label

    return SHORT if provider_id == ID else provider_label(provider_id)


def available_models(root, provider_id: str) -> tuple:
    from backend.ai import catalog, claude_code, codex, kimi_cli

    if provider_id == kimi_cli.ID:
        return kimi_cli.models()
    if provider_id == codex.ID:
        return codex.models()
    if provider_id == claude_code.ID:
        return claude_code.MODELS
    if provider_id == catalog.AZURE:
        return tuple(catalog.azure_deployments(root))
    return ()


def model_for(root, provider_id: str, tier: str) -> str:
    """The model this endpoint runs for a tier: the preferred one when the plan offers it."""
    from backend.ai import _default_model

    preferred = (PREFERRED.get(provider_id) or {}).get(tier)
    listed = available_models(root, provider_id)
    if preferred and preferred in listed:
        return preferred
    fallback = _default_model(provider_id, tier) if provider_id in FREE else (listed[0] if listed else "")
    return fallback or (listed[0] if listed else "")


# ----- the route policy ---------------------------------------------------------

def default_policy() -> dict:
    return {"order": list(DEFAULT_ORDER), "enabled": {p: True for p in DEFAULT_ORDER}, "allow_fallbacks": True,
            "capacity": {}}


def _capacity(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def normalise(policy: dict | None) -> dict:
    """A complete policy: every endpoint once, known ones only, missing values filled in.

    ``capacity`` holds the limits she typed per free plan (tokens per 5-hour window);
    a plan without one uses the learned limit, else the starting guess (limits.py).
    """
    policy = policy or {}
    order = [p for p in dict.fromkeys(policy.get("order") or []) if p in DEFAULT_ORDER]
    order += [p for p in DEFAULT_ORDER if p not in order]
    enabled = policy.get("enabled") or {}
    capacity = policy.get("capacity") or {}
    return {
        "order": order,
        "enabled": {p: bool(enabled.get(p, True)) for p in order},
        "allow_fallbacks": bool(policy.get("allow_fallbacks", True)),
        "capacity": {p: _capacity(capacity.get(p)) for p in FREE if _capacity(capacity.get(p))},
    }


def validate(policy: dict) -> dict:
    """The policy to save, or ValueError when it would leave no free plan switched on."""
    unknown = [p for p in (policy.get("order") or []) if p not in DEFAULT_ORDER]
    if unknown:
        raise ValueError("Unknown AI on the route: " + ", ".join(unknown))
    for provider, value in (policy.get("capacity") or {}).items():
        if value not in (None, "", 0) and not (_capacity(value) and 10_000 <= _capacity(value) <= 1_000_000_000):
            raise ValueError(f"The limit for {label(provider)} should be a number of tokens between 10,000 and 1,000,000,000.")
    clean = normalise(policy)
    if not any(clean["enabled"][p] for p in FREE):
        raise ValueError("Keep at least one free plan (Kimi, Codex or Claude) switched on.")
    return clean


def policy_from(preferences: dict | None) -> dict:
    return normalise((preferences or {}).get("route"))


def ordered(policy: dict) -> list:
    """The route order with paid endpoints moved last: a free plan with room always goes first."""
    order = normalise(policy)["order"]
    return [p for p in order if not is_paid(p)] + [p for p in order if is_paid(p)]


# ----- readiness and status -------------------------------------------------------

def ready_from(ready_map: dict) -> bool:
    return any(ready_map.get(p) for p in DEFAULT_ORDER)


def ready(root, ready_map: dict | None = None) -> bool:
    if ready_map is None:
        from backend.ai import ready_providers

        ready_map = ready_providers(root)
    return ready_from(ready_map)


def when_text(iso: str | None) -> str:
    """"5:40 PM" today, "Thu 9:00 AM" on another day, in this PC's time."""
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return iso
    now = datetime.now().astimezone()
    clock = when.strftime("%I:%M %p").lstrip("0")
    return clock if when.date() == now.date() else when.strftime("%a ") + clock


def status(root, policy: dict | None = None, *, ready_map: dict | None = None, book=None, paid: dict | None = None) -> list:
    """The route as the pages and the CLI show it, in the order it is tried."""
    from backend.ai import ready_providers

    policy = normalise(policy)
    ready_map = ready_map if ready_map is not None else ready_providers(root)
    book = book or limits.HealthBook(root)
    health = book.status()
    rows = []
    for position, provider in enumerate(ordered(policy), start=1):
        entry = health.get(provider) or {}
        resting = entry.get("resting")
        usage = None
        if not is_paid(provider):
            usage = book.usage(provider, policy["capacity"].get(provider))
            usage["resets_text"] = when_text(usage["window_resets"])
        rows.append({
            "usage": usage,
            "provider": provider, "label": label(provider), "position": position,
            "enabled": policy["enabled"][provider], "ready": bool(ready_map.get(provider)),
            "paid": is_paid(provider),
            "models": {"strong": model_for(root, provider, "strong"), "cheap": model_for(root, provider, "cheap")},
            "capabilities": sorted(CAPABILITIES.get(provider, ())),
            "resting": ({**resting, "until_text": when_text(resting["until"])} if resting else None),
            "last_ok": entry.get("last_ok"), "last_error": entry.get("last_error"),
            "served_today": entry.get("served_today", 0),
            "paid_block": (paid or {}).get("block") if is_paid(provider) else None,
        })
    return rows


# ----- routing ---------------------------------------------------------------------

def _plan(root, policy: dict, tier: str, needs: dict, ready_map: dict, book) -> tuple[list, list]:
    candidates, skipped = [], []
    for provider in ordered(policy):
        if not policy["enabled"][provider]:
            skipped.append((provider, "switched off in Settings"))
            continue
        if not ready_map.get(provider):
            skipped.append((provider, "not set up on this PC"))
            continue
        missing = [name for name, needed in needs.items() if needed and name not in CAPABILITIES.get(provider, ())]
        if missing:
            skipped.append((provider, NEED_WORDS.get(missing[0], "cannot do this step")))
            continue
        resting = book.resting(provider)
        if resting:
            why = "usage limit" if resting["kind"] == "limit" else "failing"
            skipped.append((provider, f"resting until {when_text(resting['until'])} ({why})"))
            continue
        model = model_for(root, provider, tier)
        if not model:
            skipped.append((provider, "has no model to run"))
            continue
        candidates.append((provider, model))
    return candidates, skipped


def available(root, policy: dict | None = None, tier: str = "cheap", *, ready_map: dict | None = None,
              book=None) -> list:
    """The endpoints that could take a step right now, in route order: switched on, set up, not resting."""
    from backend.ai import ready_providers

    ready_map = ready_map if ready_map is not None else ready_providers(root)
    return _plan(root, normalise(policy), tier, {}, ready_map, book or limits.HealthBook(root))[0]


def free_only(policy: dict | None) -> dict:
    """The same route with every paid endpoint switched off: for steps that must never cost money."""
    clean = normalise(policy)
    return {**clean, "enabled": {p: on and not is_paid(p) for p, on in clean["enabled"].items()}}


def _summary(skipped: list, errors: list) -> str:
    said = {}
    for provider, reason in skipped:
        said.setdefault(provider, reason)
    for provider, message in errors:
        said[provider] = ("usage limit reached" if limits.is_limit(message) else "failed") + f": {message[:160]}"
    if not said:
        return "No AI is set up on this PC for this step. Sign in to Kimi Code, Codex or Claude Code, or add an Azure key."
    return "No AI could take this step. " + " ".join(f"{label(p)}: {r}." for p, r in said.items())


def route(root, *, tier: str, attempt, needs: dict | None = None, policy: dict | None = None,
          ready_map: dict | None = None, book=None, paid_gate=None, on_switch=None):
    """Run ``attempt(provider, model)`` on the first endpoint that can take the step.

    Returns ``(result, (provider, model))``: the answer and the endpoint that
    served it. ``paid_gate(provider)`` returns a reason to skip a paid endpoint
    (today's paid limit is used up) or None. ``on_switch(event)`` is told when a
    failed endpoint was replaced by another. Raises RouteError when nothing is
    left, or the endpoint's own error when fallbacks are off.
    """
    from backend.ai import ready_providers

    policy = normalise(policy)
    ready_map = ready_map if ready_map is not None else ready_providers(root)
    book = book or limits.HealthBook(root)
    candidates, skipped = _plan(root, policy, tier, needs or {}, ready_map, book)
    errors = []
    first = None
    for provider, model in candidates:
        if is_paid(provider) and paid_gate:
            blocked = paid_gate(provider)
            if blocked:
                skipped.append((provider, blocked))
                continue
        first = first or (provider, model)
        try:
            result = attempt(provider, model)
        except Exception as error:  # a runtime's ValueError or a specialist's AgentError
            message = str(error)
            if limits.is_limit(message):
                book.rest_for_limit(provider, message, paid=is_paid(provider))
            else:
                book.record(provider, False, message)
            errors.append((provider, message))
            if not policy["allow_fallbacks"]:
                raise
            continue
        book.record(provider, True)
        if errors and on_switch:
            on_switch({"from_provider": first[0], "from_model": first[1], "to_provider": provider,
                       "to_model": model, "reason": errors[-1][1][:300]})
        return result, (provider, model)
    raise RouteError(_summary(skipped, errors))
