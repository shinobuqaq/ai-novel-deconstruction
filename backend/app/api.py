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
    AnalysisRun,
    AnalysisRunTask,
    AnalysisIssue,
    CandidateStatus,
    EntityCandidate,
    EvidenceSpan,
    EventCandidate,
    DeepAnalysis,
    Project,
    SourceDocument,
    SourceIssue,
    SourceUnit,
    SourceVersion,
    Task,
    TaskStatus,
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
    AnalysisRunRead,
    AnalysisIssueCreate,
    AnalysisIssueRead,
    AnalysisProfileRead,
    AnalysisProfileWrite,
    EntityCandidateRead,
    EvidenceContextRead,
    EvidenceSpanRead,
    EventCandidateRead,
    WorkbenchRead,
    DeepAnalysisDiffRead,
    DeepAnalysisRevisionRead,
    ModelCatalogRead,
    ModelConnectionRead,
    ModelProbeRead,
    ModelServiceRead,
    ModelServiceWrite,
    ModelSettingsRead,
    OpenAIConfigRead,
    OpenAIConfigWrite,
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
    source_unit_display_content,
    source_text,
)
from .services.analysis import (
    ANALYSIS_STAGE,
    analysis_run_progress,
    confirm_analysis_run,
    enqueue_deep_analysis,
    enqueue_narrative_synthesis,
    refresh_analysis_run,
    start_entities_events_run,
)
from .services.workbench import build_workbench_projection
from .services.provider_config import (
    AnalysisProfile,
    ModelService,
    ModelSettingsError,
    delete_model_service,
    discover_models,
    read_model_settings,
    read_openai_config,
    record_connection_result,
    save_analysis_profile,
    save_model_service,
    probe_selected_model,
    write_openai_config,
)

router = APIRouter()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _model_service_read(service: ModelService) -> ModelServiceRead:
    return ModelServiceRead(
        id=service.id,
        name=service.name,
        service_type=service.service_type,
        base_url=service.base_url,
        configured=service.configured,
        last_tested_at=service.last_tested_at,
        last_test_status=service.last_test_status,
        last_test_message=service.last_test_message,
        capabilities={
            "tested_model": service.capabilities.tested_model,
            "tested_at": service.capabilities.tested_at,
            "ordinary_request": service.capabilities.ordinary_request,
            "structured_output": service.capabilities.structured_output,
            "temperature": service.capabilities.temperature,
            "reasoning_effort": service.capabilities.reasoning_effort,
            "model_catalog": service.capabilities.model_catalog,
        },
    )


def _analysis_profile_read(profile: AnalysisProfile) -> AnalysisProfileRead:
    return AnalysisProfileRead(
        id=profile.id,
        name=profile.name,
        task_type=profile.task_type,
        service_id=profile.service_id,
        model=profile.model,
        temperature=profile.temperature,
        max_output_tokens=profile.max_output_tokens,
        reasoning_effort=profile.reasoning_effort,
        timeout_seconds=profile.timeout_seconds,
        max_retries=profile.max_retries,
    )


def _model_settings_error(error: ModelSettingsError, *, connection: bool = False) -> HTTPException:
    status_code = 502 if connection else 422
    if error.code in {"PROVIDER_NOT_FOUND", "ANALYSIS_PROFILE_NOT_FOUND", "MODEL_NOT_FOUND"}:
        status_code = 404
    if error.code == "PROVIDER_NOT_CONFIGURED":
        status_code = 409
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": error.message},
    )


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


def _analysis_run_read(session: Session, run: AnalysisRun) -> AnalysisRunRead:
    refresh_analysis_run(session, run)
    completed, failed = analysis_run_progress(session, run)
    failure = session.execute(
        select(Task.last_error_code, Task.last_error_message)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.status == TaskStatus.FAILED.value,
        )
        .order_by(AnalysisRunTask.batch_index)
        .limit(1)
    ).one_or_none()
    return AnalysisRunRead(
        id=run.id,
        source_version_id=run.source_version_id,
        stage=run.stage,
        status=run.status,
        total_batches=run.total_batches,
        completed_batches=completed,
        failed_batches=failed,
        failure_code=failure[0] if failure else None,
        failure_message=failure[1] if failure else None,
        created_at=_as_utc(run.created_at),
        finished_at=_as_utc(run.finished_at),
        confirmed_at=_as_utc(run.confirmed_at),
    )


