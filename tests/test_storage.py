import asyncio
import importlib.util
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mint.backend.db.base import Base
from mint.backend.db.read_storage import APOLLO_USAGE_SOURCES, EXA_USAGE_SOURCES, USAGE_SOURCES
from mint.backend.db.storage import EnrichmentStorage
from mint.backend.db.tables import Company, EnrichmentLog, Person
from mint.backend.enrichment.usage import begin_usage, end_usage, record_usage
from mint.backend.settings import Settings
from mint.backend.models import (
    ApolloOrgResult,
    ApolloPersonResult,
    CandidateCompany,
    EnrichmentInput,
    EnrichmentResult,
    ExaEnrichmentResult,
    ResolvedIdentity,
)


def run(coro):
    return asyncio.run(coro)


def storage_database_url() -> str:
    url = Settings().mint_test_database_url
    if not url:
        pytest.skip("MINT_TEST_DATABASE_URL is not set")
    if importlib.util.find_spec("asyncpg") is None:
        pytest.skip("asyncpg is not installed")
    return url


@pytest.fixture()
def storage():
    url = storage_database_url()

    async def setup():
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return engine, session_factory, EnrichmentStorage(session_factory)

    engine, session_factory, adapter = run(setup())
    try:
        yield adapter, session_factory
    finally:
        async def teardown():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

        run(teardown())


def lead(email: str = "alex@quadex.ai") -> EnrichmentInput:
    return EnrichmentInput(
        email=email,
        first_name="Alex",
        last_name="Rivera",
        company_name="Quadex AI",
        title="Founder",
        what_they_are_building="coding agents",
        source="csv",
        event_id="evt_1",
        survey_responses={"Company": "Quadex AI"},
    )


def test_core_enrichment_save_survives_optional_usage_log_failure() -> None:
    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def begin(self):
            return Transaction()

    adapter = EnrichmentStorage(lambda: Session())
    adapter._upsert_company = AsyncMock(return_value=None)
    adapter._upsert_person = AsyncMock(return_value=uuid4())
    adapter._insert_logs = AsyncMock(side_effect=RuntimeError("usage log schema unavailable"))

    input_lead = lead()
    result = enriched_result()
    run(adapter.save(input_lead, result))

    adapter._upsert_company.assert_awaited_once()
    assert len(adapter._upsert_company.await_args.args) == 4
    assert adapter._upsert_company.await_args.args[1] is input_lead
    assert adapter._upsert_company.await_args.args[2] is result
    adapter._upsert_person.assert_awaited_once()
    adapter._insert_logs.assert_awaited_once()


def resolution(
    *,
    domain: str | None = "quadex.ai",
    confidence: str = "high",
    affiliation_state: str = "verified",
) -> ResolvedIdentity:
    return ResolvedIdentity(
        person_source="apollo",
        name="Alex Rivera",
        title="Founder",
        seniority="founder",
        company_source="email_domain" if domain else "none",
        domain=domain,
        company_name="Quadex AI" if domain else None,
        affiliation_state=affiliation_state,
        confidence=confidence,
        evidence={
            "person_state": "verified",
            "company_state": "verified" if domain else "unresolved",
            "chain": ["Apollo matched person by email", f"-> confidence: {confidence}"],
        },
        candidate_companies=[
            CandidateCompany(domain="acme.com", name="Acme", source="apollo_org", note="Apollo employer")
        ]
        if confidence == "flagged"
        else [],
    )


def apollo_person(email: str = "alex@quadex.ai") -> ApolloPersonResult:
    return ApolloPersonResult(
        matched=True,
        match_status="matched",
        email=email,
        first_name="Alex",
        last_name="Rivera",
        name="Alex Rivera",
        title="Founder",
        seniority="founder",
        departments=["engineering"],
        linkedin_url="https://linkedin.com/in/alex",
        location="San Francisco",
        organization_name="Quadex AI",
        organization_domain="quadex.ai",
        raw_data={"requestId": "apollo-person-1", "person": {"id": "p1"}},
    )


def apollo_org(domain: str = "quadex.ai") -> ApolloOrgResult:
    return ApolloOrgResult(
        domain=domain,
        name="Quadex AI",
        industry="software",
        employee_count=24,
        founded_year=2024,
        funding_stage="Seed",
        total_funding="$4M",
        revenue="$1M",
        technologies=["Python", "Kubernetes"],
        headcount_growth_6mo=0.25,
        headcount_growth_12mo=0.5,
        raw_data={"requestId": "apollo-org-1", "organization": {"id": "o1"}},
    )


