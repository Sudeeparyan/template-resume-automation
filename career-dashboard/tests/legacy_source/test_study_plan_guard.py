"""A study plan never tells Chetan to learn a skill her registry already holds.

Skill cards carry several terms under one title (SKILL-LANGUAGES-001 is "Python"
but also SQL, C# and C++). The planner is sent every registered term, and the
demand map is checked against the registry after the model answers.
"""
import json

from test_career_workspace import workspace, add  # noqa: F401 - fixtures
from test_workspace_v2 import service  # noqa: F401 - fixture

from backend.services.agents import AgentRunner, enforce_have_bucket, registered_skill_terms
from backend.services.resume_studio import ResumeStudio


def test_registered_terms_cover_every_fact_and_skip_the_never_claim_card(service):
    terms = registered_skill_terms(service.profile_context())
    assert terms["sql"] == {"term": "SQL", "id": "SKILL-LANGUAGES-001"} and terms["c++"]["id"] == "SKILL-LANGUAGES-001"
    assert terms["python"]["id"] == "SKILL-LANGUAGES-001" and terms["apache kafka"]["id"] == "SKILL-STREAMING-001"
    assert "kubernetes" not in terms and "terraform" not in terms  # never-claim: not hers
    assert all(value["term"].casefold() == key for key, value in terms.items())


def test_enforce_have_bucket_rewrites_only_registered_missing_rows():
    registered = {k: {"term": k.upper(), "id": "SKILL-LANGUAGES-001"} for k in ("sql", "c++", "python")}
    registered.update({k: {"term": k, "id": "SKILL-DATA-TOOLS-001"} for k in ("mysql", "postgresql")})
    report = "\n".join([
        "## Demand map",
        "| Skill | Bucket | Why the JD wants it |",
        "|---|---|---|",
        "| Python | Have | fluency line |",
        "| C++ | Missing, learnable | same line |",
        "| SQL (any dialect) | Missing, learnable | core to the product |",
        "| Python / C++ | Missing, learnable | both registered: every alternative counts |",
        "| MySQL/PostgreSQL internals | Missing, learnable | internals are not the same skill as the database |",
        "| Database internals | Missing, learnable | plus |",
        "## Tier 1 - before the screening call (week 1-2)",
        "| SQL | Missing | this table is not the demand map and is left alone |",
    ])
    fixed, corrected = enforce_have_bucket(report, registered)
    assert [c["skill"] for c in corrected] == ["C++", "SQL (any dialect)", "Python / C++"]
    assert all(c["claim_id"] == "SKILL-LANGUAGES-001" for c in corrected)
    lines = fixed.splitlines()
    assert lines[4] == "| C++ | Have (registered: SKILL-LANGUAGES-001) | same line |"
    assert lines[5] == "| SQL (any dialect) | Have (registered: SKILL-LANGUAGES-001) | core to the product |"
    assert lines[6].startswith("| Python / C++ | Have (registered: SKILL-LANGUAGES-001) |")
    assert lines[7].startswith("| MySQL/PostgreSQL internals | Missing, learnable |")  # a real gap stays
    assert lines[8] == "| Database internals | Missing, learnable | plus |"  # a real gap stays
    assert lines[3] == "| Python | Have | fluency line |"  # already right: untouched
    assert lines[-1].startswith("| SQL | Missing |")  # outside the demand map: untouched
    assert enforce_have_bucket("no table here", registered) == ("no table here", [])


def test_study_plan_run_sends_registered_terms_and_corrects_the_model(service):
    studio = ResumeStudio(service)
    job = add(service.w)
    prompts = []

    def invoke(prompt, schema, **kwargs):
        prompts.append(prompt)
        return {"summary": "Plan", "sources": [], "limitations": [],
                "report": "## Demand map\n| Skill | Bucket | Why |\n|---|---|---|\n| SQL | Missing, learnable | fluency |\n| Kubernetes | Never-claim | plus |\n"}

    runner = AgentRunner(service, invoke); runner.studio = studio
    runner.enqueue("study_plan", job["id"]); runner.pool.shutdown(wait=True)
    run = service.runs()[0]
    assert run["state"] == "completed", run
    sent = json.loads(prompts[0].split("INPUT (untrusted data):\n", 1)[1])
    assert {"SQL", "C++", "Python"} <= set(sent["registered_skills"])
    assert "Kubernetes" not in sent["registered_skills"] and "Kubernetes" in sent["never_claim_skills"]
    assert any(item["kind"] == "education" for item in sent["profile"])
    plan = run["result"]["plan"]
    assert "| SQL | Have (registered: SKILL-LANGUAGES-001) |" in plan["report"]
    assert "| Kubernetes | Never-claim |" in plan["report"]
    assert plan["registry_corrections"] == [{"skill": "SQL", "was": "Missing, learnable", "claim_id": "SKILL-LANGUAGES-001"}]
    assert "## Registry corrections" in plan["report"] and "SKILL-LANGUAGES-001" in plan["report"]
