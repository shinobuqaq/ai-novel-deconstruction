"""Add versioned evidence ledgers for individual North Star questions.

Revision ID: 0015_learning_question_evidence
Revises: 0014_learning_reports
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_learning_question_evidence"
down_revision = "0014_learning_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "learning_question_evidence" not in inspector.get_table_names():
        op.create_table(
            "learning_question_evidence",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("source_version_id", sa.String(length=64), nullable=False),
            sa.Column("question_id", sa.String(length=20), nullable=False),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
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
        for item in sa.inspect(op.get_bind()).get_indexes(
            "learning_question_evidence"
        )
    }
    if "ux_learning_question_evidence_run_question_revision" not in indexes:
        op.create_index(
            "ux_learning_question_evidence_run_question_revision",
            "learning_question_evidence",
            ["run_id", "question_id", "revision_no"],
            unique=True,
        )
    if "ux_learning_question_evidence_task" not in indexes:
        op.create_index(
            "ux_learning_question_evidence_task",
            "learning_question_evidence",
            ["created_by_task_id"],
            unique=True,
        )
    if "ix_learning_question_evidence_source_version" not in indexes:
        op.create_index(
            "ix_learning_question_evidence_source_version",
            "learning_question_evidence",
            ["source_version_id"],
            unique=False,
        )


def downgrade() -> None:
    if "learning_question_evidence" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("learning_question_evidence")