def exa_enrichment(data_quality: str = "rich") -> ExaEnrichmentResult:
    return ExaEnrichmentResult(
        what_they_do="Quadex builds autonomous coding agents for enterprises.",
        target_vertical="agentic",
        business_model="SaaS",
        stage="early-revenue",
        revenue_signals="Pricing page and customer logos.",
        customer_signals="Enterprise pilots.",
        funding_signals="Seed round.",
        inference_signals="Long-running agent inference workloads.",
        spend_signals="$10K+/mo likely inference spend.",
        competitors_or_current_infra="Uses AWS today.",
        tech_stack=["Python", "Kubernetes"],
        hiring_signals=["Hiring ML infrastructure engineer"],
        recent_activity=["Launched enterprise plan"],
        person_role_signals="Founder likely owns infra budget.",
        data_quality=data_quality,
        exa_grounding=[{"url": "https://quadex.ai"}],
        raw_data={"requestId": "exa-deep-1", "output": {"content": {}}},
    )


def enriched_result(email: str = "alex@quadex.ai") -> EnrichmentResult:
    return EnrichmentResult(
        resolution=resolution(),
        person=apollo_person(email),
        company_apollo=apollo_org(),
        company_exa=exa_enrichment(),
        people_search=[{"title": "Alex Rivera - Founder at Quadex AI"}],
        data_quality="rich",
        enriched=True,
        enrichment_sources=["apollo_person", "exa_people", "apollo_org", "exa_deep"],
        errors=[],
    )


def test_provider_usage_logs_are_attributed_to_the_event() -> None:
    class FakeSession:
        rows: list[dict] | None = None

        async def execute(self, _statement, rows):
            self.rows = rows

    adapter = EnrichmentStorage(None)
    session = FakeSession()
    input_lead = lead()
    event_id = uuid4()
    input_lead.event_id = str(event_id)
    usage_token = begin_usage()
    try:
        record_usage("apollo", "organization_enrichment", credits=1)
        record_usage("exa", "deep_search", estimated_cost_usd="0.017")
        run(adapter._insert_logs(session, input_lead, enriched_result(), uuid4(), uuid4()))
    finally:
        end_usage(usage_token)

    assert session.rows is not None
    assert [row["source"] for row in session.rows] == ["apollo", "exa"]
    assert all(row["event_id"] == event_id for row in session.rows)
    assert session.rows[0]["credits"] == 1
    assert session.rows[1]["estimated_cost_usd"] == Decimal("0.017")


def test_usage_monitor_includes_legacy_and_current_provider_log_sources() -> None:
    assert {"apollo", "apollo_person", "apollo_org"}.issubset(APOLLO_USAGE_SOURCES)
    assert {"exa", "exa_deep", "exa_people"}.issubset(EXA_USAGE_SOURCES)
    assert set(APOLLO_USAGE_SOURCES).isdisjoint(EXA_USAGE_SOURCES)
    assert set(USAGE_SOURCES) == set(APOLLO_USAGE_SOURCES) | set(EXA_USAGE_SOURCES)


def test_save_round_trips_company_person_and_logs(storage) -> None:
    adapter, session_factory = storage

    run(adapter.save(lead(), enriched_result()))

    async def assertions():
        async with session_factory() as session:
            person = (await session.execute(select(Person).where(Person.email == "alex@quadex.ai"))).scalar_one()
            company = (await session.execute(select(Company).where(Company.domain == "quadex.ai"))).scalar_one()
            log_count = await session.scalar(select(func.count()).select_from(EnrichmentLog))

            assert person.company_id == company.id
            assert person.resolution_confidence == "high"
            assert person.resolution_evidence == {
                "person_state": "verified",
                "company_state": "verified",
                "chain": ["Apollo matched person by email", "-> confidence: high"],
            }
            assert person.apollo_person_title == "Founder"
            assert person.exa_person_role_signals == "Founder likely owns infra budget."
            assert person.exa_people_search_results == [{"title": "Alex Rivera - Founder at Quadex AI"}]
            assert person.self_reported_building == "coding agents"
            assert company.apollo_org_employee_count == 24
            assert company.exa_enrichment["target_vertical"] == "agentic"
            assert company.exa_what_they_do.startswith("Quadex builds")
            assert company.exa_target_vertical == "agentic"
            assert company.exa_stage == "early-revenue"
            assert company.exa_data_quality == "rich"
            assert company.exa_grounding == [{"url": "https://quadex.ai"}]
            assert log_count == 4

    run(assertions())


