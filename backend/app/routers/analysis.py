from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import get_db
from ..models import (
    Artifact,
    ArtifactBlob,
    ArtifactStatus,
    AnalysisRun,
    AnalysisRunTask,
    AnalysisIssue,
    CandidateStatus,
    EntityCandidate,
    EventCandidate,
    DeepAnalysis,
    NarrativeSynthesis,
    PersonIdentityDecision,
    SourceVersion,
    Task,
    TaskAttempt,
    TaskStatus,
)
from ..repositories import resolve_provider_confirmation
from ..schemas import (
    AnalysisCallContentRead,
    AnalysisIssueCreate,
    AnalysisIssueRead,
    AnalysisRunDiagnosticsRead,
    AnalysisRunRead,
    AnalysisUsageEstimateRead,
    DeepAnalysisDiffRead,
    DeepAnalysisRevisionRead,
    DeepRevisionImpactRead,
    EntityCandidateRead,
    EventCandidateRead,
    PersonIdentityDecisionWrite,
    ProviderConfirmationWrite,
    WorkbenchRead,
    WorkbenchStateAtChapterRead,
)
from ..services.source_import import SourceImportError
from ..services.analysis import (
    ANALYSIS_STAGE,
    build_deep_revision_impact,
    confirm_analysis_run,
    enqueue_deep_analysis,
    enqueue_narrative_synthesis,
    estimate_analysis_usage,
    start_entities_events_run,
)
from ..services.character_design import enqueue_character_design_evidence
from ..services.chapter_end_hooks import enqueue_chapter_end_hooks
from ..services.opening_hook_payoffs import enqueue_opening_hook_payoffs
from ..services.opening_structure import enqueue_opening_structure
from ..services.workbench import (
    build_state_at_chapter_projection,
    build_workbench_projection,
)
from ..services.pacing_extractor import build_unified_scene_and_pacing_projection
from .common import (
    _NARRATIVE_COMPONENT_LABELS,
    _analysis_issue_read,
    _analysis_run_diagnostics,
    _analysis_run_read,
    _as_utc,
    _entity_candidate_read,
    _event_candidate_read,
    _json_dict,
    _source_error,
    _workbench_target_ids,
    _workspace_diagnostic_text,
)

router = APIRouter()


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
        .join(NarrativeSynthesis, NarrativeSynthesis.run_id == AnalysisRun.id)
        .where(
            AnalysisRun.source_version_id == version_id,
            AnalysisRun.stage == ANALYSIS_STAGE,
        )
        .order_by(AnalysisRun.created_at.desc())
    )
    if run is None:
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
    "/api/source-versions/{version_id}/analysis/entities-events/estimate",
    response_model=AnalysisUsageEstimateRead,
)
def entities_events_estimate(
    version_id: str,
    request: Request,
    session: Session = Depends(get_db),
) -> AnalysisUsageEstimateRead:
    version = session.get(SourceVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="SOURCE_VERSION_NOT_FOUND")
    try:
        estimate = estimate_analysis_usage(session, request.app.state.settings, version)
    except SourceImportError as error:
        raise _source_error(error) from error
    return AnalysisUsageEstimateRead.model_validate(estimate)


@router.get(
    "/api/analysis-runs/{run_id}/diagnostics",
    response_model=AnalysisRunDiagnosticsRead,
)
def analysis_run_diagnostics_get(
    run_id: str,
    session: Session = Depends(get_db),
) -> AnalysisRunDiagnosticsRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    return _analysis_run_diagnostics(session, run)


