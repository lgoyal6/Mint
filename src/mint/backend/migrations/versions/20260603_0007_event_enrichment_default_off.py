"""default event enrichment to disabled

Revision ID: 20260603_0007
Revises: 20260601_0006
Create Date: 2026-06-03
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260603_0007"
down_revision: str | None = "20260601_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("events", "enrichment_enabled", server_default=sa.text("false"))


def downgrade() -> None:
    op.alter_column("events", "enrichment_enabled", server_default=sa.text("true"))
