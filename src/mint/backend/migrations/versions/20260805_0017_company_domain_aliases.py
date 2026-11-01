"""Remember human-confirmed company domain aliases.

Revision ID: 20260805_0017
Revises: 20260728_0016
"""

from alembic import op
import sqlalchemy as sa


revision = "20260805_0017"
down_revision = "20260728_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_domain_aliases",
        sa.Column("domain", sa.Text(), primary_key=True),
        sa.Column("canonical_domain", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="human_review"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_company_domain_aliases_canonical_domain", "company_domain_aliases", ["canonical_domain"])


def downgrade() -> None:
    op.drop_index("ix_company_domain_aliases_canonical_domain", table_name="company_domain_aliases")
    op.drop_table("company_domain_aliases")
