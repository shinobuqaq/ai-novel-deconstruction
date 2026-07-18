"""Add source import, chapter, issue and evidence tables.

Revision ID: 0004_source_ingest
Revises: 0003_artifact_blobs
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_source_ingest"
down_revision: Union[str, None] = "0003_artifact_blobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_table(name: str, columns: list[sa.Column]) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if name not in inspector.get_table_names():
        op.create_table(name, *columns)
        return
    existing = {column["name"] for column in inspector.get_columns(name)}
    expected = {column.name for column in columns}
    if not expected.issubset(existing):
        raise RuntimeError(f"INCOMPATIBLE_PARTIAL_{name.upper()}_SCHEMA:{sorted(existing)}")


def _ensure_indexes(
    table: str,
    indexes: list[tuple[str, list[str], bool]],
) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    for name, columns, unique in indexes:
        if name not in existing:
            op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    _ensure_table("source_documents", [
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("project_id", sa.String(length=64), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("source_format", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ])
    _ensure_indexes("source_documents", [
        ("ix_source_documents_project_id", ["project_id"], False),
        ("ix_source_documents_project_created", ["project_id", "created_at"], False),
    ])

    _ensure_table("source_versions", [
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("document_id", sa.String(length=64), sa.ForeignKey("source_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("original_relative_path", sa.Text(), nullable=False),
        sa.Column("text_relative_path", sa.Text(), nullable=False),
        sa.Column("total_chars", sa.Integer(), nullable=False),
        sa.Column("chapter_count", sa.Integer(), nullable=False),
        sa.Column("detected_encoding", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    ])
    _ensure_indexes("source_versions", [
        ("ix_source_versions_document_id", ["document_id"], False),
        ("ux_source_version_no", ["document_id", "version_no"], True),
        ("ux_source_version_hash", ["document_id", "content_hash"], True),
        ("ix_source_versions_status_created", ["status", "created_at"], False),
    ])

    _ensure_table("source_units", [
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("source_version_id", sa.String(length=64), sa.ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("unit_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ])
    _ensure_indexes("source_units", [
        ("ix_source_units_source_version_id", ["source_version_id"], False),
        ("ux_source_unit_ordinal", ["source_version_id", "ordinal"], True),
        ("ix_source_units_version_range", ["source_version_id", "start_char", "end_char"], False),
    ])

    _ensure_table("source_issues", [
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("source_version_id", sa.String(length=64), sa.ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_unit_id", sa.String(length=64), sa.ForeignKey("source_units.id", ondelete="SET NULL"), nullable=True),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    ])
    _ensure_indexes("source_issues", [
        ("ix_source_issues_source_version_id", ["source_version_id"], False),
        ("ix_source_issues_version_status", ["source_version_id", "status", "severity"], False),
    ])

    _ensure_table("evidence_spans", [
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("source_version_id", sa.String(length=64), sa.ForeignKey("source_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_unit_id", sa.String(length=64), sa.ForeignKey("source_units.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paragraph_index", sa.Integer(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("text_snapshot", sa.Text(), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
    ])
    _ensure_indexes("evidence_spans", [
        ("ix_evidence_spans_source_version_id", ["source_version_id"], False),
        ("ix_evidence_spans_source_unit_id", ["source_unit_id"], False),
        ("ux_evidence_span_range", ["source_version_id", "start_char", "end_char"], True),
        ("ix_evidence_spans_unit_paragraph", ["source_unit_id", "paragraph_index"], False),
    ])


def downgrade() -> None:
    op.drop_index("ix_evidence_spans_unit_paragraph", table_name="evidence_spans")
    op.drop_index("ux_evidence_span_range", table_name="evidence_spans")
    op.drop_index("ix_evidence_spans_source_unit_id", table_name="evidence_spans")
    op.drop_index("ix_evidence_spans_source_version_id", table_name="evidence_spans")
    op.drop_table("evidence_spans")
    op.drop_index("ix_source_issues_version_status", table_name="source_issues")
    op.drop_index("ix_source_issues_source_version_id", table_name="source_issues")
    op.drop_table("source_issues")
    op.drop_index("ix_source_units_version_range", table_name="source_units")
    op.drop_index("ux_source_unit_ordinal", table_name="source_units")
    op.drop_index("ix_source_units_source_version_id", table_name="source_units")
    op.drop_table("source_units")
    op.drop_index("ix_source_versions_status_created", table_name="source_versions")
    op.drop_index("ux_source_version_hash", table_name="source_versions")
    op.drop_index("ux_source_version_no", table_name="source_versions")
    op.drop_index("ix_source_versions_document_id", table_name="source_versions")
    op.drop_table("source_versions")
    op.drop_index("ix_source_documents_project_created", table_name="source_documents")
    op.drop_index("ix_source_documents_project_id", table_name="source_documents")
    op.drop_table("source_documents")
