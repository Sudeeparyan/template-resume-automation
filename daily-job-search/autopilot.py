#!/usr/bin/env python3
"""Morning jobs: a fresh, checked list of jobs every morning, and it mends what it can by itself.

    daily-job-search\\morning-jobs.cmd                   every profile with Morning jobs switched on
    daily-job-search\\morning-jobs.cmd --profile <id>    one profile
    daily-job-search\\morning-jobs.cmd --list-only       only rewrite the list from what is saved now
    daily-job-search\\morning-jobs.cmd --background      start the run and return at once (for AI apps)
    daily-job-search\\morning-jobs.cmd --check           a health check: no AI, no search, nothing changed
    daily-job-search\\morning-jobs.cmd --jobs 5          find 5 new jobs now, each with a tailored resume

(macOS: bash daily-job-search/morning-jobs.command with the same options.)

It can run at any time and as often as you like: Windows Task Scheduler at night and at the
ready-by time (Settings -> This profile -> Morning jobs), an AI app's scheduler (Claude Cowork,
Codex, Kimi; see AUTOPILOT.md) or by hand. It does what fits the moment, for each profile:

* before the ready-by time with nothing run yet: the overnight hunt (services/hunt.py) until
  shortly before then, the night shared fairly between profiles;
* a hunt is running: it follows it to the end;
* this morning's hunt has finished: it only rewrites the list;
* after the ready-by time with nothing run (the PC was off or asleep): a short catch-up hunt.

Then it writes MORNING-JOBS.md and morning-jobs.json in the profile's daily-job-search folder
(dated, and as the latest copy) and an index of every profile's list in
daily-job-search/MORNING-JOBS.md. The list is written even when everything else failed: the new
jobs with their fit, why they fit, the apply link and the tailored resume; the saved jobs still
to apply for; the applications already made (never suggested again); what it fixed by itself;
and, only when a person must act, what for.

What it mends by itself:
* the app is not running, stopped answering or runs older code: it starts or restarts it (on
  another port when 8000 belongs to a different program);
* missing Python packages: installed again;
* the app stopped in the middle of the hunt: the hunt carries on with the jobs already saved;
* a hunt ended by an unexpected problem: started again with the time left (a few times at most);
* no internet: it waits for the connection to come back;
* every free AI plan reached its usage limit: the hunt waits for the reset instead of failing,
  and never uses a paid AI unless the hunt settings allow it;
* no AI signed in at all: it still reads the job boards and holds the matches for the AI check;
* the PC going to sleep while it works: it keeps it awake until the list is written.

Nothing is ever submitted and no one is contacted.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DAILY_DIR = Path(__file__).resolve().parent
ROOT = DAILY_DIR.parent
APP_ROOT = ROOT / "career-dashboard"
BACKEND = APP_ROOT / "backend"
SCRIPTS = BACKEND / "scripts"
VENV_PY = BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
LOG_DIR = DAILY_DIR / "logs"
INDEX = DAILY_DIR / "MORNING-JOBS.md"
LOCK = LOG_DIR / "autopilot.lock"
DEFAULT_PORT = 8000
READY_BY = "09:00"
# The hunt ends this long before the ready-by time, so the list is there on time.
READY_MARGIN_MINUTES = 15
# "Give me 5 jobs now" (--jobs without --hours): search at most this long, then prepare each job.
ON_DEMAND_HOURS = 1.5
# After the ready-by time with nothing run yet: a short catch-up hunt of this many hours.
CATCH_UP_HOURS = 1.5
# Until this long after the ready-by time a run still belongs to that morning (a catch-up);
# later in the day a run prepares the next morning.
CATCH_UP_WINDOW_HOURS = 6
# A hunt started up to this long before the ready-by time belongs to that morning (one run in
# the afternoon before does not stand in for the night's).
MORNING_WINDOW_HOURS = 12
MAX_HUNT_HOURS = 10.0
MIN_HUNT_MINUTES = 20
# A hunt that ends with an unexpected problem is started again at most this many times per run.
MAX_RESTARTS = 3
RESTART_PAUSE_SECONDS = 120
POLL_SECONDS = 30
# Saved in the last this many days and not applied for yet: listed as still to apply.
TO_APPLY_DAYS = 21
# No internet: check again this often.
OFFLINE_CHECK_SECONDS = 180
# A lock older than this is from a run that died without cleaning up.
LOCK_MAX_HOURS = 14
APPLIED = ("applied", "interview", "offer", "rejected", "withdrawn", "ghosted")
STATUS_WORDS = {"applied": "Applied", "interview": "Interview", "offer": "Offer", "rejected": "Rejected",
                "withdrawn": "Withdrawn", "ghosted": "No reply"}
# Problems only the person can solve: the run says so plainly and does not retry them.
NEEDS_PERSON = re.compile(r"work authori|citizenship|sponsorship will be needed|no target roles|nothing to search"
                          r"|agent is paused|Build this profile|still being set up", re.IGNORECASE)
# Another search holds the profile: wait for it, then carry on.
BUSY = re.compile(r"Daily Search is running|job search started elsewhere", re.IGNORECASE)
ALREADY = re.compile(r"hunt is already running", re.IGNORECASE)
ONLINE_PROBES = ("https://boards-api.greenhouse.io/v1/boards/stripe", "https://www.gradireland.com/")


# ----- the log and the journal ---------------------------------------------------------------

def log(message: str) -> None:
    line = time.strftime("%H:%M:%S") + "  " + message
    if sys.stdout is not None:  # pythonw (the scheduled task) has no console
        try:
            print(line, flush=True)
        except (OSError, ValueError, UnicodeError):
            pass
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / f"autopilot-{time.strftime('%Y-%m-%d')}.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


@dataclass
class Journal:
    """What happened, in plain words: fixed by itself, needs the person, or worth knowing."""

    entries: list = field(default_factory=list)

    def add(self, kind: str, text: str, profile: str | None = None) -> None:
        self.entries.append({"kind": kind, "text": text, "profile": profile, "at": time.strftime("%H:%M")})
        log({"fixed": "Fixed by itself: ", "needs_you": "Needs you: "}.get(kind, "") + text)

    def fixed(self, text, profile=None):
        self.add("fixed", text, profile)

    def needs_you(self, text, profile=None):
        self.add("needs_you", text, profile)

    def note(self, text, profile=None):
        self.add("note", text, profile)

    def of(self, kind: str, profile: str) -> list[str]:
        seen, out = set(), []
        for entry in self.entries:
            if entry["kind"] == kind and entry["profile"] in (None, profile) and entry["text"] not in seen:
                seen.add(entry["text"])
                out.append(f"{entry['at']} {entry['text']}")
        return out


# ----- one run at a time, and the PC stays awake -----------------------------------------------

def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Lock:
    """One working run on this PC; a second one only rewrites the list."""

    def __init__(self, path: Path | None = None):
        self.path, self.held = path or LOCK, False

    def acquire(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if (isinstance(data, dict) and _alive(int(data.get("pid") or 0))
                and time.time() - float(data.get("started") or 0) < LOCK_MAX_HOURS * 3600):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8")
        self.held = True
        return True

    def release(self) -> None:
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self.held = False


class KeepAwake:
    """Stops the PC from sleeping while the run works (Windows and macOS); best effort."""

    def __enter__(self):
        self.process = None
        try:
            if os.name == "nt":
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # CONTINUOUS | SYSTEM_REQUIRED
            elif sys.platform == "darwin":
                self.process = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except Exception:  # noqa: BLE001 - a PC that sleeps anyway is not a reason to stop
            pass
        return self

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            elif self.process:
                self.process.terminate()
        except Exception:  # noqa: BLE001
            pass
        return False


# ----- the app: start it, restart it, talk to it ------------------------------------------------

class ApiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class Server:
    """The dashboard server this run talks to, started or restarted whenever it needs to be."""

    def __init__(self, journal: Journal, base_url: str | None = None, *, sleep=time.sleep):
        self.journal, self.sleep = journal, sleep
        self.fixed = bool(base_url)  # a test copy named by --base-url is never started or moved
        self.base = (base_url or f"http://127.0.0.1:{DEFAULT_PORT}").rstrip("/")

    def health(self, base: str | None = None, timeout: float = 5) -> dict | None:
        try:
            with urllib.request.urlopen((base or self.base) + "/api/health", timeout=timeout) as response:
                document = json.load(response)
        except (OSError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    @staticmethod
    def ours(document: dict | None) -> bool:
        from backend.paths import APP_ID

        if not document or document.get("app") != APP_ID:
            return False
        try:
            return Path(str(document.get("root"))).resolve() == APP_ROOT.resolve()
        except OSError:
            return False

    def port(self) -> int:
        return int(self.base.rsplit(":", 1)[-1])

    def ensure(self) -> str:
        """'ok', 'started', 'restarted' or 'failed': the app answers at self.base afterwards unless failed."""
        document = self.health()
        if self.ours(document):
            if not self.fixed and not document.get("busy") and self._stale(document):
                log("The app runs older code than is on disk; restarting it.")
                if self._start(replacing=document.get("started_at")):
                    self.journal.note("The app was restarted to run the newest version.")
                    return "restarted"
            return "ok"
        if self.fixed:
            for _ in range(20):  # a test copy may be restarting; never start one here
                self.sleep(3)
                if self.ours(self.health()):
                    return "ok"
            return "failed"
        if document:
            return self._move_port(f"Port {self.port()} belongs to another program")
        if self._port_taken(self.port()):
            # Something holds the port without answering: starting up, or stuck.
            for _ in range(30):
                self.sleep(3)
                if self.ours(self.health()):
                    return "ok"
            if self._stop_stuck(self.port()):
                if self._start():
                    self.journal.fixed("The app had stopped answering, so it was restarted.")
                    return "restarted"
                return "failed"
            return self._move_port(f"Port {self.port()} is held by a program that does not answer")
        log("The app is not running; starting it (no browser tab).")
        return "started" if self._start() else "failed"

    def _move_port(self, why: str) -> str:
        for port in range(DEFAULT_PORT + 1, DEFAULT_PORT + 11):
            base = f"http://127.0.0.1:{port}"
            document = self.health(base)
            if self.ours(document):
                self.base = base
                return "ok"
            if document is None and not self._port_taken(port):
                self.base = base
                if self._start():
                    self.journal.fixed(f"{why}, so the app was started on port {port} for this run.")
                    return "started"
                return "failed"
        self.journal.needs_you(f"{why}, and ports {DEFAULT_PORT + 1}-{DEFAULT_PORT + 10} are taken too. "
                               "Close the other program or restart the PC.")
        return "failed"

    @staticmethod
    def _port_taken(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            return probe.connect_ex(("127.0.0.1", port)) == 0

    @staticmethod
    def _stale(document: dict) -> bool:
        started = document.get("started_at")
        if not isinstance(started, (int, float)):
            return False
        try:
            sys.path.insert(0, str(BACKEND))
            from run import newest_source_time

            return newest_source_time() > started
        except Exception:  # noqa: BLE001 - when in doubt, keep the running app
            return False

    def _start(self, replacing=None) -> bool:
        """Start run.py on this port; ``replacing`` is the started_at of an older copy it replaces."""
        python = VENV_PY if VENV_PY.exists() else Path(sys.executable)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        server_log = (LOG_DIR / f"app-{time.strftime('%Y-%m-%d')}.log").open("a", encoding="utf-8")
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen([str(python), "run.py", "--no-browser", "--port", str(self.port())], cwd=str(BACKEND),
                             stdout=server_log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=flags, close_fds=True)
        except OSError as error:
            log(f"Could not start the app: {error}")
            return False
        deadline = time.monotonic() + 300  # the first start can rebuild the page
        while time.monotonic() < deadline:
            self.sleep(3)
            document = self.health()
            if self.ours(document) and (replacing is None or document.get("started_at") != replacing):
                log("The app is up on " + self.base)
                return True
        log("The app did not answer within 5 minutes of starting; see " + server_log.name)
        return False

    @staticmethod
    def _stop_stuck(port: int) -> bool:
        """Stop the program on the port only when it is this app's own server."""
        try:
            if os.name == "nt":
                out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=30).stdout
                pids = [int(p[4]) for p in (line.split() for line in out.splitlines())
                        if len(p) >= 5 and p[0] == "TCP" and p[1].endswith(f":{port}") and p[3] == "LISTENING"]
                if not pids:
                    return False
                command = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                     f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pids[0]}').CommandLine"],
                    capture_output=True, text=True, timeout=60).stdout
            else:
                out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                                     capture_output=True, text=True, timeout=30).stdout.split()
                pids = [int(p) for p in out if p.isdigit()]
                if not pids:
                    return False
                command = subprocess.run(["ps", "-o", "command=", "-p", str(pids[0])],
                                         capture_output=True, text=True, timeout=30).stdout
            if "run.py" not in command and "uvicorn" not in command or str(BACKEND).casefold() not in command.casefold():
                return False
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pids[0]), "/T", "/F"], capture_output=True, timeout=60)
            else:
                os.kill(pids[0], 15)
            time.sleep(3)
            return not Server._port_taken(port)
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def api(self, method: str, path: str, body=None, timeout: float = 60):
        """One API call; an app that does not answer is started again and the call retried."""
        for attempt in range(1, 4):
            request = urllib.request.Request(self.base + path, method=method)
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, data=data, timeout=timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                raw = error.read().decode(errors="replace")
                try:
                    detail = json.loads(raw).get("detail")
                except (ValueError, AttributeError):
                    detail = None
                message = detail if isinstance(detail, str) else (raw[:500] or f"HTTP {error.code}")
                if error.code >= 500 and attempt < 3:
                    log(f"{method} {path}: the app answered {error.code}; trying again.")
                    self.sleep(10 * attempt)
                    continue
                raise ApiError(message, error.code) from None
            except (OSError, ValueError) as error:
                log(f"{method} {path}: no answer ({error}).")
                if attempt == 3:
                    raise ApiError(f"The app did not answer: {error}") from None
                state = self.ensure()
                if state in ("started", "restarted"):
                    self.journal.fixed("The app stopped answering during the run, so it was started again.")
                elif state == "failed":
                    self.sleep(30)
        raise ApiError("The app did not answer.")


