"""Agent requests and terminal statuses reflect their durable work, using no live AI."""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from backend.services import agents as agent_module
from backend.services.agents import AgentRunner
from backend.services.task_execution import TaskRepository, job_write_slot
from test_hunt import JD, ireland_profile


class PendingPool:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))


@pytest.fixture
def worker(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    runner = AgentRunner(services)
    runner.pool.shutdown(wait=True)
    runner.pool = PendingPool()
    monkeypatch.setattr(runner.gateway, "resolve", lambda *args, **kwargs:
                        (SimpleNamespace(id="codex", capabilities={"web", "structured"}), "sample"))
    monkeypatch.setattr(services, "export_state", lambda: None)
    monkeypatch.setattr(services.w, "export_tracking", lambda: None)
    timers = []

    class PendingTimer:
        def __init__(self, seconds, callback):
            self.seconds, self.callback = seconds, callback

        def start(self):
            timers.append(self)

    monkeypatch.setattr(agent_module.threading, "Timer", PendingTimer)
    return services, runner, timers


def insert_run(services, kind="salary_research", *, job_id=None, free_only=False):
    run_id = uuid.uuid4().hex
    with services.w.connect() as db:
        db.execute("INSERT INTO agent_runs(id,kind,job_id,state,input,created_at,updated_at,provider,model,preset) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (run_id, kind, job_id, "queued", json.dumps({"_free_only": free_only}),
                    services.now(), services.now(), "codex", "sample", "default"))
    return run_id


def record(services, run_id):
    with services.w.connect() as db:
        return dict(db.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone())


def test_distinct_discovery_presets_never_share_a_run(worker):
    _, runner, _ = worker
    options = {"count": 1, "focus": {"hunt": True}}
    web = runner.enqueue("discovery", preset="default", **options)
    feeds = runner.enqueue("discovery", preset="feeds", **options)
    assert web["id"] != feeds["id"]
    assert runner.enqueue("discovery", preset="feeds", **options)["id"] == feeds["id"]


def test_reusing_a_running_request_reports_its_actual_state(worker):
    services, runner, _ = worker
    options = {"count": 1, "focus": {"hunt": True}}
    queued = runner.enqueue("discovery", preset="feeds", **options)
    runner.update(queued["id"], "running", {"stage": "Reading feeds"})
    assert runner.enqueue("discovery", preset="feeds", **options)["state"] == "running"


def test_success_is_published_only_after_the_durable_stage_commits(worker, monkeypatch):
    services, runner, _ = worker
    run_id = insert_run(services)
    monkeypatch.setattr(runner, "research_salary", lambda job_id: {"stage": "Complete", "checked": True})
    ready, release = threading.Event(), threading.Event()
    original_complete = TaskRepository.complete

    def complete(repo, task_id, owner, result):
        ready.set()
        assert release.wait(timeout=5)
        return original_complete(repo, task_id, owner, result)

    monkeypatch.setattr(TaskRepository, "complete", complete)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.run, run_id)
        try:
            assert ready.wait(timeout=5)
            assert record(services, run_id)["state"] == "running"
        finally:
            release.set()
        future.result(timeout=5)
    assert record(services, run_id)["state"] == "completed"


def test_job_associated_agents_share_the_pipeline_writer_slot(worker, monkeypatch):
    services, runner, _ = worker
    job = services.w.add_job("Sample Company", "Analyst", "Dublin, Ireland", "https://jobs.example/sample",
                             JD)
    run_id = insert_run(services, job_id=job["id"])
    claimed, entered = threading.Event(), threading.Event()
    original_claim = TaskRepository.claim

    def claim(repo, *args, **kwargs):
        result = original_claim(repo, *args, **kwargs)
        claimed.set()
        return result

    def salary(job_id):
        entered.set()
        return {"stage": "Complete", "checked": True}

    monkeypatch.setattr(TaskRepository, "claim", claim)
    monkeypatch.setattr(runner, "research_salary", salary)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with job_write_slot(services.w.root, job["id"]):
            future = pool.submit(runner.run, run_id)
            assert claimed.wait(timeout=5)
            assert not entered.wait(timeout=0.15)  # another writer owns this job
        future.result(timeout=5)
    assert entered.is_set() and record(services, run_id)["state"] == "completed"


def test_a_transient_attempt_stays_nonterminal_until_its_retry(worker, monkeypatch):
    services, runner, timers = worker
    run_id = insert_run(services, free_only=True)
    attempts, transitions = [], []
    original_update = runner.update

    def update(run_id, state, *args, **kwargs):
        transitions.append(state)
        return original_update(run_id, state, *args, **kwargs)

    def salary(job_id):
        attempts.append(runner.gateway.providers["auto"].local.free_only)
        if len(attempts) == 1:
            raise TimeoutError("Temporary connection timeout")
        return {"stage": "Complete", "checked": True}

    monkeypatch.setattr(runner, "update", update)
    monkeypatch.setattr(runner, "research_salary", salary)
    runner.run(run_id)
    assert record(services, run_id)["state"] == "queued" and len(timers) == 1
    assert "failed" not in transitions
    with services.w.connect() as db:
        db.execute("UPDATE execution_tasks SET retry_at=0 WHERE stage='agent:salary_research'")
    runner.run(run_id)
    assert record(services, run_id)["state"] == "completed"
    assert attempts == [True, True]  # the persisted policy survives the timer/restart boundary
    assert transitions.count("completed") == 1


def test_partial_edit_failure_is_never_replayed_automatically(worker, monkeypatch):
    services, runner, timers = worker
    run_id = insert_run(services, kind="instruction_interpret")

    def partial_edit(run_id):
        raise TimeoutError("Connection timed out after applying a draft edit")

    monkeypatch.setattr(runner, "_run", partial_edit)
    runner.run(run_id)
    assert record(services, run_id)["state"] == "failed"
    assert timers == []


def test_a_terminal_failure_is_published_once(worker, monkeypatch):
    services, runner, timers = worker
    run_id = insert_run(services)
    transitions = []
    original_update = runner.update

    def update(run_id, state, *args, **kwargs):
        transitions.append(state)
        return original_update(run_id, state, *args, **kwargs)

    monkeypatch.setattr(runner, "update", update)
    monkeypatch.setattr(runner, "research_salary", lambda job_id: (_ for _ in ()).throw(ValueError("Missing salary sources")))
    runner.run(run_id)
    assert record(services, run_id)["state"] == "failed"
    assert transitions.count("failed") == 1 and timers == []
