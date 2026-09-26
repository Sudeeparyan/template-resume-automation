"""Source versions and build rollback use disposable profile folders."""

import json
import sqlite3
from contextlib import closing
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.intake.job import IntakeJob  # noqa: E402
from backend.services.intake.runs import BuildRuns  # noqa: E402
from backend.services.intake.extract import Block  # noqa: E402
from backend.services.source_library import SourceLibrary  # noqa: E402


class SourceLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "profile"
        self.root.mkdir()
        self.library = SourceLibrary(self.root)

    def test_versions_are_immutable_and_only_active_version_is_materialized(self):
        first = self.library.add_upload("Resume.md", b"First evidence\n")
        source_id = first["id"]
        self.library.add_version(source_id, b"Second evidence\n")
        listing = self.library.list()[0]
        self.assertEqual(listing["current_version"], 2)
        self.assertEqual(len(listing["versions"]), 2)
        self.assertEqual(self.library._version_path(listing, 1).read_bytes(), b"First evidence\n")
        self.assertEqual((self.root / "data/context/files/Resume.md").read_bytes(), b"Second evidence\n")
        self.library.set_active(source_id, False)
        self.assertFalse((self.root / "data/context/files/Resume.md").exists())
        self.assertEqual(self.library._version_path(listing, 2).read_bytes(), b"Second evidence\n")

    def test_note_index_keeps_version_and_citation(self):
        note = self.library.add_note("Career notes", "I worked in Cork.")
        self.library.capture_extraction([Block("P001", "paragraph", "I worked in Cork.", note["name"])])
        result = self.library.search("Cork")
        self.assertEqual([(item["source_id"], item["version"], item["block_id"]) for item in result],
                         [(note["id"], 1, "P001")])
        version_markdown = self.root / "data/source_library/files" / note["id"] / "v0001.md"
        self.assertIn("[P001] I worked in Cork.", version_markdown.read_text(encoding="utf-8"))
        self.assertEqual(self.library.list()[0]["versions"][0]["extracted_chars"], len("I worked in Cork."))

    def test_file_swap_rolls_back_when_second_replacement_fails(self):
        job = IntakeJob(self.root)
        old = self.root / "data/config/profile.yml"
        old.parent.mkdir(parents=True)
        old.write_text("old", encoding="utf-8")
        target = self.root / "data/context/evidence.yml"
        original_replace = Path.replace

        def fail_second(path, destination):
            if Path(destination) == target:
                raise OSError("simulated swap failure")
            return original_replace(path, destination)

        from unittest.mock import patch

        with patch.object(Path, "replace", fail_second):
            with self.assertRaisesRegex(OSError, "simulated"):
                job._commit_file_set({"data/config/profile.yml": "new", "data/context/evidence.yml": "new"})
        self.assertEqual(old.read_text(encoding="utf-8"), "old")
        self.assertFalse(target.exists())


class FakeProfiles:
    def __init__(self, state="ready"):
        self.value = {"id": "candidate", "name": "Candidate", "country": "ie", "state": state,
                      "built_at": "yesterday"}

    def get(self, _profile_id):
        return dict(self.value)

    def update(self, _profile_id, **fields):
        self.value.update(fields)
        return dict(self.value)


class FakeApps:
    def __init__(self):
        self.closed = 0
        self.opened = 0

    def close(self, _profile_id):
        self.closed += 1

    def app(self, _profile_id):
        self.opened += 1


class BuildRunTests(unittest.TestCase):
    def test_failed_finish_restores_previous_profile_and_job_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "data/config/profile.yml"
            old.parent.mkdir(parents=True)
            old.write_text("candidate_revision: old\n", encoding="utf-8")
            db = root / "data/career.db"
            with closing(sqlite3.connect(db)) as connection:
                connection.execute("CREATE TABLE jobs (id TEXT)")
                connection.execute("INSERT INTO jobs VALUES ('exact-job-id')")
                connection.commit()
            job = IntakeJob(root)
            job.library.add_note("About", "Candidate information")
            job.start = lambda: setattr(job, "thread", threading.Thread(target=lambda: None)) or job.thread.start()
            job.state = lambda: {"state": "review", "steps": []}
            job.draft = lambda: {"contact": {"full_name": "Candidate"}, "targets": {"roles": ["Analyst"]},
                                 "authorization": {}, "questions": []}
            job.update = lambda _changes: None

            def write_new():
                old.write_text("candidate_revision: new\n", encoding="utf-8")
                with closing(sqlite3.connect(db)) as connection:
                    connection.execute("DELETE FROM jobs")
                    connection.commit()
                return {"files": ["data/config/profile.yml"], "pack": "ie", "built_at": "now", "revision": "new"}

            job.build = write_new
            profiles = FakeProfiles()
            apps = FakeApps()
            runner = BuildRuns("candidate", job, profiles, apps,
                               lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("resume failed")))
            run = runner.start({"target_markets": ["ie"]})
            runner.thread.join(timeout=5)
            self.assertEqual(runner.get(run["id"])["status"], "failed")
            self.assertEqual(old.read_text(encoding="utf-8"), "candidate_revision: old\n")
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute("SELECT id FROM jobs").fetchone()[0], "exact-job-id")
            self.assertEqual(profiles.get("candidate")["state"], "ready")
            self.assertGreaterEqual(apps.opened, 1)


if __name__ == "__main__":
    unittest.main()
