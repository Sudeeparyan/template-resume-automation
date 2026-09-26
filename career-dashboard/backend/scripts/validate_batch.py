#!/usr/bin/env python3
"""Fail-closed validation for a completed 2-10 resume batch."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from validate_resume import (
    REQUIRED_SECTIONS,
    UNSAFE_PATTERNS,
    inspect_pdf,
    load_yaml,
    sha256,
)


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.resume_contract import load_contract  # noqa: E402

CONTRACT = load_contract()
RESUME_VALIDATOR = ROOT / "backend/scripts/validate_resume.py"
# The artifacts a released application folder must hold (profile.yml batch_contract).
REQUIRED_PASSED_ARTIFACTS = tuple(CONTRACT.required_artifacts) or (
    "job-description.md",
    "evaluation.md",
    "company-research.md",
    "study-plan.md",
    "evidence-map.yml",
    "resume.tex",
    "resume.pdf",
    "resume-preview/page-01.png",
    "qa.json",
)
EXPECTED_PREVIEWS = tuple(CONTRACT.preview_files)
# Stable facts every released resume must show, derived from the registry's immutable claims.
VISIBLE_INVARIANTS = tuple(CONTRACT.visible_invariants)
TERMINAL_STATUSES = {"passed", "rejected"}


def resolve_path(value: str, manifest_path: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    run_relative = (manifest_path.parent / path).resolve()
    if run_relative.exists():
        return run_relative
    return (ROOT / path).resolve()


def normalize_pdf_text(text: str) -> str:
    normalized = (
        text.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .replace("\u00a0", " ")
    )
    return re.sub(r"\s+", " ", normalized).strip()


def inspection_text(inspection: dict[str, Any]) -> str:
    pages = inspection.get("pages")
    if not isinstance(pages, list):
        return ""
    return "\n".join(
        str(page.get("text", "")) for page in pages if isinstance(page, dict)
    )


def same_path(recorded: Any, expected: Path) -> bool:
    if not isinstance(recorded, str) or not recorded.strip():
        return False
    try:
        return Path(recorded).expanduser().resolve() == expected.resolve()
    except OSError:
        return False


def valid_review_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def compute_artifact_hashes(artifact_dir: Path) -> dict[str, Any]:
    preview_dir = artifact_dir / "resume-preview"
    return {
        "source_sha256": sha256(artifact_dir / "resume.tex"),
        "pdf_sha256": sha256(artifact_dir / "resume.pdf"),
        "job_snapshot_sha256": sha256(artifact_dir / "job-description.md"),
        "evidence_map_sha256": sha256(artifact_dir / "evidence-map.yml"),
        "preview_sha256": {
            filename: sha256(preview_dir / filename)
            for filename in EXPECTED_PREVIEWS
            if (preview_dir / filename).is_file()
        },
    }


def inspect_published_pdf(
    pdf_path: Path,
    preview_dir: Path,
    project_title: str | None,
    failures: list[str],
) -> tuple[str, dict[str, str]]:
    """Inspect the published bytes and prove that its previews were rendered from them."""
    preview_hashes = {
        filename: sha256(preview_dir / filename)
        for filename in EXPECTED_PREVIEWS
        if (preview_dir / filename).is_file()
    }
    preview_files = sorted(path.name for path in preview_dir.glob("page-*.png"))
    if preview_files != list(EXPECTED_PREVIEWS):
        failures.append(
            "Preview directory must contain exactly " + ", ".join(EXPECTED_PREVIEWS)
        )

    with tempfile.TemporaryDirectory(prefix="batch-published-pdf-") as temporary:
        rendered_dir = Path(temporary) / "rendered"
        try:
            inspection = inspect_pdf(pdf_path, rendered_dir)
        except (RuntimeError, json.JSONDecodeError, OSError) as exc:
            failures.append(f"Published PDF inspection failed: {exc}")
            return "", preview_hashes

        rendered_files = sorted(path.name for path in rendered_dir.glob("page-*.png"))
        if rendered_files != list(EXPECTED_PREVIEWS):
            failures.append(
                "Published PDF did not render exactly " + ", ".join(EXPECTED_PREVIEWS)
            )
        for filename in EXPECTED_PREVIEWS:
            stored = preview_dir / filename
            rendered = rendered_dir / filename
            if stored.is_file() and rendered.is_file() and sha256(stored) != sha256(rendered):
                failures.append(
                    f"Stored preview is stale or not rendered from resume.pdf: {filename}"
                )

        pages = inspection.get("pages")
        if not isinstance(pages, list):
            pages = []
        page_count = inspection.get("page_count")
        if page_count != CONTRACT.pages:
            failures.append(f"Published PDF must have exactly {CONTRACT.pages} page(s); found {page_count}")
        for page in pages:
            if not isinstance(page, dict):
                continue
            width = float(page.get("width_points", 0))
            height = float(page.get("height_points", 0))
            if not CONTRACT.is_paper(width, height):
                failures.append(
                    f"Published PDF page {page.get('number')} is not {CONTRACT.paper.title()} "
                    f"({width:.1f} x {height:.1f} pt)"
                )
            if int(page.get("text_characters", 0)) < 900:
                failures.append(
                    f"Published PDF page {page.get('number')} is suspiciously sparse"
                )

        metadata = inspection.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        if not (
            metadata.get("author") == CONTRACT.pdf_author
            and str(metadata.get("title", "")).startswith(CONTRACT.pdf_author)
        ):
            failures.append("Published PDF metadata author/title does not match the candidate")

        raw_text = inspection_text(inspection)
        normalized = normalize_pdf_text(raw_text)
        if len(normalized) < 1000:
            failures.append("Published PDF text is missing or not sufficiently extractable")
        for value in VISIBLE_INVARIANTS:
            if value not in normalized:
                failures.append(f"Published PDF is missing visible invariant: {value}")
        for label, pattern in UNSAFE_PATTERNS.items():
            if re.search(pattern, raw_text):
                failures.append(f"Unsafe content appears in published PDF: {label}")

        heading_positions = [normalized.find(heading) for heading in REQUIRED_SECTIONS]
        if not all(
            left >= 0 and right >= 0 and left < right
            for left, right in zip(heading_positions, heading_positions[1:])
        ):
            failures.append("Published PDF headings are not in the expected ATS reading order")
        if project_title and normalized.count(project_title) != 1:
            failures.append(
                "Published PDF must show the registered selected-project title exactly once"
            )
        return normalized, preview_hashes


def revalidate_current_source(
    source_path: Path,
    evidence_map_path: Path,
    failures: list[str],
) -> tuple[dict[str, Any], str]:
    """Run the current validator and compile into temporary, non-release paths."""
    with tempfile.TemporaryDirectory(prefix="batch-source-recheck-") as temporary:
        temporary_path = Path(temporary)
        fresh_pdf = temporary_path / "resume.pdf"
        fresh_preview = temporary_path / "resume-preview"
        fresh_qa_path = temporary_path / "qa.json"
        command = [
            sys.executable,
            str(RESUME_VALIDATOR),
            str(source_path),
            "--compile",
            "--output",
            str(fresh_pdf),
            "--render-dir",
            str(fresh_preview),
            "--qa-json",
            str(fresh_qa_path),
            "--evidence-map",
            str(evidence_map_path),
            "--visual-review",
            "pending",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            failures.append("Fresh source validation timed out")
            return {}, ""

        fresh_qa: dict[str, Any] = {}
        if fresh_qa_path.is_file():
            try:
                loaded = json.loads(fresh_qa_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    fresh_qa = loaded
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                failures.append(f"Fresh QA output is unreadable: {exc}")
        if result.returncode != 0 or fresh_qa.get("failures"):
            detail = "; ".join(str(item) for item in fresh_qa.get("failures", []))
            if not detail:
                detail = (result.stdout + "\n" + result.stderr).strip()[-1500:]
            failures.append(f"Current source failed fresh validation: {detail or 'unknown error'}")
            return fresh_qa, ""
        if not (
            fresh_qa.get("status") == "AUTOMATED_PASS_MANUAL_PENDING"
            and fresh_qa.get("compile_ok") is True
            and fresh_qa.get("page_count") == CONTRACT.pages
            and fresh_qa.get("published_pdf") is True
        ):
            failures.append(f"Fresh QA did not satisfy the automated {CONTRACT.describe_pages()} contract")
        if not fresh_pdf.is_file():
            failures.append("Fresh validation did not produce a PDF")
            return fresh_qa, ""

        comparison_render = temporary_path / "comparison-render"
        try:
            fresh_inspection = inspect_pdf(fresh_pdf, comparison_render)
        except (RuntimeError, json.JSONDecodeError, OSError) as exc:
            failures.append(f"Fresh PDF inspection failed: {exc}")
            return fresh_qa, ""
        return fresh_qa, normalize_pdf_text(inspection_text(fresh_inspection))


def audit_passed_artifact(
    raw_job: dict[str, Any],
    manifest_path: Path,
    candidate_revision: Any,
) -> dict[str, Any]:
    """Validate one passed job without trusting its cached QA assertions."""
    job_id = str(raw_job.get("job_id", "")).strip()
    report: dict[str, Any] = {
        "job_id": job_id,
        "artifact_dir": None,
        "valid": False,
        "failures": [],
        "computed_hashes": {},
        "page_count": None,
        "selected_project_id": None,
        "job_snapshot_sha256": None,
        "_pdf_path": None,
        "_normalized_pdf_text": "",
    }
    failures: list[str] = report["failures"]

    artifact_value = raw_job.get("artifact_dir")
    if not isinstance(artifact_value, str) or not artifact_value.strip():
        failures.append("Passed job needs artifact_dir")
        return report
    artifact_dir = resolve_path(artifact_value, manifest_path)
    report["artifact_dir"] = str(artifact_dir)

    for filename in REQUIRED_PASSED_ARTIFACTS:
        if not (artifact_dir / filename).is_file():
            failures.append(f"Missing artifact: {filename}")
    if failures:
        return report

    source_path = artifact_dir / "resume.tex"
    pdf_path = artifact_dir / "resume.pdf"
    jd_path = artifact_dir / "job-description.md"
    evidence_map_path = artifact_dir / "evidence-map.yml"
    preview_dir = artifact_dir / "resume-preview"
    qa_path = artifact_dir / "qa.json"
    computed_hashes = compute_artifact_hashes(artifact_dir)
    report["computed_hashes"] = computed_hashes
    report["job_snapshot_sha256"] = computed_hashes["job_snapshot_sha256"]
    report["_pdf_path"] = str(pdf_path.resolve())

    try:
        qa_loaded = json.loads(qa_path.read_text(encoding="utf-8"))
        if not isinstance(qa_loaded, dict):
            raise ValueError("qa.json must contain an object")
        qa = qa_loaded
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        failures.append(f"Unable to read qa.json: {exc}")
        return report
    try:
        evidence_map = load_yaml(evidence_map_path)
    except (RuntimeError, json.JSONDecodeError) as exc:
        failures.append(f"Unable to read evidence-map.yml: {exc}")
        return report

    required_qa = (
        qa.get("schema_version") == 2
        and qa.get("status") == "PASS"
        and qa.get("release_ready") is True
        and qa.get("page_count") == CONTRACT.pages
        and qa.get("project_count") == 2
        and qa.get("compile_ok") is True
        and qa.get("published_pdf") is True
        and qa.get("pdf_text_extractable") is True
        and qa.get("required_headings_present") is True
        and qa.get("overflow_count") == 0
        and qa.get("visual_review") == "pass"
        and isinstance(qa.get("visual_reviewer"), str)
        and len(qa["visual_reviewer"].strip()) >= 3
        and valid_review_timestamp(qa.get("visual_reviewed_at"))
        and not qa.get("failures")
    )
    if not required_qa:
        failures.append("qa.json does not satisfy the release contract")

    if not same_path(qa.get("source"), source_path):
        failures.append("qa.json source path does not identify this resume.tex")
    if not same_path(qa.get("pdf"), pdf_path):
        failures.append("qa.json PDF path does not identify this resume.pdf")
    if not same_path(qa.get("render_dir"), preview_dir):
        failures.append("qa.json render path does not identify this resume-preview directory")

    evidence_qa = qa.get("evidence_map")
    if not isinstance(evidence_qa, dict):
        evidence_qa = {}
        failures.append("qa.json is missing evidence-map validation details")
    elif not same_path(evidence_qa.get("path"), evidence_map_path):
        failures.append("qa.json evidence-map path does not identify this evidence-map.yml")

    hash_comparisons = (
        ("source", qa.get("source_sha256"), computed_hashes["source_sha256"]),
        ("PDF", qa.get("pdf_sha256"), computed_hashes["pdf_sha256"]),
        ("compiled PDF", qa.get("compiled_pdf_sha256"), computed_hashes["pdf_sha256"]),
        (
            "evidence map",
            evidence_qa.get("sha256"),
            computed_hashes["evidence_map_sha256"],
        ),
        (
            "JD snapshot",
            evidence_qa.get("job_snapshot_sha256"),
            computed_hashes["job_snapshot_sha256"],
        ),
    )
    for label, recorded, actual in hash_comparisons:
        if recorded != actual:
            failures.append(f"qa.json {label} hash is stale or does not match current bytes")
    if qa.get("preview_sha256") != computed_hashes["preview_sha256"]:
        failures.append("qa.json preview hashes are stale or do not match current PNGs")

    manifest_snapshot = raw_job.get("snapshot_sha256")
    if manifest_snapshot != computed_hashes["job_snapshot_sha256"]:
        failures.append("Manifest JD hash does not match current job-description.md")
    snapshot_value = raw_job.get("snapshot_path")
    if not isinstance(snapshot_value, str) or not same_path(
        str(resolve_path(snapshot_value, manifest_path)), jd_path
    ):
        failures.append("Manifest snapshot_path does not identify this job-description.md")

    if evidence_map.get("job_snapshot_sha256") != computed_hashes["job_snapshot_sha256"]:
        failures.append("Evidence-map JD hash does not match job-description.md")
    if evidence_map.get("candidate_revision") != candidate_revision:
        failures.append("Evidence-map candidate revision does not match batch")
    if evidence_map.get("job_id") != job_id:
        failures.append("Evidence-map job_id does not match manifest")
    if evidence_map.get("held_claims_used") != []:
        failures.append("Evidence map contains held claims")
    if qa.get("candidate_revision") != candidate_revision:
        failures.append("qa.json candidate revision does not match batch")

    project_id = str(evidence_map.get("selected_project_id", ""))
    report["selected_project_id"] = project_id
    if project_id != qa.get("selected_project_id"):
        failures.append("Selected project differs between evidence map and QA")
    manifest_project = raw_job.get("selected_project_id")
    if manifest_project is not None and manifest_project != project_id:
        failures.append("Selected project differs between manifest and artifacts")
    manifest_page_count = raw_job.get("page_count")
    if manifest_page_count is not None and manifest_page_count != CONTRACT.pages:
        failures.append(f"Manifest page_count does not match the {CONTRACT.describe_pages()} artifact")
    manifest_coverage = raw_job.get("supported_requirement_coverage")
    evidence_coverage = evidence_map.get("supported_requirement_coverage")
    if manifest_coverage is not None and manifest_coverage != evidence_coverage:
        failures.append("Requirement coverage differs between manifest and evidence map")
    completion_revision = raw_job.get("candidate_revision_at_completion")
    if completion_revision is not None and completion_revision != candidate_revision:
        failures.append("Job completion candidate revision is stale")

    if pdf_path.stat().st_mtime < source_path.stat().st_mtime:
        failures.append("Published PDF is older than resume.tex")

    project_qa = qa.get("selected_project")
    project_title = (
        str(project_qa.get("title"))
        if isinstance(project_qa, dict) and isinstance(project_qa.get("title"), str)
        else None
    )
    published_text, rendered_preview_hashes = inspect_published_pdf(
        pdf_path,
        preview_dir,
        project_title,
        failures,
    )
    report["_normalized_pdf_text"] = published_text
    if rendered_preview_hashes != computed_hashes["preview_sha256"]:
        failures.append("Preview hashes changed while inspecting the published PDF")

    fresh_qa, fresh_text = revalidate_current_source(
        source_path,
        evidence_map_path,
        failures,
    )
    if fresh_qa:
        if fresh_qa.get("source_sha256") != computed_hashes["source_sha256"]:
            failures.append("Fresh validator source hash does not match current resume.tex")
        fresh_evidence = fresh_qa.get("evidence_map")
        if not isinstance(fresh_evidence, dict) or (
            fresh_evidence.get("sha256") != computed_hashes["evidence_map_sha256"]
        ):
            failures.append("Fresh validator evidence-map hash does not match current bytes")
        if fresh_qa.get("selected_project_id") != project_id:
            failures.append("Fresh validator selected a different project")
    if published_text and fresh_text and published_text != fresh_text:
        failures.append("Published resume.pdf text does not match a fresh compile of resume.tex")
    try:
        final_hashes = compute_artifact_hashes(artifact_dir)
    except OSError as exc:
        failures.append(f"Release artifacts changed or disappeared during validation: {exc}")
    else:
        if final_hashes != computed_hashes:
            failures.append("Release artifacts changed during validation; rerun on stable inputs")

    report["page_count"] = CONTRACT.pages if published_text and not any(
        "must have exactly" in message for message in failures
    ) else None
    report["valid"] = not failures
    return report


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        if os.name != "nt":
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def validate_manifest(manifest_path: Path) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []
    report: dict[str, Any] = {
        "schema_version": 2,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path),
        "candidate_revision": None,
        "jobs": [],
        "counts": {},
        "cross_batch": {},
        "failures": failures,
        "warnings": warnings,
        "status": "FAIL",
    }

    if not manifest_path.is_file():
        failures.append(f"Missing batch manifest: {manifest_path}")
        return report

    try:
        manifest = load_yaml(manifest_path)
        evidence = load_yaml(ROOT / "data/context/evidence.yml")
    except (RuntimeError, json.JSONDecodeError) as exc:
        failures.append(str(exc))
        return report

    jobs = manifest.get("jobs")
    candidate_revision = manifest.get("candidate_revision")
    report["candidate_revision"] = candidate_revision
    if not isinstance(jobs, list) or not 2 <= len(jobs) <= 10:
        failures.append("Batch manifest must contain 2-10 jobs")
        return report
    if candidate_revision != evidence.get("candidate_revision"):
        failures.append("Batch candidate_revision does not match context/evidence.yml")
    declared_contract = manifest.get("required_artifacts")
    if declared_contract is not None and declared_contract != list(REQUIRED_PASSED_ARTIFACTS):
        failures.append("Manifest required_artifacts does not match the canonical nine paths")

    job_ids = [str(job.get("job_id", "")) for job in jobs if isinstance(job, dict)]
    duplicate_job_ids = sorted(
        value for value, count in Counter(job_ids).items() if value and count > 1
    )
    if duplicate_job_ids:
        failures.append(f"Duplicate batch job IDs: {', '.join(duplicate_job_ids)}")

    statuses: Counter[str] = Counter()
    pdf_paths: list[str] = []
    artifact_dirs: list[str] = []
    snapshot_hashes: list[str] = []
    passed_pdf_text: list[tuple[str, str]] = []
    selected_projects: dict[str, str] = {}

    for index, raw_job in enumerate(jobs, start=1):
        if not isinstance(raw_job, dict):
            failures.append(f"Job {index} is not a mapping")
            continue
        job_id = str(raw_job.get("job_id", "")).strip()
        status = str(raw_job.get("status", "")).strip().lower()
        statuses[status or "missing"] += 1
        if not job_id:
            failures.append(f"{index}: Missing job_id")
        if status not in TERMINAL_STATUSES:
            failures.append(
                f"{job_id or index}: Non-terminal or invalid status: {status or '<missing>'}"
            )

        if status == "rejected":
            job_failures: list[str] = []
            if not raw_job.get("error") and not raw_job.get("reason"):
                job_failures.append("Rejected job needs an explicit reason")
            job_report = {
                "index": index,
                "job_id": job_id,
                "status": status,
                "artifact_dir": raw_job.get("artifact_dir"),
                "valid": not job_failures and bool(job_id),
                "failures": job_failures,
            }
            report["jobs"].append(job_report)
            failures.extend(f"{job_id or index}: {message}" for message in job_failures)
            continue
        if status != "passed":
            report["jobs"].append(
                {
                    "index": index,
                    "job_id": job_id,
                    "status": status,
                    "artifact_dir": raw_job.get("artifact_dir"),
                    "valid": False,
                    "failures": ["Job is not in a releasable terminal state"],
                }
            )
            continue

        job_report = audit_passed_artifact(raw_job, manifest_path, candidate_revision)
        job_report["index"] = index
        job_report["status"] = status
        report["jobs"].append(job_report)
        failures.extend(
            f"{job_id or index}: {message}" for message in job_report["failures"]
        )
        if job_report.get("_pdf_path"):
            pdf_paths.append(str(job_report["_pdf_path"]))
        if job_report.get("artifact_dir"):
            artifact_dirs.append(str(job_report["artifact_dir"]))
        if job_report.get("job_snapshot_sha256"):
            snapshot_hashes.append(str(job_report["job_snapshot_sha256"]))
        if job_report.get("_normalized_pdf_text"):
            passed_pdf_text.append((job_id, str(job_report["_normalized_pdf_text"])))
        selected_projects[job_id] = str(job_report.get("selected_project_id") or "")

    duplicate_pdf_paths = sorted(
        value for value, count in Counter(pdf_paths).items() if count > 1
    )
    duplicate_artifact_dirs = sorted(
        value for value, count in Counter(artifact_dirs).items() if count > 1
    )
    duplicate_snapshot_hashes = sorted(
        value for value, count in Counter(snapshot_hashes).items() if count > 1
    )
    if duplicate_pdf_paths:
        failures.append("Duplicate PDF output paths across passed jobs")
    if duplicate_artifact_dirs:
        failures.append("Duplicate artifact directories across passed jobs")
    if duplicate_snapshot_hashes:
        failures.append("Duplicate JD snapshots across passed jobs")

    near_identical_pairs = []
    for left_index, (left_id, left_text) in enumerate(passed_pdf_text):
        for right_id, right_text in passed_pdf_text[left_index + 1 :]:
            ratio = difflib.SequenceMatcher(None, left_text, right_text).ratio()
            if ratio >= 0.985:
                near_identical_pairs.append(
                    {"left": left_id, "right": right_id, "similarity": round(ratio, 4)}
                )
    if near_identical_pairs:
        warnings.append(
            "Near-identical tailored resumes detected; confirm the JDs genuinely "
            "require the same framing"
        )

    for job_report in report["jobs"]:
        job_report.pop("_pdf_path", None)
        job_report.pop("_normalized_pdf_text", None)
    report["counts"] = dict(sorted(statuses.items()))
    report["cross_batch"] = {
        "unique_job_ids": not duplicate_job_ids,
        "unique_artifact_dirs": not duplicate_artifact_dirs,
        "unique_pdf_paths": not duplicate_pdf_paths,
        "unique_job_snapshots": not duplicate_snapshot_hashes,
        "selected_projects": selected_projects,
        "near_identical_pairs": near_identical_pairs,
    }
    report["status"] = "FAIL" if failures else "PASS"
    return report


def finish(report: dict[str, Any], output: Path | None) -> int:
    if output:
        atomic_write_json(output, report)
    for warning in report.get("warnings", []):
        print(f"WARN: {warning}")
    for failure in report.get("failures", []):
        print(f"FAIL: {failure}")
    counts = report.get("counts", {})
    print(
        f"{report.get('status')}: jobs={sum(counts.values())} "
        f"counts={json.dumps(counts, sort_keys=True)}"
    )
    return 0 if report.get("status") == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a completed 2-10 resume batch, recompute all artifact hashes, "
            "inspect published PDFs, and freshly recompile passed sources."
        )
    )
    parser.add_argument("manifest", type=Path, help="Path to batch.yml")
    parser.add_argument("--json", type=Path, help="Write a machine-readable batch QA report")
    args = parser.parse_args()
    manifest_path = args.manifest.expanduser().resolve()
    return finish(validate_manifest(manifest_path), args.json)


if __name__ == "__main__":
    sys.exit(main())
