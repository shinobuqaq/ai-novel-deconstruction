from __future__ import annotations

import asyncio
import json

from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import AnalysisRun, Task
from ..providers.base import ProviderError
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
    parse_provider_output,
    persist_analysis_output,
    provider_payload_for_claim,
    refresh_analysis_run,
)


async def execute_task(
    session_factory: sessionmaker[Session],
    settings: Settings,
    claim: ClaimedTask,
    provider_registry: ProviderRegistry,
) -> bool:
    if claim.kind not in {"fake.echo", ANALYSIS_TASK_KIND}:
        raise ValueError(f"UNSUPPORTED_TASK_KIND:{claim.kind}")

    payload = json.loads(claim.payload_json)
    provider_name = (
        str(payload.get("provider_name") or "openai")
        if claim.kind == ANALYSIS_TASK_KIND
        else settings.provider_name
    )
    provider = provider_registry.resolve(provider_name)
    if claim.kind == ANALYSIS_TASK_KIND:
        with session_factory() as session:
            provider_payload = provider_payload_for_claim(session, settings, payload)
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
        )

    persisted_analysis = None
    if claim.kind == ANALYSIS_TASK_KIND:
        try:
            analysis_output = parse_provider_output(response.parsed)
        except ValueError as exc:
            raise ProviderError(
                code="PROVIDER_INVALID_OUTPUT",
                message="在线 AI 返回的人物和事件结构不完整。",
                retryable=True,
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

    with session_factory() as session:
        if not task_claim_is_current(session, claim=claim):
            acknowledge_task_cancellation(session, claim=claim)
            return False
        artifact_kind = (
            "analysis.entities_events.result"
            if claim.kind == ANALYSIS_TASK_KIND
            else "fake.echo.result"
        )
        artifact_payload = {
            "task_id": claim.id,
            "response": response.parsed,
            "model": {
                "provider_id": response.provider_id or provider.name,
                "model": response.model,
                "parameters": response.parameters,
            },
            "usage": {
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
            },
        }
        if persisted_analysis is not None:
            artifact_payload["accepted"] = {
                "entity_ids": list(persisted_analysis.entity_ids),
                "event_ids": list(persisted_analysis.event_ids),
                "rejected_entities": persisted_analysis.rejected_entities,
                "rejected_events": persisted_analysis.rejected_events,
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
            usage_json=json.dumps(
                {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                },
                sort_keys=True,
            ),
        )
        if not accepted:
            acknowledge_task_cancellation(session, claim=claim)
    if accepted and claim.kind == ANALYSIS_TASK_KIND:
        with session_factory() as session:
            run = session.get(AnalysisRun, payload.get("run_id"))
            if run is not None:
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
            )
            if not failed:
                acknowledge_task_cancellation(session, claim=claim)
            return failed
