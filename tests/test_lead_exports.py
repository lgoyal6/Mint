import csv
from io import BytesIO, StringIO

from openpyxl import load_workbook

from mint.backend.exports.leads import (
    LEAD_EXPORT_COLUMNS,
    LeadExportData,
    LeadExportRow,
    LeadExportSummaryRow,
    write_lead_export,
)


def sample_export() -> LeadExportData:
    return LeadExportData(
        rows=[
            LeadExportRow(
                tier="P0",
                primary_product="maas",
                confidence=5,
                review_status="CLASSIFIED",
                first_name="Alex",
                last_name="Founder",
                email="founder@agentco.ai",
                title="Founder",
                linkedin_url="https://linkedin.com/in/alexfounder",
                company="AgentCo",
                domain="agentco.ai",
                target_vertical="agentic",
                what_they_are_building="Production AI agents for finance workflows.",
                key_signal="Founder + production inference.",
                key_risk="Depends on enrichment freshness.",
                reasoning="Founder at target-vertical company with grounded inference.",
                product_fit=["maas", "dedicated"],
                np_trigger=None,
                event_names=["Hard Problems"],
            )
        ],
        summary=[
            LeadExportSummaryRow(event="Hard Problems", classified_rows=1, p0=1, p1=0, p2=0, nr=0, np=0)
        ],
    )


def test_xlsx_writer_creates_summary_and_leads_sheets_with_formatting() -> None:
    content, media_type = write_lead_export(sample_export(), fmt="xlsx")

    workbook = load_workbook(BytesIO(content))
    summary = workbook["Summary"]
    leads = workbook["Leads"]

    assert media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert workbook.sheetnames == ["Summary", "Leads"]
    assert [cell.value for cell in summary[1]] == ["Event", "Classified Rows", "P0", "P1", "P2", "NR", "NP"]
    assert [cell.value for cell in summary[2]] == ["Hard Problems", 1, 1, 0, 0, 0, 0]
    assert [cell.value for cell in leads[1]] == LEAD_EXPORT_COLUMNS
    assert [cell.value for cell in leads[2]] == [
        "P0",
        "maas",
        5,
        "CLASSIFIED",
        "Alex",
        "Founder",
        "Alex Founder",
        "founder@agentco.ai",
        "Founder",
        "https://linkedin.com/in/alexfounder",
        "AgentCo",
        "agentco.ai",
        "unknown",
        "unknown",
        "unknown",
        "agentic",
        "Production AI agents for finance workflows.",
        "Founder + production inference.",
        "Depends on enrichment freshness.",
        "Founder at target-vertical company with grounded inference.",
        "maas, dedicated",
        None,
    ]
    assert summary.freeze_panes == "A2"
    assert leads.freeze_panes == "A2"
    assert summary.auto_filter.ref == "A1:G2"
    assert leads.auto_filter.ref == "A1:V2"
    assert leads["Q2"].alignment.wrap_text is True
    assert leads["T2"].alignment.wrap_text is True
    assert leads["A1"].fill.fgColor.rgb == "FF1F2937"


def test_csv_writer_outputs_only_lead_columns() -> None:
    content, media_type = write_lead_export(sample_export(), fmt="csv")

    rows = list(csv.reader(StringIO(content.decode("utf-8"))))

    assert media_type == "text/csv"
    assert rows[0] == LEAD_EXPORT_COLUMNS
    assert rows[1][0:8] == [
        "P0",
        "maas",
        "5",
        "CLASSIFIED",
        "Alex",
        "Founder",
        "Alex Founder",
        "founder@agentco.ai",
    ]
    assert rows[1][20] == "maas, dedicated"


def test_icp_profile_writer_outputs_analysis_table_columns() -> None:
    export = sample_export().model_copy(
        update={
            "profile": "icp",
            "rows": [
                sample_export().rows[0].model_copy(
                    update={
                        "checked_in": True,
                        "apollo_match": "matched",
                        "resolution_confidence": "high",
                        "data_quality": "rich",
                    }
                )
            ],
        }
    )
    content, media_type = write_lead_export(export, fmt="csv")
    rows = list(csv.reader(StringIO(content.decode("utf-8"))))

    assert media_type == "text/csv"
    assert rows[0] == ["tier", "product_fit", "primary_product", "confidence", "name", "title", "company", "domain", "company_stage", "stage_source", "stage_confidence", "vertical", "email", "checked_in", "apollo_match", "resolution_conf", "data_quality", "key_signal", "key_risk", "np_trigger"]
    assert rows[1][0:5] == ["P0", "maas, dedicated", "maas", "5", "Alex Founder"]
    assert rows[1][8:14] == ["unknown", "unknown", "unknown", "agentic", "founder@agentco.ai", "yes"]
    assert rows[1][14:17] == ["matched", "high", "rich"]


def test_mql_profile_writer_creates_analysis_tabs() -> None:
    base = sample_export().rows[0]
    export = sample_export().model_copy(
        update={
            "profile": "mql",
            "rows": [
                base.model_copy(update={"checked_in": True, "approval_status": "pending"}),
                base.model_copy(
                    update={
                        "tier": "P1",
                        "email": "review@agentco.ai",
                        "name": "Riley Reviewer",
                        "review_status": "NEEDS_REVIEW",
                        "resolution_confidence": "low",
                        "location": "Berlin, Germany",
                        "region": "Europe",
                        "approval_status": "approved",
                    }
                ),
                base.model_copy(
                    update={
                        "tier": "P2",
                        "email": "potential@agentco.ai",
                        "name": "Pat Potential",
                    }
                ),
                base.model_copy(update={"tier": "NR", "email": "nurture@example.com"}),
            ],
        }
    )

    content, _ = write_lead_export(export, fmt="xlsx")
    workbook = load_workbook(BytesIO(content))

    assert workbook.sheetnames == [
        "Summary",
        "Event Priority",
        "P0 Approval Needed",
        "MQLs",
        "P2 Potential",
        "Needs Review",
        "All Classified",
        "MQLs - Europe",
        "MQLs - Unknown",
    ]
    assert workbook["Event Priority"].max_row == 3
    assert workbook["P0 Approval Needed"].max_row == 2
    assert workbook["P0 Approval Needed"]["B2"].value == "URGENT — APPROVE + CONTACT"
    assert workbook["MQLs"].max_row == 3
    assert workbook["P2 Potential"].max_row == 2
    assert workbook["P2 Potential"]["A2"].value == "P2"
    assert workbook["Needs Review"].max_row == 2
    assert workbook["All Classified"].max_row == 5
    assert workbook["MQLs"]["A2"].value == "P0"
    assert workbook["MQLs"]["A3"].value == "P1"
    assert workbook["Needs Review"]["Q2"].value == "review@agentco.ai"
    assert workbook["MQLs"]["H2"].value == "unknown"
    assert workbook["MQLs - Europe"]["L2"].value == "Berlin, Germany"
