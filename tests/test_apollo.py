import asyncio
import json

import httpx

from mint.backend.enrichment.apollo import ApolloClient


def run(coro):
    return asyncio.run(coro)


def test_match_person_maps_apollo_person_response_to_typed_model() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/api/v1/people/bulk_match"
        payload = json.loads(request.content)
        assert payload["details"][0]["email"] == "alex@quadex.ai"
        return httpx.Response(
            200,
            json={
                "matches": [
                    {
                        "email": "alex@quadex.ai",
                        "first_name": "Alex",
                        "last_name": "Rivera",
                        "title": "Founder",
                        "seniority": "founder",
                        "departments": ["Engineering"],
                        "linkedin_url": "https://linkedin.com/in/alex",
                        "city": "San Francisco",
                        "state": "CA",
                        "country": "United States",
                        "organization": {
                            "name": "Quadex AI",
                            "website_url": "https://www.quadex.ai",
                        },
                    }
                ]
            },
        )

    client = ApolloClient(
        api_key="apollo-key",
        base_url="https://apollo.test/api/v1",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )

    person = run(client.match_person("alex@quadex.ai"))

    assert person.matched is True
    assert person.match_status == "matched"
    assert person.title == "Founder"
    assert person.organization_name == "Quadex AI"
    assert person.organization_domain == "quadex.ai"
    assert requests[0].headers["x-api-key"] == "apollo-key"
    run(client.close())


def test_health_check_uses_apollo_no_credit_auth_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/auth/health"
        assert request.headers["x-api-key"] == "apollo-key"
        return httpx.Response(200, json={"healthy": True, "is_logged_in": True})

    client = ApolloClient(
        api_key="apollo-key",
        base_url="https://apollo.test/api/v1",
        transport=httpx.MockTransport(handler),
    )

    check = run(client.health_check())

    assert check == {"configured": True, "verified": True, "status": "ok", "credit_cost": "none"}
    assert len(requests) == 1
    run(client.close())


def test_health_check_does_not_call_apollo_without_a_key() -> None:
    client = ApolloClient(api_key=None)

    assert run(client.health_check()) == {
        "configured": False,
        "verified": False,
        "status": "disabled",
        "credit_cost": "none",
    }
    run(client.close())


def test_people_search_uses_current_api_search_endpoint_without_revealing_email() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/mixed_people/api_search"
        assert request.url.params.get_list("person_titles[]") == ["CTO"]
        assert request.url.params.get_list("organization_num_employees_ranges[]") == ["11,20"]
        assert request.url.params.get_list("person_locations[]") == ["United States"]
        assert request.url.params.get_list("q_organization_keyword_tags[]") == []
        assert request.url.params["page"] == "1"
        assert request.url.params["per_page"] == "100"
        assert not request.content
        return httpx.Response(200, json={"people": [{"id": "person-1", "name": "Alex"}]})

    client = ApolloClient(
        api_key="apollo-key",
        base_url="https://apollo.test/api/v1",
        transport=httpx.MockTransport(handler),
    )

    people = run(
        client.search_people(
            {
                "person_titles": ["CTO"],
                "org_headcount_ranges": ["11,20"],
                "person_locations": ["United States"],
                "max_pages": 1,
                "per_page": 100,
            }
        )
    )

    assert people == [{"id": "person-1", "name": "Alex"}]
    assert len(requests) == 1
    run(client.close())


def test_people_search_diversifies_keywords_and_deduplicates_people() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        keyword = request.url.params.get("q_organization_keyword_tags[]")
        people = [{"id": "shared", "name": "Shared Founder"}]
        people.append({"id": f"only-{keyword}", "name": keyword})
        return httpx.Response(200, json={"people": people})

    client = ApolloClient(
        api_key="apollo-key",
        base_url="https://apollo.test/api/v1",
        transport=httpx.MockTransport(handler),
    )

    people = run(
        client.search_people(
            {
                "person_titles": ["CTO"],
                "person_seniorities": ["c_suite"],
                "org_keywords": ["coding agent", "sandbox"],
                "pages_per_keyword": 1,
                "max_results": 10,
                "per_page": 100,
            }
        )
    )

    assert len(requests) == 2
    assert {person["id"] for person in people} == {"shared", "only-coding agent", "only-sandbox"}
    shared = next(person for person in people if person["id"] == "shared")
    assert shared["_mint_search_keywords"] == ["coding agent", "sandbox"]
    assert requests[0].url.params.get_list("person_seniorities[]") == ["c_suite"]
    run(client.close())