def _analysis_issue_read(issue: AnalysisIssue) -> AnalysisIssueRead:
    return AnalysisIssueRead(
        id=issue.id,
        run_id=issue.run_id,
        target_kind=issue.target_kind,
        target_id=issue.target_id,
        target_label=issue.target_label,
        category=issue.category,
        note=issue.note,
        status=issue.status,
        created_at=_as_utc(issue.created_at),
        resolved_at=_as_utc(issue.resolved_at),
    )


def _entity_candidate_read(candidate: EntityCandidate) -> EntityCandidateRead:
    return EntityCandidateRead(
        id=candidate.id,
        run_id=candidate.run_id,
        source_version_id=candidate.source_version_id,
        name=candidate.name,
        entity_type=candidate.entity_type,
        aliases=json.loads(candidate.aliases_json),
        description=candidate.description,
        evidence_ids=json.loads(candidate.evidence_ids_json),
        status=candidate.status,
        confidence=candidate.confidence,
    )


def _event_candidate_read(candidate: EventCandidate) -> EventCandidateRead:
    return EventCandidateRead(
        id=candidate.id,
        run_id=candidate.run_id,
        source_version_id=candidate.source_version_id,
        title=candidate.title,
        event_type=candidate.event_type,
        summary=candidate.summary,
        participants=json.loads(candidate.participants_json),
        evidence_ids=json.loads(candidate.evidence_ids_json),
        start_char=candidate.start_char,
        end_char=candidate.end_char,
        status=candidate.status,
        confidence=candidate.confidence,
    )


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    return {"status": "ok", "app": request.app.title}


@router.get("/api/settings/openai", response_model=OpenAIConfigRead)
def openai_config_get(request: Request) -> OpenAIConfigRead:
    config = read_openai_config(request.app.state.settings)
    return OpenAIConfigRead(
        configured=config.configured,
        base_url=config.base_url,
        model=config.model,
    )


@router.put("/api/settings/openai", response_model=OpenAIConfigRead)
def openai_config_put(
    payload: OpenAIConfigWrite,
    request: Request,
) -> OpenAIConfigRead:
    try:
        config = write_openai_config(
            request.app.state.settings,
            api_key=payload.api_key,
            base_url=payload.base_url,
            model=payload.model,
        )
    except ValueError as error:
        messages = {
            "OPENAI_API_KEY_REQUIRED": "请输入 API Key。",
            "OPENAI_BASE_URL_INVALID": "接口地址必须使用 HTTPS。",
            "OPENAI_MODEL_REQUIRED": "模型名称不能为空。",
        }
        raise HTTPException(
            status_code=422,
            detail={"code": str(error), "message": messages.get(str(error), "AI 配置无效。")},
        ) from error
    return OpenAIConfigRead(
        configured=config.configured,
        base_url=config.base_url,
        model=config.model,
    )


@router.get("/api/settings/models", response_model=ModelSettingsRead)
def model_settings_get(request: Request) -> ModelSettingsRead:
    settings = read_model_settings(request.app.state.settings)
    return ModelSettingsRead(
        services=[_model_service_read(item) for item in settings.services],
        analysis_profiles=[_analysis_profile_read(item) for item in settings.analysis_profiles],
    )


