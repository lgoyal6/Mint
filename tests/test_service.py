import asyncio

import pytest

from mint.backend.enrichment.service import EnrichmentService
from mint.backend.models import (
    ApolloOrgResult,
    ApolloPersonResult,
    EnrichmentInput,
    EnrichmentResult,
    ExaEnrichmentResult,
    ResolvedIdentity,
)


def run(coro):
    return asyncio.run(coro)


def exa_result(domain: str = "quadex.ai", data_quality: str = "rich") -> ExaEnrichmentResult:
    return ExaEnrichmentResult(
        what_they_do=f"{domain} builds AI infrastructure.",
        target_vertical="agentic",
        stage="early-revenue",
        revenue_signals="Pricing page.",
        funding_signals="Seed funded.",
        inference_signals="Hosts model workflows.",
        spend_signals="Hiring infra engineers.",
        person_role_signals="Founder likely buyer.",
        data_quality=data_quality,
    )


def resolved_identity(
    *,
    domain: str | None = "quadex.ai",
    confidence: str = "high",
    affiliation_state: str = "verified",
) -> ResolvedIdentity:
    company_state = "verified" if domain else "unresolved"
    company_source = "email_domain" if domain else "none"
    if confidence == "low":
        company_state = "inferred"
        company_source = "exa_company_search"
        affiliation_state = "unverified"
    if confidence == "flagged":
        company_state = "unresolved"
        company_source = "none"
        affiliation_state = "contradicted"
        domain = None
    return ResolvedIdentity(
        person_source="apollo",
        name="Alex Rivera",
        title="Founder",
        seniority="founder",
        company_source=company_source,
        domain=domain,
        company_name="Quadex AI" if domain else None,
        affiliation_state=affiliation_state,
        confidence=confidence,
        evidence={
            "person_state": "verified",
            "company_state": company_state,
            "chain": [f"-> confidence: {confidence}"],
        },
    )


class FakeApollo:
    def __init__(self) -> None:
        self.person_calls: list[str] = []
        self.linkedin_urls: list[str | None] = []
        self.org_calls: list[str] = []

    async def match_person(
        self,
        email: str,
        first_name=None,
        last_name=None,
        company_name=None,
        linkedin_url=None,
    ):
        self.person_calls.append(email)
        self.linkedin_urls.append(linkedin_url)
        return ApolloPersonResult(
            matched=email != "unknown@gmail.com",
            email=email,
            first_name=first_name,
            last_name=last_name,
            title=None if email == "unknown@gmail.com" else "Founder",
            organization_name=company_name or "Quadex AI",
            organization_domain="quadex.ai" if email.endswith("@quadex.ai") else None,
            linkedin_url="https://linkedin.com/in/alex-rivera" if email != "unknown@gmail.com" else None,
        )

    async def enrich_org(self, domain: str):
        self.org_calls.append(domain)
        return ApolloOrgResult(domain=domain, name="Quadex AI", employee_count=20)

    async def close(self):
        pass


class BrokenApollo(FakeApollo):
    async def match_person(self, email: str, first_name=None, last_name=None, company_name=None):
        raise RuntimeError("apollo unavailable")


class FakeExa:
    def __init__(self, fail_deep: bool = False) -> None:
        self.fail_deep = fail_deep
        self.deep_calls: list[tuple[str, str | None, dict]] = []

    async def deep_research(self, company_name: str, domain: str | None, lead_context: dict):
        self.deep_calls.append((company_name, domain, lead_context))
        if self.fail_deep:
            raise RuntimeError("exa unavailable")
        return exa_result(domain or company_name)

    async def close(self):
        pass


class FakeResolver:
    def __init__(self, resolution: ResolvedIdentity) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.resolution = resolution
        self.people_results = [{"title": "Alex Rivera - Founder", "url": "https://linkedin.com/in/example"}]

    async def resolve(self, email: str, raw_company: str | None, apollo_person, **kwargs):
        self.calls.append((email, raw_company))
        return self.resolution

    def people_search_results_for(self, email: str, raw_company: str | None):
        return self.people_results


class FreshStorage:
    def __init__(self, *, apollo_age_days: float = 0.0, exa_present: bool = True) -> None:
        from datetime import UTC, datetime, timedelta

        self.freshness = type(
            "Freshness",
            (),
            {
                "email": "alex@quadex.ai",
                "apollo_enriched_at": datetime.now(UTC) - timedelta(days=apollo_age_days),
                "exa_enriched_at": datetime.now(UTC) if exa_present else None,
            },
        )()
        self.saved: list[tuple[EnrichmentInput, EnrichmentResult]] = []

    async def get_freshness(self, email: str):
        return self.freshness

    async def save(self, input: EnrichmentInput, result: EnrichmentResult) -> None:
        self.saved.append((input, result))


