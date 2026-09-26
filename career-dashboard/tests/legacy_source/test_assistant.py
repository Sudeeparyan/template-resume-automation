"""The assistant chat: paste a posting, get the resume; everything else routes to the same services the tabs use."""

import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from test_career_workspace import JD, workspace  # noqa: E402,F401
from test_workspace_v2 import service  # noqa: E402,F401
import backend.ai  # noqa: E402
from backend.ai.agents import schemas  # noqa: E402
from backend.ai.agents.graph import AgentError  # noqa: E402
from backend.services.agents import AgentRunner  # noqa: E402
from backend.services.assistant import Assistant, looks_like_posting, parse_posting_fields  # noqa: E402
from backend.services.resume_studio import ResumeStudio  # noqa: E402

POSTING = """Software Engineer, Data Platform
Acme Analytics
Austin, TX (Hybrid)

About the role
We are looking for an early-career engineer to build streaming data pipelines with Apache Kafka and Flink,
orchestrate ETL with Airflow on AWS (Glue, S3), write SQL transformations and validate data quality.

Responsibilities
- Build and operate pipelines in Python and PostgreSQL
- Work with analysts on data quality

Qualifications
- Bachelor's degree in Computer Science or similar
- Experience with Python and SQL; Kafka or Flink preferred
"""
LINK = "https://careers.acme.test/jobs/1234"
REFUSAL = "Must be authorized to work in the United States without sponsorship now or in the future."


class StubTeam:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def run(self, name, payload, **_options):
        self.calls.append((name, payload))
        if self.error:
            raise AgentError(self.error)
        return self.result


def use_team(monkeypatch, team, configured=True):
    monkeypatch.setattr(backend.ai, "any_provider_configured", lambda root: configured)
    monkeypatch.setattr(backend.ai, "team_for", lambda services, on_usage=None: team)


