"""Orchestration for the specialist agents.

Routing is deterministic: the workflow decides which specialist runs, not a
model. That keeps a run's cost predictable and means a routing bug is a code
bug rather than a prompt bug. Models are used for judgement inside a step, never
to choose the next step.

Isolation: the hiring-manager agent must only ever see the job description and
public company research. It is called through `run_isolated`, which accepts a
fixed, narrow payload and cannot be handed the candidate profile. The rule comes
from AGENTS.md and is asserted by tests/test_ai_agents.py.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from backend.ai import catalog, claude_code, codex, kimi_cli, models, router
from backend.ai.agents.specialists import GROUNDING, REGISTRY, Specialist


# Provider errors quote request URLs, key-management links and account IDs. None
# of that belongs in text shown to the user, so failures are reported by class.
_ERROR_BY_STATUS = {
    401: "the API key was rejected",
    402: "the account is out of credits",
    403: "the account is not permitted to use this model",
    404: "the model is not available to this account",
    429: "the provider's rate limit or quota is exhausted",
}


def describe_provider_error(error: Exception) -> str:
    """A short, safe description of why a provider call failed."""
    status = getattr(error, "status_code", None) or getattr(
        getattr(error, "response", None), "status_code", None
    )
    if status is None:
        text = str(error)
        for code in _ERROR_BY_STATUS:
            if f"Error code: {code}" in text or f"({code})" in text:
                status = code
                break
    if status in _ERROR_BY_STATUS:
        return _ERROR_BY_STATUS[status]
    # SDKs differ: some raise typed errors carrying no HTTP status.
    name = type(error).__name__
    for marker, description in (
        ("RateLimit", "the provider's rate limit or quota is exhausted"),
        ("ResourceExhausted", "the provider's rate limit or quota is exhausted"),
        ("PermissionDenied", "the account is not permitted to use this model"),
        ("Authentication", "the API key was rejected"),
        ("NotFound", "the model is not available to this account"),
        ("Timeout", "the provider could not be reached"),
        ("Connection", "the provider could not be reached"),
    ):
        if marker in name:
            return description
    return f"the provider returned an unexpected {name}"


class AgentError(RuntimeError):
    """A specialist could not produce a usable result."""


SCHEMA_MISMATCH = "did not match its schema"


def schema_problem(error: Exception) -> str:
    """The first few reasons an answer failed its schema, short enough to hand back to the model."""
    errors = getattr(error, "errors", None)
    if callable(errors):
        try:
            parts = [".".join(str(p) for p in item.get("loc", ())) + ": " + str(item.get("msg", ""))
                     for item in errors()[:3]]
            return "; ".join(part for part in parts if part.strip(": "))[:400]
        except Exception:  # noqa: BLE001 - a description is a courtesy, never a new failure
            pass
    return " ".join(str(error).split())[:400]


class AgentTeam:
    """Runs specialists against the configured provider, tier by tier.

    `tiers` maps "strong"/"cheap" to a (provider_id, model) pair. The caller
    supplies it from saved preferences so the choice stays a runtime setting.
    `fallback` is the backup (provider_id, model) Settings names: a call that
    fails on its tier's provider (quota, outage, bad output) is tried there once,
    as the gateway does for background runs, and `fell_back` says it happened.

    A tier set to ``auto`` goes through the router (backend/ai/router.py): Kimi,
    Codex and Claude first, Azure last. `route` carries the saved route policy,
    the paid-call gate and the switch log (``backend.ai.route_options``); the
    gate also applies to a tier set to a paid provider directly. `served` names
    the endpoint that answered the last call.
    """

    def __init__(self, root: Path, tiers: dict, on_usage=None, fallback: tuple | None = None,
                 persona: dict | None = None, route: dict | None = None):
        self.root = Path(root)
        # Whose workspace this team works for (backend/ai/persona.py); None = the backup
        # profile, whose prompts are the original text.
        self.persona = persona
        self.tiers = tiers
        self.on_usage = on_usage
        self.fallback = tuple(fallback) if fallback else None
        self.fell_back: dict | None = None
        self.route = route or {}
        self.served: tuple | None = None

    @classmethod
    def from_preferences(cls, root: Path, preferences: dict, on_usage=None, persona: dict | None = None) -> "AgentTeam":
        tiers = {}
        for tier in catalog.TIERS:
            chosen = (preferences.get("tiers") or {}).get(tier) or {}
            provider = chosen.get("provider") or "openrouter"
            tiers[tier] = (provider, chosen.get("model") or catalog.default_model(provider, tier))
        return cls(root, tiers, on_usage, persona=persona)

    def _record(self, agent: Specialist, raw, started: float, provider: str, model: str, chars: int = 0) -> None:
        usage = raw if isinstance(raw, dict) else getattr(raw, "usage_metadata", None) or {}
        self._meter(provider, usage, chars, agent.needs_web)
        if not self.on_usage:
            return
        self.on_usage({
            "agent": agent.name,
            "tier": agent.tier,
            "provider": provider,
            "model": model,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "seconds": round(time.time() - started, 2),
        })

    def _meter(self, provider: str, usage: dict, chars: int, web: bool) -> None:
        """Count one call against a free plan's 5-hour window (real tokens when reported)."""
        if provider not in router.FREE:
            return
        from backend.ai import limits

        try:
            reported = [usage.get("input_tokens"), usage.get("output_tokens")]
            tokens = (sum(reported) if all(isinstance(n, int) for n in reported)
                      else limits.estimate_tokens(chars, web))
            limits.HealthBook(self.root).meter(provider, tokens)
        except Exception:
            pass  # metering is advice; it must never fail a finished step

    def run(self, agent_name: str, payload: Any, *, max_tokens: int | None = None):
        """Run one specialist and return its parsed, schema-valid result."""
        agent = REGISTRY[agent_name]
        if agent.isolated:
            raise AgentError(
                f"{agent.name} is isolated from candidate data and must be called via run_isolated()"
            )
        return self._invoke(agent, payload, max_tokens or agent.max_tokens)

    def run_isolated(self, agent_name: str, *, job_description: str, public_research: str = "",
                     max_tokens: int | None = None):
        """Run an isolated specialist. Only the two fields below can reach it."""
        agent = REGISTRY[agent_name]
        if not agent.isolated:
            raise AgentError(f"{agent.name} is not an isolated agent")
        payload = {"job_description": job_description, "public_company_research": public_research}
        return self._invoke(agent, payload, max_tokens or agent.max_tokens)

    def _invoke(self, agent: Specialist, payload: Any, max_tokens: int):
        provider, model = self.tiers[agent.tier]
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
        self.fell_back = None
        if provider == router.ID:
            return self._invoke_routed(agent, text, max_tokens)
        self._check_paid(agent, provider)
        self.served = (provider, model)
        try:
            return self._repairing(agent, text, provider, model, max_tokens)
        except AgentError as error:
            if not self.fallback or self.fallback[0] == provider:
                raise
            backup, backup_model = self.fallback
            try:
                self._check_paid(agent, backup)
                result = self._repairing(agent, text, backup, backup_model, max_tokens)
            except AgentError:
                raise error from None
            self.fell_back = {"agent": agent.name, "from_provider": provider, "from_model": model,
                              "to_provider": backup, "to_model": backup_model, "reason": str(error)[:300]}
            self.served = (backup, backup_model)
            return result

    def _check_paid(self, agent: Specialist, provider: str) -> None:
        """A paid provider runs only while today's paid-call limit has room."""
        gate = self.route.get("paid_gate")
        if gate and router.is_paid(provider):
            blocked = gate(provider)
            if blocked:
                raise AgentError(f"{agent.name} was not run on {provider}: {blocked}. Choose Auto or a "
                                 "free plan in Settings, or raise the paid limit there.")

    def _invoke_routed(self, agent: Specialist, text: str, max_tokens: int):
        """The step through the router: the first endpoint on the route that can take it."""
        switched = {}

        def on_switch(event):
            switched.update(event)
            if self.route.get("on_switch"):
                self.route["on_switch"]({**event, "agent": agent.name})

        try:
            result, served = router.route(
                self.root, tier=agent.tier, policy=self.route.get("policy"),
                ready_map=self.route.get("ready"), paid_gate=self.route.get("paid_gate"), on_switch=on_switch,
                attempt=lambda provider, model: self._repairing(agent, text, provider, model, max_tokens),
            )
        except router.RouteError as error:
            raise AgentError(f"{agent.name} could not run: {error}") from None
        self.served = served
        if switched:
            self.fell_back = {"agent": agent.name, "from_provider": switched["from_provider"],
                              "from_model": switched["from_model"], "to_provider": served[0],
                              "to_model": served[1], "reason": switched["reason"],
                              "recorded": bool(self.route.get("on_switch"))}
        return result

    def _repairing(self, agent: Specialist, text: str, provider: str, model: str, max_tokens: int):
        """One call, and when its answer has the wrong shape, one more on the same endpoint
        with the reasons, before the route (or the backup) moves on to another AI."""
        try:
            return self._invoke_on(agent, text, provider, model, max_tokens)
        except AgentError as error:
            if SCHEMA_MISMATCH not in str(error):
                raise
            retry = (text + "\n\nYOUR PREVIOUS ANSWER WAS REJECTED because it did not match the required "
                     "output shape (" + str(error).split(SCHEMA_MISMATCH, 1)[1].strip(" ():") + "). "
                     "Return one corrected answer in exactly that shape.")
            return self._invoke_on(agent, retry, provider, model, max_tokens)

    def _invoke_on(self, agent: Specialist, text: str, provider: str, model: str, max_tokens: int):
        if provider in (claude_code.ID, codex.ID, kimi_cli.ID):
            return self._invoke_local(agent, text, provider, model)
        llm = models.build(self.root, provider, model, max_tokens=max_tokens)
        structured = llm.with_structured_output(agent.schema, include_raw=True)
        started = time.time()
        try:
            result = structured.invoke(agent.prompt(text, self.persona))
        except Exception as error:  # provider, network or quota failure
            raise AgentError(
                f"{agent.name} could not run on {provider}/{model}: {describe_provider_error(error)}"
            ) from error
        self._record(agent, result.get("raw"), started, provider, model)
        if result.get("parsing_error") or result.get("parsed") is None:
            detail = schema_problem(result["parsing_error"]) if result.get("parsing_error") else "no answer"
            raise AgentError(f"{agent.name} returned output that {SCHEMA_MISMATCH} ({detail})")
        return result["parsed"]

    def _invoke_local(self, agent: Specialist, text: str, provider: str, model: str):
        """The same step through a local CLI (Claude Code, Codex or Kimi Code)
        instead of LangChain.

        The specialist's system text and schema are passed as they are; the
        isolation rules above apply unchanged because the payload is built
        before this point.
        """
        from pydantic import ValidationError

        runtime = {claude_code.ID: claude_code, codex.ID: codex, kimi_cli.ID: kimi_cli}[provider]
        started = time.time()
        system = agent.system_for(self.persona) + "\n\n" + GROUNDING
        try:
            raw, usage = runtime.run(
                text, agent.schema.model_json_schema(), model=model,
                web=agent.needs_web, system=system,
            )
        except ValueError as error:
            raise AgentError(f"{agent.name} could not run on {provider}/{model}: {error}") from error
        self._record(agent, usage, started, provider, model,
                     chars=len(text) + len(system) + len(json.dumps(raw, ensure_ascii=False, default=str)))
        try:
            return agent.schema.model_validate(raw)
        except ValidationError as error:
            raise AgentError(f"{agent.name} returned output that {SCHEMA_MISMATCH} ({schema_problem(error)})") from None
