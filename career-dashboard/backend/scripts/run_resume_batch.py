#!/usr/bin/env python3
"""Run a resumable resume batch with isolated workers and atomic status updates.

The manifest may be YAML initially. Status updates are written as JSON, which is
also valid YAML, so every transition can be replaced atomically without relying
on a third-party YAML emitter.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import subprocess
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from validate_batch import (
    REQUIRED_PASSED_ARTIFACTS,
    atomic_write_json,
    audit_passed_artifact,
    resolve_path,
    validate_manifest,
)
from validate_resume import load_yaml, sha256


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = ROOT / "data/context/evidence.yml"
PROFILE_PATH = ROOT / "data/config/profile.yml"
ALLOWED_STATUSES = {"pending", "running", "passed", "rejected", "failed"}
TERMINAL_STATUSES = {"passed", "rejected"}
WORKER_RESULT = "worker-result.json"
WORKER_LOG = "worker.log"
PLACEHOLDERS = (
    "workspace",
    "manifest",
    "run_id",
    "job_id",
    "artifact_dir",
    "snapshot_path",
    "attempt",
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def lock_is_live(path: Path) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        pid = int(value["pid"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def manifest_lock(manifest_path: Path) -> Iterator[None]:
    lock_path = manifest_path.with_name(f".{manifest_path.name}.lock")
    while True:
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            if lock_is_live(lock_path):
                raise RuntimeError(f"Another batch runner owns {lock_path}")
            lock_path.unlink()
            continue
        break
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "created_at": now()}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def manifest_jobs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not 2 <= len(jobs) <= 10:
        raise RuntimeError("Batch manifest must contain 2-10 jobs")
    if not all(isinstance(job, dict) for job in jobs):
        raise RuntimeError("Every batch job must be a mapping")
    return jobs


def validate_isolation(jobs: list[dict[str, Any]], manifest_path: Path) -> None:
    job_ids: list[str] = []
    artifact_dirs: list[Path] = []
    for index, job in enumerate(jobs, start=1):
        job_id = str(job.get("job_id", "")).strip()
        if not job_id:
            raise RuntimeError(f"Job {index} needs job_id")
        job_ids.append(job_id)
        artifact_value = job.get("artifact_dir")
        if not isinstance(artifact_value, str) or not artifact_value.strip():
            raise RuntimeError(f"{job_id}: artifact_dir is required")
        artifact_dirs.append(resolve_path(artifact_value, manifest_path))
        status = str(job.get("status", "pending")).lower()
        if status not in ALLOWED_STATUSES:
            raise RuntimeError(f"{job_id}: invalid status {status}")
        attempts = job.get("attempts", 0)
        if type(attempts) is not int or attempts < 0:
            raise RuntimeError(f"{job_id}: attempts must be a non-negative integer")
    duplicates = sorted(
        value for value, count in Counter(job_ids).items() if count > 1
    )
    if duplicates:
        raise RuntimeError(f"Duplicate batch job IDs: {', '.join(duplicates)}")
    if len({str(path) for path in artifact_dirs}) != len(artifact_dirs):
        raise RuntimeError("Every worker needs a unique artifact_dir")
    for left_index, left in enumerate(artifact_dirs):
        for right in artifact_dirs[left_index + 1 :]:
            if left in right.parents or right in left.parents:
                raise RuntimeError(
                    f"Worker artifact directories may not overlap: {left} and {right}"
                )


def snapshot_for_job(
    job: dict[str, Any],
    manifest_path: Path,
) -> tuple[Path, str]:
    artifact_dir = resolve_path(str(job["artifact_dir"]), manifest_path)
    snapshot_value = job.get("snapshot_path")
    snapshot_path = (
        resolve_path(snapshot_value, manifest_path)
        if isinstance(snapshot_value, str) and snapshot_value.strip()
        else artifact_dir / "job-description.md"
    )
    expected_snapshot = (artifact_dir / "job-description.md").resolve()
    if snapshot_path.resolve() != expected_snapshot:
        raise RuntimeError(
            f"{job['job_id']}: snapshot_path must be the job artifact's job-description.md"
        )
    if not snapshot_path.is_file():
        raise RuntimeError(f"{job['job_id']}: missing JD snapshot {snapshot_path}")
    return snapshot_path, sha256(snapshot_path)


def normalize_command(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"{label} must be a non-empty argument list")
    if not all(isinstance(item, str) and item for item in value):
        raise RuntimeError(f"{label} arguments must be non-empty strings")
    return list(value)


def expand_command(command: list[str], values: dict[str, str]) -> list[str]:
    expanded: list[str] = []
    for argument in command:
        value = argument
        for placeholder in PLACEHOLDERS:
            value = value.replace(f"{{{placeholder}}}", values[placeholder])
        expanded.append(value)
    return expanded


def load_worker_result(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Unreadable {WORKER_RESULT}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{WORKER_RESULT} must contain an object")
    return value


def write_state(manifest_path: Path, manifest: dict[str, Any]) -> None:
    jobs = manifest_jobs(manifest)
    manifest["updated_at"] = now()
    manifest["counts"] = dict(
        sorted(Counter(str(job.get("status", "missing")) for job in jobs).items())
    )
    atomic_write_json(manifest_path, manifest)


def prepare_job_state(
    job: dict[str, Any],
    manifest_path: Path,
    candidate_revision: str,
    max_attempts: int,
) -> tuple[Path, Path, str, bool]:
    artifact_dir = resolve_path(str(job["artifact_dir"]), manifest_path)
    snapshot_path, actual_snapshot_hash = snapshot_for_job(job, manifest_path)
    previous_hash = job.get("snapshot_sha256")
    previous_revision = job.get("candidate_revision_at_completion")
    status = str(job.get("status", "pending")).lower()
    unchanged_completion = (
        status in TERMINAL_STATUSES
        and previous_hash == actual_snapshot_hash
        and previous_revision == candidate_revision
    )

    if unchanged_completion and status == "passed":
        audit = audit_passed_artifact(job, manifest_path, candidate_revision)
        if audit["valid"]:
            return artifact_dir, snapshot_path, actual_snapshot_hash, True
        job["status"] = "pending"
        job["error"] = "Previously passed artifacts failed revalidation: " + "; ".join(
            audit["failures"]
        )
    elif unchanged_completion and status == "rejected":
        if job.get("reason") or job.get("error"):
            return artifact_dir, snapshot_path, actual_snapshot_hash, True
        job["status"] = "pending"
        job["error"] = "Previously rejected job has no reason"
    elif status in TERMINAL_STATUSES:
        job["status"] = "pending"
        job["attempts"] = 0
        job["error"] = "Inputs changed; terminal result invalidated"
        job["candidate_revision_at_completion"] = None
        job["completed_at"] = None
        job["selected_project_id"] = None
        job["page_count"] = None
        job["supported_requirement_coverage"] = None

    if status == "running":
        result_path = artifact_dir / WORKER_RESULT
        try:
            worker_result = load_worker_result(result_path)
        except RuntimeError:
            worker_result = None
        result_status = (
            str(worker_result.get("status", "")).lower()
            if worker_result is not None
            else ""
        )
        reason = (
            worker_result.get("reason") or worker_result.get("error")
            if worker_result is not None
            else None
        )
        if result_status == "rejected" and isinstance(reason, str) and reason.strip():
            job.update(
                {
                    "status": "rejected",
                    "reason": reason.strip(),
                    "error": None,
                    "candidate_revision_at_completion": candidate_revision,
                    "snapshot_sha256": actual_snapshot_hash,
                    "completed_at": now(),
                }
            )
            return artifact_dir, snapshot_path, actual_snapshot_hash, True
        audit = audit_passed_artifact(job, manifest_path, candidate_revision)
        if audit["valid"]:
            evidence_map = load_yaml(artifact_dir / "evidence-map.yml")
            job.update(
                {
                    "status": "passed",
                    "selected_project_id": audit["selected_project_id"],
                    "page_count": audit["page_count"],
                    "supported_requirement_coverage": evidence_map.get(
                        "supported_requirement_coverage"
                    ),
                    "error": None,
                    "candidate_revision_at_completion": candidate_revision,
                    "snapshot_sha256": actual_snapshot_hash,
                    "completed_at": now(),
                }
            )
            return artifact_dir, snapshot_path, actual_snapshot_hash, True
        job["status"] = "pending"
        job["error"] = "Recovered an interrupted running attempt"
    if status == "failed" and int(job.get("attempts", 0) or 0) < max_attempts:
        job["status"] = "pending"
    if previous_hash != actual_snapshot_hash and status not in TERMINAL_STATUSES:
        job["attempts"] = 0
        job["error"] = "JD snapshot changed; retry budget reset"
    job["snapshot_path"] = str(snapshot_path)
    job["snapshot_sha256"] = actual_snapshot_hash
    job.setdefault("attempts", 0)
    return artifact_dir, snapshot_path, actual_snapshot_hash, False


def append_log_header(
    stream: Any,
    job_id: str,
    attempt: int,
    command: list[str],
) -> None:
    stream.write(
        "\n"
        + json.dumps(
            {
                "event": "worker_attempt",
                "started_at": now(),
                "job_id": job_id,
                "attempt": attempt,
                "command": command,
            },
            sort_keys=True,
        )
        + "\n"
    )
    stream.flush()


def execute_job(
    job: dict[str, Any],
    manifest: dict[str, Any],
    manifest_path: Path,
    candidate_revision: str,
    command_override: list[str] | None,
    max_attempts: int,
    worker_timeout: int,
) -> None:
    artifact_dir, snapshot_path, snapshot_hash, skip = prepare_job_state(
        job,
        manifest_path,
        candidate_revision,
        max_attempts,
    )
    write_state(manifest_path, manifest)
    if skip:
        print(f"SKIP: {job['job_id']} remains {job['status']}")
        return

    command_value: Any = command_override
    if command_value is None:
        command_value = job.get("worker_command", manifest.get("worker_command"))
    try:
        command_template = normalize_command(
            command_value,
            f"{job['job_id']} worker_command",
        )
    except RuntimeError as exc:
        job["status"] = "failed"
        job["error"] = str(exc)
        write_state(manifest_path, manifest)
        return

    artifact_dir.mkdir(parents=True, exist_ok=True)
    result_path = artifact_dir / WORKER_RESULT
    log_path = artifact_dir / WORKER_LOG
    attempts = int(job.get("attempts", 0) or 0)
    run_id = str(manifest.get("run_id", manifest_path.parent.name))
    if attempts >= max_attempts:
        job["status"] = "failed"
        job["error"] = (
            f"Retry budget exhausted ({attempts}/{max_attempts} attempts); "
            "change the inputs or explicitly increase the bounded retry limit"
        )
        write_state(manifest_path, manifest)
        print(f"FAILED: {job['job_id']} — {job['error']}")
        return

    while attempts < max_attempts:
        attempts += 1
        values = {
            "workspace": str(ROOT),
            "manifest": str(manifest_path),
            "run_id": run_id,
            "job_id": str(job["job_id"]),
            "artifact_dir": str(artifact_dir),
            "snapshot_path": str(snapshot_path),
            "attempt": str(attempts),
        }
        command = expand_command(command_template, values)
        try:
            result_path.unlink()
        except FileNotFoundError:
            pass

        job.update(
            {
                "status": "running",
                "attempts": attempts,
                "started_at": now(),
                "completed_at": None,
                "error": None,
                "selected_project_id": None,
                "page_count": None,
                "supported_requirement_coverage": None,
            }
        )
        write_state(manifest_path, manifest)
        manifest_hash_before_worker = sha256(manifest_path)
        environment = os.environ.copy()
        environment.update(
            {
                "BATCH_RUN_ID": run_id,
                "BATCH_JOB_ID": str(job["job_id"]),
                "BATCH_ARTIFACT_DIR": str(artifact_dir),
                "BATCH_SNAPSHOT_PATH": str(snapshot_path),
                "BATCH_ATTEMPT": str(attempts),
                "BATCH_MANIFEST": str(manifest_path),
            }
        )

        return_code: int | None = None
        attempt_error: str | None = None
        with log_path.open("a", encoding="utf-8") as log:
            append_log_header(log, str(job["job_id"]), attempts, command)
            try:
                result = subprocess.run(
                    command,
                    cwd=artifact_dir,
                    env=environment,
                    text=True,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=worker_timeout,
                )
                return_code = result.returncode
            except subprocess.TimeoutExpired:
                attempt_error = f"Worker timed out after {worker_timeout} seconds"
            except OSError as exc:
                attempt_error = f"Worker could not start: {exc}"
            log.write(
                json.dumps(
                    {
                        "event": "worker_exit",
                        "finished_at": now(),
                        "job_id": job["job_id"],
                        "attempt": attempts,
                        "return_code": return_code,
                        "error": attempt_error,
                    },
                    sort_keys=True,
                )
                + "\n"
            )

        worker_result: dict[str, Any] | None = None
        try:
            manifest_unchanged = (
                manifest_path.is_file()
                and sha256(manifest_path) == manifest_hash_before_worker
            )
        except OSError:
            manifest_unchanged = False
        if not manifest_unchanged:
            attempt_error = (
                "Worker modified the batch manifest; only the runner may write status"
            )
        elif attempt_error is None and return_code == 0:
            try:
                worker_result = load_worker_result(result_path)
            except RuntimeError as exc:
                attempt_error = str(exc)
        elif attempt_error is None:
            attempt_error = f"Worker exited with status {return_code}"

        worker_payload = worker_result or {}
        result_status = str(worker_payload.get("status", "")).lower()
        if attempt_error is None and result_status == "rejected":
            reason = worker_payload.get("reason") or worker_payload.get("error")
            if not isinstance(reason, str) or not reason.strip():
                attempt_error = "Rejected worker result needs a reason"
            elif sha256(snapshot_path) != snapshot_hash:
                attempt_error = "Worker changed the immutable JD snapshot"
            else:
                job.update(
                    {
                        "status": "rejected",
                        "reason": reason.strip(),
                        "error": None,
                        "candidate_revision_at_completion": candidate_revision,
                        "snapshot_sha256": snapshot_hash,
                        "completed_at": now(),
                    }
                )
                write_state(manifest_path, manifest)
                print(f"REJECTED: {job['job_id']} — {reason.strip()}")
                return
        elif attempt_error is None and result_status not in {"", "passed"}:
            reason = worker_payload.get("error") or worker_payload.get("reason")
            attempt_error = str(reason or f"Invalid worker result status: {result_status}")

        if attempt_error is None:
            audit = audit_passed_artifact(job, manifest_path, candidate_revision)
            if audit["valid"]:
                evidence_map = load_yaml(artifact_dir / "evidence-map.yml")
                job.update(
                    {
                        "status": "passed",
                        "selected_project_id": audit["selected_project_id"],
                        "page_count": audit["page_count"],
                        "supported_requirement_coverage": evidence_map.get(
                            "supported_requirement_coverage"
                        ),
                        "error": None,
                        "candidate_revision_at_completion": candidate_revision,
                        "snapshot_sha256": snapshot_hash,
                        "completed_at": now(),
                    }
                )
                write_state(manifest_path, manifest)
                print(f"PASSED: {job['job_id']} on attempt {attempts}")
                return
            attempt_error = "Artifact validation failed: " + "; ".join(audit["failures"])

        has_attempt_left = attempts < max_attempts
        job.update(
            {
                "status": "pending" if has_attempt_left else "failed",
                "error": attempt_error,
                "last_attempt_finished_at": now(),
            }
        )
        write_state(manifest_path, manifest)
        print(
            f"{'RETRY' if has_attempt_left else 'FAILED'}: {job['job_id']} "
            f"attempt {attempts}/{max_attempts} — {attempt_error}"
        )


def mark_running_jobs_interrupted(
    manifest: dict[str, Any],
    manifest_path: Path,
) -> None:
    changed = False
    for job in manifest_jobs(manifest):
        if job.get("status") == "running":
            job["status"] = "pending"
            job["error"] = "Batch runner interrupted; safe to resume"
            changed = True
    if changed:
        write_state(manifest_path, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run or resume isolated resume workers. worker_command must be an argument "
            "list and may use {workspace}, {manifest}, {run_id}, {job_id}, "
            "{artifact_dir}, {snapshot_path}, and {attempt}."
        )
    )
    parser.add_argument("manifest", type=Path, help="Path to the batch YAML/JSON manifest")
    parser.add_argument(
        "--retry-limit",
        type=int,
        help="Retries after the first attempt (0-3; defaults to batch_contract.retry_limit)",
    )
    parser.add_argument(
        "--worker-timeout",
        type=int,
        default=1800,
        help="Maximum seconds for one worker attempt (default: 1800)",
    )
    parser.add_argument(
        "--worker-command",
        nargs=argparse.REMAINDER,
        help="Override the manifest worker_command with this shell-free argument list",
    )
    parser.add_argument(
        "--final-json",
        type=Path,
        help="Batch QA output (default: batch-qa.json beside the manifest)",
    )
    parser.add_argument(
        "--no-final-validation",
        action="store_true",
        help="Skip the final cross-batch audit (diagnostics only; never a release)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print planned work without changing state",
    )
    args = parser.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    if not manifest_path.is_file():
        print(f"FAIL: Missing batch manifest: {manifest_path}")
        return 1
    if args.worker_timeout < 1:
        print("FAIL: --worker-timeout must be positive")
        return 1

    try:
        manifest = load_yaml(manifest_path)
        evidence = load_yaml(EVIDENCE_PATH)
        profile = load_yaml(PROFILE_PATH)
        jobs = manifest_jobs(manifest)
        validate_isolation(jobs, manifest_path)
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}")
        return 1

    candidate_revision = evidence.get("candidate_revision")
    if not isinstance(candidate_revision, str) or (
        manifest.get("candidate_revision") != candidate_revision
    ):
        print("FAIL: Batch candidate_revision does not match context/evidence.yml")
        return 1
    declared_contract = manifest.get("required_artifacts")
    if declared_contract is not None and declared_contract != list(
        REQUIRED_PASSED_ARTIFACTS
    ):
        print("FAIL: Manifest required_artifacts does not match the canonical nine paths")
        return 1

    configured_retry_limit = (
        profile.get("batch_contract", {}).get("retry_limit", 3)
        if isinstance(profile.get("batch_contract"), dict)
        else 3
    )
    retry_limit = (
        args.retry_limit
        if args.retry_limit is not None
        else manifest.get("retry_limit", configured_retry_limit)
    )
    if not isinstance(retry_limit, int) or not 0 <= retry_limit <= 3:
        print("FAIL: retry limit must be an integer from 0 to 3")
        return 1
    max_attempts = retry_limit + 1
    try:
        command_override = (
            normalize_command(args.worker_command, "--worker-command")
            if args.worker_command
            else None
        )
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1

    if args.dry_run:
        try:
            for job in jobs:
                _, snapshot_hash = snapshot_for_job(job, manifest_path)
                command_value: Any = command_override
                if command_value is None:
                    command_value = job.get(
                        "worker_command",
                        manifest.get("worker_command"),
                    )
                normalize_command(command_value, f"{job['job_id']} worker_command")
                print(
                    f"PLAN: {job['job_id']} status={job.get('status', 'pending')} "
                    f"attempts={job.get('attempts', 0)}/{max_attempts} "
                    f"snapshot={snapshot_hash[:12]}"
                )
        except RuntimeError as exc:
            print(f"FAIL: {exc}")
            return 1
        return 0

    try:
        with manifest_lock(manifest_path):
            manifest["runner_schema_version"] = 1
            manifest["required_artifacts"] = list(REQUIRED_PASSED_ARTIFACTS)
            try:
                for job in jobs:
                    execute_job(
                        job,
                        manifest,
                        manifest_path,
                        candidate_revision,
                        command_override,
                        max_attempts,
                        args.worker_timeout,
                    )
            except KeyboardInterrupt:
                mark_running_jobs_interrupted(manifest, manifest_path)
                print("INTERRUPTED: statuses were saved; rerun the same command to resume")
                return 130

            if args.no_final_validation:
                statuses = Counter(str(job.get("status")) for job in jobs)
                print(f"DONE WITHOUT RELEASE AUDIT: {dict(sorted(statuses.items()))}")
                return 0 if all(job.get("status") in TERMINAL_STATUSES for job in jobs) else 1

            final_report = validate_manifest(manifest_path)
            final_json = (
                args.final_json.expanduser().resolve()
                if args.final_json
                else manifest_path.parent / "batch-qa.json"
            )
            atomic_write_json(final_json, final_report)
            for failure in final_report["failures"]:
                print(f"FAIL: {failure}")
            print(
                f"{final_report['status']}: "
                f"counts={json.dumps(final_report['counts'], sort_keys=True)} "
                f"report={final_json}"
            )
            return 0 if final_report["status"] == "PASS" else 1
    except (OSError, RuntimeError) as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
