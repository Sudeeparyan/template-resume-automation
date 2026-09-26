"""The whole job-finder flow, end to end, for two personas.

Onboard the profile, discover exactly five ranked postings, tailor all five
resumes, review the predicted items in Assurance, record an application, then
change a record through the chat assistant and see it land everywhere. Finally
switch the provider to Kimi and confirm the agents still resolve.
"""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from test_career_workspace import workspace  # noqa: E402,F401 - fixture
from test_tailoring import service  # noqa: E402,F401 - fixture (pinned day, workflows)
from test_tailoring import StubTeam  # noqa: E402
from test_assistant_agent import ScriptedTeam, call, reply  # noqa: E402
from test_assistant import use_team  # noqa: E402
from backend.services.agents import AgentRunner  # noqa: E402
from backend.services.assistant import Assistant  # noqa: E402
from backend.services.resume_studio import TOP_UP_SKILLS, ResumeStudio  # noqa: E402
from backend.ai.providers import AIGateway  # noqa: E402
from backend.ai.settings import choose_main  # noqa: E402
from backend.dashboard.app import create_app  # noqa: E402
from fixtures.personas import PERSONAS, posting, tailoring_for  # noqa: E402


@pytest.fixture
def client(service, monkeypatch):
    monkeypatch.setattr(ResumeStudio, "fit", lambda self, job_id, revision: self.get(job_id))
    monkeypatch.setattr(ResumeStudio, "score", lambda self, job_id: {"cached": True})
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as test_client:
        yield test_client


@pytest.mark.parametrize("persona", ["fresher", "experienced"])
def test_the_whole_pipeline_twice(service, client, monkeypatch, persona):
    spec = PERSONAS[persona]

    # 1. Onboard: the persona's entries are saved, reviewed and reconciled.
    for entry in spec["knowledge"]:
        service.save_knowledge(entry)
    service.reconcile_knowledge()
    assert not service.profile_dirty()
    context_titles = {item["title"] for item in service.profile_context()}
    assert spec["knowledge"][0]["title"] in context_titles

    # 2. Daily discovery: five live-looking postings in, all gates pass, ranked by fit.
    service.save_goals({"weekly_target": 35, "workdays": [0, 1, 2, 3, 4, 5, 6], "start_date": service.today()})
    found = {
        "summary": "Five postings from the fixture search.",
        "rejected_leads": [],
        "jobs": [posting(company, n + 1) for n, company in enumerate(spec["companies"])],
    }
    runner = AgentRunner(service, lambda *a, **k: found)
    runner.enqueue("discovery")
    runner.pool.shutdown(wait=True)
    run = service.runs()[0]
    assert run["state"] == "completed", run["error"]

    jobs = sorted(service.w.jobs(), key=lambda j: j["company"])
    assert [j["company"] for j in jobs] == sorted(spec["companies"])
    assert len(jobs) == 5
    for j in jobs:
        assert isinstance(j["fit_score"], int) and 0 <= j["fit_score"] <= 100
        assert j["fit_rationale"].startswith("Fit ")
        assert j["raw_jd"]
        assert j["status"] == "saved"

    # 3. Tailor each resume: registry content plus per-company predicted items.
    studio = ResumeStudio(service)
    item_counts = {}
    for index, j in enumerate(jobs):
        draft = studio.tailor(j["id"], StubTeam(tailoring_for(service, persona, index)))
        item_counts[j["id"]] = sum(draft["items"].values())
        # The plan's 3 verified + 2 predicted, plus up to TOP_UP_SKILLS registered skills the role requires.
        assert draft["tailored"] is True and draft["items"]["predicted"] == 2
        assert 3 <= draft["items"]["verified"] <= 3 + TOP_UP_SKILLS
        rows = studio.items(j["id"])
        assert len(rows) == sum(draft["items"].values()) and {row["decision"] for row in rows} == {"pending"}
        assert draft["file_root"]
        assert (service.w.root / "data/output" / draft["file_root"] / "resume.tex").is_file()

    # 4. Assurance: every job shows its predicted claims; keep on one, remove on another.
    for j in jobs:
        report = client.get("/api/v2/assurance/" + j["id"]).json()
        assert report["note"] is None and report["summary"]["predicted"] >= 2
        # Only the two suggestions wait for her; the registry items never need a decision.
        assert report["summary"]["pending"] == 2 < item_counts[j["id"]]
    keep_job, drop_job = jobs[0], jobs[1]
    outcomes = {}
    for j, decision in ((keep_job, "kept"), (drop_job, "removed")):
        items = client.get("/api/v2/studio/" + j["id"] + "/items").json()
        target = next(row for row in items if row["section"] == "skills" and row["origin"] == "predicted")
        outcome = client.post(
            f"/api/v2/studio/{j['id']}/items/{target['id']}/decision", json={"decision": decision}
        )
        assert outcome.status_code == 200
        outcomes[decision] = outcome.json()
    kept_report = client.get("/api/v2/assurance/" + keep_job["id"]).json()
    assert kept_report["summary"]["kept"] == 1
    assert spec["predicted_skill"] in json.dumps(kept_report)
    dropped_report = client.get("/api/v2/assurance/" + drop_job["id"]).json()
    assert dropped_report["summary"]["removed"] == 1
    # The removed skill leaves the resume at once; its row stays listed so the decision is visible.
    assert spec["predicted_skill"] not in outcomes["removed"]["draft"]["source"]
    removed_claims = [c for c in dropped_report["claims"] if c["text"] == spec["predicted_skill"]]
    assert removed_claims and all(c["decision"] == "removed" for c in removed_claims)

    # 5. Mark one applied with the real submission date; the pipeline moves.
    service.w.update_job(keep_job["id"], "applied", "Sent through the company portal", "2026-09-12")
    applied = service.w.get_job(keep_job["id"])
    assert applied["status"] == "applied" and applied["application_date"] == "2026-09-12"

    # 6. Chat change: the assistant plans, shows a diff, waits for yes, then applies.
    chat_runner = AgentRunner(
        service, execute=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no AI here"))
    )
    chat_runner.studio = studio
    assistant = Assistant(service, studio, chat_runner, background=False)
    team = ScriptedTeam(
        call("update_job", job_id=drop_job["id"], notes="Portal login created"),
        reply("Noted the portal login on it."),
    )
    use_team(monkeypatch, team)
    ask = assistant.send("add a note to one of the new jobs", "e2e-1")
    assert ask["state"] == "needs_input"
    assert ask["data"]["diff"] == [{"field": "Notes", "before": drop_job["notes"], "after": "Portal login created"}]
    done = assistant.send("yes", "e2e-2")
    assert done["state"] == "done" and done["data"]["trace"]
    assert service.w.get_job(drop_job["id"])["notes"] == "Portal login created"
    summary = client.get("/api/v2/summary").json()
    row = next(j for j in summary["jobs"] if j["id"] == drop_job["id"])
    assert row["notes"] == "Portal login created"

    # 7. Switch the provider to Kimi: structured work resolves there, and web work
    #    is still routed only to providers that can browse.
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test-moonshot-key-1234567890")
    gateway = AIGateway(service, lambda *a, **k: {})
    choose_main(service, gateway, "kimi", "kimi-k2-0905-preview")
    assert service.pref("ai_preferences")["default"]["provider"] == "kimi"
    provider, _model = gateway.resolve("requirement_extraction")
    assert provider.id == "kimi"
    with pytest.raises(ValueError, match="not compatible"):
        gateway.resolve("discovery", "kimi", "kimi-k2-0905-preview")
