"""A disposable profile served by the real dashboard shell, for API tests.

Used by the board, interview, follow-up, profile-strength and export tests. Everything lives
under pytest's tmp_path; no real profile, backup or credential is ever read.
"""

from __future__ import annotations

from contextlib import contextmanager

import yaml
from fastapi.testclient import TestClient

import backend.profiles
from backend.dashboard.shell import create_shell
from backend.profiles import ProfileStore
from career import Workspace

JD = ("Analyze sample data in Dublin, document findings, maintain reports, "
      "review source records, and share findings with the team every week.")


@contextmanager
def profile_app(tmp_path, monkeypatch, profile: dict | None = None, evidence: dict | None = None):
    """Yield (client, base, root): a test client, the profile's API prefix and its folder."""
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    pid = store.create("Example Person")["id"]
    root = store.root_for(pid)
    (root / "data/config/profile.yml").write_text(yaml.safe_dump(profile or {
        "country_pack": "ie", "target_markets": ["ie"], "candidate_revision": "2026-11-01.1",
        "candidate": {"full_name": "Example Person", "timezone": "Europe/Dublin"},
        "work_authorization_by_market": {"ie": {"status": "authorized", "citizenship": "citizen"}},
    }), encoding="utf-8")
    (root / "data/context/evidence.yml").write_text(yaml.safe_dump(evidence or {
        "candidate_revision": "2026-11-01.1",
        "claims": [{"id": "FACT-EXAMPLE", "category": "other", "approved_external_use": "Example fixture fact."}],
        "projects": [],
    }), encoding="utf-8")
    store.update(pid, state="ready")
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    monkeypatch.setattr(backend.profiles, "store", lambda: store)
    with TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1") as client:
        yield client, f"/p/{pid}/api", root


def add_job(root, company="Example Company", title="Data Analyst", url_tail="data-analyst"):
    return Workspace(root).add_job(company, title, "Dublin, Ireland",
                                   f"https://example.org/jobs/{url_tail}", JD)
