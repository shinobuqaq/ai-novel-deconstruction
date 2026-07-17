from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .db import create_db_engine, create_session_factory
from .models import (
    Artifact,
    ArtifactBlob,
    ArtifactStatus,
    Base,
    TaskAttempt,
    TaskAttemptStatus,
    TaskStatus,
)
from .repositories import claim_next_task, create_project, create_task, get_task


def _runtime():
    settings = get_settings()
    settings.ensure_directories()
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    return settings, engine, create_session_factory(engine)


def seed() -> int:
    _settings, engine, session_factory = _runtime()
    try:
        with session_factory() as session:
            project = create_project(
                session,
                name="M0 crash recovery gate",
                description="Isolated Windows hard-kill verification",
            )
            task = create_task(
                session,
                project_id=project.id,
                kind="fake.echo",
                payload={"message": "recover after hard kill"},
                max_attempts=3,
            )
            print(task.id)
        return 0
    finally:
        engine.dispose()


def claim_and_hang(sentinel: Path) -> int:
    settings, engine, session_factory = _runtime()
    try:
        with session_factory() as session:
            claim = claim_next_task(
                session,
                worker_id="m0-hard-kill-probe",
                lease_seconds=settings.worker_lease_seconds,
            )
        if claim is None:
            raise RuntimeError("M0_GATE_TASK_NOT_CLAIMED")
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(
            json.dumps(
                {
                    "task_id": claim.id,
                    "attempt_id": claim.current_attempt_id,
                    "lease_generation": claim.lease_generation,
                    "lease_expires_at": claim.lease_expires_at.isoformat(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"claimed task={claim.id} attempt={claim.current_attempt_id}", flush=True)
        time.sleep(600)
        return 0
    finally:
        engine.dispose()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(task_id: str) -> int:
    settings, engine, session_factory = _runtime()
    try:
        with session_factory() as session:
            task = get_task(session, task_id)
            if task is None:
                raise RuntimeError("M0_GATE_TASK_MISSING")
            attempts = list(
                session.scalars(
                    select(TaskAttempt)
                    .where(TaskAttempt.task_id == task_id)
                    .order_by(TaskAttempt.attempt_no)
                )
            )
            artifact = (
                session.get(Artifact, task.result_artifact_id)
                if task.result_artifact_id
                else None
            )
            blob = (
                session.get(ArtifactBlob, artifact.blob_id)
                if artifact is not None
                else None
            )

            blob_path = (
                settings.workspace_dir / blob.relative_path
                if blob is not None
                else None
            )
            blob_file_valid = bool(
                blob_path is not None
                and blob_path.is_file()
                and _sha256_file(blob_path) == blob.content_hash
            )
            temp_files = [
                path.relative_to(settings.workspace_dir).as_posix()
                for path in settings.workspace_dir.rglob("*.tmp-*")
            ]
            result = {
                "task_id": task.id,
                "task_status": task.status,
                "attempts": task.attempts,
                "attempt_statuses": [attempt.status for attempt in attempts],
                "lease_generation": task.lease_generation,
                "result_artifact_id": task.result_artifact_id,
                "artifact_status": None if artifact is None else artifact.status,
                "blob_status": None if blob is None else blob.status,
                "blob_file_valid": blob_file_valid,
                "temp_files": temp_files,
            }
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))

            expected = (
                task.status == TaskStatus.SUCCEEDED.value
                and task.attempts == 2
                and [attempt.status for attempt in attempts]
                == [
                    TaskAttemptStatus.EXPIRED.value,
                    TaskAttemptStatus.SUCCEEDED.value,
                ]
                and task.lease_generation == 2
                and artifact is not None
                and artifact.status == ArtifactStatus.READY.value
                and blob is not None
                and blob.status == ArtifactStatus.READY.value
                and blob_file_valid
                and temp_files == []
            )
            return 0 if expected else 1
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="M0 hard-kill gate probe")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed")
    claim_parser = subparsers.add_parser("claim-and-hang")
    claim_parser.add_argument("--sentinel", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--task-id", required=True)
    args = parser.parse_args()

    if args.command == "seed":
        raise SystemExit(seed())
    if args.command == "claim-and-hang":
        raise SystemExit(claim_and_hang(args.sentinel))
    raise SystemExit(verify(args.task_id))


if __name__ == "__main__":
    main()
