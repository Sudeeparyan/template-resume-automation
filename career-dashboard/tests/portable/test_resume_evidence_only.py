"""Resume output must never turn model suggestions into candidate evidence."""

from __future__ import annotations

import json

import pytest
import yaml

from backend.services.resume_studio import ResumeStudio
from test_hunt import ireland_profile, posting


def studio_fixture(tmp_path, monkeypatch):
    services = ireland_profile(tmp_path)
    evidence_path = services.w.root / "data/context/evidence.yml"
    evidence = yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    evidence["candidate_revision"] = "sample-revision"
    evidence["projects"] = [
        {"id": f"PROJ-00{number}", "status": "user_reported", "signature_eligible": True,
         "resume_content": {"title": title, "context": "Personal project",
                            "bullets": [f"Used Python for {title.lower()}."]}}
        for number, title in ((1, "Report tool"), (2, "Data checker"))
    ]
    evidence_path.write_text(yaml.safe_dump(evidence), encoding="utf-8")
    job = services.add_posting(posting(8), source="discovery")["job"]
    studio = ResumeStudio(services)
    source = "% EVIDENCE: SKILL-001\n\\newcommand{\\SkillsLanguages}{Python, SQL}\n"
    for slot, project in zip(("SelectedProject", "SecondProject"), evidence["projects"]):
        content = project["resume_content"]
        for suffix, value in (("ID", project["id"]), ("Title", content["title"]),
                              ("Context", content["context"]), ("BulletOne", content["bullets"][0]),
                              ("BulletTwo", ""), ("BulletThree", "")):
            source += f"% EVIDENCE: {project['id']}\n\\newcommand{{\\{slot}{suffix}}}{{{value}}}\n"
        marker = "SELECTED_PROJECT" if slot == "SelectedProject" else "SECOND_PROJECT"
        source += f"% {marker}_BLOCK_START\n\\{slot}Title\n% {marker}_BLOCK_END\n"
    folder = "data/output/sample-job/studio"
    (services.w.root / folder).mkdir(parents=True)
    with services.w.connect() as db:
        db.execute("INSERT INTO studio_drafts VALUES(?,?,?,?,?,?)",
                   (job["id"], source, 1, folder, services.now(), evidence["candidate_revision"]))
        db.execute("INSERT INTO studio_versions VALUES(?,?,?,?)", (job["id"], 1, source, services.now()))
    monkeypatch.setattr(studio, "fit", lambda job_id, revision: studio.get(job_id))
    monkeypatch.setattr(studio, "score", lambda job_id: {})
    return services, studio, job, source, evidence


def predicted_skill(services, job_id, source, *, decision="pending", tagged=True):
    source = source.replace("{Python, SQL}", "{Python, SQL, Apache Kafka}")
    if tagged:
        source = source.replace("% EVIDENCE: SKILL-001", "% EVIDENCE: SKILL-001 resume_items:legacy-item", 1)
    with services.w.connect() as db:
        db.execute("UPDATE studio_drafts SET source=? WHERE job_id=?", (source, job_id))
        db.execute("INSERT INTO resume_items(id,job_id,section,content,origin,evidence_id,decision,created_at,updated_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", ("legacy-item", job_id, "skills", "Apache Kafka", "predicted", "",
                                                 decision, services.now(), services.now()))
    return source


class SuggestedItemsTeam:
    def __init__(self, evidence):
        self.evidence = evidence

    def run(self, name, payload):
        assert name == "job_tailor"
        return {
            "projects": [
                {"origin": "predicted", "title": "Employer streaming platform", "context": "Personal project",
                 "bullets": ["Built a production stream using Apache Kafka."]},
                *[{"origin": "verified", "evidence_id": project["id"], **project["resume_content"]}
                  for project in self.evidence["projects"]],
            ],
            "skills": [{"name": "Python", "origin": "verified", "evidence_id": "SKILL-001"},
                       {"name": "SQL", "origin": "verified", "evidence_id": "SKILL-001"},
                       {"name": "Apache Kafka", "origin": "predicted"}],
            "rationale": "The registered projects show reporting and data checks.",
        }


def test_tailoring_ignores_suggestions_and_removes_old_predicted_skill(tmp_path, monkeypatch):
    services, studio, job, source, evidence = studio_fixture(tmp_path, monkeypatch)
    predicted_skill(services, job["id"], source, decision="kept")
    result = studio.tailor(job["id"], SuggestedItemsTeam(evidence))
    assert "Apache Kafka" not in result["source"]
    assert "Employer streaming platform" not in result["source"]
    assert "resume_items:" not in result["source"]
    assert "Report tool" in result["source"] and "Data checker" in result["source"]
    assert result["items"]["predicted"] == 0
    assert all(row["origin"] == "verified" and row["evidence_id"]
               for row in studio.items(job["id"]) if row["decision"] != "removed")


def test_retailoring_preserves_audit_for_old_untagged_suggestion(tmp_path, monkeypatch):
    services, studio, job, source, evidence = studio_fixture(tmp_path, monkeypatch)
    old_source = predicted_skill(services, job["id"], source, decision="kept", tagged=False)
    assert studio.resume_evidence_problem(job["id"], old_source)
    result = studio.tailor(job["id"], SuggestedItemsTeam(evidence))
    assert not studio.resume_evidence_problem(job["id"], result["source"])
    history = next(row for row in studio.items(job["id"]) if row["id"] == "legacy-item")
    assert history["decision"] == "removed"
    assert studio.resume_evidence_problem(job["id"], old_source)


