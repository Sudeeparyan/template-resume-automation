"""Per-company 60/40 tailoring: verified registry content plus reviewable predicted items.

Only Projects and Skills may change, every item's origin is stored for review, and a
failed tailoring must leave the saved draft untouched. fit/score are stubbed so no
LaTeX runtime is needed.
"""

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts"), str(ROOT / "tests")]

from test_career_workspace import add, workspace  # noqa: E402,F401
from backend.services.workspace_v2 import CareerServices  # noqa: E402
from backend.services.resume_studio import ResumeStudio, plain  # noqa: E402
import backend.ai  # noqa: E402
from backend.ai.agents import schemas  # noqa: E402
from backend.ai.agents.graph import AgentError  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.dashboard.app import create_app  # noqa: E402
from validate_resume import extract_zero_argument_macros  # noqa: E402


@pytest.fixture
def service(workspace, monkeypatch):
    monkeypatch.setattr(CareerServices, "today", staticmethod(lambda: "2026-09-12"))
    shutil.copytree(ROOT / "backend/workflows", workspace.root / "backend/workflows")
    return CareerServices(workspace)


@pytest.fixture
def studio(service, monkeypatch):
    from backend.services import fit

    studio = ResumeStudio(service)
    monkeypatch.setattr(studio, "fit", lambda job_id, revision: studio.get(job_id))
    monkeypatch.setattr(studio, "score", lambda job_id: {"cached": True})
    # These tests pin the plan itself; the requirement check and its skill top-up have
    # their own tests below (test_verified_skills_the_role_requires_are_topped_up) and in test_fit.py.
    monkeypatch.setattr(fit, "for_job", lambda services, job_id, **_: None)
    return studio


@pytest.fixture
def job(service):
    return add(service.w)


class StubTeam:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def run(self, name, payload, **_options):
        self.called = (name, payload)
        if self.error:
            raise AgentError(self.error)
        return self.result


def tailored_result(service, first_verified="PROJ-P05-RESUME"):
    registry = {p["id"]: p for p in service.w.evidence()["projects"]}
    content = registry[first_verified]["resume_content"]
    return schemas.TailoringResult(
        projects=[
            schemas.TailoredProject(
                title=content["title"],
                context=content["context"],
                bullets=list(content["bullets"]),
                origin="verified",
                evidence_id=first_verified,
            ),
            schemas.TailoredProject(
                title="Streaming Order Analytics Prototype",
                context="Apache Kafka, Python, PostgreSQL",
                bullets=[
                    "Prototyped a Kafka consumer that aggregates order events into PostgreSQL summary tables",
                    "Added SQL validation queries comparing event counts against source totals",
                ],
                origin="predicted",
            ),
        ],
        skills=[
            schemas.TailoredSkill(name="Python", origin="verified"),
            schemas.TailoredSkill(name="Apache Kafka", origin="verified"),
            schemas.TailoredSkill(name="Confluent Cloud", origin="predicted"),
        ],
        rationale="Kafka and Python match the posting; the prototype mirrors the described pipelines.",
    )


def section(source, name):
    return re.search(r"\\section\{" + name + r"\}[\s\S]*?(?=\\section\{|\\end\{document\})", source)[0]


def test_items_are_persisted_and_only_projects_and_skills_change(service, studio, job):
    before = studio.open(job["id"])["source"]
    team = StubTeam(tailored_result(service))
    draft = studio.tailor(job["id"], team)

    assert team.called[0] == "job_tailor"
    # The specialist is told the never-claim list so its predicted items avoid it.
    assert isinstance(team.called[1].get("never_claim"), list)
    assert draft["tailored"] is True
    assert draft["items"] == {"verified": 3, "predicted": 2}

    rows = studio.items(job["id"])
    assert [row["section"] for row in rows] == ["projects", "projects", "skills", "skills", "skills"]
    assert [row["origin"] for row in rows] == ["verified", "predicted", "verified", "verified", "predicted"]
    assert all(row["decision"] == "pending" for row in rows)
    verified, predicted = rows[0], rows[1]
    assert verified["evidence_id"] == "PROJ-P05-RESUME"
    assert verified["content"]["title"] == draft["fields"]["SelectedProjectTitle"]
    assert predicted["evidence_id"] == ""
    assert predicted["content"]["bullets"][0].startswith("Prototyped")
    assert [row["content"] for row in rows[2:]] == ["Python", "Apache Kafka", "Confluent Cloud"]
    assert rows[-1]["evidence_id"] == ""

    after = draft["source"]
    old, new = extract_zero_argument_macros(before), extract_zero_argument_macros(after)
    changed = {name for name in set(old) | set(new) if old.get(name) != new.get(name)}
    allowed = {name for name in set(old) | set(new)
               if name == "CoreSkills" or name.startswith(("SelectedProject", "SecondProject", "Skills"))}
    assert changed and changed <= allowed
    for name in ("Education", "Professional Experience"):
        assert section(before, name) == section(after, name)

    # The rendered document is seamless: the review labels never reach the source.
    assert "predicted" not in after.casefold() and "verified" not in after.casefold()
    assert draft["fields"]["SecondProjectTitle"] == "Streaming Order Analytics Prototype"
    # The tailored picks lead each line; a line the plan all but emptied keeps its first
    # original entries (half of them, at least three), so no heading prints bare.
    assert plain(new["SkillsLanguages"]) == "Python, SQL, C#"
    assert plain(new["SkillsData"]).startswith("Apache Kafka, Apache Flink, Flink SQL")
    assert plain(new["SkillsML"]).startswith("PyTorch, Scikit-learn, Pandas")
    assert plain(new["SkillsCloud"]).endswith("Docker, Confluent Cloud")


def test_a_thin_skills_plan_never_leaves_a_line_empty_or_prints_a_phrase(service, studio, job):
    """Live on 23 Sep (Azure, Color Health): the plan kept five skills and 'predicted' two
    sentences, so 'Data Engineering:' printed empty and Cloud read as prose."""
    studio.open(job["id"])
    result = tailored_result(service)
    result.skills = [
        schemas.TailoredSkill(name="Python", origin="verified"),
        schemas.TailoredSkill(name="LangChain", origin="verified"),
        schemas.TailoredSkill(name="REST API design for patient onboarding and screening workflows", origin="predicted"),
        schemas.TailoredSkill(name="Automated testing and observability for Python web services", origin="predicted"),
        schemas.TailoredSkill(name="Confluent Cloud", origin="predicted"),
    ]
    draft = studio.tailor(job["id"], StubTeam(result))
    macros = {name: plain(value) for name, value in extract_zero_argument_macros(draft["source"]).items()
              if name.startswith("Skills")}
    assert all(len(value.split(", ")) >= 3 for value in macros.values()), macros
    assert macros["SkillsCloud"].endswith(", Confluent Cloud")
    assert "patient onboarding" not in draft["source"] and "observability for" not in draft["source"]
    assert [row["content"] for row in studio.items(job["id"]) if row["section"] == "skills"] == ["Python", "LangChain", "Confluent Cloud"]


def test_a_removed_project_restores_the_slot_from_the_registry(service, studio, job):
    studio.open(job["id"])
    studio.tailor(job["id"], StubTeam(tailored_result(service)))
    items = studio.items(job["id"])
    predicted = next(row for row in items if row["section"] == "projects" and row["origin"] == "predicted")

    outcome = studio.decide(job["id"], predicted["id"], "removed")
    draft = outcome["draft"]
    registry_ids = {p["id"] for p in service.w.evidence()["projects"] if p.get("resume_content")}
    assert draft["fields"]["SecondProjectTitle"] != "Streaming Order Analytics Prototype"
    assert draft["fields"]["SecondProjectID"] in registry_ids
    row = next(item for item in outcome["items"] if item["id"] == predicted["id"])
    assert row["decision"] == "removed"


