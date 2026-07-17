from __future__ import annotations

import json
import secrets
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
) -> Task | None:
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
        if task is None:
            raise RuntimeError("CLAIMED_TASK_NOT_FOUND")
        return task
    except Exception:
        session.rollback()
        raise


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
