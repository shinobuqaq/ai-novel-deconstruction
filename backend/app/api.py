from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import get_db
from .models import (
    Artifact,
    ArtifactBlob,
    ArtifactStatus,
    EvidenceSpan,
    Project,
    SourceDocument,
    SourceIssue,
    SourceUnit,
    SourceVersion,
    Task,
)
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
from .schemas import (
    ArtifactRead,
    EvidenceContextRead,
    EvidenceSpanRead,
    ProjectCreate,
    ProjectRead,
    SourceDocumentRead,
    SourceImportRead,
    SourceIssueRead,
    SourceUnitContentRead,
    SourceUnitRead,
    SourceVersionRead,
    TaskCreate,
    TaskRead,
)
from .services.source_import import (
    SourceImportError,
    confirm_source_version,
    import_source,
    resolve_source_issue,
    source_text,
)

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


def _source_issue_read(issue: SourceIssue) -> SourceIssueRead:
    return SourceIssueRead(
        id=issue.id,
        source_version_id=issue.source_version_id,
        source_unit_id=issue.source_unit_id,
        code=issue.code,
        severity=issue.severity,
        message=issue.message,
        details=json.loads(issue.details_json),
        status=issue.status,
        created_at=_as_utc(issue.created_at),
        resolved_at=_as_utc(issue.resolved_at),
    )


def _source_error(error: SourceImportError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
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


@router.post(
    "/api/projects/{project_id}/sources/import",
    response_model=SourceImportRead,
    status_code=status.HTTP_201_CREATED,
)
async def sources_import(
    project_id: str,
    filename: str,
    request: Request,
    session: Session = Depends(get_db),
) -> SourceImportRead:
    project = get_project(session, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="PROJECT_NOT_FOUND")
    try:
        result = import_source(
            session,
            request.app.state.settings,
            project=project,
            filename=filename,
            payload=await request.body(),
        )
    except SourceImportError as error:
        raise _source_error(error) from error
    return SourceImportRead(
        document=SourceDocumentRead.model_validate(result.document),
        version=SourceVersionRead.model_validate(result.version),
        units=[SourceUnitRead.model_validate(unit) for unit in result.units],
        issues=[_source_issue_read(issue) for issue in result.issues],
        reused_existing=result.reused_existing,
    )


@router.get(
    "/api/projects/{project_id}/sources",
    response_model=list[SourceDocumentRead],
)
def sources_list(
    project_id: str,
    session: Session = Depends(get_db),
) -> list[SourceDocument]:
    if get_project(session, project_id) is None:
        raise HTTPException(status_code=404, detail="PROJECT_NOT_FOUND")
    stmt = (
        select(SourceDocument)
        .where(SourceDocument.project_id == project_id)
        .order_by(SourceDocument.created_at.desc())
    )
    return list(session.scalars(stmt))


@router.get(
    "/api/projects/{project_id}/source-versions",
    response_model=list[SourceVersionRead],
)
def source_versions_list(
    project_id: str,
    session: Session = Depends(get_db),
) -> list[SourceVersion]:
    if get_project(session, project_id) is None:
        raise HTTPException(status_code=404, detail="PROJECT_NOT_FOUND")
    stmt = (
        select(SourceVersion)
        .join(SourceDocument, SourceVersion.document_id == SourceDocument.id)
        .where(SourceDocument.project_id == project_id)
        .order_by(SourceVersion.created_at.desc())
    )
    return list(session.scalars(stmt))


@router.get(
    "/api/source-versions/{version_id}/chapters",
    response_model=list[SourceUnitRead],
)
def source_chapters_list(
    version_id: str,
    session: Session = Depends(get_db),
) -> list[SourceUnit]:
    if session.get(SourceVersion, version_id) is None:
        raise HTTPException(status_code=404, detail="SOURCE_VERSION_NOT_FOUND")
    return list(session.scalars(
        select(SourceUnit)
        .where(SourceUnit.source_version_id == version_id)
        .order_by(SourceUnit.ordinal)
    ))


@router.get(
    "/api/source-versions/{version_id}/issues",
    response_model=list[SourceIssueRead],
)
def source_issues_list(
    version_id: str,
    session: Session = Depends(get_db),
) -> list[SourceIssueRead]:
    if session.get(SourceVersion, version_id) is None:
        raise HTTPException(status_code=404, detail="SOURCE_VERSION_NOT_FOUND")
    issues = session.scalars(
        select(SourceIssue)
        .where(SourceIssue.source_version_id == version_id)
        .order_by(SourceIssue.created_at, SourceIssue.id)
    )
    return [_source_issue_read(issue) for issue in issues]


@router.post(
    "/api/source-issues/{issue_id}/resolve",
    response_model=SourceIssueRead,
)
def source_issues_resolve(
    issue_id: str,
    session: Session = Depends(get_db),
) -> SourceIssueRead:
    issue = session.get(SourceIssue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="SOURCE_ISSUE_NOT_FOUND")
    return _source_issue_read(resolve_source_issue(session, issue))


@router.post(
    "/api/source-versions/{version_id}/confirm",
    response_model=SourceVersionRead,
)
def source_versions_confirm(
    version_id: str,
    session: Session = Depends(get_db),
) -> SourceVersion:
    version = session.get(SourceVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="SOURCE_VERSION_NOT_FOUND")
    try:
        return confirm_source_version(session, version)
    except SourceImportError as error:
        raise _source_error(error) from error


@router.get(
    "/api/chapters/{unit_id}/content",
    response_model=SourceUnitContentRead,
)
def source_unit_content(
    unit_id: str,
    request: Request,
    session: Session = Depends(get_db),
) -> SourceUnitContentRead:
    unit = session.get(SourceUnit, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="SOURCE_UNIT_NOT_FOUND")
    try:
        text = source_text(request.app.state.settings, unit.source_version)
    except SourceImportError as error:
        raise _source_error(error) from error
    return SourceUnitContentRead(
        id=unit.id,
        source_version_id=unit.source_version_id,
        ordinal=unit.ordinal,
        title=unit.title,
        start_char=unit.start_char,
        end_char=unit.end_char,
        content=text[unit.start_char:unit.end_char],
    )


@router.get(
    "/api/evidence/{evidence_id}",
    response_model=EvidenceContextRead,
)
def evidence_get(
    evidence_id: str,
    request: Request,
    session: Session = Depends(get_db),
) -> EvidenceContextRead:
    evidence = session.get(EvidenceSpan, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="EVIDENCE_NOT_FOUND")
    try:
        text = source_text(request.app.state.settings, evidence.source_version)
    except SourceImportError as error:
        raise _source_error(error) from error
    context_start = max(0, evidence.start_char - 200)
    context_end = min(len(text), evidence.end_char + 200)
    return EvidenceContextRead(
        evidence=EvidenceSpanRead.model_validate(evidence),
        chapter_title=evidence.source_unit.title,
        context_start=context_start,
        context_end=context_end,
        context_text=text[context_start:context_end],
    )


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
