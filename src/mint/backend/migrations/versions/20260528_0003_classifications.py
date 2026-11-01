"""add classifications

Revision ID: 20260528_0003
Revises: 20260528_0002
Create Date: 2026-05-28
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260528_0003"
down_revision: str | None = "20260528_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "classifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("np_trigger", sa.Text()),
        sa.Column("confidence", sa.Integer()),
        sa.Column("reasoning", sa.Text()),
        sa.Column("key_signal", sa.Text()),
        sa.Column("key_risk", sa.Text()),
        sa.Column("flags", postgresql.ARRAY(sa.Text())),
        sa.Column("review_status", sa.Text()),
        sa.Column("reviewed_by", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reclassified_to", sa.Text()),
        sa.Column("pipeline_version", sa.Text()),
        sa.Column("classified_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_classifications_person_id", "classifications", ["person_id"])
    op.create_index("ix_classifications_company_id", "classifications", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_classifications_company_id", table_name="classifications")
    op.drop_index("ix_classifications_person_id", table_name="classifications")
    op.drop_table("classifications")
