"""Your data (services/export.py): the JSON backup and the documents zip.

The backup holds the person's own records and never a credential; the zip only ever reads
files inside the profile's output folder.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, timedelta

import yaml

from app_fixture import add_job, profile_app
from backend.services.export import scrub
from career import Workspace


def test_the_backup_holds_the_records_and_no_credentials(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        # A settings file that (wrongly) holds secrets: the backup must leave them out.
        config = root / "data/config/profile.yml"
        profile = yaml.safe_load(config.read_text(encoding="utf-8"))
        profile["integrations"] = {"api_key": "sk-test-not-real", "gmail": {"access_token": "tok-test", "label": "Jobs"}}
        config.write_text(yaml.safe_dump(profile), encoding="utf-8")

        job = add_job(root)
        removed = add_job(root, "Removed Company", "Analyst", "removed")
        ws = Workspace(root)
        ws.update_job(job["id"], "applied", application_date=(date.today() - timedelta(days=3)).isoformat())
        ws.remove_job(removed["id"], "Wrong location")
        client.post(base + f"/v2/jobs/{job['id']}/interviews", json={"starts_at": (date.today() + timedelta(days=2)).isoformat() + "T10:00"})

        response = client.get(base + "/v2/export/backup.json")
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        body = response.json()
        assert body["format"] == "career-workspace-backup" and body["version"] == 1
        ids = {j["id"]: j for j in body["applications"]}
        assert ids[job["id"]]["status"] == "applied"
        assert ids[removed["id"]]["deletion_reason"] == "Wrong location"
        assert len(body["interviews"]) == 1
        assert any(e["action"] == "progress_updated" for e in body["history"])
        text = json.dumps(body)
        assert "sk-test-not-real" not in text and "tok-test" not in text
        assert body["configuration"]["integrations"]["gmail"]["label"] == "Jobs"


def test_scrub_removes_secret_named_fields_at_any_depth():
    cleaned = scrub({"a": 1, "API_KEY": "x", "nested": [{"refresh_token": "y", "keep": "z"}], "password": "p"})
    assert cleaned == {"a": 1, "nested": [{"keep": "z"}]}


def test_the_documents_zip_has_each_file_once_and_stays_in_the_output_folder(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        job = add_job(root, "Example, Company!", "Data Analyst", "zip")
        folder = root / "data/output/example-company-data-analyst"
        folder.mkdir(parents=True)
        (folder / "resume.pdf").write_bytes(b"%PDF-1.4 test resume")
        secret = root / "data/config/not-a-document.txt"
        secret.write_text("never zipped", encoding="utf-8")
        with Workspace(root).connect() as db:
            db.execute("UPDATE jobs SET folder=? WHERE id=?", ("data/output/example-company-data-analyst", job["id"]))
            # A letter row pointing outside the output folder must be skipped, not read.
            db.execute("INSERT INTO cover_letters(job_id,version,content,path,created_at,evidence_revision) VALUES(?,?,?,?,?,?)",
                       (job["id"], 1, "Dear team", "../config/not-a-document.txt", "2026-11-01T00:00:00", "r1"))
        response = client.get(base + "/v2/export/documents.zip")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        assert names == ["Example-Company_Data-Analyst/resume.pdf"]
        assert archive.read(names[0]) == b"%PDF-1.4 test resume"


def test_an_empty_workspace_gives_an_empty_zip(tmp_path, monkeypatch):
    with profile_app(tmp_path, monkeypatch) as (client, base, root):
        response = client.get(base + "/v2/export/documents.zip")
        assert response.status_code == 200
        assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == []
