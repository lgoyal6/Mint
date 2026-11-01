from __future__ import annotations

import csv
from io import BytesIO, StringIO
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pydantic import BaseModel, ConfigDict, Field


LEAD_EXPORT_COLUMNS = [
    "Tier",
    "Primary Product",
    "Confidence",
    "Review Status",
    "First Name",
    "Last Name",
    "Name",
    "Email",
    "Title",
    "LinkedIn URL",
    "Company",
    "Domain",
    "Company Stage",
    "Stage Source",
    "Stage Confidence",
    "Target Vertical",
    "What they are Building",
    "Key Signal",
    "Key Risk",
    "Reasoning",
    "Product Fit",
    "NP Trigger",
]
ICP_EXPORT_COLUMNS = [
    "tier",
    "product_fit",
    "primary_product",
    "confidence",
    "name",
    "title",
    "company",
    "domain",
    "company_stage",
    "stage_source",
    "stage_confidence",
    "vertical",
    "email",
    "checked_in",
    "apollo_match",
    "resolution_conf",
    "data_quality",
    "key_signal",
    "key_risk",
    "np_trigger",
]
SUMMARY_COLUMNS = ["Event", "Classified Rows", "P0", "P1", "P2", "NR", "NP"]

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MEDIA_TYPE = "text/csv"
ExportProfile = Literal["standard", "icp", "mql"]

MQL_EXPORT_COLUMNS = [
    "Tier",
    "Event Priority",
    "Approval Status",
    "Name",
    "Title",
    "Company",
    "Domain",
    "Company Stage",
    "Stage Source",
    "Stage Confidence",
    "Region",
    "Location",
    "Primary Product",
    "Product Fit",
    "Confidence",
    "Review Status",
    "Email",
    "LinkedIn URL",
    "Target Vertical",
    "Checked In",
    "Apollo Match",
    "Resolution Confidence",
    "Data Quality",
    "What they are Building",
    "Key Signal",
    "Key Risk",
    "Reasoning",
    "NP Trigger",
]


class LeadExportRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tier: str | None = None
    primary_product: str | None = None
    confidence: int | None = None
    review_status: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    name: str | None = None
    email: str
    title: str | None = None
    linkedin_url: str | None = None
    company: str | None = None
    domain: str | None = None
    company_stage: str = "unknown"
    stage_source: str = "unknown"
    stage_confidence: str = "unknown"
    target_vertical: str | None = None
    what_they_are_building: str | None = None
    key_signal: str | None = None
    key_risk: str | None = None
    reasoning: str | None = None
    product_fit: list[str] = Field(default_factory=list)
    np_trigger: str | None = None
    event_names: list[str] = Field(default_factory=list)
    approval_status: str | None = None
    checked_in: bool | None = None
    apollo_match: str | None = None
    resolution_confidence: str | None = None
    data_quality: str | None = None
    location: str | None = None
    region: str = "Unknown"


class LeadExportSummaryRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: str
    classified_rows: int
    p0: int
    p1: int
    p2: int
    nr: int
    np: int


class LeadExportData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rows: list[LeadExportRow]
    summary: list[LeadExportSummaryRow]
    profile: ExportProfile = "standard"


class LeadExportArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    filename: str
    download_url: str
    row_count: int
    format: str


class AgentBoxExportRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    title: str | None = None
    company: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    band: str
    total: int
    needs_review: bool
    suggested_sender: str
    runtime_fit: dict[str, object]
    forcing_event: dict[str, object]
    inference_attach: dict[str, object]
    budget_path: dict[str, object]
    strategic_value: dict[str, object]
    apollo_person_id: str | None = None
    domain: str | None = None
    headcount: int | None = None
    funding_stage: str | None = None
    total_funding: float | None = None
    days_since_funding: int | None = None
    org_short_desc: str | None = None
    org_keywords: list[str] = Field(default_factory=list)
    ephemeral_only: bool = False
    vendor_origin_policy: bool = False
    campaign: str = "default"
    candidate_segment: str = "unreviewed"
    workload_review: str = ""


