"""The requirement matrix (services/fit.py): the AI proposes, the code verifies, one list serves everything."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]

from backend.ai import router  # noqa: E402
from backend.ai.agents import schemas  # noqa: E402
from backend.ai.agents.graph import AgentError  # noqa: E402
from backend.assessment import AssessmentService  # noqa: E402
from backend.job_quality import JobQualityService  # noqa: E402
from backend.services import fit  # noqa: E402
from backend.services.agents import DISCOVERY_SPARES, AgentRunner  # noqa: E402
from test_career_workspace import add, workspace  # noqa: E402,F401
from test_workspace_v2 import service  # noqa: E402,F401

JD = ("Entry-level Data Engineer. Required: Python and SQL. You will build streaming pipelines with Apache Kafka. "
      "Experience with Kubernetes is required. Preferred: a degree in computer science. "
      "Candidates must hold an active Top Secret security clearance. You must be authorized to work in the US.")
POSTING = {"company": "Acme", "title": "Data Engineer", "location": "Austin, TX",
           "url": "https://acme.example/jobs/1", "description": JD}


class StubTeam:
    """Stands in for an AgentTeam on a free plan: returns a fixed matrix, or fails."""

    def __init__(self, raw=None, error=None):
        self.raw, self.error, self.calls = raw, error, []
        self.served = ("kimi_cli", "kimi-code/k3")
        self.root, self.tiers, self.on_usage, self.persona, self.route = None, {}, None, None, {}

    def run(self, name, payload, **_):
        self.calls.append((name, payload))
        if self.error:
            raise AgentError(self.error)
        return schemas.FitAnalysis.model_validate(self.raw)


def raw_matrix():
    return {
        "requirements": [
            {"text": "Python", "category": "required", "excerpt": "Required: Python and SQL.",
             "status": "met", "evidence_ids": ["SKILL-LANGUAGES-001"]},
            # An excerpt the posting never wrote: not evidence of anything.
            {"text": "Spark", "category": "required", "excerpt": "Five years of Spark in production.",
             "status": "met", "evidence_ids": ["SKILL-LAKEHOUSE-001"]},
            # "met" on an id that does not exist is a guess.
            {"text": "Apache Kafka", "category": "responsibility",
             "excerpt": "You will build streaming pipelines with Apache Kafka.",
             "status": "met", "evidence_ids": ["SKILL-INVENTED-999"]},
            # A never-claim skill stays missing whatever the model says.
            {"text": "Kubernetes", "category": "required", "excerpt": "Experience with Kubernetes is required.",
             "status": "met", "evidence_ids": ["SKILL-CLOUD-001"]},
            # Coursework alone makes an item partial at most.
            {"text": "degree in computer science", "category": "preferred",
             "excerpt": "Preferred: a degree in computer science.", "status": "met", "evidence_ids": ["COURSEWORK-MS-001"]},
        ],
        "hard_blockers": [
            {"excerpt": "Candidates must hold an active Top Secret security clearance.", "reason": "requires a security clearance"},
            # Work permission belongs to the sponsorship gate, never to this list.
            {"excerpt": "You must be authorized to work in the US.", "reason": "needs work authorization"},
        ],
        "summary": "Strong on Python; Kubernetes is a gap.",
    }


def test_the_code_verifies_every_excerpt_and_evidence_id(service):
    cat = fit.catalogue(service)
    matrix = fit.verify(raw_matrix(), JD, cat)
    by = {r["text"]: r for r in matrix["requirements"]}
    assert "Spark" not in by, "an excerpt not in the posting is dropped"
    assert by["Python"]["status"] == "met" and by["Python"]["evidence_ids"] == ["SKILL-LANGUAGES-001"]
    assert by["Apache Kafka"]["status"] == "missing" and by["Apache Kafka"]["evidence_ids"] == []
    assert by["Kubernetes"]["status"] == "missing", "a never-claim skill is never met"
    assert by["degree in computer science"]["status"] == "partial"
    assert [b["reason"] for b in matrix["hard_blockers"]] == ["requires a security clearance"]
    assert matrix["checks"] == {"dropped_ungrounded": 1, "downgraded": 3}


def test_a_never_claim_tool_among_examples_caps_the_item_at_partial(service):
    jd = "Familiarity with modern web stacks including Python, SQL and Kubernetes. Experience with Kubernetes is required."
    raw = {"requirements": [
        {"text": "web development (Python, SQL, Kubernetes)", "category": "required", "status": "met",
         "excerpt": "Familiarity with modern web stacks including Python, SQL and Kubernetes.", "evidence_ids": ["SKILL-LANGUAGES-001"]},
        {"text": "Kubernetes", "category": "required", "status": "met",
         "excerpt": "Experience with Kubernetes is required.", "evidence_ids": ["SKILL-LANGUAGES-001"]},
    ]}
    by = {r["text"]: r for r in fit.verify(raw, jd, fit.catalogue(service))["requirements"]}
    grouped = by["web development (Python, SQL, Kubernetes)"]
    assert grouped["status"] == "partial" and grouped["evidence_ids"] == ["SKILL-LANGUAGES-001"]
    assert "never-claim" in grouped["note"]
    assert by["Kubernetes"]["status"] == "missing", "the requirement itself is never met"


def test_a_typographic_difference_quotes_the_posting_itself(service):
    jd = "We use SQL daily – and Python too."
    raw = {"requirements": [{"text": "SQL", "category": "required", "excerpt": "We use SQL daily - and Python too.",
                             "status": "met", "evidence_ids": ["SKILL-LANGUAGES-001"]}]}
    matrix = fit.verify(raw, jd, fit.catalogue(service))
    assert matrix["requirements"][0]["excerpt"] in jd


def test_the_score_follows_the_formula(service):
    root = service.w.root
    def matrix(*statuses):
        return {"requirements": [{"text": f"r{i}", "category": "required", "excerpt": "x", "status": s, "evidence_ids": []}
                                 for i, s in enumerate(statuses)], "hard_blockers": []}
    assert fit.COVERAGE_POINTS + fit.ROLE_POINTS + fit.LOCATION_POINTS == 100

    def points(met, total):  # the coverage part: PRIOR_ITEMS imaginary half-met items weigh in
        return round(fit.COVERAGE_POINTS * (met + 0.5 * fit.PRIOR_ITEMS) / (total + fit.PRIOR_ITEMS))

    everything = fit.score(matrix(*["met"] * 10), POSTING, root)
    assert everything["score"] == points(10, 10) + 25 and everything["must_have_ok"]
    half = fit.score(matrix("met", "missing"), POSTING, root)
    assert half["score"] == points(1, 2) + 25 == round(fit.COVERAGE_POINTS * 0.5) + 25 and half["must_have_ok"]
    short = fit.score(matrix("met", "missing", "missing"), POSTING, root)
    assert not short["must_have_ok"]
    abroad = fit.score(matrix("met"), {**POSTING, "location": "Dublin, Ireland", "title": "Senior Data Engineer"}, root)
    assert abroad["components"] == {"requirements": points(1, 1), "role_seniority": 0, "location": 0}
    nothing = fit.score(matrix(), POSTING, root)
    assert nothing["components"]["requirements"] == round(fit.COVERAGE_POINTS * 0.5)
    # Two recognised must-haves, both met, is a thinner case than twelve of thirteen met.
    sparse = fit.score(matrix("met", "met"), POSTING, root)
    thorough = fit.score(matrix(*["met"] * 12, "missing"), POSTING, root)
    assert thorough["score"] > sparse["score"]


def test_the_rules_path_matches_her_own_skills_and_knows_what_she_lacks(service):
    analysis = fit.analyse(service, POSTING)
    assert analysis["method"] == "rules" and analysis["provider"] == ""
    by = {r["text"]: r for r in analysis["matrix"]["requirements"] if r["status"] != "unknown"}
    assert by["Python"]["status"] == "met" and by["Python"]["evidence_ids"]
    assert by["Apache Kafka"]["status"] == "met"
    assert by["Kubernetes"]["status"] == "missing"
    assert "Checked by rules" in analysis["rationale"] and "Missing: Kubernetes" in analysis["rationale"]


def test_the_rules_credit_a_field_from_the_tools_that_prove_it(service):
    # "Machine learning" is rarely written in her evidence; PyTorch, computer vision and her lab projects are.
    posting = {**POSTING, "description": "Required: hands-on machine learning experience and SQL. " * 3}
    by = {r["text"]: r for r in fit.analyse(service, posting)["matrix"]["requirements"] if r["status"] != "unknown"}
    assert by["ML"]["status"] == "met"
    assert any(i.startswith(("SKILL-ML", "PROJ-")) for i in by["ML"]["evidence_ids"]), by["ML"]
    assert "COURSEWORK-MS-001" not in by["ML"]["evidence_ids"][:1], "real work, not only coursework, proves it"


def test_the_ai_path_is_verified_and_scored(service):
    team = StubTeam(raw_matrix())
    analysis = fit.analyse(service, POSTING, team=team)
    assert team.calls[0][0] == "fit_analyst"
    payload = team.calls[0][1]
    assert payload["never_claim"] and any(e["id"] == "SKILL-LANGUAGES-001" for e in payload["evidence"])
    assert analysis["method"] == "ai" and analysis["provider_label"].startswith("Kimi")
    assert analysis["matrix"]["hard_blockers"] and "Blocker: requires a security clearance" in analysis["rationale"]
    relevance = JobQualityService(service).relevance(POSTING, analysis=analysis)
    assert not relevance["eligible"] and any("security clearance" in b for b in relevance["blockers"])


def test_an_ai_failure_falls_back_to_the_rules_path(service):
    analysis = fit.analyse(service, POSTING, team=StubTeam(error="No AI could take this step."))
    assert analysis["method"] == "rules" and "No AI could take" in analysis["ai_error"]
    # Nothing the AI said could be found in the posting: the posting's own words win.
    ungrounded = {"requirements": [{"text": "Go", "category": "required", "excerpt": "Not in the posting at all.",
                                    "status": "met", "evidence_ids": []}]}
    assert fit.analyse(service, POSTING, team=StubTeam(ungrounded))["method"] == "rules"


def test_the_matrix_is_cached_per_posting_and_evidence(service, monkeypatch):
    job = add(service.w)
    first = fit.for_job(service, job["id"])
    assert first["method"] == "rules"
    assert service.w.get_job(job["id"])["fit_score"] == first["score"]
    assert fit.cached(service, job["id"])["score"] == first["score"]
    # Her evidence changed: the saved check no longer applies.
    real = fit.catalogue
    monkeypatch.setattr(fit, "catalogue", lambda services, profile_text="": {**real(services, profile_text), "hash": "changed"})
    assert fit.cached(service, job["id"]) is None
    monkeypatch.setattr(fit, "catalogue", real)
    # A free plan came back: the rules check is upgraded to an AI one.
    team = StubTeam({"requirements": [{"text": "Python", "category": "required",
                                       "excerpt": "Entry-level Data Engineer working with Python and PostgreSQL.",
                                       "status": "met", "evidence_ids": ["SKILL-LANGUAGES-001"]}]})
    upgraded = fit.for_job(service, job["id"], team=team)
    assert upgraded["method"] == "ai" and fit.cached(service, job["id"])["method"] == "ai"
    assert fit.for_job(service, job["id"], team=StubTeam(error="unused"))["method"] == "ai"  # served from the cache


def test_saved_jobs_are_brought_up_to_date_by_rules_without_ai(service, monkeypatch):
    job = add(service.w)
    # Saved before the requirement check: an old word-overlap score, no matrix.
    with service.w.connect() as db:
        db.execute("UPDATE jobs SET fit_score=13, fit_rationale='old overlap score' WHERE id=?", (job["id"],))
    monkeypatch.setattr(fit, "fit_team", lambda services: pytest.fail("the start-up refresh never calls an AI"))
    assert fit.backfill(service) == 1
    checked = fit.cached(service, job["id"])
    assert checked["method"] == "rules"
    assert service.w.get_job(job["id"])["fit_score"] == checked["score"] != 13
    assert fit.backfill(service) == 0, "nothing to do the second time"
    # The formula changed but the matrix did not: only the shown score moves.
    with service.w.connect() as db:
        db.execute("UPDATE jobs SET fit_score=1 WHERE id=?", (job["id"],))
    assert fit.backfill(service) == 1 and service.w.get_job(job["id"])["fit_score"] == checked["score"]
    assert "Checked by rules" in service.w.get_job(job["id"])["fit_rationale"]


def test_coverage_uses_the_matrix_and_its_evidence_words(service):
    job = add(service.w)
    team = StubTeam({"requirements": [{"text": "stream processing", "category": "required",
                                       "excerpt": "Build streaming data pipelines with Apache Kafka and Flink, orchestrate ETL with Airflow on AWS (Glue, S3), write SQL transformations and validate data quality.",
                                       "status": "met", "evidence_ids": ["SKILL-STREAMING-001"]}]})
    fit.for_job(service, job["id"], team=team)
    requirements = AssessmentService(service).requirements(job["id"])
    assert requirements[0]["fit_status"] == "met"
    assert {"stream processing", "apache kafka", "apache flink"} <= set(requirements[0]["aliases"])


def test_the_check_only_ever_uses_free_plans(service, rules_only_fit_check, monkeypatch):
    policy = router.default_policy()
    assert router.available(service.w.root, router.free_only(policy), ready_map={"azure_openai": True}) == []
    assert router.available(service.w.root, router.free_only(policy), ready_map={"kimi_cli": True, "azure_openai": True})[0][0] == "kimi_cli"
    import backend.ai as ai
    monkeypatch.setattr(ai, "ready_providers", lambda root: {"azure_openai": True})
    assert rules_only_fit_check(service) is None, "only a paid key is set up: the rules path"
    monkeypatch.setattr(ai, "ready_providers", lambda root: {"kimi_cli": True, "azure_openai": True})
    team = rules_only_fit_check(service)
    assert team is not None and not team.route["policy"]["enabled"]["azure_openai"]


def test_discovery_checks_only_a_shortlist(service, monkeypatch):
    team = StubTeam()
    monkeypatch.setattr(fit, "fit_team", lambda services: team)
    batches = []

    def analyse_many(services, postings, team=None):
        batches.append(len(postings))
        return [{**fit.analyse(services, p), "score": 92, "must_have_ok": True} for p in postings]

    monkeypatch.setattr(fit, "analyse_many", analyse_many)
    unique = []
    for n in range(10):
        posting = {**POSTING, "description": JD.split(" Candidates")[0] + " " * 80, "url": f"https://acme.example/jobs/{n}"}
        unique.append({**posting, "relevance": {"score": 50 + n, "fit": fit.analyse(service, posting)}})
    output, records = {"rejected_leads": [], "summary": ""}, []
    kept = AgentRunner(service)._fit_shortlist(unique, 2, "default", output, records)
    assert batches == [2 + DISCOVERY_SPARES] and len(kept) == 2 + DISCOVERY_SPARES
    assert [job["url"] for job in kept][0].endswith("/9"), "best rules fit first"
    assert output["fit_check"]["checked"] == 2 + DISCOVERY_SPARES
    assert AgentRunner(service)._fit_shortlist(unique, 0, "default", output, records) == []