@router.get(
    "/api/analysis-runs/{run_id}/attempts/{attempt_id}/content",
    response_model=AnalysisCallContentRead,
)
def analysis_call_content_get(
    run_id: str,
    attempt_id: str,
    request: Request,
    session: Session = Depends(get_db),
) -> AnalysisCallContentRead:
    attempt = session.scalar(
        select(TaskAttempt)
        .join(Task, Task.id == TaskAttempt.task_id)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(AnalysisRunTask.run_id == run_id, TaskAttempt.id == attempt_id)
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_ATTEMPT_NOT_FOUND")
    settings: Settings = request.app.state.settings
    diagnostics = _json_dict(attempt.diagnostics_json)
    input_text = _workspace_diagnostic_text(settings, diagnostics.get("request_input_path"))
    output_text = _workspace_diagnostic_text(settings, diagnostics.get("raw_output_path"))
    artifact = session.scalar(
        select(Artifact).where(Artifact.created_by_attempt_id == attempt.id)
    )
    if artifact is not None:
        blob = session.get(ArtifactBlob, artifact.blob_id)
        if blob is not None and blob.status == ArtifactStatus.READY.value:
            raw = _workspace_diagnostic_text(settings, blob.relative_path)
            if raw is not None:
                try:
                    artifact_payload = json.loads(raw)
                except json.JSONDecodeError:
                    artifact_payload = {}
                request_payload = artifact_payload.get("request")
                if input_text is None and isinstance(request_payload, dict):
                    stored_input = request_payload.get("input")
                    if isinstance(stored_input, str):
                        input_text = stored_input
                if output_text is None and "response" in artifact_payload:
                    output_text = json.dumps(
                        artifact_payload["response"],
                        ensure_ascii=False,
                        indent=2,
                    )
    return AnalysisCallContentRead(
        attempt_id=attempt.id,
        input_text=input_text,
        output_text=output_text,
        input_note=(
            None
            if input_text is not None
            else "这次历史调用只保存了输入长度和校验指纹，无法还原当时的完整输入；从现在开始的新调用会保存完整输入。"
        ),
        output_note=(
            None
            if output_text is not None
            else "这次调用没有留下可读取的模型输出，可能在收到响应前就已失败。"
        ),
    )


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
    view: str | None = None,
    include_question_evidence: bool = True,
    session: Session = Depends(get_db),
) -> WorkbenchRead:
    if view in {"archive", "overview"}:
        include_question_evidence = False
    try:
        projection = build_workbench_projection(
            session,
            run_id,
            deep_revision=deep_revision,
            include_question_evidence=include_question_evidence,
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


@router.get("/api/analysis-runs/{run_id}/workbench/pacing")
def analysis_workbench_pacing_get(
    run_id: str,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        pacing_data = build_unified_scene_and_pacing_projection(session, run_id)
    except ValueError as error:
        if str(error) == "ANALYSIS_RUN_NOT_FOUND":
            raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND") from error
        raise
    return {
        "narrative_scene_tree": pacing_data.get("narrative_scene_tree"),
        "chapter_end_hooks_status": pacing_data.get("chapter_end_hooks_status"),
        "chapter_end_hooks_evidence": pacing_data.get("chapter_end_hooks_evidence"),
        "chapter_end_hooks": pacing_data.get("chapter_end_hooks", []),
        "opening_hook_payoffs_status": pacing_data.get("opening_hook_payoffs_status"),
        "opening_hook_payoffs_evidence": pacing_data.get("opening_hook_payoffs_evidence"),
        "opening_hook_payoffs": pacing_data.get("opening_hook_payoffs", []),
        "opening_structure_status": pacing_data.get("opening_structure_status"),
        "opening_structure_evidence": pacing_data.get("opening_structure_evidence"),
    }


@router.post(
    "/api/analysis-runs/{run_id}/person-identity-decisions",
    response_model=WorkbenchRead,
)
def person_identity_decision_create(
    run_id: str,
    payload: PersonIdentityDecisionWrite,
    session: Session = Depends(get_db),
) -> WorkbenchRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    if run.status == "CONFIRMED":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ANALYSIS_ALREADY_CONFIRMED",
                "message": "这次拆解已经确认，人物身份不能再原地修改。",
            },
        )
    projection = build_workbench_projection(session, run_id)
    candidate = next(
        (
            item
            for item in projection.get("person_identity_candidates", [])
            if item.get("candidate_key") == payload.candidate_key
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PERSON_IDENTITY_CANDIDATE_NOT_FOUND",
                "message": "这组人物身份候选已经处理或不存在，请刷新后再试。",
            },
        )
    decision = PersonIdentityDecision(
        run_id=run_id,
        pair_key=candidate["candidate_key"],
        left_name=candidate["left_name"],
        right_name=candidate["right_name"],
        canonical_name=(
            candidate["recommended_name"]
            if payload.decision == "SAME"
            else None
        ),
        decision=payload.decision,
    )
    session.add(decision)
    session.commit()
    return WorkbenchRead.model_validate(
        build_workbench_projection(session, run_id)
    )


