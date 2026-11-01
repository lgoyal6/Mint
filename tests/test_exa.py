import asyncio
import json

import httpx

from mint.backend.enrichment.exa import ENRICHMENT_SCHEMA, ExaClient


def run(coro):
    return asyncio.run(coro)


def test_health_check_never_calls_exa_or_spends_credits() -> None:
    client = ExaClient(api_key="exa-key")

    assert client.health_check() == {
        "configured": True,
        "verified": False,
        "status": "configured_unverified",
        "credit_cost": "not_checked",
        "detail": "No documented no-cost Exa key check; Mint did not make a billable request.",
    }
    run(client.close())


def test_live_check_uses_one_result_without_page_contents() -> None:
    seen_payload: dict | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = json.loads(request.content)
        return httpx.Response(200, json={"results": [{"title": "Mint"}]})

    client = ExaClient(
        api_key="exa-key",
        base_url="https://exa.test",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )

    result = run(client.live_check())

    assert seen_payload == {
        "query": "Mint enrichment integration verification",
        "type": "auto",
        "numResults": 1,
    }
    assert result["verified"] is True
    assert result["estimated_cost_usd"] == 0.007
    run(client.close())


def complete_exa_content() -> dict:
    return {
        "what_they_do": "Quadex builds autonomous coding agents for enterprises.",
        "target_vertical": "agentic",
        "stage": "early-revenue",
        "funding_and_revenue": "Seed funded. Public pricing and customer logos.",
        "compute_profile": "Runs long-horizon agent workflows and is evaluating hosted inference providers.",
        "team_and_activity": "Uses Python and Kubernetes. Hiring ML infrastructure engineer. Launched enterprise product.",
        "person_role_signals": "Founder likely controls infra budget.",
        "data_quality": "rich",
    }


def test_deep_research_sends_design_doc_schema_and_parses_output() -> None:
    seen_payload: dict | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "requestId": "exa-request-1",
                "output": {
                    "content": complete_exa_content(),
                    "grounding": [{"url": "https://quadex.ai"}],
                },
                "results": [{"url": "https://quadex.ai"}],
            },
        )

    client = ExaClient(
        api_key="exa-key",
        base_url="https://exa.test",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )

    result = run(
        client.deep_research(
            company_name="Quadex AI",
            domain="quadex.ai",
            lead_context={"email": "alex@quadex.ai", "what_they_are_building": "coding agent"},
        )
    )

    assert seen_payload is not None
    assert seen_payload["type"] == "deep"
    assert seen_payload["outputSchema"] == ENRICHMENT_SCHEMA
    assert len(seen_payload["outputSchema"]["properties"]) == 8
    assert '"Quadex AI" funding revenue customers' in seen_payload["additionalQueries"]
    assert "coding agent" in seen_payload["query"]
    assert "GMI Cloud sells GPU cloud" in seen_payload["query"]
    assert "likely buyer/user" in seen_payload["query"]
    assert "not whether they sell GPU compute" in seen_payload["query"]
    assert "do not classify the lead as P0, P1, MQL, SQL, or NP" in seen_payload["query"]
    assert "classifier can make the tiering decision later" in seen_payload["query"]
    assert "target company is a buyer/user" in seen_payload["systemPrompt"]
    assert result.target_vertical == "agentic"
    assert result.revenue_signals == "Seed funded. Public pricing and customer logos."
    assert result.funding_signals == "Seed funded. Public pricing and customer logos."
    assert result.inference_signals == "Runs long-horizon agent workflows and is evaluating hosted inference providers."
    assert result.spend_signals == "Runs long-horizon agent workflows and is evaluating hosted inference providers."
    assert result.tech_stack == []
    assert result.hiring_signals == [
        "Uses Python and Kubernetes. Hiring ML infrastructure engineer. Launched enterprise product."
    ]
    assert result.recent_activity == [
        "Uses Python and Kubernetes. Hiring ML infrastructure engineer. Launched enterprise product."
    ]
    assert result.exa_grounding == [{"url": "https://quadex.ai"}]
    run(client.close())


def test_enrichment_schema_encodes_gmi_cloud_icp_without_expanding_properties() -> None:
    assert len(ENRICHMENT_SCHEMA["properties"]) == 8
    target_description = ENRICHMENT_SCHEMA["properties"]["target_vertical"]["description"]
    compute_description = ENRICHMENT_SCHEMA["properties"]["compute_profile"]["description"]
    funding_description = ENRICHMENT_SCHEMA["properties"]["funding_and_revenue"]["description"]

    assert "GMI Cloud ICP" in target_description
    assert "likely buyer/user" in target_description
    assert "video_ai_multimodal" in target_description
    assert "not_target" in target_description
    assert "Autonomous vehicles and CV/vision platforms are adjacent" in target_description
    assert "estimated monthly GPU/inference spend" in compute_description
    assert "Do not assign P0/P1/P2/NR" in compute_description
    assert "classifier can apply thresholds later" in compute_description
    assert "Series A or above" in funding_description


def test_deep_research_normalizes_verbose_data_quality() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "content": {
                        **complete_exa_content(),
                        "data_quality": "moderate: useful sources found but spend signals are inferred",
                    },
                    "grounding": [],
                }
            },
        )

    client = ExaClient(
        api_key="exa-key",
        base_url="https://exa.test",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )

    result = run(client.deep_research("Quadex AI", "quadex.ai", {}))

    assert result.data_quality == "moderate"
    run(client.close())


def test_deep_research_forces_parent_university_to_not_target() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "output": {
                    "content": {
                        **complete_exa_content(),
                        "what_they_do": "University of Florida is a public research university with robotics labs.",
                        "target_vertical": "robotics",
                    },
                    "grounding": [],
                }
            },
        )

    client = ExaClient(
        api_key="exa-key",
        base_url="https://exa.test",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )

    result = run(client.deep_research("University of Florida", "ufl.edu", {}))

    assert result.target_vertical == "not_target"
    run(client.close())


def test_company_and_people_search_compact_results() -> None:
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if payload.get("category") == "company":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Quadex AI",
                            "url": "https://www.quadex.ai/about",
                            "company": {"domain": "quadex.ai"},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Alex Rivera - Founder at Quadex AI",
                        "url": "https://linkedin.com/in/alex",
                        "highlights": ["Founder at Quadex AI"],
                        "extra": "ignored",
                    }
                ]
            },
        )

    client = ExaClient(
        api_key="exa-key",
        base_url="https://exa.test",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )

    companies = run(client.company_search("Quadex AI"))
    people = run(client.people_search("Alex Rivera", "Quadex AI"))
    profile = run(client.profile_affiliation_search("Alex Rivera", "https://linkedin.com/in/alex", "Quadex AI"))

    assert payloads[0]["category"] == "company"
    assert payloads[1]["category"] == "people"
    assert payloads[2]["category"] == "people"
    assert '"https://linkedin.com/in/alex"' in payloads[2]["query"]
    assert companies == [{"name": "Quadex AI", "domain": "quadex.ai", "url": "https://www.quadex.ai/about"}]
    assert people == [
        {
            "title": "Alex Rivera - Founder at Quadex AI",
            "url": "https://linkedin.com/in/alex",
            "highlights": ["Founder at Quadex AI"],
        }
    ]
    assert profile == people
    run(client.close())
