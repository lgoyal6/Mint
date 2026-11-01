import asyncio
from datetime import date, timedelta
from io import BytesIO

from openpyxl import load_workbook

from mint.backend.classification.classifier import load_icp_config
from mint.backend.enrichment.apollo import ApolloClient
from mint.backend.enrichment.exa import ExaClient
from mint.backend.source.agentbox import AgentBoxSourceService, _band
from mint.backend.routes.source import _uploaded_icp_config


def run(coro):
    return asyncio.run(coro)


def person(*, description: str, stage: str = "Series A", funding: str = "$12M", employees: int = 70, funded: str = "2026-06-01"):
    return {
        "id": "person-1",
        "name": "Alex Founder",
        "title": "CTO & Founder",
        "linkedin_url": "https://linkedin.com/in/alex-founder",
        "organization": {
            "name": "AgentCo",
            "primary_domain": "agentco.ai",
            "short_description": description,
            "latest_funding_stage": stage,
            "total_funding_printed": funding,
            "estimated_num_employees": employees,
            "latest_funding_round_date": funded,
        },
    }


class SourceApollo(ApolloClient):
    def __init__(self, people, profiles=None):
        super().__init__(None)
        self.people = people
        self.profiles = profiles or {}
        self.campaigns = []
        self.profile_requests = []

    async def search_people(self, campaign):
        self.campaigns.append(campaign)
        return self.people

    async def enrich_people_for_linkedin(self, person_ids):
        self.profile_requests.append(person_ids)
        return {person_id: self.profiles[person_id] for person_id in person_ids if person_id in self.profiles}


class SourceExa(ExaClient):
    def __init__(self, text: str | None = None):
        super().__init__(None)
        self.text = text
        self.calls = 0

    async def runtime_fit_text(self, company_name, domain):
        self.calls += 1
        return self.text, "exa" if self.text else "dry-run"


def service(people, exa_text=None):
    return AgentBoxSourceService(load_icp_config(), SourceApollo(people), SourceExa(exa_text))


def test_uploaded_icp_yaml_is_normalized_for_sourcing() -> None:
    config = _uploaded_icp_config("Fintech infra", """
person_titles: [CTO, VP Engineering]
company_keywords: [payments infrastructure, developer platform]
locations: [United States]
headcount_ranges: ["51,200"]
strong_fit_signals: [self-hosted, transaction processing]
exclusion_terms: [agency]
""")
    source = config["agentbox_campaign"]
    campaign = source["campaigns"]["uploaded_icp"]

    assert source["label"] == "Fintech infra"
    assert campaign["person_titles"] == ["CTO", "VP Engineering"]
    assert campaign["org_keywords"] == ["payments infrastructure", "developer platform"]
    assert source["runtime_terms"]["strong"] == ["self-hosted", "transaction processing"]


def test_uploaded_icp_requires_titles_and_company_keywords() -> None:
    try:
        _uploaded_icp_config("Incomplete", '{"locations": ["United States"]}')
    except ValueError as exc:
        assert "person_titles" in str(exc)
    else:
        raise AssertionError("Expected an incomplete ICP to be rejected")


def test_strong_runtime_series_a_recent_funding_is_not_capped() -> None:
    result = run(service([person(description="placeholder")], "Persistent sandbox code execution with vLLM inference.").run())
    lead = result.leads[0]
    assert lead.band in {"nurture", "pursue"}
    assert lead.dimensions["runtime_fit"].score == 2
    assert lead.band != "pass"


def test_runtime_zero_caps_strong_firmographics_to_pass() -> None:
    recent_funding = (date.today() - timedelta(days=30)).isoformat()
    result = run(service([person(description="Enterprise payments platform", stage="Series B", employees=120, funded=recent_funding)]).run())
    lead = result.leads[0]
    assert lead.total >= 3
    assert lead.dimensions["runtime_fit"].score == 0
    assert lead.band == "pass"


def test_ephemeral_wording_remains_a_sales_lead_instead_of_forcing_partner_track() -> None:
    assert _band(runtime_score=2, total=10, ephemeral_only=True, vendor_origin_policy=False) == "pursue"


def test_strong_runtime_is_a_lead_without_manual_commercial_flags() -> None:
    assert _band(runtime_score=2, total=2, ephemeral_only=False, vendor_origin_policy=False) == "nurture"
    assert _band(runtime_score=2, total=3, ephemeral_only=False, vendor_origin_policy=False) == "pursue"
    assert _band(runtime_score=2, total=4, ephemeral_only=False, vendor_origin_policy=False) == "pursue"


def test_medium_runtime_is_kept_as_a_reviewable_lead() -> None:
    assert _band(runtime_score=1, total=1, ephemeral_only=False, vendor_origin_policy=False) == "nurture"


def test_apollo_discovery_keyword_does_not_qualify_when_exa_text_is_sparse() -> None:
    candidate = person(description="placeholder")
    candidate["_mint_search_keywords"] = ["coding agent"]

    result = run(service([candidate]).run())

    assert result.leads[0].band == "pass"
    assert "discovery keyword matches do not qualify" in result.leads[0].dimensions["runtime_fit"].note


def test_obvious_non_target_vertical_is_rejected_by_icp_gate() -> None:
    candidate = person(description="AI agent tools for breweries and beverage companies")
    result = run(service([candidate], "Persistent sandbox code execution").run())

    assert result.leads[0].band == "pass"
    assert any(term in result.leads[0].dimensions["runtime_fit"].note for term in ("brew", "beverage"))


