"""index review_status and current_classification_id

Revision ID: 20260610_0010
Revises: 20260610_0009
Create Date: 2026-06-10
"""

from typing import Sequence

from alembic import op


revision: str = "20260610_0010"
down_revision: str | None = "20260610_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_classifications_review_status", "classifications", ["review_status"])
    op.create_index("ix_people_current_classification_id", "people", ["current_classification_id"])


def downgrade() -> None:
    op.drop_index("ix_people_current_classification_id", table_name="people")
    op.drop_index("ix_classifications_review_status", table_name="classifications")
