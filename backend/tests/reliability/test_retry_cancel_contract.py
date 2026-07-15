from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import TaskStatus
from app.repositories import (
    claim_next_task,
    get_task,
    retry_task,
)
from app.services.tasks import execute_task_sync


@pytest.mark.xfail(
    reason=(
        "M0-GAP-RETRY-01: retry_task can return an exhausted task to "
        "PENDING, leaving it permanently unclaimable instead of terminal."
    ),
    strict=True,
)
def test_retry_does_not_reopen_a_task_after_max_attempts(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory(
        kind="unsupported.kind",
        max_attempts=1,
    )

    with reliability_env.session_factory() as session:
        task = claim_next_task(
            session,
            worker_id="worker-exhausted",
            lease_seconds=60,
        )
        assert task is not None
        assert task.attempts == 1

        execute_task_sync(
            session,
            reliability_env.settings,
            task,
        )
        assert task.status == TaskStatus.FAILED.value

        retried = retry_task(session, task)
        assert retried.status == TaskStatus.FAILED.value

    with reliability_env.session_factory() as reading_session:
        persisted = get_task(reading_session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.FAILED.value


@pytest.mark.xfail(
    reason=(
        "M0-GAP-CANCEL-01: CANCELLED is currently reopened by retry_task; "
        "terminal states are not immutable."
    ),
    strict=True,
)
def test_cancelled_task_is_terminal_and_cannot_be_reopened(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory()

    with reliability_env.session_factory() as session:
        task = get_task(session, task_id)
        assert task is not None

        task.status = TaskStatus.CANCELLED.value
        task.finished_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(task)

        retried = retry_task(session, task)
        assert retried.status == TaskStatus.CANCELLED.value


@pytest.mark.xfail(
    reason=(
        "M0-GAP-STATE-01: the state machine lacks RETRY_WAIT and "
        "CANCEL_REQUESTED, so backoff and cooperative cancellation cannot "
        "be represented."
    ),
    strict=True,
)
def test_task_state_machine_contains_reliability_states() -> None:
    actual = {status.value for status in TaskStatus}
    required = {
        "PENDING",
        "RUNNING",
        "RETRY_WAIT",
        "CANCEL_REQUESTED",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
    }

    assert required.issubset(actual)
