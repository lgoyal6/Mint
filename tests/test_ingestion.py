from openpyxl import Workbook

from mint.backend.ingestion.csv_loader import load_csv, load_rows
from mint.backend.ingestion.normalizer import (
    csv_approval_status_by_email,
    normalize_csv_rows,
    normalize_luma_guests,
)


def test_normalize_csv_rows_auto_detects_columns_custom_questions_and_deduplicates() -> None:
    rows = [
        {
            "Email Address": " ALEX@EXAMPLE.COM ",
            "First Name": "Alex",
            "Last Name": "Rivera",
            "What company do you work for?": "Quadex AI",
            "Website URL": "https://quadex.ai",
            "What are you building?": "Autonomous coding agent",
            "How many GPUs?": "8x H100",
        },
        {
            "Email Address": "alex@example.com",
            "First Name": "Duplicate",
        },
    ]

    leads = normalize_csv_rows(rows, column_mapping=None)

    assert len(leads) == 1
    assert leads[0].email == "alex@example.com"
    assert leads[0].company_name == "Quadex AI"
    assert leads[0].website_url == "https://quadex.ai"
    assert leads[0].what_they_are_building == "Autonomous coding agent"
    assert leads[0].survey_responses == {"How many GPUs?": "8x H100"}
    assert leads[0].source == "csv"


def test_normalize_csv_rows_maps_luma_job_title_to_title() -> None:
    rows = [
        {
            "Email": "alex@example.com",
            "first_name": "Alex",
            "last_name": "Rivera",
            "What company do you work for?": "Quadex AI",
            "What is your job title?": "Founder",
            "What are you building or deploying into production?": "Coding agent",
            "What is your X (Twitter) handle?": "@alex",
            "guest_id": "gst-123",
            "checked_in_at": "2026-05-27T06:00:00Z",
            "amount": "0",
            "qr_code_url": "https://example.com/qr",
        }
    ]

    leads = normalize_csv_rows(rows, column_mapping=None)

    assert leads[0].title == "Founder"
    assert leads[0].survey_responses == {"What is your X (Twitter) handle?": "@alex"}


def test_normalize_csv_rows_handles_luma_export_question_headers_and_status() -> None:
    rows = [
        {
            "Name": "Aarya Mani",
            "Status": "Going",
            "What's your LinkedIn URL?": "https://www.linkedin.com/in/aarya-mani-810a732a1/",
            "Are you a": "Founder",
            "What's your company?": "Solace",
            "What is your email? (The host may contact you at this email)": "Sinbadmiracle11@gmail.com",
        },
        {
            "Name": "Maybe Guest",
            "Status": "Maybe",
            "What's your company?": "Maybe Co",
            "What is your email? (The host may contact you at this email)": "maybe@example.com",
        },
    ]

    leads = normalize_csv_rows(rows, column_mapping=None)

    assert len(leads) == 2
    assert leads[0].email == "sinbadmiracle11@gmail.com"
    assert leads[0].first_name == "Aarya"
    assert leads[0].last_name == "Mani"
    assert leads[0].company_name == "Solace"
    assert leads[0].linkedin_url == "https://www.linkedin.com/in/aarya-mani-810a732a1/"
    assert leads[0].survey_responses == {"Are you a": "Founder"}
    assert leads[1].email == "maybe@example.com"
    assert csv_approval_status_by_email(rows) == {
        "sinbadmiracle11@gmail.com": "going",
        "maybe@example.com": "maybe",
    }


def test_normalize_csv_rows_keeps_company_stage_question_out_of_company_name() -> None:
    rows = [
        {
            "name": "Jason Melo",
            "email": "jason@touchbrick.com",
            "What is your LinkedIn profile?": "https://linkedin.com/in/jasonmelo",
            "What's your company's website?": "https://privacypal.ai/",
            "Which best describes your company today?": "Raised (pre-seed/seed/A+), pre-revenue",
            "What's your current GTM motion?": "Mostly inbound / community / partnerships",
        }
    ]

    leads = normalize_csv_rows(rows, column_mapping=None)

    assert len(leads) == 1
    assert leads[0].company_name is None
    assert leads[0].website_url == "https://privacypal.ai/"
    assert leads[0].linkedin_url == "https://linkedin.com/in/jasonmelo"
    assert leads[0].survey_responses == {
        "Which best describes your company today?": "Raised (pre-seed/seed/A+), pre-revenue",
        "What's your current GTM motion?": "Mostly inbound / community / partnerships",
    }


