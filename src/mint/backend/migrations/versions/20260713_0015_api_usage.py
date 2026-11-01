"""attribute provider usage to events

Revision ID: 20260713_0015
Revises: 20260713_0014
Create Date: 2026-07-13
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260713_0015"
down_revision: str | None = "20260713_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("enrichment_logs", sa.Column("event_id", postgresql.UUID(as_uuid=True)))
    op.add_column("enrichment_logs", sa.Column("operation", sa.Text()))
    op.add_column("enrichment_logs", sa.Column("requests", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("enrichment_logs", sa.Column("credits", sa.Numeric(12, 3), nullable=False, server_default="0"))
    op.add_column(
        "enrichment_logs", sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0")
    )
    op.add_column("enrichment_logs", sa.Column("pricing_basis", sa.Text()))
    op.create_foreign_key(
        "fk_enrichment_logs_event_id_events", "enrichment_logs", "events", ["event_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_enrichment_logs_event_id", "enrichment_logs", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_enrichment_logs_event_id", table_name="enrichment_logs")
    op.drop_constraint("fk_enrichment_logs_event_id_events", "enrichment_logs", type_="foreignkey")
    op.drop_column("enrichment_logs", "pricing_basis")
    op.drop_column("enrichment_logs", "estimated_cost_usd")
    op.drop_column("enrichment_logs", "credits")
    op.drop_column("enrichment_logs", "requests")
    op.drop_column("enrichment_logs", "operation")
    op.drop_column("enrichment_logs", "event_id")