# ----- internet and Python packages ------------------------------------------------------------------

def online(probes=ONLINE_PROBES, timeout: float = 10) -> bool:
    for url in probes:
        try:
            request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "CareerWorkspace/1.0"})
            with urllib.request.urlopen(request, timeout=timeout):
                return True
        except urllib.error.HTTPError:
            return True  # any answer means the connection works
        except (OSError, ValueError):
            continue
    return False


def wait_online(journal: Journal, until_epoch: float, *, check=online, sleep=time.sleep) -> bool:
    if check():
        return True
    began = time.strftime("%H:%M")
    log("No internet connection; waiting for it to come back.")
    while time.time() + OFFLINE_CHECK_SECONDS < until_epoch:
        sleep(OFFLINE_CHECK_SECONDS)
        if check():
            journal.fixed(f"There was no internet from {began} to {time.strftime('%H:%M')}; the search waited and then carried on.")
            return True
    journal.needs_you(f"The PC had no internet connection from {began}, so nothing new could be searched. "
                      "The list shows the jobs saved earlier.")
    return False


def ensure_packages(journal: Journal) -> None:
    """The app's Python packages: installed again when one is missing."""
    python = VENV_PY if VENV_PY.exists() else Path(sys.executable)
    probe = [str(python), "-c", "import fastapi, uvicorn, yaml, pydantic, langgraph, langchain_core"]
    try:
        if subprocess.run(probe, capture_output=True, timeout=120).returncode == 0:
            return
        log("Some of the app's Python packages are missing; installing them again.")
        # The backend's list is the one the launcher installs (career-dashboard/requirements.txt points to it).
        result = subprocess.run([str(python), "-m", "pip", "install", "-q", "-r", str(BACKEND / "requirements.txt")],
                                capture_output=True, text=True, timeout=1800)
        if result.returncode == 0 and subprocess.run(probe, capture_output=True, timeout=120).returncode == 0:
            journal.fixed("Missing Python packages were installed again.")
        else:
            journal.needs_you("The app's Python packages could not be installed. Run Start Dashboard once; "
                              "it sets everything up. " + (result.stderr or "")[-300:])
    except (OSError, subprocess.SubprocessError) as error:
        log(f"Could not check the Python packages: {error}")


