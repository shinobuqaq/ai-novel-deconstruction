from __future__ import annotations

import asyncio
import hashlib
import json

from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import AnalysisRun, Task
from ..providers.base import ProviderError, ProviderResponse
from ..providers.registry import ProviderRegistry
from ..repositories import (
    ClaimedTask,
    acknowledge_task_cancellation,
    complete_task_attempt,
    fail_task_attempt,
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
    parse_narrative_synthesis,
    parse_provider_output,
    persist_analysis_output,
    persist_deep_analysis,
    persist_hierarchical_digest,
    persist_narrative_synthesis,
    provider_payload_for_deep_analysis,
    provider_payload_for_hierarchical_digest,
    provider_payload_for_narrative_synthesis,
    provider_payload_for_claim,
    refresh_analysis_run,
    StructuredOutputValidationError,
)


ANALYSIS_TASK_KINDS = {
    ANALYSIS_TASK_KIND,
    HIERARCHICAL_DIGEST_TASK_KIND,
    NARRATIVE_SYNTHESIS_TASK_KIND,
    DEEP_ANALYSIS_TASK_KIND,
}

_OUTPUT_FIELD_LABELS = {
    "entities": "人物与实体",
    "events": "事件",
    "story_overview": "故事总览",
    "character_roles": "人物档案",
    "character_relations": "人物关系",
    "narrative_phases": "剧情阶段",
    "event_relations": "事件关系",
    "summary": "范围摘要",
    "situation": "阶段局面",
    "key_actions": "关键行动",
    "character_progressions": "人物变化",
    "fact_versions": "事实",
    "state_changes": "状态变化",
    "actor_knowledge": "人物认知",
    "world_rules": "世界规则",
    "foreshadowing": "伏笔",
    "conflicts": "冲突",
    "scene_analysis": "场景与节奏",
    "claims": "分析结论",
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
    else:
        provider_payload = payload
    try:
        response = await provider.complete(task_kind=claim.kind, payload=provider_payload)
    except ProviderError:
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
        )

    persisted_analysis = None
    persisted_digest = None
    persisted_narrative = None
    persisted_deep = None
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
                ) from exc
    elif claim.kind == NARRATIVE_SYNTHESIS_TASK_KIND:
        try:
            narrative_output = parse_narrative_synthesis(response.parsed)
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
            ) from exc
        with session_factory() as session:
            if not task_claim_is_current(session, claim=claim):
                acknowledge_task_cancellation(session, claim=claim)
                return False
            task = session.get(Task, claim.id)
            if task is None:
                raise ValueError("TASK_NOT_FOUND")
            try:
                persisted_narrative = persist_narrative_synthesis(
                    session,
                    task=task,
                    attempt_id=claim.current_attempt_id,
                    task_payload=payload,
                    output=narrative_output,
                )
            except ValueError as exc:
                raise ProviderError(
                    code="PROVIDER_INVALID_OUTPUT",
                    message="在线 AI 返回的故事结构引用了不存在的人物、事件或原文证据。",
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
            else "analysis.narrative_synthesis.result"
            if claim.kind == NARRATIVE_SYNTHESIS_TASK_KIND
            else "analysis.deep_insights.result"
            if claim.kind == DEEP_ANALYSIS_TASK_KIND
            else "fake.echo.result"
        )
        usage_payload: dict[str, object] = {
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }
        if isinstance(response.parameters.get("cost"), dict):
            usage_payload["cost"] = response.parameters["cost"]
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
                    if payload_requests:
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
    try:
        failure_provider_name = str(json.loads(claim.payload_json).get("provider_name") or settings.provider_name)
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
            if isinstance(exc.diagnostics.get("cost"), dict):
                failure_usage["cost"] = exc.diagnostics["cost"]
            failure_provider_name = exc.provider_name or failure_provider_name
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
            )
            if not failed:
                acknowledge_task_cancellation(session, claim=claim)
            return failed
