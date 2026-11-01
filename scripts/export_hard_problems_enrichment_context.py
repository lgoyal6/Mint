import asyncio
import csv
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mint.backend.settings import Settings


EVENT_ID = "evt-rxLBfzlbFsiHJy0"
OUT = Path.home() / "Downloads" / "mint_hard_problems_enrichment_context.csv"
SUMMARY = Path("outputs/luma_evt-rxLBfzlbFsiHJy0_enrichment_context_summary.json")

SQL = text(
    """
    SELECT
        r.email,
        concat_ws(' ', p.first_name, p.last_name) AS name,
        p.first_name,
        p.last_name,
        p.self_reported_company,
        p.self_reported_title,
        p.self_reported_building,
        r.survey_responses,
        r.approval_status,
        r.checked_in_at,

        p.resolution_confidence,
        p.affiliation_state,
        p.resolution_evidence ->> 'person_state' AS person_state,
        p.resolution_evidence ->> 'company_state' AS company_state,
        p.resolution_evidence,
        p.candidate_companies,

        p.apollo_match_status,
        p.apollo_person_title,
        p.apollo_person_seniority,
        p.apollo_person_departments,
        p.apollo_person_linkedin_url,
        p.apollo_person_location,
        p.apollo_person_data,

        p.exa_person_role_signals,
        p.exa_people_search_results,

        c.domain AS company_domain,
        c.name AS company_name,
        c.apollo_org_industry,
        c.apollo_org_employee_count,
        c.apollo_org_founded_year,
        c.apollo_org_funding_stage,
        c.apollo_org_total_funding,
        c.apollo_org_revenue,
        c.apollo_org_technologies,
        c.apollo_org_headcount_growth_6mo,
        c.apollo_org_headcount_growth_12mo,
        c.apollo_org_data,

        c.exa_what_they_do,
        c.exa_target_vertical,
        c.exa_stage,
        c.exa_data_quality,
        c.exa_enrichment,
        c.exa_deep_raw,
        c.exa_grounding,

        p.enrichment_data_quality,
        p.apollo_enriched_at AS person_apollo_enriched_at,
        p.exa_enriched_at AS person_exa_enriched_at,
        c.apollo_enriched_at AS company_apollo_enriched_at,
        c.exa_enriched_at AS company_exa_enriched_at
    FROM events e
    JOIN registrations r ON r.event_id = e.id
    LEFT JOIN people p ON p.id = r.person_id
    LEFT JOIN companies c ON c.id = p.company_id
    WHERE e.luma_event_id = :event_id
      AND r.approval_status IN ('going', 'approved')
    ORDER BY
        CASE p.resolution_confidence
            WHEN 'high' THEN 1
            WHEN 'medium' THEN 2
            WHEN 'flagged' THEN 3
            WHEN 'low' THEN 4
            ELSE 5
        END,
        c.domain NULLS LAST,
        r.email
    """
)

FIELDS = [
    "email",
    "name",
    "first_name",
    "last_name",
    "self_reported_company",
    "self_reported_title",
    "self_reported_building",
    "survey_responses",
    "approval_status",
    "checked_in_at",
    "resolution_confidence",
    "affiliation_state",
    "person_state",
    "company_state",
    "resolution_evidence",
    "candidate_companies",
    "apollo_match_status",
    "apollo_person_title",
    "apollo_person_seniority",
    "apollo_person_departments",
    "apollo_person_linkedin_url",
    "apollo_person_location",
    "apollo_person_data",
    "exa_person_role_signals",
    "exa_people_search_results",
    "company_domain",
    "company_name",
    "apollo_org_industry",
    "apollo_org_employee_count",
    "apollo_org_founded_year",
    "apollo_org_funding_stage",
    "apollo_org_total_funding",
    "apollo_org_revenue",
    "apollo_org_technologies",
    "apollo_org_headcount_growth_6mo",
    "apollo_org_headcount_growth_12mo",
    "apollo_org_data",
    "exa_what_they_do",
    "exa_target_vertical",
    "exa_stage",
    "exa_data_quality",
    "exa_enrichment",
    "exa_deep_raw",
    "exa_grounding",
    "enrichment_data_quality",
    "person_apollo_enriched_at",
    "person_exa_enriched_at",
    "company_apollo_enriched_at",
    "company_exa_enriched_at",
]


def serialize(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


async def main() -> None:
    settings = Settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set")

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(SQL, {"event_id": EVENT_ID})).mappings().all()
    finally:
        await engine.dispose()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            record = dict(row)
            writer.writerow({field: serialize(record.get(field)) for field in FIELDS})

    summary = {
        "event_id": EVENT_ID,
        "rows": len(rows),
        "with_apollo_person_data": sum(1 for row in rows if row.get("apollo_person_data")),
        "with_apollo_org_data": sum(1 for row in rows if row.get("apollo_org_data")),
        "with_exa_enrichment": sum(1 for row in rows if row.get("exa_enrichment")),
        "with_exa_people_search_results": sum(1 for row in rows if row.get("exa_people_search_results")),
        "csv_path": str(OUT),
    }
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
