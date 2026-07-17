from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import Artifact, ArtifactBlob, ArtifactStatus, TaskStatus
from app.repositories import claim_next_task, get_task
from app.services.artifacts import (
    ArtifactFaultPoint,
    CrashAt,
    InjectedArtifactCrash,
    reconcile_artifacts,
    write_json_artifact,
)


def test_crash_after_temp_write_is_cleaned(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    task_id = task_factory()
    with pytest.raises(InjectedArtifactCrash):
        with reliability_env.session_factory() as session:
            write_json_artifact(
                session,
                reliability_env.settings,
                project_id=project_id,
                kind="recovery.temp.result",
                payload={"fault": "after-temp"},
                created_by_task_id=task_id,
                fault_injector=CrashAt(ArtifactFaultPoint.AFTER_TEMP_WRITE),
            )

    assert list(reliability_env.settings.workspace_dir.rglob("*.tmp-*"))
    with reliability_env.session_factory() as session:
        report = reconcile_artifacts(
            session,
            reliability_env.settings,
            stale_after_seconds=0,
        )
    assert report.temp_files_removed == 1
    assert not list(reliability_env.settings.workspace_dir.rglob("*.tmp-*"))


def test_crash_after_replace_adopts_orphan_blob(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    task_id = task_factory()
    with pytest.raises(InjectedArtifactCrash):
        with reliability_env.session_factory() as session:
            write_json_artifact(
                session,
                reliability_env.settings,
                project_id=project_id,
                kind="recovery.orphan.result",
                payload={"fault": "after-replace"},
                created_by_task_id=task_id,
                fault_injector=CrashAt(ArtifactFaultPoint.AFTER_REPLACE),
            )

    with reliability_env.session_factory() as session:
        assert list(session.scalars(select(ArtifactBlob))) == []
        report = reconcile_artifacts(
            session,
            reliability_env.settings,
            stale_after_seconds=0,
        )
    assert report.orphan_blobs_adopted == 1

    with reliability_env.session_factory() as session:
        blobs = list(session.scalars(select(ArtifactBlob)))
        second = reconcile_artifacts(
            session,
            reliability_env.settings,
            stale_after_seconds=0,
        )
    assert len(blobs) == 1
    assert blobs[0].status == ArtifactStatus.READY.value
    assert second.changes == 0


def test_crash_after_artifact_commit_recovers_current_task(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    task_id = task_factory()
    with reliability_env.session_factory() as session:
        claim = claim_next_task(
            session,
            worker_id="worker-artifact-crash",
            lease_seconds=60,
        )
        assert claim is not None

    with pytest.raises(InjectedArtifactCrash):
        with reliability_env.session_factory() as session:
            write_json_artifact(
                session,
                reliability_env.settings,
                project_id=project_id,
                kind="recovery.task.result",
                payload={"fault": "after-artifact-commit"},
                created_by_task_id=task_id,
                created_by_attempt_id=claim.current_attempt_id,
                lease_generation=claim.lease_generation,
                fault_injector=CrashAt(
                    ArtifactFaultPoint.AFTER_ARTIFACT_COMMIT
                ),
            )

    with reliability_env.session_factory() as session:
        before = get_task(session, task_id)
        assert before is not None
        assert before.status == TaskStatus.RUNNING.value
        report = reconcile_artifacts(
            session,
            reliability_env.settings,
            stale_after_seconds=0,
        )
    assert report.tasks_recovered == 1

    with reliability_env.session_factory() as session:
        recovered = get_task(session, task_id)
        assert recovered is not None
        assert recovered.status == TaskStatus.SUCCEEDED.value
        assert recovered.result_artifact_id is not None
        second = reconcile_artifacts(
            session,
            reliability_env.settings,
            stale_after_seconds=0,
        )
    assert second.changes == 0


def test_stale_attempt_artifact_cannot_recover_task(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    task_id = task_factory()
    with reliability_env.session_factory() as session:
        stale_claim = claim_next_task(
            session,
            worker_id="worker-stale-artifact",
            lease_seconds=60,
        )
        assert stale_claim is not None

    with pytest.raises(InjectedArtifactCrash):
        with reliability_env.session_factory() as session:
            write_json_artifact(
                session,
                reliability_env.settings,
                project_id=project_id,
                kind="recovery.stale.result",
                payload={"fault": "stale-attempt"},
                created_by_task_id=task_id,
                created_by_attempt_id=stale_claim.current_attempt_id,
                lease_generation=stale_claim.lease_generation,
                fault_injector=CrashAt(
                    ArtifactFaultPoint.AFTER_ARTIFACT_COMMIT
                ),
            )

    with reliability_env.session_factory() as session:
        task = get_task(session, task_id)
        assert task is not None
        assert task.current_attempt is not None
        task.current_attempt.status = "EXPIRED"
        task.status = TaskStatus.RETRY_WAIT.value
        task.next_attempt_at = datetime.now(timezone.utc)
        task.lease_owner = None
        task.lease_expires_at = None
        session.commit()

    with reliability_env.session_factory() as session:
        current_claim = claim_next_task(
            session,
            worker_id="worker-current-artifact",
            lease_seconds=60,
        )
        assert current_claim is not None
        report = reconcile_artifacts(
            session,
            reliability_env.settings,
            stale_after_seconds=0,
        )
    assert report.tasks_recovered == 0
    assert report.artifacts_marked_dirty == 1

    with reliability_env.session_factory() as session:
        task = get_task(session, task_id)
        artifact = session.scalar(
            select(Artifact).where(Artifact.created_by_task_id == task_id)
        )
        assert task is not None
        assert task.status == TaskStatus.RUNNING.value
        assert task.current_attempt_id == current_claim.current_attempt_id
        assert artifact is not None
        assert artifact.status == ArtifactStatus.DIRTY.value


def test_missing_ready_blob_is_marked_dirty_idempotently(
    reliability_env,
    task_factory,
    project_id,
) -> None:
    task_id = task_factory()
    with reliability_env.session_factory() as session:
        artifact = write_json_artifact(
            session,
            reliability_env.settings,
            project_id=project_id,
            kind="recovery.missing.result",
            payload={"fault": "missing-file"},
            created_by_task_id=task_id,
        )
        blob_id = artifact.blob_id

    with reliability_env.session_factory() as session:
        blob = session.get(ArtifactBlob, blob_id)
        assert blob is not None
        (reliability_env.settings.workspace_dir / blob.relative_path).unlink()
        first = reconcile_artifacts(
            session,
            reliability_env.settings,
            stale_after_seconds=0,
        )
    assert first.blobs_marked_dirty == 1
    assert first.artifacts_marked_dirty == 1

    with reliability_env.session_factory() as session:
        blob = session.get(ArtifactBlob, blob_id)
        artifact = session.scalar(
            select(Artifact).where(Artifact.created_by_task_id == task_id)
        )
        second = reconcile_artifacts(
            session,
            reliability_env.settings,
            stale_after_seconds=0,
        )
    assert blob is not None and blob.status == ArtifactStatus.DIRTY.value
    assert artifact is not None and artifact.status == ArtifactStatus.DIRTY.value
    assert second.changes == 0
