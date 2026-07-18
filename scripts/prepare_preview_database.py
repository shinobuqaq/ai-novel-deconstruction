from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


AUTO_SCHEMA_TABLES = {
    "projects",
    "tasks",
    "task_attempts",
    "artifacts",
    "artifact_blobs",
    "source_documents",
    "source_versions",
    "source_units",
    "source_issues",
    "evidence_spans",
    "analysis_runs",
    "analysis_run_tasks",
    "entity_candidates",
    "event_candidates",
}


def adopt_auto_created_schema(database: Path) -> str:
    if not database.exists() or database.stat().st_size == 0:
        return "NEW_DATABASE"

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "alembic_version" in tables:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            if revision:
                return f"VERSIONED:{revision[0]}"

        if not AUTO_SCHEMA_TABLES.issubset(tables):
            missing = sorted(AUTO_SCHEMA_TABLES - tables)
            raise RuntimeError(f"旧预览数据库结构不完整，缺少表：{', '.join(missing)}")

        version_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(source_versions)")
        }
        revision = (
            "0006_source_parser_version"
            if "parser_version" in version_columns
            else "0005_analysis_candidates"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
        connection.execute("DELETE FROM alembic_version")
        connection.execute(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            (revision,),
        )
        connection.commit()
        return f"ADOPTED:{revision}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    print(adopt_auto_created_schema(args.database.resolve()))


if __name__ == "__main__":
    main()
