from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..models import Artifact, ArtifactBlob, ArtifactStatus, Task
from ..repositories import (
    create_task,
    get_project,
    get_task,
    list_tasks,
    request_task_cancellation,
    retry_task,
)
from ..schemas import ArtifactRead, TaskCreate, TaskRead
from .common import _artifact_read, _task_read

router = APIRouter()


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
