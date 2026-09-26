"""The opt-in Windows scheduled task that has a list of jobs ready every morning.

One task for the whole PC, "Career Morning Jobs", runs daily-job-search/autopilot.py for
every profile whose Morning jobs switch is on (Settings -> This profile). It starts twice a
day: at night, NIGHT_HOURS before the ready-by time, for the overnight hunt, and at the
ready-by time itself, which only rewrites the list when the night's run finished, or does a
short catch-up when the PC was off. One task, not one per profile, so profiles take turns
with the AI plans instead of competing for them. It wakes the PC, runs on battery, starts
as soon as possible after a missed start and is restarted if it fails.

Tasks are per-user (no administrator rights). Off Windows every call is a no-op that says
so; an AI app's scheduler can run the same command there (daily-job-search/AUTOPILOT.md).
Tests replace `_powershell` so the suite never touches the real Task Scheduler.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

from backend.paths import APP_ROOT, REPO_ROOT

MORNING_TASK = "Career Morning Jobs"
# Older versions registered one task per profile under this prefix; the morning task replaces them.
OLD_PREFIX = "Career Daily Job Search - "
READY_BY = "09:00"
# The night search starts this long before the ready-by time.
NIGHT_HOURS = 7.5
# The longest a run may take before Task Scheduler stops it.
RUN_LIMIT_HOURS = 12


def supported() -> bool:
    # CAREER_NO_SCHEDULED_TASKS=1 keeps a disposable copy of the app (a test run) from
    # registering real tasks that would point at a temporary folder.
    return sys.platform == "win32" and not os.environ.get("CAREER_NO_SCHEDULED_TASKS")


def unsupported_note() -> str:
    if sys.platform != "win32":
        return ("Scheduled tasks are set up on Windows only. On this computer, let an AI app's scheduler "
                "run daily-job-search/morning-jobs.command (see daily-job-search/AUTOPILOT.md).")
    return "Scheduled tasks are switched off for this copy of the app (CAREER_NO_SCHEDULED_TASKS)."


def _quote(value) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _powershell(script: str, timeout: int = 60) -> tuple[int, str]:
    run = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
    )
    return run.returncode, (run.stdout or "") + (run.stderr or "")


def valid_time(value: str) -> str:
    datetime.strptime(value, "%H:%M")
    return value


def night_start(ready_by: str) -> str:
    """When the overnight hunt starts for a list ready at ``ready_by`` (09:00 -> 01:30)."""
    return (datetime.strptime(valid_time(ready_by), "%H:%M") - timedelta(hours=NIGHT_HOURS)).strftime("%H:%M")


def _python() -> str:
    """The app's own Python, windowless so nothing pops up at night."""
    scripts = APP_ROOT / "backend/.venv/Scripts"
    for name in ("pythonw.exe", "python.exe"):
        if (scripts / name).exists():
            return str(scripts / name)
    return sys.executable


def _remove_old_script() -> str:
    return (f"Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {{ $_.TaskName -like "
            f"{_quote(OLD_PREFIX + '*')} }} | Unregister-ScheduledTask -Confirm:$false")


def install_morning(ready_by: str) -> dict:
    """Register (or replace) the morning task for a list ready at ``ready_by`` (HH:MM, local time)."""
    ready_by = valid_time(ready_by)
    night = night_start(ready_by)
    out = {"task": MORNING_TASK, "ready_by": ready_by, "night_start": night}
    if not supported():
        return {**out, "installed": False, "note": unsupported_note()}
    script_path = REPO_ROOT / "daily-job-search/autopilot.py"
    description = (f"Morning jobs: searches overnight from {night} and has a checked list of jobs with tailored "
                   f"resumes ready by {ready_by} in daily-job-search/MORNING-JOBS.md. It fixes what it can by "
                   "itself. Nothing is ever submitted; the candidate reviews and applies.")
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _remove_old_script(),
        f"$action = New-ScheduledTaskAction -Execute {_quote(_python())} -Argument {_quote(chr(34) + str(script_path) + chr(34))} "
        f"-WorkingDirectory {_quote(REPO_ROOT / 'daily-job-search')}",
        f"$triggers = @((New-ScheduledTaskTrigger -Daily -At {_quote(night)}), (New-ScheduledTaskTrigger -Daily -At {_quote(ready_by)}))",
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries "
        f"-DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours {RUN_LIMIT_HOURS}) "
        "-MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)",
        f"Register-ScheduledTask -TaskName {_quote(MORNING_TASK)} -Action $action -Trigger $triggers "
        f"-Settings $settings -Description {_quote(description)} -Force | Out-Null",
    ])
    try:
        code, output = _powershell(script)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {**out, "installed": False, "note": f"Could not reach Task Scheduler: {error}"}
    if code:
        return {**out, "installed": False, "note": output.strip()[-400:] or "Task Scheduler refused the task."}
    return {**out, "installed": True, "note": f"Searches from {night}; the list is ready by {ready_by} every day."}