@router.post(
    "/api/settings/model-services",
    response_model=ModelServiceRead,
    status_code=status.HTTP_201_CREATED,
)
def model_services_create(payload: ModelServiceWrite, request: Request) -> ModelServiceRead:
    try:
        service = save_model_service(
            request.app.state.settings,
            service_id=None,
            name=payload.name,
            service_type=payload.service_type,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
    except ModelSettingsError as error:
        raise _model_settings_error(error) from error
    return _model_service_read(service)


@router.put(
    "/api/settings/model-services/{service_id}",
    response_model=ModelServiceRead,
)
def model_services_update(
    service_id: str,
    payload: ModelServiceWrite,
    request: Request,
) -> ModelServiceRead:
    try:
        service = save_model_service(
            request.app.state.settings,
            service_id=service_id,
            name=payload.name,
            service_type=payload.service_type,
            base_url=payload.base_url,
            api_key=payload.api_key,
        )
    except ModelSettingsError as error:
        raise _model_settings_error(error) from error
    return _model_service_read(service)


@router.delete(
    "/api/settings/model-services/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def model_services_delete(service_id: str, request: Request) -> None:
    try:
        delete_model_service(request.app.state.settings, service_id)
    except ModelSettingsError as error:
        raise _model_settings_error(error) from error


@router.get(
    "/api/settings/model-services/{service_id}/models",
    response_model=ModelCatalogRead,
)
async def model_services_models(service_id: str, request: Request) -> ModelCatalogRead:
    try:
        models = await discover_models(request.app.state.settings, service_id)
    except ModelSettingsError as error:
        try:
            record_connection_result(
                request.app.state.settings,
                service_id,
                success=False,
                message=error.message,
                model_catalog_status="UNSUPPORTED" if error.code in {
                    "PROVIDER_MODELS_UNSUPPORTED",
                    "PROVIDER_MODELS_INVALID",
                    "PROVIDER_MODELS_EMPTY",
                } else "FAILED",
            )
        except ModelSettingsError:
            pass
        raise _model_settings_error(error, connection=True) from error
    record_connection_result(
        request.app.state.settings,
        service_id,
        success=True,
        message=f"已读取 {len(models)} 个可用模型。",
        model_catalog_status="SUPPORTED",
    )
    return ModelCatalogRead(
        service_id=service_id,
        models=models,
        message=f"已读取 {len(models)} 个可用模型。",
    )


@router.post(
    "/api/settings/model-services/{service_id}/test",
    response_model=ModelConnectionRead,
)
async def model_services_test(service_id: str, request: Request) -> ModelConnectionRead:
    try:
        models = await discover_models(request.app.state.settings, service_id)
    except ModelSettingsError as error:
        if error.code in {
            "PROVIDER_MODELS_UNSUPPORTED",
            "PROVIDER_MODELS_INVALID",
            "PROVIDER_MODELS_EMPTY",
        }:
            service = record_connection_result(
                request.app.state.settings,
                service_id,
                success=True,
                message=error.message,
                model_catalog_status="UNSUPPORTED",
            )
            return ModelConnectionRead(
                service=_model_service_read(service),
                model_count=0,
                message=error.message,
            )
        try:
            record_connection_result(
                request.app.state.settings,
                service_id,
                success=False,
                message=error.message,
                model_catalog_status="FAILED",
            )
        except ModelSettingsError:
            pass
        raise _model_settings_error(error, connection=True) from error
    message = f"连接成功，并读取到 {len(models)} 个模型。"
    service = record_connection_result(
        request.app.state.settings,
        service_id,
        success=True,
        message=message,
        model_catalog_status="SUPPORTED",
    )
    return ModelConnectionRead(
        service=_model_service_read(service),
        model_count=len(models),
        message=message,
    )


@router.post(
    "/api/settings/analysis-profiles/{profile_id}/test",
    response_model=ModelProbeRead,
)
async def analysis_profile_test(profile_id: str, request: Request) -> ModelProbeRead:
    try:
        result = await probe_selected_model(request.app.state.settings, profile_id)
    except ModelSettingsError as error:
        raise _model_settings_error(error, connection=True) from error
    return ModelProbeRead(
        service=_model_service_read(result.service),
        message=result.message,
    )


@router.put(
    "/api/settings/analysis-profiles/{profile_id}",
    response_model=AnalysisProfileRead,
)
def analysis_profiles_update(
    profile_id: str,
    payload: AnalysisProfileWrite,
    request: Request,
) -> AnalysisProfileRead:
    try:
        profile = save_analysis_profile(
            request.app.state.settings,
            profile_id=profile_id,
            name=payload.name,
            service_id=payload.service_id,
            model=payload.model,
            temperature=payload.temperature,
            max_output_tokens=payload.max_output_tokens,
            reasoning_effort=payload.reasoning_effort,
            timeout_seconds=payload.timeout_seconds,
            max_retries=payload.max_retries,
        )
    except ModelSettingsError as error:
        raise _model_settings_error(error) from error
    return _analysis_profile_read(profile)


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


@router.post(
    "/api/source-versions/{version_id}/analysis/entities-events/start",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_201_CREATED,
)
def entities_events_start(
    version_id: str,
    request: Request,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    version = session.get(SourceVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="SOURCE_VERSION_NOT_FOUND")
    try:
        run = start_entities_events_run(session, request.app.state.settings, version)
    except SourceImportError as error:
        raise _source_error(error) from error
    return _analysis_run_read(session, run)


@router.get(
    "/api/source-versions/{version_id}/analysis/entities-events",
    response_model=AnalysisRunRead | None,
)
def entities_events_latest(
    version_id: str,
    session: Session = Depends(get_db),
) -> AnalysisRunRead | None:
    if session.get(SourceVersion, version_id) is None:
        raise HTTPException(status_code=404, detail="SOURCE_VERSION_NOT_FOUND")
    run = session.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.source_version_id == version_id,
            AnalysisRun.stage == ANALYSIS_STAGE,
        )
        .order_by(AnalysisRun.created_at.desc())
    )
    return _analysis_run_read(session, run) if run else None


@router.get(
    "/api/analysis-runs/{run_id}/entities",
    response_model=list[EntityCandidateRead],
)
def analysis_entities_list(
    run_id: str,
    session: Session = Depends(get_db),
) -> list[EntityCandidateRead]:
    if session.get(AnalysisRun, run_id) is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    candidates = session.scalars(
        select(EntityCandidate)
        .join(Task, Task.id == EntityCandidate.created_by_task_id)
        .where(
            EntityCandidate.run_id == run_id,
            EntityCandidate.status != CandidateStatus.REJECTED.value,
            Task.status == TaskStatus.SUCCEEDED.value,
            Task.current_attempt_id == EntityCandidate.created_by_attempt_id,
        )
        .order_by(EntityCandidate.name)
    )
    return [_entity_candidate_read(item) for item in candidates]


@router.get(
    "/api/analysis-runs/{run_id}/events",
    response_model=list[EventCandidateRead],
)
def analysis_events_list(
    run_id: str,
    session: Session = Depends(get_db),
) -> list[EventCandidateRead]:
    if session.get(AnalysisRun, run_id) is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    candidates = session.scalars(
        select(EventCandidate)
        .join(Task, Task.id == EventCandidate.created_by_task_id)
        .where(
            EventCandidate.run_id == run_id,
            EventCandidate.status != CandidateStatus.REJECTED.value,
            Task.status == TaskStatus.SUCCEEDED.value,
            Task.current_attempt_id == EventCandidate.created_by_attempt_id,
        )
        .order_by(EventCandidate.start_char, EventCandidate.title)
    )
    return [_event_candidate_read(item) for item in candidates]


@router.get(
    "/api/analysis-runs/{run_id}/workbench",
    response_model=WorkbenchRead,
)
def analysis_workbench_get(
    run_id: str,
    deep_revision: int | None = None,
    session: Session = Depends(get_db),
) -> WorkbenchRead:
    try:
        projection = build_workbench_projection(
            session,
            run_id,
            deep_revision=deep_revision,
        )
    except ValueError as error:
        if str(error) == "ANALYSIS_RUN_NOT_FOUND":
            raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND") from error
        if str(error) == "DEEP_ANALYSIS_REVISION_NOT_FOUND":
            raise HTTPException(
                status_code=404,
                detail={"code": "DEEP_ANALYSIS_REVISION_NOT_FOUND", "message": "没有找到这个拆解版本。"},
            ) from error
        raise
    return WorkbenchRead.model_validate(projection)


@router.post(
    "/api/analysis-runs/{run_id}/narrative/start",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def narrative_synthesis_start(
    run_id: str,
    request: Request,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    task = enqueue_narrative_synthesis(session, request.app.state.settings, run)
    if task is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FOUNDATION_ANALYSIS_NOT_READY",
                "message": "人物和事件基础分析尚未完成，暂时不能整理完整故事结构。",
            },
        )
    return _analysis_run_read(session, run)


@router.post(
    "/api/analysis-runs/{run_id}/deep/start",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def deep_analysis_start(
    run_id: str,
    request: Request,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    task = enqueue_deep_analysis(session, request.app.state.settings, run)
    if task is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NARRATIVE_SYNTHESIS_NOT_READY",
                "message": "故事结构尚未整理完成，暂时不能生成事实状态和核心拆解。",
            },
        )
    return _analysis_run_read(session, run)


def _workbench_target_ids(projection: dict) -> set[str]:
    ids: set[str] = set()
    for collection in (
        projection.get("characters", []),
        projection.get("events", []),
        projection.get("phases", []),
        projection.get("related_entities", []),
    ):
        ids.update(item.get("id") for item in collection if item.get("id"))
    deep = projection.get("deep_analysis") or {}
    for name in (
        "fact_versions",
        "state_changes",
        "actor_knowledge",
        "world_rules",
        "foreshadowing",
        "conflicts",
        "scene_analysis",
        "claims",
    ):
        ids.update(item.get("id") for item in deep.get(name, []) if item.get("id"))
    return ids


@router.get(
    "/api/analysis-runs/{run_id}/issues",
    response_model=list[AnalysisIssueRead],
)
def analysis_issues_list(
    run_id: str,
    session: Session = Depends(get_db),
) -> list[AnalysisIssueRead]:
    if session.get(AnalysisRun, run_id) is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    issues = session.scalars(
        select(AnalysisIssue)
        .where(AnalysisIssue.run_id == run_id)
        .order_by(AnalysisIssue.created_at.desc())
    )
    return [_analysis_issue_read(issue) for issue in issues]


@router.post(
    "/api/analysis-runs/{run_id}/issues",
    response_model=AnalysisIssueRead,
    status_code=status.HTTP_201_CREATED,
)
def analysis_issue_create(
    run_id: str,
    payload: AnalysisIssueCreate,
    session: Session = Depends(get_db),
) -> AnalysisIssueRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    if payload.target_id:
        projection = build_workbench_projection(session, run_id)
        if payload.target_id not in _workbench_target_ids(projection):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "ANALYSIS_TARGET_NOT_FOUND",
                    "message": "要标记的问题对象已经不存在，请刷新工作台后再试。",
                },
            )
    issue = AnalysisIssue(
        run_id=run_id,
        target_kind=payload.target_kind.strip(),
        target_id=payload.target_id,
        target_label=payload.target_label.strip(),
        category=payload.category.strip(),
        note=payload.note.strip(),
        status="OPEN",
    )
    session.add(issue)
    session.commit()
    session.refresh(issue)
    return _analysis_issue_read(issue)