class FreshCompanyStorage:
    def __init__(
        self, *, age_days: float = 0.0, exa_data_quality: str = "rich", exa_present: bool = True
    ) -> None:
        from datetime import UTC, datetime, timedelta

        enriched_at = datetime.now(UTC) - timedelta(days=age_days)
        self.company_freshness = type(
            "CompanyFreshness",
            (),
            {
                "domain": "quadex.ai",
                "apollo_enriched_at": enriched_at,
                "exa_enriched_at": enriched_at if exa_present else None,
                "exa_data_quality": exa_data_quality,
            },
        )()
        self.saved: list[tuple[EnrichmentInput, EnrichmentResult]] = []

    async def get_freshness(self, email: str):
        return None

    async def get_company_freshness(self, domain: str):
        return self.company_freshness

    async def save(self, input: EnrichmentInput, result: EnrichmentResult) -> None:
        self.saved.append((input, result))


def test_enrich_lead_returns_partial_result_when_exa_fails() -> None:
    apollo = FakeApollo()
    exa = FakeExa(fail_deep=True)
    service = EnrichmentService(apollo=apollo, exa=exa, resolver=FakeResolver(resolved_identity()))
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", last_name="Rivera", company_name="Quadex AI")

    result = run(service.enrich_lead(lead))

    assert result.resolution.confidence == "high"
    assert result.enriched is True
    assert result.person is not None
    assert result.company_apollo is not None
    assert result.company_exa is None
    assert result.data_quality == "moderate"
    assert "apollo_person" in result.enrichment_sources
    assert "apollo_org" in result.enrichment_sources
    assert result.errors == ["exa_deep: exa unavailable"]


def test_enrich_lead_passes_supplied_linkedin_profile_to_apollo() -> None:
    apollo = FakeApollo()
    service = EnrichmentService(
        apollo=apollo,
        exa=FakeExa(),
        resolver=FakeResolver(resolved_identity()),
    )
    lead = EnrichmentInput(
        email="alex@gmail.com",
        first_name="Alex",
        last_name="Rivera",
        company_name="Quadex AI",
        linkedin_url="https://www.linkedin.com/in/alex-rivera",
    )

    run(service.enrich_lead(lead))

    assert apollo.linkedin_urls == ["https://www.linkedin.com/in/alex-rivera"]


def test_enrich_lead_records_apollo_person_error_without_success_source() -> None:
    service = EnrichmentService(
        apollo=BrokenApollo(),
        exa=FakeExa(),
        resolver=FakeResolver(resolved_identity(domain=None, confidence="low")),
    )
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", last_name="Rivera")

    result = run(service.enrich_lead(lead))

    assert "apollo_person" not in result.enrichment_sources
    assert result.person is not None
    assert result.person.matched is False
    assert result.enriched is False
    assert result.errors == ["apollo_person: apollo unavailable"]


@pytest.mark.parametrize("confidence", ["high", "medium"])
def test_high_and_medium_confidence_run_company_enrichment(confidence: str) -> None:
    exa = FakeExa()
    service = EnrichmentService(
        apollo=FakeApollo(),
        exa=exa,
        resolver=FakeResolver(
            resolved_identity(
                confidence=confidence,
                affiliation_state="verified" if confidence == "high" else "inferred",
            )
        ),
    )
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", last_name="Rivera", company_name="Quadex AI")

    result = run(service.enrich_lead(lead))

    assert result.enriched is True
    assert result.company_apollo is not None
    assert result.company_exa is not None
    assert exa.deep_calls[0][1] == "quadex.ai"
    assert exa.deep_calls[0][2]["resolved_confidence"] == confidence


def test_low_confidence_self_reported_company_runs_company_enrichment() -> None:
    apollo = FakeApollo()
    exa = FakeExa()
    service = EnrichmentService(
        apollo=apollo,
        exa=exa,
        resolver=FakeResolver(resolved_identity(domain="wobblegenomics.com", confidence="low")),
    )
    lead = EnrichmentInput(
        email="unknown@gmail.com",
        first_name="Lucas",
        last_name="Chen",
        company_name="Wobble",
    )

    result = run(service.enrich_lead(lead))

    assert result.resolution.confidence == "low"
    assert result.enriched is True
    assert result.company_apollo is not None
    assert result.company_exa is not None
    assert apollo.org_calls == ["wobblegenomics.com"]
    assert exa.deep_calls[0][0] == "Quadex AI"
    assert exa.deep_calls[0][1] == "wobblegenomics.com"
    assert exa.deep_calls[0][2]["company_name"] == "Quadex AI"
    assert exa.deep_calls[0][2]["resolved_confidence"] == "low"


