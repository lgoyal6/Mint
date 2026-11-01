from mint.backend.models import EnrichmentInput, EventInput, ExaEnrichmentResult
from mint.backend.utils.domain import clean_domain, email_domain, is_personal_email_domain


def test_enrichment_input_normalizes_email_and_blank_strings() -> None:
    lead = EnrichmentInput(
        email="  ALEX@Quadex.AI  ",
        first_name=" Alex ",
        last_name="",
        company_name="  Quadex AI ",
    )

    assert lead.email == "alex@quadex.ai"
    assert lead.first_name == "Alex"
    assert lead.last_name is None
    assert lead.company_name == "Quadex AI"


def test_exa_enrichment_schema_defaults_match_design_doc() -> None:
    result = ExaEnrichmentResult(
        what_they_do="Builds coding agents.",
        target_vertical="agentic",
        stage="early-revenue",
        revenue_signals="Pricing page and customer logos.",
        funding_signals="Seed round.",
        inference_signals="Hosts agent models.",
        spend_signals="Hiring infra engineers.",
        person_role_signals="Founder is likely buyer.",
        data_quality="rich",
    )

    assert result.business_model is None
    assert result.customer_signals == ""
    assert result.competitors_or_current_infra == ""
    assert result.tech_stack == []
    assert result.hiring_signals == []
    assert result.recent_activity == []


def test_event_input_defaults_enrichment_disabled() -> None:
    event = EventInput(name="New Managed Event", status="upcoming")

    assert event.enrichment_enabled is False


def test_domain_helpers_clean_urls_and_detect_personal_email_domains() -> None:
    assert clean_domain("https://www.example.com/path?q=1") == "example.com"
    assert email_domain("alex@EXAMPLE.com") == "example.com"
    assert is_personal_email_domain("gmail.com") is True
    assert is_personal_email_domain("example.com") is False