def test_csv_approval_status_normalizes_luma_pending_approval() -> None:
    rows = [
        {"email": "approved@example.com", "approval_status": "approved"},
        {"email": "pending@example.com", "approval_status": "pending_approval"},
        {"email": "declined@example.com", "approval_status": "declined"},
    ]

    assert csv_approval_status_by_email(rows) == {
        "approved@example.com": "approved",
        "pending@example.com": "pending",
        "declined@example.com": "declined",
    }


def test_load_csv_reads_file_and_returns_enrichment_inputs(tmp_path) -> None:
    path = tmp_path / "leads.csv"
    path.write_text(
        "Email,First Name,Company,What are you working on?\n"
        "casey@example.com,Casey,Quadex AI,Robotics foundation model\n",
        encoding="utf-8",
    )

    leads = load_csv(str(path))

    assert len(leads) == 1
    assert leads[0].email == "casey@example.com"
    assert leads[0].company_name == "Quadex AI"
    assert leads[0].what_they_are_building == "Robotics foundation model"


def test_load_csv_detects_semicolon_delimiter(tmp_path) -> None:
    path = tmp_path / "partner-export.csv"
    path.write_text(
        "Email;First Name;Company;Title\n"
        "moritz@example.com;Moritz;Berthold Labs;Founder\n",
        encoding="utf-8",
    )

    leads = load_csv(str(path))

    assert len(leads) == 1
    assert leads[0].email == "moritz@example.com"
    assert leads[0].company_name == "Berthold Labs"
    assert leads[0].title == "Founder"


def test_load_rows_uses_mql_sheet_when_reuploading_export(tmp_path) -> None:
    path = tmp_path / "existing-mql.xlsx"
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    summary.append(["Metric", "Value"])
    summary.append(["MQLs", 1])
    mqls = workbook.create_sheet("MQLs")
    mqls.append(["Name", "Email", "Company", "Location"])
    mqls.append(["Alex Rivera", "alex@example.com", "Quadex AI", None])
    workbook.save(path)

    rows = load_rows(str(path))

    assert rows == [
        {
            "Name": "Alex Rivera",
            "Email": "alex@example.com",
            "Company": "Quadex AI",
            "Location": None,
        }
    ]


def test_normalize_luma_guests_filters_going_status_and_maps_custom_answers() -> None:
    guests = [
        {
            "email": "alex@example.com",
            "name": "Alex Rivera",
            "approval_status": "going",
            "registration_answers": [
                {"question": "What company do you work for?", "answer": "Quadex AI"},
                {"question": "What is your role?", "answer": "Founder"},
                {"question": "What are you building?", "answer": "Coding agent"},
                {"question": "GPU count?", "answer": "8"},
            ],
        },
        {"email": "pending@example.com", "name": "Pending User", "approval_status": "pending"},
    ]

    leads = normalize_luma_guests(guests, event_id="evt-123")

    assert len(leads) == 1
    assert leads[0].first_name == "Alex"
    assert leads[0].last_name == "Rivera"
    assert leads[0].company_name == "Quadex AI"
    assert leads[0].title == "Founder"
    assert leads[0].what_they_are_building == "Coding agent"
    assert leads[0].survey_responses == {"GPU count?": "8"}
    assert leads[0].source == "luma"
    assert leads[0].event_id == "evt-123"


def test_normalize_luma_guests_accepts_luma_user_prefixed_name_fields() -> None:
    guests = [
        {
            "email": "alex@example.com",
            "user_name": "Alex Rivera",
            "user_first_name": "Alex",
            "user_last_name": "Rivera",
            "approval_status": "approved",
        }
    ]

    leads = normalize_luma_guests(guests, event_id="evt-123")

    assert len(leads) == 1
    assert leads[0].first_name == "Alex"
    assert leads[0].last_name == "Rivera"


def test_normalize_luma_guests_maps_job_title_and_relative_linkedin_profile() -> None:
    guests = [
        {
            "email": "david@example.com",
            "name": "David Babazadeh",
            "approval_status": "going",
            "registration_answers": [
                {"question": "What company do you work for?", "answer": "Creator Vision"},
                {"question": "What is your job title?", "answer": "Co-Founder/ML Researcher"},
                {"question": "What is your LinkedIn profile?", "answer": "/in/david-babazadeh"},
            ],
        }
    ]

    leads = normalize_luma_guests(guests, event_id="evt-cvpr")

    assert len(leads) == 1
    assert leads[0].company_name == "Creator Vision"
    assert leads[0].title == "Co-Founder/ML Researcher"
    assert leads[0].linkedin_url == "https://www.linkedin.com/in/david-babazadeh"
    assert leads[0].survey_responses is None
