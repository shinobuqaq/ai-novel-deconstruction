"""Add the evidence-backed deep analysis stage.

Revision ID: 0008_deep_analysis
Revises: 0007_narrative_synthesis
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_deep_analysis"
down_revision: Union[str, None] = "0007_narrative_synthesis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "deep_analyses" not in inspector.get_table_names():
        op.create_table(
            "deep_analyses",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "run_id",
                sa.String(length=64),
                sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "source_version_id",
                sa.String(length=64),
                sa.ForeignKey("source_versions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("revision_no", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("prompt_id", sa.String(length=80), nullable=False),
            sa.Column("prompt_version", sa.String(length=40), nullable=False),
            sa.Column("created_by_task_id", sa.String(length=64), nullable=False),
            sa.Column("created_by_attempt_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    else:
        columns = {item["name"] for item in inspector.get_columns("deep_analyses")}
        if "revision_no" not in columns:
            op.add_column(
                "deep_analyses",
                sa.Column("revision_no", sa.Integer(), nullable=False, server_default="1"),
            )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("deep_analyses")}
    if "ux_deep_analysis_run" in indexes:
        op.drop_index("ux_deep_analysis_run", table_name="deep_analyses")
        indexes.remove("ux_deep_analysis_run")
    if "ux_deep_analysis_run_revision" not in indexes:
        op.create_index(
            "ux_deep_analysis_run_revision",
            "deep_analyses",
            ["run_id", "revision_no"],
            unique=True,
        )
    if "ux_deep_analysis_task" not in indexes:
        op.create_index("ux_deep_analysis_task", "deep_analyses", ["created_by_task_id"], unique=True)
    if "ix_deep_analyses_source_version" not in indexes:
        op.create_index(
            "ix_deep_analyses_source_version",
            "deep_analyses",
            ["source_version_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_deep_analyses_source_version", table_name="deep_analyses")
    op.drop_index("ux_deep_analysis_task", table_name="deep_analyses")
    op.drop_index("ux_deep_analysis_run_revision", table_name="deep_analyses")
    op.drop_table("deep_analyses")
