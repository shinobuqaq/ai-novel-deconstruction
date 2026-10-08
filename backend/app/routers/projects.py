from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Project, SourceDocument, SourceVersion
from ..repositories import create_project, get_project, list_projects
from ..schemas import (
    ProjectCreate,
    ProjectRead,
    SourceDocumentRead,
    SourceImportRead,
    SourceUnitRead,
    SourceVersionRead,
)
from ..services.source_import import SourceImportError, import_source
from .common import _source_error, _source_issue_read

router = APIRouter()


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
