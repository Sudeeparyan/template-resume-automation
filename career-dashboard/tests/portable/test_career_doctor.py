"""Fresh-clone diagnostics and chat CLI routing never use a real profile."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2]
REPO = APP.parent


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


doctor = load_script("career_doctor", REPO / "scripts/career_doctor.py")
cli = load_script("career_cli", APP / "backend/scripts/career_cli.py")


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(doctor.importlib.metadata, "version", lambda name: "1.0")
    monkeypatch.setattr(doctor.sys, "version_info", (3, 12, 0))
    monkeypatch.setattr(doctor, "executable", lambda name: True)
    monkeypatch.setattr(doctor, "node_status", lambda: {"available": True, "supported": True, "version": "v20.0.0"})


def registry(folder: Path, entries, last="") -> Path:
    folder.mkdir()
    path = folder / "registry.json"
    path.write_text(json.dumps({"profiles": entries, "last_used": last}), encoding="utf-8")
    return path


def action_codes(result):
    return {item["code"] for item in result["next_actions"]}


def test_installed_app_with_no_profiles_stays_in_app_mode(tmp_path, installed):
    folder = tmp_path / "profiles"
    result = doctor.report(folder)
    assert result["app_mode"] is True
    assert result["profile_count"] == 0 and result["selected_profile"] is None
    assert "setup_profile" in action_codes(result)
    assert not folder.exists()


def test_fresh_clone_doctor_runs_without_site_packages_and_does_not_write(tmp_path):
    folder = tmp_path / "profiles"
    # -S removes site packages: the installed app and pytest cannot rescue this process.
    result = subprocess.run([sys.executable, "-I", "-S", str(REPO / "scripts/career_doctor.py"),
                             "--profiles-dir", str(folder)], capture_output=True, text=True,
                            encoding="utf-8", check=True)
    data = json.loads(result.stdout)
    assert data["app_mode"] is False and data["profile_count"] == 0
    assert "PyYAML" in data["dependencies"]["missing"]
    assert {"install_app", "setup_profile"} <= action_codes(data)
    assert not folder.exists() and list(tmp_path.iterdir()) == []


def test_doctor_is_routed_without_importing_the_backend(tmp_path):
    script, args = cli.target_for(["doctor", "--profiles-dir", str(tmp_path / "profiles")])
    assert script == REPO / "scripts/career_doctor.py"
    result = subprocess.run([sys.executable, "-I", "-S", str(APP / "backend/scripts/career_cli.py"),
                             "doctor", *args], capture_output=True, text=True, encoding="utf-8", check=True)
    assert json.loads(result.stdout)["profile_count"] == 0
    assert not (tmp_path / "profiles").exists()


def test_multiple_profiles_require_selection_without_reading_candidate_files(tmp_path, installed, monkeypatch):
    folder = tmp_path / "profiles"
    path = registry(folder, [{"id": "example-one", "name": "Private name", "state": "ready", "extra": "private"},
                             {"id": "example-two", "state": "onboarding"}], "example-one")
    before = path.read_bytes()
    real_read = Path.read_text

    def guarded_read(target, *args, **kwargs):
        # The public permit-rules file and the app's own AI-client code are never candidate data.
        allowed = {path, doctor.PERMIT_RULES, *(source for source, _ in doctor.CLI_SOURCES.values())}
        assert target in allowed, "Only registry metadata may be opened"
        return real_read(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    result = doctor.report(folder)
    assert result["last_used_profile"] == "example-one" and result["selected_profile"] is None
    assert "select_profile" in action_codes(result)
    assert "Private name" not in json.dumps(result) and "private" not in json.dumps(result)
    selected = doctor.report(folder, "example-two")
    assert selected["selected_profile"] == "example-two" and "finish_setup" in action_codes(selected)
    assert path.read_bytes() == before


def test_unknown_selection_does_not_fall_back_to_someone_else(tmp_path, installed):
    folder = tmp_path / "profiles"
    registry(folder, [{"id": "example-one", "state": "ready"}], "missing-profile")
    result = doctor.report(folder, "missing-profile")
    assert result["selected_profile"] is None and result["last_used_profile"] is None
    assert result["selection_error"] and "select_profile" in action_codes(result)
    assert "read_profile_status" not in action_codes(result)
    single = doctor.report(folder)
    assert single["selected_profile"] == "example-one" and "read_profile_status" in action_codes(single)


@pytest.mark.parametrize("content", ["{", "null", "[]", '{"profiles": null}',
    '{"profiles": [{"id": "../outside", "state": "ready"}]}',
    '{"profiles": [{"id": "example", "state": []}]}',
    '{"profiles": [{"id": "example", "state": "ready"}, {"id": "example", "state": "ready"}]}'])
def test_damaged_registry_is_never_a_fresh_start(tmp_path, installed, content):
    folder = tmp_path / "profiles"
    folder.mkdir()
    path = folder / "registry.json"
    path.write_text(content, encoding="utf-8")
    result = doctor.report(folder)
    assert result["registry_error"] and result["profile_count"] is None
    assert "repair_registry" in action_codes(result) and "setup_profile" not in action_codes(result)
    assert path.read_text(encoding="utf-8") == content


def test_executable_discovery_never_claims_signin(tmp_path, installed):
    result = doctor.report(tmp_path / "profiles")
    assert all(result["tools"]["ai_cli"].values())
    assert "sign-in" in result["tools"]["ai_check"]
    assert "verify_ai_provider" in action_codes(result)
    assert "credentials" in result["note"] and result["read_only"] is True


def test_doctor_warns_if_permit_rules_are_missing_or_not_utf8(tmp_path, installed, monkeypatch):
    path = tmp_path / "permit-rules.yml"
    monkeypatch.setattr(doctor, "PERMIT_RULES", path)
    missing = doctor.report(tmp_path / "profiles")
    assert missing["permit_rules"]["state"] == "missing"
    assert "review_permit_rules" in action_codes(missing)
    path.write_bytes(b"\xff")
    invalid = doctor.report(tmp_path / "profiles")
    assert invalid["permit_rules"]["state"] == "unreadable"
    assert "review_permit_rules" in action_codes(invalid)


@pytest.mark.parametrize("args,expected", [
    (["status", "--profile", "example"], ["--profile", "example", "status"]),
    (["--profile", "example", "status"], ["--profile", "example", "status"]),
    (["jobs", "--profile=example"], ["--profile=example", "jobs"]),
    (["update", "job-one", "--status", "applied", "--profile", "example"],
     ["--profile", "example", "update", "job-one", "--status", "applied"]),
])
def test_profile_flag_is_accepted_before_or_after_default_subcommands(args, expected):
    script, actual = cli.target_for(args)
    assert script == cli.DEFAULT and actual == expected


def test_special_routes_keep_their_own_profile_parser():
    script, args = cli.target_for(["ws", "summary", "--profile", "example"])
    assert script == cli.TARGETS["ws"] and args == ["summary", "--profile", "example"]
    script, args = cli.target_for(["setup", "--profile=example"])
    assert script == cli.TARGETS["setup"] and args == ["--profile=example"]


@pytest.mark.parametrize("authorization,expected", [
    ({"status": "authorized", "citizenship": "citizen"}, "C"),
    ({"status": "needs_sponsorship", "citizenship": "noncitizen"}, "EXCLUDED"),
    ({"status": "unknown", "citizenship": "unknown"}, None),
])
def test_sponsor_cli_uses_selected_profile_authorization(tmp_path, monkeypatch, capsys, authorization, expected):
    import yaml
    import backend.profiles
    import workspace
    from backend.profiles import ProfileStore

    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    profile = store.create("Example Person")
    root = store.root_for(profile["id"])
    (root / "data/config/profile.yml").write_text(yaml.safe_dump({
        "candidate": {"full_name": "Example Person"}, "country_pack": "ie", "target_markets": ["ie"],
        "work_authorization_by_market": {"ie": authorization}}), encoding="utf-8")
    (root / "data/context/evidence.yml").write_text("claims: []\nprojects: []\n", encoding="utf-8")
    posting = tmp_path / "posting.txt"
    posting.write_text("We cannot provide visa sponsorship.", encoding="utf-8")
    monkeypatch.setattr(backend.profiles, "store", lambda: store)
    monkeypatch.setattr(sys, "argv", ["workspace.py", "sponsor-check", "--profile", profile["id"],
                                    "--company", "Example Services", "--file", str(posting), "--market", "ie"])
    if expected is None:
        with pytest.raises(ValueError, match="Confirm work authorization"):
            workspace.main()
    else:
        workspace.main()
        result = json.loads(capsys.readouterr().out)
        assert result["tier"] == expected
        assert result["h1b_found"] is False
        if expected == "EXCLUDED":
            assert result["screen"]["sentence"] == posting.read_text(encoding="utf-8")


def test_bundled_ai_clis_are_found_where_the_app_looks(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "executable", lambda name: False)
    for _, variable in doctor.CLI_SOURCES.values():
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(doctor, "bundled_locations", lambda source: [str(tmp_path / "bundle" / "*" / "claude.exe")]
                        if source.name == "claude_code.py" else [])
    assert not doctor.cli_available("claude")
    (tmp_path / "bundle" / "2.1.0").mkdir(parents=True)
    (tmp_path / "bundle" / "2.1.0" / "claude.exe").write_bytes(b"")
    assert doctor.cli_available("claude") and not doctor.cli_available("codex")
    assert doctor.bundled_locations(doctor.CLI_SOURCES["codex"][0]) or True  # parsing the real source never raises


def test_the_app_knows_bundled_locations_from_source():
    patterns = doctor.bundled_locations(doctor.CLI_SOURCES["claude"][0])
    assert patterns and all(isinstance(p, str) for p in patterns)


def test_a_onedrive_workspace_is_flagged_without_printing_its_path(tmp_path, installed, monkeypatch):
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))
    assert doctor.in_synced_folder(tmp_path / "OneDrive" / "Desktop" / "Template")
    assert doctor.in_synced_folder(Path("D:/Work/OneDrive - Example Ltd/Template"))
    assert not doctor.in_synced_folder(tmp_path / "Local" / "Template")
    monkeypatch.setattr(doctor, "in_synced_folder", lambda path=None: True)
    result = doctor.report(tmp_path / "profiles")
    assert result["synced_folder"] is True and "move_out_of_sync" in action_codes(result)
    assert str(tmp_path) not in json.dumps(result)