def test_flagged_confidence_skips_company_enrichment() -> None:
    apollo = FakeApollo()
    exa = FakeExa()
    service = EnrichmentService(
        apollo=apollo,
        exa=exa,
        resolver=FakeResolver(resolved_identity(domain="wobblegenomics.com", confidence="flagged")),
    )
    lead = EnrichmentInput(
        email="unknown@gmail.com",
        first_name="Lucas",
        last_name="Chen",
        company_name="Wobble",
    )

    result = run(service.enrich_lead(lead))

    assert result.resolution.confidence == "flagged"
    assert result.enriched is False
    assert result.company_apollo is None
    assert result.company_exa is None
    assert apollo.org_calls == []
    assert exa.deep_calls == []


def test_people_search_from_resolve_is_reused_without_new_service_call() -> None:
    service = EnrichmentService(
        apollo=FakeApollo(),
        exa=FakeExa(),
        resolver=FakeResolver(resolved_identity(confidence="low")),
    )
    lead = EnrichmentInput(email="unknown@gmail.com", first_name="Alex", last_name="Rivera", company_name="Quadex AI")

    result = run(service.enrich_lead(lead))

    assert result.people_search == [{"title": "Alex Rivera - Founder", "url": "https://linkedin.com/in/example"}]
    assert "exa_people" in result.enrichment_sources


def test_data_quality_and_resolution_confidence_are_separate_axes() -> None:
    result = run(
        EnrichmentService(
            apollo=FakeApollo(),
            exa=FakeExa(),
            resolver=FakeResolver(resolved_identity(confidence="low")),
        ).enrich_lead(
            EnrichmentInput(email="unknown@gmail.com", first_name="Lucas", last_name="Chen", company_name="Wobble")
        )
    )

    assert result.resolution.confidence == "low"
    assert result.data_quality == "rich"


def test_enrich_batch_deduplicates_email_and_company_enrichment_in_input_order() -> None:
    apollo = FakeApollo()
    exa = FakeExa()
    service = EnrichmentService(apollo=apollo, exa=exa, resolver=FakeResolver(resolved_identity()))
    inputs = [
        EnrichmentInput(email="alex@quadex.ai", first_name="Alex", company_name="Quadex AI"),
        EnrichmentInput(email="blair@quadex.ai", first_name="Blair", company_name="Quadex AI"),
        EnrichmentInput(email="ALEX@quadex.ai", first_name="Alex", company_name="Quadex AI"),
    ]

    results = run(service.enrich_batch(inputs, max_concurrent=3))

    assert [result.person.email for result in results if result.person] == [
        "alex@quadex.ai",
        "blair@quadex.ai",
        "alex@quadex.ai",
    ]
    assert all(result.resolution.domain == "quadex.ai" for result in results)
    assert apollo.person_calls == ["alex@quadex.ai", "blair@quadex.ai"]
    assert apollo.org_calls == ["quadex.ai"]
    assert [call[1] for call in exa.deep_calls] == ["quadex.ai"]


def test_enrich_batch_reports_progress_per_unique_email() -> None:
    service = EnrichmentService(apollo=FakeApollo(), exa=FakeExa(), resolver=FakeResolver(resolved_identity()))
    inputs = [
        EnrichmentInput(email="alex@quadex.ai", first_name="Alex", company_name="Quadex AI"),
        EnrichmentInput(email="blair@quadex.ai", first_name="Blair", company_name="Quadex AI"),
        EnrichmentInput(email="ALEX@quadex.ai", first_name="Alex", company_name="Quadex AI"),
    ]
    updates: list[tuple[int, int]] = []

    async def on_progress(done: int, total: int) -> None:
        updates.append((done, total))

    run(service.enrich_batch(inputs, max_concurrent=3, on_progress=on_progress))

    assert len(updates) == 3
    assert updates[0] == (0, 2)
    assert sorted(done for done, _ in updates[1:]) == [1, 2]
    assert all(total == 2 for _, total in updates)


def test_fresh_company_skips_company_fetch_but_keeps_person_enrichment() -> None:
    storage = FreshCompanyStorage()
    apollo = FakeApollo()
    exa = FakeExa()
    service = EnrichmentService(
        apollo=apollo,
        exa=exa,
        resolver=FakeResolver(resolved_identity()),
        storage=storage,
    )
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", company_name="Quadex AI")

    result = run(service.enrich_lead(lead))

    assert result.enriched is True
    assert apollo.person_calls == ["alex@quadex.ai"]
    assert apollo.org_calls == []
    assert exa.deep_calls == []
    assert "company_fresh" in result.enrichment_sources
    assert result.data_quality == "rich"


