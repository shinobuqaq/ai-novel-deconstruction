from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select

from app.models import Artifact, ArtifactBlob
from app.services.artifacts import write_json_artifact


def test_same_task_result_is_idempotent(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    task_id = task_factory()
    payload = {"result": "same"}

    with reliability_env.session_factory() as session:
        first = write_json_artifact(
            session,
            reliability_env.settings,
            project_id=project_id,
            kind="contract.idempotent.result",
            payload=payload,
            created_by_task_id=task_id,
        )
        first_id = first.id

    with reliability_env.session_factory() as session:
        second = write_json_artifact(
            session,
            reliability_env.settings,
            project_id=project_id,
            kind="contract.idempotent.result",
            payload=payload,
            created_by_task_id=task_id,
        )

    assert second.id == first_id


def test_same_result_key_rejects_different_content(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    task_id = task_factory()
    with reliability_env.session_factory() as session:
        write_json_artifact(
            session,
            reliability_env.settings,
            project_id=project_id,
            kind="contract.conflict.result",
            payload={"version": 1},
            created_by_task_id=task_id,
        )

    with reliability_env.session_factory() as session:
        with pytest.raises(RuntimeError, match="ARTIFACT_RESULT_KEY_CONFLICT"):
            write_json_artifact(
                session,
                reliability_env.settings,
                project_id=project_id,
                kind="contract.conflict.result",
                payload={"version": 2},
                created_by_task_id=task_id,
            )


def test_concurrent_same_task_commit_creates_one_artifact(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    task_id = task_factory()
    barrier = Barrier(2, timeout=10)

    def write() -> str:
        with reliability_env.session_factory() as session:
            barrier.wait()
            artifact = write_json_artifact(
                session,
                reliability_env.settings,
                project_id=project_id,
                kind="contract.concurrent.result",
                payload={"result": "one"},
                created_by_task_id=task_id,
            )
            return artifact.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        artifact_ids = list(executor.map(lambda _: write(), range(2)))

    assert artifact_ids[0] == artifact_ids[1]
    with reliability_env.session_factory() as session:
        artifacts = list(
            session.scalars(
                select(Artifact).where(Artifact.created_by_task_id == task_id)
            )
        )
        blobs = list(session.scalars(select(ArtifactBlob)))
    assert len(artifacts) == 1
    assert len(blobs) == 1
