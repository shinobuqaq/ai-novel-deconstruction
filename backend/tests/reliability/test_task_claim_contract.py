from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

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


@pytest.mark.xfail(
    reason=(
        "M0-GAP-CLAIM-01: claim_next_task performs SELECT and UPDATE "
        "as separate operations, so two workers can select the same task."
    ),
    strict=True,
)
def test_two_synchronized_workers_cannot_both_claim_one_task(
    reliability_env,
    task_factory,
) -> None:
    task_id = task_factory()
    barrier = Barrier(2, timeout=10)

    def claim(worker_id: str) -> dict[str, str | None]:
        with reliability_env.session_factory() as session:
            original_scalar = session.scalar

            def synchronized_scalar(*args, **kwargs):
                selected = original_scalar(*args, **kwargs)
                barrier.wait()
                return selected

            session.scalar = synchronized_scalar  # type: ignore[method-assign]

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


@pytest.mark.xfail(
    reason=(
        "M0-GAP-CLAIM-02: a claim does not create an immutable Attempt "
        "identity, lease token, or lease generation."
    ),
    strict=True,
)
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