@router.post(
    "/api/analysis-issues/{issue_id}/resolve",
    response_model=AnalysisIssueRead,
)
def analysis_issue_resolve(
    issue_id: str,
    session: Session = Depends(get_db),
) -> AnalysisIssueRead:
    issue = session.get(AnalysisIssue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_ISSUE_NOT_FOUND")
    issue.status = "RESOLVED"
    issue.resolved_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(issue)
    return _analysis_issue_read(issue)


@router.post(
    "/api/analysis-runs/{run_id}/deep/recompute",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def deep_analysis_recompute(
    run_id: str,
    request: Request,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    active = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == "analysis.deep_insights",
            Task.status.in_((TaskStatus.PENDING.value, TaskStatus.RUNNING.value, TaskStatus.RETRY_WAIT.value)),
        )
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "DEEP_ANALYSIS_RUNNING", "message": "深层拆解正在处理中，请等待当前结果完成。"},
        )
    issues = list(session.scalars(
        select(AnalysisIssue).where(
            AnalysisIssue.run_id == run_id,
            AnalysisIssue.status == "OPEN",
        )
    ))
    if not issues:
        raise HTTPException(
            status_code=409,
            detail={"code": "NO_OPEN_ANALYSIS_ISSUES", "message": "请先标记需要重新检查的问题。"},
        )
    revision_requests = [
        {
            "issue_id": issue.id,
            "target_kind": issue.target_kind,
            "target_id": issue.target_id,
            "target_label": issue.target_label,
            "category": issue.category,
            "note": issue.note,
        }
        for issue in issues
    ]
    narrative_targets = {"CHARACTER", "STORY", "PLOT", "EVENT", "RELATION"}
    needs_narrative = any(
        issue.target_kind in narrative_targets for issue in issues
    )
    if needs_narrative:
        active_narrative = session.scalar(
            select(Task)
            .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
            .where(
                AnalysisRunTask.run_id == run_id,
                Task.kind == "analysis.narrative_synthesis",
                Task.status.in_((TaskStatus.PENDING.value, TaskStatus.RUNNING.value, TaskStatus.RETRY_WAIT.value)),
            )
        )
        if active_narrative is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "NARRATIVE_SYNTHESIS_RUNNING", "message": "故事结构正在重新整理，请等待当前结果完成。"},
            )
        task = enqueue_narrative_synthesis(
            session,
            request.app.state.settings,
            run,
            force=True,
            revision_requests=revision_requests,
        )
    else:
        task = enqueue_deep_analysis(
            session,
            request.app.state.settings,
            run,
            force=True,
            revision_requests=revision_requests,
        )
    if task is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "NARRATIVE_SYNTHESIS_NOT_READY", "message": "故事结构尚未完成，暂时不能重新分析。"},
        )
    return _analysis_run_read(session, run)


