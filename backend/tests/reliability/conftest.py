from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import create_db_engine, create_session_factory
from app.models import Base
from app.repositories import create_project, create_task


@dataclass(frozen=True, slots=True)
class ReliabilityEnvironment:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]


@pytest.fixture()
def reliability_env(tmp_path: Path) -> Iterator[ReliabilityEnvironment]:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'reliability.db').as_posix()}",
        workspace_dir=tmp_path / "workspace",
        auto_create_schema=True,
    )
    settings.ensure_directories()

    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    try:
        yield ReliabilityEnvironment(
            settings=settings,
            engine=engine,
            session_factory=session_factory,
        )
    finally:
        engine.dispose()


@pytest.fixture()
def project_id(reliability_env: ReliabilityEnvironment) -> str:
    with reliability_env.session_factory() as session:
        project = create_project(
            session,
            name="Reliability baseline",
            description="PR B contract test fixture",
        )
        return project.id


@pytest.fixture()
def task_factory(
    reliability_env: ReliabilityEnvironment,
    project_id: str,
) -> Callable[..., str]:
    def create(
        *,
        kind: str = "fake.echo",
        payload: dict | None = None,
        max_attempts: int = 3,
    ) -> str:
        with reliability_env.session_factory() as session:
            task = create_task(
                session,
                project_id=project_id,
                kind=kind,
                payload=payload or {"message": "baseline"},
                max_attempts=max_attempts,
            )
            return task.id

    return create