class AgentBoxExportData(BaseModel):
    rows: list[AgentBoxExportRow]


class LeadExportService:
    def __init__(self, export_dir: Path) -> None:
        self.export_dir = export_dir

    def create_artifact(self, export: LeadExportData, *, fmt: str = "xlsx") -> LeadExportArtifact:
        normalized = _normalize_format(fmt)
        content, _ = write_lead_export(export, fmt=normalized)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        prefix = "mint-mql-analysis" if export.profile == "mql" and normalized == "xlsx" else "mint-leads"
        filename = f"{prefix}-{uuid4().hex}.{normalized}"
        path = self.export_dir / filename
        path.write_bytes(content)
        return LeadExportArtifact(
            filename=filename,
            download_url=f"/exports/{filename}",
            row_count=len(export.rows),
            format=normalized,
        )

    def resolve_artifact(self, filename: str) -> Path | None:
        if Path(filename).name != filename:
            return None
        path = (self.export_dir / filename).resolve()
        export_root = self.export_dir.resolve()
        if export_root not in path.parents:
            return None
        if not path.is_file():
            return None
        return path

    def create_agentbox_artifact(self, export: AgentBoxExportData) -> LeadExportArtifact:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        filename = f"mint-agentbox-leads-{uuid4().hex}.xlsx"
        path = self.export_dir / filename
        path.write_bytes(write_agentbox_export(export))
        return LeadExportArtifact(filename=filename, download_url=f"/exports/{filename}", row_count=len(export.rows), format="xlsx")


def write_lead_export(export: LeadExportData, fmt: str = "xlsx") -> tuple[bytes, str]:
    normalized = _normalize_format(fmt)
    if normalized == "csv":
        return _write_csv(export), CSV_MEDIA_TYPE
    return _write_xlsx(export), XLSX_MEDIA_TYPE


AGENTBOX_COLUMNS = [
    "Name", "Title", "Company", "LinkedIn URL", "Email", "Band", "Total", "Needs Review", "Suggested Sender", "Campaign", "Candidate Segment", "Workload Review",
    "Runtime Fit Score", "Runtime Fit Source", "Runtime Fit Confidence", "Runtime Evidence Note",
    "Forcing Event Score", "Forcing Event Source", "Forcing Event Confidence", "Forcing Event Note",
    "Inference Attach Score", "Inference Attach Source", "Inference Attach Confidence", "Inference Attach Note",
    "Budget Path Score", "Budget Path Source", "Budget Path Confidence", "Budget Path Note",
    "Strategic Value Score", "Strategic Value Source", "Strategic Value Confidence", "Strategic Value Note",
    "Apollo Person ID", "Domain", "Headcount", "Funding Stage", "Total Funding", "Days Since Funding",
    "Organization Description", "Organization Keywords", "Ephemeral Only", "Vendor Origin Policy",
]


