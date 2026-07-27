"""Add versioned creation learning reports.

Revision ID: 0014_learning_reports
Revises: 0013_person_identity_decisions
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0014_learning_reports"
down_revision = "0013_person_identity_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "learning_reports" not in inspector.get_table_names():
        op.create_table(
            "learning_reports",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("source_version_id", sa.String(length=64), nullable=False),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("source_deep_revision", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("prompt_id", sa.String(length=80), nullable=False),
            sa.Column("prompt_version", sa.String(length=40), nullable=False),
            sa.Column("created_by_task_id", sa.String(length=64), nullable=False),
            sa.Column("created_by_attempt_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["source_version_id"], ["source_versions.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    indexes = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes("learning_reports")
    }
    if "ux_learning_report_run_revision" not in indexes:
        op.create_index(
            "ux_learning_report_run_revision",
            "learning_reports",
            ["run_id", "revision_no"],
            unique=True,
        )
    if "ux_learning_report_task" not in indexes:
        op.create_index(
            "ux_learning_report_task",
            "learning_reports",
            ["created_by_task_id"],
            unique=True,
        )
    if "ix_learning_reports_source_version" not in indexes:
        op.create_index(
            "ix_learning_reports_source_version",
            "learning_reports",
            ["source_version_id"],
            unique=False,
        )


def downgrade() -> None:
    if "learning_reports" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("learning_reports")