# ----- which profiles, and what to do for each ----------------------------------------------------

def load_store(profiles_dir: str | None = None):
    from backend.profiles import ProfileStore, store

    if profiles_dir:
        base = Path(profiles_dir).resolve()
        return ProfileStore(base=base, legacy_root=base / ".no-legacy")
    return store()


def choose(store, only: str | None, journal: Journal) -> list[dict]:
    ready = [p for p in store.list() if p.get("state") == "ready"]
    if only:
        chosen = [p for p in ready if p["id"] == only]
        if not chosen:
            journal.needs_you(f"There is no ready profile called '{only}' on this PC. Check the name in the profile menu.")
        return chosen
    enabled = [p for p in ready if (p.get("schedule") or {}).get("enabled")]
    if enabled:
        return enabled
    if not ready:
        journal.needs_you("There is no profile yet. Open Start Dashboard, add your resume and build your profile.")
        return []
    last = store.last_used()
    fallback = next((p for p in ready if p["id"] == last), ready[0])
    journal.note(f"Morning jobs are not switched on for any profile yet, so this ran for {fallback['name']} "
                 "(the profile opened last). Switch it on in Settings -> This profile.")
    return [fallback]


def ready_by_for(profiles: list[dict], override: str | None = None) -> str:
    times = sorted((p.get("schedule") or {}).get("ready_by") or READY_BY for p in profiles)
    value = override or (times[0] if times else READY_BY)
    datetime.strptime(value, "%H:%M")
    return value


def local_now() -> datetime:
    return datetime.now().astimezone()


def morning_of(now: datetime, ready_by: str) -> tuple[datetime, bool]:
    """(the ready-by moment this run works for, whether it is a catch-up because that moment passed)."""
    hour, minute = (int(part) for part in ready_by.split(":"))
    today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < today:
        return today, False
    if now < today + timedelta(hours=CATCH_UP_WINDOW_HOURS):
        return today, True
    return today + timedelta(days=1), False


def _when(stamp) -> datetime | None:
    if not stamp:
        return None
    try:
        value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def plan(now: datetime, ready_by: str, last: dict | None, *, share: int = 1, hours: float | None = None,
         on_demand: bool = False) -> dict:
    """What to do for one profile now: {"action": "start" | "done", "hours", "why"}.

    ``on_demand`` is a person asking for jobs now (``--jobs``): it searches even when this
    morning's hunt has already finished."""
    morning, catch_up = morning_of(now, ready_by)
    window_start = morning - timedelta(hours=MORNING_WINDOW_HOURS)
    created = _when((last or {}).get("created_at"))
    if on_demand and hours:
        return {"action": "start", "hours": round(min(MAX_HUNT_HOURS, hours), 2), "why": "the jobs you asked for now"}
    if last and created and created >= window_start and last.get("state") in ("completed", "stopped"):
        why = ("you stopped this morning's hunt, so it is not started again" if last["state"] == "stopped"
               else "this morning's hunt has finished")
        return {"action": "done", "hours": 0, "why": why}
    if hours:
        return {"action": "start", "hours": round(min(MAX_HUNT_HOURS, hours), 2), "why": "the hours you asked for"}
    if catch_up:
        return {"action": "start", "hours": CATCH_UP_HOURS,
                "why": f"catch-up: nothing ran before {ready_by} (the PC was off or asleep)"}
    left = (morning - timedelta(minutes=READY_MARGIN_MINUTES) - now).total_seconds() / 3600
    share_hours = min(MAX_HUNT_HOURS, left / max(1, share))
    if share_hours * 60 < MIN_HUNT_MINUTES:
        return {"action": "done", "hours": 0, "why": f"too little time left before {ready_by}"}
    return {"action": "start", "hours": round(share_hours, 2),
            "why": f"the overnight hunt, ending before {ready_by}"}


