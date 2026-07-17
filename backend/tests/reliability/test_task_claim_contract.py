from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from app.models import TaskStatus
from app.repositories import claim_next_task, get_task


def test_reliability_harness_persists_across_independent_sessions(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory()

    with reliability_env.session_factory() as claiming_session:
        claimed = claim_next_task(
            claiming_session,
            worker_id="worker-harness",
            lease_seconds=60,
        )
        assert claimed is not None
        assert claimed.id == task_id

    with reliability_env.session_factory() as reading_session:
        persisted = get_task(reading_session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.RUNNING.value
        assert persisted.lease_owner == "worker-harness"
        assert persisted.attempts == 1


def test_two_synchronized_workers_cannot_both_claim_one_task(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory()
    barrier = Barrier(2, timeout=10)

    def claim(worker_id: str) -> dict[str, str | None]:
        with reliability_env.session_factory() as session:
            barrier.wait()
            try:
                task = claim_next_task(
                    session,
                    worker_id=worker_id,
                    lease_seconds=60,
                )
            except Exception as exc:  # noqa: BLE001 - result is asserted below
                return {
                    "worker": worker_id,
                    "task_id": None,
                    "error": f"{type(exc).__name__}:{exc}",
                }

            return {
                "worker": worker_id,
                "task_id": None if task is None else task.id,
                "error": None,
            }

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(claim, "worker-a"),
            executor.submit(claim, "worker-b"),
        ]
        results = [future.result(timeout=20) for future in futures]

    errors = [result["error"] for result in results if result["error"]]
    claimed = [
        result
        for result in results
        if result["task_id"] == task_id and result["error"] is None
    ]
    not_claimed = [
        result
        for result in results
        if result["task_id"] is None and result["error"] is None
    ]

    assert errors == []
    assert len(claimed) == 1
    assert len(not_claimed) == 1


def test_claim_creates_an_attempt_identity_and_fencing_token(
    reliability_env,
    task_factory,
) -> None:
    task_factory()

    with reliability_env.session_factory() as session:
        claimed = claim_next_task(
            session,
            worker_id="worker-attempt",
            lease_seconds=60,
        )
        assert claimed is not None

        required_fields = (
            "current_attempt_id",
            "lease_token",
            "lease_generation",
        )
        missing = [
            field
            for field in required_fields
            if not hasattr(claimed, field)
        ]

        assert missing == []
        assert claimed.current_attempt_id
        assert claimed.lease_token
        assert claimed.lease_generation >= 1


def test_twenty_workers_create_exactly_one_attempt(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory()
    barrier = Barrier(20, timeout=20)

    def claim(worker_number: int) -> tuple[str | None, str | None]:
        with reliability_env.session_factory() as session:
            barrier.wait()
            task = claim_next_task(
                session,
                worker_id=f"worker-{worker_number}",
                lease_seconds=60,
            )
            if task is None:
                return None, None
            return task.id, task.current_attempt_id

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(claim, range(20)))

    claimed = [result for result in results if result[0] is not None]
    assert len(claimed) == 1
    assert claimed[0][0] == task_id
    assert claimed[0][1] is not None

    with reliability_env.session_factory() as session:
        persisted = get_task(session, task_id)
        assert persisted is not None
        assert persisted.attempts == 1
        assert len(persisted.attempt_records) == 1


def test_two_workers_claim_two_different_tasks(
    reliability_env,
    task_factory,
) -> None:
    task_ids = {task_factory(), task_factory()}
    barrier = Barrier(2, timeout=10)

    def claim(worker_id: str) -> str | None:
        with reliability_env.session_factory() as session:
            barrier.wait()
            task = claim_next_task(
                session,
                worker_id=worker_id,
                lease_seconds=60,
            )
            return None if task is None else task.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed_ids = set(executor.map(claim, ("worker-a", "worker-b")))

    assert claimed_ids == task_ids


def test_retry_wait_task_is_claimed_only_after_next_attempt_at(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory()
    now = datetime.now(timezone.utc)

    with reliability_env.session_factory() as session:
        task = get_task(session, task_id)
        assert task is not None
        task.status = TaskStatus.RETRY_WAIT.value
        task.next_attempt_at = now + timedelta(seconds=30)
        session.commit()

    with reliability_env.session_factory() as session:
        too_early = claim_next_task(
            session,
            worker_id="worker-early",
            lease_seconds=60,
            now=now,
        )
        assert too_early is None

    with reliability_env.session_factory() as session:
        due = claim_next_task(
            session,
            worker_id="worker-due",
            lease_seconds=60,
            now=now + timedelta(seconds=31),
        )
        assert due is not None
        assert due.id == task_id
        assert due.attempts == 1
