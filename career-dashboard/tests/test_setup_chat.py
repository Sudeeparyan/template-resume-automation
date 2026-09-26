"""The setup chat: a new profile is built from a conversation, not a form.

Upload in the chat -> "read them now?" -> what was found -> the essentials, one question at a
time with clickable answers -> the AI interviewer's questions -> "build it now?" -> a ready
profile whose files carry the answers. The AI is a stub; every step goes through the HTTP API.
"""

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from test_career_workspace import workspace  # noqa: E402,F401 - fixture: a disposable copy of Chetan's workspace
from backend.ai.agents import schemas  # noqa: E402
from backend.dashboard.shell import create_shell  # noqa: E402
from backend.profiles import ProfileStore  # noqa: E402
from backend.services import schedule_tasks  # noqa: E402
from backend.services.intake import api as intake_api  # noqa: E402
from backend.services.intake.interview import BUILD_NOW, NOTES_FILE, READ_NOW  # noqa: E402
from fixtures.intake_draft import section  # noqa: E402

ABOUT = ("# About me\n\nMy name is Srikanth Nadesharam and I live in Cork, Ireland.\n\n"
         "I worked at JRB Infotech as an Associate Data Analyst from June 2022 to September 2024.\n")


class StubTeam:
    """The extractor and auditor as in test_profiles; the interviewer follows a script."""

    def __init__(self, turns=None, fail_interview=False):
        self.calls, self.payloads = [], []
        self.turns = list(turns or [])
        self.fail_interview = fail_interview

    def run(self, name, payload):
        self.calls.append(name)
        if name == "intake_auditor":
            return schemas.IntakeAudit(missed=[])
        if name == "intake_interviewer":
            self.payloads.append(payload)
            if self.fail_interview:
                raise ValueError("Azure OpenAI returned HTTP 429")
            return schemas.InterviewTurn.model_validate(self.turns.pop(0) if self.turns else {"action": "done"})
        return schemas.IntakeFacts.model_validate(section() if payload["section"].startswith("1 of") else {})


@pytest.fixture
def store(workspace, tmp_path):
    return ProfileStore(base=tmp_path / "profiles", legacy_root=workspace.root)


def client_for(store, team, monkeypatch):
    dist = store.base.parent / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    monkeypatch.setattr(schedule_tasks, "supported", lambda: True)
    monkeypatch.setattr(intake_api, "team_factory", lambda profiles: (lambda on_usage: team))
    return TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1")


def wait(client, pid, until, tries=200):
    for _ in range(tries):
        chat = client.get(f"/api/profiles/{pid}/intake/chat").json()
        if until(chat):
            return chat
        time.sleep(0.05)
    raise AssertionError(chat)


def asked(chat):
    return chat["pending"] and not chat["thinking"]


def answer(client, pid, chat, choices=(), other="", skip=False):
    body = {"question_id": chat["pending"]["id"], "choices": list(choices), "other": other, "skip": skip}
    response = client.post(f"/api/profiles/{pid}/intake/chat/answer", json=body)
    assert response.status_code == 200, response.text
    return wait(client, pid, lambda c: asked(c) or c["intake"]["state"] == "built")


