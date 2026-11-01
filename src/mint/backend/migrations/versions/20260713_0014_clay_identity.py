"""store Clay identity results

Revision ID: 20260713_0014
Revises: 20260616_0013
Create Date: 2026-07-13
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260713_0014"
down_revision: str | None = "20260616_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("people", sa.Column("clay_person_data", postgresql.JSONB()))
    op.add_column("people", sa.Column("clay_enriched_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("people", "clay_enriched_at")
    op.drop_column("people", "clay_person_data")
