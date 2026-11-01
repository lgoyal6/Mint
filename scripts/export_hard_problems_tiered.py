import asyncio
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mint.backend.settings import Settings


EVENT_ID = "evt-rxLBfzlbFsiHJy0"
OUT_CSV = Path.home() / "Downloads" / "mint_hard_problems_tiered_results.csv"
OUT_XLSX = Path.home() / "Downloads" / "mint_hard_problems_tiered_results.xlsx"
SUMMARY = Path("outputs/luma_evt-rxLBfzlbFsiHJy0_export_summary.json")

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
        p.enrichment_data_quality,
        p.apollo_person_linkedin_url,
        p.apollo_match_status,
        r.approval_status,
        r.checked_in_at,
        r.survey_responses
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
        CASE p.resolution_confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'flagged' THEN 3 WHEN 'low' THEN 4 ELSE 5 END,
        cpy.exa_target_vertical NULLS LAST,
        r.email
    """
)

FIELDS = [
    "tier_index",
    "tier_bucket",
    "tier",
    "primary_product",
    "product_fit",
    "classification_confidence",
    "review_status",
    "key_signal",
    "key_risk",
    "name",
    "email",
    "title",
    "resolved_company",
    "resolved_domain",
    "resolution_confidence",
    "affiliation_state",
    "target_vertical",
    "stage",
    "np_trigger",
    "flags",
    "employee_count",
    "funding_stage",
    "revenue",
    "person_role_signals",
    "what_they_do",
    "raw_company_claim",
    "building_context",
    "enrichment_data_quality",
    "exa_data_quality",
    "person_state",
    "company_state",
    "apollo_match_status",
    "linkedin_url",
    "classification_reasoning",
    "resolution_evidence",
    "candidate_companies",
    "survey_responses",
]


def serialize(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def tier_bucket(row: dict[str, Any]) -> str:
    tier = row.get("tier")
    if tier:
        return str(tier)
    confidence = row.get("resolution_confidence") or "not_enriched"
    if confidence == "flagged":
        return "SKIPPED_FLAGGED"
    if confidence in {"low", "unresolved"}:
        return "SKIPPED_LOW_CONFIDENCE"
    return "SKIPPED_UNCLASSIFIED"


def clean_record(row: dict[str, Any], tier_counts: dict[str, int]) -> dict[str, str]:
    bucket = tier_bucket(row)
    tier_counts[bucket] += 1
    return {
        "tier_index": f"{bucket}-{tier_counts[bucket]:03d}",
        "tier_bucket": bucket,
        "tier": serialize(row.get("tier")),
        "primary_product": serialize(row.get("primary_product")),
        "product_fit": serialize(row.get("product_fit")),
        "classification_confidence": serialize(row.get("classification_confidence")),
        "review_status": serialize(row.get("review_status")),
        "key_signal": serialize(row.get("key_signal")),
        "key_risk": serialize(row.get("key_risk")),
        "name": serialize(row.get("name")),
        "email": serialize(row.get("email")),
        "title": serialize(row.get("title")),
        "resolved_company": serialize(row.get("resolved_company")),
        "resolved_domain": serialize(row.get("resolved_domain")),
        "resolution_confidence": serialize(row.get("resolution_confidence")),
        "affiliation_state": serialize(row.get("affiliation_state")),
        "target_vertical": serialize(row.get("exa_target_vertical")),
        "stage": serialize(row.get("exa_stage")),
        "np_trigger": serialize(row.get("np_trigger")),
        "flags": serialize(row.get("flags")),
        "employee_count": serialize(row.get("apollo_org_employee_count")),
        "funding_stage": serialize(row.get("apollo_org_funding_stage")),
        "revenue": serialize(row.get("apollo_org_revenue")),
        "person_role_signals": serialize(row.get("exa_person_role_signals")),
        "what_they_do": serialize(row.get("exa_what_they_do")),
        "raw_company_claim": serialize(row.get("raw_company_claim")),
        "building_context": serialize(row.get("self_reported_building")),
        "enrichment_data_quality": serialize(row.get("enrichment_data_quality")),
        "exa_data_quality": serialize(row.get("exa_data_quality")),
        "person_state": serialize(row.get("person_state")),
        "company_state": serialize(row.get("company_state")),
        "apollo_match_status": serialize(row.get("apollo_match_status")),
        "linkedin_url": serialize(row.get("apollo_person_linkedin_url")),
        "classification_reasoning": serialize(row.get("reasoning")),
        "resolution_evidence": serialize(row.get("resolution_evidence")),
        "candidate_companies": serialize(row.get("candidate_companies")),
        "survey_responses": serialize(row.get("survey_responses")),
    }


async def fetch_rows() -> list[dict[str, Any]]:
    settings = Settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(SQL, {"event_id": EVENT_ID})).mappings().all()
            return [dict(row) for row in rows]
    finally:
        await engine.dispose()


def write_csv(records: list[dict[str, str]]) -> None:
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)


def write_xlsx(records: list[dict[str, str]], summary: dict[str, Any]) -> None:
    wb = Workbook()
    ws_summary = wb.active
    ws_summary.title = "Summary"
    ws = wb.create_sheet("Tier Index")

    ws_summary.append(["Metric", "Value"])
    for key, value in summary.items():
        ws_summary.append([key, serialize(value)])
    _style_sheet(ws_summary, freeze="A2")
    ws_summary.column_dimensions["A"].width = 28
    ws_summary.column_dimensions["B"].width = 80

    ws.append(FIELDS)
    for record in records:
        ws.append([record[field] for field in FIELDS])
    _style_sheet(ws, freeze="A2")
    widths = {
        "A": 18,
        "B": 22,
        "C": 10,
        "D": 14,
        "E": 15,
        "F": 28,
        "G": 30,
        "H": 24,
        "I": 28,
        "J": 28,
        "K": 28,
        "L": 24,
        "M": 18,
        "N": 18,
        "O": 24,
        "P": 42,
        "Q": 18,
        "R": 28,
        "S": 14,
        "T": 18,
        "U": 18,
        "V": 56,
        "W": 56,
        "X": 28,
        "Y": 36,
        "Z": 18,
        "AA": 18,
        "AB": 18,
        "AC": 18,
        "AD": 18,
        "AE": 32,
        "AF": 70,
        "AG": 70,
        "AH": 48,
        "AI": 48,
    }
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.auto_filter.ref = ws.dimensions
    for row in ws.iter_rows(min_row=2):
        bucket = row[1].value or ""
        fill = _bucket_fill(str(bucket))
        for cell in row:
            cell.fill = fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_XLSX)


def _style_sheet(ws, *, freeze: str) -> None:
    header_fill = PatternFill("solid", fgColor="1F2937")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = freeze
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _bucket_fill(bucket: str) -> PatternFill:
    colors = {
        "P0": "D1FAE5",
        "P1": "DBEAFE",
        "NP": "F3F4F6",
        "SKIPPED_FLAGGED": "FEE2E2",
        "SKIPPED_LOW_CONFIDENCE": "FEF3C7",
        "SKIPPED_UNCLASSIFIED": "E5E7EB",
    }
    return PatternFill("solid", fgColor=colors.get(bucket, "FFFFFF"))


async def main() -> None:
    rows = await fetch_rows()
    tier_counts: dict[str, int] = defaultdict(int)
    records = [clean_record(row, tier_counts) for row in rows]
    counts = Counter(record["tier_bucket"] for record in records)
    summary = {
        "event_id": EVENT_ID,
        "going_or_approved_rows": len(records),
        "tier_counts": dict(counts),
        "csv_path": str(OUT_CSV),
        "xlsx_path": str(OUT_XLSX),
    }
    write_csv(records)
    write_xlsx(records, summary)
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