@dataclass
class Context:
    journal: Journal
    server: Server
    ready_by: str
    hours: float | None = None
    jobs: int | None = None  # --jobs: how many new jobs to find now (the hunt's target)
    sleep: object = time.sleep
    clock: object = time.time
    now: object = local_now


def hunt_profile(ctx: Context, profile: dict, share: int) -> None:
    """Run (or follow) this morning's hunt for one profile until it ends, mending what stops it."""
    pid, journal = profile["id"], ctx.journal

    def api(method, path, body=None):
        return ctx.server.api(method, f"/p/{pid}/api/v2{path}", body)

    restarts = waits = 0
    while True:
        status = api("GET", "/hunt/status")
        run = status.get("current")
        if not run:
            decision = plan(ctx.now(), ctx.ready_by, status.get("last"), share=share,
                            hours=ctx.hours if not restarts or ctx.jobs else None, on_demand=bool(ctx.jobs))
            log(f"{profile['name']}: {decision['why']}" + (f" ({decision['hours']} h)" if decision["hours"] else ""))
            if decision["action"] == "done":
                return
            try:
                run = api("POST", "/hunt/run", {"hours": decision["hours"], **({"target": ctx.jobs} if ctx.jobs else {})})
            except ApiError as error:
                text = str(error)
                if NEEDS_PERSON.search(text):
                    journal.needs_you(text, pid)
                    return
                if ALREADY.search(text):
                    continue
                if BUSY.search(text) and waits < 3:
                    waits += 1
                    log(f"{profile['name']}: {text} Waiting for it to finish.")
                    wait_idle(ctx, api)
                    continue
                restarts += 1
                if restarts > MAX_RESTARTS:
                    journal.note(f"The hunt could not start ({text}); the next run tries again.", pid)
                    return
                log(f"{profile['name']}: the hunt could not start ({text}); trying again shortly.")
                ctx.sleep(RESTART_PAUSE_SECONDS)
                continue
            config = run.get("config") or {}
            log(f"{profile['name']}: hunt started, {config.get('target')} job(s) at fit {config.get('min_fit')}+ "
                f"within {config.get('hours')} h ({config.get('sources')}).")
            if config.get("no_ai"):
                journal.needs_you("No AI app was ready, so the search read the job boards and employer feeds only "
                                  "and holds what it found for the AI requirement check. Open Kimi Code, Codex or "
                                  "Claude Code and sign in; the next run finishes the check.", pid)
        else:
            log(f"{profile['name']}: a hunt is already running; following it.")
        final = follow(ctx, api, profile, run)
        if final is None:
            journal.note("The hunt had not finished when this run's time was up; it keeps going in the app.", pid)
            return
        state, error = final.get("state"), final.get("error") or ""
        if state in ("completed", "stopped"):
            log(f"{profile['name']}: hunt {state}, {len((final.get('progress') or {}).get('saved') or [])} job(s) saved.")
            return
        if NEEDS_PERSON.search(error):
            journal.needs_you(error, pid)
            return
        restarts += 1
        if restarts > MAX_RESTARTS:
            journal.note(f"The hunt stopped {restarts} times ({error[:200]}); the next run tries again.", pid)
            return
        journal.fixed(f"The hunt stopped ({error[:160] or state}); it was started again with the time left.", pid)
        ctx.sleep(RESTART_PAUSE_SECONDS)


def wait_idle(ctx: Context, api, minutes: int = 45) -> None:
    """Wait (up to `minutes`) for a Daily Search the person started to finish."""
    until = ctx.clock() + minutes * 60
    while ctx.clock() < until:
        ctx.sleep(60)
        try:
            if not api("GET", "/pipeline/status").get("current"):
                return
        except ApiError:
            return


def follow(ctx: Context, api, profile: dict, run: dict) -> dict | None:
    """Poll the hunt until it ends; a continuation after an app restart is followed too."""
    run_id = run["id"]
    stage, saved, quiet_polls = "", 0, 0
    hard_deadline = ctx.clock() + (float((run.get("config") or {}).get("hours") or 1) + 2) * 3600
    while ctx.clock() < hard_deadline:
        ctx.sleep(POLL_SECONDS)
        try:
            status = api("GET", "/hunt/status")
        except ApiError as error:
            log(f"{profile['name']}: could not read the hunt's progress ({error}).")
            continue
        current, last = status.get("current"), status.get("last") or {}
        if current:
            if current["id"] != run_id:
                if (current.get("progress") or {}).get("resumed_from"):
                    ctx.journal.fixed("The app stopped during the hunt; after it restarted, the hunt carried on by "
                                      "itself with the jobs already saved.", profile["id"])
                run_id = current["id"]
                hard_deadline = ctx.clock() + (float((current.get("config") or {}).get("hours") or 1) + 2) * 3600
            progress = current.get("progress") or {}
            if progress.get("stage") and progress["stage"] != stage:
                stage = progress["stage"]
                log(f"{profile['name']}: {stage}")
            for job in (progress.get("saved") or [])[saved:]:
                log(f"{profile['name']}: saved {job.get('company')} - {job.get('title')} (fit {job.get('fit')})")
            saved = len(progress.get("saved") or [])
            continue
        if last.get("id") == run_id:
            # The app restarted: it marks the hunt interrupted, then carries it on in a new run.
            if last.get("state") == "interrupted" and ((last.get("progress") or {}).get("resumed_into") or quiet_polls < 2):
                quiet_polls += 1
                continue
            return last
        quiet_polls += 1
        if quiet_polls > 6:
            # The hunt is gone from the app (a restart lost it): the caller starts one again.
            return {"state": "lost", "error": "The hunt could not be found after the app restarted."}
    return None


# ----- the morning list ---------------------------------------------------------------------------------

