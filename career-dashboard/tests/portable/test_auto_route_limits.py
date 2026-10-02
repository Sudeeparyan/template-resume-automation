"""Auto uses another signed-in plan when one reaches its usage window."""

import pytest

from backend.ai import limits, router


def _models(monkeypatch):
    # A routing test should never consult this computer's CLIs or API keys.
    monkeypatch.setattr(router, "model_for", lambda _root, provider, _tier: provider + "-model")


def test_limit_switches_to_next_free_plan_and_rests_the_first(tmp_path, monkeypatch):
    _models(monkeypatch)
    book = limits.HealthBook(tmp_path)
    ready = {provider: provider in router.FREE for provider in router.DEFAULT_ORDER}
    tried, switches = [], []

    def attempt(provider, _model):
        tried.append(provider)
        if provider == "kimi_cli":
            raise ValueError("usage limit reached; try again in 1 hour")
        return {"answered": True}

    answer, served = router.route(tmp_path, tier="cheap", attempt=attempt,
                                  ready_map=ready, book=book, on_switch=switches.append)
    assert answer == {"answered": True}
    assert served == ("codex", "codex-model")
    assert tried == ["kimi_cli", "codex"]
    assert switches[0]["from_provider"] == "kimi_cli"
    assert switches[0]["to_provider"] == "codex"
    assert book.resting("kimi_cli")["kind"] == "limit"

    tried.clear()
    router.route(tmp_path, tier="cheap", attempt=attempt, ready_map=ready, book=book)
    assert tried == ["codex"]


def test_saved_route_and_paid_gate_are_honored(tmp_path, monkeypatch):
    _models(monkeypatch)
    ready = {provider: True for provider in router.DEFAULT_ORDER}
    policy = router.default_policy()
    policy["enabled"]["kimi_cli"] = False
    tried, gated = [], []

    def attempt(provider, _model):
        tried.append(provider)
        raise ValueError("usage limit reached")

    def paid_gate(provider):
        gated.append(provider)
        return "paid AI is switched off"

    with pytest.raises(router.RouteError, match="paid AI is switched off"):
        router.route(tmp_path, tier="strong", attempt=attempt, policy=policy,
                     ready_map=ready, paid_gate=paid_gate)
    assert tried == ["codex", "claude_code"]
    assert gated == ["azure_openai"]
