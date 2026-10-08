from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import EvidenceSpan, SourceIssue, SourceUnit, SourceVersion
from ..schemas import (
    EvidenceContextRead,
    EvidenceSpanRead,
    SourceIssueRead,
    SourceStructureRead,
    SourceUnitContentRead,
    SourceUnitMerge,
    SourceUnitRead,
    SourceUnitSplit,
    SourceUnitUpdate,
    SourceVersionRead,
)
from ..services.source_import import (
    SourceImportError,
    confirm_source_version,
    merge_source_unit,
    resolve_source_issue,
    source_text,
    source_unit_display_content,
    split_source_unit,
    update_source_unit,
)
from .common import (
    _source_error,
    _source_issue_read,
    _source_structure_read,
)

router = APIRouter()


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


@router.patch(
    "/api/chapters/{unit_id}",
    response_model=SourceStructureRead,
)
def source_unit_update(
    unit_id: str,
    payload: SourceUnitUpdate,
    request: Request,
    session: Session = Depends(get_db),
) -> SourceStructureRead:
    unit = session.get(SourceUnit, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="SOURCE_UNIT_NOT_FOUND")
    try:
        result = update_source_unit(
            session,
            request.app.state.settings,
            unit,
            title=payload.title,
            unit_type=payload.unit_type,
        )
    except SourceImportError as error:
        raise _source_error(error) from error
    return _source_structure_read(result)


@router.post(
    "/api/chapters/{unit_id}/split",
    response_model=SourceStructureRead,
)
def source_unit_split(
    unit_id: str,
    payload: SourceUnitSplit,
    request: Request,
    session: Session = Depends(get_db),
) -> SourceStructureRead:
    unit = session.get(SourceUnit, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="SOURCE_UNIT_NOT_FOUND")
    try:
        result = split_source_unit(
            session,
            request.app.state.settings,
            unit,
            split_char=payload.split_char,
            title=payload.title,
            unit_type=payload.unit_type,
        )
    except SourceImportError as error:
        raise _source_error(error) from error
    return _source_structure_read(result)


@router.post(
    "/api/chapters/{unit_id}/merge",
    response_model=SourceStructureRead,
)
def source_unit_merge(
    unit_id: str,
    payload: SourceUnitMerge,
    request: Request,
    session: Session = Depends(get_db),
) -> SourceStructureRead:
    unit = session.get(SourceUnit, unit_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="SOURCE_UNIT_NOT_FOUND")
    try:
        result = merge_source_unit(
            session,
            request.app.state.settings,
            unit,
            direction=payload.direction,
        )
    except SourceImportError as error:
        raise _source_error(error) from error
    return _source_structure_read(result)


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
    display_start, content = source_unit_display_content(text, unit)
    return SourceUnitContentRead(
        id=unit.id,
        source_version_id=unit.source_version_id,
        ordinal=unit.ordinal,
        title=unit.title,
        start_char=display_start,
        end_char=unit.end_char,
        content=content,
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
