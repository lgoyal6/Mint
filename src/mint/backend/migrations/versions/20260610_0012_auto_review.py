"""add auto-review bookkeeping columns

Revision ID: 20260610_0012
Revises: 20260610_0011
Create Date: 2026-06-10
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260610_0012"
down_revision: str | None = "20260610_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("classifications", sa.Column("auto_review_status", sa.Text(), nullable=True))
    op.add_column("classifications", sa.Column("auto_reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("classifications", sa.Column("auto_review_note", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("auto_resolve_attempted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("people", sa.Column("auto_resolve_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("people", "auto_resolve_note")
    op.drop_column("people", "auto_resolve_attempted_at")
    op.drop_column("classifications", "auto_review_note")
    op.drop_column("classifications", "auto_reviewed_at")
    op.drop_column("classifications", "auto_review_status")
