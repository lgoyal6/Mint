"""add review_dismissed_at to people

Revision ID: 20260610_0011
Revises: 20260610_0010
Create Date: 2026-06-10
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260610_0011"
down_revision: str | None = "20260610_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("people", sa.Column("review_dismissed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("people", "review_dismissed_at")