def remove_morning() -> dict:
    """Unregister the morning task (and any older per-profile task); nothing to do when there is none."""
    if not supported():
        return {"removed": False, "note": unsupported_note()}
    script = "\n".join([
        f"Get-ScheduledTask -TaskName {_quote(MORNING_TASK)} -ErrorAction SilentlyContinue | "
        "Unregister-ScheduledTask -Confirm:$false",
        _remove_old_script(),
    ])
    try:
        code, output = _powershell(script)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"removed": False, "note": f"Could not reach Task Scheduler: {error}"}
    return {"removed": code == 0, "note": "" if code == 0 else output.strip()[-400:]}


def remove(profile_id: str) -> dict:
    """An older version's task for this one profile, when it still exists (Reset and Delete)."""
    if not supported():
        return {"removed": False, "note": unsupported_note()}
    script = (f"Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {{ $_.TaskName -like "
              f"{_quote(OLD_PREFIX + '*')} -and $_.TaskName.EndsWith({_quote('(' + profile_id + ')')}) }} | "
              "Unregister-ScheduledTask -Confirm:$false")
    try:
        code, output = _powershell(script)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"removed": False, "note": f"Could not reach Task Scheduler: {error}"}
    return {"removed": code == 0, "note": "" if code == 0 else output.strip()[-400:]}


def morning_status() -> dict:
    """{'exists', 'supported', 'enabled', 'next_run', 'last_run', 'last_result'} for the morning task."""
    if not supported():
        return {"exists": False, "supported": False}
    script = (
        f"Get-ScheduledTask -TaskName {_quote(MORNING_TASK)} -ErrorAction SilentlyContinue | Select-Object -First 1 | "
        "ForEach-Object { $info = $_ | Get-ScheduledTaskInfo; "
        "[pscustomobject]@{ task = $_.TaskName; state = [string]$_.State; "
        "next_run = if ($info.NextRunTime) { $info.NextRunTime.ToString('s') } else { '' }; "
        "last_run = if ($info.LastRunTime -and $info.LastRunTime.Year -gt 2000) { $info.LastRunTime.ToString('s') } else { '' }; "
        "last_result = $info.LastTaskResult } } | ConvertTo-Json -Compress"
    )
    try:
        code, output = _powershell(script)
    except (OSError, subprocess.TimeoutExpired):
        return {"exists": False, "supported": True, "error": "Task Scheduler did not answer"}
    text = output.strip()
    if code or not text.startswith("{"):
        return {"exists": False, "supported": True}
    try:
        found = json.loads(text)
    except json.JSONDecodeError:
        return {"exists": False, "supported": True}
    return {"exists": True, "supported": True, "task": found.get("task"), "enabled": found.get("state") != "Disabled",
            "next_run": found.get("next_run") or None, "last_run": found.get("last_run") or None,
            "last_result": found.get("last_result")}


def enabled_profiles(profiles) -> list[dict]:
    return [p for p in profiles.list() if p.get("state") == "ready" and (p.get("schedule") or {}).get("enabled")]


def sync(profiles) -> dict:
    """Install, update or remove the morning task so it matches the profiles with Morning jobs on."""
    enabled = enabled_profiles(profiles)
    if not enabled:
        return {"installed": False, **remove_morning()}
    ready_by = min((p.get("schedule") or {}).get("ready_by") or READY_BY for p in enabled)
    return install_morning(ready_by)
