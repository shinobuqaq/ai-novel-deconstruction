from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from time import monotonic, sleep

from sqlalchemy import select

from app.models import Artifact, TaskAttemptStatus, TaskStatus
from app.repositories import claim_next_task, get_task, heartbeat_task
from app.services.tasks import execute_task_sync
from app.worker import _maintain_lease
from app.providers import create_default_provider_registry


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _simulate_expired_attempt_reaped(reliability_env, task_id: str) -> None:
    with reliability_env.session_factory() as session:
        task = get_task(session, task_id)
        assert task is not None
        assert task.current_attempt is not None
        task.current_attempt.status = TaskAttemptStatus.EXPIRED.value
        task.current_attempt.finished_at = datetime.now(timezone.utc)
        task.status = TaskStatus.RETRY_WAIT.value
        task.next_attempt_at = datetime.now(timezone.utc)
        task.lease_owner = None
        task.lease_expires_at = None
        session.commit()


def test_stale_worker_cannot_finalize_after_lease_reclaim(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory(payload={"message": "fenced completion"})

    with reliability_env.session_factory() as stale_session:
        stale_task = claim_next_task(
            stale_session,
            worker_id="worker-stale",
            lease_seconds=-1,
        )
        assert stale_task is not None

    _simulate_expired_attempt_reaped(reliability_env, task_id)

    with reliability_env.session_factory() as current_session:
        current_task = claim_next_task(
            current_session,
            worker_id="worker-current",
            lease_seconds=60,
        )
        assert current_task is not None
        assert current_task.id == task_id
        assert current_task.lease_owner == "worker-current"

    assert not execute_task_sync(
        reliability_env.session_factory,
        reliability_env.settings,
        stale_task,
        create_default_provider_registry(),
    )

    with reliability_env.session_factory() as reading_session:
        persisted = get_task(reading_session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.RUNNING.value
        assert persisted.lease_owner == "worker-current"
        assert persisted.result_artifact_id is None
        stale_attempt = next(
            attempt
            for attempt in persisted.attempt_records
            if attempt.id == stale_task.current_attempt_id
        )
        assert stale_attempt.status == TaskAttemptStatus.EXPIRED.value
        stale_artifacts = list(
            reading_session.scalars(
                select(Artifact).where(Artifact.created_by_task_id == task_id)
            )
        )
        assert stale_artifacts == []


def test_stale_worker_failure_cannot_overwrite_new_attempt(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory(kind="unsupported.kind")

    with reliability_env.session_factory() as session:
        stale_claim = claim_next_task(
            session,
            worker_id="worker-stale",
            lease_seconds=-1,
        )
        assert stale_claim is not None

    _simulate_expired_attempt_reaped(reliability_env, task_id)

    with reliability_env.session_factory() as session:
        current_claim = claim_next_task(
            session,
            worker_id="worker-current",
            lease_seconds=60,
        )
        assert current_claim is not None
        assert current_claim.id == task_id

    assert not execute_task_sync(
        reliability_env.session_factory,
        reliability_env.settings,
        stale_claim,
        create_default_provider_registry(),
    )

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.RUNNING.value
        assert persisted.current_attempt_id == current_claim.current_attempt_id
        assert persisted.lease_owner == "worker-current"
        assert persisted.error_code is None


def test_current_worker_can_renew_lease(reliability_env, task_factory) -> None:
    task_factory()
    now = datetime.now(timezone.utc)
    with reliability_env.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="worker-current",
            lease_seconds=10,
            now=now,
        )
        assert claim is not None

    with reliability_env.session_factory() as session:
        renewed = heartbeat_task(
            session,
            task_id=claim.id,
            attempt_id=claim.current_attempt_id,
            lease_token=claim.lease_token,
            lease_generation=claim.lease_generation,
            worker_id=claim.lease_owner,
            lease_seconds=30,
            now=now + timedelta(seconds=5),
        )
    assert renewed

    with reliability_env.session_factory() as session:
        persisted = get_task(session, claim.id)
        assert persisted is not None
        assert persisted.lease_expires_at is not None
        assert _as_utc(persisted.lease_expires_at) == now + timedelta(seconds=35)
        assert persisted.current_attempt is not None
        assert _as_utc(persisted.current_attempt.heartbeat_at) == (
            now + timedelta(seconds=5)
        )


def test_wrong_or_expired_lease_cannot_heartbeat(
    reliability_env,
    task_factory,
) -> None:
    task_factory()
    now = datetime.now(timezone.utc)
    with reliability_env.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="worker-current",
            lease_seconds=10,
            now=now,
        )
        assert claim is not None

    for token, heartbeat_at in (
        ("wrong-token", now + timedelta(seconds=1)),
        (claim.lease_token, now + timedelta(seconds=11)),
    ):
        with reliability_env.session_factory() as session:
            renewed = heartbeat_task(
                session,
                task_id=claim.id,
                attempt_id=claim.current_attempt_id,
                lease_token=token,
                lease_generation=claim.lease_generation,
                worker_id=claim.lease_owner,
                lease_seconds=30,
                now=heartbeat_at,
            )
        assert not renewed


def test_worker_maintains_lease_in_background(
    reliability_env,
    task_factory,
) -> None:
    task_factory()
    with reliability_env.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="worker-background-heartbeat",
            lease_seconds=2,
        )
        assert claim is not None

    stop = Event()
    lease_lost = Event()
    heartbeat = Thread(
        target=_maintain_lease,
        kwargs={
            "session_factory": reliability_env.session_factory,
            "claim": claim,
            "lease_seconds": 2,
            "stop": stop,
            "lease_lost": lease_lost,
            "heartbeat_interval_seconds": 0.01,
        },
        daemon=True,
    )
    heartbeat.start()

    heartbeat_changed = False
    deadline = monotonic() + 2
    while monotonic() < deadline:
        with reliability_env.session_factory() as session:
            persisted = get_task(session, claim.id)
            assert persisted is not None
            assert persisted.current_attempt is not None
            heartbeat_changed = (
                _as_utc(persisted.current_attempt.heartbeat_at)
                > _as_utc(claim.lease_expires_at) - timedelta(seconds=2)
            )
        if heartbeat_changed:
            break
        sleep(0.01)

    stop.set()
    heartbeat.join(timeout=2)

    assert heartbeat_changed
    assert not heartbeat.is_alive()
    assert not lease_lost.is_set()
