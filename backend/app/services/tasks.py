from __future__ import annotations

import asyncio
import json

from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..providers.fake import FakeProvider
from ..repositories import (
    ClaimedTask,
    complete_task_attempt,
    fail_claim_permanently,
    task_claim_is_current,
)
from .artifacts import write_json_artifact


async def execute_task(
    session_factory: sessionmaker[Session],
    settings: Settings,
    claim: ClaimedTask,
) -> bool:
    if claim.kind != "fake.echo":
        raise ValueError(f"UNSUPPORTED_TASK_KIND:{claim.kind}")

    payload = json.loads(claim.payload_json)
    provider = FakeProvider()
    response = await provider.complete(task_kind=claim.kind, payload=payload)

    with session_factory() as session:
        if not task_claim_is_current(session, claim=claim):
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
            metadata={"provider": "fake"},
        )
        return complete_task_attempt(
            session,
            task_id=claim.id,
            attempt_id=claim.current_attempt_id,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            result_artifact_id=artifact.id,
        )


def execute_task_sync(
    session_factory: sessionmaker[Session],
    settings: Settings,
    claim: ClaimedTask,
) -> bool:
    try:
        return asyncio.run(execute_task(session_factory, settings, claim))
    except Exception as exc:
        with session_factory() as session:
            return fail_claim_permanently(
                session,
                claim=claim,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
