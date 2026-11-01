"""drop dead schema columns

Revision ID: 20260601_0006
Revises: 20260529_0005
Create Date: 2026-06-01
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260601_0006"
down_revision: str | None = "20260529_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("people", "has_console_account")
    op.drop_column("people", "console_checked_at")
    op.drop_column("people", "hubspot_contact_id")
    op.drop_column("people", "hubspot_synced_at")
    op.drop_column("enrichment_logs", "cost_dollars")
    op.drop_column("enrichment_logs", "changes_detected")


def downgrade() -> None:
    op.add_column("enrichment_logs", sa.Column("changes_detected", postgresql.JSONB()))
    op.add_column("enrichment_logs", sa.Column("cost_dollars", sa.Numeric()))
    op.add_column("people", sa.Column("hubspot_synced_at", sa.DateTime(timezone=True)))
    op.add_column("people", sa.Column("hubspot_contact_id", sa.Text()))
    op.add_column("people", sa.Column("console_checked_at", sa.DateTime(timezone=True)))
    op.add_column("people", sa.Column("has_console_account", sa.Boolean()))
