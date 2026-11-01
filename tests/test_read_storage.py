import asyncio
import importlib.util
from uuid import UUID

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mint.backend.db.base import Base
from mint.backend.db.read_storage import LeadFilters, ReadStorage, _location_from_payload, _region_for_location
from mint.backend.db.tables import Classification, Company, Event, Person, Registration
from mint.backend.settings import Settings


def run(coro):
    return asyncio.run(coro)


def read_database_url() -> str:
    url = Settings().mint_test_database_url
    if not url:
        pytest.skip("MINT_TEST_DATABASE_URL is not set")
    if importlib.util.find_spec("asyncpg") is None:
        pytest.skip("asyncpg is not installed")
    return url


@pytest.fixture()
def read_storage():
    url = read_database_url()

    async def setup():
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        ids = await seed_read_rows(session_factory)
        return engine, session_factory, ReadStorage(session_factory), ids

    engine, session_factory, adapter, ids = run(setup())
    try:
        yield adapter, session_factory, ids
    finally:
        async def teardown():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

        run(teardown())


async def seed_read_rows(session_factory):
    async with session_factory() as session:
        async with session.begin():
            company_id = (
                await session.execute(
                    insert(Company)
                    .values(
                        domain="agentco.ai",
                        name="AgentCo",
                        apollo_org_industry="computer software",
                        apollo_org_employee_count=42,
                        apollo_org_funding_stage="Series A",
                        apollo_org_revenue="$2M",
                        exa_what_they_do="AgentCo builds production agent infrastructure.",
                        exa_target_vertical="agentic",
                        exa_stage="growth",
                        exa_data_quality="rich",
                        exa_enrichment={
                            "what_they_do": "AgentCo builds production agent infrastructure.",
                            "inference_signals": "Production API inference traffic.",
                            "person_role_signals": "Founder is an infrastructure buyer.",
                            "data_quality": "rich",
                        },
                    )
                    .returning(Company.id)
                )
            ).scalar_one()
            second_company_id = (
                await session.execute(
                    insert(Company)
                    .values(
                        domain="reviewco.ai",
                        name="ReviewCo",
                        exa_target_vertical="agentic",
                        exa_stage="unknown",
                        exa_data_quality="moderate",
                        exa_enrichment={"what_they_do": "ReviewCo builds agents."},
                    )
                    .returning(Company.id)
                )
            ).scalar_one()
            event_id = (
                await session.execute(
                    insert(Event)
                    .values(name="Hard Problems", luma_event_id="evt-hard", status="past", guest_count=4)
                    .returning(Event.id)
                )
            ).scalar_one()
            second_event_id = (
                await session.execute(
                    insert(Event)
                    .values(name="Agent Summit", luma_event_id="evt-agent", status="past", guest_count=2)
                    .returning(Event.id)
                )
            ).scalar_one()
            person_id = (
                await session.execute(
                    insert(Person)
                    .values(
                        email="founder@agentco.ai",
                        first_name="Alex",
                        last_name="Founder",
                        company_id=company_id,
                        affiliation_state="verified",
                        resolution_confidence="high",
                        resolution_evidence={"chain": ["verified"], "person_state": "verified", "company_state": "verified"},
                        apollo_match_status="matched",
                        apollo_person_title="Founder",
                        apollo_person_seniority="founder",
                        apollo_person_linkedin_url="https://linkedin.com/in/alexfounder",
                        exa_person_role_signals="Founder is likely the buyer.",
                        self_reported_company="AgentCo",
                        self_reported_building="AI agents",
                        events_registered=1,
                        events_attended=1,
                        enrichment_data_quality="rich",
                    )
                    .returning(Person.id)
                )
            ).scalar_one()
            classification_id = (
                await session.execute(
                    insert(Classification)
                    .values(
                        person_id=person_id,
                        company_id=company_id,
                        tier="P0",
                        product_fit=["maas"],
                        primary_product="maas",
                        confidence=5,
                        reasoning="Founder at target-vertical company with grounded inference.",
                        key_signal="Founder + production inference.",
                        key_risk="Depends on enrichment freshness.",
                        flags=[],
                        review_status="CLASSIFIED",
                        pipeline_version="test",
                    )
                    .returning(Classification.id)
                )
            ).scalar_one()
            await session.execute(
                insert(Registration).values(
                    person_id=person_id,
                    event_id=event_id,
                    email="founder@agentco.ai",
                    approval_status="going",
                    checked_in_at=func.now(),
                    survey_responses={"company": "AgentCo"},
                )
            )
            await session.execute(
                Person.__table__.update()
                .where(Person.id == person_id)
                .values(current_tier="P0", current_confidence=5, current_classification_id=classification_id)
            )

            review_person_id = (
                await session.execute(
                    insert(Person)
                    .values(
                        email="reviewer@reviewco.ai",
                        first_name="Riley",
                        last_name="Reviewer",
                        company_id=second_company_id,
                        affiliation_state="inferred",
                        resolution_confidence="medium",
                        apollo_person_title="CEO",
                        enrichment_data_quality="moderate",
                    )
                    .returning(Person.id)
                )
            ).scalar_one()
            review_classification_id = (
                await session.execute(
                    insert(Classification)
                    .values(
                        person_id=review_person_id,
                        company_id=second_company_id,
                        tier="P1",
                        product_fit=["maas"],
                        primary_product="maas",
                        confidence=2,
                        reasoning="Needs a human look.",
                        key_signal="CEO at target vertical.",
                        key_risk="Thin evidence.",
                        flags=["healthcare_biotech_pharma"],
                        review_status="NEEDS_REVIEW",
                        pipeline_version="test",
                    )
                    .returning(Classification.id)
                )
            ).scalar_one()
            await session.execute(
                Person.__table__.update()
                .where(Person.id == review_person_id)
                .values(current_tier="P1", current_confidence=2, current_classification_id=review_classification_id)
            )
            await session.execute(
                insert(Registration).values(
                    person_id=review_person_id,
                    event_id=second_event_id,
                    email="reviewer@reviewco.ai",
                    approval_status="going",
                )
            )

            flagged_person_id = (
                await session.execute(
                    insert(Person)
                    .values(
                        email="flagged@gmail.com",
                        first_name="Casey",
                        last_name="Conflict",
                        affiliation_state="contradicted",
                        resolution_confidence="flagged",
                        resolution_evidence={
                            "chain": ["Apollo returned oldco.com but self-report resolved to agentco.ai"],
                            "person_state": "verified",
                            "company_state": "unresolved",
                        },
                        candidate_companies=[
                            {"domain": "oldco.com", "name": "OldCo", "source": "apollo_org"},
                            {"domain": "agentco.ai", "name": "AgentCo", "source": "exa_company_search"},
                        ],
                        self_reported_company="AgentCo",
                    )
                    .returning(Person.id)
                )
            ).scalar_one()

            low_person_id = (
                await session.execute(
                    insert(Person)
                    .values(
                        email="low@gmail.com",
                        first_name="Low",
                        last_name="Signal",
                        company_id=company_id,
                        affiliation_state="unverified",
                        resolution_confidence="low",
                        self_reported_company="AgentCo",
                        enrichment_data_quality="sparse",
                    )
                    .returning(Person.id)
                )
            ).scalar_one()

    return {
        "company_id": company_id,
        "event_id": event_id,
        "second_event_id": second_event_id,
        "person_id": person_id,
        "review_person_id": review_person_id,
        "flagged_person_id": flagged_person_id,
        "low_person_id": low_person_id,
    }