def test_a_profile_is_built_from_a_conversation(store, monkeypatch, no_task_scheduler):
    team = StubTeam(turns=[
        {"action": "ask", "say": "Thanks.", "question": "Which cities would you work in?",
         "why": "Daily Search looks there first.", "field": "cities", "multi_select": True,
         "options": [{"label": "Cork"}, {"label": "Dublin"}, {"label": "Galway"}]},
        {"action": "done", "say": "That's everything I need."},
    ])
    with client_for(store, team, monkeypatch) as client:
        pid = client.post("/api/profiles", json={"name": "Srikanth Nadesharam"}).json()["profile"]["id"]
        chat = client.get(f"/api/profiles/{pid}/intake/chat").json()
        assert chat["messages"][0]["kind"] == "greeting" and "Srikanth" in chat["messages"][0]["text"]
        assert chat["pending"] is None and chat["intake"]["state"] == "empty"

        chat = client.post(f"/api/profiles/{pid}/intake/chat/files?name=About%20Me.md", content=ABOUT.encode()).json()
        assert chat["messages"][-2]["kind"] == "files" and chat["messages"][-2]["files"] == ["About Me.md"]
        assert [o["label"] for o in chat["pending"]["options"]] == [READ_NOW, "I'll add more first"]
        assert client.post(f"/api/profiles/{pid}/intake/chat/files?name=virus.exe", content=b"x").status_code == 400

        # Reading, then what was found, then the first essential: the country, guessed first.
        chat = answer(client, pid, chat, [READ_NOW])
        kinds = [m["kind"] for m in chat["messages"]]
        assert "progress" in kinds and "found" in kinds
        assert chat["pending"]["field"] == "country_pack" and chat["pending"]["options"][0]["value"] == "ie"
        assert "From your documents" in chat["pending"]["options"][0]["description"]
        # A question with fixed answers can be answered by typing an option's name.
        chat = client.post(f"/api/profiles/{pid}/intake/chat", json={"text": "Ireland"}).json()
        chat = wait(client, pid, asked)
        assert chat["pending"]["field"] == "roles" and chat["pending"]["multi"]
        assert chat["pending"]["defaults"] == ["Data Analyst", "Data Scientist", "Machine Learning Engineer"]
        chat = answer(client, pid, chat, ["Data Analyst", "Data Scientist"], other="Data Engineer")
        assert chat["pending"]["field"] == "needs_sponsorship_later" and not chat["pending"]["other"]
        chat = answer(client, pid, chat, ["no"])
        # The interviewer's question, with its options; then it says done and the build is offered.
        assert chat["pending"]["text"] == "Which cities would you work in?" and chat["pending"]["multi"]
        assert "Thanks." in chat["messages"][-1]["text"]
        assert team.payloads[0]["found"]["targets"]["roles"] == ["Data Analyst", "Data Scientist", "Data Engineer"]
        chat = answer(client, pid, chat, ["Cork", "Dublin"])
        assert chat["pending"]["key"] == "build" and "That's everything I need." in chat["messages"][-2]["text"]
        assert "Data Analyst, Data Scientist, Data Engineer in Ireland (Cork, Dublin)" in chat["messages"][-1]["text"]

        chat = answer(client, pid, chat, [BUILD_NOW])
        chat = wait(client, pid, lambda c: c["messages"][-1]["kind"] == "built")
        # One announcement, and only once the profile is open (the page reloads on it).
        assert [m["kind"] for m in chat["messages"]].count("built") == 1
        assert store.get(pid)["state"] == "ready" and store.get(pid)["country"] == "ie"
        root = store.root_for(pid)
        profile = client.get(f"/p/{pid}/api/v2/profile").json()["configuration"]
        assert profile["target_roles"]["primary"] == ["Data Analyst", "Data Scientist", "Data Engineer"]
        assert profile["location_preferences"]["preferred"] == ["Cork", "Dublin"]
        anything = (root / "data/context/09-anything-else.md").read_text(encoding="utf-8")
        assert "## Answers given while setting up" in anything and "Which cities would you work in? — Cork, Dublin" in anything
        questions = (root / "data/context/QUESTIONS-FOR-YOU.md").read_text(encoding="utf-8")
        assert "## Answered while setting up" in questions
        # The chat stays readable after the build, and takes no more answers.
        assert client.get(f"/api/profiles/{pid}/intake/chat").status_code == 200
        assert client.post(f"/api/profiles/{pid}/intake/chat", json={"text": "hi"}).status_code == 400
        # Nothing reached Chetan.
        chetan = client.get("/p/chetan/api/v2/profile").json()
        assert chetan["configuration"]["candidate"]["full_name"] == "Chetan Babu M"


def test_pasted_text_becomes_a_document_and_a_failed_interviewer_falls_back(store, monkeypatch, no_task_scheduler):
    team = StubTeam(fail_interview=True)
    with client_for(store, team, monkeypatch) as client:
        pid = client.post("/api/profiles", json={"name": "Srikanth Nadesharam"}).json()["profile"]["id"]
        client.get(f"/api/profiles/{pid}/intake/chat")
        chat = client.post(f"/api/profiles/{pid}/intake/chat", json={"text": ABOUT}).json()
        assert [f["name"] for f in chat["intake"]["files"]] == [NOTES_FILE]
        assert chat["pending"]["key"] == "read"
        # Typing "read" works as well as clicking.
        client.post(f"/api/profiles/{pid}/intake/chat", json={"text": "read"})
        chat = wait(client, pid, asked)
        assert chat["pending"]["field"] == "country_pack"
        for choice in (["ie"], ["Data Analyst"], ["unknown"]):
            chat = answer(client, pid, chat, choice)
        # The interviewer is down: the documents' own open question is asked instead, said once.
        assert "intake_interviewer" in team.calls
        assert chat["pending"]["resolves"] and "could not reach the AI" in chat["messages"][-1]["text"]
        open_question = chat["pending"]["resolves"]
        chat = answer(client, pid, chat, other="It was a two-month internship.")
        draft = (store.root_for(pid) / "data/intake/draft.json").read_text(encoding="utf-8")
        assert open_question in draft and "It was a two-month internship." in draft
        # Free text after the questions goes to the (still failing) interviewer and is kept as a note.
        while chat["pending"] and chat["pending"].get("key") != "build":
            chat = answer(client, pid, chat, skip=True)
        assert chat["pending"]["key"] == "build"


