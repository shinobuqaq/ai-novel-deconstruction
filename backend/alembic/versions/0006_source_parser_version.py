"""Version source parsing so unchanged files can be parsed again.

Revision ID: 0006_source_parser_version
Revises: 0005_analysis_candidates
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_source_parser_version"
down_revision: Union[str, None] = "0005_analysis_candidates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("source_versions")}
    if "parser_version" not in columns:
        op.add_column(
            "source_versions",
            sa.Column(
                "parser_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )

    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("source_versions")}
    if "ux_source_version_hash" in indexes:
        op.drop_index("ux_source_version_hash", table_name="source_versions")
    if "ux_source_version_hash_parser" not in indexes:
        op.create_index(
            "ux_source_version_hash_parser",
            "source_versions",
            ["document_id", "content_hash", "parser_version"],
            unique=True,
        )


def downgrade() -> None:
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("source_versions")}
    if "ux_source_version_hash_parser" in indexes:
        op.drop_index("ux_source_version_hash_parser", table_name="source_versions")
    if "ux_source_version_hash" not in indexes:
        op.create_index(
            "ux_source_version_hash",
            "source_versions",
            ["document_id", "content_hash"],
            unique=True,
        )
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("source_versions")}
    if "parser_version" in columns:
        op.drop_column("source_versions", "parser_version")