def _shown(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _resume(workspace, job: dict) -> tuple[Path | None, str]:
    """The current, assessed Studio PDF, never a base draft or an old preview.

    Studio's compile and assessment hashes establish which artifact was checked.
    This is still a draft for the person's review, not a release approval.
    """
    import hashlib
    from backend.resume_contract import contract_for

    with workspace.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='studio_drafts'").fetchone():
            return None, "Tailored resume is not prepared yet"
        row = db.execute("SELECT * FROM studio_drafts WHERE job_id=?", (job["id"],)).fetchone()
        if not row:
            return None, "Tailored resume is not prepared yet"
        draft = dict(row)
        scores = db.execute("SELECT source_sha256,pdf_sha256,jd_sha256 FROM resume_scores "
                            "WHERE job_id=? AND revision=?", (job["id"], draft["revision"])).fetchall()
    try:
        output = (workspace.root / "data/output").resolve()
        folder = (workspace.root / draft["folder"]).resolve()
        if not folder.is_relative_to(output):
            return None, "Resume folder needs review"
        preview = json.loads((folder / "preview.json").read_text(encoding="utf-8"))
        source_hash = hashlib.sha256(draft["source"].encode()).hexdigest()
        if preview.get("revision") != draft["revision"] or preview.get("source_sha256") != source_hash:
            return None, "Rebuild the current resume revision in Resume Studio"
        if draft["profile_revision"] != workspace.evidence()["candidate_revision"]:
            return None, "Sync the resume with the current profile evidence"
        pdf = (output / preview["path"] / "resume.pdf").resolve()
        if not pdf.is_relative_to(folder) or not pdf.is_file():
            return None, "Current resume PDF is missing"
        contract = contract_for(workspace.root, job.get("market") or None)
        if preview.get("page_count") != contract.pages or not (
                (preview.get("layout") or {}).get("full_pages") or contract.relaxed_min_words):
            return None, "Fit the resume to the profile's page contract"
        pdf_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
        jd_hash = hashlib.sha256(job["description"].encode()).hexdigest()
        if not any((r["source_sha256"], r["pdf_sha256"], r["jd_sha256"]) ==
                   (source_hash, pdf_hash, jd_hash) for r in scores):
            return None, "Current PDF needs its resume assessment"
        return pdf, ""
    except (OSError, ValueError, KeyError, TypeError):
        return None, "Current resume metadata needs review in Resume Studio"


def _readiness(services, job: dict, discovered: set[str], cat: dict, preferences: dict) -> tuple[Path | None, list[str]]:
    """Use the same final checks as Daily Search; reporting never writes the draft or calls AI."""
    from backend.job_quality import JobQualityService
    from backend.services import fit, readiness
    from backend.services.demo import demo_mode
    from backend.services.resume_studio import ResumeStudio

    class ReadOnlyStudio(ResumeStudio):
        def __init__(self):
            # Existing artifacts already have the Studio schema. A report must
            # neither create a missing schema nor reconcile source files.
            self.s, self.w = services, services.w

        def get(self, job_id):
            return super().get(job_id, write_source=False)

    demo = demo_mode(services)
    reasons = []
    reasons.extend(JobQualityService(services).blockers(job))
    analysis = fit.cached(services, job["id"], cat=cat)
    if analysis and not demo and preferences.get("require_ai_fit", True) and analysis["method"] != "ai":
        reasons.append("Waiting for the AI requirement check")
    pdf, resume_reason = _resume(services.w, job)
    if resume_reason:
        reasons.append(resume_reason)
    if pdf is not None:
        checked = readiness.check(services, ReadOnlyStudio(), job["id"], fit_bar=preferences.get("min_fit", 70))
        if checked["verdict"] != "ready":
            reasons.extend(checked["next"] or [checked["label"]])
    return (pdf if not reasons else None), list(dict.fromkeys(reasons))


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".writing")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _cell(value) -> str:
    return " ".join(str(value or "").split()).replace("|", "/")


def pay_line(job: dict) -> str:
    """Pay as the morning list states it: advertised (the posting's own figure), a market estimate with
    what to confirm, or not stated. Never presents an estimate as the vacancy's pay."""
    from backend.services.opportunities import pay_note

    info = job.get("opportunity") or {}
    pay = info.get("salary") or {}
    if pay.get("kind") == "advertised" and pay.get("annual_min"):
        low, high = pay["annual_min"], pay.get("annual_max")
        currency = pay.get("currency") or "EUR"
        return f"{currency} {low:,.0f}" + (f"–{high:,.0f}" if high and high != low else "") + " a year (advertised in the posting)"
    note = pay_note(job)
    if note:
        return note
    if info.get("floor"):
        return f"Not stated in the posting. Confirm with the recruiter that the base salary is at least EUR {info['floor']:,.0f}."
    return ""


def permit_path_line(job: dict) -> str:
    """The permit-path evidence score (backend/permits/path_score.py), labelled as what it is."""
    path = (job.get("opportunity") or {}).get("permit_path") or {}
    if not isinstance(path.get("score"), (int, float)):
        return ""
    return f"{path['score']:.0f}/100 ({path.get('label') or 'Evidence score, not approval likelihood'})"