def test_list_leads_filters_and_returns_summaries(read_storage) -> None:
    adapter, _, ids = read_storage

    page = run(
        adapter.list_leads(
            LeadFilters(tier="P0", primary_product="maas", event_id=ids["event_id"], search="agentco"),
            sort="tier",
            limit=50,
            offset=0,
        )
    )

    assert page.total == 1
    assert page.items[0].email == "founder@agentco.ai"
    assert page.items[0].resolved_company == "AgentCo"
    assert page.items[0].tier == "P0"
    assert page.items[0].target_vertical == "agentic"
    assert page.items[0].person_id == ids["person_id"]


def test_event_analytics_counts_audience_fit_from_going_attendees_only(read_storage) -> None:
    adapter, session_factory, ids = read_storage

    async def add_non_going_p1_registration():
        async with session_factory() as session:
            async with session.begin():
                person_id = (
                    await session.execute(
                        insert(Person)
                        .values(
                            email="declined@reviewco.ai",
                            first_name="Dana",
                            last_name="Declined",
                            company_id=ids["company_id"],
                            current_tier="P1",
                        )
                        .returning(Person.id)
                    )
                ).scalar_one()
                await session.execute(
                    insert(Registration).values(
                        person_id=person_id,
                        event_id=ids["event_id"],
                        email="declined@reviewco.ai",
                        approval_status="declined",
                    )
                )

    run(add_non_going_p1_registration())

    analytics = run(adapter.event_analytics(ids["event_id"]))

    audience_fit = next(axis for axis in analytics["auto"] if axis["key"] == "audience_fit")
    assert audience_fit["value"] == 100
    assert audience_fit["detail"] == "1/1 P0/P1 of attendees"


