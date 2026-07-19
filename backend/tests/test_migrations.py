from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.db import create_db_engine
from app.main import create_app
from app.models import Base


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
        artifact = connection.execute(
            "SELECT result_key, blob_id FROM artifacts "
            "WHERE id = 'art_migration'"
        ).fetchone()
        blob_count = connection.execute(
            "SELECT COUNT(*) FROM artifact_blobs"
        ).fetchone()
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert revision == ("0010_task_attempt_diagnostics",)
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
    assert artifact == (
        "tsk_migration:migration.fixture",
        "blb_" + "0" * 64,
    )
    assert blob_count == (1,)
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
        artifact_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(artifacts)")
        }
        blob_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(artifact_blobs)")
        }
        artifact_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(artifacts)")
        }
        blob_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(artifact_blobs)")
        }
        artifact_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(artifacts)"
        ).fetchall()
        source_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('source_documents', 'source_versions', "
                "'source_units', 'source_issues', 'evidence_spans')"
            )
        }
        source_version_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(source_versions)")
        }
        source_version_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(source_versions)")
        }
        narrative_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(narrative_syntheses)")
        }
        narrative_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(narrative_syntheses)")
        }
        deep_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(deep_analyses)")
        }
        deep_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(deep_analyses)")
        }
        issue_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(analysis_issues)")
        }
        issue_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(analysis_issues)")
        }

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
        "diagnostics_json",
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
    assert {
        "result_key",
        "blob_id",
        "created_by_attempt_id",
        "lease_generation",
    }.issubset(artifact_columns)
    assert {
        "id",
        "content_hash",
        "status",
        "relative_path",
        "size_bytes",
        "created_at",
    } == blob_columns
    assert "ux_artifact_result_key" in artifact_indexes
    assert "ux_artifact_blob_content_hash" in blob_indexes
    assert any(
        row[2] == "artifact_blobs"
        and row[3] == "blob_id"
        and row[4] == "id"
        for row in artifact_foreign_keys
    )
    assert source_tables == {
        "source_documents",
        "source_versions",
        "source_units",
        "source_issues",
        "evidence_spans",
    }
    assert "parser_version" in source_version_columns
    assert "ux_source_version_hash_parser" in source_version_indexes
    assert "ux_source_version_hash" not in source_version_indexes
    assert {
        "id",
        "run_id",
        "source_version_id",
        "payload_json",
        "prompt_id",
        "prompt_version",
        "created_by_task_id",
        "created_by_attempt_id",
        "created_at",
    } == narrative_columns
    assert {
        "ux_narrative_synthesis_run",
        "ix_narrative_syntheses_source_version",
    }.issubset(narrative_indexes)
    assert {
        "id",
        "run_id",
        "source_version_id",
        "revision_no",
        "payload_json",
        "prompt_id",
        "prompt_version",
        "created_by_task_id",
        "created_by_attempt_id",
        "created_at",
    } == deep_columns
    assert {
        "ux_deep_analysis_run_revision",
        "ux_deep_analysis_task",
        "ix_deep_analyses_source_version",
    }.issubset(deep_indexes)
    assert {
        "id",
        "run_id",
        "target_kind",
        "target_id",
        "target_label",
        "category",
        "note",
        "status",
        "created_at",
        "resolved_at",
    } == issue_columns
    assert {
        "ix_analysis_issues_run_id",
        "ix_analysis_issues_run_status",
    }.issubset(issue_indexes)


def test_partial_auto_created_schema_is_repaired_without_data_loss(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "partial.db"
    workspace = tmp_path / "workspace"
    _configure_migration_environment(monkeypatch, database, workspace)

    _upgrade("0001_m0_core")
    _seed_0001_database(database)

    settings = Settings(
        database_url=_database_url(database),
        workspace_dir=workspace,
        auto_create_schema=True,
        _env_file=None,
    )
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    engine.dispose()

    with sqlite3.connect(database) as connection:
        revision_before = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        task_columns_before = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)")
        }
        attempt_table_before = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'task_attempts'"
        ).fetchone()

    assert revision_before == ("0001_m0_core",)
    assert attempt_table_before == ("task_attempts",)
    assert "current_attempt_id" not in task_columns_before

    _upgrade("head")

    with sqlite3.connect(database) as connection:
        revision_after = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        task = connection.execute(
            "SELECT payload_json, lease_generation FROM tasks "
            "WHERE id = 'tsk_migration'"
        ).fetchone()
        task_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(tasks)"
        ).fetchall()
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert revision_after == ("0010_task_attempt_diagnostics",)
    assert task == ('{"message":"preserve me"}', 0)
    assert any(
        row[2] == "task_attempts"
        and row[3] == "current_attempt_id"
        and row[4] == "id"
        for row in task_foreign_keys
    )
    assert foreign_key_errors == []


def test_existing_pre_revision_deep_table_is_upgraded_without_data_loss(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "old-deep.db"
    _configure_migration_environment(monkeypatch, database, tmp_path / "workspace")
    _upgrade("0007_narrative_synthesis")
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE deep_analyses (
                id VARCHAR(64) PRIMARY KEY,
                run_id VARCHAR(64) NOT NULL,
                source_version_id VARCHAR(64) NOT NULL,
                payload_json TEXT NOT NULL,
                prompt_id VARCHAR(80) NOT NULL,
                prompt_version VARCHAR(40) NOT NULL,
                created_by_task_id VARCHAR(64) NOT NULL,
                created_by_attempt_id VARCHAR(64) NOT NULL,
                created_at DATETIME NOT NULL
            );
            CREATE UNIQUE INDEX ux_deep_analysis_run ON deep_analyses (run_id);
            CREATE INDEX ix_deep_analyses_source_version ON deep_analyses (source_version_id);
            INSERT INTO deep_analyses
            VALUES ('dpa_old', 'run_old', 'svr_old', '{}', 'deep', '1.0.0', 'tsk_old', 'att_old', '2026-07-20T00:00:00');
            """
        )
        connection.commit()

    _upgrade("head")

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(deep_analyses)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(deep_analyses)")}
        row = connection.execute(
            "SELECT id, revision_no, payload_json FROM deep_analyses WHERE id = 'dpa_old'"
        ).fetchone()

    assert "revision_no" in columns
    assert "ux_deep_analysis_run" not in indexes
    assert {
        "ux_deep_analysis_run_revision",
        "ux_deep_analysis_task",
        "ix_deep_analyses_source_version",
    }.issubset(indexes)
    assert row == ("dpa_old", 1, "{}")