def test_competitor_is_rejected_by_icp_gate() -> None:
    candidate = person(description="E2B provides sandboxed code execution for AI agents")
    result = run(service([candidate], "Persistent sandbox code execution").run())

    assert result.leads[0].band == "pass"
    assert "e2b" in result.leads[0].dimensions["runtime_fit"].note.lower()


def test_runtime_research_is_cached_per_company() -> None:
    first = person(description="placeholder")
    second = person(description="placeholder")
    second["id"] = "person-2"
    second["name"] = "Taylor Founder"
    exa = SourceExa("persistent sandbox")
    source = AgentBoxSourceService(load_icp_config(), SourceApollo([first, second]), exa)

    result = run(source.run())

    assert len(result.leads) == 2
    assert exa.calls == 1


def test_paid_profile_enrichment_populates_company_data_and_rescores_without_second_exa_call() -> None:
    partial = {
        "id": "person-1",
        "name": "Alex Founder",
        "title": "CTO & Founder",
        "organization_name": "AgentCo",
    }
    profiles = {
        "person-1": {
            "id": "person-1",
            "linkedin_url": "https://linkedin.com/in/alex-founder",
            "email": "alex@agentco.ai",
            "organization": {
                "name": "AgentCo",
                "primary_domain": "agentco.ai",
                "estimated_num_employees": 70,
                "latest_funding_stage": "Series A",
                "total_funding_printed": "$12M",
                "latest_funding_round_date": "2026-06-01",
                "short_description": "Persistent agent runtime",
                "keywords": ["agentic", "sandbox"],
            },
        }
    }
    apollo = SourceApollo([partial], profiles)
    exa = SourceExa("Persistent sandbox code execution with GPU inference.")
    source = AgentBoxSourceService(load_icp_config(), apollo, exa)

    result = run(source.run())

    lead = result.leads[0]
    assert lead.band == "pursue"
    assert lead.total >= 4
    assert lead.domain == "agentco.ai"
    assert lead.headcount == 70
    assert lead.funding_stage == "Series A"
    assert lead.total_funding == 12_000_000
    assert lead.linkedin_url == "https://linkedin.com/in/alex-founder"
    assert lead.email == "alex@agentco.ai"
    assert exa.calls == 1
    assert apollo.profile_requests == [["person-1"]]


def test_run_limit_overrides_campaign_max_results_for_pilot() -> None:
    apollo = SourceApollo([person(description="sandbox")])
    source = AgentBoxSourceService(load_icp_config(), apollo, SourceExa("persistent sandbox"))

    run(source.run(max_results=20))

    assert apollo.campaigns[0]["max_results"] == 20


def test_vendor_origin_policy_disqualifies_perfect_lead() -> None:
    result = run(service([person(description="placeholder")], "Persistent sandbox code execution with GPU inference.").run({"person-1": {"vendor_origin_policy": True, "gmi_inference_committed": True, "funded_mandate": True, "strategic_value": "distribution"}}))
    lead = result.leads[0]
    assert lead.total >= 8
    assert lead.band == "disqualify"


def test_low_runtime_confidence_requires_review() -> None:
    result = run(service([person(description="AI agent tooling")]).run())
    lead = result.leads[0]
    assert lead.dimensions["runtime_fit"].confidence == 0.5
    assert lead.needs_review is True


def test_v5_non_targets_are_capped_even_when_they_use_agent_language() -> None:
    result = run(service([person(description="AI agent pure API wrapper", stage="Series B")]).run())

    assert result.leads[0].dimensions["runtime_fit"].score == 0
    assert result.leads[0].band == "pass"


def test_tier_3_campaign_is_separate_from_the_product_runtime_default() -> None:
    apollo = SourceApollo([person(description="sandbox")])
    source = AgentBoxSourceService(load_icp_config(), apollo, SourceExa("persistent sandbox"))

    result = run(source.run(campaign_name="tier_3_strategic"))

    assert result.campaign == "tier_3_strategic"
    assert apollo.campaigns[0]["candidate_segment"] == "tier_3"


def test_qualified_lead_without_confirmed_inference_commitment_requires_review() -> None:
    result = run(service([person(description="placeholder")], "Persistent sandbox code execution with vLLM inference.").run())

    assert result.leads[0].needs_review is True


def test_campaign_tier_alone_does_not_force_review() -> None:
    result = run(
        service([person(description="placeholder")], "Persistent sandbox code execution with vLLM inference.").run(
            {"person-1": {"gmi_inference_committed": True}}
        )
    )

    assert result.leads[0].needs_review is False


def test_full_dry_run_creates_xlsx_and_summary() -> None:
    result = run(AgentBoxSourceService(load_icp_config(), ApolloClient(None), ExaClient(None)).run())
    workbook = load_workbook(BytesIO(result.xlsx))
    assert result.mode == "dry-run"
    assert len(result.leads) == 2
    assert workbook.sheetnames == ["AgentBox Leads", "Outreach Review"]
    assert workbook["AgentBox Leads"].freeze_panes == "A2"
    assert workbook["AgentBox Leads"].auto_filter.ref is not None
    assert workbook["Outreach Review"].max_row > 1
