"""The launcher only ever opens Chetan's own dashboard.

Another career dashboard on this Mac listens on 8000. Opening a browser tab on
a port that belongs to it would show somebody else's jobs and resumes as if
they were hers, so run.py has a distinct default port and asks the port who is
there before any tab opens.
"""
import http.server
import json
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

import run  # noqa: E402  (backend/run.py)
from backend.paths import APP_ID, APP_ROOT, DEFAULT_PORT  # noqa: E402


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def fake_dashboard():
    """Serve a /api/health document of our choosing on a free loopback port."""
    servers = []

    def serve(document):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(document).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


def test_default_port_is_not_the_other_dashboards_port():
    assert DEFAULT_PORT == 8010
    assert run.build_parser().parse_args([]).port == DEFAULT_PORT != 8000


def test_port_state_recognises_ours_other_and_free(fake_dashboard):
    ours = fake_dashboard({"app": APP_ID, "version": "2.0.0", "root": str(APP_ROOT)})
    assert run.port_state(ours) == ("ours", "")

    other = fake_dashboard({"app": "chetan-career-workspace", "root": "/Users/chetan/Desktop/Resume/career-dashboard"})
    state, detail = run.port_state(other)
    assert state == "other"
    assert "chetan-career-workspace" in detail and "/Users/chetan/Desktop/Resume" in detail

    assert run.port_state(free_port()) == ("free", "")


def test_a_plain_listener_without_health_counts_as_other():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        state, detail = run.port_state(listener.getsockname()[1])
    assert state == "other" and "no dashboard health endpoint" in detail


def test_main_refuses_someone_elses_port_and_opens_no_browser(fake_dashboard, monkeypatch):
    opened = []
    monkeypatch.setattr(run.webbrowser, "open", lambda url: opened.append(url))
    other = fake_dashboard({"app": "chetan-career-workspace", "root": "/Users/chetan/Desktop/Resume/career-dashboard"})
    with pytest.raises(SystemExit) as stop:
        run.main(["--port", str(other)])
    message = str(stop.value)
    assert f"Port {other} is already used by chetan-career-workspace" in message
    assert "no browser tab was opened" in message and f"--port {other + 1}" in message
    assert opened == []


def test_main_reuses_a_running_copy_of_this_app(fake_dashboard, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(run.webbrowser, "open", lambda url: opened.append(url))
    ours = fake_dashboard({"app": APP_ID, "root": str(APP_ROOT)})
    run.main(["--port", str(ours)])
    assert opened == [f"http://127.0.0.1:{ours}"]
    assert "Dashboard already running" in capsys.readouterr().out


def test_browser_opens_only_once_the_port_answers_as_this_app(fake_dashboard, monkeypatch):
    opened = []
    monkeypatch.setattr(run.webbrowser, "open", lambda url: opened.append(url))
    other = fake_dashboard({"app": "chetan-career-workspace", "root": "/elsewhere"})
    assert run.open_when_ready(f"http://127.0.0.1:{other}", deadline_seconds=1.0) is False
    assert opened == []
    ours = fake_dashboard({"app": APP_ID, "root": str(APP_ROOT)})
    assert run.open_when_ready(f"http://127.0.0.1:{ours}", deadline_seconds=5.0) is True
    assert opened == [f"http://127.0.0.1:{ours}"]


def test_a_copy_running_older_code_is_restarted(fake_dashboard, monkeypatch, capsys):
    """Starting again must load new code; a copy started before the last change is replaced."""
    stopped, served = [], []
    monkeypatch.setattr(run.webbrowser, "open", lambda url: None)
    ours = fake_dashboard({"app": APP_ID, "root": str(APP_ROOT), "pid": 424242, "started_at": 1.0, "busy": False})
    monkeypatch.setattr(run, "stop_server", lambda document, port: stopped.append(document["pid"]) or True)
    monkeypatch.setattr("build_frontend.build", lambda: None)
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: served.append(k["port"]))
    run.main(["--port", str(ours), "--no-browser"])
    assert stopped == [424242] and served == [ours]
    output = capsys.readouterr().out
    assert "Restarting the dashboard" in output and "AI apps on this PC" in output


def test_a_busy_copy_is_never_restarted(fake_dashboard, monkeypatch, capsys):
    monkeypatch.setattr(run.webbrowser, "open", lambda url: None)
    ours = fake_dashboard({"app": APP_ID, "root": str(APP_ROOT), "pid": 424242, "started_at": 1.0, "busy": True})
    monkeypatch.setattr(run, "stop_server", lambda document, port: pytest.fail("a busy dashboard was stopped"))
    run.main(["--port", str(ours), "--restart", "--no-browser"])
    output = capsys.readouterr().out
    assert "busy with a search" in output and "Dashboard already running" in output


def test_a_current_copy_is_reused_unless_restart_is_asked(fake_dashboard, monkeypatch, capsys):
    import time

    monkeypatch.setattr(run.webbrowser, "open", lambda url: None)
    ours = fake_dashboard({"app": APP_ID, "root": str(APP_ROOT), "pid": 424242, "started_at": time.time() + 3600, "busy": False})
    monkeypatch.setattr(run, "stop_server", lambda document, port: pytest.fail("a current dashboard was stopped"))
    run.main(["--port", str(ours), "--no-browser"])
    assert "Dashboard already running" in capsys.readouterr().out