def test_export_leads_returns_full_rows_and_filtered_event_summary(read_storage) -> None:
    adapter, _, ids = read_storage

    export = run(adapter.export_leads(LeadFilters(primary_product="maas"), sort="tier"))

    assert [row.email for row in export.rows] == ["founder@agentco.ai", "reviewer@reviewco.ai"]
    first = export.rows[0]
    assert first.tier == "P0"
    assert first.primary_product == "maas"
    assert first.confidence == 5
    assert first.review_status == "CLASSIFIED"
    assert first.first_name == "Alex"
    assert first.last_name == "Founder"
    assert first.name == "Alex Founder"
    assert first.email == "founder@agentco.ai"
    assert first.title == "Founder"
    assert first.linkedin_url == "https://linkedin.com/in/alexfounder"
    assert first.company == "AgentCo"
    assert first.domain == "agentco.ai"
    assert first.target_vertical == "agentic"
    assert first.region == "Needs location review"
    assert first.what_they_are_building == "AI agents"
    assert first.key_signal == "Founder + production inference."
    assert first.key_risk == "Depends on enrichment freshness."
    assert first.reasoning == "Founder at target-vertical company with grounded inference."
    assert first.product_fit == ["maas"]
    assert first.np_trigger is None
    assert first.event_names == ["Hard Problems"]
    assert [(row.event, row.classified_rows, row.p0, row.p1, row.np) for row in export.summary] == [
        ("Hard Problems", 1, 1, 0, 0),
        ("Agent Summit", 1, 0, 1, 0),
        ("Total", 2, 1, 1, 0),
    ]

    event_export = run(adapter.export_leads(LeadFilters(event_id=ids["event_id"]), sort="tier"))
    assert [row.email for row in event_export.rows] == ["founder@agentco.ai"]
    assert [(row.event, row.classified_rows, row.p0, row.p1, row.np) for row in event_export.summary] == [
        ("Hard Problems", 1, 1, 0, 0)
    ]

    tier_export = run(adapter.export_leads(LeadFilters(tiers=["P0", "P1"]), sort="tier"))
    assert [row.email for row in tier_export.rows] == ["founder@agentco.ai", "reviewer@reviewco.ai"]


def test_region_for_location_groups_apollo_locations() -> None:
    assert _region_for_location("San Francisco, CA, United States") == "North America"
    assert _region_for_location("Seoul, South Korea") == "APAC"
    assert _region_for_location("London, United Kingdom") == "Europe"
    assert _region_for_location(None) == "Needs location review"


def test_raw_apollo_location_fallback_preserves_city_and_country() -> None:
    assert _location_from_payload({"city": "Seoul", "country": "South Korea"}) == "Seoul, South Korea"
    assert _location_from_payload({"organization": {"location": "San Francisco, United States"}}) == "San Francisco, United States"


def test_get_lead_detail_includes_company_classification_history_and_registrations(read_storage) -> None:
    adapter, _, ids = read_storage

    detail = run(adapter.get_lead_detail(ids["person_id"]))

    assert detail is not None
    assert detail.person.email == "founder@agentco.ai"
    assert detail.company is not None
    assert detail.company.exa_enrichment["inference_signals"] == "Production API inference traffic."
    assert detail.current_classification is not None
    assert detail.current_classification.primary_product == "maas"
    assert len(detail.classification_history) == 1
    assert detail.registrations[0].event_name == "Hard Problems"


