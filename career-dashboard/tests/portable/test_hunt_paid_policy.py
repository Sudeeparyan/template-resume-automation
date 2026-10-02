"""Unattended hunts keep paid endpoints off unless this run permits them."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import sqlite3
from types import SimpleNamespace

import pytest

from backend.ai import router
from backend.ai.providers import AIGateway, RouterProvider
from backend.services.hunt import DEFAULTS, Hunt


def hunt_with_providers(*, free_ready=True):
    hunt = object.__new__(Hunt)
    choices = [{"id": "auto", "ready": True, "models": [{"id": "auto"}]},
               {"id": "codex", "ready": free_ready, "models": [{"id": "sample"}]},
               {"id": "azure_openai", "ready": True, "models": [{"id": "sample"}]}]
    hunt.pipeline = SimpleNamespace(providers=lambda: choices)
    hunt.preferences = lambda: {**DEFAULTS, "provider": "azure_openai", "model": "sample"}
    return hunt


def test_hunt_uses_free_route_when_paid_ai_was_not_permitted():
    config = hunt_with_providers().validate({"allow_paid": False})
    assert config["provider"] == router.ID and config["model"] == router.ID
    assert config["allow_paid"] is False


def test_paid_only_machine_reads_feeds_and_holds_for_a_free_plan():
    config = hunt_with_providers(free_ready=False).validate({"allow_paid": False})
    assert config["provider"] == router.ID and config["sources"] == "feeds"
    assert config["no_ai"] is True


def test_explicit_paid_permission_preserves_the_selected_provider():
    config = hunt_with_providers().validate({"allow_paid": True})
    assert config["provider"] == "azure_openai" and config["allow_paid"] is True


class StubEndpoint:
    models = ("sample",)
    capabilities = {"structured", "web"}
    configured = True

    def __init__(self, name, calls, *, failing=False):
        self.id, self.calls, self.failing = name, calls, failing

    def generate(self, prompt, schema, **options):
        self.calls.append(self.id)
        if self.failing:
            raise ValueError("The free plan failed")
        return {"provider": self.id}


def gateway_stub(tmp_path):
    calls = []
    preferences = {"default": {"provider": "codex", "model": "sample"},
                   "fallback": {"provider": "azure_openai", "model": "sample"}}

    @contextmanager
    def connect():
        db = sqlite3.connect(tmp_path / "runtime.db", timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    with connect() as db:
        db.execute("""CREATE TABLE ai_calls(id TEXT PRIMARY KEY,cache_key TEXT,day TEXT,
            state TEXT,created_at TEXT,error TEXT,provider TEXT,model TEXT,action TEXT,
            cache_version TEXT,input_tokens INTEGER,output_tokens INTEGER)""")

    service = SimpleNamespace(pref=lambda key, default=None: preferences if key == "ai_preferences" else default,
                              today=lambda: "2026-01-01",
                              now=lambda: "2026-01-01T00:00:00+00:00",
                              w=SimpleNamespace(root=tmp_path, connect=connect, record_event=lambda *args, **kwargs: None))
    gateway = object.__new__(AIGateway)
    gateway.s = service
    gateway.providers = {"codex": StubEndpoint("codex", calls, failing=True),
                         "azure_openai": StubEndpoint("azure_openai", calls)}
    gateway.providers[router.ID] = RouterProvider(gateway)
    return gateway, calls


def test_free_only_run_refuses_named_paid_provider_before_any_call(tmp_path):
    gateway, calls = gateway_stub(tmp_path)
    gateway.providers[router.ID].local.free_only = True
    with pytest.raises(ValueError, match="paid"):
        gateway.generate("document_review", "sample", {}, provider="azure_openai", model="sample")
    assert calls == []


def test_free_only_run_does_not_use_paid_backup_after_a_free_failure(tmp_path):
    gateway, calls = gateway_stub(tmp_path)
    gateway.providers[router.ID].local.free_only = True
    with pytest.raises(ValueError, match="free plan failed"):
        gateway.generate("document_review", "sample", {}, provider="codex", model="sample")
    assert calls == ["codex"]


def test_ordinary_paid_choice_and_backup_still_work(tmp_path):
    gateway, calls = gateway_stub(tmp_path)
    assert gateway.generate("document_review", "sample", {}, provider="azure_openai", model="sample") == {"provider": "azure_openai"}
    assert gateway.generate("document_review", "sample", {}, provider="codex", model="sample") == {"provider": "azure_openai"}
    assert calls == ["azure_openai", "codex", "azure_openai"]


def test_unattended_policy_does_not_block_a_concurrent_authorized_request(tmp_path):
    gateway, calls = gateway_stub(tmp_path)
    barrier = threading.Barrier(2)

    def invoke(free_only):
        gateway.providers[router.ID].local.free_only = free_only
        barrier.wait(timeout=5)
        try:
            return gateway.generate("document_review", "sample", {}, provider="azure_openai", model="sample")
        except ValueError:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, (True, False)))
    assert results == ["blocked", {"provider": "azure_openai"}]
    assert calls == ["azure_openai"]
