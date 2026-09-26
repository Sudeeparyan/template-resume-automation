#!/usr/bin/env python3
"""The opt-in morning run for one profile.

Each profile has its own "Career Daily Job Search" task, which passes --profile
<id>. By hand:

    career-dashboard/backend/.venv/Scripts/python.exe daily-job-search/morning_run.py [--profile <id>]

Each profile's run talks only to that profile's API (/p/<id>/api/...) and writes
its logs, MORNING-REPORT.md and history.csv in that profile's own daily folder.

What it does, in order:
  1. Starts the dashboard server on http://127.0.0.1:8000 if it is not running.
  2. Runs the Daily Search pipeline, the same engine as the Daily Search page, with
     every helper on: tracked career pages first, then the balanced web mix if the
     day is still short. It finds jobs through the sponsorship gate, never-re-apply
     and the requirement check (services/fit.py), then for each job: company
     research, the tailored resume (predicted items stay pending in the Assurance
     tab), the study plan and the contract-compliant PDF. Auto (Settings) picks the AI:
     free plans first, paid Azure last. Saved jobs an earlier run left unprepared
     are picked up too.
  3. Re-checks each resume with validate_resume.py.
  4. Regenerates the dashboard projections and writes MORNING-REPORT.md under
     daily-job-search/<date>/.

Nothing is ever submitted anywhere; the candidate reviews and applies.
"""
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

DAILY_DIR = Path(__file__).resolve().parent
ROOT = DAILY_DIR.parent
BACKEND = ROOT / "career-dashboard" / "backend"
APP_ROOT = ROOT / "career-dashboard"
VENV_PY = BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
SCRIPTS = BACKEND / "scripts"
DEFAULT_URL = "http://127.0.0.1:8000"
POLL_SECONDS = 15
# The helpers every morning job gets, in the Daily Search pipeline's own words.
STEP_NAMES = {"research": "Company research", "tailor": "Tailored resume", "study_plan": "Study plan", "pdf": "Resume PDF"}


def _base_url() -> str:
    """The dashboard to talk to: 8000, or a test copy named by --base-url / CAREER_BASE_URL."""
    if "--base-url" in sys.argv:
        index = sys.argv.index("--base-url")
        if index + 1 < len(sys.argv):
            return sys.argv[index + 1].rstrip("/")
    return (os.environ.get("CAREER_BASE_URL") or DEFAULT_URL).rstrip("/")


BASE_URL = _base_url()

# The scheduled task's console is cp1252; em-dashes and arrows in job text must not crash logging.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path[:0] = [str(APP_ROOT), str(SCRIPTS)]


def _profile_arg() -> str:
    if "--profile" in sys.argv:
        index = sys.argv.index("--profile")
        if index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    from backend.profiles import store

    selected = store().last_used()
    if not selected:
        raise SystemExit("No profile is selected. Create one in the dashboard or pass --profile <id>.")
    return selected


PROFILE = _profile_arg()
try:
    from backend.profiles import store as _profiles

    _entry = _profiles().get(PROFILE)
    WORKSPACE_ROOT = _profiles().root_for(PROFILE)
except Exception as _error:  # an unknown or unreadable profile: nothing to run
    raise SystemExit(f"Profile '{PROFILE}' is not on this PC ({_error}).")
if _entry.get("state") != "ready":
    raise SystemExit(f"Profile '{PROFILE}' is still being set up; its daily search starts once its workspace is built.")
# Imported legacy workspaces retain their historical daily folder.
if not _entry.get("legacy"):
    DAILY_DIR = WORKSPACE_ROOT / "daily-job-search"
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
API_PREFIX = f"/p/{PROFILE}"

from backend.resume_contract import contract_for

CONTRACT = contract_for(WORKSPACE_ROOT)

try:
    import yaml
    from tracking import today as _today

    _zone = ((yaml.safe_load((WORKSPACE_ROOT / "data/config/profile.yml").read_text(encoding="utf-8")) or {})
             .get("candidate") or {}).get("timezone")
    TODAY = _today(_zone or None)
except Exception:  # the run must still work if the app's helpers move
    TODAY = date.today().isoformat()

