from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import AnalysisRun, AnalysisRunTask, Task, TaskAttempt, TaskAttemptStatus, TaskStatus
from ..providers.base import ProviderError, ProviderResponse
from ..providers.registry import ProviderRegistry
from ..repositories import (
    ClaimedTask,
    acknowledge_task_cancellation,
    complete_task_attempt,
    fail_task_attempt,
    pause_task_for_provider_confirmation,
    task_claim_is_current,
)
from .artifacts import write_json_artifact
from .analysis import (
    ANALYSIS_TASK_KIND,
    DEEP_ANALYSIS_TASK_KIND,
    HIERARCHICAL_DIGEST_TASK_KIND,
    NARRATIVE_SYNTHESIS_TASK_KIND,
    enqueue_deep_analysis,
    enqueue_narrative_synthesis,
    parse_deep_analysis,
    parse_hierarchical_digest,
    parse_narrative_component,
    parse_narrative_synthesis,
    parse_provider_output,
    persist_analysis_output,
    persist_deep_analysis,
    persist_hierarchical_digest,
    persist_narrative_component,
    persist_narrative_synthesis,
    provider_payload_for_deep_analysis,
    provider_payload_for_hierarchical_digest,
    provider_payload_for_narrative_synthesis,
    provider_payload_for_claim,
    refresh_analysis_run,
    StructuredOutputValidationError,
)
from .learning_report import (
    LEARNING_REPORT_TASK_KIND,
    LearningReportValidationError,
    parse_learning_report,
    persist_learning_report,
    provider_payload_for_learning_report,
)
from .character_design import (
    CHARACTER_DESIGN_TASK_KIND,
    CharacterDesignValidationError,
    parse_character_design_evidence,
    persist_character_design_evidence,
    provider_payload_for_character_design,
)
from .chapter_end_hooks import (
    CHAPTER_END_HOOKS_TASK_KIND,
    ChapterEndHooksValidationError,
    parse_chapter_end_hooks,
    persist_chapter_end_hooks,
    provider_payload_for_chapter_end_hooks,
)


ANALYSIS_TASK_KINDS = {
    ANALYSIS_TASK_KIND,
    HIERARCHICAL_DIGEST_TASK_KIND,
    NARRATIVE_SYNTHESIS_TASK_KIND,
    DEEP_ANALYSIS_TASK_KIND,
    CHARACTER_DESIGN_TASK_KIND,
    CHAPTER_END_HOOKS_TASK_KIND,
    LEARNING_REPORT_TASK_KIND,
}

_IMMEDIATE_PROVIDER_SWITCH_CODES = {
    "PROVIDER_AUTH_FAILED",
    "PROVIDER_BAD_REQUEST",
    "PROVIDER_NOT_CONFIGURED",
}

_OUTPUT_FIELD_LABELS = {
    "entities": "人物与实体",
    "events": "事件",
    "story_overview": "故事总览",
    "character_roles": "人物档案",
    "character_relations": "人物关系",
    "change_history": "关系变化时间线",
    "chapter_ordinal": "小说章节",
    "before": "变化前",
    "after": "变化后",
    "trigger_event_id": "触发事件",
    "narrative_phases": "剧情阶段",
    "event_relations": "事件关系",
    "discovery_routes": "事件发现依据",
    "summary": "范围摘要",
    "situation": "阶段局面",
    "key_actions": "关键行动",
    "character_progressions": "人物变化",
    "fact_versions": "事实",
    "state_changes": "状态变化",
    "actor_knowledge": "人物认知",
    "knowledge_transfers": "认知传播",
    "world_rules": "世界规则",
    "foreshadowing": "伏笔",
    "conflicts": "冲突",
    "scene_analysis": "场景与节奏",
    "claims": "分析结论",
    "answers": "学习问题答案",
    "question_id": "北极星问题编号",
    "metrics": "可核查计数",
    "limitations": "限制与缺口",
    "reusable_lessons": "可参考方法",
    "do_not_copy": "不可照搬内容",
    "author_decisions": "作者决策推断",
    "method_candidates": "可参考方法候选",
    "name": "名称",
    "title": "标题",
    "role": "角色定位",
    "role_reason": "定位依据",
    "evidence_ids": "原文依据",
    "event_ids": "相关事件",
    "confidence": "置信度",
}


def _validation_path(parts: list[object]) -> str:
    path = ""
    for part in parts:
        if isinstance(part, int):
            path += f"第 {part + 1} 项"
            continue
        label = _OUTPUT_FIELD_LABELS.get(str(part), str(part))
        path += (" / " if path else "") + label
    return path or "返回结果"


def _validation_reason(error_type: str) -> str:
    if error_type == "missing":
        return "缺少必填内容"
    if error_type == "extra_forbidden":
        return "包含系统不接受的额外字段"
    if error_type == "literal_error":
        return "值不在允许范围内"
    if error_type.startswith("string_too_"):
        return "文字长度不符合要求"
    if error_type.startswith("too_") or error_type.endswith("_too_long"):
        return "项目数量超过允许范围"
    if error_type.startswith("list_type"):
        return "应当返回列表"
    if error_type.startswith("dict_type") or error_type == "model_type":
        return "应当返回结构化对象"
    if error_type.startswith("int_") or error_type.startswith("greater_than") or error_type.startswith("less_than"):
        return "数字格式或范围不符合要求"
    return "内容格式不符合要求"


