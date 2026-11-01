"""initial enrichment persistence tables

Revision ID: 20260528_0001
Revises:
Create Date: 2026-05-28
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260528_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("apollo_org_data", postgresql.JSONB()),
        sa.Column("apollo_org_industry", sa.Text()),
        sa.Column("apollo_org_employee_count", sa.Integer()),
        sa.Column("apollo_org_founded_year", sa.Integer()),
        sa.Column("apollo_org_funding_stage", sa.Text()),
        sa.Column("apollo_org_total_funding", sa.Text()),
        sa.Column("apollo_org_revenue", sa.Text()),
        sa.Column("apollo_org_technologies", postgresql.ARRAY(sa.Text())),
        sa.Column("apollo_org_headcount_growth_6mo", sa.Numeric()),
        sa.Column("apollo_org_headcount_growth_12mo", sa.Numeric()),
        sa.Column("exa_enrichment", postgresql.JSONB()),
        sa.Column("exa_what_they_do", sa.Text()),
        sa.Column("exa_target_vertical", sa.Text()),
        sa.Column("exa_stage", sa.Text()),
        sa.Column("exa_data_quality", sa.Text()),
        sa.Column("exa_deep_raw", postgresql.JSONB()),
        sa.Column("exa_grounding", postgresql.JSONB()),
        sa.Column("apollo_enriched_at", sa.DateTime(timezone=True)),
        sa.Column("exa_enriched_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("domain"),
    )
    op.create_index("ix_companies_domain", "companies", ["domain"])

    op.create_table(
        "people",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("first_name", sa.Text()),
        sa.Column("last_name", sa.Text()),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("companies.id")),
        sa.Column("person_state", sa.Text()),
        sa.Column("company_state", sa.Text()),
        sa.Column("affiliation_state", sa.Text()),
        sa.Column("resolution_confidence", sa.Text()),
        sa.Column("resolution_evidence", postgresql.JSONB()),
        sa.Column("candidate_companies", postgresql.JSONB()),
        sa.Column("apollo_person_data", postgresql.JSONB()),
        sa.Column("apollo_match_status", sa.Text()),
        sa.Column("apollo_person_title", sa.Text()),
        sa.Column("apollo_person_seniority", sa.Text()),
        sa.Column("apollo_person_departments", postgresql.ARRAY(sa.Text())),
        sa.Column("apollo_person_linkedin_url", sa.Text()),
        sa.Column("apollo_person_location", sa.Text()),
        sa.Column("exa_person_role_signals", sa.Text()),
        sa.Column("exa_people_search_results", postgresql.JSONB()),
        sa.Column("self_reported_title", sa.Text()),
        sa.Column("self_reported_company", sa.Text()),
        sa.Column("self_reported_building", sa.Text()),
        sa.Column("apollo_enriched_at", sa.DateTime(timezone=True)),
        sa.Column("exa_enriched_at", sa.DateTime(timezone=True)),
        sa.Column("enrichment_data_quality", sa.Text()),
        sa.Column("current_tier", sa.Text()),
        sa.Column("current_confidence", sa.Integer()),
        sa.Column("current_classification_id", postgresql.UUID(as_uuid=True)),
        sa.Column("has_console_account", sa.Boolean()),
        sa.Column("console_checked_at", sa.DateTime(timezone=True)),
        sa.Column("hubspot_contact_id", sa.Text()),
        sa.Column("hubspot_synced_at", sa.DateTime(timezone=True)),
        sa.Column("events_registered", sa.Integer(), server_default=sa.text("0")),
        sa.Column("events_attended", sa.Integer(), server_default=sa.text("0")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_people_email", "people", ["email"])
    op.create_index("ix_people_company_id", "people", ["company_id"])

    op.create_table(
        "enrichment_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text()),
        sa.Column("cost_dollars", sa.Numeric()),
        sa.Column("request_id", sa.Text()),
        sa.Column("changes_detected", postgresql.JSONB()),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_enrichment_logs_entity_id", "enrichment_logs", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_enrichment_logs_entity_id", table_name="enrichment_logs")
    op.drop_table("enrichment_logs")
    op.drop_index("ix_people_company_id", table_name="people")
    op.drop_index("ix_people_email", table_name="people")
    op.drop_table("people")
    op.drop_index("ix_companies_domain", table_name="companies")
    op.drop_table("companies")
