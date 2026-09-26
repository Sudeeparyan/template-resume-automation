#!/usr/bin/env python3
"""Offline tests for the verify-job-url skill."""

from __future__ import annotations

import importlib.util
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# The skills live at the repo root, beside the one AGENTS.md every AI app reads.
SCRIPT = ROOT.parent / ".agents/skills/verify-job-url/scripts/verify_job_url.py"
SPEC = importlib.util.spec_from_file_location("verify_job_url", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class TestHandler(BaseHTTPRequestHandler):
    __test__ = False
    def do_GET(self):
        if self.path == "/jobs/redirect":
            self.send_response(302)
            self.send_header("Location", "/careers")
            self.end_headers()
            return

        pages = {
            "/jobs/active": (
                200,
                "<html><title>Data Analyst</title><body>Data Analyst Dublin Apply now</body></html>",
            ),
            "/jobs/expired": (
                200,
                "<html><body>This job is no longer available</body></html>",
            ),
            "/careers": (
                200,
                "<html><body>Explore careers with Example Company</body></html>",
            ),
        }
        status, body = pages.get(self.path, (404, "<html><body>Page not found</body></html>"))
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, _format, *_args):
        return


class VerifyJobUrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_pipeline_parser(self):
        source = (
            "# Pipeline\n\n"
            "- [ ] https://example.test/jobs/123 | Example Co | Data Analyst | Analytics | Dublin\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "pipeline.md"
            fixture.write_text(source, encoding="utf-8")
            entries = MODULE.extract_urls_from_markdown(str(fixture))

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["company"], "Example Co")
        self.assertEqual(entries[0]["role"], "Data Analyst")
        self.assertEqual(entries[0]["track"], "Analytics")
        self.assertEqual(entries[0]["location"], "Dublin")

    def test_active_page(self):
        result = MODULE.check_url(f"{self.base_url}/jobs/active", "direct_job")
        self.assertEqual(result["status"], "LIKELY_ACTIVE")

    def test_expired_page(self):
        result = MODULE.check_url(f"{self.base_url}/jobs/expired", "direct_job")
        self.assertEqual(result["status"], "EXPIRED")

    def test_generic_career_portal(self):
        result = MODULE.check_url(f"{self.base_url}/careers", "career_portal")
        self.assertEqual(result["status"], "NEEDS_MANUAL_CHECK")

    def test_specific_job_redirecting_to_portal(self):
        result = MODULE.check_url(f"{self.base_url}/jobs/redirect", "direct_job")
        self.assertEqual(result["status"], "EXPIRED")

    def test_visible_text_ignores_scripts(self):
        source = "<script>This job is no longer available</script><p>Data Analyst Apply now</p>"
        self.assertEqual(MODULE.html_to_text(source), "Data Analyst Apply now")


if __name__ == "__main__":
    unittest.main()
