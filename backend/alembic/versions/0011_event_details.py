"""Add structured event details without rewriting existing candidates.

Revision ID: 0011_event_details
Revises: 0010_task_attempt_diagnostics
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_event_details"
down_revision: Union[str, None] = "0010_task_attempt_diagnostics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns("event_candidates")
    }
    if "details_json" not in columns:
        op.add_column(
            "event_candidates",
            sa.Column(
                "details_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            ),
        )


def downgrade() -> None:
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns("event_candidates")
    }
    if "details_json" in columns:
        op.drop_column("event_candidates", "details_json")