@router.delete(
    "/api/person-identity-decisions/{decision_id}",
    response_model=WorkbenchRead,
)
def person_identity_decision_delete(
    decision_id: str,
    session: Session = Depends(get_db),
) -> WorkbenchRead:
    decision = session.get(PersonIdentityDecision, decision_id)
    if decision is None:
        raise HTTPException(
            status_code=404,
            detail="PERSON_IDENTITY_DECISION_NOT_FOUND",
        )
    run = session.get(AnalysisRun, decision.run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    if run.status == "CONFIRMED":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ANALYSIS_ALREADY_CONFIRMED",
                "message": "这次拆解已经确认，人物身份不能再原地修改。",
            },
        )
    run_id = decision.run_id
    session.delete(decision)
    session.commit()
    return WorkbenchRead.model_validate(
        build_workbench_projection(session, run_id)
    )


@router.get(
    "/api/analysis-runs/{run_id}/state-at-chapter",
    response_model=WorkbenchStateAtChapterRead,
)
def analysis_state_at_chapter_get(
    run_id: str,
    chapter_ordinal: int,
    deep_revision: int | None = None,
    session: Session = Depends(get_db),
) -> WorkbenchStateAtChapterRead:
    try:
        projection = build_state_at_chapter_projection(
            session,
            run_id,
            chapter_ordinal,
            deep_revision=deep_revision,
        )
    except ValueError as error:
        code = str(error)
        if code == "ANALYSIS_RUN_NOT_FOUND":
            raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND") from error
        if code == "DEEP_ANALYSIS_REVISION_NOT_FOUND":
            raise HTTPException(
                status_code=404,
                detail={"code": code, "message": "没有找到这个拆解版本。"},
            ) from error
        if code == "CHAPTER_ORDINAL_INVALID":
            raise HTTPException(
                status_code=422,
                detail={"code": code, "message": "请选择这本小说中存在的章节。"},
            ) from error
        raise
    return WorkbenchStateAtChapterRead.model_validate(projection)


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
    "/api/analysis-runs/{run_id}/narrative/components/{component}/retry",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def narrative_component_retry(
    run_id: str,
    component: str,
    request: Request,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    component = component.lower()
    if component not in _NARRATIVE_COMPONENT_LABELS:
        raise HTTPException(
            status_code=404,
            detail={"code": "NARRATIVE_COMPONENT_NOT_FOUND", "message": "没有找到这个故事结构版块。"},
        )
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    synthesis = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run_id)
    )
    if synthesis is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "NARRATIVE_SYNTHESIS_NOT_READY", "message": "故事结构还没有形成可用版本，暂时不能只重做其中一个版块。"},
        )
    active = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind.in_(("analysis.narrative_synthesis", "analysis.deep_insights")),
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "ANALYSIS_UPDATE_RUNNING", "message": "当前还有分析任务正在处理，请完成或停止后再单独重做这个版块。"},
        )
    label = _NARRATIVE_COMPONENT_LABELS[component]
    task = enqueue_narrative_synthesis(
        session,
        request.app.state.settings,
        run,
        force=True,
        revision_requests=[{
            "target_kind": component.upper(),
            "target_id": None,
            "target_label": label,
            "category": "REGENERATE_COMPONENT",
            "note": f"只重新生成{label}，保留其他已经成功的故事结构版块。",
        }],
        components=[component],
        enqueue_deep_after_narrative=False,
    )
    if task is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "NARRATIVE_SYNTHESIS_NOT_READY", "message": "基础人物和事件尚未准备完成，暂时不能重新生成。"},
        )
    return _analysis_run_read(session, run)


