from datetime import date
import json

import pytest
import yaml

from make_demo_profile import MARKER, make_demo, require_synthetic_demo


def test_demo_build_uses_separate_registry_and_preserves_the_evidence_gates(tmp_path):
    base = tmp_path / "demo/profiles"
    result = make_demo(base, as_of=date(2026, 10, 3))
    assert result["synthetic"] and not result["pdf_built"]
    root = base / result["profile_id"]
    require_synthetic_demo(root)
    profile = yaml.safe_load(
        (root / "data/config/profile.yml").read_text(encoding="utf-8")
    )
    evidence = yaml.safe_load(
        (root / "data/context/evidence.yml").read_text(encoding="utf-8")
    )
    assert profile["target_markets"] == ["ie"]
    assert profile["work_authorization_by_market"]["ie"]["valid_until"] == "2027-10-03"
    assert profile["education_for_permits"]["nfq_level"] == 9
    from backend.countries import require_known_authorization
    from backend.services.salary import floor_for

    require_known_authorization(root, "ie")
    assert floor_for(profile, on=date(2026, 10, 3)) == 34009
    assert profile["work_authorization_by_market"]["ie"]["valid_until_confirmed"] is True
    assert profile["education_for_permits"]["award_date_confirmed"] is True
    assert profile["target_roles"]["max_years_required"] == 2
    assert evidence["projects"] and all(c["source_refs"] for c in evidence["claims"])
    entries = evidence["claims"] + evidence["projects"]
    assert all(entry["status"] == "user_reported" for entry in entries)
    for entry in entries:
        for ref in entry["source_refs"]:
            source, block = ref.split("#", 1)
            assert (root / source).is_file() and block == "DEMO001"
            assert "Synthetic demo only" in (root / source).read_text(encoding="utf-8")
    assert (root / "data/templates/resume-base.tex").is_file()
    assert not (root / "data/career.db").exists()  # no preferences that weaken gates
    original = (root / "data/config/profile.yml").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        make_demo(base)
    assert (root / "data/config/profile.yml").read_bytes() == original


def test_demo_tools_refuse_the_real_registry_and_unmarked_workspaces(
    tmp_path, monkeypatch
):
    from backend import paths

    monkeypatch.setattr(paths, "PROFILES", tmp_path / "real-profiles")
    with pytest.raises(ValueError, match="separate registry"):
        make_demo(paths.PROFILES)
    with pytest.raises(ValueError, match="Real profiles"):
        require_synthetic_demo(paths.PROFILES / "person")
    with pytest.raises(ValueError, match="marked synthetic"):
        require_synthetic_demo(tmp_path / "unmarked")


def test_demo_never_reads_or_changes_an_existing_profile_registry(
    tmp_path, monkeypatch
):
    from pathlib import Path

    base = tmp_path / "existing-profiles"
    base.mkdir()
    registry = base / "registry.json"
    registry.write_text("a deliberately unreadable existing registry", encoding="utf-8")
    before = registry.read_bytes()
    original_read = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert (
            path != registry
        ), "The demo command must not open an existing person's registry"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    with pytest.raises(ValueError, match="registry already exists"):
        make_demo(base)
    assert registry.read_bytes() == before
    assert list(base.iterdir()) == [registry]


def test_demo_refuses_a_disabled_ireland_market_before_creating_files(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CAREER_MARKETS", "us")
    base = tmp_path / "new-demo"
    with pytest.raises(ValueError, match="Ireland market"):
        make_demo(base)
    assert not base.exists()


@pytest.mark.parametrize(
    "marker,profile",
    [
        ([], {"synthetic_demo": True}),
        ({"synthetic": True, "schema_version": 1, "as_of": "2026-10-03"}, []),
        (
            {"synthetic": True, "schema_version": True, "as_of": "2026-10-03"},
            {"synthetic_demo": True},
        ),
        (
            {"synthetic": True, "schema_version": 1, "as_of": 20261003},
            {"synthetic_demo": True},
        ),
        (
            {"synthetic": True, "schema_version": 1, "as_of": "invalid"},
            {"synthetic_demo": True},
        ),
        (
            {"synthetic": True, "schema_version": 1, "as_of": "2026-10-03"},
            {"synthetic_demo": "true"},
        ),
    ],
)
def test_demo_open_guard_requires_well_formed_markers_and_profile_mapping(
    tmp_path, marker, profile
):
    root = tmp_path / "demo"
    config = root / "data/config/profile.yml"
    config.parent.mkdir(parents=True)
    config.write_text(yaml.safe_dump(profile), encoding="utf-8")
    (root / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(ValueError, match="marked synthetic"):
        require_synthetic_demo(root)