@router.get(
    "/api/analysis-runs/{run_id}/deep/revisions",
    response_model=list[DeepAnalysisRevisionRead],
)
def deep_analysis_revisions(
    run_id: str,
    session: Session = Depends(get_db),
) -> list[DeepAnalysisRevisionRead]:
    if session.get(AnalysisRun, run_id) is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    rows = session.scalars(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run_id)
        .order_by(DeepAnalysis.revision_no)
    )
    return [
        DeepAnalysisRevisionRead(
            revision_no=row.revision_no,
            created_at=_as_utc(row.created_at),
            prompt_version=row.prompt_version,
        )
        for row in rows
    ]


@router.get(
    "/api/analysis-runs/{run_id}/deep/diff",
    response_model=DeepAnalysisDiffRead,
)
def deep_analysis_diff(
    run_id: str,
    from_revision: int | None = None,
    to_revision: int | None = None,
    session: Session = Depends(get_db),
) -> DeepAnalysisDiffRead:
    if session.get(AnalysisRun, run_id) is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    rows = list(session.scalars(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run_id)
        .order_by(DeepAnalysis.revision_no)
    ))
    if len(rows) < 2:
        raise HTTPException(status_code=409, detail={"code": "DEEP_REVISION_NOT_ENOUGH", "message": "当前还没有两个可比较的拆解版本。"})
    by_revision = {row.revision_no: row for row in rows}
    from_revision = from_revision or rows[-2].revision_no
    to_revision = to_revision or rows[-1].revision_no
    before = by_revision.get(from_revision)
    after = by_revision.get(to_revision)
    if before is None or after is None or before.revision_no == after.revision_no:
        raise HTTPException(status_code=422, detail={"code": "DEEP_REVISION_INVALID", "message": "要比较的拆解版本不存在。"})

    collections = ("fact_versions", "state_changes", "actor_knowledge", "world_rules", "foreshadowing", "conflicts", "scene_analysis", "claims")
    def key_for(collection: str, item: dict) -> str:
        if collection == "fact_versions":
            return f"{item.get('subject')}:{item.get('predicate')}:{item.get('valid_from_chapter')}"
        if collection == "state_changes":
            return f"{item.get('subject')}:{item.get('aspect')}:{item.get('chapter_ordinal')}"
        if collection == "actor_knowledge":
            return f"{item.get('actor')}:{item.get('proposition')}:{item.get('chapter_ordinal')}"
        if collection == "scene_analysis":
            return str(item.get("chapter_ordinal"))
        if collection == "claims":
            return f"{item.get('claim_kind')}:{item.get('scope')}:{item.get('claim_text')}"
        return str(item.get("title"))
    before_payload = json.loads(before.payload_json)
    after_payload = json.loads(after.payload_json)
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    changed_counts: dict[str, int] = {}
    for collection in collections:
        old = {key_for(collection, item): item for item in before_payload.get(collection, [])}
        new = {key_for(collection, item): item for item in after_payload.get(collection, [])}
        added[collection] = [new[key].get("title") or new[key].get("claim_text") or key for key in sorted(new.keys() - old.keys())]
        removed[collection] = [old[key].get("title") or old[key].get("claim_text") or key for key in sorted(old.keys() - new.keys())]
        changed_counts[collection] = sum(1 for key in new.keys() & old.keys() if new[key] != old[key])
    return DeepAnalysisDiffRead(
        from_revision=before.revision_no,
        to_revision=after.revision_no,
        added=added,
        removed=removed,
        changed_counts=changed_counts,
    )


@router.post(
    "/api/analysis-runs/{run_id}/confirm",
    response_model=AnalysisRunRead,
)
def analysis_run_confirm(
    run_id: str,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    try:
        confirmed = confirm_analysis_run(session, run)
    except SourceImportError as error:
        raise _source_error(error) from error
    return _analysis_run_read(session, confirmed)


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
