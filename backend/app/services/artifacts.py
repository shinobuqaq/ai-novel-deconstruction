from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
    Artifact,
    ArtifactBlob,
    ArtifactStatus,
    Task,
    TaskAttempt,
    TaskAttemptStatus,
    TaskStatus,
)


class ArtifactFaultPoint(StrEnum):
    AFTER_TEMP_WRITE = "after_temp_write"
    AFTER_REPLACE = "after_replace"
    AFTER_ARTIFACT_COMMIT = "after_artifact_commit"


class InjectedArtifactCrash(BaseException):
    pass


@dataclass(frozen=True, slots=True)
class CrashAt:
    point: ArtifactFaultPoint

    def __call__(self, current: ArtifactFaultPoint) -> None:
        if current == self.point:
            raise InjectedArtifactCrash(current.value)


@dataclass(frozen=True, slots=True)
class ArtifactRecoveryReport:
    temp_files_removed: int = 0
    orphan_blobs_adopted: int = 0
    orphan_files_removed: int = 0
    blobs_recovered: int = 0
    blobs_marked_dirty: int = 0
    artifacts_recovered: int = 0
    artifacts_marked_dirty: int = 0
    tasks_recovered: int = 0

    @property
    def changes(self) -> int:
        return sum(
            (
                self.temp_files_removed,
                self.orphan_blobs_adopted,
                self.orphan_files_removed,
                self.blobs_recovered,
                self.blobs_marked_dirty,
                self.artifacts_recovered,
                self.artifacts_marked_dirty,
                self.tasks_recovered,
            )
        )


ArtifactFaultInjector = Callable[[ArtifactFaultPoint], None]


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inject(
    fault_injector: ArtifactFaultInjector | None,
    point: ArtifactFaultPoint,
) -> None:
    if fault_injector is not None:
        fault_injector(point)