@router.post(
    "/api/analysis-runs/{run_id}/narrative/repair",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def narrative_synthesis_repair(
    run_id: str,
    request: Request,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    projection = build_workbench_projection(session, run_id)
    missing = [
        item["name"]
        for item in projection.get("characters", [])
        if item.get("role_required")
        and item.get("role") == "UNCLASSIFIED"
    ]
    if not missing:
        raise HTTPException(
            status_code=409,
            detail={"code": "NARRATIVE_REPAIR_NOT_NEEDED", "message": "人物和剧情结构已经完整，不需要重新整理。"},
        )
    active = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind.in_(("analysis.narrative_synthesis", "analysis.deep_insights")),
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
            )),
        )
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "ANALYSIS_REPAIR_RUNNING", "message": "人物和剧情正在重新整理，请等待当前处理完成。"},
        )
    task = enqueue_narrative_synthesis(
        session,
        request.app.state.settings,
        run,
        force=True,
        revision_requests=[{
            "target_kind": "STORY",
            "target_id": None,
            "target_label": "人物角色覆盖",
            "category": "INCOMPLETE",
            "note": f"以下人物尚未完成角色定位：{'、'.join(missing)}。请重新整理全部人物、关系和剧情阶段。",
        }],
    )
    if task is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "NARRATIVE_SYNTHESIS_NOT_READY", "message": "基础人物与事件尚未准备完成，暂时不能重新整理。"},
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
    synthesis = session.scalar(
        select(NarrativeSynthesis).where(NarrativeSynthesis.run_id == run_id)
    )
    latest_deep = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run_id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    force = bool(
        synthesis is not None
        and latest_deep is not None
        and synthesis.created_at > latest_deep.created_at
    )
    task = enqueue_deep_analysis(
        session,
        request.app.state.settings,
        run,
        force=force,
        revision_requests=([
            {
                "target_kind": "STORY",
                "target_id": None,
                "target_label": "更新后的故事结构",
                "category": "NARRATIVE_UPDATED",
                "note": "故事结构已有局部更新，请基于最新版本重新生成深层拆解。",
            }
        ] if force else None),
    )
    if task is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NARRATIVE_SYNTHESIS_NOT_READY",
                "message": "故事结构尚未整理完成，暂时不能生成事实状态和核心拆解。",
            },
        )
    return _analysis_run_read(session, run)


@router.post(
    "/api/analysis-runs/{run_id}/character-design/start",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def character_design_start(
    run_id: str,
    request: Request,
    force: bool = False,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    active_deep = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == "analysis.deep_insights",
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
    )
    if active_deep is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEEP_ANALYSIS_RUNNING",
                "message": "事实状态和核心拆解仍在生成，请完成后再分析主角证据。",
            },
        )
    task = enqueue_character_design_evidence(
        session,
        request.app.state.settings,
        run,
        force=force,
    )
    if task is None:
        projection = build_workbench_projection(session, run_id)
        if projection.get("character_design_status") == "READY":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CHARACTER_DESIGN_ALREADY_CURRENT",
                    "message": "当前主角证据表已经基于最新拆解生成；需要重做时请明确使用重新分析。",
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CHARACTER_DESIGN_SOURCE_NOT_READY",
                "message": "主角、故事结构或深层拆解尚未就绪，暂时不能生成 2.2 证据表。",
            },
        )
    return _analysis_run_read(session, run)


