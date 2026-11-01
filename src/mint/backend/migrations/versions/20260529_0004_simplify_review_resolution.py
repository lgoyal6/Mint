"""simplify review status and resolution state columns

Revision ID: 20260529_0004
Revises: 20260528_0003
Create Date: 2026-05-29
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260529_0004"
down_revision: str | None = "20260528_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE classifications
        SET review_status = 'CLASSIFIED'
        WHERE review_status IS NULL OR review_status <> 'CLASSIFIED'
        """
    )
    op.execute(
        """
        UPDATE people
        SET resolution_confidence = 'low'
        WHERE resolution_confidence = 'unresolved'
        """
    )
    op.execute(
        """
        UPDATE people
        SET resolution_evidence = jsonb_build_object(
            'person_state', person_state,
            'company_state', company_state,
            'chain',
            COALESCE(
                CASE
                    WHEN jsonb_typeof(resolution_evidence) = 'array' THEN resolution_evidence
                    WHEN jsonb_typeof(resolution_evidence) = 'object' THEN resolution_evidence -> 'chain'
                    ELSE NULL
                END,
                '[]'::jsonb
            )
        )
        """
    )
    op.drop_column("people", "person_state")
    op.drop_column("people", "company_state")


def downgrade() -> None:
    op.add_column("people", sa.Column("person_state", sa.Text()))
    op.add_column("people", sa.Column("company_state", sa.Text()))
    op.execute(
        """
        UPDATE people
        SET
            person_state = resolution_evidence ->> 'person_state',
            company_state = resolution_evidence ->> 'company_state'
        WHERE jsonb_typeof(resolution_evidence) = 'object'
        """
    )