@pytest.mark.parametrize("decision", ["pending", "kept", "removed"])
@pytest.mark.parametrize("tagged", [True, False])
def test_legacy_suggestion_in_source_cannot_be_exported(tmp_path, monkeypatch, decision, tagged):
    services, studio, job, source, _ = studio_fixture(tmp_path, monkeypatch)
    predicted_skill(services, job["id"], source, decision=decision, tagged=tagged)
    for format in ("tex", "pdf"):
        with pytest.raises(ValueError, match="evidence"):
            studio.download(job["id"], format)
    with pytest.raises(ValueError, match="evidence"):
        studio.preview(job["id"], 1)
    # Exercise the real method; the fixture stubs PDF fitting for tailoring tests.
    with pytest.raises(ValueError, match="evidence"):
        ResumeStudio.fit(studio, job["id"], 1)


def test_keep_is_not_candidate_evidence_and_remove_repairs_legacy_draft(tmp_path, monkeypatch):
    services, studio, job, source, _ = studio_fixture(tmp_path, monkeypatch)
    predicted_skill(services, job["id"], source)
    with pytest.raises(ValueError, match="evidence"):
        studio.decide(job["id"], "legacy-item", "kept")
    assert studio.items(job["id"])[0]["decision"] == "pending"
    repaired = studio.decide(job["id"], "legacy-item", "removed")["draft"]
    assert "Apache Kafka" not in repaired["source"]
    assert "resume_items:" not in repaired["source"]
    path, _ = studio.download(job["id"], "tex")
    assert "Apache Kafka" not in path.read_text(encoding="utf-8")


def test_removed_project_restores_registry_content(tmp_path, monkeypatch):
    services, studio, job, source, _ = studio_fixture(tmp_path, monkeypatch)
    source = source.replace("{PROJ-001}", "{legacy-project}").replace("{Report tool}", "{Employer streaming platform}")
    content = {"title": "Employer streaming platform", "context": "Personal project", "bullets": ["Built Kafka streams."]}
    with services.w.connect() as db:
        db.execute("UPDATE studio_drafts SET source=? WHERE job_id=?", (source, job["id"]))
        db.execute("INSERT INTO resume_items(id,job_id,section,content,origin,evidence_id,decision,created_at,updated_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", ("legacy-project", job["id"], "projects", json.dumps(content), "predicted", "",
                                                 "kept", services.now(), services.now()))
    assert studio.resume_evidence_problem(job["id"], source)
    repaired = studio.decide(job["id"], "legacy-project", "removed")["draft"]
    assert "Employer streaming platform" not in repaired["source"]
    assert not studio.resume_evidence_problem(job["id"], repaired["source"])


def test_verified_rewrites_and_mislabeled_skills_cannot_add_claims(tmp_path, monkeypatch):
    services, studio, job, _, evidence = studio_fixture(tmp_path, monkeypatch)
    plan = SuggestedItemsTeam(evidence).run("job_tailor", {})
    plan["projects"] = plan["projects"][1:]
    plan["projects"][0].update(title="Commercial streaming platform", bullets=["Saved a million dollars with Kafka."])
    plan["skills"] = [{"name": "Python", "origin": "predicted"},
                      {"name": "Python", "origin": "verified", "evidence_id": "FORGED-ID"},
                      {"name": "Apache Kafka", "origin": "verified", "evidence_id": "SKILL-001"}]
    team = type("FixedTeam", (), {"run": lambda self, name, payload: plan})()
    result = studio.tailor(job["id"], team)
    assert "Commercial streaming platform" not in result["source"]
    assert "million dollars" not in result["source"]
    assert "Apache Kafka" not in result["source"]
    assert "Report tool" in result["source"] and "Python" in result["source"]
    assert all(row["evidence_id"] != "FORGED-ID" for row in studio.items(job["id"]))


def test_never_claim_list_is_not_a_verified_skill_pool(tmp_path, monkeypatch):
    services, studio, _, _, evidence = studio_fixture(tmp_path, monkeypatch)
    evidence["claims"].append({"id": "SKILL-NEVER-001", "category": "constraint", "status": "user_reported",
                               "approved_facts": ["Apache Kafka"]})
    (services.w.root / "data/context/evidence.yml").write_text(yaml.safe_dump(evidence), encoding="utf-8")
    assert "apache kafka" not in studio._verified_skill_pool()


def test_preview_metadata_cannot_make_a_missing_pdf_current(tmp_path, monkeypatch):
    import hashlib

    services, studio, job, source, _ = studio_fixture(tmp_path, monkeypatch)
    folder = services.w.root / "data/output/sample-job/studio"
    (folder / "preview.json").write_text(json.dumps({"revision": 1, "page_count": 2,
                                                    "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                                                    "path": "sample-job/studio/preview-1"}), encoding="utf-8")
    preview_pdf = folder / "preview-1/resume.pdf"
    preview_pdf.parent.mkdir()
    preview_pdf.write_bytes(b"%PDF-1.7 disposable preview")
    assert studio.get(job["id"])["preview"]["current"] is True
    preview_pdf.unlink()
    assert studio.get(job["id"])["preview"]["current"] is False
    with pytest.raises(ValueError, match="current"):
        studio.download(job["id"], "pdf")
