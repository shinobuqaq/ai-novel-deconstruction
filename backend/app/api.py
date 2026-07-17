from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import get_db
from .models import Artifact, ArtifactBlob, ArtifactStatus, Project, Task
from .repositories import (
    create_project,
    create_task,
    get_project,
    get_task,
    list_projects,
    list_tasks,
    request_task_cancellation,
    retry_task,
)
from .schemas import ArtifactRead, ProjectCreate, ProjectRead, TaskCreate, TaskRead

router = APIRouter()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _task_read(task: Task) -> TaskRead:
    return TaskRead(
        id=task.id,
        project_id=task.project_id,
        kind=task.kind,
        status=task.status,
        payload=json.loads(task.payload_json),
        result_artifact_id=task.result_artifact_id,
        attempts=task.attempts,
        max_attempts=task.max_attempts,
        lease_owner=task.lease_owner,
        lease_expires_at=_as_utc(task.lease_expires_at),
        current_attempt_id=task.current_attempt_id,
        lease_generation=task.lease_generation,
        next_attempt_at=_as_utc(task.next_attempt_at),
        cancel_requested_at=_as_utc(task.cancel_requested_at),
        last_error_code=task.last_error_code,
        last_error_message=task.last_error_message,
        error_code=task.error_code,
        error_message=task.error_message,
        created_at=_as_utc(task.created_at),
        started_at=_as_utc(task.started_at),
        finished_at=_as_utc(task.finished_at),
        updated_at=_as_utc(task.updated_at),
    )


def _artifact_read(artifact: Artifact) -> ArtifactRead:
    return ArtifactRead(
        id=artifact.id,
        project_id=artifact.project_id,
        kind=artifact.kind,
        schema_version=artifact.schema_version,
        status=artifact.status,
        result_key=artifact.result_key,
        blob_id=artifact.blob_id,
        content_hash=artifact.content_hash,
        relative_path=artifact.relative_path,
        created_by_task_id=artifact.created_by_task_id,
        created_by_attempt_id=artifact.created_by_attempt_id,
        lease_generation=artifact.lease_generation,
        metadata=json.loads(artifact.metadata_json),
        created_at=_as_utc(artifact.created_at),
    )


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    return {"status": "ok", "app": request.app.title}


@router.post("/api/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def projects_create(payload: ProjectCreate, session: Session = Depends(get_db)) -> Project:
    return create_project(session, name=payload.name, description=payload.description)


@router.get("/api/projects", response_model=list[ProjectRead])
def projects_list(session: Session = Depends(get_db)) -> list[Project]:
    return list_projects(session)


@router.post("/api/tasks", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def tasks_create(payload: TaskCreate, session: Session = Depends(get_db)) -> TaskRead:
    if get_project(session, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="PROJECT_NOT_FOUND")
    task = create_task(
        session,
        project_id=payload.project_id,
        kind=payload.kind,
        payload=payload.payload,
        max_attempts=payload.max_attempts,
    )
    return _task_read(task)


@router.get("/api/tasks", response_model=list[TaskRead])
def tasks_list(project_id: str | None = None, session: Session = Depends(get_db)) -> list[TaskRead]:
    return [_task_read(task) for task in list_tasks(session, project_id=project_id)]


@router.get("/api/tasks/{task_id}", response_model=TaskRead)
def tasks_get(task_id: str, session: Session = Depends(get_db)) -> TaskRead:
    task = get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND")
    return _task_read(task)


@router.post("/api/tasks/{task_id}/retry", response_model=TaskRead)
def tasks_retry(task_id: str, session: Session = Depends(get_db)) -> TaskRead:
    task = get_task(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND")
    return _task_read(retry_task(session, task))


@router.post("/api/tasks/{task_id}/cancel", response_model=TaskRead)
def tasks_cancel(task_id: str, session: Session = Depends(get_db)) -> TaskRead:
    task = request_task_cancellation(session, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="TASK_NOT_FOUND")
    return _task_read(task)


@router.get("/api/artifacts", response_model=list[ArtifactRead])
def artifacts_list(project_id: str | None = None, session: Session = Depends(get_db)) -> list[ArtifactRead]:
    stmt = select(Artifact).order_by(Artifact.created_at.desc())
    if project_id:
        stmt = stmt.where(Artifact.project_id == project_id)
    return [_artifact_read(item) for item in session.scalars(stmt)]


@router.get("/api/artifacts/{artifact_id}/content")
def artifact_content(artifact_id: str, request: Request, session: Session = Depends(get_db)) -> dict:
    artifact = session.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="ARTIFACT_NOT_FOUND")
    blob = session.get(ArtifactBlob, artifact.blob_id)
    if blob is None:
        raise HTTPException(status_code=409, detail="ARTIFACT_BLOB_MISSING")
    if blob.status != ArtifactStatus.READY.value:
        raise HTTPException(status_code=409, detail="ARTIFACT_BLOB_NOT_READY")
    settings: Settings = request.app.state.settings
    path = settings.workspace_dir / Path(blob.relative_path)
    if not path.is_file():
        raise HTTPException(status_code=409, detail="ARTIFACT_FILE_MISSING")
    return json.loads(path.read_text(encoding="utf-8"))
