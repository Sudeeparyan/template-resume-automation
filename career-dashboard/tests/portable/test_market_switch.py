"""One switch decides which job markets this copy offers (backend/countries/markets.yml).

This copy searches Ireland only. The US pack stays in the code and its tests run with the
``us_enabled`` fixture; a profile built for a switched-off market keeps its own rules and
is refused, never silently turned into another country's profile.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from backend.countries import (  # noqa: E402
    COUNTRIES,
    available,
    enabled_markets,
    is_enabled,
    known_markets,
    pack_for,
    require_enabled_markets,
    require_known_authorization,
    target_markets_for,
)
from backend.profiles import ProfileError, ProfileStore  # noqa: E402
from backend.services.intake.job import guess_pack  # noqa: E402
from backend.services.intake.job import IntakeJob, STATE_REVIEW  # noqa: E402
from backend.services.intake.build import build_profile  # noqa: E402
from backend.services.intake.runs import BuildRuns  # noqa: E402

STAMP_1G = {
    "status": "authorized",
    "citizenship": "noncitizen",
    "needs_sponsorship_later": "yes",
}


def _profile(root: Path, markets: list[str], authorization: dict | None = None) -> Path:
    path = root / "data/config/profile.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "country_pack": markets[0],
                "target_markets": markets,
                "candidate": {"full_name": "Example Person"},
                "work_authorization_by_market": authorization or {},
            }
        ),
        encoding="utf-8",
    )
    return root


def front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


def test_this_copy_offers_ireland_only():
    config = yaml.safe_load((COUNTRIES / "markets.yml").read_text(encoding="utf-8"))
    assert config["enabled"] == ["ie"]
    assert enabled_markets() == ["ie"] and is_enabled("ie") and not is_enabled("us")
    assert [pack.code for pack in available()] == ["ie"]
    # The US pack is still in the code, dormant.
    assert set(known_markets()) == {"ie", "us"}
    assert {pack.code for pack in available(include_disabled=True)} == {"ie", "us"}


def test_the_environment_switch_turns_the_us_back_on(us_enabled):
    assert enabled_markets() == ["ie", "us"] and is_enabled("us")
    assert [pack.code for pack in available()] == ["ie", "us"]


def test_an_unknown_or_empty_switch_falls_back_to_ireland(monkeypatch):
    monkeypatch.setenv("CAREER_MARKETS", "xx, ")
    assert enabled_markets() == ["ie"]


def test_a_dual_market_profile_searches_only_the_offered_market(tmp_path):
    root = _profile(tmp_path, ["ie", "us"], {"ie": STAMP_1G})
    assert target_markets_for(root) == ["ie"]
    require_enabled_markets(root)
    require_known_authorization(root)  # the US facts are not needed while the US is off


def test_a_profile_for_a_switched_off_market_is_refused_not_moved(tmp_path):
    root = _profile(
        tmp_path, ["us"], {"us": {"status": "authorized", "citizenship": "citizen"}}
    )
    assert target_markets_for(root) == ["us"] and pack_for(root).code == "us"
    with pytest.raises(ValueError, match="no longer offers"):
        require_known_authorization(root)
    with pytest.raises(ValueError, match="switched off"):
        require_known_authorization(root, "us")


def test_onboarding_never_guesses_a_switched_off_market(monkeypatch):
    american = {
        "targets": {"countries": ["United States"]},
        "contact": {"country": "United States"},
    }
    assert guess_pack(american) == "ie"
    monkeypatch.setenv("CAREER_MARKETS", "ie,us")
    assert guess_pack(american) == "us"


def test_the_profile_list_refuses_a_switched_off_market(tmp_path):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    profile = store.create("Example Person")
    with pytest.raises(ProfileError, match="Ireland"):
        store.update(profile["id"], target_markets=["us"])
    assert store.update(profile["id"], target_markets=["ie"])["target_markets"] == [
        "ie"
    ]


def test_a_new_profile_uses_the_first_enabled_market(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREER_MARKETS", "us")
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    assert store.create("Example Person")["target_markets"] == ["us"]


def test_the_review_country_picker_cannot_bypass_the_market_switch(tmp_path):
    job = IntakeJob(tmp_path)
    job._write("draft.json", {"country_pack": "ie", "target_markets": ["ie"]})
    with pytest.raises(ValueError, match="switched off"):
        job.update({"country_pack": "us"})
    assert job.draft()["target_markets"] == ["ie"]
    with pytest.raises(ValueError, match="Ireland"):
        job.update({"target_markets": "ie"})


def test_a_review_country_change_updates_the_selected_markets(tmp_path, us_enabled):
    job = IntakeJob(tmp_path)
    job._write("draft.json", {"country_pack": "ie", "target_markets": ["ie"]})
    assert job.update({"country_pack": "us"})["target_markets"] == ["us"]


def test_a_direct_profile_build_refuses_a_dormant_pack():
    from backend.countries import load_pack

    with pytest.raises(ValueError, match="switched off"):
        build_profile({}, {}, load_pack("us"), "test")


def test_a_direct_profile_build_does_not_substitute_ireland_for_dormant_targets():
    from backend.countries import load_pack

    draft = {
        "contact": {},
        "authorization": {},
        "targets": {},
        "target_markets": ["us"],
    }
    with pytest.raises(ValueError, match="no longer offers"):
        build_profile(draft, {"claims": [], "projects": []}, load_pack("ie"), "test")


def test_direct_prepare_refuses_a_dormant_profile_before_creating_artifacts(tmp_path):
    from career import Workspace

    root = _profile(tmp_path, ["us"])
    workspace = Workspace(root)
    with pytest.raises(ValueError, match="no longer offers"):
        workspace.prepare("example-job")
    assert not (root / "data/output/applications").exists()


@pytest.mark.parametrize(
    "kind",
    [
        "research",
        "resume_advisor",
        "resume_build",
        "resume_match",
        "instruction_interpret",
        "study_plan",
        "salary_research",
    ],
)
def test_job_agents_refuse_dormant_jobs_before_resolving_ai(tmp_path, kind):
    from types import SimpleNamespace
    from backend.services.agents import AgentRunner

    root = _profile(tmp_path, ["ie"])
    runner = AgentRunner.__new__(AgentRunner)
    runner.s = SimpleNamespace(agent_enabled=lambda _kind: True)
    runner.w = SimpleNamespace(
        root=root, get_job=lambda _id: {"id": "example-job", "market": "us"}
    )
    runner.gateway = SimpleNamespace(
        resolve=lambda *_args: pytest.fail("A dormant-market job must not resolve AI")
    )
    with pytest.raises(ValueError, match="no longer offers"):
        runner.enqueue(kind, job_id="example-job")


def test_direct_studio_tailoring_refuses_a_dormant_saved_job(tmp_path):
    from types import SimpleNamespace
    from backend.services.resume_studio import ResumeStudio

    studio = ResumeStudio.__new__(ResumeStudio)
    studio.w = SimpleNamespace(
        root=_profile(tmp_path, ["us"]),
        get_job=lambda _id: {"id": "example-job", "market": "us"},
    )
    with pytest.raises(ValueError, match="no longer offers"):
        studio.tailor("example-job", None)


@pytest.mark.parametrize("kind", ["discovery", "research"])
def test_recovered_runs_recheck_the_market_before_dispatch(tmp_path, kind):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from backend.services.agents import AgentRunner

    row = {"kind": kind, "job_id": None if kind == "discovery" else "example-job"}

    @contextmanager
    def connect():
        yield SimpleNamespace(
            execute=lambda *_args: SimpleNamespace(fetchone=lambda: row)
        )

    runner = AgentRunner.__new__(AgentRunner)
    runner.w = SimpleNamespace(
        root=_profile(tmp_path, ["us"]),
        connect=connect,
        get_job=lambda _id: {"market": "us"},
    )
    with pytest.raises(ValueError, match="no longer offers"):
        runner._run("recovered-run")
    assert not hasattr(runner, "current_provider")


def test_build_runs_refuse_an_explicit_empty_market_selection(tmp_path):
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    profile = store.create("Example Person")
    runner = BuildRuns(
        profile["id"], IntakeJob(store.root_for(profile["id"])), store, None, None
    )
    with pytest.raises(ValueError, match="Ireland"):
        runner.start({"target_markets": []})


def test_a_failed_rebuild_restores_a_legacy_dual_market_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREER_MARKETS", "ie,us")
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    profile = store.create("Example Person")
    before = store.update(
        profile["id"], state="ready", target_markets=["ie", "us"], built_at="old"
    )
    root = store.root_for(profile["id"])
    config = root / "data/config/profile.yml"
    config.write_text("candidate_revision: old\n", encoding="utf-8")
    job = IntakeJob(root)
    job.add_note("Synthetic source", "Example Person seeks analyst work in Ireland.")
    job._write(
        "draft.json",
        {
            "contact": {"full_name": "Example Person"},
            "targets": {"roles": ["Analyst"]},
            "authorization": {},
            "country_pack": "ie",
            "target_markets": ["ie", "us"],
        },
    )
    job._save(state=STATE_REVIEW)

    def build():
        config.write_text("candidate_revision: new\n", encoding="utf-8")
        return {
            "files": ["data/config/profile.yml"],
            "pack": "ie",
            "built_at": "new",
            "revision": "new",
        }

    job.build = build

    class Apps:
        def close(self, _profile_id):
            pass

        def app(self, _profile_id):
            pass

    def finish(profile_id, _job, _summary, **_kwargs):
        store.update(profile_id, target_markets=["ie"], built_at="new")
        return {
            "compiled": False,
            "status": "FAIL",
            "failures": ["Synthetic PDF failure"],
        }

    monkeypatch.setenv("CAREER_MARKETS", "ie")
    runner = BuildRuns(profile["id"], job, store, Apps(), finish)
    run = runner.start_reviewed({"target_markets": ["ie"]})
    runner.thread.join(timeout=5)
    result = runner.get(run["id"])
    assert result["status"] == "failed" and "restoring" not in str(result["errors"])
    assert config.read_text(encoding="utf-8") == "candidate_revision: old\n"
    restored = store.get(profile["id"])
    assert (
        restored["target_markets"] == before["target_markets"]
        and restored["built_at"] == "old"
    )
    with pytest.raises(ProfileError, match="Ireland"):
        store.update(profile["id"], target_markets=["us"])


def test_ai_apps_are_offered_only_the_ireland_job_source_skill():
    assert (REPO / ".agents/skills/ireland-job-sources/SKILL.md").is_file()
    assert not (REPO / ".agents/skills/us-job-sources").exists()
    assert not (REPO / ".claude/skills/us-job-sources").exists()
    # The US skill is kept, well formed, in the dormant pack.
    dormant = COUNTRIES / "us/agent-skill"
    meta = front_matter(dormant / "us-job-sources/SKILL.md")
    assert meta["name"] == "us-job-sources" and 40 <= len(meta["description"]) <= 1024
    assert front_matter(dormant / "claude-pointer/SKILL.md") == meta
    assert "us-job-sources" not in (REPO / "AGENTS.md").read_text(encoding="utf-8")


def test_workspace_validation_checks_the_skills_match_the_markets():
    sys.path.insert(0, str(ROOT / "backend/scripts"))
    import validate_workspace

    validate_workspace.ERRORS.clear()
    validate_workspace.validate_markets()
    assert validate_workspace.ERRORS == []