def test_a_removed_skill_is_stripped_and_a_kept_one_changes_nothing(service, studio, job):
    studio.open(job["id"])
    studio.tailor(job["id"], StubTeam(tailored_result(service)))
    items = studio.items(job["id"])
    predicted = next(row for row in items if row["section"] == "skills" and row["origin"] == "predicted")

    outcome = studio.decide(job["id"], predicted["id"], "removed")
    macros = extract_zero_argument_macros(outcome["draft"]["source"])
    assert "Confluent Cloud" not in plain(macros["SkillsCloud"])
    assert next(item for item in outcome["items"] if item["id"] == predicted["id"])["decision"] == "removed"
    removed_source = outcome["draft"]["source"]

    verified = next(row for row in items if row["origin"] == "verified")
    outcome = studio.decide(job["id"], verified["id"], "kept")
    assert outcome["draft"]["source"] == removed_source
    assert next(item for item in outcome["items"] if item["id"] == verified["id"])["decision"] == "kept"

    with pytest.raises(ValueError, match="kept or removed"):
        studio.decide(job["id"], verified["id"], "maybe")


def test_the_tailor_is_told_which_projects_may_lead(service, studio, job):
    """Another company's signature and a support-only project cannot fill the first slot, so the
    tailor must be told; before, it chose a taken signature and the whole tailor failed."""
    studio.open(job["id"])
    with service.w.connect() as db:
        db.execute("INSERT OR REPLACE INTO signature_assignments VALUES(?,?,?,?)",
                   ("othercompany", "PROJ-P06-EXPENSE", None, service.now()))
    team = StubTeam(tailored_result(service))
    studio.tailor(job["id"], team)
    lead = {project["evidence_id"]: project["can_lead"] for project in team.called[1]["verified_projects"]}
    assert lead["PROJ-P06-EXPENSE"] is False and lead["PROJ-P10-PACMAN"] is False
    assert lead["PROJ-P05-RESUME"] is True


def test_a_failed_tailor_leaves_the_draft_and_items_untouched(service, studio, job):
    before = studio.open(job["id"])["source"]
    with pytest.raises(ValueError, match="left unchanged"):
        studio.tailor(job["id"], StubTeam(error="job_tailor could not run: the account is out of credits"))
    assert studio.get(job["id"])["source"] == before
    assert studio.items(job["id"]) == []
    assert any(row["action"] == "studio_tailor_failed" for row in service.w.activity())


def test_a_reworded_verified_project_gets_its_registered_wording_back(service, studio, job):
    """Verified wording is never the AI's to change; a reworded copy is repaired, not fatal."""
    studio.open(job["id"])
    result = tailored_result(service)
    result.projects[0].bullets[0] = "Reworded beyond recognition."
    out = studio.tailor(job["id"], StubTeam(result))
    registered = {p["id"]: p for p in service.w.evidence()["projects"]}["PROJ-P05-RESUME"]["resume_content"]
    source = studio.get(job["id"])["source"]
    assert "Reworded beyond recognition" not in source
    assert plain(registered["bullets"][0])[:40] in plain(source)
    assert any("Restored the registered wording" in w for w in out["warnings"])


class SequenceTeam:
    """Answers each job_tailor call with the next plan, recording every payload."""

    def __init__(self, *results):
        self.results, self.calls = list(results), []

    def run(self, name, payload, **_options):
        self.calls.append((name, payload))
        return self.results.pop(0)


def test_a_lead_that_may_not_lead_is_swapped_not_fatal(service, studio, job):
    """23 Sep: the plan led with another company's signature project and the whole tailor failed."""
    studio.open(job["id"])
    with service.w.connect() as db:
        db.execute("INSERT OR REPLACE INTO signature_assignments VALUES(?,?,?,?)",
                   ("othercompany", "PROJ-P06-EXPENSE", None, service.now()))
    taken, free = tailored_result(service, "PROJ-P06-EXPENSE"), tailored_result(service)
    taken.projects.insert(1, free.projects[0])  # P06 (taken) first, then P05 (may lead)
    out = studio.tailor(job["id"], StubTeam(taken))
    assert "PROJ-P05-RESUME" in studio.get(job["id"])["source"].split("SECOND_PROJECT")[0]
    assert any("Led with" in w and "already leads another company" in w for w in out["warnings"])


