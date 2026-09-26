from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend/scripts")]
VALIDATOR = ROOT / "backend/scripts/validate_resume.py"
BASE = ROOT / "data/templates/resume-base.tex"
# The validator compares against the live registry, so the fixture follows it.
CANDIDATE_REVISION = re.search(
    r"(?m)^candidate_revision:\s*\"?([^\"\s]+)",
    (ROOT / "data/context/evidence.yml").read_text(encoding="utf-8"),
).group(1)
PDF_INTEGRATION_AVAILABLE = bool(
    shutil.which("tectonic") and (shutil.which("swiftc") or shutil.which("swift"))
)


class ResumeQualityTests(unittest.TestCase):
    def run_validator(self, source: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(source), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def make_tailored_fixture(
        self,
        directory: Path,
        source_text: str | None = None,
        selected_project_id: str = "PROJ-P01-IOT",
    ) -> tuple[Path, Path, Path]:
        source_text = source_text or BASE.read_text(encoding="utf-8")
        source = directory / "resume.tex"
        source.write_text(source_text, encoding="utf-8")

        job_description = directory / "job-description.md"
        job_description.write_text(
            "# Job Description\n\n"
            "The Data Engineer will build streaming pipelines with Kafka and Flink, "
            "orchestrate Airflow jobs on AWS and validate clinical device telemetry.\n",
            encoding="utf-8",
        )
        job_hash = hashlib.sha256(job_description.read_bytes()).hexdigest()
        source_ids = sorted(
            {
                evidence_id
                for match in re.finditer(
                    r"(?m)^\s*%\s*EVIDENCE:\s*(.*?)\s*$",
                    source_text,
                )
                for evidence_id in match.group(1).split()
            }
        )
        claim_list = "\n".join(f'  - "{evidence_id}"' for evidence_id in source_ids)

        from validate_resume import extract_zero_argument_macros
        second_id = extract_zero_argument_macros(source_text).get('SecondProjectID')
        evidence_map = directory / "evidence-map.yml"
        evidence_map.write_text(
            f'candidate_revision: "{CANDIDATE_REVISION}"\n'
            'job_id: "001-data-engineer-test"\n'
            "role_eligible: true\n"
            f'job_snapshot_sha256: "{job_hash}"\n'
            "supported_requirement_coverage: 75.0\n"
            "requirements:\n"
            '  - id: "R01"\n'
            '    priority: "required"\n'
            '    text: "Kafka and Flink streaming pipelines"\n'
            "    evidence_ids:\n"
            '      - "PROJ-P01-IOT"\n'
            '    strength: "strong"\n'
            '  - id: "R02"\n'
            '    priority: "preferred"\n'
            '    text: "ClickHouse or another OLAP store"\n'
            "    evidence_ids:\n"
            '      - "SKILL-STREAMING-001"\n'
            '    strength: "partial"\n'
            "company_problem:\n"
            '  statement: "The role requires low-latency telemetry pipelines with '
            'event-time correctness."\n'
            '  source_url: "https://www.example-devices.com/careers"\n'
            '  published_or_accessed: "2026-09-17"\n'
            '  evidence_class: "explicit"\n'
            '  confidence: "high"\n'
            f'selected_project_ids: ["{selected_project_id}", "{second_id}"]\n'
            f'selected_project_id: "{selected_project_id}"\n'
            'selected_project_reason: "The streaming platform directly '
            'demonstrates event-time processing and OLAP schema design."\n'
            "resume_claim_ids:\n"
            f"{claim_list}\n"
            "held_claims_used: []\n",
            encoding="utf-8",
        )
        return source, evidence_map, job_description

    def registered_projects(self) -> list[dict[str, object]]:
        program = (
            'require "yaml"; require "json"; '
            "data = YAML.safe_load(File.read(ARGV.fetch(0)), aliases: false); "
            'STDOUT.write(JSON.generate(data.fetch("projects")))'
        )
        result = subprocess.run(
            ["ruby", "-e", program, str(ROOT / "data/context/evidence.yml")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)

    def source_with_project(self, project: dict[str, object]) -> str:
        from backend.services.resume_projects import install_project
        source = install_project(BASE.read_text(), project)
        # Supporting project: the short two-bullet Expense Tracker, or the news RAG when that is the signature.
        second_id = 'PROJ-P06-EXPENSE' if project['id'] != 'PROJ-P06-EXPENSE' else 'PROJ-P04-NEWS-RAG'
        second = next(p for p in self.registered_projects() if p['id'] == second_id)
        return install_project(source, second, second=True)

    def test_base_resume_passes_static_contract(self) -> None:
        result = self.run_validator(BASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AUTOMATED_PASS_MANUAL_PENDING", result.stdout)

    def test_valid_tailored_fixture_passes_static_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, evidence_map, _ = self.make_tailored_fixture(Path(temporary))
            result = self.run_validator(
                source,
                "--evidence-map",
                str(evidence_map),
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AUTOMATED_PASS_MANUAL_PENDING", result.stdout)

    def test_supplied_phone_and_full_urls_are_in_the_header(self) -> None:
        result = self.run_validator(BASE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = BASE.read_text()
        for value in ("+1 (479) 301-1366", "https://chetan0159.github.io/Portfolio/", "https://github.com/Chetan0159"):
            self.assertIn(value, text)
        # Never on a resume: a years total, a publication, LinkedIn, a city, a summary.
        for banned in ("years of experience", "ICCV", "linkedin", "Fayetteville, AR \\textbar", "Professional Summary"):
            self.assertNotIn(banned, text)

    def test_unknown_project_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "resume.tex"
            source.write_text(
                BASE.read_text(encoding="utf-8").replace(
                    r"\newcommand{\SelectedProjectID}{PROJ-P01-IOT}",
                    r"\newcommand{\SelectedProjectID}{PROJ-INVENTED}",
                    1,
                ),
                encoding="utf-8",
            )
            result = self.run_validator(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not resume-ready", result.stdout)

    def test_second_project_section_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "resume.tex"
            text = BASE.read_text(encoding="utf-8").replace(
                "\\section{Education}",
                "\\section{Projects}\nInvented duplicate\n\n\\section{Education}",
                1,
            )
            source.write_text(text, encoding="utf-8")
            result = self.run_validator(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one Projects", result.stdout)

    def test_fabricated_registered_project_content_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, evidence_map, _ = self.make_tailored_fixture(Path(temporary))
            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    r"\newcommand{\SelectedProjectTitle}{Real-Time Medical IoT Analytics Platform}",
                    r"\newcommand{\SelectedProjectTitle}{Invented Autonomous Forecasting Platform}",
                    1,
                ),
                encoding="utf-8",
            )
            result = self.run_validator(
                source,
                "--evidence-map",
                str(evidence_map),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Selected project title does not match its registered project ID",
            result.stdout,
        )

    def test_second_project_inside_selected_block_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, evidence_map, _ = self.make_tailored_fixture(Path(temporary))
            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    "% SELECTED_PROJECT_BLOCK_END",
                    "\\textbf{Second Project: Invented Forecasting Work}\\\\\n"
                    "\\begin{resumeitems}\n"
                    "  % EVIDENCE: PROJ-P01-IOT\n"
                    "  \\item Added another visible project inside the selected-project block.\n"
                    "\\end{resumeitems}\n"
                    "% SELECTED_PROJECT_BLOCK_END",
                    1,
                ),
                encoding="utf-8",
            )
            result = self.run_validator(
                source,
                "--evidence-map",
                str(evidence_map),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Selected project block must render only the registered 2-3 project bullets",
            result.stdout,
        )

    def test_layout_overrides_are_rejected(self) -> None:
        mutations = {
            "geometry": (
                r"\usepackage[top=0.5in,bottom=0.5in,left=0.6in,right=0.6in]{geometry}",
                r"\usepackage[top=0.5in,bottom=0.5in,left=0.6in,right=0.6in]{geometry}"
                "\n"
                r"\geometry{top=0.20in,bottom=0.20in,left=0.20in,right=0.20in}",
                "Prohibited layout detected: geometry override",
            ),
            "line-spread": (
                r"\renewcommand{\baselinestretch}{1.0}",
                r"\renewcommand{\baselinestretch}{1.0}"
                "\n"
                r"\renewcommand{\baselinestretch}{0.60}",
                "Resume must preserve the single fixed baselinestretch value",
            ),
        }
        for label, (old, new, expected) in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                source, evidence_map, _ = self.make_tailored_fixture(Path(temporary))
                source.write_text(
                    source.read_text(encoding="utf-8").replace(old, new, 1),
                    encoding="utf-8",
                )
                result = self.run_validator(
                    source,
                    "--evidence-map",
                    str(evidence_map),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stdout)

    def test_visual_pass_requires_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, evidence_map, _ = self.make_tailored_fixture(Path(temporary))
            result = self.run_validator(
                source,
                "--evidence-map",
                str(evidence_map),
                "--visual-review",
                "pass",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "A visual reviewer identity is required for a pass/fail visual review",
            result.stdout,
        )

    def test_stale_job_snapshot_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, evidence_map, job_description = self.make_tailored_fixture(
                Path(temporary)
            )
            job_description.write_text(
                job_description.read_text(encoding="utf-8")
                + "\nThis requirement was added after the evidence map was created.\n",
                encoding="utf-8",
            )
            result = self.run_validator(
                source,
                "--evidence-map",
                str(evidence_map),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Evidence-map job_snapshot_sha256 does not match the saved JD snapshot",
            result.stdout,
        )

    def test_invalid_evidence_map_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, evidence_map, _ = self.make_tailored_fixture(Path(temporary))
            mapping = evidence_map.read_text(encoding="utf-8")
            mapping = mapping.replace("role_eligible: true", "role_eligible: false", 1)
            mapping = mapping.replace(
                "supported_requirement_coverage: 75.0",
                "supported_requirement_coverage: 101",
                1,
            )
            mapping = mapping.replace(
                'source_url: "https://www.example-devices.com/careers"',
                'source_url: "https://example.invalid/problem"',
                1,
            )
            mapping = mapping.replace(
                'published_or_accessed: "2026-09-17"',
                'published_or_accessed: "2026/09/17"',
                1,
            )
            evidence_map.write_text(mapping, encoding="utf-8")
            result = self.run_validator(
                source,
                "--evidence-map",
                str(evidence_map),
            )
        self.assertNotEqual(result.returncode, 0)
        for expected in (
            "Evidence map role_eligible must be true",
            "supported_requirement_coverage must be a number from 0 to 100",
            "company_problem.source_url must be a real HTTP(S) source",
            "company_problem.published_or_accessed must be YYYY-MM-DD",
        ):
            self.assertIn(expected, result.stdout)

    def test_tailored_copy_requires_evidence_map(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "resume.tex"
            source.write_text(BASE.read_text(encoding="utf-8"), encoding="utf-8")
            result = self.run_validator(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing evidence map", result.stdout)

    @unittest.skipUnless(PDF_INTEGRATION_AVAILABLE, "PDF integration tools are required")
    def test_compiled_base_is_release_ready_after_visual_signoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "resume.pdf"
            qa_path = directory / "qa.json"
            result = self.run_validator(
                BASE,
                "--compile",
                "--output",
                str(output),
                "--qa-json",
                str(qa_path),
                "--visual-review",
                "pass",
                "--visual-reviewer",
                "Automated test reviewer",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertEqual(qa["status"], "PASS")
            self.assertTrue(qa["release_ready"])
            self.assertEqual(qa["page_count"], 1)
            self.assertEqual(qa["project_count"], 2)
            self.assertTrue(qa["pdf_text_extractable"])
            self.assertEqual(qa["overflow_count"], 0)
            self.assertTrue(output.is_file())

    @unittest.skipUnless(PDF_INTEGRATION_AVAILABLE, "PDF integration tools are required")
    def test_valid_tailored_fixture_is_release_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, evidence_map, _ = self.make_tailored_fixture(directory)
            output = directory / "resume.pdf"
            qa_path = directory / "qa.json"
            result = self.run_validator(
                source,
                "--compile",
                "--output",
                str(output),
                "--qa-json",
                str(qa_path),
                "--evidence-map",
                str(evidence_map),
                "--visual-review",
                "pass",
                "--visual-reviewer",
                "Automated test reviewer",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertEqual(qa["status"], "PASS")
            self.assertTrue(qa["release_ready"])
            self.assertTrue(qa["published_pdf"])
            self.assertTrue(qa["selected_project"]["content_matches_registry"])
            self.assertTrue(qa["evidence_map"]["all_candidate_claims_grounded"])
            self.assertEqual(
                set(qa["preview_sha256"]),
                {"page-01.png"},
            )
            self.assertTrue(output.is_file())

    @unittest.skipUnless(PDF_INTEGRATION_AVAILABLE, "PDF integration tools are required")
    def test_every_registered_project_variant_compiles_to_one_page(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projects = self.registered_projects()
            self.assertGreaterEqual(len(projects), 10)
            for project in projects:
                project_id = str(project["id"])
                with self.subTest(project=project_id):
                    directory = root / project_id
                    directory.mkdir()
                    source, evidence_map, _ = self.make_tailored_fixture(
                        directory,
                        self.source_with_project(project),
                        project_id,
                    )
                    output = directory / "resume.pdf"
                    qa_path = directory / "qa.json"
                    result = self.run_validator(
                        source,
                        "--compile",
                        "--output",
                        str(output),
                        "--qa-json",
                        str(qa_path),
                        "--evidence-map",
                        str(evidence_map),
                        "--visual-review",
                        "pass",
                        "--visual-reviewer",
                        "Automated project-variant test",
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"{project_id}\n{result.stdout}{result.stderr}",
                    )
                    qa = json.loads(qa_path.read_text(encoding="utf-8"))
                    self.assertEqual(qa["status"], "PASS")
                    self.assertEqual(qa["selected_project_id"], project_id)
                    self.assertEqual(qa["page_count"], 1)
                    self.assertTrue(
                        qa["selected_project"]["content_matches_registry"]
                    )

    @unittest.skipUnless(PDF_INTEGRATION_AVAILABLE, "PDF integration tools are required")
    def test_failed_validation_does_not_overwrite_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, evidence_map, _ = self.make_tailored_fixture(directory)
            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    r"\newcommand{\SelectedProjectTitle}{Real-Time Medical IoT Analytics Platform}",
                    r"\newcommand{\SelectedProjectTitle}{Invented Forecasting Platform}",
                    1,
                ),
                encoding="utf-8",
            )
            output = directory / "resume.pdf"
            sentinel = b"known-good-pdf"
            output.write_bytes(sentinel)
            qa_path = directory / "qa.json"
            result = self.run_validator(
                source,
                "--compile",
                "--output",
                str(output),
                "--qa-json",
                str(qa_path),
                "--evidence-map",
                str(evidence_map),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), sentinel)
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            self.assertEqual(qa["status"], "FAIL")
            self.assertFalse(qa["published_pdf"])


if __name__ == "__main__":
    unittest.main()
