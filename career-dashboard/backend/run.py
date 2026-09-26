#!/usr/bin/env python3
"""Start the local dashboard.

From this folder, with the venv active:  python run.py
The React client is rebuilt first when its sources changed.

The port defaults to paths.DEFAULT_PORT (8000). Before anything opens a browser
tab the launcher asks the port who is there: it only ever opens this app, and
it refuses plainly when the port belongs to something else.

When this app is already running but was started before the code last
changed, it is stopped and started again (never while a search or an agent run
is in progress), so starting always gives the newest version. ``--restart``
forces that even when the code is unchanged.
"""
import argparse
import importlib.util
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# Make ``backend`` importable however this file is launched, then let paths.py
# answer everything else.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.paths import APP_ID, APP_ROOT, APP_TITLE, DEFAULT_PORT, SCRIPTS  # noqa: E402
from backend.pdf_compiler import tectonic_executable  # noqa: E402

sys.path.insert(0, str(SCRIPTS))


def health(url, timeout=1.0):
    """The /api/health document of whatever answers at ``url``, or None."""
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=timeout) as response:
            document = json.load(response)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def is_ours(document):
    return bool(document) and document.get("app") == APP_ID and document.get("root") == str(APP_ROOT)


def port_state(port, host="127.0.0.1"):
    """'ours' | 'free' | 'other' — what is on the port right now.

    Returns a (state, detail) pair; ``detail`` names the other occupant so the
    message the user sees says whose app it is instead of a bare bind error.
    """
    document = health(f"http://{host}:{port}")
    if is_ours(document):
        return "ours", ""
    if document:
        return "other", f"{document.get('app') or 'another dashboard'} at {document.get('root') or '?'}"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # Same option uvicorn binds with, so a just-stopped server's TIME_WAIT
        # sockets do not read as an occupant while a live listener still does.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return "other", "another program (no dashboard health endpoint)"
    return "free", ""


def newest_source_time(root=APP_ROOT):
    """When the app's code last changed: backend Python and the React sources."""
    newest = 0.0
    for sub in ("backend", "frontend/src"):
        for folder, dirs, files in os.walk(Path(root) / sub):
            dirs[:] = [d for d in dirs if d not in {"__pycache__", "node_modules", "dist"} and not d.startswith(".")]
            for name in files:
                if name.endswith((".py", ".ts", ".tsx", ".css")):
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(folder, name)))
                    except OSError:
                        continue
    return newest


def stop_server(document, port, timeout=20.0):
    """Stop the dashboard that answered with ``document``; True once the port is free."""
    pid = document.get("pid")
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    stop = time.monotonic() + timeout
    while time.monotonic() < stop:
        if port_state(port)[0] == "free":
            return True
        time.sleep(0.5)
    return False


def ai_apps_line():
    """One line naming the AI apps and keys this PC offers, for the start window."""
    try:
        from backend.ai import kimi_cli, provider_label, ready_providers

        ready = ready_providers(APP_ROOT)
        local = ", ".join(provider_label(p) + (" ready" if ready.get(p) else " not found")
                          for p in ("claude_code", "codex", "kimi_cli"))
        keyed = [provider_label(p) for p in ("openai", "azure_openai", "anthropic", "openrouter", "gemini", "kimi") if ready.get(p)]
        pdf = "ready" if tectonic_executable() else "not found (resumes cannot be turned into PDFs until Tectonic is installed)"
        kimi = ("\nKimi Code: only the VS Code extension is installed. To use Kimi here, run in PowerShell: "
                + kimi_cli.INSTALL_COMMAND) if kimi_cli.extension_only() else ""
        return ("AI apps on this PC: " + local + (" · API keys: " + ", ".join(keyed) if keyed else "")
                + "\nPDF compiler (Tectonic): " + pdf + kimi)
    except Exception as error:  # informational only; never block the start
        return f"AI apps on this PC: could not check ({type(error).__name__})"


def open_when_ready(url, deadline_seconds=30.0):
    """Open a browser tab only once ``url`` answers as this app."""
    stop = time.monotonic() + deadline_seconds
    while time.monotonic() < stop:
        if is_ours(health(url)):
            webbrowser.open(url)
            return True
        time.sleep(0.4)
    return False


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--restart", action="store_true",
                        help="Restart a running copy of this dashboard even if its code is current")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port from 1024 to 65535")
    if importlib.util.find_spec("uvicorn") is None:
        raise SystemExit(
            "Install dependencies first: python -m pip install -r requirements.txt"
        )
    url = f"http://127.0.0.1:{args.port}"
    state, detail = port_state(args.port)
    if state == "ours":
        # A copy started before the code last changed would hide new features, so
        # replace it, but never while it is in the middle of a search or an agent run.
        document = health(url) or {}
        started = document.get("started_at")
        stale = isinstance(started, (int, float)) and newest_source_time() > started
        if (stale or args.restart) and document.get("pid"):
            if document.get("busy"):
                print("The dashboard is busy with a search or an agent run, so it was not restarted.\n"
                      "Start again when it finishes to load the newest version.", flush=True)
            else:
                print("Restarting the dashboard so it runs the newest version...", flush=True)
                if stop_server(document, args.port):
                    state = "free"
                else:
                    print("The running dashboard did not stop. Close its window, then start again.", flush=True)
        elif args.restart:
            print("The running dashboard is an older version that cannot be restarted from here.\n"
                  "Close its window, then start again.", flush=True)
    if state == "ours":
        print(f"Dashboard already running: {url}", flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        return
    if state == "other":
        raise SystemExit(
            f"Port {args.port} is already used by {detail}.\n"
            f"That is not {APP_TITLE}, so no browser tab was opened.\n"
            f"Start this app on a free port instead:  python run.py --port {args.port + 1}"
        )

    from build_frontend import build

    try:
        build()
    except (Exception, SystemExit) as error:  # noqa: BLE001 - the API (and the morning run) must still start
        print(f"The dashboard page could not be rebuilt ({error}).\n"
              "The previous page is served if there is one; job searches and the morning list still run. "
              "Run Check Workspace.cmd to see what stopped the build.", flush=True)
    import uvicorn
    from backend.dashboard.shell import create_shell

    print(ai_apps_line(), flush=True)
    print(f"{APP_TITLE}: {url}", flush=True)
    if not args.no_browser:
        opener = threading.Thread(target=open_when_ready, args=(url,), daemon=True)
        opener.start()
    # One server for every profile; each profile is its own isolated app under /p/<id>/.
    uvicorn.run(create_shell(schedule=True), host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