def test_stale_company_runs_company_fetch() -> None:
    storage = FreshCompanyStorage(age_days=45.0)
    apollo = FakeApollo()
    exa = FakeExa()
    service = EnrichmentService(
        apollo=apollo,
        exa=exa,
        resolver=FakeResolver(resolved_identity()),
        storage=storage,
    )
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", company_name="Quadex AI")

    result = run(service.enrich_lead(lead))

    assert apollo.org_calls == ["quadex.ai"]
    assert [call[1] for call in exa.deep_calls] == ["quadex.ai"]
    assert "company_fresh" not in result.enrichment_sources


def test_enrich_resolved_lead_reuses_fresh_company() -> None:
    storage = FreshCompanyStorage()
    apollo = FakeApollo()
    exa = FakeExa()
    service = EnrichmentService(
        apollo=apollo,
        exa=exa,
        resolver=FakeResolver(resolved_identity()),
        storage=storage,
    )
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", company_name="Quadex AI")

    result = run(service.enrich_resolved_lead(lead, resolved_identity()))

    assert result.enriched is True
    assert apollo.org_calls == []
    assert exa.deep_calls == []
    assert "company_fresh" in result.enrichment_sources


def test_enrich_resolved_lead_can_refresh_the_apollo_person_profile() -> None:
    storage = FreshCompanyStorage()
    apollo = FakeApollo()
    service = EnrichmentService(
        apollo=apollo,
        exa=FakeExa(),
        resolver=FakeResolver(resolved_identity()),
        storage=storage,
    )
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", company_name="Quadex AI")

    result = run(service.enrich_resolved_lead(lead, resolved_identity(), refresh_person=True))

    assert apollo.person_calls == ["alex@quadex.ai"]
    assert apollo.org_calls == []
    assert result.person is not None
    assert result.person.linkedin_url == "https://linkedin.com/in/alex-rivera"
    assert "apollo_person" in result.enrichment_sources


def test_fresh_storage_record_skips_enrichment_and_logs_skip() -> None:
    storage = FreshStorage()
    apollo = FakeApollo()
    exa = FakeExa()
    service = EnrichmentService(
        apollo=apollo,
        exa=exa,
        resolver=FakeResolver(resolved_identity()),
        storage=storage,
    )
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", company_name="Quadex AI")

    result = run(service.enrich_lead(lead))

    assert result.enriched is False
    assert result.enrichment_sources == ["skipped_fresh"]
    assert apollo.person_calls == []
    assert exa.deep_calls == []


def test_fresh_apollo_with_null_exa_still_skips_person_enrichment() -> None:
    # Regression for the credit leak: a person with a fresh Apollo timestamp but
    # no Exa timestamp (Exa never ran for them) must still short-circuit. The old
    # logic required a non-null Exa timestamp, so these people were re-charged on
    # Apollo every sync (~67x re-enrichment per person).
    storage = FreshStorage(exa_present=False)
    apollo = FakeApollo()
    exa = FakeExa()
    service = EnrichmentService(
        apollo=apollo,
        exa=exa,
        resolver=FakeResolver(resolved_identity()),
        storage=storage,
    )
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", company_name="Quadex AI")

    result = run(service.enrich_lead(lead))

    assert result.enrichment_sources == ["skipped_fresh"]
    assert apollo.person_calls == []
    assert exa.deep_calls == []


def test_stale_apollo_reenriches_even_when_exa_null() -> None:
    # Apollo TTL is still respected: past 90 days, re-enrich regardless of Exa.
    storage = FreshStorage(apollo_age_days=120.0, exa_present=False)
    apollo = FakeApollo()
    exa = FakeExa()
    service = EnrichmentService(
        apollo=apollo,
        exa=exa,
        resolver=FakeResolver(resolved_identity()),
        storage=storage,
    )
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", company_name="Quadex AI")

    result = run(service.enrich_lead(lead))

    assert result.enrichment_sources != ["skipped_fresh"]
    assert apollo.person_calls == ["alex@quadex.ai"]


def test_fresh_company_with_null_exa_skips_company_fetch() -> None:
    # Same leak on the company path: a fresh Apollo org timestamp with null Exa
    # must skip the paid organizations/enrich call (~6x company re-enrichment).
    storage = FreshCompanyStorage(exa_present=False, exa_data_quality="")
    apollo = FakeApollo()
    exa = FakeExa()
    service = EnrichmentService(
        apollo=apollo,
        exa=exa,
        resolver=FakeResolver(resolved_identity()),
        storage=storage,
    )
    lead = EnrichmentInput(email="alex@quadex.ai", first_name="Alex", company_name="Quadex AI")

    result = run(service.enrich_lead(lead))

    assert apollo.org_calls == []
    assert "company_fresh" in result.enrichment_sources
    assert storage.saved == [(lead, result)]
