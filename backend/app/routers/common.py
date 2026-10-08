from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import (
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
    NarrativeSynthesis,
    SourceIssue,
    SourceUnit,
    SourceVersion,
    Task,
    TaskAttempt,
    TaskStatus,
)
from ..schemas import (
    ArtifactRead,
    AnalysisCallDiagnosticRead,
    AnalysisRunRead,
    AnalysisRunDiagnosticsRead,
    AnalysisStageDiagnosticRead,
    ProviderConfirmationRead,
    AnalysisIssueRead,
    AnalysisProfileRead,
    EntityCandidateRead,
    EventCandidateRead,
    ModelServiceRead,
    SourceIssueRead,
    SourceStructureRead,
    SourceUnitRead,
    SourceVersionRead,
    TaskRead,
)
from ..services.source_import import SourceImportError
from ..services.provider_config import (
    AnalysisProfile,
    ModelService,
    ModelSettingsError,
)
from ..services.analysis import (
    analysis_run_progress,
    refresh_analysis_run,
)
from ..services.character_design import CHARACTER_DESIGN_TASK_KIND
from ..services.chapter_end_hooks import CHAPTER_END_HOOKS_TASK_KIND
from ..services.opening_hook_payoffs import OPENING_HOOK_PAYOFFS_TASK_KIND
from ..services.opening_payoff_candidates import OPENING_PAYOFF_CANDIDATES_TASK_KIND
from ..services.learning_report import LEARNING_REPORT_TASK_KIND


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_dict(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
        context_window_tokens=profile.context_window_tokens,
        failover_targets=[
            {"service_id": item.service_id, "model": item.model}
            for item in profile.failover_targets
        ],
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


def _source_structure_read(result) -> SourceStructureRead:
    return SourceStructureRead(
        version=SourceVersionRead.model_validate(result.version),
        units=[SourceUnitRead.model_validate(unit) for unit in result.units],
        issues=[_source_issue_read(issue) for issue in result.issues],
        selected_unit_id=result.selected_unit_id,
    )


def _analysis_run_read(session: Session, run: AnalysisRun) -> AnalysisRunRead:
    refresh_analysis_run(session, run)
    completed, failed = analysis_run_progress(session, run)
    failure = session.execute(
        select(Task.last_error_code, Task.last_error_message, AnalysisRunTask.batch_index)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.status == TaskStatus.FAILED.value,
        )
        .order_by(AnalysisRunTask.batch_index.desc())
        .limit(1)
    ).one_or_none()
    synthesis = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run.id)
    )
    deep_analysis = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run.id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    usable_task_ids = {
        value
        for value in [
            synthesis.created_by_task_id if synthesis is not None else None,
            deep_analysis.created_by_task_id if deep_analysis is not None else None,
        ]
        if value
    }
    usable_batch_index = session.scalar(
        select(AnalysisRunTask.batch_index)
        .where(
            AnalysisRunTask.run_id == run.id,
            AnalysisRunTask.task_id.in_(usable_task_ids),
        )
        .order_by(AnalysisRunTask.batch_index.desc())
        .limit(1)
    ) if usable_task_ids else None
    latest_update_failed = bool(
        failure
        and (usable_batch_index is None or int(failure[2]) > int(usable_batch_index))
    )
    waiting_task = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run.id,
            Task.status == TaskStatus.WAITING_CONFIRMATION.value,
        )
        .order_by(AnalysisRunTask.batch_index)
    )
    provider_confirmation = None
    if waiting_task is not None:
        payload = _json_dict(waiting_task.payload_json)
        pending = payload.get("pending_provider_switch")
        if isinstance(pending, dict):
            provider_confirmation = ProviderConfirmationRead(
                current_service_name=str(pending.get("current_service_name") or "当前服务"),
                current_model=str(pending.get("current_model") or "当前模型"),
                next_service_name=str(pending.get("next_service_name") or "备用服务"),
                next_model=str(pending.get("next_model") or "备用模型"),
                failure_count=int(pending.get("failure_count") or 0),
                threshold=int(pending.get("threshold") or 3),
                error_code=str(pending.get("error_code") or "PROVIDER_FAILURE"),
                message=str(pending.get("message") or "当前模型服务连续失败。"),
            )
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
        has_usable_result=synthesis is not None,
        usable_result_level=(
            "FULL" if deep_analysis is not None
            else "STORY" if synthesis is not None
            else "FOUNDATION" if completed > 0
            else "NONE"
        ),
        latest_update_failed=latest_update_failed,
        created_at=_as_utc(run.created_at),
        finished_at=_as_utc(run.finished_at),
        confirmed_at=_as_utc(run.confirmed_at),
        provider_confirmation=provider_confirmation,
    )


