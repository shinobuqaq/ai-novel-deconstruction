from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Artifact, ArtifactBlob, ArtifactStatus


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
            os.replace(temp_path, destination)
            blob.status = ArtifactStatus.READY.value
            blob.size_bytes = len(content)
        artifact.status = ArtifactStatus.READY.value
        session.commit()
        session.refresh(artifact)
        return artifact
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        blob.status = ArtifactStatus.FAILED.value
        artifact.status = ArtifactStatus.FAILED.value
        session.commit()
        raise