LOG_DIR = DAILY_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / (TODAY + ".log")
REPORT_DIR = DAILY_DIR / TODAY


def log(message):
    line = time.strftime("%H:%M:%S") + "  " + message
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


class ApiError(Exception):
    pass


def api(method, path, body=None, timeout=30):
    # Every profile's API lives under /p/<id>/api/...; the profile is fixed by the URL.
    request = urllib.request.Request(BASE_URL + API_PREFIX + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, data=data, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:1500]
        raise ApiError(f"{method} {path} -> HTTP {error.code}: {detail}") from None
    except (OSError, ValueError) as error:
        raise ApiError(f"{method} {path} -> {error}") from None


def server_health():
    try:
        with urllib.request.urlopen(BASE_URL + "/api/health", timeout=2) as response:
            document = json.load(response)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def ensure_server():
    document = server_health()
    if document:
        from backend.paths import APP_ID

        if document.get("app") == APP_ID and Path(str(document.get("root", ""))).resolve() == APP_ROOT.resolve():
            log("Dashboard is already running on " + BASE_URL)
            return
        raise SystemExit(
            f"{BASE_URL} is answered by a different app, so the morning run cannot start. "
            "Close whatever is on port 8000 and run Start Dashboard.cmd."
        )
    if BASE_URL != DEFAULT_URL:
        raise SystemExit(f"No dashboard answers at {BASE_URL}; start that copy first.")
    log("Dashboard is not running; starting it in the background (no browser tab).")
    server_log = LOG_FILE.open("a", encoding="utf-8")
    starter = VENV_PY if VENV_PY.exists() else "python"
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(starter), "run.py", "--no-browser"],
        cwd=str(BACKEND),
        stdout=server_log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        close_fds=True,
    )
    deadline = time.monotonic() + 300  # the first start can rebuild the React client
    while time.monotonic() < deadline:
        if server_health():
            log("Dashboard is up on " + BASE_URL)
            return
        time.sleep(3)
    raise SystemExit(
        "The dashboard did not start within 5 minutes. "
        "Open Start Dashboard.cmd and share what it says. Log: " + str(LOG_FILE)
    )


def run_pipeline(source, count, include_unprepared=False, timeout_minutes=150):
    """One Daily Search run, the same engine as the page: find, then research, tailor, study plan, PDF.

    Returns the finished run (its per-job steps are the report). Waits while it runs;
    Auto moves to the next free plan when one reaches its limit, so there is no retry here.
    """
    body = {"count": count, "source": source, "include_unprepared": include_unprepared,
            "steps": {step: True for step in STEP_NAMES}}
    started = api("POST", "/api/v2/pipeline/run", body)
    run_id = started["id"]
    log(f"Started Daily Search ({source}, up to {count} job(s)) run {run_id}")
    deadline = time.monotonic() + timeout_minutes * 60
    stage = ""
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        status = api("GET", "/api/v2/pipeline/status")
        current = status.get("current") or {}
        if current.get("id") == run_id:
            now = (current.get("progress") or {}).get("stage") or ""
            if now and now != stage:
                log("  " + now)
                stage = now
            continue
        last = status.get("last") or {}
        if last.get("id") == run_id:
            return last
    raise ApiError(f"Daily Search did not finish within {timeout_minutes} minutes")


def list_jobs():
    output = subprocess.run(
        [str(VENV_PY), str(SCRIPTS / "career.py"), "--profile", PROFILE, "jobs"],
        capture_output=True, text=True, timeout=120,
    )
    if output.returncode:
        raise ApiError("career.py jobs failed: " + output.stderr[-800:])
    return json.loads(output.stdout)