_ANALYSIS_STAGE_DIAGNOSTICS = (
    ("analysis.entities_events", "人物与事件抽取"),
    ("analysis.hierarchical_digest", "长篇分层整理"),
    ("analysis.narrative_synthesis", "故事结构整理"),
    ("analysis.deep_insights", "事实与核心分析"),
    (CHARACTER_DESIGN_TASK_KIND, "主角双层欲望与最小完整集证据"),
    (CHAPTER_END_HOOKS_TASK_KIND, "逐章章末钩类型与节律账本"),
    (OPENING_PAYOFF_CANDIDATES_TASK_KIND, "卖点首次兑现连续候选账本"),
    (OPENING_HOOK_PAYOFFS_TASK_KIND, "前三章章末钩兑现追踪表"),
    (LEARNING_REPORT_TASK_KIND, "创作学习报告"),
)

_NARRATIVE_COMPONENT_LABELS = {
    "overview": "故事总览",
    "characters": "人物档案",
    "plot": "剧情阶段",
    "relations": "人物与事件关系",
}


def _analysis_run_diagnostics(
    session: Session,
    run: AnalysisRun,
) -> AnalysisRunDiagnosticsRead:
    tasks = list(session.scalars(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(AnalysisRunTask.run_id == run.id)
        .order_by(AnalysisRunTask.batch_index)
    ))
    task_ids = [task.id for task in tasks]
    attempts = (
        list(session.scalars(
            select(TaskAttempt)
            .where(TaskAttempt.task_id.in_(task_ids))
            .order_by(TaskAttempt.started_at)
        ))
        if task_ids
        else []
    )
    attempts_by_task: dict[str, list[TaskAttempt]] = {}
    for attempt in attempts:
        attempts_by_task.setdefault(attempt.task_id, []).append(attempt)

    stage_rows: list[AnalysisStageDiagnosticRead] = []
    for kind, label in _ANALYSIS_STAGE_DIAGNOSTICS:
        stage_tasks = [task for task in tasks if task.kind == kind]
        if kind in {
            "analysis.hierarchical_digest",
            CHARACTER_DESIGN_TASK_KIND,
            CHAPTER_END_HOOKS_TASK_KIND,
            OPENING_PAYOFF_CANDIDATES_TASK_KIND,
            OPENING_HOOK_PAYOFFS_TASK_KIND,
            LEARNING_REPORT_TASK_KIND,
        } and not stage_tasks:
            continue
        stage_attempts = [
            attempt
            for task in stage_tasks
            for attempt in attempts_by_task.get(task.id, [])
        ]
        if any(
            task.status in {
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.CANCEL_REQUESTED.value,
            }
            for task in stage_tasks
        ):
            stage_status = "RUNNING"
        elif (
            kind in {
                "analysis.deep_insights",
                CHARACTER_DESIGN_TASK_KIND,
                CHAPTER_END_HOOKS_TASK_KIND,
                OPENING_PAYOFF_CANDIDATES_TASK_KIND,
                OPENING_HOOK_PAYOFFS_TASK_KIND,
                LEARNING_REPORT_TASK_KIND,
            }
            and any(task.status == TaskStatus.SUCCEEDED.value for task in stage_tasks)
        ):
            stage_status = "SUCCEEDED"
        elif any(task.status == TaskStatus.FAILED.value for task in stage_tasks):
            stage_status = "FAILED"
        elif stage_tasks and all(
            task.status == TaskStatus.SUCCEEDED.value for task in stage_tasks
        ):
            stage_status = "SUCCEEDED"
        elif stage_tasks and all(
            task.status == TaskStatus.CANCELLED.value for task in stage_tasks
        ):
            stage_status = "CANCELLED"
        else:
            stage_status = "PENDING"

        usage = [_json_dict(attempt.usage_json) for attempt in stage_attempts]
        diagnostics = [
            _json_dict(attempt.diagnostics_json) for attempt in stage_attempts
        ]
        context_rows = [
            item.get("context")
            for item in diagnostics
            if isinstance(item.get("context"), dict)
        ]
        omitted_reasons: dict[str, int] = {}
        for context in context_rows:
            for reason, count in (context.get("omitted_reasons") or {}).items():
                omitted_reasons[str(reason)] = omitted_reasons.get(str(reason), 0) + int(count or 0)
        failed_attempts = [
            attempt for attempt in stage_attempts if attempt.error_message
        ]
        latest_error = (
            failed_attempts[-1].error_message
            if failed_attempts and stage_status != "SUCCEEDED"
            else None
        )
        call_rows: list[AnalysisCallDiagnosticRead] = []
        for task in stage_tasks:
            task_payload = _json_dict(task.payload_json)
            component = str(task_payload.get("narrative_component") or "") or None
            for attempt in attempts_by_task.get(task.id, []):
                attempt_usage = _json_dict(attempt.usage_json)
                attempt_diagnostics = _json_dict(attempt.diagnostics_json)
                context = attempt_diagnostics.get("context")
                if not isinstance(context, dict):
                    context = {}
                artifact = session.scalar(
                    select(Artifact).where(Artifact.created_by_attempt_id == attempt.id)
                )
                input_path = attempt_diagnostics.get("request_input_path")
                output_path = attempt_diagnostics.get("raw_output_path")
                finished_at = _as_utc(attempt.finished_at)
                started_at = _as_utc(attempt.started_at)
                duration_seconds = (
                    max(0.0, (finished_at - started_at).total_seconds())
                    if finished_at is not None
                    else 0.0
                )
                call_rows.append(AnalysisCallDiagnosticRead(
                    attempt_id=attempt.id,
                    task_id=task.id,
                    task_kind=task.kind,
                    component=component,
                    component_label=_NARRATIVE_COMPONENT_LABELS.get(component or ""),
                    attempt_no=attempt.attempt_no,
                    status=attempt.status,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=round(duration_seconds, 3),
                    provider_name=attempt.provider_name,
                    model=str(attempt_diagnostics.get("model") or "") or None,
                    transport_mode=(
                        str(attempt_diagnostics.get("transport_mode") or "") or None
                    ),
                    prompt_tokens=int(attempt_usage.get("prompt_tokens") or 0),
                    completion_tokens=int(attempt_usage.get("completion_tokens") or 0),
                    input_chars=int(attempt_diagnostics.get("input_chars") or 0),
                    output_chars=int(attempt_diagnostics.get("output_chars") or 0),
                    selected_material_count=int(context.get("selected_count") or 0),
                    selected_material_chars=int(context.get("selected_chars") or 0),
                    omitted_material_count=int(context.get("omitted_count") or 0),
                    omitted_material_chars=int(context.get("omitted_chars") or 0),
                    omitted_material_reasons={
                        str(reason): int(count or 0)
                        for reason, count in (context.get("omitted_reasons") or {}).items()
                    },
                    error_message=attempt.error_message,
                    result_artifact_id=artifact.id if artifact is not None else None,
                    has_input_content=bool(input_path or artifact is not None),
                    has_output_content=bool(output_path or artifact is not None),
                    can_retry_component=bool(component),
                ))
        stage_rows.append(AnalysisStageDiagnosticRead(
            key=kind,
            label=label,
            status=stage_status,
            task_count=len(stage_tasks),
            attempt_count=len(stage_attempts),
            retry_count=sum(max(0, task.attempts - 1) for task in stage_tasks),
            duration_seconds=round(sum(row.duration_seconds for row in call_rows), 3),
            prompt_tokens=sum(int(item.get("prompt_tokens") or 0) for item in usage),
            completion_tokens=sum(int(item.get("completion_tokens") or 0) for item in usage),
            input_chars=sum(int(item.get("input_chars") or 0) for item in diagnostics),
            output_chars=sum(int(item.get("output_chars") or 0) for item in diagnostics),
            selected_material_count=sum(int(item.get("selected_count") or 0) for item in context_rows),
            selected_material_chars=sum(int(item.get("selected_chars") or 0) for item in context_rows),
            omitted_material_count=sum(int(item.get("omitted_count") or 0) for item in context_rows),
            omitted_material_chars=sum(int(item.get("omitted_chars") or 0) for item in context_rows),
            omitted_material_reasons=omitted_reasons,
            latest_error=latest_error,
            calls=call_rows,
        ))

    current = next(
        (row.label for row in stage_rows if row.status != "SUCCEEDED"),
        "全部分析已经完成",
    )
    return AnalysisRunDiagnosticsRead(
        run_id=run.id,
        current_step=current,
        attempt_count=sum(row.attempt_count for row in stage_rows),
        retry_count=sum(row.retry_count for row in stage_rows),
        duration_seconds=round(sum(row.duration_seconds for row in stage_rows), 3),
        prompt_tokens=sum(row.prompt_tokens for row in stage_rows),
        completion_tokens=sum(row.completion_tokens for row in stage_rows),
        input_chars=sum(row.input_chars for row in stage_rows),
        output_chars=sum(row.output_chars for row in stage_rows),
        stages=stage_rows,
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
        "knowledge_transfers",
        "world_rules",
        "foreshadowing",
        "conflicts",
        "scene_analysis",
        "claims",
    ):
        ids.update(item.get("id") for item in deep.get(name, []) if item.get("id"))
    return ids


def _workspace_diagnostic_text(settings: Settings, relative_path: object) -> str | None:
    if not isinstance(relative_path, str) or not relative_path:
        return None
    root = settings.workspace_dir.resolve()
    path = (root / Path(relative_path)).resolve()
    if path != root and root not in path.parents:
        return None
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else None
    except OSError:
        return None
