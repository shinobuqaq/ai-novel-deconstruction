"""Add durable source-linked summaries for long-book analysis.

Revision ID: 0012_analysis_digests
Revises: 0011_event_details
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_analysis_digests"
down_revision: Union[str, None] = "0011_event_details"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "analysis_digests" in inspector.get_table_names():
        return
    op.create_table(
        "analysis_digests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("source_version_id", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("start_chapter", sa.Integer(), nullable=False),
        sa.Column("end_chapter", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("source_digest_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_event_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_unit_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
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
    op.create_index(
        "ux_analysis_digest_run_level_sequence",
        "analysis_digests",
        ["run_id", "level", "sequence_no"],
        unique=True,
    )
    op.create_index(
        "ux_analysis_digest_task",
        "analysis_digests",
        ["created_by_task_id"],
        unique=True,
    )
    op.create_index(
        "ix_analysis_digests_source_version",
        "analysis_digests",
        ["source_version_id"],
        unique=False,
    )


def downgrade() -> None:
    if "analysis_digests" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("analysis_digests")
