"""Add task attempt history and reliability state fields.

Revision ID: 0002_task_attempts
Revises: 0001_m0_core
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_task_attempts"
down_revision: Union[str, None] = "0001_m0_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ATTEMPT_COLUMNS = {
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
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "task_attempts" not in inspector.get_table_names():
        op.create_table(
            "task_attempts",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "task_id",
                sa.String(length=64),
                sa.ForeignKey("tasks.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("attempt_no", sa.Integer(), nullable=False),
            sa.Column("lease_generation", sa.Integer(), nullable=False),
            sa.Column("lease_token", sa.String(length=128), nullable=False),
            sa.Column("worker_id", sa.String(length=120), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("provider_name", sa.String(length=100), nullable=True),
            sa.Column("error_code", sa.String(length=100), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("usage_json", sa.Text(), server_default="{}", nullable=False),
        )
    else:
        existing_columns = {
            column["name"] for column in inspector.get_columns("task_attempts")
        }
        if existing_columns != ATTEMPT_COLUMNS:
            raise RuntimeError(
                "INCOMPATIBLE_PARTIAL_TASK_ATTEMPTS_SCHEMA:"
                f"{sorted(existing_columns)}"
            )

    inspector = sa.inspect(bind)
    existing_indexes = {
        index["name"] for index in inspector.get_indexes("task_attempts")
    }
    required_indexes = (
        ("ux_task_attempt_task_no", ["task_id", "attempt_no"], True),
        ("ux_task_attempt_lease_token", ["lease_token"], True),
        ("ix_task_attempt_status_lease", ["status", "lease_expires_at"], False),
        ("ix_task_attempt_task_started", ["task_id", "started_at"], False),
    )
    for name, columns, unique in required_indexes:
        if name not in existing_indexes:
            op.create_index(name, "task_attempts", columns, unique=unique)

    inspector = sa.inspect(bind)
    task_columns = {
        column["name"] for column in inspector.get_columns("tasks")
    }
    task_foreign_keys = inspector.get_foreign_keys("tasks")
    has_current_attempt_fk = any(
        foreign_key.get("referred_table") == "task_attempts"
        and foreign_key.get("constrained_columns") == ["current_attempt_id"]
        for foreign_key in task_foreign_keys
    )

    if not {
        "current_attempt_id",
        "lease_generation",
        "next_attempt_at",
        "cancel_requested_at",
        "last_error_code",
        "last_error_message",
    }.issubset(task_columns) or not has_current_attempt_fk:
        with op.batch_alter_table("tasks", recreate="always") as batch_op:
            if "current_attempt_id" not in task_columns:
                batch_op.add_column(
                    sa.Column("current_attempt_id", sa.String(length=64), nullable=True)
                )
            if "lease_generation" not in task_columns:
                batch_op.add_column(
                    sa.Column(
                        "lease_generation",
                        sa.Integer(),
                        server_default="0",
                        nullable=False,
                    )
                )
            if "next_attempt_at" not in task_columns:
                batch_op.add_column(
                    sa.Column(
                        "next_attempt_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    )
                )
            if "cancel_requested_at" not in task_columns:
                batch_op.add_column(
                    sa.Column(
                        "cancel_requested_at",
                        sa.DateTime(timezone=True),
                        nullable=True,
                    )
                )
            if "last_error_code" not in task_columns:
                batch_op.add_column(
                    sa.Column("last_error_code", sa.String(length=100), nullable=True)
                )
            if "last_error_message" not in task_columns:
                batch_op.add_column(
                    sa.Column("last_error_message", sa.Text(), nullable=True)
                )
            if not has_current_attempt_fk:
                batch_op.create_foreign_key(
                    "fk_tasks_current_attempt_id_task_attempts",
                    "task_attempts",
                    ["current_attempt_id"],
                    ["id"],
                    ondelete="SET NULL",
                )


def downgrade() -> None:
    with op.batch_alter_table("tasks", recreate="always") as batch_op:
        batch_op.drop_constraint(
            "fk_tasks_current_attempt_id_task_attempts",
            type_="foreignkey",
        )
        batch_op.drop_column("last_error_message")
        batch_op.drop_column("last_error_code")
        batch_op.drop_column("cancel_requested_at")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("lease_generation")
        batch_op.drop_column("current_attempt_id")

    op.drop_index("ix_task_attempt_task_started", table_name="task_attempts")
    op.drop_index("ix_task_attempt_status_lease", table_name="task_attempts")
    op.drop_index("ux_task_attempt_lease_token", table_name="task_attempts")
    op.drop_index("ux_task_attempt_task_no", table_name="task_attempts")
    op.drop_table("task_attempts")
