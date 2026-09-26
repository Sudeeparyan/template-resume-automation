"""A Profile save reaches every place that reads Chetan's facts, and nothing else moves."""

import importlib.util
import shutil
import subprocess
import sys

import pytest
import yaml
from fastapi.testclient import TestClient

from test_career_workspace import add, workspace, ROOT  # noqa: F401 - fixture
from test_profile_fields import service  # noqa: F401 - fixture (pinned day, workflows)
from backend.dashboard.app import create_app
from backend.resume_contract import contract_for
from backend.services import profile_sync
from backend.services.resume_studio import ResumeStudio
from backend.services.workspace_v2 import CareerServices
from career import Workspace
from validate_resume import extract_zero_argument_macros

TEMPLATE = "data/templates/resume-base.tex"


def entry(service, id):
    return next(i for i in service.knowledge() if i["id"] == id)


def claim(service, id):
    return next(c for group in ("claims", "projects") for c in service.w.evidence()[group] if c["id"] == id)


def text(service, relative):
    return (service.w.root / relative).read_text(encoding="utf-8")


def edit(service, id, **fields):
    old = entry(service, id)
    return service.save_knowledge({"kind": old["kind"], "title": old["title"], "revision": old["revision"],
                                   "fields": fields}, id)


def workspace_failures(root):
    """The workspace validator's profile, registry and base-resume checks, run on the copy."""
    spec = importlib.util.spec_from_file_location("copied_validate_workspace", root / "backend/scripts/validate_workspace.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONTRACT = contract_for(root)  # the copy's contract, not the one cached for the repo
    failures = []
    module.fail, module.warn = failures.append, lambda message: None
    profile = module.load_yaml_mapping("data/config/profile.yml")
    registry = module.load_yaml_mapping("data/context/evidence.yml")
    module.validate_profile(profile, registry)
    module.validate_evidence_registry(registry, profile)
    module.validate_resume()
    return failures


def reimported(service, tmp_path):
    """A fresh database built from the copy's files, as on a new machine (career.db is not in git)."""
    fresh = tmp_path / "fresh"
    for name in ("data/config", "data/context", "data/templates", "backend/scripts", "backend/workflows"):
        shutil.copytree(service.w.root / name, fresh / name)
    for module in ("resume_contract.py", "pdf_compiler.py", "ai_marks.py"):
        shutil.copy2(service.w.root / "backend" / module, fresh / "backend" / module)
    (fresh / "backend/__init__.py").write_text("")
    (fresh / "data/historical-packs.json").write_text("[]")
    return CareerServices(Workspace(fresh))


def resume_check(root, relative=TEMPLATE):
    run = subprocess.run([sys.executable, "backend/scripts/validate_resume.py", relative],
                         cwd=root, capture_output=True, text=True, timeout=120)
    return run.stdout + run.stderr


def test_an_experience_edit_reaches_the_registry_the_resume_and_the_agents(service):
    before_profile = text(service, "data/config/profile.yml")
    before_revision = service.w.profile()["candidate_revision"]
    saved = edit(service, "EXP-TA-001", location="Fayetteville, Arkansas", bullets=[
        "Supported 100+ students in Programming Foundations through lab instruction and debugging help",
        "Assisted students with control flow, functions, arrays, programming logic, debugging, and problem-solving techniques",
        "Supported Database Management Systems instruction for approximately 180 students, assisting with database concepts, assignments, assessments, and technical questions",
        "Graded weekly lab submissions",
    ])
    assert saved["review_state"] == "registered" and not service.profile_dirty()
    assert saved["synced"]["updated"] == ["Profile settings (profile.yml)", "Evidence registry (evidence.yml)", "Base resume"]
    registered = claim(service, "EXP-TA-001")
    assert registered == saved["data"] and registered["location"] == "Fayetteville, Arkansas"
    assert registered["approved_facts"][-1] == "Graded weekly lab submissions"
    # One revision for both files, today's date; nothing else in profile.yml moved.
    revision = service.w.evidence()["candidate_revision"]
    assert revision == service.w.profile()["candidate_revision"] == "2026-09-12.1"
    assert text(service, "data/config/profile.yml") == before_profile.replace(before_revision, revision)
    # The base resume prints the new heading, reworded bullet and new bullet in place.
    template = text(service, TEMPLATE)
    assert r"\roleheading{Teaching Assistant}{Sep 2024 -- May 2026}{University of Arkansas, Dept. of Computer Science and Engineering}{Fayetteville, Arkansas}" in template
    assert "through lab instruction and debugging help\n  % EVIDENCE: EXP-TA-001\n  \\item Graded weekly lab submissions" in template
    assert "laboratory instruction" not in template
    # Agents read the same wording with its details.
    context = next(i for i in service.profile_context() if i["id"] == "EXP-TA-001")
    assert context["details"]["location"] == "Fayetteville, Arkansas"
    assert workspace_failures(service.w.root) == []
    assert "AUTOMATED_PASS" in resume_check(service.w.root)


def test_a_contact_change_moves_its_twin_the_header_and_the_contract(service):
    saved = edit(service, "personal:phone", value="+1 (479) 555-0100")
    assert saved["data"]["value"] == "+1 (479) 555-0100"
    assert service.w.profile()["candidate"]["phone"] == "+1 (479) 555-0100"
    assert claim(service, "CONTACT-PHONE-001")["value"] == "+1 (479) 555-0100"
    twin = entry(service, "CONTACT-PHONE-001")
    assert twin["summary"] == "+1 (479) 555-0100" and twin["data"]["value"] == "+1 (479) 555-0100"
    macros = extract_zero_argument_macros(text(service, TEMPLATE))
    assert macros["ResumeContact"].startswith("+1 (479) 555-0100 \\textbar{} \\href{mailto:chetanprm0501@gmail.com}")
    assert "+1 (479) 555-0100" in contract_for(service.w.root).header_values()
    # The other direction: editing the registry claim moves profile.yml and the Basics row.
    edit(service, "CONTACT-EMAIL-001", details="chetan.m@example.com")
    assert service.w.profile()["candidate"]["email"] == "chetan.m@example.com"
    assert entry(service, "personal:email")["summary"] == "chetan.m@example.com"
    assert "\\href{mailto:chetan.m@example.com}{chetan.m@example.com}" in text(service, TEMPLATE)
    assert workspace_failures(service.w.root) == []


def test_skill_lists_follow_and_the_never_claim_list_is_respected(service):
    edit(service, "SKILL-LANGUAGES-001", skills=["Python", "SQL", "C#", "C++17", "Bash"])
    macros = extract_zero_argument_macros(text(service, TEMPLATE))
    assert macros["SkillsLanguages"] == "Python, SQL, C\\#, C++17, Java, C, Bash"
    with pytest.raises(ValueError, match="Go is on your never-claim list"):
        edit(service, "SKILL-LANGUAGES-001", skills=["Python", "Go"])
    assert claim(service, "SKILL-LANGUAGES-001")["approved_facts"][-1] == "Bash"
    # A skill moved onto the never-claim list leaves every resume at once.
    never = entry(service, "SKILL-NEVER-001")
    edit(service, "SKILL-NEVER-001", skills=[*never["data"]["approved_facts"], "Java"])
    assert "Java" not in extract_zero_argument_macros(text(service, TEMPLATE))["SkillsLanguages"]
    assert "AUTOMATED_PASS" in resume_check(service.w.root)


def test_a_new_role_is_registered_placed_newest_first_and_reimported(service, tmp_path):
    saved = service.save_knowledge({"kind": "experience", "title": "x", "fields": {
        "title": "Data Engineer", "employer": "Acme Corp", "dates": "Oct 2026 - Present",
        "location": "Remote", "bullets": ["Built ingestion jobs in Airflow"]}})
    registry_id = saved["data"]["id"]
    assert registry_id.startswith("EXP-USER-") and claim(service, registry_id)["employer"] == "Acme Corp"
    template = text(service, TEMPLATE)
    experience = template.split("\\section{Professional Experience}")[1]
    assert experience.index("Acme Corp") < experience.index("InsOps Inc.")
    assert workspace_failures(service.w.root) == []
    # career.db is not in git: a fresh database imports the same profile from the files.
    again = next(i for i in reimported(service, tmp_path).knowledge() if i["id"] == registry_id)
    assert again["title"] == "Data Engineer" and again["kind"] == "experience"


def test_removal_takes_an_entry_off_resumes_and_required_entries_stay(service, tmp_path):
    removed = service.delete_knowledge("SKILL-GENAI-001", propagate=True)
    assert removed["synced"]["updated"][-1] == "Base resume"
    held = claim(service, "SKILL-GENAI-001")
    assert held["status"] == "hold" and held["profile_removed"] == "2026-09-12"
    template = text(service, TEMPLATE)
    assert "SKILL-GENAI-001" not in template
    assert "LangChain" not in extract_zero_argument_macros(template)["SkillsML"]
    assert "% EVIDENCE: SKILL-ML-001 SKILL-CV-TOOLS-001 SKILL-DATA-TOOLS-001\n\\newcommand{\\SkillsML}" in template
    # A project in a resume slot is replaced by the next ranked one, at the same length.
    service.delete_knowledge("PROJ-P04-NEWS-RAG", propagate=True)
    macros = extract_zero_argument_macros(text(service, TEMPLATE))
    assert macros["SecondProjectID"] not in ("PROJ-P04-NEWS-RAG", macros["SelectedProjectID"])
    assert "PROJ-P04-NEWS-RAG" not in text(service, TEMPLATE)
    assert workspace_failures(service.w.root) == []
    assert "AUTOMATED_PASS" in resume_check(service.w.root)
    # Entries every resume prints can be edited, never removed.
    files = {f: text(service, f) for f in ("data/context/evidence.yml", TEMPLATE)}
    for locked in ("EXP-INSOPS-001", "personal:email"):
        with pytest.raises(ValueError, match="can't be removed"):
            service.delete_knowledge(locked, propagate=True)
    assert {f: text(service, f) for f in files} == files
    assert entry(service, "EXP-INSOPS-001")["deleted"] == 0
    # A fresh database keeps the removed entry removed.
    assert "SKILL-GENAI-001" not in {i["id"] for i in reimported(service, tmp_path).knowledge()}


def test_open_drafts_follow_and_sent_ones_never_change(service):
    open_job, sent_job = add(service.w, "1"), add(service.w, "2", "Analytics Engineer")
    for job in (open_job, sent_job):
        service.w.prepare(job["id"])
    studio = ResumeStudio(service)
    draft = studio.open(open_job["id"])
    with service.w.connect() as db:
        db.execute("UPDATE jobs SET status='applied' WHERE id=?", (sent_job["id"],))
    sent_folder = service.w.current_folder(sent_job["id"])
    sent_before = (sent_folder / "resume.tex").read_text(encoding="utf-8")
    saved = edit(service, "EXP-TA-001", location="Fayetteville, Arkansas")
    assert saved["synced"]["drafts"] == [{"job_id": open_job["id"], "company": "Example employer", "title": "Data Engineer"}]
    updated = studio.get(open_job["id"])
    assert updated["revision"] == draft["revision"] + 1 and "{Fayetteville, Arkansas}" in updated["source"]
    assert updated["profile_revision"] == service.w.evidence()["candidate_revision"]
    assert not any("older evidence revision" in w for w in updated["warnings"])
    mapping = yaml.safe_load((service.w.current_folder(open_job["id"]) / "evidence-map.yml").read_text(encoding="utf-8"))
    assert mapping["candidate_revision"] == service.w.evidence()["candidate_revision"]
    # What was sent stays exactly as sent.
    assert (sent_folder / "resume.tex").read_text(encoding="utf-8") == sent_before


def test_suggestions_wait_for_confirm_and_a_failed_write_changes_nothing(service, monkeypatch):
    old = entry(service, "EXP-TA-001")
    files = {f: text(service, f) for f in ("data/context/evidence.yml", "data/config/profile.yml", TEMPLATE)}
    # A chat or CLI save (no form fields) waits for review and leaves the files alone.
    service.save_knowledge({**old, "summary": "Ran weekly C++ lab sections"}, old["id"])
    assert service.profile_dirty() and {f: text(service, f) for f in files} == files
    # A write that fails half way puts every file back and keeps the database as it was.
    real = profile_sync.atomic_write
    def failing(path, content):
        if path.name == "resume-base.tex":
            raise OSError("disk full")
        real(path, content)
    monkeypatch.setattr(profile_sync, "atomic_write", failing)
    with pytest.raises(OSError):
        service.reconcile_knowledge([old["id"]])
    assert {f: text(service, f) for f in files} == files and service.profile_dirty()
    monkeypatch.setattr(profile_sync, "atomic_write", real)
    confirmed = service.reconcile_knowledge([old["id"]])
    assert confirmed["synced"]["updated"][-1] == "Base resume" and not service.profile_dirty()
    assert claim(service, "EXP-TA-001")["approved_facts"] == ["Ran weekly C++ lab sections"]
    assert "\\item Ran weekly C++ lab sections" in text(service, TEMPLATE)


def test_the_profile_api_removes_keeps_and_explains(service):
    app = create_app(service.w.root)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.delete("/api/v2/profile/items/EXP-INSOPS-001").status_code == 400
        removed = client.delete("/api/v2/profile/items/SKILL-GENAI-001")
        assert removed.status_code == 200 and removed.json()["synced"]["revision"] == "2026-09-12.1"
        assert "SKILL-GENAI-001" not in text(service, TEMPLATE)
        # A removal suggested outside the page waits; "Keep it" brings the entry back.
        service.delete_knowledge("LANG-001")
        kept = client.post("/api/v2/profile/items/LANG-001/restore")
        assert kept.status_code == 200 and kept.json()["reconciled"] == ["LANG-001"]
        assert entry(service, "LANG-001")["deleted"] == 0 and not service.profile_dirty()
        # A suggested project that is only a sentence is kept, and the page says why it is off resumes.
        idea = service.save_knowledge({"kind": "project", "title": "Campus meter pipeline",
                                       "summary": "Moved meter readings through Kafka into PostgreSQL."})
        confirmed = client.post("/api/v2/profile/reconcile", json={"ids": [idea["id"]]}).json()
        assert "add at least one technology" in confirmed["synced"]["notes"][0]
        shown = {i["id"]: i for i in client.get("/api/v2/profile").json()["items"]}
        assert "add at least one technology" in shown[idea["id"]]["off_resumes"]
        assert shown["PROJ-P01-IOT"]["off_resumes"] is None