@pytest.fixture
def assistant(service, monkeypatch):
    studio = ResumeStudio(service)
    runner = AgentRunner(service, execute=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI here")))
    runner.studio = studio
    # In-process: replies are complete when send() returns.
    return Assistant(service, studio, runner, background=False)


def stub_fit(assistant, monkeypatch):
    """Stand in for the compile-measure-cut loop so the pipeline test needs no LaTeX run."""
    def fit(job_id, revision):
        draft = assistant.studio.get(job_id)
        return {**draft, "revision": revision,
                "preview": {"path": "applications/x/studio/preview-1", "page_count": 1, "body_font_pt": 10.5,
                            "layout": {"pages": [{"fill_percent": 96.0}]}, "ranking": {"cuts": []}, "current": True},
                "match": {"resume_coverage": {"score": 72}, "ats_readiness": {"score": 95}, "missing_unsupported": ["flink"]}}
    monkeypatch.setattr(assistant.studio, "fit", fit)


def test_posting_detection_and_free_field_extraction():
    assert looks_like_posting(POSTING)
    assert not looks_like_posting("find jobs")
    assert not looks_like_posting("what did I apply to last week?")
    fields = parse_posting_fields(POSTING + "\nApply: " + LINK + ".")
    assert fields["title"] == "Software Engineer, Data Platform"
    assert fields["location"] == "Austin, TX (Hybrid)"
    assert fields["url"] == LINK
    labelled = parse_posting_fields("Company: Acme Analytics\nJob title: Data Engineer\nLocation: Remote (US)\n")
    assert labelled == {"company": "Acme Analytics", "title": "Data Engineer", "location": "Remote (US)"}


def test_pasted_posting_without_a_link_asks_once_then_builds_the_resume(assistant, monkeypatch):
    stub_fit(assistant, monkeypatch)
    use_team(monkeypatch, StubTeam(schemas.PostingFields(company="Acme Analytics", title="Software Engineer, Data Platform", location="Austin, TX (Hybrid)")))
    first = assistant.send(POSTING, "m1")
    assert first["state"] == "needs_input" and "link" in first["response"]
    assert assistant.s.pref("assistant_pending")["kind"] == "posting_link"

    second = assistant.send("here you go " + LINK, "m2")
    assert second["state"] == "done", second["response"]
    data = second["data"]
    assert data["intent"] == "resume_ready" and data["pdf"].endswith("/resume.pdf") and data["coverage"] == 72
    assert [s["label"] for s in second["steps"]][:5] == [
        "Reading the posting", "Sponsorship gate and never-re-apply", "Opening the draft",
        "Fitting one US Letter page", "Scoring against the posting"]
    assert all(s["state"] == "done" for s in second["steps"])
    # The job is on the Dashboard like any other saved posting, with its tier, and no AI was needed to save it.
    [job] = assistant.w.jobs()
    assert job["company"] == "Acme Analytics" and job["title"] == "Software Engineer, Data Platform" and job["sponsor_tier"] in {"S", "A", "B", "C"}
    assert job["status"] == "prepared" and job["application_date"] is None
    assert assistant.s.pref("assistant_pending") is None
    assert "nothing here has been submitted" in second["response"].casefold()


def test_the_parser_cannot_invent_an_employer(assistant, monkeypatch):
    """A value that is not in the pasted text is dropped, and the chat asks instead."""
    stub_fit(assistant, monkeypatch)
    body = "\n".join(POSTING.splitlines()[3:])  # no title line, no company line
    use_team(monkeypatch, StubTeam(schemas.PostingFields(company="Globex", title="Wizard")))
    reply = assistant.send(body + "\n" + LINK, "m1")
    assert reply["state"] == "needs_input" and "Company | Job title | Location" in reply["response"]
    answer = assistant.send("Acme Analytics | Data Engineer | Austin, TX", "m2")
    assert answer["state"] == "done" and answer["data"]["intent"] == "resume_ready"
    [job] = assistant.w.jobs()
    assert (job["company"], job["title"], job["location"]) == ("Acme Analytics", "Data Engineer", "Austin, TX")


def test_a_refusing_posting_is_excluded_with_its_sentence_and_never_drafted(assistant, monkeypatch):
    use_team(monkeypatch, StubTeam(error="must not be called: the fields are labelled"), configured=False)
    reply = assistant.send("Company: Acme Analytics\nTitle: Data Engineer\nLocation: Austin, TX\n" + LINK + "\n" + POSTING + "\n" + REFUSAL, "m1")
    assert reply["state"] == "done" and "without sponsorship" in reply["response"]
    assert reply["data"]["intent"] == "posting_excluded"
    assert assistant.w.jobs() == [] and len(assistant.s.excluded()) == 1
    assert reply["steps"][1]["state"] == "failed"


def test_same_role_again_is_blocked_and_the_same_link_reuses_the_record(assistant, monkeypatch):
    stub_fit(assistant, monkeypatch)
    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    labelled = "Company: Acme Analytics\nTitle: Data Engineer\nLocation: Austin, TX\n" + POSTING + "\n"
    assert assistant.send(labelled + LINK, "m1")["data"]["intent"] == "resume_ready"
    again = assistant.send(labelled + "https://careers.acme.test/jobs/9999", "m2")
    assert again["data"]["intent"] == "posting_blocked" and again["data"]["rule"] == "same_role"
    same = assistant.send(labelled + LINK, "m3")
    assert same["data"]["intent"] == "resume_ready" and same["data"]["duplicate"] is True
    assert len(assistant.w.jobs()) == 1


def test_applied_is_recorded_only_after_yes_and_keeps_her_date(assistant, monkeypatch):
    stub_fit(assistant, monkeypatch)
    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    assistant.send("Company: Acme Analytics\nTitle: Data Engineer\nLocation: Austin, TX\n" + POSTING + "\n" + LINK, "m1")
    ask = assistant.send("applied to Acme on 2026-09-10", "m2")
    assert ask["state"] == "needs_input" and "2026-09-10" in ask["response"]
    assert assistant.w.jobs()[0]["application_date"] is None
    assert assistant.send("no", "m3")["state"] == "done"
    assert assistant.w.jobs()[0]["application_date"] is None
    assistant.send("I applied to Acme on 2026-09-10", "m4")
    done = assistant.send("yes", "m5")
    assert done["data"]["intent"] == "mark_applied"
    job = assistant.w.jobs()[0]
    assert job["status"] == "applied" and job["application_date"] == "2026-09-10"


def test_ambiguous_company_asks_which_posting(assistant, monkeypatch):
    stub_fit(assistant, monkeypatch)
    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    for n, title in enumerate(("Data Engineer", "Machine Learning Engineer"), 1):
        assistant.send(f"Company: Acme Analytics\nTitle: {title}\nLocation: Austin, TX\n{POSTING}\nhttps://careers.acme.test/jobs/{n}", f"m{n}")
    which = assistant.send("study plan for Acme", "m3")
    assert which["state"] == "needs_input" and "1. Acme Analytics — Data Engineer" in which["response"]
    # The runner is asked for a real study_plan run for the chosen job.
    queued = []
    monkeypatch.setattr(assistant.runner, "enqueue", lambda kind, job_id=None, *a, **k: queued.append((kind, job_id)) or {"id": "run1234abcd", "state": "queued"})
    picked = assistant.send("2", "m4")
    assert picked["state"] == "done" and picked["data"]["run_id"] == "run1234abcd"
    assert queued == [("study_plan", [j for j in assistant.w.jobs() if j["title"] == "Machine Learning Engineer"][0]["id"])]


def test_find_jobs_starts_discovery_and_status_reads_the_workspace(assistant, monkeypatch):
    queued = []
    monkeypatch.setattr(assistant.runner, "enqueue", lambda kind, job_id=None, provider=None, model=None, preset="default": queued.append((kind, preset)) or {"id": "d1", "state": "queued"})
    assistant.s.set_pref("discovery_preferences", {"preset": "portals"})
    found = assistant.send("find me some jobs", "m1")
    assert found["state"] == "done" and queued == [("discovery", "portals")]
    status = assistant.send("status", "m2")
    assert status["state"] == "done" and "This week" in status["response"]
    assert assistant.send("help", "m3")["data"]["intent"] == "help"


def test_open_questions_go_to_the_agent_with_the_snapshot_and_the_tools(assistant, monkeypatch):
    """A question needs no tool: the agent reads the snapshot and replies, changing nothing."""
    team = StubTeam(schemas.AgentTurn(thought="Nothing is applied yet; answering from the snapshot.", action="reply",
                                      reply="You have nothing applied yet.", suggestions=["status", "find jobs"]))
    use_team(monkeypatch, team)
    before = assistant.w.jobs()
    reply = assistant.send("what did I apply to last week?", "m1")
    assert reply["state"] == "done" and reply["response"] == "You have nothing applied yet."
    assert reply["data"]["suggestions"] == ["status", "find jobs"] and reply["data"]["intent"] == "agent"
    [(name, payload)] = team.calls
    assert name == "workspace_agent" and payload["task"] == [{"role": "user", "content": "what did I apply to last week?"}]
    assert set(payload["workspace"]) >= {"jobs", "goals", "counts", "active_runs", "profile_has_unreviewed_edits"}
    assert {t["name"] for t in payload["tools"]} >= {"list_jobs", "build_resume", "run_agent", "search_profile", "set_goals"}
    assert assistant.w.jobs() == before
    # The model's decision is the one step, with its thought as the detail.
    assert [(s["label"], s["state"], s["agent"]) for s in reply["steps"]] == [("Thinking", "done", "assistant")]


def test_without_a_provider_the_chat_still_runs_the_exact_commands(assistant, monkeypatch):
    use_team(monkeypatch, StubTeam(error="unreachable"), configured=False)
    reply = assistant.send("what did I apply to last week?", "m1")
    assert reply["state"] == "done" and "No AI runtime" in reply["response"]


def test_api_returns_at_once_and_the_reply_fills_in(service, monkeypatch):
    from backend.dashboard.app import create_app

    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        posted = client.post("/api/v2/assistant/messages", json={"message": "status", "request_id": "r1"})
        assert posted.status_code == 202
        for _ in range(200):
            row = client.get("/api/v2/assistant/messages/r1").json()
            if row["state"] != "processing":
                break
            import time
            time.sleep(0.05)
        assert row["state"] == "done" and "This week" in row["response"]
        overview = client.get("/api/v2/assistant").json()
        assert [m["id"] for m in overview["messages"]] == ["r1"] and overview["busy"] is False
        # The same request ID is idempotent.
        assert client.post("/api/v2/assistant/messages", json={"message": "status", "request_id": "r1"}).json()["id"] == "r1"
        assert client.post("/api/v2/assistant/messages", json={"message": "help", "request_id": "r1"}).status_code == 400


def test_real_compile_end_to_end(assistant, monkeypatch):
    """The whole path with the real fitter: one US Letter page and a PDF on disk."""
    if not shutil.which("tectonic"):
        pytest.skip("PDF runtime unavailable")
    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    reply = assistant.send("Company: Acme Analytics\nTitle: Data Engineer\nLocation: Austin, TX\n" + POSTING + "\n" + LINK, uuid.uuid4().hex)
    assert reply["state"] == "done", reply["response"]
    data = reply["data"]
    pdf = assistant.w.root / "data/output" / data["pdf"]
    assert pdf.exists() and (assistant.w.root / "data/output" / data["preview_png"]).exists()
    draft = assistant.studio.get(data["job_id"])
    assert draft["preview"]["page_count"] == 1 and draft["preview"]["current"]


# ---- Conversations and Stop --------------------------------------------------------

def test_new_chat_keeps_the_old_thread_in_history_and_scopes_the_agents_memory(assistant, monkeypatch):
    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    first = assistant.conversation_id()
    assistant.send("status", "c1")
    assistant.send("excluded", "c2")
    assert [m["id"] for m in assistant.history()] == ["c1", "c2"]
    # The old thread stays; the new one starts empty; the agent's recent memory is scoped.
    fresh = assistant.new_conversation()
    assert fresh != first and assistant.conversation_id() == fresh
    assert assistant.history() == [] and assistant.history(conversation_id=first)[-1]["id"] == "c2"
    assert assistant._payload("x", [], 0)["recent_conversation"] == []
    # New chat on an empty thread reuses it instead of leaving empty threads behind.
    assert assistant.new_conversation() == fresh
    assistant.send("status", "c3")
    listed = assistant.overview()["conversations"]
    assert [(c["count"], c["current"]) for c in listed] == [(1, True), (2, False)]
    assert listed[1]["title"] == "status" and listed[0]["id"] == fresh
    # Reopening the old thread shows its messages again; the pending question is dropped.
    assistant.s.set_pref("assistant_pending", {"kind": "agent", "transcript": []})
    assert assistant.open_conversation(first) == first
    assert [m["id"] for m in assistant.history()] == ["c1", "c2"] and assistant.s.pref("assistant_pending") is None
    with pytest.raises(ValueError):
        assistant.open_conversation("nope")


def test_deleting_a_conversation_removes_only_its_messages(assistant, monkeypatch):
    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    first = assistant.conversation_id()
    assistant.send("status", "d1")
    second = assistant.new_conversation()
    assistant.send("excluded", "d2")
    # Deleting the open thread lands on the most recent other one.
    result = assistant.delete_conversation(second)
    assert result["deleted"] == 1 and result["conversation_id"] == first
    assert [m["id"] for m in assistant.history()] == ["d1"]
    with pytest.raises(ValueError):
        assistant.get("d2")
    # Deleting the last thread leaves a fresh, empty one open.
    assistant.delete_conversation(first)
    assert assistant.history() == [] and assistant.conversations() == []
    assert assistant.conversation_id() not in {first, second}


def test_clear_history_removes_every_chat_and_opens_a_fresh_one(assistant, monkeypatch):
    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    assistant.send("status", "h1")
    first = assistant.conversation_id()
    assistant.new_conversation()
    assistant.send("excluded", "h2")
    assistant.s.set_pref("assistant_pending", {"kind": "agent", "transcript": []})
    before = assistant.s.summary()["counts"]
    result = assistant.clear_history()
    assert result["deleted"] == 2 and result["conversations"] == 2
    assert assistant.conversations() == [] and assistant.history() == []
    assert assistant.conversation_id() == result["conversation_id"] and assistant.conversation_id() != first
    assert assistant.s.pref("assistant_pending") is None
    for id in ("h1", "h2"):
        with pytest.raises(ValueError):
            assistant.get(id)
    # Nothing outside the chat is touched.
    assert assistant.s.summary()["counts"] == before
    assert [e["action"] for e in assistant.w.activity(1)] == ["assistant_history_cleared"]


def test_a_conversation_title_is_the_first_line_cut_short():
    from backend.services.assistant import conversation_title

    assert conversation_title("status") == "status"
    assert conversation_title("JD: Software Engineer, Data Platform\nAcme Analytics") == "Software Engineer, Data Platform"
    assert conversation_title("  \n  ") == "New chat"
    assert conversation_title(None) == "New chat"
    long = conversation_title("x" * 200)
    assert len(long) == 60 and long.endswith("…")


def test_older_messages_join_the_open_conversation_on_upgrade(service, monkeypatch):
    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    studio = ResumeStudio(service)
    runner = AgentRunner(service, execute=lambda *a, **k: None)
    runner.studio = studio
    with service.w.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS assistant_messages(id TEXT PRIMARY KEY, message TEXT NOT NULL, response TEXT NOT NULL, state TEXT NOT NULL, steps TEXT NOT NULL DEFAULT '[]', data TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        db.execute("INSERT INTO assistant_messages VALUES('old','status','This week','done','[]','{}','2026-09-01T10:00:00','2026-09-01T10:00:05')")
    assistant = Assistant(service, studio, runner, background=False)
    assert [m["id"] for m in assistant.history()] == ["old"]
    assert assistant.conversations()[0] == {**assistant.conversations()[0], "count": 1, "current": True, "title": "status", "busy": False}


def test_stop_ends_the_reply_at_the_next_step_and_says_what_stayed(assistant, monkeypatch):
    from backend.services.assistant import STOPPED_REPLY

    seen = []

    class StopsItself(StubTeam):
        def run(self, name, payload, **_):
            # The first turn asks for a tool; she presses Stop while it runs.
            seen.append(name)
            assistant.stop("s1")
            return schemas.AgentTurn(thought="Looking.", action="call", tool="status", arguments="{}")

    use_team(monkeypatch, StopsItself())
    row = assistant.send("how is my week going?", "s1")
    assert row["state"] == "failed" and row["response"] == STOPPED_REPLY and row["data"]["intent"] == "stopped"
    assert len(seen) == 1, "no second model call after Stop"
    assert row["steps"][-1] == {**row["steps"][-1], "label": "Stopped", "state": "failed", "agent": "assistant"}
    # The decision that came back after Stop was dropped: the tool never ran, the Thinking step did not finish.
    assert [(s["label"], s["state"], s["detail"]) for s in row["steps"][:-1]] == [("Thinking", "failed", "Stopped before it finished")]
    assert "s1" not in assistant.stopping
    # Stopping a finished message changes nothing.
    assert assistant.stop("s1")["response"] == STOPPED_REPLY


def test_conversation_and_stop_routes(service, monkeypatch):
    from backend.dashboard.app import create_app

    use_team(monkeypatch, StubTeam(error="not needed"), configured=False)
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.post("/api/v2/assistant/messages", json={"message": "status", "request_id": "r1"})
        for _ in range(200):
            if client.get("/api/v2/assistant/messages/r1").json()["state"] != "processing":
                break
            import time
            time.sleep(0.05)
        first = client.get("/api/v2/assistant").json()["conversation_id"]
        made = client.post("/api/v2/assistant/conversations")
        assert made.status_code == 201 and made.json()["messages"] == [] and made.json()["conversation_id"] != first
        assert [c["id"] for c in client.get("/api/v2/assistant/conversations").json()] == [first]
        opened = client.put(f"/api/v2/assistant/conversations/{first}").json()
        assert [m["id"] for m in opened["messages"]] == ["r1"] and opened["conversations"][0]["current"] is True
        assert client.put("/api/v2/assistant/conversations/missing").status_code == 400
        assert client.post("/api/v2/assistant/messages/r1/stop").json()["state"] == "done"
        gone = client.delete(f"/api/v2/assistant/conversations/{first}").json()
        assert gone["messages"] == [] and gone["conversations"] == []
        assert client.get("/api/v2/assistant/messages/r1").status_code == 400
        client.post("/api/v2/assistant/messages", json={"message": "status", "request_id": "r2"})
        for _ in range(200):
            if client.get("/api/v2/assistant/messages/r2").json()["state"] != "processing":
                break
            time.sleep(0.05)
        cleared = client.delete("/api/v2/assistant/conversations").json()
        assert cleared["messages"] == [] and cleared["conversations"] == [] and cleared["conversation_id"]
        assert client.get("/api/v2/assistant/messages/r2").status_code == 400
