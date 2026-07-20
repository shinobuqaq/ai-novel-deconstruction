"""Add user-facing analysis issues.

Revision ID: 0009_analysis_issues
Revises: 0008_deep_analysis
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_analysis_issues"
down_revision: Union[str, None] = "0008_deep_analysis"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "analysis_issues" not in inspector.get_table_names():
        op.create_table(
            "analysis_issues",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column(
                "run_id",
                sa.String(length=64),
                sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("target_kind", sa.String(length=40), nullable=False),
            sa.Column("target_id", sa.String(length=64), nullable=True),
            sa.Column("target_label", sa.String(length=300), nullable=False),
            sa.Column("category", sa.String(length=40), nullable=False),
            sa.Column("note", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("analysis_issues")}
    if "ix_analysis_issues_run_id" not in indexes:
        op.create_index("ix_analysis_issues_run_id", "analysis_issues", ["run_id"], unique=False)
    if "ix_analysis_issues_run_status" not in indexes:
        op.create_index(
            "ix_analysis_issues_run_status",
            "analysis_issues",
            ["run_id", "status", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_analysis_issues_run_status", table_name="analysis_issues")
    op.drop_index("ix_analysis_issues_run_id", table_name="analysis_issues")
    op.drop_table("analysis_issues")