def _validation_message(stage_label: str, errors: list[dict]) -> str:
    examples = [
        f"{_validation_path(item.get('path', []))}：{_validation_reason(str(item.get('type') or ''))}"
        for item in errors[:3]
    ]
    detail = "；".join(examples)
    suffix = f"，共发现 {len(errors)} 处结构问题" if len(errors) > 3 else ""
    return f"在线 AI 返回的{stage_label}不完整。{detail}{suffix}。系统会自动重试。"


def _deep_consistency_message(reason_code: str) -> str:
    messages = {
        "DEEP_ANALYSIS_FUTURE_EVIDENCE_LEAK": "在线 AI 把后文章节才出现的依据提前写进了前文章节状态。系统已拒绝保存，并会自动重试。",
        "DEEP_ANALYSIS_STATE_REPLAY_CONFLICT": "在线 AI 对同一对象在同一章给出了互相矛盾的状态。系统已拒绝保存，并会自动重试。",
        "DEEP_ANALYSIS_KNOWLEDGE_REPLAY_CONFLICT": "在线 AI 对同一人物在同一章给出了互相矛盾的认知状态。系统已拒绝保存，并会自动重试。",
        "DEEP_ANALYSIS_KNOWLEDGE_TRANSFER_SELF_REFERENCE": "在线 AI 把告知、传闻或撤回错误地写成了人物传给自己。系统已拒绝保存，并会自动重试。",
        "DEEP_ANALYSIS_KNOWLEDGE_TRANSFER_RESULT_MISSING": "在线 AI 描述了信息传播过程，但没有给出接收者在同一章形成的认知结果。系统已拒绝保存，并会自动重试。",
    }
    return messages.get(
        reason_code,
        "在线 AI 返回的深层拆解引用了不存在的章节、人物、事件或原文依据。系统会自动重试。",
    )


def _narrative_consistency_message(reason_code: str) -> str:
    if reason_code.startswith("NARRATIVE_INTERNAL_ID_LEAK"):
        return "在线 AI 把系统内部证据编号写进了给用户阅读的正文。系统已拒绝保存这份结果，并会自动重试。"
    return "在线 AI 返回的故事结构引用了不存在的人物、事件或原文证据。系统会自动重试。"


def _attempt_diagnostics(
    provider_payload: dict,
    response: ProviderResponse,
    *,
    phase: str,
    validation_errors: list[dict] | None = None,
    reason_code: str | None = None,
) -> dict:
    model_input = str(provider_payload.get("input") or "")
    diagnostics = {
        "phase": phase,
        "prompt_id": provider_payload.get("prompt_id"),
        "prompt_version": provider_payload.get("prompt_version"),
        "model_profile_id": provider_payload.get("model_profile_id"),
        "model": response.model,
        "input_chars": len(model_input),
        "output_chars": len(response.raw_text),
    }
    transport_mode = response.parameters.get("transport_mode")
    if isinstance(transport_mode, str) and transport_mode:
        diagnostics["transport_mode"] = transport_mode
    request_input_path = provider_payload.get("request_input_path")
    if isinstance(request_input_path, str) and request_input_path:
        diagnostics["request_input_path"] = request_input_path
    repairs = response.parameters.get("json_repairs")
    if isinstance(repairs, list) and repairs:
        diagnostics["json_repairs"] = [str(item) for item in repairs[:10]]
    context_manifest = provider_payload.get("context_manifest")
    if isinstance(context_manifest, dict):
        # Keep the diagnostic compact enough for the task table while still
        # preserving the exact selected/omitted counts and reasons.
        diagnostics["context"] = {
            key: context_manifest.get(key)
            for key in (
                "budget_chars",
                "selected_count",
                "selected_chars",
                "omitted_count",
                "omitted_chars",
                "selected_by_kind",
                "omitted_reasons",
            )
        }
    if validation_errors:
        diagnostics["validation_errors"] = validation_errors[:20]
        diagnostics["validation_error_count"] = len(validation_errors)
    if reason_code:
        diagnostics["reason_code"] = reason_code[:200]
    return diagnostics


def _persist_failed_model_output(
    settings: Settings,
    claim: ClaimedTask,
    raw_text: str,
) -> str:
    diagnostics_root = settings.workspace_dir / "diagnostics" / "model-output-failures"
    task_dir = diagnostics_root / claim.id
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / f"{claim.current_attempt_id}.txt"
    temporary_path = Path(f"{path}.tmp")
    temporary_path.write_text(raw_text, encoding="utf-8")
    temporary_path.replace(path)
    return path.relative_to(settings.workspace_dir).as_posix()


def _persist_model_input(
    settings: Settings,
    claim: ClaimedTask,
    input_text: str,
) -> str:
    diagnostics_root = settings.workspace_dir / "diagnostics" / "model-call-inputs"
    task_dir = diagnostics_root / claim.id
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / f"{claim.current_attempt_id}.txt"
    temporary_path = Path(f"{path}.tmp")
    temporary_path.write_text(input_text, encoding="utf-8")
    temporary_path.replace(path)
    return path.relative_to(settings.workspace_dir).as_posix()


