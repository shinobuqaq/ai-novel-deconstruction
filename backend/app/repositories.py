from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.orm import Session

from .models import (
    Project,
    Task,
    TaskAttempt,
    TaskAttemptStatus,
    TaskStatus,
    new_id,
)


@dataclass(frozen=True, slots=True)
class ClaimedTask:
    id: str
    project_id: str
    kind: str
    payload_json: str
    attempts: int
    max_attempts: int
    current_attempt_id: str
    lease_token: str
    lease_generation: int
    lease_owner: str
    lease_expires_at: datetime


def create_project(session: Session, *, name: str, description: str | None) -> Project:
    project = Project(name=name.strip(), description=description)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def list_projects(session: Session) -> list[Project]:
    return list(session.scalars(select(Project).order_by(Project.created_at.desc())))


def get_project(session: Session, project_id: str) -> Project | None:
    return session.get(Project, project_id)


def create_task(
    session: Session,
    *,
    project_id: str,
    kind: str,
    payload: dict,
    max_attempts: int,
) -> Task:
    task = Task(
        project_id=project_id,
        kind=kind,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        max_attempts=max_attempts,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def list_tasks(session: Session, *, project_id: str | None = None) -> list[Task]:
    stmt: Select[tuple[Task]] = select(Task).order_by(Task.created_at.desc())
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    return list(session.scalars(stmt))


def get_task(session: Session, task_id: str) -> Task | None:
    return session.get(Task, task_id)


def claim_next_task(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> ClaimedTask | None:
    now = now or datetime.now(timezone.utc)
    lease_expires_at = now + timedelta(seconds=lease_seconds)

    try:
        connection = session.connection()
        if connection.dialect.name != "sqlite":
            raise RuntimeError("TASK_CLAIM_REQUIRES_SQLITE_ADAPTER")
        connection.exec_driver_sql("BEGIN IMMEDIATE")

        eligible = or_(
            Task.status == TaskStatus.PENDING.value,
            and_(
                Task.status == TaskStatus.RETRY_WAIT.value,
                Task.next_attempt_at.is_not(None),
                Task.next_attempt_at <= now,
            ),
        )
        candidate = session.execute(
            select(
                Task.id,
                Task.attempts,
                Task.max_attempts,
                Task.lease_generation,
            )
            .where(Task.attempts < Task.max_attempts, eligible)
            .order_by(
                Task.next_attempt_at.asc().nulls_first(),
                Task.created_at.asc(),
            )
            .limit(1)
        ).first()
        if candidate is None:
            session.rollback()
            return None

        attempt_id = new_id("att")
        next_attempt_no = candidate.attempts + 1
        next_generation = candidate.lease_generation + 1
        attempt = TaskAttempt(
            id=attempt_id,
            task_id=candidate.id,
            attempt_no=next_attempt_no,
            lease_generation=next_generation,
            lease_token=secrets.token_urlsafe(32),
            worker_id=worker_id,
            status=TaskAttemptStatus.RUNNING.value,
            started_at=now,
            heartbeat_at=now,
            lease_expires_at=lease_expires_at,
        )
        session.add(attempt)
        session.flush()

        result = session.execute(
            update(Task)
            .where(
                Task.id == candidate.id,
                Task.attempts == candidate.attempts,
                Task.lease_generation == candidate.lease_generation,
                Task.attempts < Task.max_attempts,
                eligible,
            )
            .values(
                status=TaskStatus.RUNNING.value,
                attempts=next_attempt_no,
                lease_generation=next_generation,
                current_attempt_id=attempt_id,
                lease_owner=worker_id,
                lease_expires_at=lease_expires_at,
                next_attempt_at=None,
                started_at=func.coalesce(Task.started_at, now),
                last_error_code=None,
                last_error_message=None,
                error_code=None,
                error_message=None,
            )
        )
        if result.rowcount != 1:
            session.rollback()
            return None

        session.commit()
        task = session.get(Task, candidate.id)
        if task is None or task.current_attempt is None:
            raise RuntimeError("CLAIMED_TASK_NOT_FOUND")
        return ClaimedTask(
            id=task.id,
            project_id=task.project_id,
            kind=task.kind,
            payload_json=task.payload_json,
            attempts=task.attempts,
            max_attempts=task.max_attempts,
            current_attempt_id=task.current_attempt.id,
            lease_token=task.current_attempt.lease_token,
            lease_generation=task.lease_generation,
            lease_owner=worker_id,
            lease_expires_at=task.current_attempt.lease_expires_at,
        )
    except Exception:
        session.rollback()
        raise


def heartbeat_task(
    session: Session,
    *,
    task_id: str,
    attempt_id: str,
    lease_token: str,
    lease_generation: int,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    next_expiry = now + timedelta(seconds=lease_seconds)

    attempt_result = session.execute(
        update(TaskAttempt)
        .where(
            TaskAttempt.id == attempt_id,
            TaskAttempt.task_id == task_id,
            TaskAttempt.lease_token == lease_token,
            TaskAttempt.lease_generation == lease_generation,
            TaskAttempt.worker_id == worker_id,
            TaskAttempt.status == TaskAttemptStatus.RUNNING.value,
            TaskAttempt.lease_expires_at > now,
        )
        .values(heartbeat_at=now, lease_expires_at=next_expiry)
    )
    task_result = session.execute(
        update(Task)
        .where(
            Task.id == task_id,
            Task.status == TaskStatus.RUNNING.value,
            Task.current_attempt_id == attempt_id,
            Task.lease_generation == lease_generation,
            Task.lease_owner == worker_id,
            Task.lease_expires_at > now,
        )
        .values(lease_expires_at=next_expiry)
    )
    if attempt_result.rowcount != 1 or task_result.rowcount != 1:
        session.rollback()
        return False

    session.commit()
    return True


def task_claim_is_current(
    session: Session,
    *,
    claim: ClaimedTask,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    current_attempt_id = session.scalar(
        select(Task.current_attempt_id)
        .join(TaskAttempt, Task.current_attempt_id == TaskAttempt.id)
        .where(
            Task.id == claim.id,
            Task.status == TaskStatus.RUNNING.value,
            Task.current_attempt_id == claim.current_attempt_id,
            Task.lease_generation == claim.lease_generation,
            Task.lease_owner == claim.lease_owner,
            Task.lease_expires_at > now,
            TaskAttempt.lease_token == claim.lease_token,
            TaskAttempt.worker_id == claim.lease_owner,
            TaskAttempt.status == TaskAttemptStatus.RUNNING.value,
            TaskAttempt.lease_expires_at > now,
        )
    )
    return current_attempt_id == claim.current_attempt_id


def complete_task_attempt(
    session: Session,
    *,
    task_id: str,
    attempt_id: str,
    lease_token: str,
    lease_generation: int,
    result_artifact_id: str,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)

    attempt_result = session.execute(
        update(TaskAttempt)
        .where(
            TaskAttempt.id == attempt_id,
            TaskAttempt.task_id == task_id,
            TaskAttempt.lease_token == lease_token,
            TaskAttempt.lease_generation == lease_generation,
            TaskAttempt.status == TaskAttemptStatus.RUNNING.value,
            TaskAttempt.lease_expires_at > now,
        )
        .values(
            status=TaskAttemptStatus.SUCCEEDED.value,
            finished_at=now,
        )
    )
    task_result = session.execute(
        update(Task)
        .where(
            Task.id == task_id,
            Task.status == TaskStatus.RUNNING.value,
            Task.current_attempt_id == attempt_id,
            Task.lease_generation == lease_generation,
            Task.lease_owner.is_not(None),
            Task.lease_expires_at > now,
        )
        .values(
            status=TaskStatus.SUCCEEDED.value,
            result_artifact_id=result_artifact_id,
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            last_error_message=None,
            error_code=None,
            error_message=None,
        )
    )
    if attempt_result.rowcount != 1 or task_result.rowcount != 1:
        session.rollback()
        return False

    session.commit()
    return True


def fail_claim_permanently(
    session: Session,
    *,
    claim: ClaimedTask,
    error_code: str,
    error_message: str,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(timezone.utc)
    message = error_message[:4000]

    attempt_result = session.execute(
        update(TaskAttempt)
        .where(
            TaskAttempt.id == claim.current_attempt_id,
            TaskAttempt.task_id == claim.id,
            TaskAttempt.lease_token == claim.lease_token,
            TaskAttempt.lease_generation == claim.lease_generation,
            TaskAttempt.status == TaskAttemptStatus.RUNNING.value,
            TaskAttempt.lease_expires_at > now,
        )
        .values(
            status=TaskAttemptStatus.PERMANENT_FAILED.value,
            finished_at=now,
            error_code=error_code,
            error_message=message,
        )
    )
    task_result = session.execute(
        update(Task)
        .where(
            Task.id == claim.id,
            Task.status == TaskStatus.RUNNING.value,
            Task.current_attempt_id == claim.current_attempt_id,
            Task.lease_generation == claim.lease_generation,
            Task.lease_expires_at > now,
        )
        .values(
            status=TaskStatus.FAILED.value,
            finished_at=now,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=error_code,
            last_error_message=message,
            error_code=error_code,
            error_message=message,
        )
    )
    if attempt_result.rowcount != 1 or task_result.rowcount != 1:
        session.rollback()
        return False

    session.commit()
    return True


def retry_task(session: Session, task: Task) -> Task:
    if task.status not in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
        return task
    task.status = TaskStatus.PENDING.value
    task.lease_owner = None
    task.lease_expires_at = None
    task.error_code = None
    task.error_message = None
    task.finished_at = None
    session.commit()
    session.refresh(task)
    return task
