"""The LangGraph workflows: verified company dossiers, the research graph, and durable resumption."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.graphs import checkpoint, dossier, history, research
from backend.graphs.executor import run_graph
from backend.graphs.runtime import GraphContext
from backend.market.store import MarketStore
from backend.services.agents import AgentRunner
from test_hunt import ireland_profile
from test_job_sources import fetcher

ABOUT = "https://acme.example/about"
NEWS = "https://news.example/acme-dublin"
ABOUT_PAGE = ("<html><body><p>Acme Analytics builds reporting software for hospitals.</p>"
              "<p>Our Dublin office opened in 2019 and employs 240 people.</p></body></html>")
NEWS_PAGE = ("<html><body><p>Acme Analytics will add 100 jobs in Dublin over two years.</p>"
             "<p>The company was founded in Galway in 2015.</p></body></html>")
REPORT = {"summary": "View", "report": "Report text", "sources": [], "limitations": []}


def facet_claims(prompt: str) -> dict:
    if "FACET: What the company does" in prompt:
        return {"claims": [{"text": "Acme builds reporting software.", "quote": "builds reporting software for hospitals",
                            "url": ABOUT, "published_at": ""},
                           {"text": "Acme is the largest company in Ireland.", "quote": "the largest company in Ireland",
                            "url": ABOUT, "published_at": ""}]}
    if "FACET: Its presence in Ireland" in prompt:
        return {"claims": [{"text": "Acme employs 240 people in Dublin.", "quote": "Our Dublin office opened in 2019 and employs 240 people",
                            "url": ABOUT, "published_at": ""},
                           {"text": "Acme has a Cork lab.", "quote": "our Cork laboratory opened last year", "url": "http://10.0.0.5/x",
                            "published_at": ""}]}
    if "FACET: News" in prompt:
        return {"claims": [{"text": "Acme adds 100 jobs.", "quote": "will add 100 jobs in Dublin over two years",
                            "url": NEWS, "published_at": "2026-09-01"},
                           {"text": "Acme opened in 2015.", "quote": "The company was founded in Galway in 2015",
                            "url": NEWS, "published_at": "2015-01-01"}]}
    return {"claims": []}


def runner_for(services, calls):
    runner = AgentRunner(services)
    runner.gateway.resolve = lambda action, provider=None, model=None: (
        SimpleNamespace(id="kimi_cli", capabilities={"web"}), "kimi-runtime")

    def generate(action, text, shape, **options):
        calls.append({"prompt": text, "web": options.get("web")})
        if "You research one facet" in text:
            return facet_claims(text)
        return REPORT

    runner.gateway.generate = generate
    return runner


def context_for(runner, run_id="run-1"):
    pages = fetcher({ABOUT: ABOUT_PAGE, NEWS: NEWS_PAGE})
    return GraphContext(services=runner.s, runner=runner, run_id=run_id, fetcher=pages,
                        url_check=lambda url: "10.0.0.5" not in url)


def test_a_dossier_keeps_only_claims_whose_quote_is_on_the_cited_page(tmp_path):
    services = ireland_profile(tmp_path)
    calls = []
    runner = runner_for(services, calls)
    final = run_graph("dossier", dossier.build, context_for(runner), {"employer": "Acme Analytics", "depth": "standard"},
                      root=services.w.root)
    found = final["dossier"]
    kept = {claim["text"] for claim in found["claims"]}
    assert kept == {"Acme builds reporting software.", "Acme employs 240 people in Dublin.", "Acme adds 100 jobs."}
    reasons = {claim["text"]: claim["reason"] for claim in found["unverified"]}
    assert reasons["Acme is the largest company in Ireland."] == "the quote is not on the cited page word for word"
    assert reasons["Acme has a Cork lab."] == "not a public web address"
    assert reasons["Acme opened in 2015."].startswith("news older than twelve months")
    assert all(call["web"] for call in calls) and len(calls) == 4  # one web search per facet, nothing else
    assert not any("candidate" in call["prompt"].casefold() and "no candidate" not in call["prompt"].casefold() for call in calls)
    report = dossier.as_report(found)
    assert "word for word" in report["report"] and {s["url"] for s in report["sources"]} == {ABOUT, NEWS}
    # Shared and reused: a second run for another profile makes no AI call.
    again = []
    other = run_graph("dossier", dossier.build, context_for(runner_for(services, again), "run-2"),
                      {"employer": "Acme Analytics", "depth": "quick"}, root=services.w.root)
    assert other["cached"] and again == [] and MarketStore().dossier(found["employer_key"])["claims"]


def test_the_research_graph_keeps_the_hiring_manager_away_from_the_profile_and_writes_its_files(tmp_path):
    services = ireland_profile(tmp_path)
    job = services.w.add_job("Acme Analytics", "Data Analyst", "Dublin, Ireland", "https://jobs.example/acme/1",
                             "Build SQL dashboards for hospital clients and present findings. " * 4)
    folder = services.w.root / "data/output/2026-10-04/acme-data-analyst"
    folder.mkdir(parents=True)
    with services.w.connect() as db:
        db.execute("UPDATE jobs SET folder=? WHERE id=?", ("data/output/2026-10-04/acme-data-analyst", job["id"]))
    calls = []
    runner = runner_for(services, calls)
    row = {"id": "research-1", "job_id": job["id"], "input": json.dumps({"company": "Acme Analytics", "title": "Data Analyst",
                                                                          "location": "Dublin, Ireland", "description": "SQL"})}
    output = research.run(runner, row, fetcher=fetcher({ABOUT: ABOUT_PAGE, NEWS: NEWS_PAGE}),
                          url_check=lambda url: "10.0.0.5" not in url)
    assert set(output) >= {"research", "hiring", "comparison", "path", "hiring_profile_access", "dossier"}
    assert output["hiring_profile_access"] is False and output["dossier"]["verified_claims"] == 3
    hiring = next(call for call in calls if "ROLE AND PUBLIC RESEARCH" in call["prompt"])
    profile_text = json.dumps(services.profile_context())
    assert '"profile"' not in hiring["prompt"] and hiring["web"] is False
    compared = next(call for call in calls if "verified_requirement_check" in call["prompt"])
    assert '"profile"' in compared["prompt"] and profile_text[:40] in compared["prompt"]
    assert output["path"].replace("\\", "/") == "data/output/2026-10-04/acme-data-analyst/company-research.md"
    assert "word for word" in (folder / "company-research.md").read_text(encoding="utf-8")
    assert (folder / "hiring-manager.md").is_file()
    assert json.loads((folder / "role-analysis.json").read_text(encoding="utf-8"))["dossier"]["claims"] == 3


def test_a_run_stopped_mid_graph_resumes_after_its_last_finished_node(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    calls = []
    runner = runner_for(services, calls)
    row = {"id": "research-2", "job_id": "", "input": json.dumps({"company": "Acme Analytics", "title": "Data Analyst"})}
    real = research.comparison
    attempts = {"n": 0}

    def flaky(state, runtime):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("the app was closed")
        return real(state, runtime)

    monkeypatch.setattr(research, "comparison", flaky)
    options = {"fetcher": fetcher({ABOUT: ABOUT_PAGE, NEWS: NEWS_PAGE}), "url_check": lambda url: True}
    with pytest.raises(RuntimeError):
        research.run(runner, row, **options)
    stopped = history.summary(services.w.root, "research-2")["threads"][0]
    assert stopped["thread"] == "research:research-2" and not stopped["finished"] and stopped["resumes_at"] == ["comparison"]
    before = len(calls)
    output = research.run(runner, row, **options)
    assert output["comparison"] == REPORT and attempts["n"] == 2
    assert len(calls) - before == 1  # only the comparison ran again: no repeated web research
    assert research.run(runner, row, **options)["comparison"] == REPORT and len(calls) - before == 1


def test_a_run_can_be_run_again_from_one_step_as_a_new_run(tmp_path):
    services = ireland_profile(tmp_path)
    calls = []
    runner = runner_for(services, calls)
    role = {"company": "Acme Analytics", "title": "Data Analyst"}
    options = {"fetcher": fetcher({ABOUT: ABOUT_PAGE, NEWS: NEWS_PAGE}), "url_check": lambda url: True}
    research.run(runner, {"id": "research-3", "job_id": "", "input": json.dumps(role)}, **options)
    old = history.summary(services.w.root, "research-3")["threads"][0]
    point = next(step["checkpoint_id"] for step in old["steps"] if step["next"] == ["comparison"])
    before = len(calls)
    again = {"id": "research-4", "job_id": "",
             "input": json.dumps({**role, "_rerun_from": {"run_id": "research-3", "checkpoint_id": point}})}
    output = research.run(runner, again, **options)
    assert output["comparison"] == REPORT and output["dossier"]["verified_claims"] == 3
    assert len(calls) - before == 1  # only the comparison ran again: the dossier and hiring view were kept
    new = history.summary(services.w.root, "research-4")["threads"][0]
    assert new["thread"] == "research:research-4" and new["finished"]
    assert any(step["source"] == "fork" for step in new["steps"])
    assert history.summary(services.w.root, "research-3")["threads"][0]["steps"] == old["steps"]  # unchanged
    with pytest.raises(ValueError, match="Nothing runs after"):
        research.run(runner, {"id": "research-5", "job_id": "", "input": json.dumps(
            {**role, "_rerun_from": {"run_id": "research-3", "checkpoint_id": old["steps"][-1]["checkpoint_id"]}})}, **options)
    with pytest.raises(ValueError, match="not among"):
        research.run(runner, {"id": "research-6", "job_id": "", "input": json.dumps(
            {**role, "_rerun_from": {"run_id": "research-3", "checkpoint_id": "no-such-checkpoint"}})}, **options)
    checkpoint.close(services.w.root)


def test_only_a_finished_research_run_of_the_same_job_can_be_rerun(tmp_path):
    services = ireland_profile(tmp_path)
    runner = AgentRunner(services)
    with services.w.connect() as db:
        for run_id, kind, state in (("r-done", "research", "completed"), ("r-busy", "research", "running"),
                                    ("d-done", "discovery", "completed")):
            db.execute("INSERT INTO agent_runs(id,kind,job_id,state,input,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (run_id, kind, None, state, "{}", services.now(), services.now()))
    with pytest.raises(ValueError, match="Only a research run"):
        runner.enqueue("discovery", None, rerun_from={"run_id": "d-done", "checkpoint_id": "x"})
    with pytest.raises(ValueError, match="not a research run"):
        runner._rerun_source("research", None, {"run_id": "d-done", "checkpoint_id": "x"})
    with pytest.raises(ValueError, match="still going"):
        runner._rerun_source("research", None, {"run_id": "r-busy", "checkpoint_id": "x"})
    with pytest.raises(ValueError, match="no saved checkpoints"):
        runner._rerun_source("research", None, {"run_id": "r-done", "checkpoint_id": "x"})


def test_a_runs_history_lists_each_node_and_its_changes_without_the_data_unless_asked(tmp_path):
    services = ireland_profile(tmp_path)
    runner = runner_for(services, [])
    run_graph("dossier", dossier.build, context_for(runner, "run-h"), {"employer": "Acme Analytics", "depth": "quick"},
              root=services.w.root)
    found = history.summary(services.w.root, "run-h")
    (thread,) = found["threads"]
    assert thread["thread"] == "dossier:run-h" and thread["finished"] and thread["resumes_at"] == []
    ran = [node for step in thread["steps"] for node in step["ran"]]
    assert ran == ["cache_lookup", "facts", "plan", "research_facet", "verify", "assemble", "store"]
    assert any("(parallel tasks)" in step["next"] for step in thread["steps"])
    facts_step = next(step for step in thread["steps"] if step["ran"] == ["facts"])
    assert set(facts_step["changed"]) == {"facts"} and "preview" not in facts_step["changed"]["facts"]
    assert "Acme" not in json.dumps(found)  # redacted: types, sizes and hashes only
    shown = history.summary(services.w.root, "run-h", content=True)["threads"][0]
    assert "Acme Analytics" in json.dumps(shown)
    checkpoint.close(services.w.root)


def test_old_checkpoints_are_pruned(tmp_path):
    services = ireland_profile(tmp_path)
    runner = runner_for(services, [])
    run_graph("dossier", dossier.build, context_for(runner, "old-run"), {"employer": "Acme Analytics", "depth": "quick"},
              root=services.w.root)
    assert "dossier:old-run" in checkpoint.threads(services.w.root)
    assert checkpoint.prune_old(services.w.root, days=-1) == 1 and checkpoint.threads(services.w.root) == []
    checkpoint.close(services.w.root)


def test_a_facet_whose_ai_answer_is_unusable_is_skipped_not_fatal_and_not_cached(tmp_path):
    services = ireland_profile(tmp_path)
    calls = []
    runner = runner_for(services, calls)
    real = runner.gateway.generate

    def generate(action, text, shape, **options):
        if "FACET: News" in text:
            return {"summary": "not the shape asked for"}
        return real(action, text, shape, **options)

    runner.gateway.generate = generate
    final = run_graph("dossier", dossier.build, context_for(runner, "run-s"), {"employer": "Acme Analytics", "depth": "standard"},
                      root=services.w.root)
    found = final["dossier"]
    assert {claim["facet"] for claim in found["claims"]} == {"overview", "irish_presence"}
    assert [item["facet"] for item in found["skipped"]] == ["news"]
    assert any(line.startswith("Recent news: not researched") for line in dossier.as_report(found)["limitations"])
    assert MarketStore().dossier(found["employer_key"]) is None  # a gap is not reused for 30 days
    checkpoint.close(services.w.root)
