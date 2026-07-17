from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import TaskAttempt, TaskAttemptStatus
from app.repositories import create_project, create_task


def test_task_attempt_can_be_recorded_and_selected_as_current(client) -> None:
    now = datetime.now(timezone.utc)

    with client.app.state.session_factory() as session:
        project = create_project(
            session,
            name="Task attempt model",
            description=None,
        )
        task = create_task(
            session,
            project_id=project.id,
            kind="fake.echo",
            payload={"message": "schema"},
            max_attempts=3,
        )
        attempt = TaskAttempt(
            task_id=task.id,
            attempt_no=1,
            lease_generation=1,
            lease_token="lease-token-schema-test",
            worker_id="worker-schema-test",
            status=TaskAttemptStatus.RUNNING.value,
            started_at=now,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=60),
        )
        session.add(attempt)
        session.flush()

        task.current_attempt_id = attempt.id
        task.lease_generation = 1
        session.commit()

        session.refresh(task)
        session.refresh(attempt)

        assert task.current_attempt_id == attempt.id
        assert task.current_attempt is not None
        assert task.current_attempt.lease_token == "lease-token-schema-test"
        assert task.attempt_records == [attempt]
        assert attempt.task_id == task.id
        assert attempt.status == TaskAttemptStatus.RUNNING.value
