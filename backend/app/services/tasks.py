from __future__ import annotations

import asyncio
import json

from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
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


async def execute_task(
    session_factory: sessionmaker[Session],
    settings: Settings,
    claim: ClaimedTask,
    provider_registry: ProviderRegistry,
) -> bool:
    if claim.kind != "fake.echo":
        raise ValueError(f"UNSUPPORTED_TASK_KIND:{claim.kind}")

    payload = json.loads(claim.payload_json)
    provider = provider_registry.resolve(settings.provider_name)
    try:
        response = await provider.complete(task_kind=claim.kind, payload=payload)
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

    with session_factory() as session:
        if not task_claim_is_current(session, claim=claim):
            acknowledge_task_cancellation(session, claim=claim)
            return False
        artifact = write_json_artifact(
            session,
            settings,
            project_id=claim.project_id,
            kind="fake.echo.result",
            payload={
                "task_id": claim.id,
                "response": response.parsed,
                "usage": {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                },
            },
            created_by_task_id=claim.id,
            created_by_attempt_id=claim.current_attempt_id,
            lease_generation=claim.lease_generation,
            metadata={"provider": "fake"},
        )
        accepted = complete_task_attempt(
            session,
            task_id=claim.id,
            attempt_id=claim.current_attempt_id,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            result_artifact_id=artifact.id,
            provider_name=provider.name,
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
        return accepted


def execute_task_sync(
    session_factory: sessionmaker[Session],
    settings: Settings,
    claim: ClaimedTask,
    provider_registry: ProviderRegistry,
) -> bool:
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
                    settings.provider_name
                    if error_code.startswith("PROVIDER_")
                    else None
                ),
            )
            if not failed:
                acknowledge_task_cancellation(session, claim=claim)
            return failed