def test_bulk_people_enrichment_returns_linkedin_profiles_without_contact_reveals() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v1/people/bulk_match"
        payload = json.loads(request.content)
        assert payload == {"details": [{"id": "person-1"}, {"id": "person-2"}]}
        assert "reveal_personal_emails" not in payload
        assert "reveal_phone_number" not in payload
        return httpx.Response(
            200,
            json={
                "credits_consumed": 2,
                "matches": [
                    {"id": "person-1", "linkedin_url": "https://linkedin.com/in/one"},
                    {"id": "person-2", "linkedin_url": "https://linkedin.com/in/two"},
                ],
            },
        )

    client = ApolloClient(
        api_key="apollo-key",
        base_url="https://apollo.test/api/v1",
        transport=httpx.MockTransport(handler),
    )

    profiles = run(client.enrich_people_for_linkedin(["person-1", "person-2", "person-1"]))

    assert profiles["person-1"]["linkedin_url"] == "https://linkedin.com/in/one"
    assert profiles["person-2"]["linkedin_url"] == "https://linkedin.com/in/two"
    assert len(requests) == 1
    run(client.close())


def test_enrich_org_retries_429_and_maps_org_fields() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.url.path == "/api/v1/organizations/enrich"
        assert request.method == "POST"
        assert json.loads(request.content)["domain"] == "quadex.ai"
        if attempts == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200,
            json={
                "organization": {
                    "name": "Quadex AI",
                    "domain": "quadex.ai",
                    "industry": "AI",
                    "estimated_num_employees": 42,
                    "founded_year": 2023,
                    "latest_funding_stage": "Seed",
                    "latest_funding_round_date": "2025-04-01",
                    "total_funding_printed": "$8M",
                    "organization_revenue_printed": "$1M-$10M",
                    "technology_names": ["Python", "Kubernetes"],
                    "employee_growth_percentages": {"six_month": 0.2, "twelve_month": 0.6},
                }
            },
        )

    client = ApolloClient(
        api_key="apollo-key",
        base_url="https://apollo.test/api/v1",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )

    org = run(client.enrich_org("quadex.ai"))

    assert attempts == 2
    assert org.domain == "quadex.ai"
    assert org.employee_count == 42
    assert org.funding_stage == "Seed"
    assert org.latest_funding_round_date == "2025-04-01"
    assert org.technologies == ["Python", "Kubernetes"]
    assert org.headcount_growth_6mo == 0.2
    assert org.headcount_growth_12mo == 0.6
    run(client.close())


def test_match_person_falls_back_to_single_match_when_bulk_payload_is_rejected() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v1/people/bulk_match":
            return httpx.Response(422, json={"error": "invalid details payload"})
        assert request.url.path == "/api/v1/people/match"
        payload = json.loads(request.content)
        assert payload["email"] == "alex@quadex.ai"
        return httpx.Response(
            200,
            json={
                "person": {
                    "email": "alex@quadex.ai",
                    "first_name": "Alex",
                    "last_name": "Rivera",
                    "title": "Founder",
                    "organization": {"name": "Quadex AI", "website_url": "https://quadex.ai"},
                }
            },
        )

    client = ApolloClient(
        api_key="apollo-key",
        base_url="https://apollo.test/api/v1",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,
    )

    person = run(client.match_person("alex@quadex.ai"))

    assert paths == ["/api/v1/people/bulk_match", "/api/v1/people/match"]
    assert person.matched is True
    assert person.organization_domain == "quadex.ai"
    run(client.close())
