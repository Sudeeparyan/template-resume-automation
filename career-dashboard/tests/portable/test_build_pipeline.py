"""Build disposable Ireland, US, and dual-market profiles through the public API."""

from __future__ import annotations

import io
import sys
import time
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

APP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "backend/scripts"))

from backend.ai.agents.schemas import IntakeAudit, IntakeFacts  # noqa: E402
from backend.dashboard.shell import create_shell  # noqa: E402
from backend.pdf_compiler import tectonic_executable  # noqa: E402
from backend.profiles import ProfileStore  # noqa: E402
from backend.services.intake.job import IntakeJob  # noqa: E402
from backend.services.workspace_v2 import agents_for  # noqa: E402
import backend.services.intake.api as intake_api  # noqa: E402


class ExtractorFixture:
    """Stable AI boundary; source extraction, persistence and PDF build stay real."""

    def run(self, name, payload):
        if name == "intake_auditor":
            return IntakeAudit()
        assert name == "profile_extractor"
        assert "Example Person" in payload["text"]
        return IntakeFacts.model_validate({
            "contact": {"full_name": "Example Person", "city": "Dublin", "country": "Ireland", "refs": ["P001"]},
            "targets": {"roles": ["Customer Support Specialist"], "countries": ["Ireland"], "refs": ["P001"]},
            "experience": [{"employer": "Example Services", "title": "Customer Support Specialist",
                            "location": "Dublin", "start": "January 2023", "end": "Present",
                            "bullets": ["Responded to customer requests by email and phone.",
                                        "Used Zendesk to track support tickets and follow-up actions."],
                            "tools": ["Zendesk"], "refs": ["P001"]}],
            "skills": [{"name": "Customer service tools", "skills": ["Zendesk", "Email support"],
                        "level": "used", "refs": ["P001"]}],
            "questions": ["Which employment start date is correct?"] if "Different start date" in payload["text"] else [],
        })


def _docx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types' />")
        archive.writestr("word/document.xml", """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body><w:p><w:r><w:t>Example Person has customer support experience.</w:t></w:r></w:p></w:body></w:document>""")
    return output.getvalue()


