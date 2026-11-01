import asyncio
import csv
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mint.backend.settings import Settings


EVENT_ID = "evt-rUVMKfmk2tDF1Bz"
OUT = Path.home() / "Downloads" / "mint_evt-rUVMKfmk2tDF1Bz_classification_results.csv"
SUMMARY = Path("outputs/luma_evt-rUVMKfmk2tDF1Bz_export_summary.json")

SQL = text(
    """
    SELECT
        r.email,
        concat_ws(' ', p.first_name, p.last_name) AS name,
        coalesce(p.apollo_person_title, p.self_reported_title) AS title,
        p.self_reported_company AS raw_company_claim,
        cpy.name AS resolved_company,
        cpy.domain AS resolved_domain,
        p.resolution_confidence,
        p.affiliation_state,
        p.resolution_evidence ->> 'person_state' AS person_state,
        p.resolution_evidence ->> 'company_state' AS company_state,
        p.enrichment_data_quality,
        c.tier,
        c.product_fit,
        c.primary_product,
        c.np_trigger,
        c.confidence AS classification_confidence,
        c.review_status,
        c.flags,
        c.key_signal,
        c.key_risk,
        c.reasoning,
        cpy.exa_target_vertical,
        cpy.exa_stage,
        cpy.exa_data_quality,
        cpy.exa_what_they_do,
        cpy.apollo_org_employee_count,
        cpy.apollo_org_funding_stage,
        cpy.apollo_org_revenue,
        p.exa_person_role_signals,
        p.self_reported_building,
        p.resolution_evidence,
        p.candidate_companies,
        r.approval_status,
        r.checked_in_at,
        r.survey_responses,
        p.apollo_person_linkedin_url,
        p.apollo_match_status
    FROM events e
    JOIN registrations r ON r.event_id = e.id
    LEFT JOIN people p ON p.id = r.person_id
    LEFT JOIN companies cpy ON cpy.id = p.company_id
    LEFT JOIN classifications c ON c.id = p.current_classification_id
    WHERE e.luma_event_id = :event_id
      AND r.approval_status IN ('going', 'approved')
    ORDER BY
        CASE c.tier WHEN 'P0' THEN 1 WHEN 'P1' THEN 2 WHEN 'NP' THEN 3 ELSE 4 END,
        c.confidence DESC NULLS LAST,
        p.resolution_confidence,
        r.email
    """
)

FIELDS = [
    "classification_status",
    "tier",
    "primary_product",
    "product_fit",
    "np_trigger",
    "classification_confidence",
    "review_status",
    "flags",
    "key_signal",
    "key_risk",
    "email",
    "name",
    "title",
    "raw_company_claim",
    "resolved_company",
    "resolved_domain",
    "resolution_confidence",
    "affiliation_state",
    "person_state",
    "company_state",
    "enrichment_data_quality",
    "exa_target_vertical",
    "exa_stage",
    "exa_data_quality",
    "apollo_org_employee_count",
    "apollo_org_funding_stage",
    "apollo_org_revenue",
    "self_reported_building",
    "exa_person_role_signals",
    "exa_what_they_do",
    "reasoning",
    "resolution_evidence",
    "candidate_companies",
    "apollo_person_linkedin_url",
    "apollo_match_status",
    "approval_status",
    "checked_in_at",
    "survey_responses",
]


def serialize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def classification_status(row: dict[str, Any]) -> str:
    if row.get("tier"):
        return "classified"
    if not row.get("resolution_confidence"):
        return "not_enriched"
    if row.get("resolution_confidence") not in {"high", "medium"}:
        return "not_classified_resolution_gate"
    if not row.get("resolved_domain"):
        return "not_classified_no_trusted_company"
    return "not_classified"


async def main() -> None:
    settings = Settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set")

    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        rows = (await conn.execute(SQL, {"event_id": EVENT_ID})).mappings().all()
    await engine.dispose()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            record = dict(row)
            record["classification_status"] = classification_status(record)
            writer.writerow({field: serialize(record.get(field)) for field in FIELDS})

    counts: dict[str, int] = {}
    for row in rows:
        status = classification_status(dict(row))
        tier = row.get("tier") or "unclassified"
        counts[status] = counts.get(status, 0) + 1
        counts[f"tier:{tier}"] = counts.get(f"tier:{tier}", 0) + 1

    summary = {
        "event_id": EVENT_ID,
        "rows": len(rows),
        "counts": counts,
        "csv_path": str(OUT),
    }
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