def collect(store, profile: dict, journal: Journal, now: datetime, ready_by: str) -> dict:
    """Everything the morning list shows for one profile, read straight from its database."""
    sys.path[:0] = [p for p in (str(APP_ROOT), str(SCRIPTS)) if p not in sys.path]
    from career import Workspace

    from backend.services import fit, search_memory
    from backend.services.workspace_v2 import CareerServices

    pid = profile["id"]
    root = store.root_for(pid)
    workspace = Workspace(root)
    services = CareerServices(workspace)
    cat = fit.catalogue(services)
    preferences = services.pref("hunt_preferences", {}) or {}
    discovered = set()
    with workspace.connect() as db:
        # Both ordinary discovery and the overnight hunt save through the same gates.
        for row in db.execute("SELECT result FROM agent_runs WHERE kind='discovery' AND state='completed'"):
            try:
                discovered.update(json.loads(row["result"] or "{}").get("added_job_ids") or [])
            except (ValueError, TypeError, AttributeError):
                continue
        search_memory.ensure(db)
        discovered.update(row[0] for row in db.execute(
            "SELECT j.id FROM jobs j JOIN search_memory m ON m.url=j.url WHERE m.outcome='saved'"))
    morning, _ = morning_of(now, ready_by)
    since = morning - timedelta(hours=MORNING_WINDOW_HOURS)
    if since > now:  # in the evening the next morning's night has not begun: list the latest morning
        since -= timedelta(days=1)
    oldest = now - timedelta(days=TO_APPLY_DAYS)
    active = workspace.jobs()
    everything = workspace.jobs(include_deleted=True)

    def brief(job):
        resume, pending = _readiness(services, job, discovered, cat, preferences)
        evidence = job.get("sponsor_evidence") or {}
        record = evidence.get("permit_record") or {}
        return {"id": job["id"], "company": job["company"], "title": job["title"], "location": job.get("location") or "",
                "fit": job.get("fit_score"), "why": " ".join(str(job.get("fit_rationale") or "").split())[:400],
                "url": job.get("url") or "", "status": job["status"], "saved_at": job.get("created_at"),
                "resume": _shown(resume) if resume else None, "folder": job.get("folder") or None,
                "work_permit": evidence.get("sentence") or evidence.get("label") or "",
                "permit_quote": evidence.get("sentence") or "",
                "dete": evidence.get("label") or "" if record.get("found") else "",
                "pay": pay_line(job), "permit_path": permit_path_line(job),
                "pending_reasons": pending, "review_required": True}

    def by_fit(job):
        return -(job["fit"] if isinstance(job["fit"], (int, float)) else -1)

    candidates = [brief(j) for j in active if j["status"] in ("saved", "prepared")
                  and (_when(j.get("created_at")) or oldest) >= oldest]
    pending = [j for j in candidates if j["pending_reasons"]]
    fresh = [j for j in candidates if not j["pending_reasons"] and (_when(j["saved_at"]) or since) >= since]
    to_apply = [j for j in candidates if not j["pending_reasons"] and (_when(j["saved_at"]) or oldest) < since]
    counts = Counter(j["status"] for j in everything if j["status"] in APPLIED)
    held, held_top, hunts = 0, [], []
    with workspace.connect() as db:
        held = search_memory.held_count(db)
        held_top = [{"company": p.get("company"), "title": p.get("title"), "url": p.get("url")}
                    for p in search_memory.held(db, limit=5)]
        try:
            rows = db.execute("SELECT state, config, progress, error, created_at, finished_at FROM hunt_runs "
                              "WHERE created_at >= ? ORDER BY created_at",
                              (since.astimezone(timezone.utc).isoformat(timespec="seconds"),)).fetchall()
        except Exception:  # noqa: BLE001 - no hunt has ever run in this profile
            rows = []
    for row in rows:
        progress = json.loads(row["progress"] or "{}")
        passes = progress.get("passes") or []
        hunts.append({
            "state": row["state"], "error": row["error"], "started": row["created_at"], "finished": row["finished_at"],
            "saved": len(progress.get("saved") or []), "target": (json.loads(row["config"] or "{}")).get("target"),
            "searches": sum(1 for p in passes if p.get("state") == "done"),
            "looked": sum(int(p.get("looked") or 0) for p in passes),
            "turned_away": sum(int(p.get("turned_away") or 0) for p in passes),
            "report": progress.get("report"), "no_ai": bool(json.loads(row["config"] or "{}").get("no_ai")),
            "resumed": bool(progress.get("resumed_from")),
        })
    tracker_alerts = []
    try:
        from backend.market import tracker
        from backend.market.store import MarketStore

        tracker_alerts = [alert for alert in tracker.alerts(services, MarketStore()) if alert["new"]]
    except Exception:  # noqa: BLE001 - the Tracker is a convenience; the morning list is written regardless
        tracker_alerts = []
    try:
        from backend.permits.timeline import timeline

        permit_dates = timeline(workspace.profile(), on=now.date())
    except Exception:  # noqa: BLE001 - dated facts are a convenience; the list is written regardless
        permit_dates = None
    asks = journal.of("needs_you", pid)
    try:
        from backend.services import needs_you

        # The same list as the Dashboard's; this runner reports AI readiness itself.
        asks += [item["text"] for item in needs_you.items(services) if item["id"] != "ai_setup" and item["text"] not in asks]
    except Exception:  # noqa: BLE001 - the list is written regardless
        pass
    return {
        "date": now.date().isoformat(), "updated": now.isoformat(timespec="minutes"),
        "profile": {"id": pid, "name": profile["name"]},
        "new": sorted(fresh, key=by_fit), "to_apply": sorted(to_apply, key=by_fit)[:15],
        "pending": sorted(pending, key=by_fit),
        "to_apply_total": len(to_apply), "applications": {s: counts.get(s, 0) for s in APPLIED},
        "held": held, "held_top": held_top, "hunts": hunts, "tracker_alerts": tracker_alerts, "permit_dates": permit_dates,
        "fixed": journal.of("fixed", pid), "needs_you": asks, "notes": journal.of("note", pid),
        "daily_dir": workspace.daily_dir, "root": root,
    }


def permit_dates_lines(found: dict | None) -> list[str]:
    """Dated facts from the person's confirmed details and the published rules (permits/timeline.py)."""
    if not found or not found.get("events"):
        return []
    lines = ["## Your permit dates", ""]
    for event in found["events"]:
        if event["id"] == "gep_lead_time" and not event.get("date"):
            line = f"- {event['label']}: {event['weeks']} weeks before a job's start date"
        elif event.get("date"):
            left = event.get("days_left")
            line = f"- {event['label']}: {event['date']}" + (f" ({left} days left)" if isinstance(left, int) and left >= 0 else "")
        else:
            line = f"- {event['label']}: {event['note']}"
        lines.append(line + (f" · [source]({event['url']})" if event.get("url") else ""))
    return lines + ["", "Dates from what you confirmed and the published rules. Not immigration advice.", ""]


def render(data: dict, base_url: str) -> str:
    now = datetime.fromisoformat(data["updated"])
    new, to_apply = data["new"], data["to_apply"]
    applied = sum(data["applications"].values())
    lines = [f"# Morning jobs — {now.strftime('%A %d %B %Y').replace(' 0', ' ')}", "",
             f"**{data['profile']['name']}** · {len(new)} new · {data['to_apply_total']} still to apply · "
             f"{applied} application(s) tracked, never suggested again · updated {now.strftime('%H:%M')}", ""]
    if data["needs_you"]:
        lines += ["## Needs you", ""] + [f"- {text}" for text in data["needs_you"]] + [""]
    lines += permit_dates_lines(data.get("permit_dates"))
    lines += ["## New this morning", ""]
    if not new:
        lines.append("No new job has both current checks and a current tailored PDF ready for your review. "
                     "See pending items and the search notes below for unfinished work or problems.")
        lines.append("")
    for number, job in enumerate(new, 1):
        fit = f"fit {job['fit']}/100" if job["fit"] is not None else "fit not scored yet"
        lines.append(f"{number}. **{job['company']} — {job['title']}** · {job['location']} · {fit}")
        if job["why"]:
            lines.append(f"   - Why: {job['why']}")
        if job.get("pay"):
            lines.append(f"   - Pay: {job['pay']}")
        if job.get("permit_quote"):
            lines.append(f"   - The posting says: “{job['permit_quote']}”")
        if job.get("dete"):
            lines.append(f"   - Permit record: {job['dete']}")
        if job.get("permit_path"):
            lines.append(f"   - Permit-path evidence: {job['permit_path']}")
        elif job["work_permit"] and not job.get("permit_quote"):
            lines.append(f"   - Work permit: {job['work_permit']}")
        if job["url"]:
            lines.append(f"   - Apply: {job['url']}")
        lines.append(f"   - Tailored resume (review before applying): `{job['resume']}`")
    if new:
        lines.append("")
    if data.get("pending"):
        lines += [f"## Pending checks or resume ({len(data['pending'])})", "",
                  "These saved jobs are not included in the ready count.", ""]
        for job in data["pending"]:
            lines.append(f"- **{job['company']} — {job['title']}**: " + "; ".join(job["pending_reasons"]))
        lines.append("")
    if to_apply:
        lines += ["## Still to apply", "", "| Fit | Company | Role | Saved | Apply |", "|---|---|---|---|---|"]
        for job in to_apply:
            saved = (_when(job["saved_at"]) or now).astimezone().strftime("%d %b")
            link = f"[posting]({job['url']})" if job["url"] else ""
            lines.append(f"| {job['fit'] if job['fit'] is not None else '–'} | {_cell(job['company'])} | "
                         f"{_cell(job['title'])} | {saved} | {link} |")
        if data["to_apply_total"] > len(to_apply):
            lines.append(f"\n…and {data['to_apply_total'] - len(to_apply)} more in the dashboard.")
        lines.append("")
    if data.get("tracker_alerts"):
        lines += ["## Tracker alerts", ""]
        for alert in data["tracker_alerts"]:
            examples = "; ".join(f"{e['title']} at {e['company']}" for e in alert["examples"])
            lines.append(f"- **{alert['name']}**: {alert['new']} new" + (f" (for example {examples})" if examples else ""))
        lines += ["", "Open the Tracker in the dashboard for each role's permit facts; these are not checked "
                      "against your profile yet.", ""]
    if data["held"]:
        lines += [f"## Waiting for the AI requirement check ({data['held']})", "",
                  "These passed every other check; the next run checks them against your profile first. "
                  "They are not verified yet.", ""]
        lines += [f"- {p['company']} — {p['title']}" + (f" · {p['url']}" if p.get("url") else "") for p in data["held_top"]]
        lines.append("")
    if applied:
        shown = " · ".join(f"{STATUS_WORDS[s]} {n}" for s, n in data["applications"].items() if n)
        lines += ["## Your applications", "", shown + ". The search never suggests these roles again.", ""]
    lines += ["## How the search went", ""]
    for hunt in data["hunts"]:
        started = (_when(hunt["started"]) or now).astimezone().strftime("%H:%M")
        ended = (_when(hunt["finished"]) or now).astimezone().strftime("%H:%M") if hunt["finished"] else "still running"
        searches = f"{hunt['searches']} search" + ("" if hunt["searches"] == 1 else "es")
        line = (f"- {started}–{ended}: {hunt['state']} · {searches}, looked at {hunt['looked']} "
                f"postings, saved {hunt['saved']} of {hunt['target']}, turned away {hunt['turned_away']}")
        if hunt["resumed"]:
            line += " · carried on after the app restarted"
        if hunt["report"]:
            line += f" · details: `{_shown(data['root'] / hunt['report'])}`"
        lines.append(line)
    if not data["hunts"]:
        lines.append("- No hunt ran for this morning.")
    lines += [f"- Fixed by itself: {text}" for text in data["fixed"]]
    lines += [f"- {text}" for text in data["notes"]]
    lines += ["", "Nothing was submitted and no one was contacted. Review each job and its resume, then apply "
                  "through the posting link.", f"Dashboard: {base_url}/p/{data['profile']['id']}/", ""]
    return "\n".join(lines)


