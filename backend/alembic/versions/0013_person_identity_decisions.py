"""Add reversible user decisions for person identity candidates.

Revision ID: 0013_person_identity_decisions
Revises: 0012_analysis_digests
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_person_identity_decisions"
down_revision: Union[str, None] = "0012_analysis_digests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "person_identity_decisions" not in inspector.get_table_names():
        op.create_table(
            "person_identity_decisions",
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("pair_key", sa.String(length=64), nullable=False),
            sa.Column("left_name", sa.String(length=240), nullable=False),
            sa.Column("right_name", sa.String(length=240), nullable=False),
            sa.Column("canonical_name", sa.String(length=240), nullable=True),
            sa.Column("decision", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["run_id"],
                ["analysis_runs.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    indexes = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes(
            "person_identity_decisions"
        )
    }
    if "ux_person_identity_decision_run_pair" not in indexes:
        op.create_index(
            "ux_person_identity_decision_run_pair",
            "person_identity_decisions",
            ["run_id", "pair_key"],
            unique=True,
        )
    if "ix_person_identity_decisions_run_created" not in indexes:
        op.create_index(
            "ix_person_identity_decisions_run_created",
            "person_identity_decisions",
            ["run_id", "created_at"],
            unique=False,
        )
    if "ix_person_identity_decisions_run_id" not in indexes:
        op.create_index(
            "ix_person_identity_decisions_run_id",
            "person_identity_decisions",
            ["run_id"],
            unique=False,
        )


def downgrade() -> None:
    if "person_identity_decisions" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("person_identity_decisions")
