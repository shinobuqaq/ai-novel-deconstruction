"""Separate artifact identity from content-addressed blobs.

Revision ID: 0003_artifact_blobs
Revises: 0002_task_attempts
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_artifact_blobs"
down_revision: Union[str, None] = "0002_task_attempts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ARTIFACT_BLOB_COLUMNS = {
    "id",
    "content_hash",
    "status",
    "relative_path",
    "size_bytes",
    "created_at",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "artifact_blobs" not in inspector.get_table_names():
        op.create_table(
            "artifact_blobs",
            sa.Column("id", sa.String(length=68), primary_key=True),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("relative_path", sa.Text(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), server_default="0", nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    else:
        existing_columns = {
            column["name"] for column in inspector.get_columns("artifact_blobs")
        }
        if existing_columns != ARTIFACT_BLOB_COLUMNS:
            raise RuntimeError(
                "INCOMPATIBLE_PARTIAL_ARTIFACT_BLOB_SCHEMA:"
                f"{sorted(existing_columns)}"
            )

    inspector = sa.inspect(bind)
    blob_indexes = {
        index["name"] for index in inspector.get_indexes("artifact_blobs")
    }
    if "ux_artifact_blob_content_hash" not in blob_indexes:
        op.create_index(
            "ux_artifact_blob_content_hash",
            "artifact_blobs",
            ["content_hash"],
            unique=True,
        )

    op.add_column(
        "artifacts",
        sa.Column("result_key", sa.String(length=240), nullable=True),
    )
    op.add_column(
        "artifacts",
        sa.Column("blob_id", sa.String(length=68), nullable=True),
    )
    op.add_column(
        "artifacts",
        sa.Column("created_by_attempt_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "artifacts",
        sa.Column("lease_generation", sa.Integer(), nullable=True),
    )

    rows = bind.execute(
        sa.text(
            "SELECT id, kind, content_hash, relative_path, created_by_task_id, "
            "status, created_at FROM artifacts ORDER BY created_at, id"
        )
    ).mappings()
    used_result_keys: set[str] = set()
    for row in rows:
        blob_id = f"blb_{row['content_hash']}"
        existing_blob = bind.execute(
            sa.text(
                "SELECT id FROM artifact_blobs WHERE content_hash = :content_hash"
            ),
            {"content_hash": row["content_hash"]},
        ).fetchone()
        if existing_blob is None:
            bind.execute(
                sa.text(
                    "INSERT INTO artifact_blobs "
                    "(id, content_hash, status, relative_path, size_bytes, created_at) "
                    "VALUES (:id, :content_hash, :status, :relative_path, 0, :created_at)"
                ),
                {
                    "id": blob_id,
                    "content_hash": row["content_hash"],
                    "status": row["status"],
                    "relative_path": row["relative_path"],
                    "created_at": row["created_at"],
                },
            )

        owner = row["created_by_task_id"] or row["id"]
        result_key = f"{owner}:{row['kind']}"
        if result_key in used_result_keys:
            result_key = f"{result_key}:{row['id']}"
        used_result_keys.add(result_key)
        bind.execute(
            sa.text(
                "UPDATE artifacts SET result_key = :result_key, blob_id = :blob_id "
                "WHERE id = :artifact_id"
            ),
            {
                "result_key": result_key,
                "blob_id": blob_id,
                "artifact_id": row["id"],
            },
        )

    with op.batch_alter_table("artifacts", recreate="always") as batch_op:
        batch_op.drop_index("ux_artifact_project_hash_kind")
        batch_op.alter_column(
            "result_key",
            existing_type=sa.String(length=240),
            nullable=False,
        )
        batch_op.alter_column(
            "blob_id",
            existing_type=sa.String(length=68),
            nullable=False,
        )
        batch_op.create_foreign_key(
            "fk_artifacts_blob_id_artifact_blobs",
            "artifact_blobs",
            ["blob_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ux_artifact_result_key",
            ["result_key"],
            unique=True,
        )
        batch_op.create_index(
            "ix_artifacts_blob_id",
            ["blob_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("artifacts", recreate="always") as batch_op:
        batch_op.drop_index("ix_artifacts_blob_id")
        batch_op.drop_index("ux_artifact_result_key")
        batch_op.drop_constraint(
            "fk_artifacts_blob_id_artifact_blobs",
            type_="foreignkey",
        )
        batch_op.drop_column("lease_generation")
        batch_op.drop_column("created_by_attempt_id")
        batch_op.drop_column("blob_id")
        batch_op.drop_column("result_key")
        batch_op.create_index(
            "ux_artifact_project_hash_kind",
            ["project_id", "kind", "content_hash"],
            unique=True,
        )

    op.drop_index(
        "ux_artifact_blob_content_hash",
        table_name="artifact_blobs",
    )
    op.drop_table("artifact_blobs")
