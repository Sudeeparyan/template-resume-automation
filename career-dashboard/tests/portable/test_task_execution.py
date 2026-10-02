"""Worker recovery, concurrent budgets and cache isolation use disposable SQLite only."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.ai import usage_recorder
from backend.services.agent_cache import AgentCache, paid_calls_today, paid_invocation
from backend.services.task_execution import LeaseLost, TaskBusy, TaskRepository, invocation_slot


def service_at(root, limit=10):
    root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect():
        db = sqlite3.connect(root / "runtime.db", timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS ai_calls(id TEXT PRIMARY KEY,cache_key TEXT,
            day TEXT,state TEXT,created_at TEXT,error TEXT,provider TEXT,model TEXT,action TEXT,
            cache_version TEXT,input_tokens INTEGER,output_tokens INTEGER)""")
    return SimpleNamespace(
        w=SimpleNamespace(root=root, connect=connect),
        today=lambda: datetime.now(timezone.utc).date().isoformat(),
        now=lambda: datetime.now(timezone.utc).isoformat(),
        pref=lambda key, default=None: {"daily_call_limit": limit} if key == "ai_policy" else default,
    )


def test_stage_fingerprint_reuses_only_identical_dependency_versions(tmp_path):
    repo = TaskRepository(service_at(tmp_path))
    calls = []
    first = {"jd": "posting-v1", "evidence": "profile-v1", "policy": 1}
    result = repo.run("fit", first, lambda: calls.append(1) or {"score": 70}, job_id="sample")
    assert repo.run("fit", dict(reversed(list(first.items()))), lambda: pytest.fail("repeated"), job_id="sample") == result
    repo.run("fit", {**first, "policy": 2}, lambda: calls.append(2) or {"score": 65}, job_id="sample")
    repo.run("fit", first, lambda: calls.append(3) or {"score": 60}, job_id="another")
    assert calls == [1, 2, 3]


def test_expired_lease_is_fenced_and_live_worker_survives_recovery(tmp_path):
    stamp = [100.0]
    repo = TaskRepository(service_at(tmp_path), clock=lambda: stamp[0])
    task = repo.enqueue("review", {"pdf": "one"})
    assert repo.claim(task["id"], "old", lease_seconds=10)
    stamp[0] = 105
    assert repo.recover_expired() == 0
    assert repo.claim(task["id"], "new", lease_seconds=10) is None
    stamp[0] = 111
    assert repo.recover_expired() == 1
    assert repo.claim(task["id"], "new", lease_seconds=10)["attempts"] == 2
    with pytest.raises(LeaseLost):
        repo.complete(task["id"], "old", {"wrong": True})
    assert not repo.heartbeat(task["id"], "old")
    repo.complete(task["id"], "new", {"review": "current"})
    assert repo.get(task["id"])["result"] == {"review": "current"}


def test_retry_delay_attempt_limit_and_lost_artifact_rebuild(tmp_path):
    stamp = [100.0]
    repo = TaskRepository(service_at(tmp_path), clock=lambda: stamp[0])
    task = repo.enqueue("research", {"posting": "one"}, max_attempts=2)
    repo.claim(task["id"], "first")
    repo.fail(task["id"], "first", "network", retryable=True, retry_after=10)
    assert repo.claim(task["id"], "too-early") is None
    stamp[0] = 110
    assert repo.claim(task["id"], "last")
    repo.fail(task["id"], "last", "network", retryable=True)
    assert repo.get(task["id"])["state"] == "failed"
    assert repo.claim(task["id"], "over-limit") is None
    repo.run("pdf", {"draft": 1}, lambda: {"artifact": "removed"})
    assert repo.run("pdf", {"draft": 1}, lambda: {"artifact": "rebuilt"},
                    validate_result=lambda result: False) == {"artifact": "rebuilt"}


def test_atomic_claim_allows_only_one_worker(tmp_path):
    service = service_at(tmp_path)
    first, second = TaskRepository(service), TaskRepository(service)
    task = first.enqueue("tailor", {"draft": 1})
    barrier = threading.Barrier(2)

    def claim(pair):
        repo, owner = pair
        barrier.wait(timeout=5)
        return bool(repo.claim(task["id"], owner))

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, [(first, "one"), (second, "two")])) == [False, True]
    with pytest.raises(TaskBusy):
        first.run("tailor", {"draft": 1}, lambda: pytest.fail("duplicate"))


