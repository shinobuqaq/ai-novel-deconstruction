from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import repositories
from app.models import TaskStatus
from app.repositories import claim_next_task, get_task
from app.services.artifacts import write_json_artifact


def _required_operation(name: str):
    operation = getattr(repositories, name, None)
    assert callable(operation), f"missing reliability operation: {name}"
    return operation


def _claim_identity(claimed) -> dict[str, object]:
    identity = {
        "attempt_id": getattr(claimed, "current_attempt_id", None),
        "lease_token": getattr(claimed, "lease_token", None),
        "lease_generation": getattr(claimed, "lease_generation", None),
    }
    assert all(identity.values()), identity
    return identity


def _error_code(task) -> str | None:
    return getattr(task, "last_error_code", None) or getattr(
        task,
        "error_code",
        None,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _claim_with_short_lease(reliability_env, task_id: str, worker_id: str):
    with reliability_env.session_factory() as session:
        claimed = claim_next_task(
            session,
            worker_id=worker_id,
            lease_seconds=1,
        )
        assert claimed is not None
        assert claimed.id == task_id
        lease_expires_at = claimed.lease_expires_at
        assert lease_expires_at is not None
        return _as_utc(lease_expires_at)


def test_expired_attempt_with_budget_is_reaped_to_retry_wait(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory(max_attempts=3)
    lease_expires_at = _claim_with_short_lease(
        reliability_env,
        task_id,
        "worker-retryable-expiry",
    )
    reap_expired_tasks = _required_operation("reap_expired_tasks")

    with reliability_env.session_factory() as session:
        reaped = reap_expired_tasks(
            session,
            now=lease_expires_at + timedelta(seconds=1),
        )
        session.commit()

    assert reaped == 1
    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == "RETRY_WAIT"
        assert persisted.attempts == 1
        assert persisted.next_attempt_at is not None


def test_expired_attempt_at_max_attempts_is_reaped_to_failed(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory(max_attempts=1)
    lease_expires_at = _claim_with_short_lease(
        reliability_env,
        task_id,
        "worker-exhausted-expiry",
    )
    reap_expired_tasks = _required_operation("reap_expired_tasks")

    with reliability_env.session_factory() as session:
        reaped = reap_expired_tasks(
            session,
            now=lease_expires_at + timedelta(seconds=1),
        )
        session.commit()

    assert reaped == 1
    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.FAILED.value
        assert persisted.attempts == 1
        assert _error_code(persisted) == "LEASE_EXPIRED_MAX_ATTEMPTS"
        assert persisted.finished_at is not None


def test_retryable_failure_enters_retry_wait(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory(max_attempts=3)
    now = datetime.now(timezone.utc)

    with reliability_env.session_factory() as session:
        claimed = claim_next_task(
            session,
            worker_id="worker-timeout",
            lease_seconds=60,
        )
        assert claimed is not None
        identity = _claim_identity(claimed)

    fail_task_attempt = _required_operation("fail_task_attempt")
    with reliability_env.session_factory() as session:
        fail_task_attempt(
            session,
            task_id=task_id,
            **identity,
            error_code="PROVIDER_TIMEOUT",
            error_message="deterministic timeout",
            retryable=True,
            retry_after_seconds=5,
            now=now,
        )
        session.commit()

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == "RETRY_WAIT"
        assert persisted.attempts == 1
        assert persisted.next_attempt_at is not None
        assert _as_utc(persisted.next_attempt_at) == (
            now + timedelta(seconds=5)
        )
        assert _error_code(persisted) == "PROVIDER_TIMEOUT"


def test_permanent_failure_stops_after_first_attempt(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory(max_attempts=3)
    now = datetime.now(timezone.utc)

    with reliability_env.session_factory() as session:
        claimed = claim_next_task(
            session,
            worker_id="worker-auth-failure",
            lease_seconds=60,
        )
        assert claimed is not None
        identity = _claim_identity(claimed)

    fail_task_attempt = _required_operation("fail_task_attempt")
    with reliability_env.session_factory() as session:
        fail_task_attempt(
            session,
            task_id=task_id,
            **identity,
            error_code="PROVIDER_AUTH_FAILED",
            error_message="deterministic authentication failure",
            retryable=False,
            retry_after_seconds=None,
            now=now,
        )
        session.commit()

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.FAILED.value
        assert persisted.attempts == 1
        assert _error_code(persisted) == "PROVIDER_AUTH_FAILED"
        assert persisted.finished_at is not None
        assert _as_utc(persisted.finished_at) == now


def _ready_artifact(reliability_env, task_id: str, project_id: str) -> str:
    with reliability_env.session_factory() as session:
        artifact = write_json_artifact(
            session,
            reliability_env.settings,
            project_id=project_id,
            kind="contract.cancel-race.result",
            payload={"task_id": task_id, "result": "ready"},
            created_by_task_id=task_id,
            metadata={"contract": "cancel-race"},
        )
        return artifact.id


def _claimed_task(reliability_env, task_id: str):
    with reliability_env.session_factory() as session:
        claimed = claim_next_task(
            session,
            worker_id="worker-cancel-race",
            lease_seconds=60,
        )
        assert claimed is not None
        assert claimed.id == task_id
        return _claim_identity(claimed)


def test_cancel_request_wins_when_recorded_before_completion(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    task_id = task_factory()
    artifact_id = _ready_artifact(reliability_env, task_id, project_id)
    identity = _claimed_task(reliability_env, task_id)
    now = datetime.now(timezone.utc)
    request_task_cancellation = _required_operation(
        "request_task_cancellation"
    )
    complete_task_attempt = _required_operation("complete_task_attempt")

    with reliability_env.session_factory() as session:
        request_task_cancellation(session, task_id=task_id, now=now)
        session.commit()

    with reliability_env.session_factory() as session:
        complete_task_attempt(
            session,
            task_id=task_id,
            **identity,
            result_artifact_id=artifact_id,
            now=now + timedelta(milliseconds=1),
        )
        session.commit()

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == "CANCEL_REQUESTED"
        assert persisted.result_artifact_id is None


def test_completion_wins_when_committed_before_cancel_request(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    task_id = task_factory()
    artifact_id = _ready_artifact(reliability_env, task_id, project_id)
    identity = _claimed_task(reliability_env, task_id)
    now = datetime.now(timezone.utc)
    request_task_cancellation = _required_operation(
        "request_task_cancellation"
    )
    complete_task_attempt = _required_operation("complete_task_attempt")

    with reliability_env.session_factory() as session:
        complete_task_attempt(
            session,
            task_id=task_id,
            **identity,
            result_artifact_id=artifact_id,
            now=now,
        )
        session.commit()

    with reliability_env.session_factory() as session:
        request_task_cancellation(
            session,
            task_id=task_id,
            now=now + timedelta(milliseconds=1),
        )
        session.commit()

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.SUCCEEDED.value
        assert persisted.result_artifact_id == artifact_id
