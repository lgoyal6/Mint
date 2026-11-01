"""add product fit to classifications

Revision ID: 20260529_0005
Revises: 20260529_0004
Create Date: 2026-05-29
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260529_0005"
down_revision: str | None = "20260529_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("classifications", sa.Column("product_fit", postgresql.JSONB()))
    op.add_column("classifications", sa.Column("primary_product", sa.Text()))


def downgrade() -> None:
    op.drop_column("classifications", "primary_product")
    op.drop_column("classifications", "product_fit")