def test_unrelated_cache_keys_run_concurrently(tmp_path):
    cache = AgentCache(service_at(tmp_path))
    barrier = threading.Barrier(2)

    def invoke(prompt, schema, **options):
        barrier.wait(timeout=5)  # A cache-wide network lock would time out here.
        return {"value": prompt}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda prompt: cache.execute(invoke, prompt, {"required": ["value"]},
                                                           provider="codex", web=False), ["one", "two"]))
    assert results == [{"value": "one"}, {"value": "two"}]


def test_identical_cache_key_is_singleflight_across_service_instances(tmp_path):
    service = service_at(tmp_path)
    caches = [AgentCache(service), AgentCache(service)]
    start = threading.Barrier(2)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def invoke(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=5)
        return {"value": "same"}

    def execute(cache):
        start.wait(timeout=5)
        return cache.execute(invoke, "same", {"required": ["value"]}, provider="codex", web=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute, cache) for cache in caches]
        assert entered.wait(timeout=5)
        release.set()
        assert [f.result(timeout=5) for f in futures] == [{"value": "same"}] * 2
    assert calls == [1]


def test_failed_cache_call_does_not_poison_retry(tmp_path):
    cache = AgentCache(service_at(tmp_path))
    with pytest.raises(ValueError, match="incomplete"):
        cache.execute(lambda *a, **k: {}, "same", {"required": ["value"]}, provider="codex")
    assert cache.execute(lambda *a, **k: {"value": 2}, "same", {"required": ["value"]}, provider="codex") == {"value": 2}


def test_paid_reservation_is_atomic_across_different_providers(tmp_path):
    service = service_at(tmp_path, limit=1)
    AgentCache(service)
    start = threading.Barrier(2)

    def attempt(provider):
        start.wait(timeout=5)
        try:
            with paid_invocation(service, provider, "sample", "test"):
                return "called"
        except ValueError as error:
            assert "limit" in str(error)
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ["openai", "azure_openai"])) == ["blocked", "called"]
    assert paid_calls_today(service) == 1


def test_paid_operation_and_telemetry_count_once_and_free_work_survives_limit(tmp_path):
    service = service_at(tmp_path, limit=1)
    cache = AgentCache(service)

    def invoke(*args, **kwargs):
        with paid_invocation(service, "azure_openai", "sample", "test"):
            usage_recorder(service)({"provider": "azure_openai", "input_tokens": 10, "output_tokens": 5})
            return {"value": "paid"}

    options = {"provider": "azure_openai", "model": "sample", "managed_budget": True, "web": False}
    assert cache.execute(invoke, "one", {"required": ["value"]}, **options) == {"value": "paid"}
    assert cache.execute(invoke, "one", {"required": ["value"]}, **options) == {"value": "paid"}
    stats = cache.stats()
    assert stats["paid_calls_today"] == stats["calls_today"] == 1
    assert stats["by_provider"] == [{"provider": "azure_openai", "calls": 1, "input_tokens": 10, "output_tokens": 5}]
    with pytest.raises(ValueError, match="limit"):
        cache.execute(invoke, "two", {"required": ["value"]}, **options)
    assert cache.execute(lambda *a, **k: {"value": "free"}, "free", {"required": ["value"]},
                         provider="codex") == {"value": "free"}
    assert paid_calls_today(service) == 1


def test_failed_paid_attempt_is_counted(tmp_path):
    service = service_at(tmp_path, limit=1)
    AgentCache(service)
    with pytest.raises(TimeoutError):
        with paid_invocation(service, "openai", "sample", "test"):
            raise TimeoutError("provider timed out")
    assert paid_calls_today(service) == 1
    with pytest.raises(ValueError, match="limit"):
        with paid_invocation(service, "openai", "sample", "test"):
            pytest.fail("overspent")


def test_global_slots_allow_two_providers_but_serialize_each_provider():
    both, release = threading.Barrier(3), threading.Event()

    def hold(provider):
        with invocation_slot(provider):
            with invocation_slot(provider):  # router/adapter nesting takes one slot
                both.wait(timeout=5)
                assert release.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(hold, provider) for provider in ("codex", "kimi_cli")]
        both.wait(timeout=5)
        try:
            with pytest.raises(TimeoutError):
                with invocation_slot("claude_code", timeout=0.01):
                    pytest.fail("third global call")
            with pytest.raises(TimeoutError):
                with invocation_slot("codex", timeout=0.01):
                    pytest.fail("second provider call")
        finally:
            release.set()
        for job in jobs:
            job.result(timeout=5)
    with invocation_slot("codex", timeout=0.1):
        pass  # all permits were released