def test_a_rejected_plan_gets_one_corrected_retry_with_the_reason(service, studio, job):
    studio.open(job["id"])
    broken = tailored_result(service)
    for project in broken.projects:
        project.origin, project.evidence_id = "verified", "PROJ-DOES-NOT-EXIST"
    team = SequenceTeam(broken, tailored_result(service))
    out = studio.tailor(job["id"], team)
    assert len(team.calls) == 2 and out["tailored"]
    assert "no usable projects" in team.calls[1][1]["previous_attempt_problem"]
    assert "previous_attempt_problem" not in team.calls[0][1]


def test_verified_skills_the_role_requires_are_topped_up(service, studio, job, monkeypatch):
    from backend.services import fit

    must = ["Apache Flink", "ClickHouse", "Grafana", "Apache Airflow", "PyTorch", "Databricks"]
    analysis = {"matrix": {"requirements": [
        {"text": name, "category": "required", "excerpt": name, "status": "met", "evidence_ids": [evidence]}
        for name, evidence in zip(must, ["SKILL-STREAMING-001"] * 3 + ["SKILL-CLOUD-001", "SKILL-ML-001", "SKILL-LAKEHOUSE-001"])
    ], "hard_blockers": []}}
    monkeypatch.setattr(fit, "for_job", lambda services, job_id, **_: analysis)
    studio.open(job["id"])
    team = StubTeam(tailored_result(service))
    out = studio.tailor(job["id"], team)
    assert team.called[1]["requirements"][0] == {"text": "Apache Flink", "category": "required", "status": "met",
                                                 "evidence_ids": ["SKILL-STREAMING-001"]}
    added = [i for i in studio.items(job["id"]) if i["section"] == "skills" and i["content"] in must]
    assert len(added) == 4 and all(i["origin"] == "verified" for i in added)
    assert any(w.startswith("Added Apache Flink") for w in out["warnings"])


def test_a_predicted_item_with_never_claim_wording_is_left_out_and_the_rest_is_kept(service, studio, job):
    """Kimi once suggested a publication line (live test, 24 Sep 2026): that one suggestion goes, the plan stays."""
    studio.open(job["id"])
    result = tailored_result(service)
    result.skills[2].name = "Kubernetes"  # on the registry's never-claim list
    tailored = studio.tailor(job["id"], StubTeam(result))
    assert tailored["tailored"] is True
    assert tailored["left_out"] == ["Left out the suggested skill 'Kubernetes': it used wording that is never "
                                    "allowed on a resume (" + tailored["left_out"][0].split("(", 1)[1]]
    assert any(w.startswith("Left out the suggested skill 'Kubernetes'") for w in tailored["warnings"])
    names = [item["content"] for item in studio.items(job["id"]) if item["section"] == "skills"]
    assert "Kubernetes" not in names and "Python" in names
    assert "Kubernetes" not in studio.get(job["id"])["source"]


def test_a_predicted_project_with_a_date_or_percentage_is_left_out(service, studio, job):
    studio.open(job["id"])
    result = tailored_result(service)
    predicted = next(p for p in result.projects if p.origin == "predicted")
    predicted.bullets[0] = "Cut pipeline latency by 40% in 2025"
    tailored = studio.tailor(job["id"], StubTeam(result))
    assert any("Left out the suggested project" in note for note in tailored["left_out"])
    assert all(item["origin"] == "verified" for item in studio.items(job["id"]) if item["section"] == "projects")
    assert "40\\%" not in studio.get(job["id"])["source"] and "40%" not in studio.get(job["id"])["source"]


