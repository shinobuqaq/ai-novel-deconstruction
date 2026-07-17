from __future__ import annotations

from inspect import signature

import pytest

from app.services import artifacts
from app.services.artifacts import write_json_artifact
from app.services.tasks import execute_task


def test_task_execution_accepts_an_injected_provider_boundary() -> None:
    parameters = signature(execute_task).parameters
    assert (
        "provider" in parameters
        or "provider_registry" in parameters
    )


def test_same_blob_from_two_tasks_has_distinct_artifact_identity(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    first_task_id = task_factory(payload={"task": "first"})
    second_task_id = task_factory(payload={"task": "second"})
    shared_payload = {"normalized": "same-content"}

    with reliability_env.session_factory() as first_session:
        first = write_json_artifact(
            first_session,
            reliability_env.settings,
            project_id=project_id,
            kind="contract.result",
            payload=shared_payload,
            created_by_task_id=first_task_id,
            metadata={"source": "first"},
        )

    with reliability_env.session_factory() as second_session:
        second = write_json_artifact(
            second_session,
            reliability_env.settings,
            project_id=project_id,
            kind="contract.result",
            payload=shared_payload,
            created_by_task_id=second_task_id,
            metadata={"source": "second"},
        )

    assert first.id != second.id
    assert first.content_hash == second.content_hash
    assert first.blob_id == second.blob_id
    assert first.created_by_task_id == first_task_id
    assert second.created_by_task_id == second_task_id


@pytest.mark.xfail(
    reason=(
        "M0-GAP-ARTIFACT-02: no reconciler repairs WRITING rows, promoted "
        "files, missing files, or stale-generation artifact commits."
    ),
    strict=True,
)
def test_artifact_service_exposes_a_crash_recovery_reconciler() -> None:
    candidates = (
        "reconcile_artifacts",
        "recover_artifacts",
        "reconcile_incomplete_artifacts",
    )

    available = [
        name
        for name in candidates
        if callable(getattr(artifacts, name, None))
    ]

    assert available
