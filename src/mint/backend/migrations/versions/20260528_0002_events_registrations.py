"""add events and registrations

Revision ID: 20260528_0002
Revises: 20260528_0001
Create Date: 2026-05-28
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260528_0002"
down_revision: str | None = "20260528_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("luma_event_id", sa.Text(), unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("event_date", sa.DateTime(timezone=True)),
        sa.Column("location", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("guest_count", sa.Integer(), server_default=sa.text("0")),
        sa.Column("enrichment_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "registrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id")),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("approval_status", sa.Text()),
        sa.Column("checked_in_at", sa.DateTime(timezone=True)),
        sa.Column("survey_responses", postgresql.JSONB()),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", "email", name="uq_registrations_event_email"),
    )
    op.create_index("ix_registrations_person_id", "registrations", ["person_id"])
    op.create_index("ix_registrations_event_id", "registrations", ["event_id"])
    op.create_index("ix_registrations_email", "registrations", ["email"])


def downgrade() -> None:
    op.drop_index("ix_registrations_email", table_name="registrations")
    op.drop_index("ix_registrations_event_id", table_name="registrations")
    op.drop_index("ix_registrations_person_id", table_name="registrations")
    op.drop_table("registrations")
    op.drop_table("events")