@router.post(
    "/api/analysis-runs/{run_id}/chapter-end-hooks/start",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def chapter_end_hooks_start(
    run_id: str,
    request: Request,
    force: bool = False,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    active_deep = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == "analysis.deep_insights",
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
    )
    if active_deep is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEEP_ANALYSIS_RUNNING",
                "message": "事实状态和核心拆解仍在生成，请完成后再分析逐章章末钩。",
            },
        )
    task = enqueue_chapter_end_hooks(
        session,
        request.app.state.settings,
        run,
        force=force,
    )
    if task is None:
        projection = build_workbench_projection(session, run_id)
        if projection.get("chapter_end_hooks_status") == "READY":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CHAPTER_END_HOOKS_ALREADY_CURRENT",
                    "message": "当前章末钩账本已经基于最新正文和拆解生成；需要重做时请明确使用重新分析。",
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CHAPTER_END_HOOKS_SOURCE_NOT_READY",
                "message": "真实章节、故事结构或深层拆解尚未就绪，暂时不能生成 4.9 逐章账本。",
            },
        )
    return _analysis_run_read(session, run)


@router.post(
    "/api/analysis-runs/{run_id}/opening-hook-payoffs/start",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def opening_hook_payoffs_start(
    run_id: str,
    request: Request,
    force: bool = False,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    task = enqueue_opening_hook_payoffs(
        session,
        request.app.state.settings,
        run,
        force=force,
    )
    if task is None:
        projection = build_workbench_projection(session, run_id)
        if projection.get("opening_hook_payoffs_status") == "READY":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "OPENING_HOOK_PAYOFFS_ALREADY_CURRENT",
                    "message": "当前 3.4 兑现追踪已经基于最新正文和 4.9 账本生成；需要重做时请明确使用重新分析。",
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OPENING_HOOK_PAYOFFS_SOURCE_NOT_READY",
                "message": "需要先完成最新版 4.9 全书章末钩账本，才能追踪前三章钩子的后续兑现。",
            },
        )
    return _analysis_run_read(session, run)