def validate_resume(folder):
    """Compile + validate; returns (ok, note).

    'ok' means the PDF compiled to the active page contract. The validator also hard-fails
    on the placeholder evidence map that prepare() writes (role eligibility and
    requirement review stay pending until reviewed) and on the pending visual
    review — those are Assurance-tab steps, not morning-run failures.
    """
    tex = folder / "resume.tex"
    if not tex.exists():
        return False, "resume.tex missing"
    command = [
        str(VENV_PY), str(SCRIPTS / "validate_resume.py"), str(tex),
        "--workspace", str(WORKSPACE_ROOT),
        "--compile", "--output", str(folder / "resume.pdf"),
        "--render-dir", str(folder / "resume-preview"),
        "--qa-json", str(folder / "qa.json"),
    ]
    evidence_map = folder / "evidence-map.yml"
    if evidence_map.exists():
        command += ["--evidence-map", str(evidence_map)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    qa_path = folder / "qa.json"
    qa = None
    if qa_path.exists():
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
        except ValueError:
            qa = None
    if qa:
        if not qa.get("compile_ok"):
            return False, "the PDF did not compile — open the folder and check resume.tex"
        if qa.get("page_count") != CONTRACT.pages:
            return False, f"the resume is {qa.get('page_count')} pages instead of {CONTRACT.pages}"
        if result.returncode:
            return True, f"{CONTRACT.describe_pages()} PDF compiled; evidence-map and visual review still pending (Assurance tab)"
        return True, ""
    if result.returncode:
        return False, (result.stderr + result.stdout)[-600:]
    return True, ""


def main():
    if "--check" in sys.argv:
        # Plumbing test for the scheduled task: prove the wrapper, interpreter and
        # paths work without spending any AI calls.
        log(f"Morning-run self-check (profile {PROFILE})")
        log("venv python: " + ("found" if VENV_PY.exists() else "MISSING at " + str(VENV_PY)))
        log("dashboard: " + ("running on " + BASE_URL if server_health() else "not running (would be started by a real run)"))
        log("today: " + TODAY + " · log: " + str(LOG_FILE))
        return 0 if VENV_PY.exists() else 1
    started = time.monotonic()
    log(f"=== Morning run for {TODAY} (profile {PROFILE}) ===")
    REPORT_DIR.mkdir(exist_ok=True)

    ensure_server()

    failures = []
    discovery_notes = []
    results = []
    # One engine: the same Daily Search pipeline the page runs, with every helper on, so a
    # morning resume gets company research like one started by hand. Auto (Settings) picks
    # the AI: free plans first, resting each until its reset, paid Azure last.
    try:
        remaining = api("GET", "/api/v2/pipeline/status").get("plan", {}).get("remaining_today", 5)
    except ApiError as error:
        remaining = 5
        log("Could not read today's plan (" + str(error) + "); asking for up to 5 jobs.")
    for number, source in enumerate(("portals", "balanced_five")):
        want = min(5, remaining) - len(results)
        if want <= 0:
            if number == 0:
                log("Today's application target is already met; nothing to search for.")
            break
        try:
            run = run_pipeline(source, want, include_unprepared=number == 0)
        except ApiError as error:
            if "plan is already complete" in str(error).lower():
                log("Today's application target is already met; discovery finished.")
                break
            log(f"Daily Search ({source}) did not complete: {error}")
            failures.append(f"Daily Search ({source}): {error}")
            continue
        progress = run.get("progress") or {}
        find = progress.get("find") or {}
        note = f"Daily Search ({source}): " + (find.get("note") or f"{len(progress.get('jobs') or [])} job(s)") + "."
        if run.get("state") == "failed":
            note += " Stopped by a problem: " + (run.get("error") or "no reason recorded")
            failures.append(f"Daily Search ({source}): {run.get('error') or 'failed'}")
        discovery_notes.append(note)
        log(note)
        for job in progress.get("jobs") or []:
            if not any(entry["job"]["id"] == job["id"] for entry in results):
                results.append({"job": {"id": job["id"], "company": job["company"], "title": job["title"]},
                                "steps": job.get("steps") or {}, "notes": []})

    if not results:
        log("No new jobs were prepared today.")
    known = {job["id"]: job for job in list_jobs()}
    for entry in results:
        job = known.get(entry["job"]["id"], entry["job"])
        entry["job"] = job
        log(f"--- {job['company']} — {job['title']} ---")
        for step, state in entry["steps"].items():
            if state.get("state") == "failed":
                entry["notes"].append(f"{STEP_NAMES.get(step, step)} failed: {state.get('error') or 'no reason recorded'}")
            elif state.get("state") == "skipped":
                entry["notes"].append(f"{STEP_NAMES.get(step, step)} was skipped (the run was stopped).")
            log(f"{STEP_NAMES.get(step, step)}: {state.get('state')}" + (f" — {state['note']}" if state.get("note") else ""))
        if job.get("folder"):
            folder = WORKSPACE_ROOT / job["folder"]
            ok, note = validate_resume(folder)
            entry["pdf"] = ok
            previews = sorted(folder.glob("studio/preview-*/resume.pdf"))
            if previews:
                entry["preview_pdf"] = str(previews[-1].relative_to(WORKSPACE_ROOT))
            log(f"Resume ready: {CONTRACT.describe_pages()} PDF compiled." + (" " + note if note else "") if ok else "Resume problem: " + note)
            if note:
                entry["notes"].append(note)
        else:
            entry["notes"].append("No application folder was created.")

    try:
        subprocess.run([str(VENV_PY), str(SCRIPTS / "workspace.py"), "export", "--profile", PROFILE],
                       capture_output=True, text=True, timeout=300)
        log("Dashboard projections regenerated.")
    except (OSError, subprocess.TimeoutExpired) as error:
        log("Projection export failed: " + str(error))

    if results:
        history = DAILY_DIR / "history.csv"
        new_file = not history.exists()
        with history.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow(["date", "company", "role", "url", "requisition_id", "location", "status", "artifact_dir"])
            for entry in results:
                job = entry["job"]
                writer.writerow([TODAY, job["company"], job["title"], job.get("url", ""),
                                 job.get("requisition_id", ""), job.get("location", ""),
                                 "prepared", job.get("folder", "")])

    lines = [f"# Morning report — {TODAY}", ""]
    if not results:
        lines += ["No new jobs were saved today.", ""]
        for note in discovery_notes:
            lines.append(f"- {note}")
        lines += ["",
                  "The gates only save verified, eligible postings — a quiet morning means the honest search "
                  "found nothing that passed, not that the run broke. Open the dashboard for the rejected-lead reasons, "
                  "or run Daily Search by hand.", ""]
    for entry in results:
        job = entry["job"]
        steps = entry["steps"]
        folder = job.get("folder") or ""
        lines.append(f"## {job['company']} — {job['title']}")
        lines.append(f"- Location: {job.get('location', '')} · Fit: {job.get('fit_score') or 'not scored'}")
        if job.get("fit_rationale"):
            lines.append(f"- Why it fits: {job['fit_rationale']}")
        if job.get("url"):
            lines.append(f"- Apply: {job['url']}")
        done = [STEP_NAMES.get(step, step) for step, state in steps.items() if state.get("state") == "done"]
        if done:
            lines.append("- Done: " + ", ".join(done))
        if folder:
            pdf = entry.get("preview_pdf") or (folder + "/resume.pdf (appears after the Assurance review passes)")
            lines.append(f"- Resume PDF: `{pdf}` (also: dashboard → Resume Studio → Download)")
        tailored = (steps.get("tailor") or {}).get("note")
        if tailored:
            lines.append(f"- Tailored: {tailored} — open the Assurance tab and keep/remove before applying.")
        for note in entry["notes"]:
            lines.append(f"- ⚠ {note}")
        lines.append("")
    if failures:
        lines += ["## Problems", ""] + [f"- {failure}" for failure in failures] + [""]
    lines.append("Nothing has been submitted anywhere. Review each resume, then apply through the posting link.")
    lines.append(f"Dashboard: {BASE_URL}/p/{PROFILE}/")
    report = REPORT_DIR / "MORNING-REPORT.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    log("Report written to " + str(report))

    minutes = (time.monotonic() - started) / 60
    log(f"=== Done in {minutes:.1f} minutes: {len(results)} job(s) prepared, {len(failures)} problem(s) ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as error:  # never leave a silent scheduled task
        log("Morning run stopped unexpectedly: " + repr(error))
        sys.exit(1)