def write_json_artifact(
    session: Session,
    settings: Settings,
    *,
    project_id: str,
    kind: str,
    payload: dict,
    created_by_task_id: str | None,
    created_by_attempt_id: str | None = None,
    lease_generation: int | None = None,
    metadata: dict | None = None,
    fault_injector: ArtifactFaultInjector | None = None,
) -> Artifact:
    content = _canonical_bytes(payload)
    content_hash = hashlib.sha256(content).hexdigest()
    result_owner = created_by_task_id or f"manual:{project_id}:{content_hash}"
    result_key = f"{result_owner}:{kind}"

    if not session.in_transaction():
        connection = session.connection()
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")

    existing = session.scalar(
        select(Artifact).where(
            Artifact.result_key == result_key,
        )
    )
    if existing is not None:
        if existing.content_hash != content_hash:
            raise RuntimeError("ARTIFACT_RESULT_KEY_CONFLICT")
        if existing.status != ArtifactStatus.READY.value:
            raise RuntimeError("ARTIFACT_RESULT_NOT_READY")
        return existing

    blob = session.scalar(
        select(ArtifactBlob).where(
            ArtifactBlob.content_hash == content_hash,
        )
    )
    relative_path = Path("blobs") / content_hash[:2] / f"{content_hash}.json"
    if blob is None:
        blob = ArtifactBlob(
            id=f"blb_{content_hash}",
            content_hash=content_hash,
            status=ArtifactStatus.WRITING.value,
            relative_path=relative_path.as_posix(),
            size_bytes=len(content),
        )
        session.add(blob)
        session.flush()
    elif blob.status != ArtifactStatus.READY.value:
        raise RuntimeError("ARTIFACT_BLOB_NOT_READY")

    artifact = Artifact(
        project_id=project_id,
        kind=kind,
        result_key=result_key,
        blob_id=blob.id,
        content_hash=content_hash,
        relative_path=blob.relative_path,
        created_by_task_id=created_by_task_id,
        created_by_attempt_id=created_by_attempt_id,
        lease_generation=lease_generation,
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        status=(
            ArtifactStatus.READY.value
            if blob.status == ArtifactStatus.READY.value
            else ArtifactStatus.WRITING.value
        ),
    )
    session.add(artifact)
    session.flush()

    destination = settings.workspace_dir / Path(blob.relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(f".tmp-{uuid4().hex}")

    try:
        if blob.status != ArtifactStatus.READY.value:
            temp_path.write_bytes(content)
            if hashlib.sha256(temp_path.read_bytes()).hexdigest() != content_hash:
                raise RuntimeError("ARTIFACT_HASH_MISMATCH")
            _inject(fault_injector, ArtifactFaultPoint.AFTER_TEMP_WRITE)
            os.replace(temp_path, destination)
            _inject(fault_injector, ArtifactFaultPoint.AFTER_REPLACE)
            blob.status = ArtifactStatus.READY.value
            blob.size_bytes = len(content)
        artifact.status = ArtifactStatus.READY.value
        session.commit()
        session.refresh(artifact)
        _inject(fault_injector, ArtifactFaultPoint.AFTER_ARTIFACT_COMMIT)
        return artifact
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        blob.status = ArtifactStatus.FAILED.value
        artifact.status = ArtifactStatus.FAILED.value
        session.commit()
        raise


def reconcile_artifacts(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    stale_after_seconds: float = 300,
) -> ArtifactRecoveryReport:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=max(0.0, stale_after_seconds))
    counts = {
        "temp_files_removed": 0,
        "orphan_blobs_adopted": 0,
        "orphan_files_removed": 0,
        "blobs_recovered": 0,
        "blobs_marked_dirty": 0,
        "artifacts_recovered": 0,
        "artifacts_marked_dirty": 0,
        "tasks_recovered": 0,
    }

    for temp_path in settings.workspace_dir.rglob("*.tmp-*"):
        modified_at = datetime.fromtimestamp(
            temp_path.stat().st_mtime,
            tz=timezone.utc,
        )
        if modified_at <= cutoff:
            temp_path.unlink(missing_ok=True)
            counts["temp_files_removed"] += 1

    blobs = list(session.scalars(select(ArtifactBlob)))
    known_paths = {blob.relative_path for blob in blobs}
    for blob in blobs:
        path = settings.workspace_dir / Path(blob.relative_path)
        valid = path.is_file() and _sha256_file(path) == blob.content_hash
        if valid:
            if blob.status != ArtifactStatus.READY.value:
                blob.status = ArtifactStatus.READY.value
                blob.size_bytes = path.stat().st_size
                counts["blobs_recovered"] += 1
        elif blob.status != ArtifactStatus.DIRTY.value:
            blob.status = ArtifactStatus.DIRTY.value
            counts["blobs_marked_dirty"] += 1

    blob_root = settings.workspace_dir / "blobs"
    if blob_root.is_dir():
        for path in blob_root.rglob("*.json"):
            relative_path = path.relative_to(settings.workspace_dir).as_posix()
            if relative_path in known_paths:
                continue
            modified_at = datetime.fromtimestamp(
                path.stat().st_mtime,
                tz=timezone.utc,
            )
            if modified_at > cutoff:
                continue
            content_hash = _sha256_file(path)
            canonical_name = f"{content_hash}.json"
            existing_blob = session.scalar(
                select(ArtifactBlob).where(
                    ArtifactBlob.content_hash == content_hash,
                )
            )
            if path.name != canonical_name or existing_blob is not None:
                path.unlink(missing_ok=True)
                counts["orphan_files_removed"] += 1
                continue
            session.add(
                ArtifactBlob(
                    id=f"blb_{content_hash}",
                    content_hash=content_hash,
                    status=ArtifactStatus.READY.value,
                    relative_path=relative_path,
                    size_bytes=path.stat().st_size,
                    created_at=now,
                )
            )
            known_paths.add(relative_path)
            counts["orphan_blobs_adopted"] += 1

    session.flush()
    artifacts = list(session.scalars(select(Artifact)))
    for artifact in artifacts:
        blob = session.get(ArtifactBlob, artifact.blob_id)
        if blob is None or blob.status != ArtifactStatus.READY.value:
            if artifact.status != ArtifactStatus.DIRTY.value:
                artifact.status = ArtifactStatus.DIRTY.value
                counts["artifacts_marked_dirty"] += 1
            continue

        if artifact.status != ArtifactStatus.READY.value:
            artifact.status = ArtifactStatus.READY.value
            artifact.content_hash = blob.content_hash
            artifact.relative_path = blob.relative_path
            counts["artifacts_recovered"] += 1

        if (
            artifact.created_by_task_id is None
            or artifact.created_by_attempt_id is None
            or artifact.lease_generation is None
        ):
            continue

        artifact_created_at = artifact.created_at
        if artifact_created_at.tzinfo is None:
            artifact_created_at = artifact_created_at.replace(
                tzinfo=timezone.utc
            )
        if artifact_created_at > cutoff:
            continue

        task = session.get(Task, artifact.created_by_task_id)
        attempt = session.get(TaskAttempt, artifact.created_by_attempt_id)
        if task is None or attempt is None:
            artifact.status = ArtifactStatus.DIRTY.value
            counts["artifacts_marked_dirty"] += 1
            continue

        if (
            task.status == TaskStatus.SUCCEEDED.value
            and task.result_artifact_id == artifact.id
        ):
            continue

        identity_matches = (
            task.status == TaskStatus.RUNNING.value
            and task.current_attempt_id == artifact.created_by_attempt_id
            and task.lease_generation == artifact.lease_generation
            and attempt.status == TaskAttemptStatus.RUNNING.value
        )
        if not identity_matches:
            artifact.status = ArtifactStatus.DIRTY.value
            counts["artifacts_marked_dirty"] += 1
            continue

        attempt.status = TaskAttemptStatus.SUCCEEDED.value
        attempt.finished_at = now
        task.status = TaskStatus.SUCCEEDED.value
        task.result_artifact_id = artifact.id
        task.finished_at = now
        task.lease_owner = None
        task.lease_expires_at = None
        task.next_attempt_at = None
        task.last_error_code = None
        task.last_error_message = None
        task.error_code = None
        task.error_message = None
        counts["tasks_recovered"] += 1

    session.commit()
    return ArtifactRecoveryReport(**counts)
