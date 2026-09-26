from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "backend/scripts/validate_batch.py"
RESUME_VALIDATOR = ROOT / "backend/scripts/validate_resume.py"
RUNNER = ROOT / "backend/scripts/run_resume_batch.py"
# The batch gates compare against the live registry, so the fixtures follow it.
CANDIDATE_REVISION = re.search(
    r"(?m)^candidate_revision:\s*\"?([^\"\s]+)",
    (ROOT / "data/context/evidence.yml").read_text(encoding="utf-8"),
).group(1)
REQUIRED_ARTIFACTS = [
    "job-description.md",
    "evaluation.md",
    "company-research.md",
    "study-plan.md",
    "evidence-map.yml",
    "resume.tex",
    "resume.pdf",
    "resume-preview/page-01.png",
    "qa.json",
]

sys.path.insert(0, str(ROOT / "backend/scripts"))
from validate_resume import evidence_ids_from_source, load_yaml  # noqa: E402


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BatchQualityTests(unittest.TestCase):
    def run_manifest(self, content: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "batch.yml"
            manifest.write_text(textwrap.dedent(content), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(VALIDATOR), str(manifest)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_batch_requires_at_least_two_jobs(self) -> None:
        result = self.run_manifest(
            f"""
            candidate_revision: "{CANDIDATE_REVISION}"
            jobs:
              - job_id: "one"
                status: "rejected"
                reason: "Outside profile"
            """
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2-10 jobs", result.stdout)

    def test_terminal_policy_rejections_are_valid_batch_outcomes(self) -> None:
        result = self.run_manifest(
            f"""
            candidate_revision: "{CANDIDATE_REVISION}"
            jobs:
              - job_id: "one"
                status: "rejected"
                reason: "AI engineering role"
              - job_id: "two"
                status: "rejected"
                reason: "Expired posting"
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_duplicate_job_ids_fail_cross_batch_gate(self) -> None:
        result = self.run_manifest(
            f"""
            candidate_revision: "{CANDIDATE_REVISION}"
            jobs:
              - job_id: "duplicate"
                status: "rejected"
                reason: "Outside profile"
              - job_id: "duplicate"
                status: "rejected"
                reason: "Expired"
            """
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate batch job IDs", result.stdout)

    @unittest.skipUnless(
        shutil.which("tectonic") and (shutil.which("swiftc") or shutil.which("swift")),
        "Tectonic and Swift/PDFKit are required for release-level batch integration",
    )
    def test_two_worker_happy_path_and_all_stale_artifact_hashes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            jobs = [
                self.build_passed_artifact(run_dir, "001-acme-bi-analyst", "Acme"),
                self.build_passed_artifact(run_dir, "002-beta-data-analyst", "Beta"),
            ]
            manifest = run_dir / "batch.yml"
            manifest.write_text(
                json.dumps(
                    {
                        "run_id": "batch-test",
                        "candidate_revision": CANDIDATE_REVISION,
                        "required_artifacts": REQUIRED_ARTIFACTS,
                        "jobs": jobs,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(VALIDATOR), str(manifest)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PASS: jobs=2", result.stdout)

            stale_source = Path(jobs[0]["artifact_dir"]) / "resume.tex"
            stale_source.write_text(
                stale_source.read_text(encoding="utf-8") + "\n% changed after QA\n",
                encoding="utf-8",
            )
            stale_artifact = Path(jobs[0]["artifact_dir"])
            stale_jd = stale_artifact / "job-description.md"
            stale_jd.write_text(
                stale_jd.read_text(encoding="utf-8") + "\nChanged after snapshot.\n",
                encoding="utf-8",
            )
            stale_map = stale_artifact / "evidence-map.yml"
            stale_map.write_text(
                stale_map.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            with (stale_artifact / "resume.pdf").open("ab") as stream:
                stream.write(b"\n% changed after QA\n")
            with (stale_artifact / "resume-preview/page-01.png").open("ab") as stream:
                stream.write(b"changed-after-QA")
            stale_result = subprocess.run(
                [sys.executable, str(VALIDATOR), str(manifest)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
            self.assertNotEqual(stale_result.returncode, 0)
            self.assertIn("source hash is stale", stale_result.stdout)
            self.assertIn("PDF hash is stale", stale_result.stdout)
            self.assertIn("evidence map hash is stale", stale_result.stdout)
            self.assertIn("JD snapshot hash is stale", stale_result.stdout)
            self.assertIn("preview hashes are stale", stale_result.stdout)
            self.assertIn("Stored preview is stale", stale_result.stdout)

    def test_runner_rejections_are_atomic_resumable_and_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            worker = run_dir / "reject_worker.py"
            worker.write_text(
                textwrap.dedent(
                    """
                    import json
                    from pathlib import Path

                    Path("worker-result.json").write_text(
                        json.dumps({"status": "rejected", "reason": "Policy-ineligible role"}),
                        encoding="utf-8",
                    )
                    """
                ),
                encoding="utf-8",
            )
            manifest, artifact_dirs = self.build_runner_manifest(
                run_dir,
                [sys.executable, str(worker)],
            )

            first = subprocess.run(
                [sys.executable, str(RUNNER), str(manifest), "--retry-limit", "0"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            first_state = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(
                [job["status"] for job in first_state["jobs"]],
                ["rejected", "rejected"],
            )
            self.assertEqual([job["attempts"] for job in first_state["jobs"]], [1, 1])
            for artifact_dir in artifact_dirs:
                self.assertTrue((artifact_dir / "worker.log").is_file())

            second = subprocess.run(
                [sys.executable, str(RUNNER), str(manifest), "--retry-limit", "0"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            second_state = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual([job["attempts"] for job in second_state["jobs"]], [1, 1])
            self.assertIn("SKIP:", second.stdout)

    def test_runner_stops_at_the_bounded_retry_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            worker = run_dir / "failing_worker.py"
            worker.write_text("raise SystemExit(7)\n", encoding="utf-8")
            manifest, _ = self.build_runner_manifest(
                run_dir,
                [sys.executable, str(worker)],
            )
            result = subprocess.run(
                [sys.executable, str(RUNNER), str(manifest), "--retry-limit", "1"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            state = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual([job["status"] for job in state["jobs"]], ["failed", "failed"])
            self.assertEqual([job["attempts"] for job in state["jobs"]], [2, 2])

    def build_passed_artifact(
        self,
        run_dir: Path,
        job_id: str,
        company: str,
    ) -> dict[str, object]:
        artifact_dir = run_dir / job_id
        artifact_dir.mkdir()
        source_path = artifact_dir / "resume.tex"
        shutil.copy2(ROOT / "data/templates/resume-base.tex", source_path)
        jd_path = artifact_dir / "job-description.md"
        jd_path.write_text(
            f"# {company} Data Engineer\n\nKafka, Flink, Airflow, SQL and data validation.\n",
            encoding="utf-8",
        )
        (artifact_dir / "evaluation.md").write_text(
            "# Evaluation\n\nEligible Data Engineer role with supported requirements.\n",
            encoding="utf-8",
        )
        (artifact_dir / "study-plan.md").write_text(
            "# Study plan\n\nThe wall: nothing here goes on the resume until learned.\n",
            encoding="utf-8",
        )
        (artifact_dir / "company-research.md").write_text(
            "# Company research\n\nLow-latency device telemetry is the selected problem.\n",
            encoding="utf-8",
        )

        source = source_path.read_text(encoding="utf-8")
        evidence = load_yaml(ROOT / "data/context/evidence.yml")
        evidence_failures: list[str] = []
        source_ids_result = evidence_ids_from_source(
            source,
            evidence,
            evidence_failures,
        )
        source_evidence_ids = (
            source_ids_result[0]
            if isinstance(source_ids_result, tuple)
            else source_ids_result
        )
        evidence_ids = sorted(source_evidence_ids)
        self.assertFalse(evidence_failures)
        evidence_map = {
            "candidate_revision": CANDIDATE_REVISION,
            "job_id": job_id,
            "role_eligible": True,
            "job_snapshot_sha256": file_sha256(jd_path),
            "supported_requirement_coverage": 100,
            "requirements": [
                {
                    "id": "R01",
                    "priority": "required",
                    "text": "Kafka and Flink streaming pipelines",
                    "evidence_ids": ["PROJ-P01-IOT"],
                    "strength": "strong",
                }
            ],
            "company_problem": {
                "statement": (
                    f"{company} needs low-latency telemetry pipelines with event-time correctness."
                ),
                "source_url": "https://flink.apache.org/",
                "published_or_accessed": "2026-07-27",
                "evidence_class": "inferred",
                "confidence": "medium",
            },
            "selected_project_id": "PROJ-P01-IOT",
            "selected_project_ids": ["PROJ-P01-IOT", "PROJ-P04-NEWS-RAG"],
            "selected_project_reason": (
                "The streaming platform is the closest supported analogue for this role."
            ),
            "resume_claim_ids": evidence_ids,
            "held_claims_used": [],
        }
        evidence_map_path = artifact_dir / "evidence-map.yml"
        evidence_map_path.write_text(
            json.dumps(evidence_map, indent=2),
            encoding="utf-8",
        )
        qa_path = artifact_dir / "qa.json"
        validation = subprocess.run(
            [
                "python3",
                str(RESUME_VALIDATOR),
                str(source_path),
                "--compile",
                "--output",
                str(artifact_dir / "resume.pdf"),
                "--render-dir",
                str(artifact_dir / "resume-preview"),
                "--qa-json",
                str(qa_path),
                "--evidence-map",
                str(evidence_map_path),
                "--visual-review",
                "pass",
                "--visual-reviewer",
                "Batch Test",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
        self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        self.assertEqual(qa["status"], "PASS")
        return {
            "job_id": job_id,
            "artifact_dir": str(artifact_dir),
            "snapshot_path": str(jd_path),
            "snapshot_sha256": file_sha256(jd_path),
            "status": "passed",
            "attempts": 1,
            "candidate_revision_at_completion": CANDIDATE_REVISION,
            "selected_project_id": "PROJ-P01-IOT",
            "page_count": 1,
            "supported_requirement_coverage": 100,
        }

    def build_runner_manifest(
        self,
        run_dir: Path,
        worker_command: list[str],
    ) -> tuple[Path, list[Path]]:
        artifact_dirs: list[Path] = []
        jobs = []
        for index in (1, 2):
            artifact_dir = run_dir / f"job-{index}"
            artifact_dir.mkdir()
            artifact_dirs.append(artifact_dir)
            snapshot = artifact_dir / "job-description.md"
            snapshot.write_text(
                f"# Job {index}\n\nA distinct verified role snapshot.\n",
                encoding="utf-8",
            )
            jobs.append(
                {
                    "job_id": f"job-{index:03d}",
                    "artifact_dir": str(artifact_dir),
                    "snapshot_path": str(snapshot),
                    "snapshot_sha256": file_sha256(snapshot),
                    "status": "pending",
                    "attempts": 0,
                }
            )
        manifest = run_dir / "batch.yml"
        manifest.write_text(
            json.dumps(
                {
                    "run_id": "runner-test",
                    "candidate_revision": CANDIDATE_REVISION,
                    "required_artifacts": REQUIRED_ARTIFACTS,
                    "worker_command": worker_command,
                    "jobs": jobs,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return manifest, artifact_dirs


if __name__ == "__main__":
    unittest.main()