def write_list(store, profile: dict, journal: Journal, now: datetime, ready_by: str, base_url: str) -> dict:
    data = collect(store, profile, journal, now, ready_by)
    daily = Path(data.pop("daily_dir"))
    root = data.pop("root")
    text = render({**data, "root": root}, base_url)
    dated = daily / data["date"]
    payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    _atomic(dated / "MORNING-JOBS.md", text)
    _atomic(dated / "morning-jobs.json", payload)
    latest = dated / "MORNING-JOBS.md"
    if daily.resolve() != DAILY_DIR.resolve():  # an old shared folder keeps only the dated copy
        _atomic(daily / "MORNING-JOBS.md", text)
        _atomic(daily / "morning-jobs.json", payload)
        latest = daily / "MORNING-JOBS.md"
    _track(daily, data)
    log(f"{profile['name']}: morning list written to {_shown(latest)} ({len(data['new'])} new).")
    return {**data, "list": _shown(latest)}


def _track(daily: Path, data: dict) -> None:
    """Every job the mornings brought, once, in the profile's history.csv."""
    history = daily / "history.csv"
    known = set()
    if history.exists():
        with history.open(newline="", encoding="utf-8") as handle:
            known = {row.get("url") for row in csv.DictReader(handle)}
    rows = [job for job in data["new"] if job["url"] and job["url"] not in known]
    if not rows:
        return
    new_file = not history.exists()
    with history.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(["date", "company", "role", "url", "requisition_id", "location", "status", "artifact_dir"])
        for job in rows:
            writer.writerow([data["date"], job["company"], job["title"], job["url"], "", job["location"],
                             job["status"], job["folder"] or ""])


def write_index(summaries: list[dict], journal: Journal, now: datetime) -> None:
    lines = [f"# Morning jobs", "",
             f"Updated {now.strftime('%A %d %B %Y, %H:%M').replace(' 0', ' ')}. Each person's list is in their own "
             "profile folder; open the one below.", ""]
    for summary in summaries:
        name = summary["profile"]["name"]
        if summary.get("error"):
            lines += [f"## {name}", "", f"The list could not be written this time ({summary['error']}). "
                      "The jobs are still in the dashboard.", ""]
            continue
        lines += [f"## {name}", "",
                  f"{len(summary['new'])} new this morning, {summary['to_apply_total']} still to apply: "
                  f"`{summary['list']}`", ""]
        for job in summary["new"][:10]:
            lines.append(f"- {job['company']} — {job['title']}" + (f" (fit {job['fit']})" if job["fit"] is not None else ""))
        lines += [f"- Needs you: {text}" for text in summary["needs_you"]]
        lines.append("")
    general = [e["text"] for e in journal.entries if e["kind"] == "needs_you" and e["profile"] is None]
    if not summaries:
        lines += ["No list was written."] + [f"- Needs you: {text}" for text in general] + [""]
    lines += ["Nothing was submitted and no one was contacted.", ""]
    _atomic(INDEX, "\n".join(lines))


# ----- the health check ----------------------------------------------------------------------------