def test_save_strips_postgres_null_characters_from_nested_payloads(storage) -> None:
    adapter, session_factory = storage
    input_lead = lead()
    result = enriched_result()
    result.person.raw_data["person"]["bio"] = "Founder\x00builder"
    result.company_apollo.raw_data["organization"]["description"] = "AI\x00company"
    result.company_exa.what_they_do = "Quadex builds\x00 coding agents."
    result.company_exa.raw_data["output"]["content"] = {"summary": "GPU\x00workloads"}
    result.people_search = [{"title": "Alex\x00 Rivera - Founder"}]
    result.errors = ["exa_deep: bad\x00 payload"]

    run(adapter.save(input_lead, result))

    async def assertions():
        async with session_factory() as session:
            person = (await session.execute(select(Person).where(Person.email == "alex@quadex.ai"))).scalar_one()
            company = (await session.execute(select(Company).where(Company.domain == "quadex.ai"))).scalar_one()
            error_log = (
                await session.execute(
                    select(EnrichmentLog).where(EnrichmentLog.status == "failed")
                )
            ).scalar_one()

            assert "\x00" not in person.apollo_person_data["person"]["bio"]
            assert "\x00" not in person.exa_people_search_results[0]["title"]
            assert "\x00" not in company.apollo_org_data["organization"]["description"]
            assert "\x00" not in company.exa_what_they_do
            assert "\x00" not in company.exa_deep_raw["output"]["content"]["summary"]
            assert "\x00" not in error_log.error_message

    run(assertions())


def test_upsert_updates_enrichment_fields_and_preserves_first_seen(storage) -> None:
    adapter, session_factory = storage
    run(adapter.save(lead(), enriched_result()))

    async def before():
        async with session_factory() as session:
            person = (await session.execute(select(Person).where(Person.email == "alex@quadex.ai"))).scalar_one()
            return person.first_seen_at

    first_seen = run(before())

    updated = enriched_result()
    updated.person.title = "CEO"
    updated.company_apollo.employee_count = 42
    updated.company_exa.data_quality = "moderate"
    updated.data_quality = "moderate"
    run(adapter.save(lead(), updated))

    async def assertions():
        async with session_factory() as session:
            person = (await session.execute(select(Person).where(Person.email == "alex@quadex.ai"))).scalar_one()
            company = (await session.execute(select(Company).where(Company.domain == "quadex.ai"))).scalar_one()
            person_count = await session.scalar(select(func.count()).select_from(Person))

            assert person_count == 1
            assert person.first_seen_at == first_seen
            assert person.apollo_person_title == "CEO"
            assert person.enrichment_data_quality == "moderate"
            assert company.apollo_org_employee_count == 42
            assert company.exa_data_quality == "moderate"

    run(assertions())


def test_human_resolved_affiliation_survives_later_flagged_enrichment(storage) -> None:
    adapter, session_factory = storage
    initial = enriched_result("manhbaonguyen@google.com")
    initial.resolution = resolution(domain="google.com", confidence="flagged", affiliation_state="contradicted")
    initial.resolution.company_name = "Google"
    initial.resolution.candidate_companies = [
        CandidateCompany(domain="google.com", name="Google", source="email_domain", note="Non-personal email domain"),
        CandidateCompany(domain="waymo.com", name="Waymo", source="exa_company_search", note="Self-reported company"),
    ]
    initial.company_apollo = apollo_org("google.com")
    initial.company_apollo.name = "Google"
    initial.company_exa = exa_enrichment()
    run(adapter.save(lead("manhbaonguyen@google.com"), initial))

    async def manually_resolve():
        async with session_factory() as session:
            async with session.begin():
                person = (
                    await session.execute(select(Person).where(Person.email == "manhbaonguyen@google.com"))
                ).scalar_one()
                google = (await session.execute(select(Company).where(Company.domain == "google.com"))).scalar_one()
                evidence = dict(person.resolution_evidence or {})
                evidence["human_resolution"] = {
                    "domain": "google.com",
                    "company_name": "Google",
                    "resolved_at": datetime.now(UTC).isoformat(),
                }
                await session.execute(
                    Person.__table__.update()
                    .where(Person.id == person.id)
                    .values(
                        company_id=google.id,
                        affiliation_state="verified",
                        resolution_confidence="high",
                        resolution_evidence=evidence,
                        candidate_companies=[],
                    )
                )
                return google.id

    google_id = run(manually_resolve())

    later = enriched_result("manhbaonguyen@google.com")
    later.resolution = resolution(domain="waymo.com", confidence="flagged", affiliation_state="contradicted")
    later.resolution.company_name = "Waymo"
    later.resolution.candidate_companies = [
        CandidateCompany(domain="google.com", name="Google", source="email_domain", note="Non-personal email domain"),
        CandidateCompany(domain="waymo.com", name="Waymo", source="exa_company_search", note="Self-reported company"),
    ]
    later.company_apollo = apollo_org("waymo.com")
    later.company_apollo.name = "Waymo"
    later.company_exa = exa_enrichment()
    run(adapter.save(lead("manhbaonguyen@google.com"), later))

    async def assertions():
        async with session_factory() as session:
            person = (
                await session.execute(select(Person).where(Person.email == "manhbaonguyen@google.com"))
            ).scalar_one()
            waymo = (await session.execute(select(Company).where(Company.domain == "waymo.com"))).scalar_one()

            assert waymo.name == "Waymo"
            assert person.company_id == google_id
            assert person.affiliation_state == "verified"
            assert person.resolution_confidence == "high"
            assert person.resolution_evidence["human_resolution"]["domain"] == "google.com"
            assert person.candidate_companies == []
            assert person.apollo_person_title == "Founder"

    run(assertions())