@router.post(
    "/api/analysis-runs/{run_id}/opening-structure/start",
    response_model=AnalysisRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def opening_structure_start(
    run_id: str,
    request: Request,
    force: bool = False,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    active_deep = session.scalar(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.kind == "analysis.deep_insights",
            Task.status.in_((
                TaskStatus.PENDING.value,
                TaskStatus.RUNNING.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.WAITING_CONFIRMATION.value,
            )),
        )
    )
    if active_deep is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEEP_ANALYSIS_RUNNING",
                "message": "事实状态和核心拆解仍在生成，请完成后再精读前三章。",
            },
        )
    task = enqueue_opening_structure(
        session,
        request.app.state.settings,
        run,
        force=force,
    )
    if task is None:
        projection = build_workbench_projection(session, run_id)
        if projection.get("opening_structure_status") == "READY":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "OPENING_STRUCTURE_ALREADY_CURRENT",
                    "message": "当前前三章逐段账本已经基于最新正文生成。",
                },
            )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "OPENING_STRUCTURE_SOURCE_NOT_READY",
                "message": "前三章正文、主角或深层拆解尚未就绪。",
            },
        )
    return _analysis_run_read(session, run)


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
    "/api/analysis-runs/{run_id}/deep/recompute-impact",
    response_model=DeepRevisionImpactRead,
)
def deep_analysis_recompute_impact(
    run_id: str,
    session: Session = Depends(get_db),
) -> DeepRevisionImpactRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    issues = list(session.scalars(
        select(AnalysisIssue).where(
            AnalysisIssue.run_id == run_id,
            AnalysisIssue.status == "OPEN",
        ).order_by(AnalysisIssue.created_at),
    ))
    previous = session.scalar(
        select(DeepAnalysis)
        .where(DeepAnalysis.run_id == run_id)
        .order_by(DeepAnalysis.revision_no.desc())
    )
    previous_payload = json.loads(previous.payload_json) if previous is not None else None
    requests = [
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
    return DeepRevisionImpactRead.model_validate(
        build_deep_revision_impact(requests, previous_payload)
    )


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

    collections = (
        "fact_versions",
        "state_changes",
        "actor_knowledge",
        "knowledge_transfers",
        "world_rules",
        "foreshadowing",
        "conflicts",
        "scene_analysis",
        "claims",
    )

    def key_for(collection: str, item: dict) -> str:
        if collection == "fact_versions":
            return f"{item.get('subject')}:{item.get('predicate')}:{item.get('valid_from_chapter')}"
        if collection == "state_changes":
            return f"{item.get('subject')}:{item.get('aspect')}:{item.get('chapter_ordinal')}"
        if collection == "actor_knowledge":
            return f"{item.get('actor')}:{item.get('proposition')}:{item.get('chapter_ordinal')}"
        if collection == "knowledge_transfers":
            return f"{item.get('source_actor')}:{item.get('target_actor')}:{item.get('proposition')}:{item.get('chapter_ordinal')}"
        if collection == "scene_analysis":
            return str(item.get("chapter_ordinal"))
        if collection == "claims":
            return f"{item.get('claim_kind')}:{item.get('scope')}:{item.get('claim_text')}"
        return str(item.get("title"))

    def label_for(collection: str, item: dict, fallback: str) -> str:
        if collection == "fact_versions":
            return f"{item.get('subject', '未知对象')}：{item.get('predicate', '事实')}"
        if collection == "state_changes":
            return f"{item.get('subject', '未知对象')}：{item.get('aspect', '状态变化')}"
        if collection == "actor_knowledge":
            return f"{item.get('actor', '未知人物')}：{item.get('proposition', '认知变化')}"
        if collection == "knowledge_transfers":
            return f"{item.get('source_actor', '未知来源')} → {item.get('target_actor', '未知人物')}：{item.get('proposition', '信息传播')}"
        if collection == "scene_analysis":
            return f"第 {item.get('chapter_ordinal', '?')} 章：{item.get('summary', '场景与节奏')}"
        if collection == "claims":
            return str(item.get("claim_text") or fallback)
        return str(item.get("title") or fallback)

    before_payload = json.loads(before.payload_json)
    after_payload = json.loads(after.payload_json)
    added: dict[str, list[str]] = {}
    removed: dict[str, list[str]] = {}
    changed: dict[str, list[str]] = {}
    changed_counts: dict[str, int] = {}
    for collection in collections:
        old = {key_for(collection, item): item for item in before_payload.get(collection, [])}
        new = {key_for(collection, item): item for item in after_payload.get(collection, [])}
        added[collection] = [label_for(collection, new[key], key) for key in sorted(new.keys() - old.keys())]
        removed[collection] = [label_for(collection, old[key], key) for key in sorted(old.keys() - new.keys())]
        changed_keys = [key for key in sorted(new.keys() & old.keys()) if new[key] != old[key]]
        changed[collection] = [label_for(collection, new[key], key) for key in changed_keys]
        changed_counts[collection] = len(changed_keys)
    return DeepAnalysisDiffRead(
        from_revision=before.revision_no,
        to_revision=after.revision_no,
        added=added,
        removed=removed,
        changed=changed,
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


@router.post(
    "/api/analysis-runs/{run_id}/provider-switch",
    response_model=AnalysisRunRead,
)
def analysis_provider_switch(
    run_id: str,
    payload: ProviderConfirmationWrite,
    session: Session = Depends(get_db),
) -> AnalysisRunRead:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    waiting_tasks = list(session.scalars(
        select(Task)
        .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
        .where(
            AnalysisRunTask.run_id == run_id,
            Task.status == TaskStatus.WAITING_CONFIRMATION.value,
        )
        .order_by(AnalysisRunTask.batch_index)
    ))
    if not waiting_tasks:
        raise HTTPException(status_code=409, detail={
            "code": "PROVIDER_CONFIRMATION_NOT_PENDING",
            "message": "当前没有等待确认的模型服务切换。",
        })
    try:
        for task in waiting_tasks:
            if resolve_provider_confirmation(
                session,
                task_id=task.id,
                decision=payload.decision,
            ) is None:
                session.rollback()
                raise HTTPException(status_code=409, detail={
                    "code": "PROVIDER_CONFIRMATION_STALE",
                    "message": "模型服务切换状态已经变化，请刷新页面后再选择。",
                })
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    refreshed = session.get(AnalysisRun, run_id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="ANALYSIS_RUN_NOT_FOUND")
    return _analysis_run_read(session, refreshed)