def write_agentbox_export(export: AgentBoxExportData) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "AgentBox Leads"
    sheet.append(AGENTBOX_COLUMNS)
    for row in export.rows:
        values: list[object] = [
            row.name, row.title, row.company, row.linkedin_url, row.email, row.band, row.total,
            "REVIEW" if row.needs_review else "", row.suggested_sender, row.campaign, row.candidate_segment, row.workload_review,
        ]
        for dimension in (row.runtime_fit, row.forcing_event, row.inference_attach, row.budget_path, row.strategic_value):
            values.extend([dimension.get("score"), dimension.get("source"), dimension.get("confidence"), dimension.get("note")])
        values.extend([
            row.apollo_person_id, row.domain, row.headcount, row.funding_stage, row.total_funding, row.days_since_funding,
            row.org_short_desc, ", ".join(row.org_keywords), row.ephemeral_only, row.vendor_origin_policy,
        ])
        sheet.append(values)
    _format_sheet(sheet, len(AGENTBOX_COLUMNS), [20, 25, 26, 34, 28, 16, 9, 14, 18, 24, 20, 52, *([12, 15, 14, 42] * 5), 20, 24, 12, 16, 16, 18, 46, 30, 14, 18], wrap_columns={"L", "P", "T", "X", "AB", "AF", "AM", "AN"})
    band_fills = {
        "pursue": "FFC6EFCE", "nurture": "FFFFF2CC", "pass": "FFE7E6E6", "partner_track": "FFD9EAF7", "disqualify": "FFF4CCCC",
    }
    for row in range(2, sheet.max_row + 1):
        band = str(sheet.cell(row, 6).value or "")
        fill = PatternFill("solid", fgColor=band_fills.get(band, "FFFFFFFF"))
        sheet.cell(row, 6).fill = fill
        if sheet.cell(row, 8).value == "REVIEW":
            sheet.cell(row, 8).fill = PatternFill("solid", fgColor="FFFFC7CE")
            sheet.cell(row, 8).font = Font(bold=True, color="FF9C0006")
    _write_agentbox_outreach_sheet(workbook, export.rows)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_agentbox_outreach_sheet(workbook: Workbook, rows: list[AgentBoxExportRow]) -> None:
    """Create an actionable review queue without inventing LinkedIn URLs."""
    sheet = workbook.create_sheet("Outreach Review")
    headers = [
        "Name", "Title", "Company", "LinkedIn URL", "LinkedIn Status", "Band", "Total",
        "Suggested Sender", "Runtime Evidence", "Workload Review", "Next Action",
    ]
    sheet.append(headers)
    # Keep potential partner and direct-outreach candidates visible even when
    # Apollo did not return a LinkedIn URL. A blank URL should be a review task,
    # not a reason for an otherwise useful queue to appear empty.
    review_queue = [row for row in rows if row.band in {"pursue", "nurture", "partner_track"}]
    for row in review_queue:
        sheet.append([
            row.name,
            row.title,
            row.company,
            row.linkedin_url,
            "Provided by Apollo" if row.linkedin_url else "Not returned by Apollo",
            row.band,
            row.total,
            row.suggested_sender,
            row.runtime_fit.get("note"),
            row.workload_review,
            _agentbox_next_action(row),
        ])
    _format_sheet(sheet, len(headers), [22, 26, 28, 40, 24, 14, 10, 18, 48, 58, 48], wrap_columns={"I", "J", "K"})
    for row in range(2, sheet.max_row + 1):
        band = str(sheet.cell(row, 6).value or "")
        sheet.cell(row, 6).fill = PatternFill(
            "solid",
            fgColor={"pursue": "FFC6EFCE", "nurture": "FFFFF2CC", "partner_track": "FFD9EAF7"}.get(band, "FFFFFFFF"),
        )


def _agentbox_next_action(row: AgentBoxExportRow) -> str:
    if row.band == "partner_track":
        return "Evaluate partnership fit; do not treat as a direct outbound lead yet."
    if not row.linkedin_url:
        return "Find and verify the LinkedIn profile before outreach."
    if row.needs_review:
        return "Verify the runtime evidence before outreach."
    return "Ready for a human outreach review."


def lead_row_values(row: LeadExportRow, *, profile: ExportProfile = "standard") -> list[str | int | None]:
    if profile == "icp":
        return [
            row.tier,
            ", ".join(row.product_fit),
            row.primary_product,
            row.confidence,
            row.name or _person_name(row.first_name, row.last_name),
            row.title,
            row.company,
            row.domain,
            row.company_stage,
            row.stage_source,
            row.stage_confidence,
            row.target_vertical,
            row.email,
            "yes" if row.checked_in else "no" if row.checked_in is not None else None,
            row.apollo_match,
            row.resolution_confidence,
            row.data_quality,
            row.key_signal,
            row.key_risk,
            row.np_trigger,
        ]
    return [
        row.tier,
        row.primary_product,
        row.confidence,
        row.review_status,
        row.first_name,
        row.last_name,
        row.name or _person_name(row.first_name, row.last_name),
        row.email,
        row.title,
        row.linkedin_url,
        row.company,
        row.domain,
        row.company_stage,
        row.stage_source,
        row.stage_confidence,
        row.target_vertical,
        row.what_they_are_building,
        row.key_signal,
        row.key_risk,
        row.reasoning,
        ", ".join(row.product_fit),
        row.np_trigger,
    ]