def _provider_routes(payload: dict) -> list[dict]:
    routes = payload.get("provider_routes")
    if not isinstance(routes, list):
        return []
    return [item for item in routes if isinstance(item, dict)]


def _active_provider_route(payload: dict) -> tuple[int, dict | None]:
    routes = _provider_routes(payload)
    try:
        route_index = int(payload.get("provider_route_index") or 0)
    except (TypeError, ValueError):
        route_index = 0
    if route_index < 0 or route_index >= len(routes):
        return route_index, None
    return route_index, routes[route_index]


def _run_provider_failure_streak(
    session_factory: sessionmaker[Session],
    *,
    run_id: str | None,
    provider_name: str,
    current_attempt_id: str,
    reset_at: str | None = None,
) -> int:
    if not run_id:
        return 1
    reset_time = None
    if reset_at:
        try:
            reset_time = datetime.fromisoformat(reset_at)
        except (TypeError, ValueError):
            reset_time = None
    with session_factory() as session:
        stmt = (
            select(TaskAttempt)
            .join(AnalysisRunTask, AnalysisRunTask.task_id == TaskAttempt.task_id)
            .where(AnalysisRunTask.run_id == run_id)
        )
        if reset_time is not None:
            stmt = stmt.where(TaskAttempt.started_at >= reset_time)
        attempts = list(session.scalars(
            stmt.order_by(
                TaskAttempt.finished_at.desc(),
                TaskAttempt.started_at.desc(),
                TaskAttempt.attempt_no.desc(),
            )
        ))
    streak = 1
    failed_statuses = {
        TaskAttemptStatus.RETRYABLE_FAILED.value,
        TaskAttemptStatus.PERMANENT_FAILED.value,
        TaskAttemptStatus.EXPIRED.value,
    }
    for attempt in attempts:
        if attempt.id == current_attempt_id or attempt.status == TaskAttemptStatus.RUNNING.value:
            continue
        if attempt.provider_name != provider_name:
            break
        if attempt.status in failed_statuses:
            streak += 1
            continue
        break
    return streak


def _pause_related_run_tasks(
    session_factory: sessionmaker[Session],
    *,
    run_id: str | None,
    current_task_id: str,
    pending_switch: dict,
    failure_streak: int,
) -> None:
    if not run_id:
        return
    with session_factory() as session:
        related_tasks = list(session.scalars(
            select(Task)
            .join(AnalysisRunTask, AnalysisRunTask.task_id == Task.id)
            .where(
                AnalysisRunTask.run_id == run_id,
                Task.id != current_task_id,
                Task.status.in_({TaskStatus.PENDING.value, TaskStatus.RETRY_WAIT.value}),
            )
        ))
        for task in related_tasks:
            try:
                related_payload = json.loads(task.payload_json)
            except json.JSONDecodeError:
                continue
            route_index, route = _active_provider_route(related_payload)
            if route is None or route.get("service_id") != pending_switch.get("current_service_id"):
                continue
            routes = _provider_routes(related_payload)
            if route_index + 1 >= len(routes):
                continue
            related_payload["provider_failure_streak"] = failure_streak
            related_payload["pending_provider_switch"] = dict(pending_switch)
            task.payload_json = json.dumps(related_payload, ensure_ascii=False, sort_keys=True)
            task.status = TaskStatus.WAITING_CONFIRMATION.value
            task.next_attempt_at = None
        session.commit()


