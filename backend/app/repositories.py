from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session

from .models import Project, Task, TaskStatus


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


def claim_next_task(session: Session, *, worker_id: str, lease_seconds: int) -> Task | None:
    now = datetime.now(timezone.utc)
    stmt = (
        select(Task)
        .where(
            and_(
                Task.attempts < Task.max_attempts,
                or_(
                    Task.status == TaskStatus.PENDING.value,
                    and_(
                        Task.status == TaskStatus.RUNNING.value,
                        Task.lease_expires_at.is_not(None),
                        Task.lease_expires_at < now,
                    ),
                ),
            )
        )
        .order_by(Task.created_at.asc())
        .limit(1)
    )
    task = session.scalar(stmt)
    if task is None:
        return None

    task.status = TaskStatus.RUNNING.value
    task.lease_owner = worker_id
    task.lease_expires_at = now + timedelta(seconds=lease_seconds)
    task.attempts += 1
    task.started_at = task.started_at or now
    task.error_code = None
    task.error_message = None
    session.commit()
    session.refresh(task)
    return task


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