def _person_name(first_name: str | None, last_name: str | None) -> str | None:
    parts = [part for part in [first_name, last_name] if part]
    return " ".join(parts) if parts else None


def _write_csv(export: LeadExportData) -> bytes:
    buffer = StringIO()
    writer = csv.writer(buffer)
    columns, rows = _export_layout(export)
    writer.writerow(columns)
    for row in export.rows:
        writer.writerow(rows(row))
    return buffer.getvalue().encode("utf-8")


def _write_xlsx(export: LeadExportData) -> bytes:
    if export.profile == "mql":
        return _write_mql_xlsx(export)

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    _, row_writer = _export_layout(export)
    leads = workbook.create_sheet("ICP" if export.profile == "icp" else "Leads")

    summary.append(SUMMARY_COLUMNS)
    for row in export.summary:
        summary.append([row.event, row.classified_rows, row.p0, row.p1, row.p2, row.nr, row.np])

    columns, _ = _export_layout(export)
    leads.append(columns)
    for row in export.rows:
        leads.append(row_writer(row))

    _format_sheet(summary, len(SUMMARY_COLUMNS), [28, 18, 10, 10, 10, 10, 10])
    if export.profile == "icp":
        _format_sheet(
            leads,
            len(ICP_EXPORT_COLUMNS),
            [10, 20, 18, 12, 24, 24, 24, 24, 16, 16, 16, 18, 28, 12, 16, 16, 16, 36, 36, 24],
            wrap_columns={"R", "S"},
        )
    else:
        _format_sheet(
            leads,
            len(LEAD_EXPORT_COLUMNS),
            [10, 18, 12, 16, 16, 16, 22, 28, 24, 34, 24, 22, 16, 16, 16, 22, 38, 38, 38, 50, 20, 24],
            wrap_columns={"Q", "R", "S", "T"},
        )

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_mql_xlsx(export: LeadExportData) -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    event_priority = workbook.create_sheet("Event Priority")
    approval_needed = workbook.create_sheet("P0 Approval Needed")
    mqls = workbook.create_sheet("MQLs")
    p2_potential = workbook.create_sheet("P2 Potential")
    needs_review = workbook.create_sheet("Needs Review")
    all_classified = workbook.create_sheet("All Classified")

    summary.append(SUMMARY_COLUMNS)
    for row in export.summary:
        summary.append([row.event, row.classified_rows, row.p0, row.p1, row.p2, row.nr, row.np])

    totals_row = len(export.summary) + 3
    tier_counts = {tier: sum(1 for row in export.rows if row.tier == tier) for tier in ("P0", "P1", "P2", "NR", "NP")}
    mql_count = tier_counts["P0"] + tier_counts["P1"]
    review_count = sum(1 for row in export.rows if row.review_status == "NEEDS_REVIEW")
    checked_in_mqls = sum(1 for row in export.rows if row.tier in {"P0", "P1"} and row.checked_in is True)
    approved_mqls = sum(
        1 for row in export.rows if row.tier in {"P0", "P1"} and _is_approved(row.approval_status)
    )
    p0_approval_needed = sum(
        1 for row in export.rows if row.tier == "P0" and not _is_approved(row.approval_status)
    )
    summary.cell(totals_row, 1, "MQL analysis")
    summary.cell(totals_row + 1, 1, "Classified leads")
    summary.cell(totals_row + 1, 2, len(export.rows))
    summary.cell(totals_row + 2, 1, "MQLs (P0 + P1)")
    summary.cell(totals_row + 2, 2, mql_count)
    summary.cell(totals_row + 3, 1, "Checked-in MQLs")
    summary.cell(totals_row + 3, 2, checked_in_mqls)
    summary.cell(totals_row + 4, 1, "Needs review")
    summary.cell(totals_row + 4, 2, review_count)
    summary.cell(totals_row + 5, 1, "Approved P0/P1")
    summary.cell(totals_row + 5, 2, approved_mqls)
    summary.cell(totals_row + 6, 1, "P0 approval needed")
    summary.cell(totals_row + 6, 2, p0_approval_needed)
    summary.cell(totals_row + 7, 1, "P0 / P1 / P2 / NR / NP")
    summary.cell(
        totals_row + 7,
        2,
        f"{tier_counts['P0']} / {tier_counts['P1']} / {tier_counts['P2']} / {tier_counts['NR']} / {tier_counts['NP']}",
    )

    regional_mqls = [row for row in export.rows if row.tier in {"P0", "P1"}]
    regional_counts = _region_counts(regional_mqls)
    region_header_row = totals_row + 10
    summary.cell(region_header_row, 1, "MQLs by region")
    summary.cell(region_header_row, 1).fill = PatternFill("solid", fgColor="FFDDEFEA")
    summary.cell(region_header_row, 1).font = Font(bold=True, color="FF111827")
    summary.cell(region_header_row + 1, 1, "Region")
    summary.cell(region_header_row + 1, 2, "MQLs")
    for index, (region, count) in enumerate(regional_counts, start=region_header_row + 2):
        summary.cell(index, 1, region)
        summary.cell(index, 2, count)

    priority_rows = sorted(
        (row for row in export.rows if row.tier in {"P0", "P1"}),
        key=_event_priority_sort_key,
    )
    event_priority.append(MQL_EXPORT_COLUMNS)
    for row in priority_rows:
        event_priority.append(_mql_row_values(row))

    approval_needed.append(MQL_EXPORT_COLUMNS)
    for row in priority_rows:
        if row.tier == "P0" and not _is_approved(row.approval_status):
            approval_needed.append(_mql_row_values(row))

    mqls.append(MQL_EXPORT_COLUMNS)
    for row in export.rows:
        if row.tier in {"P0", "P1"}:
            mqls.append(_mql_row_values(row))

    p2_potential.append(MQL_EXPORT_COLUMNS)
    for row in export.rows:
        if row.tier == "P2":
            p2_potential.append(_mql_row_values(row))

    needs_review.append(MQL_EXPORT_COLUMNS)
    for row in export.rows:
        if row.review_status == "NEEDS_REVIEW":
            needs_review.append(_mql_row_values(row))

    all_classified.append(MQL_EXPORT_COLUMNS)
    for row in export.rows:
        all_classified.append(_mql_row_values(row))

    for region, _ in regional_counts:
        sheet = workbook.create_sheet(_region_sheet_name(region, workbook.sheetnames))
        sheet.append(MQL_EXPORT_COLUMNS)
        for row in regional_mqls:
            if row.region == region:
                sheet.append(_mql_row_values(row))

    _format_sheet(summary, len(SUMMARY_COLUMNS), [30, 18, 10, 10, 10, 10, 10])
    summary.auto_filter.ref = f"A1:G{max(len(export.summary) + 1, 1)}"
    summary.cell(totals_row, 1).fill = PatternFill("solid", fgColor="FFDDEFEA")
    summary.cell(totals_row, 1).font = Font(bold=True, color="FF111827")
    summary.merge_cells(start_row=totals_row, start_column=1, end_row=totals_row, end_column=2)
    for row_index in range(totals_row + 1, totals_row + 8):
        summary.cell(row_index, 1).font = Font(bold=True)

    widths = [10, 28, 18, 24, 24, 24, 22, 16, 16, 16, 20, 28, 18, 20, 12, 16, 28, 34, 20, 12, 16, 18, 16, 38, 42, 42, 56, 24]
    wrap_columns = {"X", "Y", "Z", "AA"}
    _format_sheet(event_priority, len(MQL_EXPORT_COLUMNS), widths, wrap_columns=wrap_columns)
    _format_sheet(approval_needed, len(MQL_EXPORT_COLUMNS), widths, wrap_columns=wrap_columns)
    _format_sheet(mqls, len(MQL_EXPORT_COLUMNS), widths, wrap_columns=wrap_columns)
    _format_sheet(p2_potential, len(MQL_EXPORT_COLUMNS), widths, wrap_columns=wrap_columns)
    _format_sheet(needs_review, len(MQL_EXPORT_COLUMNS), widths, wrap_columns=wrap_columns)
    _format_sheet(all_classified, len(MQL_EXPORT_COLUMNS), widths, wrap_columns=wrap_columns)
    for sheet in workbook.worksheets[7:]:
        _format_sheet(sheet, len(MQL_EXPORT_COLUMNS), widths, wrap_columns=wrap_columns)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _mql_row_values(row: LeadExportRow) -> list[str | int | None]:
    return [
        row.tier,
        _event_priority(row),
        row.approval_status,
        row.name or _person_name(row.first_name, row.last_name),
        row.title,
        row.company,
        row.domain,
        row.company_stage,
        row.stage_source,
        row.stage_confidence,
        row.region,
        row.location,
        row.primary_product,
        ", ".join(row.product_fit),
        row.confidence,
        row.review_status,
        row.email,
        row.linkedin_url,
        row.target_vertical,
        "yes" if row.checked_in else "no" if row.checked_in is not None else None,
        row.apollo_match,
        row.resolution_confidence,
        row.data_quality,
        row.what_they_are_building,
        row.key_signal,
        row.key_risk,
        row.reasoning,
        row.np_trigger,
    ]


