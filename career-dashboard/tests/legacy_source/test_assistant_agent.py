"""The assistant's agent loop and its toolbox: the model decides, the tools do, the gates hold."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from test_assistant import LINK, POSTING, REFUSAL, StubTeam, assistant, stub_fit, use_team  # noqa: E402,F401
from test_career_workspace import workspace  # noqa: E402,F401
from test_workspace_v2 import service  # noqa: E402,F401
import backend.ai  # noqa: E402
from backend.ai import claude_code, codex, resolve_tiers  # noqa: E402
from backend.ai.agents import schemas  # noqa: E402
from backend.ai.agents.graph import AgentError, AgentTeam  # noqa: E402
from backend.services.assistant import MAX_TURNS  # noqa: E402
from backend.services.assistant_tools import RUNNABLE_AGENTS, Toolbox  # noqa: E402

LABELLED = "Company: Acme Analytics\nTitle: Data Engineer\nLocation: Austin, TX\n" + POSTING + "\n" + LINK


class ScriptedTeam:
    """Plays one AgentTurn per call; a step may be a function of the payload."""

    def __init__(self, *turns):
        self.turns, self.calls = list(turns), []

    def run(self, name, payload, **_options):
        assert name == "workspace_agent"
        self.calls.append(payload)
        step = self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]
        return step(payload) if callable(step) else step


def call(tool, **arguments):
    return schemas.AgentTurn(thought=f"Calling {tool}.", action="call", tool=tool, arguments=json.dumps(arguments))


def reply(text, *suggestions):
    return schemas.AgentTurn(thought="Done.", action="reply", reply=text, suggestions=list(suggestions))


def seed_job(assistant, monkeypatch):
    stub_fit(assistant, monkeypatch)
    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    assert assistant.send(LABELLED, "seed")["data"]["intent"] == "resume_ready"
    return assistant.w.jobs()[0]


# --- the loop ----------------------------------------------------------------

def test_the_agent_reads_then_acts_then_reports(assistant, monkeypatch):
    job = seed_job(assistant, monkeypatch)
    queued = []
    monkeypatch.setattr(assistant.runner, "enqueue", lambda kind, job_id=None, *a, **k: queued.append((kind, job_id)) or {"id": "run1234abcd", "state": "queued"})
    team = ScriptedTeam(
        call("list_jobs", query="acme"),
        lambda payload: call("run_agent", kind="study_plan", job_id=payload["task"][-1]["result"]["jobs"][0]["id"]),
        reply("The study plan for **Acme Analytics** is being written.", "status"),
    )
    use_team(monkeypatch, team)
    done = assistant.send("write the study plan for the Acme role and tell me when it's queued", "m1")
    assert done["state"] == "done" and done["response"].startswith("The study plan")
    assert queued == [("study_plan", job["id"])]
    assert [(s["label"], s["state"], s["agent"]) for s in done["steps"]] == [
        ("Listing saved jobs", "done", "resume_tracker"), ("Starting an agent", "done", "study_plan"), ("Thinking", "done", "assistant")]
    assert done["steps"][1]["run_id"] == "run1234abcd"
    # Each turn saw the tools, the snapshot and the task so far, with tool results appended.
    first, second, third = team.calls
    assert first["task"][-1]["role"] == "user" and {t["name"] for t in first["tools"]} >= {"build_resume", "run_agent"}
    assert second["task"][-1]["tool"] == "list_jobs" and second["task"][-1]["result"]["jobs"][0]["company"] == "Acme Analytics"
    assert third["task"][-1]["tool"] == "run_agent" and third["task"][-1]["result"]["run_id"] == "run1234abcd"
    assert third["turns"] == {"used": 2, "max": MAX_TURNS}
    assert done["data"]["suggestions"] == ["status"]


def test_a_confirm_tool_waits_for_her_yes_and_no_leaves_things_alone(assistant, monkeypatch):
    job = seed_job(assistant, monkeypatch)
    team = ScriptedTeam(
        call("update_job", job_id=job["id"], status="applied", application_date="2026-09-10"),
        lambda payload: reply("Left as it was." if payload["task"][-1].get("declined") else "Recorded as applied on 2026-09-10."),
    )
    use_team(monkeypatch, team)
    ask = assistant.send("mark the acme job as applied on the 10th", "m1")
    assert ask["state"] == "needs_input" and "**Acme Analytics — Data Engineer**: mark it *applied*, record the application date 2026-09-10" in ask["response"]
    assert ask["data"]["suggestions"] == ["yes", "no"] and assistant.s.pref("assistant_pending")["kind"] == "confirm_tool"
    assert assistant.w.jobs()[0]["status"] == "prepared"

    declined = assistant.send("no", "m2")
    assert declined["state"] == "done" and declined["response"] == "Left as it was."
    assert assistant.w.jobs()[0]["application_date"] is None and assistant.s.pref("assistant_pending") is None

    team.turns = [call("update_job", job_id=job["id"], status="applied", application_date="2026-09-10"), reply("Recorded as applied on 2026-09-10.")]
    assistant.send("ok do mark it applied on 2026-09-10", "m3")
    done = assistant.send("yes", "m4")
    assert done["state"] == "done" and done["response"].startswith("Recorded")
    assert done["steps"][0]["label"] == "Updating the job's status" and done["steps"][0]["detail"].startswith("Acme Analytics")
    fresh = assistant.w.jobs()[0]
    assert fresh["status"] == "applied" and fresh["application_date"] == "2026-09-10"


def test_the_agent_can_ask_and_her_answer_continues_the_same_task(assistant, monkeypatch):
    team = ScriptedTeam(
        schemas.AgentTurn(thought="I need the date.", action="ask", reply="Which day did you send it?", suggestions=["today"]),
        lambda payload: reply("Got it: " + payload["task"][-1]["content"]),
    )
    use_team(monkeypatch, team)
    asked = assistant.send("I sent the application", "m1")
    assert asked["state"] == "needs_input" and asked["response"] == "Which day did you send it?"
    assert assistant.s.pref("assistant_pending")["kind"] == "agent"
    answered = assistant.send("2026-09-10", "m2")
    assert answered["state"] == "done" and answered["response"] == "Got it: 2026-09-10"
    task = team.calls[-1]["task"]
    assert [t.get("role") for t in task] == ["user", "assistant", "user"] and task[1]["asked"] == "Which day did you send it?"
    assert assistant.s.pref("assistant_pending") is None


def test_bad_tool_calls_come_back_as_errors_not_crashes(assistant, monkeypatch):
    team = ScriptedTeam(
        call("teleport", where="mars"),
        schemas.AgentTurn(thought="Listing.", action="call", tool="list_jobs", arguments="not json"),
        call("get_job", job_id="nope"),
        reply("Nothing matched."),
    )
    use_team(monkeypatch, team)
    done = assistant.send("do something odd", "m1")
    assert done["state"] == "done" and done["response"] == "Nothing matched."
    failed = [s for s in done["steps"] if s["state"] == "failed"]
    assert len(failed) == 3 and "Unknown tool" in failed[0]["detail"] and "No saved job" in failed[2]["detail"]
    errors = [t["error"] for t in team.calls[-1]["task"] if t.get("error")]
    assert errors[0].startswith("Unknown tool: teleport") and "No saved job has the ID 'nope'" in errors[2]


def test_the_turn_budget_ends_a_runaway_loop(assistant, monkeypatch):
    use_team(monkeypatch, ScriptedTeam(call("status")))
    done = assistant.send("loop forever", "m1")
    assert done["state"] == "failed" and f"stopped after {MAX_TURNS} steps" in done["response"]
    assert sum(s["label"] == "Reading where the search stands" for s in done["steps"]) == MAX_TURNS


def test_build_resume_from_the_loop_hands_back_the_document_card(assistant, monkeypatch):
    job = seed_job(assistant, monkeypatch)
    use_team(monkeypatch, ScriptedTeam(call("build_resume", job_id=job["id"]), reply("Rebuilt.")))
    done = assistant.send("rebuild the acme resume please", "m1")
    assert done["state"] == "done"
    data = done["data"]
    assert data["intent"] == "resume_ready" and data["pdf"].endswith("/resume.pdf") and data["coverage"] == 72 and data["job_id"] == job["id"]
    assert done["steps"][0]["label"] == "Building the one-page resume" and done["steps"][0]["agent"] == "resume"


def test_a_runtime_failure_fails_the_message_without_changing_anything(assistant, monkeypatch):
    use_team(monkeypatch, StubTeam(error="Claude Code is not signed in"))
    before = assistant.w.jobs()
    failed = assistant.send("find me something to do", "m1")
    assert failed["state"] == "failed" and "not signed in" in failed["response"] and assistant.w.jobs() == before


def test_overview_names_the_engine_the_agents_and_the_capabilities(assistant, monkeypatch):
    monkeypatch.setattr(backend.ai, "ready_providers", lambda root: {"claude_code": True, "codex": False, "openrouter": False})
    overview = assistant.overview()
    # Nothing chosen: the chat follows the main choice (Codex here), which is not ready, so it moves.
    engine = overview["engine"]
    assert engine["provider"] == "claude_code" and engine["moved_from"] == "Codex"
    assert engine["runs"] == {"provider": "codex", "model": "codex-runtime", "label": "Codex · codex-runtime", "ready": False}
    assert [o["provider"] for o in engine["options"]] == ["claude_code"] * 3
    linked = {a["id"] for a in overview["agents"] if a["linked"]}
    assert {"assistant", "sponsorship", "reapply", "resume", "resume_match", "research", "study_plan", "discovery", "profile", "resume_tracker"} <= linked
    assert "email" in {a["id"] for a in overview["agents"]} and "email" in linked  # reading synced mail is linked; the sync is not a tool
    assert {c["group"] for c in overview["capabilities"]} == {"Jobs", "Resume", "Agents", "Profile", "Search", "Settings"}


def test_shortcuts_with_conjunctions_go_to_the_agent(assistant, monkeypatch):
    team = ScriptedTeam(reply("Two things, on it."))
    use_team(monkeypatch, team)
    done = assistant.send("research Acme and then write the study plan", "m1")
    assert done["state"] == "done" and team.calls


# --- the toolbox ------------------------------------------------------------------

@pytest.fixture
def tools(assistant):
    return assistant.tools


def test_the_toolbox_covers_every_feature_and_never_the_mail_sync(tools):
    names = set(tools.tools)
    assert {"save_posting", "build_resume", "edit_resume", "run_agent", "wait_for_run", "search_profile", "propose_profile_change",
            "set_goals", "list_mail", "read_policy", "find_jobs", "excluded_postings", "cover_letter"} <= names
    assert "email" not in RUNNABLE_AGENTS and not any("sync" in n and "mail" in n for n in names)
    confirm = {n for n, t in tools.tools.items() if t.confirm}
    assert confirm == {"update_job", "remove_job", "restore_excluded", "apply_profile_change", "reconcile_profile", "set_goals", "resolve_mail"}
    catalogue = {t["name"]: t for t in tools.catalogue()}
    assert catalogue["update_job"]["signature"] == "update_job(job_id, status?, application_date?, notes?)"
    assert "Needs her yes" in catalogue["update_job"]["description"] and "Needs her yes" not in catalogue["list_jobs"]["description"]


def test_confirmations_are_described_in_her_words(tools, assistant, monkeypatch):
    job = seed_job(assistant, monkeypatch)
    assert tools.describe("update_job", {"job_id": job["id"], "status": "rejected"}) == "**Acme Analytics — Data Engineer**: mark it *rejected*"
    assert tools.describe("remove_job", {"job_id": job["id"], "reason": "too senior"}).startswith("Remove **Acme Analytics — Data Engineer** from the active list (too senior)")
    assert tools.describe("set_goals", {"weekly_target": 12, "workdays": [0, 1, 2, 3, 4]}) == "Set the weekly target to **12** applications on Mon, Tue, Wed, Thu, Fri"
    proposal = tools.call("propose_profile_change", {"request": "add certification: AWS Data Engineer | passed 2026-09"})
    described = tools.describe("apply_profile_change", {"change_set_id": proposal["change_set_id"]})
    assert described.startswith("Change your Profile: add certification **AWS Data Engineer** — passed 2026-09") and "wait for your review" in described
    # An unknown ID never crashes the question; it falls back to the tool's label.
    assert tools.describe("update_job", {"job_id": "missing", "status": "applied"}).startswith("Updating the job's status — job_id: missing")


def test_arguments_are_checked_before_a_handler_runs(tools):
    with pytest.raises(ValueError, match="needs job_id"):
        tools.call("get_job", {})
    with pytest.raises(ValueError, match="must be one of"):
        tools.call("list_jobs", {"status": "hired"})
    assert tools.coerce(tools.get("agent_runs"), {"limit": "5", "job_id": ""}) == {"limit": 5}
    assert tools.coerce(tools.get("set_goals"), {"weekly_target": 12, "workdays": "0,1,2"}) == {"weekly_target": 12, "workdays": ["0", "1", "2"]}


def test_save_posting_tool_runs_the_gate_and_the_resume_tools_follow(tools, assistant, monkeypatch):
    stub_fit(assistant, monkeypatch)
    excluded = tools.call("save_posting", {"company": "Globex", "title": "Data Engineer", "url": "https://globex.test/1", "description": POSTING + "\n" + REFUSAL})
    assert excluded["excluded"] and "without sponsorship" in excluded["sentence"] and assistant.w.jobs() == []
    saved = tools.call("save_posting", {"company": "Acme Analytics", "title": "Data Engineer", "url": LINK, "description": POSTING, "location": "Austin, TX"})
    assert saved["saved"] and saved["job"]["sponsor_tier"] in {"S", "A", "B", "C"}
    job_id = saved["job"]["id"]
    assert tools.call("resume_status", {"job_id": job_id})["has_draft"] is False
    built = tools.call("build_resume", {"job_id": job_id})
    assert built["card"]["intent"] == "resume_ready" and built["coverage"] == 72 and built["pdf"].endswith("/resume.pdf")
    status = tools.call("resume_status", {"job_id": job_id})
    assert status["has_draft"] and status["revision"] == 1 and status["fields"]["signature_project"]
    listed = tools.call("list_jobs", {"query": "acme"})
    assert listed["total"] == 1 and listed["jobs"][0]["status"] == "prepared"
    assert tools.call("get_job", {"job_id": job_id})["description"].startswith("Software Engineer")
    assert tools.call("excluded_postings", {})["excluded"][0]["company"] == "Globex"


def test_profile_tools_propose_apply_and_reconcile(tools, assistant, monkeypatch):
    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    proposal = tools.call("propose_profile_change", {"request": "add certification: AWS Certified Data Engineer | passed September 2026"})
    assert proposal["changes"][0]["operation"] == "add" and proposal["changes"][0]["kind"] == "certification"
    assert not any(i["title"] == "AWS Certified Data Engineer" for i in assistant.s.knowledge())
    applied = tools.call("apply_profile_change", {"change_set_id": proposal["change_set_id"]})
    entry = next(i for i in assistant.s.knowledge() if i["title"] == "AWS Certified Data Engineer")
    assert applied["profile_has_unreviewed_edits"] is True and entry["review_state"] == "user_updated" and entry["data"]["pending"] is True
    found = tools.call("search_profile", {"query": "aws certified", "kind": "certification"})
    assert [i["id"] for i in found["items"]] == [entry["id"]]
    assert tools.call("get_profile_item", {"id": entry["id"]})["summary"] == "passed September 2026"
    reconciled = tools.call("reconcile_profile", {"ids": [entry["id"]]})
    assert reconciled["reconciled"] == [entry["id"]] and reconciled["profile_dirty"] is False
    overview = tools.call("profile_overview", {})
    assert overview["entries_by_kind"]["certification"] >= 1 and overview["pending_review"] == []


def test_questions_and_policy_tools_touch_only_what_they_may(tools, assistant):
    shutil.copy(ROOT / "AGENTS.md", assistant.w.root / "AGENTS.md")
    context = assistant.w.root / "data/context"
    before = {p.name: p.read_text(encoding="utf-8") for p in context.glob("*.md")}
    noted = tools.call("add_question", {"question": "Which client was the 94% figure for?"})
    assert noted["file"] == "data/context/QUESTIONS-FOR-YOU.md"
    after = {p.name: p.read_text(encoding="utf-8") for p in context.glob("*.md")}
    assert {n for n in after if after[n] != before.get(n)} == {"QUESTIONS-FOR-YOU.md"}
    assert "## Asked from the chat" in after["QUESTIONS-FOR-YOU.md"] and "Which client was the 94% figure for?" in after["QUESTIONS-FOR-YOU.md"]
    assert tools.call("open_questions", {})["text"].startswith("# Questions for you")
    policy = tools.call("read_policy", {"topic": "sponsorship"})
    assert policy["section"].startswith("The sponsorship rule") and "will not sponsor" in policy["text"]
    assert "Never re-apply" in tools.call("read_policy", {})["sections"]


def test_goals_status_and_settings_tools(tools, assistant, monkeypatch):
    monkeypatch.setattr(backend.ai, "ready_providers", lambda root: {"codex": True})
    goals = tools.call("set_goals", {"weekly_target": 12, "workdays": [0, 1, 2, 3]})
    assert goals["goals"]["weekly_target"] == 12 and assistant.s.goals()["weekly_target"] == 12
    status = tools.call("status", {})
    assert status["goals"]["weekly_target"] == 12 and status["counts"]["saved"] == 0
    settings = tools.call("ai_settings", {})
    assert settings["assistant_engine"]["provider"] == "codex" and settings["ready_providers"] == ["codex"]
    assert tools.call("set_discovery_preset", {"preset": "portals"})["preset"] == "portals"
    assert assistant.s.pref("discovery_preferences")["preset"] == "portals"


def test_agent_tools_queue_wait_and_read_runs(tools, assistant, monkeypatch):
    job = seed_job(assistant, monkeypatch)
    monkeypatch.setattr(assistant.runner, "enqueue", lambda kind, job_id=None, *a, **k: {"id": "r1", "state": "queued"})
    queued = tools.call("run_agent", {"kind": "research", "job_id": job["id"]})
    assert queued["run_id"] == "r1" and queued["agent"] == "research"
    with pytest.raises(ValueError):
        tools.call("run_agent", {"kind": "email", "job_id": job["id"]})
    runs = [{"id": "r1", "kind": "research", "job_id": job["id"], "state": "completed", "updated_at": "now", "error": None,
             "result": {"stage": "Complete", "summary": "Acme is real.", "report": "# Report\n" + "x" * 9000}}]
    monkeypatch.setattr(assistant.s, "runs", lambda: runs)
    waited = tools.call("wait_for_run", {"run_id": "r1", "seconds": 1})
    assert waited["finished"] and waited["result_summary"] == "Acme is real."
    result = tools.call("run_result", {"run_id": "r1"})
    assert len(result["result"]["report"]) == 8000 and result["state"] == "completed"
    assert tools.call("agent_runs", {"job_id": job["id"]})["runs"][0]["run_id"] == "r1"


# --- the runtime layer: Claude Code, Codex or a keyed provider ---------------------------

def test_tiers_move_to_a_runtime_that_is_ready_here(tmp_path):
    preferences = {"tiers": {"strong": {"provider": "openrouter", "model": "m"}, "cheap": {"provider": "openrouter", "model": "c"}}}
    # A tier that cannot run here goes where the background runs go (the main choice; Codex is the
    # built-in when nothing was chosen), then down the fallback order. A ready choice never moves.
    tiers, moved = resolve_tiers(tmp_path, preferences, ready={"openrouter": False, "claude_code": True, "codex": True})
    assert tiers == {"strong": ("codex", "codex-runtime"), "cheap": ("codex", "codex-runtime")} and moved == {"strong": "openrouter", "cheap": "openrouter"}
    tiers, moved = resolve_tiers(tmp_path, preferences, ready={"openrouter": False, "claude_code": True, "codex": False})
    assert tiers == {"strong": ("claude_code", "sonnet"), "cheap": ("claude_code", "haiku")} and moved == {"strong": "openrouter", "cheap": "openrouter"}
    tiers, moved = resolve_tiers(tmp_path, {**preferences, "default": {"provider": "claude_code", "model": "opus"}}, ready={"openrouter": False, "claude_code": True, "codex": True})
    assert tiers["strong"] == ("claude_code", "opus") and moved["strong"] == "openrouter"
    tiers, moved = resolve_tiers(tmp_path, preferences, ready={"openrouter": True, "claude_code": True})
    assert tiers["strong"] == ("openrouter", "m") and moved == {}
    # Nothing ready: the choice stays, so the call fails with the provider's own message.
    tiers, moved = resolve_tiers(tmp_path, preferences, ready={})
    assert tiers["strong"] == ("openrouter", "m") and moved == {}
    # No tier chosen at all: the chat runs where the background runs run (the main choice,
    # else the gateway's built-in: OpenAI with a key, otherwise Codex), so nothing is "moved".
    tiers, moved = resolve_tiers(tmp_path, {}, ready={"codex": True, "claude_code": True})
    assert tiers == {"strong": ("codex", "codex-runtime"), "cheap": ("codex", "codex-runtime")} and moved == {}
    tiers, moved = resolve_tiers(tmp_path, {"default": {"provider": "claude_code", "model": "opus"}}, ready={"codex": True, "claude_code": True})
    assert tiers == {"strong": ("claude_code", "opus"), "cheap": ("claude_code", "haiku")} and moved == {}
    tiers, moved = resolve_tiers(tmp_path, {}, ready={"openai": True, "codex": True})
    assert tiers["strong"] == ("openai", "gpt-5.2")


def test_strict_schema_closes_every_object_and_requires_every_field():
    schema = codex.strict_schema(schemas.AgentTurn.model_json_schema())
    assert schema["additionalProperties"] is False and schema["required"] == ["thought", "action", "tool", "arguments", "reply", "suggestions", "more_calls"]
    assert "default" not in schema["properties"]["tool"] and schema["properties"]["action"]["enum"] == ["call", "ask", "reply"]


def fake_codex(tmp_path, result='{"ok": true}', fail=False, stderr=""):
    """A stand-in for ``codex exec``: keeps the schema it was given, writes the result or fails like Codex does."""
    if os.name == "nt":
        script = tmp_path / "fake_codex.py"
        script.write_text(
            "import shutil, sys\n"
            "args = sys.argv[1:]\n"
            "out = args[args.index('-o') + 1]\n"
            "schema = args[args.index('--output-schema') + 1]\n"
            "shutil.copyfile(schema, __import__('pathlib').Path(__file__).with_name('schema-seen.json'))\n"
            "sys.stdin.read()\n"
            + (f"sys.stderr.write({stderr!r})\nraise SystemExit(3)\n" if fail else f"open(out, 'w', encoding='utf-8').write({result!r})\n"),
            encoding="utf-8",
        )
        path = tmp_path / "codex.cmd"
        path.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8")
        return path
    path = tmp_path / "codex"
    path.write_text("#!/bin/sh\nwhile [ $# -gt 0 ]; do if [ \"$1\" = \"-o\" ]; then out=\"$2\"; fi; "
                    "if [ \"$1\" = \"--output-schema\" ]; then cp \"$2\" \"$(dirname \"$0\")/schema-seen.json\"; fi; shift; done\n"
                    "cat > /dev/null\n" + (f"printf '%s' '{stderr}' >&2\nexit 3\n" if fail else f"printf '%s' '{result}' > \"$out\"\n"), encoding="utf-8")
    path.chmod(0o755)
    return path


CODEX_SCHEMA_ERROR = ('OpenAI Codex v0.154.0\n--------\nmodel: gpt-6\n--------\nERROR: {"type": "error", "error": {"code": "invalid_json_schema", '
                      '"message": "Invalid schema for response_format: Missing excluded."}, "status": 400}')


def test_codex_failures_name_the_real_reason_not_the_banner():
    assert codex.failure_reason(CODEX_SCHEMA_ERROR) == "Invalid schema for response_format: Missing excluded."
    assert codex.failure_reason("OpenAI Codex v0.154.0\nworkdir: /x\nERROR: You are not logged in") == \
        "Codex is not signed in on this machine: open the ChatGPT app and sign in, then retry."
    assert codex.failure_reason("OpenAI Codex v0.154.0\nworkdir: /x\nmodel: gpt-6") == ""


def test_the_runner_sends_codex_a_strict_schema_and_reports_its_reason(service, tmp_path, monkeypatch):
    """The discovery schema leaves "excluded" optional (the gate fills it), which Codex's strict
    structured output refused, failing every search with a generic sign-in hint. Codex now gets the
    closed form of every schema, and the real reason travels when it still fails."""
    from backend.services.agents import AgentRunner, DISCOVERY_SCHEMA

    assert "excluded" not in DISCOVERY_SCHEMA["required"]
    assert codex.strict_schema(DISCOVERY_SCHEMA)["required"] == list(DISCOVERY_SCHEMA["properties"])
    cli = fake_codex(tmp_path, result='{"summary": "s", "jobs": [], "rejected_leads": [], "excluded": []}')
    monkeypatch.setattr("shutil.which", lambda name: str(cli) if name == "codex" else None)
    runner = AgentRunner(service, execute=lambda *a, **k: {})
    loose = {"type": "object", "properties": {"summary": {"type": "string"}, "jobs": {"type": "array", "items": {"type": "string"}}}, "required": ["summary"]}
    assert runner.invoke("find", loose, web=True)["excluded"] == []
    seen = json.loads((tmp_path / "schema-seen.json").read_text())
    assert seen["required"] == ["summary", "jobs"] and seen["additionalProperties"] is False
    (tmp_path / "failing").mkdir()
    cli = fake_codex(tmp_path / "failing", fail=True, stderr=CODEX_SCHEMA_ERROR.replace("'", ""))
    monkeypatch.setattr("shutil.which", lambda name: str(cli) if name == "codex" else None)
    with pytest.raises(ValueError, match="Agent could not finish: Invalid schema for response_format: Missing excluded"):
        runner.invoke("find", loose)


def test_runs_started_from_the_chat_use_the_chat_engine(assistant, monkeypatch):
    """What the rail says the chat runs on is what a discovery or research run it starts uses."""
    job = seed_job(assistant, monkeypatch)
    monkeypatch.setattr(backend.ai, "ready_providers", lambda root: {"claude_code": True, "codex": True, "openrouter": False})
    monkeypatch.setattr(claude_code, "available", lambda: True)
    started = []
    monkeypatch.setattr(assistant.runner, "enqueue", lambda kind, job_id=None, provider=None, model=None, preset="default":
                        started.append((kind, provider, model, preset)) or {"id": "r1"})
    tools, service = assistant.tools, assistant.s
    # Settings chose Claude Code for everything: the chat and the runs it starts agree.
    service.set_pref("ai_preferences", {"default": {"provider": "claude_code", "model": "sonnet"}})
    tools.call("run_agent", {"kind": "research", "job_id": job["id"]})
    tools.call("find_jobs", {"preset": "portals"})
    assert started[:2] == [("research", "claude_code", "sonnet", "default"), ("discovery", "claude_code", "sonnet", "portals")]
    # An action Settings routed elsewhere keeps its own provider: the gateway decides.
    service.set_pref("ai_preferences", {"default": {"provider": "claude_code", "model": "sonnet"}, "actions": {"discovery": {"provider": "codex", "model": "codex-runtime"}}})
    tools.call("find_jobs", {})
    assert started[2][:3] == ("discovery", None, None)
    # The chat's runtime cannot do web work (a keyed provider without search): the gateway routes.
    service.set_pref("ai_preferences", {"tiers": {"strong": {"provider": "anthropic", "model": "claude-sonnet-5"}}})
    monkeypatch.setattr(backend.ai, "ready_providers", lambda root: {"claude_code": False, "codex": False, "openrouter": False, "anthropic": True})
    tools.call("run_agent", {"kind": "study_plan", "job_id": job["id"]})
    assert started[3][:3] == ("study_plan", None, None)
    # The shortcut path ("find jobs" without the model) follows the same rule.
    service.set_pref("ai_preferences", {"default": {"provider": "claude_code", "model": "opus"}})
    monkeypatch.setattr(backend.ai, "ready_providers", lambda root: {"claude_code": True, "codex": True})
    assistant.send("find jobs", "m-find")
    assert started[4] == ("discovery", "claude_code", "opus", "default")


def test_codex_runs_sealed_and_returns_the_object(tmp_path, monkeypatch):
    cli = fake_codex(tmp_path)
    monkeypatch.setenv("CODEX_CLI", str(cli))
    assert codex.available()
    seen = {}
    real = subprocess.run

    def spy(cmd, **kwargs):
        seen["cmd"], seen["input"] = cmd, kwargs["input"]
        return real(cmd, **kwargs)

    monkeypatch.setattr(codex.subprocess, "run", spy)
    result, usage = codex.run("Reply ok", {"type": "object", "properties": {"ok": {"type": "boolean"}}}, system="Be brief", web=False)
    assert result == {"ok": True}
    cmd = seen["cmd"]
    assert str(cli) in cmd and "exec" in cmd and "--ephemeral" in cmd and cmd[cmd.index("-s") + 1] == "read-only"
    assert 'web_search="disabled"' in cmd and "features.apps=false" in cmd and cmd[-1] == "-"
    assert seen["input"].startswith("Be brief\n\n---\n\nReply ok")
    (tmp_path / "broken").mkdir()
    monkeypatch.setenv("CODEX_CLI", str(fake_codex(tmp_path / "broken", fail=True)))
    with pytest.raises(ValueError, match="could not finish"):
        codex.run("x", {"type": "object", "properties": {}})


def test_codex_talks_utf8_whatever_the_windows_code_page(service, tmp_path, monkeypatch):
    """Like the real CLI, the fake refuses stdin that is not UTF-8 and answers in raw UTF-8.
    Before, Windows' ANSI code page broke both ways: a dash in a job title made Codex reject the
    prompt, and a curly quote in its answer failed to decode (live pipeline run, 23 Sep 2026).
    Both launchers are covered: the specialists' (codex.run) and the agent runner's."""
    from backend.services.agents import AgentRunner
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "prompt = sys.stdin.buffer.read().decode('utf-8')\n"
        "answer = {'echo': prompt, 'quote': '\\u201cDr\\u00e4ger\\u201d \\u2014 ok'}\n"
        "open(args[args.index('-o') + 1], 'w', encoding='utf-8').write(json.dumps(answer, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        cli = tmp_path / "codex.cmd"
        cli.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        cli = tmp_path / "codex"
        cli.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        cli.chmod(0o755)
    monkeypatch.setenv("CODEX_CLI", str(cli))
    schema = {"type": "object", "properties": {"echo": {"type": "string"}, "quote": {"type": "string"}}}
    expected = {"echo": "Boston, Massachusetts — hybrid at Dräger", "quote": "“Dräger” — ok"}
    result, _ = codex.run("Boston, Massachusetts — hybrid at Dräger", schema)
    assert result == expected
    runner = AgentRunner(service, execute=lambda *a, **k: {})
    assert runner.invoke("Boston, Massachusetts — hybrid at Dräger", schema, web=False) == expected
    runner.pool.shutdown()


def test_the_specialist_team_runs_on_codex(tmp_path, monkeypatch):
    calls = []

    def run(prompt, schema, **options):
        calls.append((prompt, schema, options))
        return {"thought": "Replying.", "action": "reply", "reply": "hi", "tool": "", "arguments": "{}", "suggestions": []}, {}

    monkeypatch.setattr(codex, "run", run)
    team = AgentTeam(tmp_path, {"strong": ("codex", "codex-runtime"), "cheap": ("codex", "codex-runtime")})
    turn = team.run("workspace_agent", {"task": []})
    assert isinstance(turn, schemas.AgentTurn) and turn.reply == "hi"
    prompt, schema, options = calls[0]
    assert '"task"' in prompt and schema["properties"]["action"]["enum"] == ["call", "ask", "reply"]
    assert options["model"] == "codex-runtime" and options["web"] is False and "You are the agent" in options["system"]
    monkeypatch.setattr(codex, "run", lambda *a, **k: (_ for _ in ()).throw(ValueError("Codex is not installed")))
    with pytest.raises(AgentError, match="Codex is not installed"):
        team.run("workspace_agent", {"task": []})


def test_choosing_codex_as_the_main_ai_moves_both_tiers(service, monkeypatch):
    from backend.ai import settings as ai_settings
    from backend.services.agents import AgentRunner

    runner = AgentRunner(service, execute=lambda *a, **k: {})
    monkeypatch.setattr(codex, "available", lambda: True)
    ai_settings.choose_main(service, runner.gateway, "codex", "codex-runtime")
    tiers = service.pref("ai_preferences")["tiers"]
    assert tiers == {"strong": {"provider": "codex", "model": "codex-runtime"}, "cheap": {"provider": "codex", "model": "codex-runtime"}}


# --- diff cards, the trace and auto-apply -------------------------------------

def test_confirm_tool_carries_a_before_after_diff(assistant, monkeypatch):
    job = seed_job(assistant, monkeypatch)
    team = ScriptedTeam(
        call("update_job", job_id=job["id"], status="applied", application_date="2026-09-10"),
        reply("Recorded."),
    )
    use_team(monkeypatch, team)
    ask = assistant.send("mark it applied on the 10th", "d1")
    assert ask["data"]["intent"] == "confirm_tool"
    diff = ask["data"]["diff"]
    assert any(r["field"] == "Status" and r["before"] == "prepared" and r["after"] == "applied" for r in diff)
    assert any(r["field"] == "Application date" and r["after"] == "2026-09-10" for r in diff)
    # The pending question carries the same diff, so a reload shows the same card.
    assert assistant.s.pref("assistant_pending")["diff"] == diff
    # And the change itself has not happened yet.
    assert assistant.w.jobs()[0]["status"] == "prepared"


def test_auto_apply_runs_gated_tools_without_the_pause(assistant, monkeypatch):
    job = seed_job(assistant, monkeypatch)
    assistant.auto_apply(True)
    team = ScriptedTeam(
        call("update_job", job_id=job["id"], status="applied", application_date="2026-09-10"),
        reply("Recorded as applied."),
    )
    use_team(monkeypatch, team)
    done = assistant.send("mark it applied on the 10th", "a1")
    assert done["state"] == "done" and assistant.s.pref("assistant_pending") is None
    assert assistant.w.jobs()[0]["status"] == "applied"
    trace = done["data"]["trace"]
    assert trace and trace[0]["tool"] == "update_job" and trace[0]["auto_applied"] is True
    with assistant.w.connect() as db:
        assert db.execute("SELECT 1 FROM activity WHERE action='assistant_auto_applied'").fetchone()


def test_auto_apply_defaults_off_and_is_per_conversation(assistant):
    assert assistant.auto_apply() is False
    assistant.auto_apply(True)
    assert assistant.auto_apply() is True
    # A different thread does not inherit the permission given in this one.
    first = assistant.conversation_id()
    with assistant.w.connect() as db:
        db.execute(
            "INSERT INTO assistant_messages(id,message,response,state,steps,data,created_at,updated_at,conversation_id) VALUES(?,?,?,?,?,?,?,?,?)",
            ("seedmsg", "hi", "hi", "done", "[]", "{}", assistant.s.now(), assistant.s.now(), first),
        )
    assistant.new_conversation()
    assert assistant.conversation_id() != first
    assert assistant.auto_apply() is False


def test_the_trace_is_kept_with_the_message(assistant, monkeypatch):
    seed_job(assistant, monkeypatch)
    team = ScriptedTeam(call("list_jobs", query="acme"), reply("Found it."))
    use_team(monkeypatch, team)
    done = assistant.send("find the acme job", "t1")
    assert done["data"]["trace"][0]["tool"] == "list_jobs"
    overview = assistant.overview()
    saved = next(m for m in overview["messages"] if m["id"] == "t1")
    assert saved["data"]["trace"][0]["tool"] == "list_jobs"


# --- faster turns, the backup AI, and the Daily Search / Assurance tools (23 Sep 2026) ---

def test_one_decision_may_add_read_only_calls_but_never_a_write(assistant, monkeypatch):
    """Live: "which jobs have no resume?" took five model turns, one lookup each."""
    job = seed_job(assistant, monkeypatch)
    arguments = json.dumps({"job_id": job["id"]})
    turn = schemas.AgentTurn(thought="Reading it all at once.", action="call", tool="list_jobs", arguments="{}",
                             more_calls=[schemas.ToolCall(tool="get_job", arguments=arguments),
                                         schemas.ToolCall(tool="resume_status", arguments=arguments),
                                         schemas.ToolCall(tool="remove_job", arguments=arguments)])
    team = ScriptedTeam(turn, reply("Read it all."))
    use_team(monkeypatch, team)
    done = assistant.send("tell me everything about the acme job", "m1")
    assert done["state"] == "done" and len(team.calls) == 2
    assert [s["label"] for s in done["steps"]] == ["Listing saved jobs", "Reading a job", "Reading the resume draft", "Thinking"]
    task = team.calls[-1]["task"]
    assert [entry.get("tool") for entry in task[1:]] == ["list_jobs", "get_job", "resume_status", "remove_job"]
    assert task[-1]["error"].startswith("Not run") and not assistant.w.get_job(job["id"]).get("deleted_at")


def test_the_team_tries_the_backup_ai_once_when_its_own_fails(tmp_path, monkeypatch):
    calls = []

    def exhausted(prompt, schema, **options):
        calls.append(("codex", options["model"]))
        raise ValueError("You have hit your usage limit")

    def answering(prompt, schema, **options):
        calls.append(("claude_code", options["model"]))
        return {"thought": "Replying.", "action": "reply", "reply": "hi", "tool": "", "arguments": "{}",
                "suggestions": [], "more_calls": []}, {}

    monkeypatch.setattr(codex, "run", exhausted)
    monkeypatch.setattr(claude_code, "run", answering)
    tiers = {"strong": ("codex", "codex-runtime"), "cheap": ("codex", "codex-runtime")}
    team = AgentTeam(tmp_path, tiers, fallback=("claude_code", "sonnet"))
    assert team.run("workspace_agent", {"task": []}).reply == "hi"
    assert calls == [("codex", "codex-runtime"), ("claude_code", "sonnet")]
    assert team.fell_back["from_provider"] == "codex" and "usage limit" in team.fell_back["reason"]
    # No backup named: the original failure stands, word for word.
    with pytest.raises(AgentError, match="usage limit"):
        AgentTeam(tmp_path, tiers).run("workspace_agent", {"task": []})


def test_the_chat_says_once_when_the_backup_ai_answered(assistant, monkeypatch):
    class BackupTeam(ScriptedTeam):
        fallback = ("azure_openai", "gpt-6-luna")

        def run(self, name, payload, **options):
            self.fell_back = {"agent": name, "from_provider": "codex", "from_model": "codex-runtime",
                              "to_provider": "azure_openai", "to_model": "gpt-6-luna", "reason": "usage limit reached"}
            return super().run(name, payload, **options)

    use_team(monkeypatch, BackupTeam(call("status"), reply("All good.")))
    done = assistant.send("how am I doing this week and what is next?", "m1")
    assert done["state"] == "done" and done["response"] == "All good."
    switched = [s for s in done["steps"] if s["label"] == "Switched to the backup AI"]
    assert len(switched) == 1 and "Azure OpenAI · gpt-6-luna" in switched[0]["detail"]
    events = [e for e in assistant.w.activity(20) if e["action"] == "provider_fallback"]
    assert events and events[0]["details"]["ai_action"] == "assistant_chat"


def test_with_no_backup_the_failure_says_how_to_recover(assistant, monkeypatch):
    use_team(monkeypatch, StubTeam(error="workspace_agent could not run on codex/codex-runtime: usage limit"))
    failed = assistant.send("how am I doing this week and what is next?", "m1")
    assert failed["state"] == "failed" and "choose a Backup provider in Settings" in failed["response"]
    assert failed["data"]["suggestions"] == ["status", "help"]


class FakePipeline:
    """The Daily Search pipeline's surface, as services/pipeline.py offers it."""

    def __init__(self):
        self.started, self.stopped, self.run = [], [], None

    def preferences(self):
        return {"count": 2, "source": "default", "provider": "codex", "model": "codex-runtime",
                "steps": {"research": False, "tailor": True, "study_plan": True, "pdf": True}}

    def providers(self):
        return [{"id": "codex", "label": "Codex", "ready": True, "models": [{"id": "codex-runtime"}]},
                {"id": "azure_openai", "label": "Azure OpenAI", "ready": True, "models": [{"id": "gpt-6-luna"}, {"id": "gpt-5.1"}]}]

    def start(self, values):
        self.started.append(values)
        self.run = {"id": "pipe1", "state": "running", "config": values, "error": None, "finished_at": None,
                    "progress": {"stage": "Finding jobs", "jobs_target": values["count"], "find": {"state": "running"}, "jobs": []}}
        return self.run

    def status(self):
        return {"current": self.run, "last": None, "budget": {"limit": 6, "used": 1, "remaining": 5},
                "plan": {"remaining_today": 3}}

    def stop(self, id):
        self.stopped.append(id)
        return self.run