async def execute_task(
    session_factory: sessionmaker[Session],
    settings: Settings,
    claim: ClaimedTask,
    provider_registry: ProviderRegistry,
) -> bool:
    if claim.kind not in {"fake.echo", *ANALYSIS_TASK_KINDS}:
        raise ValueError(f"UNSUPPORTED_TASK_KIND:{claim.kind}")

    payload = json.loads(claim.payload_json)
    provider_name = (
        str(payload.get("provider_name") or "openai")
        if claim.kind in ANALYSIS_TASK_KINDS
        else settings.provider_name
    )
    provider = provider_registry.resolve(provider_name)
    if claim.kind == ANALYSIS_TASK_KIND:
        with session_factory() as session:
            provider_payload = provider_payload_for_claim(session, settings, payload)
    elif claim.kind == NARRATIVE_SYNTHESIS_TASK_KIND:
        with session_factory() as session:
            provider_payload = provider_payload_for_narrative_synthesis(
                session, settings, payload
            )
    elif claim.kind == HIERARCHICAL_DIGEST_TASK_KIND:
        with session_factory() as session:
            provider_payload = provider_payload_for_hierarchical_digest(
                session, settings, payload
            )
    elif claim.kind == DEEP_ANALYSIS_TASK_KIND:
        with session_factory() as session:
            provider_payload = provider_payload_for_deep_analysis(
                session, settings, payload
            )
    elif claim.kind == CHARACTER_DESIGN_TASK_KIND:
        try:
            with session_factory() as session:
                provider_payload = provider_payload_for_character_design(
                    session, settings, payload
                )
        except ValueError as exc:
            reason_code = str(exc)
            message = (
                "主角事件和原文超过当前模型可安全读取的范围，需要先启用分段专项分析。"
                if reason_code.startswith("CHARACTER_DESIGN_CONTEXT_TOO_LARGE")
                else "主角、故事结构或深层拆解已经更新，请基于最新结果重新生成证据表。"
                if reason_code == "CHARACTER_DESIGN_SOURCE_OUTDATED"
                else "主角人物和事件原料尚未就绪，暂时不能生成 2.2 证据表。"
            )
            raise ProviderError(
                code=reason_code.split(":", 1)[0],
                message=message,
                retryable=False,
            ) from exc
    elif claim.kind == CHAPTER_END_HOOKS_TASK_KIND:
        try:
            with session_factory() as session:
                provider_payload = provider_payload_for_chapter_end_hooks(
                    session, settings, payload
                )
        except ValueError as exc:
            reason_code = str(exc)
            message = (
                "逐章章末原文和回应候选超过当前模型可安全读取的范围，需要调整专项取样。"
                if reason_code.startswith("CHAPTER_END_HOOKS_CONTEXT_TOO_LARGE")
                else "正文、故事结构或深层拆解已经更新，请基于最新结果重新生成章末钩账本。"
                if reason_code == "CHAPTER_END_HOOKS_SOURCE_OUTDATED"
                else "真实章节、章末原文或深层拆解尚未就绪，暂时不能生成 4.9 账本。"
            )
            raise ProviderError(
                code=reason_code.split(":", 1)[0],
                message=message,
                retryable=False,
            ) from exc
    elif claim.kind == LEARNING_REPORT_TASK_KIND:
        with session_factory() as session:
            provider_payload = provider_payload_for_learning_report(
                session, settings, payload
            )
    else:
        provider_payload = payload
    route_index, route = _active_provider_route(payload)
    if route is not None:
        provider_payload["provider_route_index"] = route_index
        provider_payload["provider_service_id"] = str(route.get("service_id") or "")
        provider_payload["provider_model"] = str(route.get("model") or "")
    if claim.kind in ANALYSIS_TASK_KINDS:
        try:
            provider_payload["request_input_path"] = _persist_model_input(
                settings,
                claim,
                str(provider_payload.get("input") or ""),
            )
        except OSError:
            # The online call can still proceed. Diagnostics will make the
            # missing transcript explicit instead of breaking the analysis.
            pass
    try:
        response = await provider.complete(task_kind=claim.kind, payload=provider_payload)
    except ProviderError as exc:
        if provider_payload.get("request_input_path"):
            exc.diagnostics = dict(exc.diagnostics)
            exc.diagnostics["request_input_path"] = provider_payload["request_input_path"]
        raise
    except Exception as exc:
        raise ProviderError(
            code="PROVIDER_UNEXPECTED_ERROR",
            message=str(exc) or "Provider raised an unexpected error.",
            retryable=False,
        ) from exc
    if not isinstance(response.parsed, dict):
        raise ProviderError(
            code="PROVIDER_INVALID_OUTPUT",
            message="Provider response must contain a JSON object.",
            retryable=True,
            diagnostics=_attempt_diagnostics(
                provider_payload,
                response,
                phase="json_object_validation",
            ),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            provider_name=response.provider_id or provider.name,
            model=response.model,
            raw_text=response.raw_text,
        )

    persisted_analysis = None
    persisted_digest = None
    persisted_narrative = None
    persisted_deep = None
    persisted_character_design = None
    persisted_chapter_end_hooks = None
    persisted_learning_report = None
    if claim.kind == ANALYSIS_TASK_KIND:
        try:
            analysis_output = parse_provider_output(response.parsed)
        except StructuredOutputValidationError as exc:
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message=_validation_message("人物和事件结构", exc.errors),
                retryable=True,
                diagnostics=_attempt_diagnostics(
                    provider_payload,
                    response,
                    phase="schema_validation",
                    validation_errors=exc.errors,
                ),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                provider_name=response.provider_id or provider.name,
                model=response.model,
                raw_text=response.raw_text,
            ) from exc
        with session_factory() as session:
            if not task_claim_is_current(session, claim=claim):
                acknowledge_task_cancellation(session, claim=claim)
                return False
            task = session.get(Task, claim.id)
            if task is None:
                raise ValueError("TASK_NOT_FOUND")
            persisted_analysis = persist_analysis_output(
                session,
                settings,
                task=task,
                attempt_id=claim.current_attempt_id,
                task_payload=payload,
                output=analysis_output,
            )
    elif claim.kind == HIERARCHICAL_DIGEST_TASK_KIND:
        try:
            digest_output = parse_hierarchical_digest(response.parsed)
        except StructuredOutputValidationError as exc:
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message=_validation_message("长篇分层摘要", exc.errors),
                retryable=True,
                diagnostics=_attempt_diagnostics(
                    provider_payload,
                    response,
                    phase="schema_validation",
                    validation_errors=exc.errors,
                ),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                provider_name=response.provider_id or provider.name,
                model=response.model,
            ) from exc
        with session_factory() as session:
            if not task_claim_is_current(session, claim=claim):
                acknowledge_task_cancellation(session, claim=claim)
                return False
            task = session.get(Task, claim.id)
            if task is None:
                raise ValueError("TASK_NOT_FOUND")
            try:
                persisted_digest = persist_hierarchical_digest(
                    session,
                    task=task,
                    attempt_id=claim.current_attempt_id,
                    task_payload=payload,
                    output=digest_output,
                )
            except ValueError as exc:
                raise ProviderError(
                    code="PROVIDER_INVALID_OUTPUT",
                    message="在线 AI 返回的分层摘要引用了范围外的事件或原文证据。",
                    retryable=True,
                    diagnostics=_attempt_diagnostics(
                        provider_payload,
                        response,
                        phase="reference_validation",
                        reason_code=str(exc),
                    ),
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    provider_name=response.provider_id or provider.name,
                    model=response.model,
                    raw_text=response.raw_text,
                ) from exc
    elif claim.kind == NARRATIVE_SYNTHESIS_TASK_KIND:
        component = str(payload.get("narrative_component") or "")
        try:
            narrative_output = (
                parse_narrative_component(component, response.parsed)
                if component
                else parse_narrative_synthesis(response.parsed)
            )
        except StructuredOutputValidationError as exc:
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message=_validation_message("故事总览和剧情结构", exc.errors),
                retryable=True,
                diagnostics=_attempt_diagnostics(
                    provider_payload,
                    response,
                    phase="schema_validation",
                    validation_errors=exc.errors,
                ),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                provider_name=response.provider_id or provider.name,
                model=response.model,
                raw_text=response.raw_text,
            ) from exc
        with session_factory() as session:
            if not task_claim_is_current(session, claim=claim):
                acknowledge_task_cancellation(session, claim=claim)
                return False
            task = session.get(Task, claim.id)
            if task is None:
                raise ValueError("TASK_NOT_FOUND")
            try:
                if component:
                    persisted_narrative = persist_narrative_component(
                        session,
                        settings,
                        task=task,
                        attempt_id=claim.current_attempt_id,
                        task_payload=payload,
                        output=narrative_output,
                    )
                else:
                    persisted_narrative = persist_narrative_synthesis(
                        session,
                        task=task,
                        attempt_id=claim.current_attempt_id,
                        task_payload=payload,
                        output=narrative_output,
                    )
            except ValueError as exc:
                reason_code = str(exc)
                raise ProviderError(
                    code="PROVIDER_INVALID_OUTPUT",
                    message=_narrative_consistency_message(reason_code),
                    retryable=True,
                    diagnostics=_attempt_diagnostics(
                        provider_payload,
                        response,
                        phase="reference_validation",
                        reason_code=reason_code,
                    ),
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    provider_name=response.provider_id or provider.name,
                    model=response.model,
                    raw_text=response.raw_text,
                ) from exc
    elif claim.kind == DEEP_ANALYSIS_TASK_KIND:
        try:
            deep_output = parse_deep_analysis(response.parsed)
        except StructuredOutputValidationError as exc:
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message=_validation_message("事实状态和核心拆解结构", exc.errors),
                retryable=True,
                diagnostics=_attempt_diagnostics(
                    provider_payload,
                    response,
                    phase="schema_validation",
                    validation_errors=exc.errors,
                ),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                provider_name=response.provider_id or provider.name,
                model=response.model,
                raw_text=response.raw_text,
            ) from exc
        with session_factory() as session:
            if not task_claim_is_current(session, claim=claim):
                acknowledge_task_cancellation(session, claim=claim)
                return False
            task = session.get(Task, claim.id)
            if task is None:
                raise ValueError("TASK_NOT_FOUND")
            try:
                persisted_deep = persist_deep_analysis(
                    session,
                    settings,
                    task=task,
                    attempt_id=claim.current_attempt_id,
                    task_payload=payload,
                    output=deep_output,
                )
            except ValueError as exc:
                reason_code = str(exc)
                raise ProviderError(
                    code="PROVIDER_INVALID_OUTPUT",
                    message=_deep_consistency_message(reason_code),
                    retryable=True,
                    diagnostics=_attempt_diagnostics(
                        provider_payload,
                        response,
                        phase="reference_validation",
                        reason_code=reason_code,
                    ),
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    provider_name=response.provider_id or provider.name,
                    model=response.model,
                    raw_text=response.raw_text,
                ) from exc
    elif claim.kind == CHARACTER_DESIGN_TASK_KIND:
        try:
            character_design_output = parse_character_design_evidence(response.parsed)
        except CharacterDesignValidationError as exc:
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message=_validation_message("主角双层欲望与最小完整集证据表", exc.errors),
                retryable=True,
                diagnostics=_attempt_diagnostics(
                    provider_payload,
                    response,
                    phase="schema_validation",
                    validation_errors=exc.errors,
                ),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                provider_name=response.provider_id or provider.name,
                model=response.model,
                raw_text=response.raw_text,
            ) from exc
        with session_factory() as session:
            if not task_claim_is_current(session, claim=claim):
                acknowledge_task_cancellation(session, claim=claim)
                return False
            task = session.get(Task, claim.id)
            if task is None:
                raise ValueError("TASK_NOT_FOUND")
            try:
                persisted_character_design = persist_character_design_evidence(
                    session,
                    task=task,
                    attempt_id=claim.current_attempt_id,
                    task_payload=payload,
                    output=character_design_output,
                )
            except ValueError as exc:
                reason_code = str(exc)
                raise ProviderError(
                    code="PROVIDER_INVALID_OUTPUT",
                    message=(
                        "主角证据表引用的事件、章节或原文对应不上，请重新生成。"
                        if reason_code != "CHARACTER_DESIGN_SOURCE_OUTDATED"
                        else "生成期间人物或拆解结果已经更新，请基于最新结果重新生成。"
                    ),
                    retryable=reason_code != "CHARACTER_DESIGN_SOURCE_OUTDATED",
                    diagnostics=_attempt_diagnostics(
                        provider_payload,
                        response,
                        phase="reference_validation",
                        reason_code=reason_code,
                    ),
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    provider_name=response.provider_id or provider.name,
                    model=response.model,
                    raw_text=response.raw_text,
                ) from exc
    elif claim.kind == CHAPTER_END_HOOKS_TASK_KIND:
        try:
            chapter_end_hooks_output = parse_chapter_end_hooks(response.parsed)
        except ChapterEndHooksValidationError as exc:
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message=_validation_message("逐章章末钩与回应账本", exc.errors),
                retryable=True,
                diagnostics=_attempt_diagnostics(
                    provider_payload,
                    response,
                    phase="schema_validation",
                    validation_errors=exc.errors,
                ),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                provider_name=response.provider_id or provider.name,
                model=response.model,
                raw_text=response.raw_text,
            ) from exc
        with session_factory() as session:
            if not task_claim_is_current(session, claim=claim):
                acknowledge_task_cancellation(session, claim=claim)
                return False
            task = session.get(Task, claim.id)
            if task is None:
                raise ValueError("TASK_NOT_FOUND")
            try:
                persisted_chapter_end_hooks = persist_chapter_end_hooks(
                    session,
                    task=task,
                    attempt_id=claim.current_attempt_id,
                    task_payload=payload,
                    output=chapter_end_hooks_output,
                    valid_response_evidence_ids={
                        str(item.get("id"))
                        for item in json.loads(
                            str(provider_payload.get("input") or "{}")
                        ).get("response_evidence_catalog", [])
                        if item.get("id")
                    },
                )
            except ValueError as exc:
                reason_code = str(exc)
                raise ProviderError(
                    code="PROVIDER_INVALID_OUTPUT",
                    message=(
                        "章末钩账本漏章，或引用的章末/回应原文与实际章节对应不上，请重新生成。"
                        if reason_code != "CHAPTER_END_HOOKS_SOURCE_OUTDATED"
                        else "生成期间正文或拆解结果已经更新，请基于最新结果重新生成。"
                    ),
                    retryable=reason_code != "CHAPTER_END_HOOKS_SOURCE_OUTDATED",
                    diagnostics=_attempt_diagnostics(
                        provider_payload,
                        response,
                        phase="reference_validation",
                        reason_code=reason_code,
                    ),
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    provider_name=response.provider_id or provider.name,
                    model=response.model,
                    raw_text=response.raw_text,
                ) from exc
    elif claim.kind == LEARNING_REPORT_TASK_KIND:
        try:
            learning_output = parse_learning_report(
                response.parsed,
                expected_question_ids=[
                    str(question_id) for question_id in payload.get("question_ids", [])
                ],
            )
        except LearningReportValidationError as exc:
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message=_validation_message("创作学习报告", exc.errors),
                retryable=True,
                diagnostics=_attempt_diagnostics(
                    provider_payload,
                    response,
                    phase="schema_validation",
                    validation_errors=exc.errors,
                ),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                provider_name=response.provider_id or provider.name,
                model=response.model,
                raw_text=response.raw_text,
            ) from exc
        with session_factory() as session:
            if not task_claim_is_current(session, claim=claim):
                acknowledge_task_cancellation(session, claim=claim)
                return False
            task = session.get(Task, claim.id)
            if task is None:
                raise ValueError("TASK_NOT_FOUND")
            try:
                persisted_learning_report = persist_learning_report(
                    session,
                    task=task,
                    attempt_id=claim.current_attempt_id,
                    task_payload=payload,
                    output=learning_output,
                )
            except ValueError as exc:
                reason_code = str(exc)
                message = (
                    "创作学习报告引用了无效的原文依据，请重新生成。"
                    if reason_code == "LEARNING_REPORT_EVIDENCE_REFERENCE_INVALID"
                    else "创作学习报告缺少结论所需的原文依据或可核查计数，请重新生成。"
                    if reason_code in {
                        "LEARNING_REPORT_ANSWER_EVIDENCE_MISSING",
                        "LEARNING_REPORT_METRIC_MISSING",
                        "LEARNING_REPORT_PARTIAL_SCOPE_VIOLATION",
                    }
                    else "报告生成期间深层拆解已经更新，请基于最新结果重新生成。"
                )
                raise ProviderError(
                    code="PROVIDER_INVALID_OUTPUT",
                    message=message,
                    retryable=reason_code != "LEARNING_REPORT_SOURCE_OUTDATED",
                    diagnostics=_attempt_diagnostics(
                        provider_payload,
                        response,
                        phase="reference_validation",
                        reason_code=reason_code,
                    ),
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    provider_name=response.provider_id or provider.name,
                    model=response.model,
                    raw_text=response.raw_text,
                ) from exc

    with session_factory() as session:
        if not task_claim_is_current(session, claim=claim):
            acknowledge_task_cancellation(session, claim=claim)
            return False
        artifact_kind = (
            "analysis.entities_events.result"
            if claim.kind == ANALYSIS_TASK_KIND
            else "analysis.hierarchical_digest.result"
            if claim.kind == HIERARCHICAL_DIGEST_TASK_KIND
            else (
                f"analysis.narrative_synthesis.{payload.get('narrative_component')}.result"
                if payload.get("narrative_component")
                else "analysis.narrative_synthesis.result"
            )
            if claim.kind == NARRATIVE_SYNTHESIS_TASK_KIND
            else "analysis.deep_insights.result"
            if claim.kind == DEEP_ANALYSIS_TASK_KIND
            else "analysis.character_design_evidence.result"
            if claim.kind == CHARACTER_DESIGN_TASK_KIND
            else "analysis.chapter_end_hooks.result"
            if claim.kind == CHAPTER_END_HOOKS_TASK_KIND
            else "analysis.learning_report.result"
            if claim.kind == LEARNING_REPORT_TASK_KIND
            else "fake.echo.result"
        )
        usage_payload: dict[str, object] = {
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }
        artifact_payload = {
            "task_id": claim.id,
            "response": response.parsed,
            "model": {
                "provider_id": response.provider_id or provider.name,
                "model": response.model,
                "parameters": response.parameters,
            },
            "usage": usage_payload,
        }
        if claim.kind in ANALYSIS_TASK_KINDS:
            request_input = str(provider_payload.get("input") or "")
            output_schema = provider_payload.get("output_schema") or {}
            artifact_payload["request"] = {
                "prompt_id": provider_payload.get("prompt_id"),
                "prompt_version": provider_payload.get("prompt_version"),
                "instructions": provider_payload.get("instructions"),
                "source_version_id": provider_payload.get("source_version_id"),
                "source_char_start": provider_payload.get("source_char_start"),
                "source_char_end": provider_payload.get("source_char_end"),
                "input_chars": len(request_input),
                "input_sha256": hashlib.sha256(request_input.encode("utf-8")).hexdigest(),
                "output_schema_sha256": hashlib.sha256(
                    json.dumps(output_schema, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "model_profile_id": provider_payload.get("model_profile_id"),
                "context": provider_payload.get("context_manifest"),
                "input": request_input,
            }
        if persisted_analysis is not None:
            artifact_payload["accepted"] = {
                "entity_ids": list(persisted_analysis.entity_ids),
                "event_ids": list(persisted_analysis.event_ids),
                "rejected_entities": persisted_analysis.rejected_entities,
                "rejected_events": persisted_analysis.rejected_events,
            }
        if persisted_narrative is not None:
            artifact_payload["accepted"] = {
                "narrative_synthesis_id": persisted_narrative.synthesis_id,
            }
        if persisted_digest is not None:
            artifact_payload["accepted"] = {
                "hierarchical_digest_id": persisted_digest.digest_id,
            }
        if persisted_deep is not None:
            artifact_payload["accepted"] = {
                "deep_analysis_id": persisted_deep.analysis_id,
            }
        if persisted_character_design is not None:
            artifact_payload["accepted"] = {
                "learning_question_evidence_id": persisted_character_design.id,
                "question_id": persisted_character_design.question_id,
            }
        if persisted_chapter_end_hooks is not None:
            artifact_payload["accepted"] = {
                "learning_question_evidence_id": persisted_chapter_end_hooks.id,
                "question_id": persisted_chapter_end_hooks.question_id,
            }
        if persisted_learning_report is not None:
            artifact_payload["accepted"] = {
                "learning_report_id": persisted_learning_report.report_id,
            }
        artifact = write_json_artifact(
            session,
            settings,
            project_id=claim.project_id,
            kind=artifact_kind,
            payload=artifact_payload,
            created_by_task_id=claim.id,
            created_by_attempt_id=claim.current_attempt_id,
            lease_generation=claim.lease_generation,
            metadata={
                "provider": response.provider_id or provider.name,
                "model": response.model,
                "parameters": response.parameters,
            },
        )
        accepted = complete_task_attempt(
            session,
            task_id=claim.id,
            attempt_id=claim.current_attempt_id,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            result_artifact_id=artifact.id,
            provider_name=response.provider_id or provider.name,
            usage_json=json.dumps(usage_payload, sort_keys=True),
            diagnostics_json=json.dumps(
                _attempt_diagnostics(
                    provider_payload,
                    response,
                    phase="completed",
                ),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        if not accepted:
            acknowledge_task_cancellation(session, claim=claim)
    if accepted and claim.kind in ANALYSIS_TASK_KINDS:
        with session_factory() as session:
            run = session.get(AnalysisRun, payload.get("run_id"))
            if run is not None:
                if claim.kind == ANALYSIS_TASK_KIND:
                    enqueue_narrative_synthesis(session, settings, run)
                elif claim.kind == HIERARCHICAL_DIGEST_TASK_KIND:
                    enqueue_narrative_synthesis(session, settings, run)
                elif claim.kind == NARRATIVE_SYNTHESIS_TASK_KIND:
                    payload_requests = payload.get("revision_requests", [])
                    if (
                        persisted_narrative is not None
                        and payload_requests
                        and payload.get("enqueue_deep_after_narrative", True)
                    ):
                        enqueue_deep_analysis(
                            session,
                            settings,
                            run,
                            force=True,
                            revision_requests=payload_requests,
                        )
                refresh_analysis_run(session, run)
    return accepted


def execute_task_sync(
    session_factory: sessionmaker[Session],
    settings: Settings,
    claim: ClaimedTask,
    provider_registry: ProviderRegistry,
) -> bool:
    task_payload: dict = {}
    active_route: dict | None = None
    try:
        task_payload = json.loads(claim.payload_json)
        _, active_route = _active_provider_route(task_payload)
        failure_provider_name = str(
            (active_route or {}).get("service_id")
            or task_payload.get("provider_name")
            or settings.provider_name
        )
    except json.JSONDecodeError:
        failure_provider_name = settings.provider_name
    try:
        return asyncio.run(
            execute_task(
                session_factory,
                settings,
                claim,
                provider_registry,
            )
        )
    except Exception as exc:
        if isinstance(exc, ProviderError):
            error_code = exc.code
            retryable = exc.retryable
            retry_after_seconds = exc.retry_after_seconds
            failure_diagnostics = exc.diagnostics
            failure_usage = {
                "prompt_tokens": exc.prompt_tokens,
                "completion_tokens": exc.completion_tokens,
            }
            failure_provider_name = (
                exc.provider_name
                or str((active_route or {}).get("service_id") or "")
                or failure_provider_name
            )
            if exc.raw_text is not None:
                failure_diagnostics = dict(failure_diagnostics)
                try:
                    failure_diagnostics["raw_output_path"] = _persist_failed_model_output(
                        settings,
                        claim,
                        exc.raw_text,
                    )
                except OSError as diagnostic_error:
                    failure_diagnostics["raw_output_persist_error"] = type(
                        diagnostic_error
                    ).__name__
            route_index, current_route = _active_provider_route(task_payload)
            routes = _provider_routes(task_payload)
            threshold = max(1, int(task_payload.get("provider_failover_threshold") or 3))
            run_id = str(task_payload.get("run_id") or "") or None
            failure_streak = _run_provider_failure_streak(
                session_factory,
                run_id=run_id,
                provider_name=failure_provider_name,
                current_attempt_id=claim.current_attempt_id,
                reset_at=str(task_payload.get("provider_failure_reset_at") or "") or None,
            )
            task_payload["provider_failure_streak"] = failure_streak
            next_route = routes[route_index + 1] if route_index + 1 < len(routes) else None
            confirmation_required = bool(
                current_route
                and next_route
                and (
                    failure_streak >= threshold
                    or error_code in _IMMEDIATE_PROVIDER_SWITCH_CODES
                )
            )
            if confirmation_required:
                pending_switch = {
                    "current_service_id": current_route.get("service_id"),
                    "current_service_name": current_route.get("service_name"),
                    "current_model": current_route.get("model"),
                    "next_service_id": next_route.get("service_id"),
                    "next_service_name": next_route.get("service_name"),
                    "next_model": next_route.get("model"),
                    "failure_count": failure_streak,
                    "threshold": threshold,
                    "error_code": error_code,
                    "message": str(exc),
                }
                task_payload["pending_provider_switch"] = pending_switch
                failure_diagnostics = dict(failure_diagnostics)
                failure_diagnostics["provider_switch_confirmation_required"] = True
                with session_factory() as session:
                    paused = pause_task_for_provider_confirmation(
                        session,
                        task_id=claim.id,
                        attempt_id=claim.current_attempt_id,
                        lease_token=claim.lease_token,
                        lease_generation=claim.lease_generation,
                        error_code=error_code,
                        error_message=str(exc),
                        retryable=retryable,
                        provider_name=failure_provider_name,
                        payload_json=json.dumps(task_payload, ensure_ascii=False, sort_keys=True),
                        usage_json=json.dumps(failure_usage, sort_keys=True),
                        diagnostics_json=json.dumps(
                            failure_diagnostics,
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                    if not paused:
                        acknowledge_task_cancellation(session, claim=claim)
                if paused:
                    _pause_related_run_tasks(
                        session_factory,
                        run_id=run_id,
                        current_task_id=claim.id,
                        pending_switch=pending_switch,
                        failure_streak=failure_streak,
                    )
                return paused
            if routes and failure_streak >= threshold:
                retryable = False
        else:
            if isinstance(exc, ValueError) and str(exc).startswith(
                "UNSUPPORTED_TASK_KIND:"
            ):
                error_code = "UNSUPPORTED_TASK_KIND"
            elif isinstance(exc, json.JSONDecodeError):
                error_code = "TASK_PAYLOAD_INVALID"
            else:
                error_code = "TASK_EXECUTION_ERROR"
            retryable = False
            retry_after_seconds = None
            failure_diagnostics = {}
            failure_usage = {}
        with session_factory() as session:
            failed = fail_task_attempt(
                session,
                task_id=claim.id,
                attempt_id=claim.current_attempt_id,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                error_code=error_code,
                error_message=str(exc),
                retryable=retryable,
                retry_after_seconds=retry_after_seconds,
                provider_name=(
                    failure_provider_name
                    if error_code.startswith("PROVIDER_")
                    else None
                ),
                usage_json=json.dumps(failure_usage, sort_keys=True),
                diagnostics_json=json.dumps(
                    failure_diagnostics,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                payload_json=(
                    json.dumps(task_payload, ensure_ascii=False, sort_keys=True)
                    if task_payload
                    else None
                ),
            )
            if not failed:
                acknowledge_task_cancellation(session, claim=claim)
            return failed
