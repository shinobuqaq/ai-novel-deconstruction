from __future__ import annotations

from inspect import signature

import pytest

from app import repositories
from app.models import TaskStatus
from app.repositories import claim_next_task, get_task
from app.services.tasks import execute_task_sync


@pytest.mark.xfail(
    reason=(
        "M0-GAP-LEASE-01: stale workers can finalize after another worker "
        "reclaims an expired lease because finalization is not fenced."
    ),
    strict=True,
)
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

        with reliability_env.session_factory() as current_session:
            current_task = claim_next_task(
                current_session,
                worker_id="worker-current",
                lease_seconds=60,
            )
            assert current_task is not None
            assert current_task.id == task_id
            assert current_task.lease_owner == "worker-current"

        execute_task_sync(
            stale_session,
            reliability_env.settings,
            stale_task,
        )

    with reliability_env.session_factory() as reading_session:
        persisted = get_task(reading_session, task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.RUNNING.value
        assert persisted.lease_owner == "worker-current"
        assert persisted.result_artifact_id is None


@pytest.mark.xfail(
    reason=(
        "M0-GAP-LEASE-02: no heartbeat operation validates task, attempt, "
        "lease token, generation, owner, and current RUNNING state."
    ),
    strict=True,
)
def test_heartbeat_contract_requires_attempt_and_fencing_identity() -> None:
    heartbeat = getattr(repositories, "heartbeat_task", None)
    assert callable(heartbeat)

    parameters = signature(heartbeat).parameters
    required = {
        "task_id",
        "attempt_id",
        "lease_token",
        "lease_generation",
        "worker_id",
    }

    assert required.issubset(parameters)