def test_the_chat_runs_and_follows_the_daily_search_pipeline(assistant):
    pipeline = FakePipeline()
    assistant.tools.pipeline = pipeline
    started = assistant.tools.call("run_search_pipeline", {"count": 2, "provider": "azure_openai", "steps": ["research", "tailor", "pdf"]})
    assert pipeline.started == [{"count": 2, "source": "default", "provider": "azure_openai", "model": "gpt-6-luna",
                                 "steps": {"research": True, "tailor": True, "study_plan": False, "pdf": True}}]
    assert started["summary"] == "Started: 2 job(s) on Azure OpenAI · gpt-6-luna · research, tailor, pdf"
    with pytest.raises(ValueError, match="ready here: codex, azure_openai"):
        assistant.tools.call("run_search_pipeline", {"provider": "gemini"})
    with pytest.raises(ValueError, match="Unknown helper"):
        assistant.tools.call("run_search_pipeline", {"steps": ["apply"]})
    status = assistant.tools.call("search_pipeline_status")
    assert status["run"]["stage"] == "Finding jobs" and status["run"]["ai"] == "azure_openai · gpt-6-luna"
    assert [ai["provider"] for ai in status["ready_ais"]] == ["codex", "azure_openai"]
    # The agent's snapshot carries the same brief, so "how is the search going?" needs no tool call.
    assert assistant._snapshot()["daily_search"]["state"] == "running"
    assert assistant.tools.call("stop_search_pipeline")["stopped"] and pipeline.stopped == ["pipe1"]
    assistant.tools.pipeline = None
    with pytest.raises(ValueError, match="not available"):
        assistant.tools.call("search_pipeline_status")