def test_unverified_result_writes_person_without_company(storage) -> None:
    adapter, session_factory = storage
    result = EnrichmentResult(
        resolution=resolution(domain="wobblegenomics.com", confidence="low", affiliation_state="unverified"),
        person=apollo_person("lucas@gmail.com"),
        data_quality="sparse",
        enriched=False,
        enrichment_sources=["apollo_person"],
        errors=[],
    )

    run(adapter.save(lead("lucas@gmail.com"), result))

    async def assertions():
        async with session_factory() as session:
            person = (await session.execute(select(Person).where(Person.email == "lucas@gmail.com"))).scalar_one()
            company_count = await session.scalar(select(func.count()).select_from(Company))

            assert person.company_id is None
            assert person.resolution_confidence == "low"
            assert company_count == 0

    run(assertions())


def test_verified_result_links_person_to_company(storage) -> None:
    adapter, session_factory = storage

    run(adapter.save(lead(), enriched_result()))

    async def assertions():
        async with session_factory() as session:
            person = (await session.execute(select(Person).where(Person.email == "alex@quadex.ai"))).scalar_one()
            assert person.company_id is not None

    run(assertions())


def test_get_freshness_returns_person_timestamps(storage) -> None:
    adapter, _ = storage
    run(adapter.save(lead(), enriched_result()))

    freshness = run(adapter.get_freshness("alex@quadex.ai"))

    assert freshness is not None
    assert freshness.email == "alex@quadex.ai"
    assert freshness.apollo_enriched_at is not None
    assert freshness.exa_enriched_at is not None
    assert freshness.apollo_enriched_at <= datetime.now(UTC)


def test_get_company_freshness_returns_company_timestamps(storage) -> None:
    adapter, _ = storage
    run(adapter.save(lead(), enriched_result()))

    freshness = run(adapter.get_company_freshness("quadex.ai"))
    missing = run(adapter.get_company_freshness("unknown.ai"))

    assert freshness is not None
    assert freshness.domain == "quadex.ai"
    assert freshness.apollo_enriched_at is not None
    assert freshness.exa_enriched_at is not None
    assert freshness.exa_data_quality == "rich"
    assert missing is None


def test_skipped_fresh_result_only_appends_log(storage) -> None:
    adapter, session_factory = storage
    original = enriched_result()
    run(adapter.save(lead(), original))

    skipped = EnrichmentResult(
        resolution=resolution(domain=None, confidence="low", affiliation_state="unverified"),
        data_quality="sparse",
        enriched=False,
        enrichment_sources=["skipped_fresh"],
        errors=[],
    )
    run(adapter.save(lead(), skipped))

    async def assertions():
        async with session_factory() as session:
            person = (await session.execute(select(Person).where(Person.email == "alex@quadex.ai"))).scalar_one()
            skipped_log = (
                await session.execute(
                    select(EnrichmentLog).where(
                        EnrichmentLog.entity_id == person.id,
                        EnrichmentLog.status == "skipped_fresh",
                    )
                )
            ).scalar_one()

            assert person.resolution_confidence == "high"
            assert skipped_log.source == "skipped_fresh"

    run(assertions())
