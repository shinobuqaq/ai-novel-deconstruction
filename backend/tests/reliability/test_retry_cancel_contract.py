from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import TaskStatus
from app.providers import create_default_provider_registry
from app.repositories import (
    claim_next_task,
    fail_task_attempt,
    get_task,
    reap_expired_tasks,
    request_task_cancellation,
    retry_task,
)
from app.services.tasks import execute_task_sync


def test_retry_does_not_reopen_a_task_after_max_attempts(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory(
        kind="unsupported.kind",
        max_attempts=1,
    )

    with reliability_env.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="worker-exhausted",
            lease_seconds=60,
        )
        assert claim is not None
        assert claim.attempts == 1

    execute_task_sync(
        reliability_env.session_factory,
        reliability_env.settings,
        claim,
        create_default_provider_registry(),
    )

    with reliability_env.session_factory() as session:
        task = get_task(session, task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED.value

        retried = retry_task(session, task)
        assert retried.status == TaskStatus.FAILED.value

    with reliability_env.session_factory() as reading_session:
        persisted = get_task(reading_session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.FAILED.value


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


def test_retryable_failures_stop_when_budget_is_exhausted(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory(max_attempts=2)
    now = datetime.now(timezone.utc)

    for attempt_number in (1, 2):
        with reliability_env.session_factory() as session:
            claim = claim_next_task(
                session,
                worker_id=f"worker-retry-{attempt_number}",
                lease_seconds=60,
                now=now + timedelta(seconds=attempt_number),
            )
            assert claim is not None
            assert claim.attempts == attempt_number

        with reliability_env.session_factory() as session:
            accepted = fail_task_attempt(
                session,
                task_id=task_id,
                attempt_id=claim.current_attempt_id,
                lease_token=claim.lease_token,
                lease_generation=claim.lease_generation,
                error_code="PROVIDER_TIMEOUT",
                error_message="retry until exhausted",
                retryable=True,
                retry_after_seconds=0,
                now=now + timedelta(seconds=attempt_number, milliseconds=1),
            )
            assert accepted

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.FAILED.value
        assert persisted.attempts == 2
        assert persisted.error_code == "PROVIDER_TIMEOUT"
        assert claim_next_task(
            session,
            worker_id="worker-too-late",
            lease_seconds=60,
            now=now + timedelta(seconds=10),
        ) is None


def test_cancel_requested_task_converges_after_lease_expiry(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory()
    now = datetime.now(timezone.utc)

    with reliability_env.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="worker-cancelled",
            lease_seconds=1,
            now=now,
        )
        assert claim is not None

    with reliability_env.session_factory() as session:
        requested = request_task_cancellation(
            session,
            task_id=task_id,
            now=now + timedelta(milliseconds=1),
        )
        assert requested is not None
        assert requested.status == TaskStatus.CANCEL_REQUESTED.value

    with reliability_env.session_factory() as session:
        assert reap_expired_tasks(
            session,
            now=now + timedelta(seconds=2),
        ) == 1

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.CANCELLED.value
        assert persisted.current_attempt is not None
        assert persisted.current_attempt.status == "CANCELLED"