def test_the_three_tailoring_routes(service, job, monkeypatch):
    from backend.services import fit

    monkeypatch.setattr(fit, "for_job", lambda services, job_id, **_: None)  # the plan alone, no top-up
    team = StubTeam(tailored_result(service))
    monkeypatch.setattr(backend.ai, "any_provider_configured", lambda root: True)
    monkeypatch.setattr(backend.ai, "team_for", lambda services, on_usage=None: team)
    monkeypatch.setattr(ResumeStudio, "fit", lambda self, job_id, revision: self.get(job_id))
    monkeypatch.setattr(ResumeStudio, "score", lambda self, job_id: {"cached": True})
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        url = "/api/v2/studio/" + job["id"]
        tailored = client.post(url + "/tailor")
        assert tailored.status_code == 200
        body = tailored.json()
        assert body["tailored"] is True
        assert body["items"] == {"verified": 3, "predicted": 2}

        items = client.get(url + "/items").json()
        assert [row["origin"] for row in items] == ["verified", "predicted", "verified", "verified", "predicted"]
        assert items[0]["content"]["title"]  # project content is decoded to an object
        assert items[2]["content"] == "Python"

        skill = next(row for row in items if row["section"] == "skills" and row["origin"] == "predicted")
        decided = client.post(url + "/items/" + skill["id"] + "/decision", json={"decision": "removed"})
        assert decided.status_code == 200
        outcome = decided.json()
        assert next(row for row in outcome["items"] if row["id"] == skill["id"])["decision"] == "removed"
        assert "Confluent Cloud" not in outcome["draft"]["source"]
        assert client.post(url + "/items/" + skill["id"] + "/decision", json={"decision": "maybe"}).status_code == 400


def test_the_tailor_route_needs_an_ai_runtime(service, job, monkeypatch):
    monkeypatch.setattr(backend.ai, "any_provider_configured", lambda root: False)
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/v2/studio/" + job["id"] + "/tailor")
        assert response.status_code == 400
        assert "No AI runtime is set up" in response.json()["detail"]


def test_re_tailoring_files_skills_by_the_base_resume_not_a_thinned_draft(service, studio, job):
    """Live on 23 Sep (Azure, Color Health): a draft an earlier tailoring had thinned
    (Data Engineering empty, two phrases on Cloud) sent every data tool to Cloud and Tools."""
    from backend.services.resume_studio import write_field

    draft = studio.open(job["id"])
    thinned = write_field(draft["source"], "SkillsData", "")
    thinned = write_field(thinned, "SkillsCloud", "REST API design for patient onboarding and screening workflows")
    with service.w.connect() as db:
        db.execute("UPDATE studio_drafts SET source=? WHERE job_id=?", (thinned, job["id"]))
    result = tailored_result(service)
    result.skills = [schemas.TailoredSkill(name=name, origin="verified")
                     for name in ("Apache Kafka", "Apache Flink", "PyTorch", "Python", "Docker")]
    tailored = studio.tailor(job["id"], StubTeam(result))
    macros = {name: plain(value) for name, value in extract_zero_argument_macros(tailored["source"]).items()
              if name.startswith("Skills")}
    assert macros["SkillsData"].startswith("Apache Kafka, Apache Flink")
    assert macros["SkillsML"].startswith("PyTorch") and macros["SkillsLanguages"].startswith("Python")
    assert macros["SkillsCloud"].startswith("Docker") and "Apache Kafka" not in macros["SkillsCloud"]
    assert "patient onboarding" not in tailored["source"]


def test_re_tailoring_rebuilds_evidence_tags_instead_of_piling_them_up(service, studio, job):
    """Live on 23 Sep: after three tailorings the Cloud line's EVIDENCE comment held dozens of
    duplicate tags and tags of deleted review rows, and Assurance marked it unsupported."""
    import re as _re

    studio.open(job["id"])
    studio.tailor(job["id"], StubTeam(tailored_result(service)))
    second = studio.tailor(job["id"], StubTeam(tailored_result(service)))
    comment = _re.search(r"% EVIDENCE: ([^\n]+)\n\\newcommand\{\\SkillsCloud\}", second["source"])[1].split()
    assert len(comment) == len(set(comment)), comment
    live = {"resume_items:" + row["id"] for row in studio.items(job["id"])}
    assert all(tag in live for tag in comment if tag.startswith("resume_items:")), comment
    assert any(tag.startswith("resume_items:") for tag in comment)  # the predicted skill stays traceable
