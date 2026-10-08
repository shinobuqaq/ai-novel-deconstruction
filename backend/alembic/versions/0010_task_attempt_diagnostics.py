"""Add non-sensitive diagnostics for every task attempt.

Revision ID: 0010_task_attempt_diagnostics
Revises: 0009_analysis_issues
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_task_attempt_diagnostics"
down_revision: Union[str, None] = "0009_analysis_issues"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        item["name"] for item in inspector.get_columns("task_attempts")
    }
    if "diagnostics_json" not in columns:
        op.add_column(
            "task_attempts",
            sa.Column(
                "diagnostics_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            ),
        )


def downgrade() -> None:
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns("task_attempts")
    }
    if "diagnostics_json" in columns:
        op.drop_column("task_attempts", "diagnostics_json")