def _is_approved(status: str | None) -> bool:
    return str(status or "").strip().lower() in {"approved", "going"}


def _event_priority(row: LeadExportRow) -> str:
    approved = _is_approved(row.approval_status)
    if row.tier == "P0" and not approved:
        return "URGENT — APPROVE + CONTACT"
    if row.tier == "P0":
        return "URGENT — CONTACT BEFORE/DURING"
    if row.tier == "P1" and not approved:
        return "REVIEW APPROVAL + CONTACT"
    if row.tier == "P1":
        return "CONTACT BEFORE/DURING"
    return ""


def _event_priority_sort_key(row: LeadExportRow) -> tuple[int, int, int, str]:
    tier_rank = 0 if row.tier == "P0" else 1
    approval_rank = 0 if row.tier == "P0" and not _is_approved(row.approval_status) else 1
    confidence_rank = -(row.confidence or 0)
    return tier_rank, approval_rank, confidence_rank, row.name or row.email


def _region_counts(rows: list[LeadExportRow]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.region] = counts.get(row.region, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _region_sheet_name(region: str, existing: list[str]) -> str:
    base = f"MQLs - {region}"[:31]
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base[:28]} {suffix}"
        suffix += 1
    return candidate


def _export_layout(export: LeadExportData) -> tuple[list[str], Callable[[LeadExportRow], list[str | int | None]]]:
    if export.profile in {"icp", "mql"}:
        return ICP_EXPORT_COLUMNS, lambda row: lead_row_values(row, profile="icp")
    return LEAD_EXPORT_COLUMNS, lambda row: lead_row_values(row, profile="standard")


def _format_sheet(
    sheet,
    column_count: int,
    widths: list[int],
    *,
    wrap_columns: set[str] | None = None,
) -> None:
    header_fill = PatternFill("solid", fgColor="FF1F2937")
    header_font = Font(color="FFFFFF", bold=True)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="top")

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{sheet.cell(row=max(sheet.max_row, 1), column=column_count).coordinate}"

    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = width

    for column in wrap_columns or set():
        for cell in sheet[column][1:]:
            cell.alignment = Alignment(wrap_text=True, vertical="top")


def _normalize_format(fmt: str) -> str:
    normalized = (fmt or "xlsx").lower()
    if normalized not in {"xlsx", "csv"}:
        raise ValueError("format must be xlsx or csv")
    return normalized