def test_the_snapshot_report_and_assurance_show_what_the_tabs_show(assistant, monkeypatch):
    job = seed_job(assistant, monkeypatch)
    row = next(j for j in assistant._snapshot()["jobs"] if j["id"] == job["id"])
    assert row["research_done"] is False and row["study_plan_done"] is False and "resume_pdf" in row
    assert assistant.tools.call("search_report")["found"] is False
    now = assistant.s.now()
    result = {"summary": "One saved.", "added_job_ids": [job["id"]],
              "rejected_leads": ["https://jobs.example/tiny: company legitimacy needs review"],
              "excluded": [{"company": "Refusing Co", "title": "Data Engineer", "url": "https://x.example",
                            "reason": "Posting explicitly will not sponsor", "sentence": "We do not sponsor visas."}]}
    columns = "id,kind,job_id,state,input,result,error,created_at,updated_at,provider,model,preset"
    with assistant.w.connect() as db:
        db.execute(f"INSERT INTO agent_runs({columns}) VALUES('r1','research',?,'completed','{{}}','{{}}',NULL,?,?,'azure_openai','gpt-6-luna','default')",
                   (job["id"], now, now))
        db.execute(f"INSERT INTO agent_runs({columns}) VALUES('d1','discovery',NULL,'completed','{{}}',?,NULL,?,?,'azure_openai','gpt-6-luna','default')",
                   (json.dumps(result), now, now))
    assert next(j for j in assistant._snapshot()["jobs"] if j["id"] == job["id"])["research_done"] is True
    report = assistant.tools.call("search_report")
    assert report["saved_jobs"][0]["id"] == job["id"] and report["turned_away"] == result["rejected_leads"]
    assert report["excluded_by_sponsorship_gate"][0]["sentence"] == "We do not sponsor visas."
    check = assistant.tools.call("resume_assurance", {"job_id": job["id"]})
    assert check["job"]["id"] == job["id"] and check["counts"]["verified"] >= 1