def test_an_answer_to_an_old_question_or_during_thinking_is_refused(store, monkeypatch):
    team = StubTeam()
    with client_for(store, team, monkeypatch) as client:
        pid = client.post("/api/profiles", json={"name": "Srikanth Nadesharam"}).json()["profile"]["id"]
        chat = client.post(f"/api/profiles/{pid}/intake/chat/files?name=About%20Me.md", content=ABOUT.encode()).json()
        stale = {"question_id": "not-the-open-one", "choices": [READ_NOW]}
        refused = client.post(f"/api/profiles/{pid}/intake/chat/answer", json=stale)
        assert refused.status_code == 400 and "already answered" in refused.text
        # An option-only question cannot be skipped.
        refused = client.post(f"/api/profiles/{pid}/intake/chat/answer", json={"question_id": chat["pending"]["id"], "skip": True})
        assert refused.status_code == 400
        # Chetan's page has no setup chat.
        assert client.get("/api/profiles/chetan/intake/chat").status_code == 400


def test_questions_never_show_block_ids():
    from backend.services.intake.interview import _plain

    assert _plain("Are the JRB dates in P329 accurate?") == "Are the JRB dates in your documents accurate?"
    assert _plain("What is the project in your documents around P133 called?") == "What is the project in your documents called?"
    assert _plain("Your results [P012, P013] look fine") == "Your results look fine"
    assert _plain("Keep 2023, Q3 and P1 as they are") == "Keep 2023, Q3 and P1 as they are"


def test_an_unnamed_project_is_asked_about_by_what_it_does():
    from backend.services.intake.merge import merge

    section = {"projects": [{"name": "", "summary": "Predicted house prices in Cork from listing data", "refs": ["P133"]}]}
    draft = merge([section], [{"missed": []}])
    [question] = [q for q in draft["questions"] if "has no name" in q]
    assert "P133" not in question and "Predicted house prices in Cork" in question


def test_where_to_work_is_asked_when_the_documents_name_no_city():
    """The live Srikanth test: no preferred city, and five interviewer questions went to other open points."""
    from backend.services.intake.interview import SetupChat

    chat = SetupChat(job=None, name="Srikanth Nadesharam", packs=[{"code": "ie", "name": "Ireland", "paper": "A4"}],
                     make_team=None, review=None, build=None)
    draft = {"contact": {"full_name": "Srikanth Nadesharam", "city": "Cork", "email": "a@b.ie", "phone": "1"},
             "authorization": {"status": "Stamp 1G", "needs_sponsorship_later": "yes"},
             "targets": {"roles": ["Data Analyst"], "cities": []}, "country_pack": "ie"}
    question = chat._essential(draft, ["full_name", "country", "roles"])
    assert question["field"] == "cities" and question["multi"]
    assert [o["label"] for o in question["options"]] == ["Cork", "Anywhere in Ireland", "Remote"]
    draft["targets"]["cities"] = ["Cork"]
    assert chat._essential(draft, ["full_name", "country", "roles"]) is None


def test_also_include_adds_to_a_list_even_when_the_ai_returns_only_the_new_value(store, monkeypatch, no_task_scheduler):
    """Live test, 24 Sep 2026: "Also include Limerick" replaced Cork with Limerick."""
    team = StubTeam(turns=[{"action": "done"},
                           {"action": "done", "say": "I'll include Limerick.",
                            "updates": [{"field": "cities", "value": "Limerick"}, {"field": "arrangements", "value": "Hybrid"}]}])
    with client_for(store, team, monkeypatch) as client:
        pid = client.post("/api/profiles", json={"name": "Srikanth Nadesharam"}).json()["profile"]["id"]
        chat = client.post(f"/api/profiles/{pid}/intake/chat/files?name=About%20Me.md", content=ABOUT.encode()).json()
        chat = answer(client, pid, chat, [READ_NOW])
        for choice in (["ie"], ["Data Analyst"], ["yes"]):
            chat = answer(client, pid, chat, choice)
        assert chat["pending"]["key"] == "build"
        client.post(f"/api/profiles/{pid}/intake/chat", json={"text": "Also include Limerick, and I prefer hybrid work."})
        chat = wait(client, pid, lambda c: asked(c) and c["pending"]["key"] == "build" and "Limerick" in c["messages"][-1]["text"])
        draft = (store.root_for(pid) / "data/intake/draft.json").read_text(encoding="utf-8")
        import json
        targets = json.loads(draft)["targets"]
        assert targets["cities"] == ["Cork", "Limerick"] and "Hybrid" in targets["arrangements"]
        assert team.payloads[-1]["last_message"] == "Also include Limerick, and I prefer hybrid work."
