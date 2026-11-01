"""Repair company-stage survey answers stored as employer names.

Revision ID: 20260811_0018
Revises: 20260805_0017
"""

from alembic import op


revision = "20260811_0018"
down_revision = "20260805_0017"
branch_labels = None
depends_on = None


COMPANY_STAGE_QUESTION = "Which best describes your company today?"
CORRUPTED_COMPANY_VALUES = (
    "Generating revenue, bootstrapped",
    "Generating revenue, venture-backed",
    "Raised (pre-seed/seed/A+), pre-revenue",
    "None of the above",
)


def upgrade() -> None:
    values = ", ".join(_sql_literal(value) for value in CORRUPTED_COMPANY_VALUES)
    question = _sql_literal(COMPANY_STAGE_QUESTION)

    # Preserve the original answer as survey context, then remove it from the
    # private ingestion context where it was incorrectly labeled company_name.
    op.execute(
        f"""
        UPDATE registrations
        SET survey_responses =
            jsonb_set(
                survey_responses,
                ARRAY[{question}],
                to_jsonb(survey_responses #>> '{{_mint_lead_context,company_name}}'),
                true
            ) #- '{{_mint_lead_context,company_name}}'
        WHERE survey_responses #>> '{{_mint_lead_context,company_name}}' IN ({values})
        """
    )

    # Exact-value matching keeps the repair limited to the survey choices that
    # the broken importer could have written. Human-confirmed resolution data,
    # company links, and candidate evidence remain untouched.
    op.execute(
        f"""
        UPDATE people
        SET self_reported_company = NULL,
            updated_at = now()
        WHERE self_reported_company IN ({values})
        """
    )


def downgrade() -> None:
    # This is a corrective data migration. Restoring known-invalid employer
    # names would reintroduce the bug, so schema downgrade intentionally keeps
    # the repaired data.
    pass


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
