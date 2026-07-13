from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Task, TaskStatus
from ..providers.fake import FakeProvider
from .artifacts import write_json_artifact


async def execute_task(session: Session, settings: Settings, task: Task) -> None:
    if task.kind != "fake.echo":
        raise ValueError(f"UNSUPPORTED_TASK_KIND:{task.kind}")

    payload = json.loads(task.payload_json)
    provider = FakeProvider()
    response = await provider.complete(task_kind=task.kind, payload=payload)

    artifact = write_json_artifact(
        session,
        settings,
        project_id=task.project_id,
        kind="fake.echo.result",
        payload={
            "task_id": task.id,
            "response": response.parsed,
            "usage": {
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
            },
        },
        created_by_task_id=task.id,
        metadata={"provider": "fake"},
    )

    task.result_artifact_id = artifact.id
    task.status = TaskStatus.SUCCEEDED.value
    task.finished_at = datetime.now(timezone.utc)
    task.lease_owner = None
    task.lease_expires_at = None
    session.commit()


def execute_task_sync(session: Session, settings: Settings, task: Task) -> None:
    try:
        asyncio.run(execute_task(session, settings, task))
    except Exception as exc:
        task.status = TaskStatus.FAILED.value
        task.error_code = type(exc).__name__
        task.error_message = str(exc)[:4000]
        task.finished_at = datetime.now(timezone.utc)
        task.lease_owner = None
        task.lease_expires_at = None
        session.commit()
