"""Add staged analysis runs and entity/event candidates.

Revision ID: 0005_analysis_candidates
Revises: 0004_source_ingest
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_analysis_candidates"
down_revision: Union[str, None] = "0004_source_ingest"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_table(name: str, columns: list[sa.Column]) -> None:
    inspector = sa.inspect(op.get_bind())
    if name not in inspector.get_table_names():
        op.create_table(name, *columns)
        return
    existing = {column["name"] for column in inspector.get_columns(name)}
    expected = {column.name for column in columns}
    if not expected.issubset(existing):
        raise RuntimeError(f"INCOMPATIBLE_PARTIAL_{name.upper()}_SCHEMA:{sorted(existing)}")


def _ensure_indexes(table: str, indexes: list[tuple[str, list[str], bool]]) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    for name, columns, unique in indexes:
        if name not in existing:
            op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    _ensure_table("analysis_runs", [
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("source_version_id", sa.String(length=64), sa.ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_batches", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    ])
    _ensure_indexes("analysis_runs", [
        ("ix_analysis_runs_source_version_id", ["source_version_id"], False),
        ("ix_analysis_runs_source_stage", ["source_version_id", "stage", "created_at"], False),
    ])

    _ensure_table("analysis_run_tasks", [
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("batch_index", sa.Integer(), nullable=False),
    ])
    _ensure_indexes("analysis_run_tasks", [
        ("ux_analysis_run_batch", ["run_id", "batch_index"], True),
    ])

    _ensure_table("entity_candidates", [
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_version_id", sa.String(length=64), sa.ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("normalized_name", sa.String(length=240), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("aliases_json", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("created_by_task_id", sa.String(length=64), nullable=False),
        sa.Column("created_by_attempt_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ])
    _ensure_indexes("entity_candidates", [
        ("ix_entity_candidates_run_id", ["run_id"], False),
        ("ix_entity_candidates_source_version_id", ["source_version_id"], False),
        ("ux_entity_candidate_run_name", ["run_id", "normalized_name"], True),
        ("ix_entity_candidates_source_status", ["source_version_id", "status"], False),
    ])

    _ensure_table("event_candidates", [
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_version_id", sa.String(length=64), sa.ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("identity_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("participants_json", sa.Text(), nullable=False),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("created_by_task_id", sa.String(length=64), nullable=False),
        sa.Column("created_by_attempt_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ])
    _ensure_indexes("event_candidates", [
        ("ix_event_candidates_run_id", ["run_id"], False),
        ("ix_event_candidates_source_version_id", ["source_version_id"], False),
        ("ux_event_candidate_run_identity", ["run_id", "identity_key"], True),
        ("ix_event_candidates_source_status", ["source_version_id", "status"], False),
    ])


def downgrade() -> None:
    op.drop_index("ix_event_candidates_source_status", table_name="event_candidates")
    op.drop_index("ux_event_candidate_run_identity", table_name="event_candidates")
    op.drop_index("ix_event_candidates_source_version_id", table_name="event_candidates")
    op.drop_index("ix_event_candidates_run_id", table_name="event_candidates")
    op.drop_table("event_candidates")
    op.drop_index("ix_entity_candidates_source_status", table_name="entity_candidates")
    op.drop_index("ux_entity_candidate_run_name", table_name="entity_candidates")
    op.drop_index("ix_entity_candidates_source_version_id", table_name="entity_candidates")
    op.drop_index("ix_entity_candidates_run_id", table_name="entity_candidates")
    op.drop_table("entity_candidates")
    op.drop_index("ux_analysis_run_batch", table_name="analysis_run_tasks")
    op.drop_table("analysis_run_tasks")
    op.drop_index("ix_analysis_runs_source_stage", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_source_version_id", table_name="analysis_runs")
    op.drop_table("analysis_runs")
