"""Morning jobs in Task Scheduler: one task for the PC that follows every profile's switch."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.dashboard.shell import create_shell
from backend.profiles import ProfileStore
from backend.services import schedule_tasks


def test_the_morning_task_wakes_the_pc_runs_on_battery_and_starts_twice(monkeypatch, no_task_scheduler):
    monkeypatch.setattr(schedule_tasks, "supported", lambda: True)
    result = schedule_tasks.install_morning("09:00")
    assert result["installed"] and result["night_start"] == "01:30" and "ready by 09:00" in result["note"]
    script = no_task_scheduler[-1]
    for part in ("-At '01:30'", "-At '09:00'", "-WakeToRun", "-StartWhenAvailable", "-AllowStartIfOnBatteries",
                 "-DontStopIfGoingOnBatteries", "-MultipleInstances IgnoreNew", "-RestartCount 3", "autopilot.py",
                 "'Career Morning Jobs'", "'Career Daily Job Search - *'"):
        assert part in script, part
    assert schedule_tasks.night_start("07:00") == "23:30"


def test_off_windows_or_in_a_test_copy_nothing_is_registered(monkeypatch, no_task_scheduler):
    monkeypatch.setattr(schedule_tasks, "supported", lambda: False)
    assert not schedule_tasks.install_morning("09:00")["installed"]
    assert schedule_tasks.morning_status() == {"exists": False, "supported": False}
    assert no_task_scheduler == []


def test_settings_says_why_there_is_no_task(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule_tasks, "supported", lambda: False)
    monkeypatch.setattr(schedule_tasks, "unsupported_note", lambda: "Scheduled tasks are switched off for this copy.")
    with TestClient(create_shell(two_ready_profiles(tmp_path), frontend=tmp_path / "no-page"),
                    base_url="http://127.0.0.1") as client:
        shown = client.get("/api/profiles/ana/schedule").json()
    assert shown["supported"] is False and shown["note"] == "Scheduled tasks are switched off for this copy."


def two_ready_profiles(tmp_path):
    base = tmp_path / "profiles"
    base.mkdir()
    people = [{"id": pid, "name": name, "country": "ie", "target_markets": ["ie"], "state": "ready", "locked": False}
              for pid, name in (("ana", "Ana Example"), ("ben", "Ben Example"))]
    (base / "registry.json").write_text(json.dumps({"profiles": people, "last_used": "ana"}), encoding="utf-8")
    return ProfileStore(base=base, legacy_root=tmp_path / "no-legacy")


def test_the_switch_in_settings_keeps_one_task_in_step_with_every_profile(tmp_path, monkeypatch, no_task_scheduler):
    monkeypatch.setattr(schedule_tasks, "supported", lambda: True)
    store = two_ready_profiles(tmp_path)
    registered = lambda: [s for s in no_task_scheduler if "Register-ScheduledTask" in s]  # noqa: E731
    with TestClient(create_shell(store, frontend=tmp_path / "no-page"), base_url="http://127.0.0.1") as client:
        off = client.get("/api/profiles/ana/schedule").json()
        assert off["enabled"] is False and off["ready_by"] == "09:00" and off["night_start"] == "01:30"

        on = client.put("/api/profiles/ana/schedule", json={"enabled": True, "ready_by": "08:00"}).json()
        assert on["enabled"] and on["ready_by"] == "08:00" and on["night_start"] == "00:30" and on["note"] == ""
        assert "-At '08:00'" in registered()[-1]
        assert store.get("ana")["schedule"]["enabled"] is True

        # A second profile shares the one task; the earlier ready-by time wins.
        client.put("/api/profiles/ben/schedule", json={"enabled": True, "ready_by": "10:00"})
        assert "-At '08:00'" in registered()[-1] and len(registered()) == 2

        # Off for both: the task goes away.
        client.put("/api/profiles/ana/schedule", json={"enabled": False})
        client.put("/api/profiles/ben/schedule", json={"enabled": False})
        assert "Unregister-ScheduledTask" in no_task_scheduler[-2] and "'Career Morning Jobs'" in no_task_scheduler[-2]

        assert client.put("/api/profiles/ana/schedule", json={"enabled": True, "ready_by": "25:00"}).status_code == 422


def run_morning(store, capsys, *argv):
    from backend.scripts import morning_cli

    for profile in store.list():
        # A built profile has its synthetic profile and evidence files.
        root = store.root_for(profile["id"])
        if not (root / "data/config/profile.yml").exists():
            (root / "data/config").mkdir(parents=True, exist_ok=True)
            (root / "data/context").mkdir(parents=True, exist_ok=True)
            (root / "data/config/profile.yml").write_text(
                "country_pack: ie\ntarget_markets: [ie]\ncandidate:\n  full_name: " + profile["name"] + "\n",
                encoding="utf-8")
            (root / "data/context/evidence.yml").write_text("claims: []\nprojects: []\n", encoding="utf-8")
    code = morning_cli.main(list(argv), profiles=store)
    return code, json.loads(capsys.readouterr().out)


def test_career_morning_sets_the_same_switch_and_the_hunt_count_from_chat(tmp_path, monkeypatch, capsys,
                                                                          no_task_scheduler):
    monkeypatch.setattr(schedule_tasks, "supported", lambda: True)
    store = two_ready_profiles(tmp_path)

    code, shown = run_morning(store, capsys, "--profile", "ana")
    assert code == 0 and shown["morning_jobs"]["enabled"] is False and shown["jobs_per_morning"] == 10
    assert not [s for s in no_task_scheduler if "Register-ScheduledTask" in s]

    code, on = run_morning(store, capsys, "--profile", "ana", "--on", "--ready-by", "8:00", "--jobs", "3")
    assert code == 0 and on["morning_jobs"]["enabled"] and on["morning_jobs"]["ready_by"] == "08:00"
    assert on["morning_jobs"]["night_start"] == "00:30" and on["jobs_per_morning"] == 3
    assert "-At '08:00'" in [s for s in no_task_scheduler if "Register-ScheduledTask" in s][-1]
    assert store.get("ana")["schedule"] == {"enabled": True, "ready_by": "08:00", "note": ""}

    # The count is the overnight hunt's own saved setting, so the dashboard shows the same number.
    from career import Workspace
    from backend.services.workspace_v2 import CareerServices

    saved = CareerServices(Workspace(store.root_for("ana"))).pref("hunt_preferences")
    assert saved["target"] == 3

    # A new time alone keeps the switch on; --off removes the PC's task when no profile wants it.
    code, later = run_morning(store, capsys, "--profile", "ana", "--ready-by", "07:30")
    assert later["morning_jobs"]["enabled"] and later["morning_jobs"]["ready_by"] == "07:30"
    code, off = run_morning(store, capsys, "--profile", "ana", "--off")
    assert code == 0 and off["morning_jobs"]["enabled"] is False
    assert "Unregister-ScheduledTask" in no_task_scheduler[-2]
    assert store.get("ben").get("schedule") is None


def test_career_morning_refuses_an_unbuilt_or_unknown_profile_and_bad_values(tmp_path, monkeypatch, capsys,
                                                                             no_task_scheduler):
    import pytest

    store = two_ready_profiles(tmp_path)
    store.update("ben", state="onboarding")
    code, refused = run_morning(store, capsys, "--profile", "ben", "--on")
    assert code == 2 and "Build this profile first" in refused["error"]
    code, unknown = run_morning(store, capsys, "--profile", "nobody")
    assert code == 2 and unknown["ok"] is False
    for bad in (["--jobs", "0"], ["--min-fit", "40"], ["--ready-by", "25:00"], ["--on", "--off"]):
        with pytest.raises(SystemExit):
            run_morning(store, capsys, "--profile", "ana", *bad)
    assert not [s for s in no_task_scheduler if "Register-ScheduledTask" in s]