def check(args, journal: Journal) -> int:
    """A read-only health check: what would stop tomorrow's list, and what the run would fix itself."""
    lines = []

    def say(ok, text):
        lines.append(("OK    " if ok is True else "FIXES " if ok is None else "NEEDS ") + text)

    say(VENV_PY.exists(), "The app's Python environment" + ("" if VENV_PY.exists() else " is missing: run Start Dashboard once"))
    packages = VENV_PY.exists() and subprocess.run([str(VENV_PY), "-c", "import fastapi, uvicorn, yaml, pydantic"],
                                                   capture_output=True, timeout=120).returncode == 0
    say(True if packages else None, "Python packages" + ("" if packages else " are missing: a run installs them again"))
    say(True if online() else None, "Internet" + ("" if online() else ": offline now; a run waits for it"))
    server = Server(journal, args.base_url)
    document = server.health()
    if server.ours(document):
        say(True, f"The app is running on {server.base}")
    elif server.fixed:
        say(False, f"The app does not answer at {server.base}")
    else:
        say(None, "The app: not running; a run starts it")
    try:
        store = load_store(args.profiles_dir)
        profiles = choose(store, args.profile, journal)
    except Exception as error:  # noqa: BLE001
        say(False, f"Profiles could not be read: {error}")
        profiles = []
    for profile in profiles:
        root = store.root_for(profile["id"])
        try:
            from backend.countries import require_known_authorization

            require_known_authorization(root)
            say(True, f"{profile['name']}: work authorization is set")
        except Exception as error:  # noqa: BLE001
            say(False, f"{profile['name']}: {error}")
        try:
            from backend.services.search_plan import plan_for

            roles = plan_for(root).get("roles") or []
            say(bool(roles), f"{profile['name']}: " + (f"searches for {', '.join(roles[:4])}" if roles
                                                       else "no target roles; add them in Profile or tell the Assistant"))
        except Exception as error:  # noqa: BLE001
            say(False, f"{profile['name']}: the search plan could not be read ({error})")
        if server.ours(document):
            try:
                overview = server.api("GET", f"/p/{profile['id']}/api/v2/hunt")
                ai = overview.get("ai") or {}
                say(True if ai.get("ready") else None, f"{profile['name']}: AI " + (
                    "is ready" if ai.get("ready") else f"is not free now ({ai.get('note') or 'none set up'}); the hunt waits or reads boards only"))
                prefs = overview.get("defaults") or {}
                say(True, f"{profile['name']}: {prefs.get('target')} jobs at fit {prefs.get('min_fit')}+ each morning "
                          f"(change it in Daily Search -> Overnight hunt)")
            except ApiError as error:
                say(None, f"{profile['name']}: could not ask the app ({error})")
    try:
        from backend.services import schedule_tasks

        task = schedule_tasks.morning_status()
        if task.get("supported") is False:
            say(None, "Scheduled task: not available here; use an AI app's scheduler (AUTOPILOT.md)")
        else:
            say(bool(task.get("exists")), "Scheduled task: " + (
                f"on, next run {str(task.get('next_run') or '?').replace('T', ' ')}" if task.get("exists")
                else "off; switch on Morning jobs in Settings -> This profile"))
    except Exception as error:  # noqa: BLE001
        say(None, f"Scheduled task: could not check ({error})")
    for entry in journal.entries:
        if entry["kind"] == "needs_you":
            say(False, entry["text"])
    for line in lines:
        log(line)
    return 0


# ----- the run -------------------------------------------------------------------------------------

def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", help="Only this profile ID")
    parser.add_argument("--ready-by", help="When the list must be ready, HH:MM (default: the profile's setting, else 09:00)")
    parser.add_argument("--hours", type=float, help="Hunt for this many hours now instead of until the ready-by time")
    parser.add_argument("--jobs", type=int, help=f"Find this many new jobs now, each with a tailored resume (1-40; "
                                                 f"searches up to {ON_DEMAND_HOURS:g} h unless --hours says otherwise)")
    parser.add_argument("--list-only", action="store_true", help="Only rewrite the list from what is saved now")
    parser.add_argument("--background", action="store_true", help="Start the run in the background and return at once")
    parser.add_argument("--check", action="store_true", help="Health check only: no AI, no search, nothing changed")
    parser.add_argument("--base-url", help="A test copy of the app (never started or moved)")
    parser.add_argument("--profiles-dir", help="A test profiles folder instead of career-dashboard/profiles")
    args = parser.parse_args(argv)
    if args.jobs is not None and not 1 <= args.jobs <= 40:
        parser.error("--jobs must be between 1 and 40")
    return args


def background(argv: list[str]) -> int:
    """Start the same run detached (no window) and return, for schedulers that cannot wait hours."""
    python = VENV_PY.with_name("pythonw.exe") if os.name == "nt" and VENV_PY.with_name("pythonw.exe").exists() else (
        VENV_PY if VENV_PY.exists() else Path(sys.executable))
    rest = [arg for arg in argv if arg != "--background"]
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    subprocess.Popen([str(python), str(Path(__file__).resolve()), *rest], cwd=str(DAILY_DIR), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
                     start_new_session=os.name != "nt")
    log("Morning jobs started in the background. The list will be in daily-job-search/MORNING-JOBS.md; "
        "progress is in daily-job-search/logs/.")
    return 0


def run(args, journal: Journal) -> None:
    ensure_packages(journal)
    store = load_store(args.profiles_dir)
    chosen = choose(store, args.profile, journal)
    ready_by = ready_by_for(chosen, args.ready_by)
    server = Server(journal, args.base_url)
    if chosen and not args.list_only:
        state = server.ensure()
        if state == "failed":
            journal.needs_you("The app could not be started, so nothing new was searched. Open Start Dashboard to "
                              f"see why; details are in {_shown(LOG_DIR)}.")
        else:
            morning, _ = morning_of(local_now(), ready_by)
            wait_online(journal, max(morning.timestamp() - READY_MARGIN_MINUTES * 60, time.time() + 3600))
            hours = args.hours or (ON_DEMAND_HOURS if args.jobs else None)
            ctx = Context(journal=journal, server=server, ready_by=ready_by, hours=hours, jobs=args.jobs)
            for index, profile in enumerate(chosen):
                try:
                    hunt_profile(ctx, profile, share=len(chosen) - index)
                except Exception as error:  # noqa: BLE001 - one profile never stops another's list
                    log(traceback.format_exc())
                    journal.note(f"The search stopped unexpectedly ({error}); the next run tries again.", profile["id"])
    now = local_now()
    summaries = []
    for profile in chosen:
        try:
            summaries.append(write_list(store, profile, journal, now, ready_by, server.base))
        except Exception as error:  # noqa: BLE001 - the index still says what happened
            log(traceback.format_exc())
            summaries.append({"profile": {"id": profile["id"], "name": profile["name"]}, "error": str(error)[:300]})
    write_index(summaries, journal, now)
    log(f"Done: {sum(len(s.get('new') or []) for s in summaries)} new job(s) across {len(summaries)} profile(s). "
        f"Lists: {_shown(INDEX)}")


def main(argv=None) -> int:
    global INDEX, LOCK
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parse(argv)
    if args.profiles_dir:  # a test folder keeps its own index and lock, away from the real ones
        INDEX = Path(args.profiles_dir).resolve() / "MORNING-JOBS.md"
        LOCK = Path(args.profiles_dir).resolve() / "autopilot.lock"
    for path in (str(APP_ROOT), str(SCRIPTS)):
        if path not in sys.path:
            sys.path.insert(0, path)
    from backend import keep_traces_local

    keep_traces_local()
    if args.background:
        return background(argv)
    journal = Journal()
    if args.check:
        return check(args, journal)
    lock = Lock()
    if not args.list_only and not lock.acquire():
        log("Another morning run is already working; this one only rewrites the list from what is saved now.")
        args.list_only = True
    try:
        with KeepAwake():
            log(f"=== Morning jobs ({'list only' if args.list_only else 'search and list'}) ===")
            run(args, journal)
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - never leave a silent scheduled task
        log("Morning jobs stopped unexpectedly:\n" + traceback.format_exc())
        sys.exit(1)
