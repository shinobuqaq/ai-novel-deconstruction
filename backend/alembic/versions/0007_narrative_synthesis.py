"""Add evidence-backed narrative synthesis records.

Revision ID: 0007_narrative_synthesis
Revises: 0006_source_parser_version
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_narrative_synthesis"
down_revision: Union[str, None] = "0006_source_parser_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "narrative_syntheses" not in inspector.get_table_names():
        op.create_table(
            "narrative_syntheses",
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
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("prompt_id", sa.String(length=80), nullable=False),
            sa.Column("prompt_version", sa.String(length=40), nullable=False),
            sa.Column("created_by_task_id", sa.String(length=64), nullable=False),
            sa.Column("created_by_attempt_id", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("narrative_syntheses")}
    if "ux_narrative_synthesis_run" not in indexes:
        op.create_index(
            "ux_narrative_synthesis_run",
            "narrative_syntheses",
            ["run_id"],
            unique=True,
        )
    if "ix_narrative_syntheses_source_version" not in indexes:
        op.create_index(
            "ix_narrative_syntheses_source_version",
            "narrative_syntheses",
            ["source_version_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_narrative_syntheses_source_version",
        table_name="narrative_syntheses",
    )
    op.drop_index("ux_narrative_synthesis_run", table_name="narrative_syntheses")
    op.drop_table("narrative_syntheses")