def _text_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 18 Tf 72 700 Td (Example Person supports customers in Dublin.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _scanned_pdf() -> bytes:
    image = Image.new("RGB", (1700, 450), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=56)
    draw.text((70, 70), "Example Person", fill="black", font=font)
    draw.text((70, 155), "Customer support specialist in Dublin", fill="black", font=font)
    output = io.BytesIO()
    image.save(output, "PDF", resolution=150)
    return output.getvalue()


def _wait(client: TestClient, base: str, run_id: str) -> dict:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = client.get(f"{base}/build-runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"completed", "failed", "stopped", "interrupted"}:
            return run
        time.sleep(.2)
    pytest.fail("Build run exceeded two minutes")


@pytest.mark.skipif(not tectonic_executable(), reason="Tectonic is required for the full PDF build")
@pytest.mark.parametrize("markets", [["ie"], ["us"], ["ie", "us"]])
def test_build_profile_and_preserve_history(tmp_path, monkeypatch, markets):
    monkeypatch.setattr(intake_api, "team_factory", lambda _profiles: lambda _on_usage: ExtractorFixture())
    store = ProfileStore(base=tmp_path / "profiles", legacy_root=tmp_path / "no-legacy")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>test</html>", encoding="utf-8")
    with TestClient(create_shell(store, frontend=dist), base_url="http://127.0.0.1") as client:
        profile = client.post("/api/profiles", json={"name": "Placeholder Name" if markets == ["us"] else "Example Person"}).json()["profile"]
        pid = profile["id"]
        base = f"/api/profiles/{pid}"
        text = (b"Example Person\nCustomer Support Specialist, Dublin, Ireland\n"
                b"Example Services, January 2023 to Present\nResponded to customer requests by email and phone.\n"
                b"Used Zendesk to track support tickets and follow-up actions.\n")
        assert client.post(base + "/sources?name=resume.md", content=text).status_code == 200
        extras = [("resume.docx", _docx())] if markets == ["us"] else \
                 [("text.pdf", _text_pdf()), ("scan.pdf", _scanned_pdf())] if len(markets) == 2 else []
        for name, content in extras:
            response = client.post(base + "/sources?name=" + name, content=content)
            assert response.status_code == 200, response.text
        if len(markets) == 2:
            assert client.post(base + "/sources/notes", json={"name": "Preferences", "text": "I prefer hybrid work."}).status_code == 200
        authorization = {market: {"status": "authorized", "citizenship": "noncitizen",
                                  "needs_sponsorship_later": "no"} for market in markets}
        response = client.post(base + "/build-runs", json={"target_markets": markets,
                                                           "work_authorization_by_market": authorization})
        assert response.status_code == 200, response.text
        first = _wait(client, base, response.json()["id"])
        assert first["status"] == "completed", first.get("errors")
        assert client.get(base).json()["profile"]["name"] == "Example Person"
        root = store.root_for(pid)
        config = yaml.safe_load((root / "data/config/profile.yml").read_text(encoding="utf-8"))
        assert "Skills" in config["resume_contract"]["required_sections"]
        assert "Technical Skills" not in config["resume_contract"]["required_sections"]
        assert "Projects" not in config["resume_contract"]["required_sections"]
        assert config["project_selection"]["required_selected_projects"] == 0
        assert not config["project_selection"]["signature_first"]
        assert all("project" not in preference.casefold()
                   for preference in config["competition_strategy"]["prefer"])
        assert all("ship the strongest real project" not in rule.casefold()
                   for rule in config["project_selection"]["safeguards"])
        assert r"\section{Skills}" in (root / "data/templates/resume-base.tex").read_text(encoding="utf-8")
        descriptions = {agent["id"]: agent["does"] for agent in agents_for(root)}
        assert "four target families" not in descriptions["discovery"]
        if markets == ["ie", "us"]:
            assert "Ireland and the US" in descriptions["discovery"]
            assert "job market's resume contract" in descriptions["resume"]
        elif markets == ["ie"]:
            assert "Ireland" in descriptions["discovery"]
            assert "A4" in descriptions["resume"]
        else:
            assert "the US" in descriptions["discovery"]
            assert "Letter" in descriptions["resume"]
        pdfs = list((root / "data/output/base").glob("*.pdf"))
        assert len(pdfs) == 1 and pdfs[0].stat().st_size > 1000
        assert client.get(f"/p/{pid}/api/overview").status_code == 200
        assert client.get(base + "/sources/search?q=Zendesk").json()["matches"]

        posting_market = "us" if "us" in markets else "ie"
        response = client.post(f"/p/{pid}/api/jobs", json={
            "company": "Sample Support", "title": "Customer Support Specialist",
            "location": "Boston, MA" if posting_market == "us" else "Dublin, Ireland",
            "url": "https://example.org/careers/support-123",
            "description": "Support customers through email and phone. Track questions in a support-ticket system, "
                           "follow up on issues, and communicate clearly with the team and customers.",
            "market": posting_market,
        })
        assert response.status_code == 201, response.text
        job = response.json()
        assert job["market"] == posting_market

        if len(markets) == 2:
            assert client.post(base + "/sources/notes", json={"name": "Date conflict",
                                                             "text": "Different start date: February 2023."}).status_code == 200
            original = IntakeJob.build
            state = {"fail": True}

            def fail_once(self):
                if state["fail"]:
                    state["fail"] = False
                    raise RuntimeError("Simulated build failure")
                return original(self)

            monkeypatch.setattr(IntakeJob, "build", fail_once)
            response = client.post(base + "/build-runs", json={"target_markets": markets})
            failed = _wait(client, base, response.json()["id"])
            assert failed["status"] == "failed" and "Simulated build failure" in failed["errors"][0]
            assert client.get(f"/p/{pid}/api/overview").json()["revision"] == first["completed_profile_revision"]
            assert len(client.get(f"/p/{pid}/api/jobs").json()) == 1

            response = client.post(base + "/build-runs/" + failed["id"] + "/retry")
            assert response.status_code == 200, response.text
            rebuilt = _wait(client, base, response.json()["id"])
            assert rebuilt["status"] == "completed", rebuilt.get("errors")
            assert rebuilt["completed_profile_revision"] != first["completed_profile_revision"]
            assert any("clarification" in flag.lower() for flag in rebuilt["flags"])
            assert len(client.get(f"/p/{pid}/api/jobs").json()) == 1
