from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "backend" / "alembic.ini"


def _alembic_config() -> Config:
    return Config(str(ALEMBIC_INI))


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _configure_migration_environment(monkeypatch, database: Path, workspace: Path) -> None:
    monkeypatch.setenv("AND_DATABASE_URL", _database_url(database))
    monkeypatch.setenv("AND_WORKSPACE_DIR", str(workspace))
    monkeypatch.setenv("AND_CORS_ORIGINS", '["http://127.0.0.1:5173"]')
    get_settings.cache_clear()


def _upgrade(revision: str) -> None:
    get_settings.cache_clear()
    command.upgrade(_alembic_config(), revision)
    get_settings.cache_clear()


def _downgrade(revision: str) -> None:
    get_settings.cache_clear()
    command.downgrade(_alembic_config(), revision)
    get_settings.cache_clear()


def _seed_0001_database(database: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO projects (id, name, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("prj_migration", "Migration fixture", "preserve me", now, now),
        )
        connection.execute(
            """
            INSERT INTO tasks (
                id, project_id, kind, status, payload_json,
                result_artifact_id, attempts, max_attempts,
                lease_owner, lease_expires_at,
                error_code, error_message,
                created_at, started_at, finished_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tsk_migration",
                "prj_migration",
                "fake.echo",
                "PENDING",
                '{"message":"preserve me"}',
                None,
                0,
                3,
                None,
                None,
                None,
                None,
                now,
                None,
                None,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO artifacts (
                id, project_id, kind, schema_version, status,
                content_hash, relative_path, created_by_task_id,
                metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "art_migration",
                "prj_migration",
                "migration.fixture",
                "1.0.0",
                "READY",
                "0" * 64,
                "artifacts/prj_migration/art_migration.json",
                "tsk_migration",
                "{}",
                now,
            ),
        )
        connection.commit()


def test_0001_data_survives_upgrade_and_downgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "migration.db"
    workspace = tmp_path / "workspace"
    _configure_migration_environment(monkeypatch, database, workspace)

    _upgrade("0001_m0_core")
    _seed_0001_database(database)
    _upgrade("head")

    with sqlite3.connect(database) as connection:
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        task = connection.execute(
            """
            SELECT status, payload_json, attempts, max_attempts,
                   current_attempt_id, lease_generation,
                   next_attempt_at, cancel_requested_at,
                   last_error_code, last_error_message
            FROM tasks
            WHERE id = 'tsk_migration'
            """
        ).fetchone()
        attempt_count = connection.execute(
            "SELECT COUNT(*) FROM task_attempts"
        ).fetchone()
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert revision == ("0002_task_attempts",)
    assert task == (
        "PENDING",
        '{"message":"preserve me"}',
        0,
        3,
        None,
        0,
        None,
        None,
        None,
        None,
    )
    assert attempt_count == (0,)
    assert foreign_key_errors == []

    settings = Settings(
        database_url=_database_url(database),
        workspace_dir=workspace,
        auto_create_schema=False,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/tasks/tsk_migration")
        assert response.status_code == 200
        payload = response.json()
        assert payload["current_attempt_id"] is None
        assert payload["lease_generation"] == 0
        assert payload["next_attempt_at"] is None

    _downgrade("0001_m0_core")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        task_after_downgrade = connection.execute(
            "SELECT status, payload_json FROM tasks WHERE id = 'tsk_migration'"
        ).fetchone()

    assert "task_attempts" not in tables
    assert task_after_downgrade == (
        "PENDING",
        '{"message":"preserve me"}',
    )

    _upgrade("head")


def test_migrated_schema_contains_task_attempt_constraints(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "schema.db"
    _configure_migration_environment(
        monkeypatch,
        database,
        tmp_path / "workspace",
    )
    _upgrade("head")

    with sqlite3.connect(database) as connection:
        task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)")
        }
        attempt_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(task_attempts)")
        }
        attempt_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(task_attempts)")
        }
        task_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(tasks)"
        ).fetchall()

    assert {
        "current_attempt_id",
        "lease_generation",
        "next_attempt_at",
        "cancel_requested_at",
        "last_error_code",
        "last_error_message",
    }.issubset(task_columns)
    assert {
        "id",
        "task_id",
        "attempt_no",
        "lease_generation",
        "lease_token",
        "worker_id",
        "status",
        "started_at",
        "heartbeat_at",
        "lease_expires_at",
        "finished_at",
        "provider_name",
        "error_code",
        "error_message",
        "usage_json",
    } == attempt_columns
    assert {
        "ux_task_attempt_task_no",
        "ux_task_attempt_lease_token",
        "ix_task_attempt_status_lease",
        "ix_task_attempt_task_started",
    }.issubset(attempt_indexes)
    assert any(
        row[2] == "task_attempts"
        and row[3] == "current_attempt_id"
        and row[4] == "id"
        for row in task_foreign_keys
    )
