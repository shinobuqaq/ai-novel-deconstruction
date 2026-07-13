from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Artifact, ArtifactStatus


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
    metadata: dict | None = None,
) -> Artifact:
    content = _canonical_bytes(payload)
    content_hash = hashlib.sha256(content).hexdigest()

    existing = session.scalar(
        select(Artifact).where(
            Artifact.project_id == project_id,
            Artifact.kind == kind,
            Artifact.content_hash == content_hash,
        )
    )
    if existing is not None:
        return existing

    artifact = Artifact(
        project_id=project_id,
        kind=kind,
        content_hash=content_hash,
        relative_path="PENDING",
        created_by_task_id=created_by_task_id,
        metadata_json=json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
        status=ArtifactStatus.WRITING.value,
    )
    session.add(artifact)
    session.flush()

    relative_path = Path("artifacts") / project_id / f"{artifact.id}.json"
    destination = settings.workspace_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(f".tmp-{uuid4().hex}")

    try:
        temp_path.write_bytes(content)
        if hashlib.sha256(temp_path.read_bytes()).hexdigest() != content_hash:
            raise RuntimeError("ARTIFACT_HASH_MISMATCH")
        os.replace(temp_path, destination)
        artifact.relative_path = relative_path.as_posix()
        artifact.status = ArtifactStatus.READY.value
        session.commit()
        session.refresh(artifact)
        return artifact
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        artifact.status = ArtifactStatus.FAILED.value
        session.commit()
        raise