def test_review_queues_and_flagged_resolution_update_person(read_storage) -> None:
    adapter, session_factory, ids = read_storage

    flagged = run(adapter.list_flagged(limit=10, offset=0))
    needs_review = run(adapter.list_needs_review(queue_type="all", limit=10, offset=0))
    updated = run(adapter.resolve_flagged_person(ids["flagged_person_id"], chosen_domain="agentco.ai"))
    context = run(adapter.enrichment_context_for_person(ids["flagged_person_id"]))

    assert flagged.total == 1
    assert flagged.items[0].person_id == ids["flagged_person_id"]
    assert any(item.person_id == ids["review_person_id"] for item in needs_review.items)
    assert any(item.person_id == ids["low_person_id"] for item in needs_review.items)
    assert updated.person_id == ids["flagged_person_id"]
    assert updated.resolution_confidence == "high"
    assert updated.affiliation_state == "verified"
    assert context is not None
    assert context.input.email == "flagged@gmail.com"
    assert context.resolution.domain == "agentco.ai"
    assert context.person.organization_domain == "agentco.ai"

    async def assertion():
        async with session_factory() as session:
            person = (
                await session.execute(select(Person).where(Person.id == ids["flagged_person_id"]))
            ).scalar_one()
            assert person.company_id == ids["company_id"]
            assert person.candidate_companies == []
            assert person.resolution_evidence["company_state"] == "verified"

    run(assertion())


def test_dismissed_people_leave_review_queues_and_counts(read_storage) -> None:
    adapter, session_factory, ids = read_storage

    async def register_low_and_dismiss():
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    insert(Registration).values(
                        person_id=ids["low_person_id"],
                        event_id=ids["event_id"],
                        email="low@gmail.com",
                        approval_status="going",
                    )
                )
                await session.execute(
                    Person.__table__.update()
                    .where(Person.id.in_([ids["flagged_person_id"], ids["low_person_id"]]))
                    .values(review_dismissed_at=func.now())
                )

    run(register_low_and_dismiss())

    flagged = run(adapter.list_flagged(limit=10, offset=0))
    needs_review = run(adapter.list_needs_review(queue_type="all", limit=10, offset=0))
    data_quality = run(adapter.data_quality_summary())
    event_summary = run(adapter.event_summary(ids["event_id"]))

    assert flagged.total == 0
    assert all(item.person_id != ids["low_person_id"] for item in needs_review.items)
    assert data_quality["resolution"]["flagged"] == 0
    assert data_quality["resolution"]["low"] == 0
    assert data_quality["promising_low_confidence"] == 0
    assert event_summary["counts"]["parked_low"] == 0
    assert event_summary["resolution_funnel"]["low"] == 0

    resolved = run(adapter.resolve_flagged_person(ids["flagged_person_id"], chosen_domain="agentco.ai"))
    assert resolved.resolution_confidence == "high"

    async def assertion():
        async with session_factory() as session:
            person = (
                await session.execute(select(Person).where(Person.id == ids["flagged_person_id"]))
            ).scalar_one()
            assert person.review_dismissed_at is None

    run(assertion())


def test_manual_reclassify_preserves_history_and_updates_current(read_storage) -> None:
    adapter, session_factory, ids = read_storage

    detail = run(
        adapter.reclassify_person(
            ids["person_id"],
            new_tier="P1",
            reason="Manual downgrade after review.",
            reviewed_by="pynay",
        )
    )

    assert detail.current_classification is not None
    assert detail.current_classification.tier == "P1"
    assert detail.current_classification.reviewed_by == "pynay"

    async def assertion():
        async with session_factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(Classification).where(Classification.person_id == ids["person_id"])
            )
            person = (await session.execute(select(Person).where(Person.id == ids["person_id"]))).scalar_one()
            assert count == 2
            assert person.current_tier == "P1"
            assert person.current_confidence == 5

    run(assertion())


def test_auto_reclassify_does_not_mark_human_review(read_storage) -> None:
    adapter, _, ids = read_storage

    detail = run(
        adapter.reclassify_person(
            ids["review_person_id"],
            new_tier="NP",
            reason="Auto-review adjustment.",
            reviewed_by="mint-auto-reviewer",
            mark_human_review=False,
        )
    )

    assert detail.current_classification is not None
    assert detail.current_classification.tier == "NP"
    assert detail.current_classification.reviewed_by == "mint-auto-reviewer"
    assert detail.current_classification.reviewed_at is None


def test_event_pipeline_and_data_quality_summaries(read_storage) -> None:
    adapter, _, ids = read_storage

    event_summary = run(adapter.event_summary(ids["event_id"]))
    pipeline_summary = run(adapter.pipeline_summary())
    data_quality = run(adapter.data_quality_summary())

    assert event_summary["counts"]["total_guests"] == 1
    assert event_summary["tier_breakdown"]["P0"] == 1
    assert event_summary["product_fit_breakdown"]["maas"] >= 1
    assert pipeline_summary["p0_p1_by_primary_product"]["maas"] >= 2
    assert data_quality["np_trigger_breakdown"] == {}
    assert data_quality["resolution"]["high_medium"] == 2
