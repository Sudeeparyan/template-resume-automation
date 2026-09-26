"""Exercise the zero-profile first run without touching an installed profile.

This smoke test checks the built client and onboarding/source API in an ignored,
temporary workspace. AI calls and job discovery are covered by separate gates.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "career-dashboard"
sys.path.insert(0, str(APP))

from backend.dashboard.shell import create_shell  # noqa: E402
from backend.profiles import ProfileStore  # noqa: E402


def main() -> int:
    dist = APP / "frontend/dist"
    if not (dist / "index.html").is_file():
        raise RuntimeError("The frontend must be built before the first-run smoke test.")
    private_scratch = ROOT / ".runtime"  # ignored by Git, like every runtime folder
    private_scratch.mkdir(exist_ok=True)
    os.environ["CAREER_NO_SCHEDULED_TASKS"] = "1"
    with tempfile.TemporaryDirectory(prefix="first-run-", dir=private_scratch) as temporary:
        isolated = Path(temporary).resolve()
        if not isolated.is_relative_to(private_scratch.resolve()):
            raise RuntimeError("First-run scratch folder escaped the workspace.")
        profiles = ProfileStore(base=isolated / "profiles", legacy_root=isolated / "legacy")
        if profiles.list():
            raise AssertionError("A fresh installation must begin with no profiles.")
        with TestClient(create_shell(profiles, frontend=dist), base_url="http://127.0.0.1") as client:
            health = client.get("/api/health")
            assert health.status_code == 200 and health.json()["profiles"] is True
            home = client.get("/")
            assert home.status_code == 200 and "<html" in home.text.lower()
            listing = client.get("/api/profiles")
            assert listing.status_code == 200 and listing.json()["profiles"] == []

            first = client.post("/api/profiles", json={"name": "Example Person"})
            assert first.status_code == 201
            profile = first.json()["profile"]
            assert profile["state"] == "onboarding" and profile["target_markets"] == ["ie"]
            assert client.get(f"/p/{profile['id']}/").status_code == 200
            base = f"/api/profiles/{profile['id']}"
            upload = client.post(base + "/sources?name=background.md", content=b"Example Person works in Cork.")
            assert upload.status_code == 200 and len(upload.json()["sources"]) == 1
            second = client.post("/api/profiles", json={"name": "Second Person"})
            assert second.status_code == 201
            other = second.json()["profile"]["id"]
            assert client.get(f"/api/profiles/{other}/sources").json()["sources"] == []
            assert client.get(base + "/sources").json()["sources"][0]["name"] == "background.md"
        assert not (isolated / "profiles" / other / "data/source_library/sources.json").exists()
    print("PASS: zero-profile startup, onboarding, source upload, and profile isolation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
