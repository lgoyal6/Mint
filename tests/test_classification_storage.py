import asyncio
import importlib.util

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mint.backend.classification.classifier import Classifier, load_icp_config
from mint.backend.classification.service import ClassificationScope, ClassificationService
from mint.backend.db.base import Base
from mint.backend.db.classification_storage import ClassificationStorage
from mint.backend.db.tables import Classification, Company, Event, Person, Registration
from mint.backend.models import ClassificationResult
from mint.backend.settings import Settings


def run(coro):
    return asyncio.run(coro)


class StorageTierLlm:
    def __init__(self, default_tier: str = "P0") -> None:
        self.default_tier = default_tier
        self.calls: list[dict] = []

    async def classify_tier(self, context, *, system_prompt, model=None):
        self.calls.append(context)
        if context["facts"]["is_pre_revenue"]:
            return {
                "product_fit": ["maas"],
                "primary_product": "maas",
                "tier": "NP",
                "confidence": 3,
                "np_trigger": "pre_revenue",
                "key_signal": "Pre-revenue target-vertical company.",
                "key_risk": "Needs commercial traction before prioritization.",
                "reasoning": "The company is in a target vertical but the enrichment says it is pre-revenue.",
            }
        return {
            "product_fit": ["maas"],
            "primary_product": "maas",
            "tier": self.default_tier,
            "confidence": 5,
            "np_trigger": None,
            "key_signal": "Target vertical with buyer fit.",
            "key_risk": "Classification depends on enrichment accuracy.",
            "reasoning": "The provided enrichment context supports the requested tier.",
        }


def storage_database_url() -> str:
    url = Settings().mint_test_database_url
    if not url:
        pytest.skip("MINT_TEST_DATABASE_URL is not set")
    if importlib.util.find_spec("asyncpg") is None:
        pytest.skip("asyncpg is not installed")
    return url


@pytest.fixture()
def classification_storage():
    url = storage_database_url()

    async def setup():
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return engine, session_factory, ClassificationStorage(session_factory)

    engine, session_factory, adapter = run(setup())
    try:
        yield adapter, session_factory
    finally:
        async def teardown():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

        run(teardown())


async def insert_company_person(
    session_factory,
    *,
    email: str = "alex@quadex.ai",
    name: str = "Quadex AI",
    domain: str | None = None,
    vertical: str = "agentic",
    stage: str = "growth",
    funding_stage: str = "Series A",
    revenue: str | None = "$1M",
    employee_count: int = 75,
    spend_text: str = "Estimated monthly GPU/inference spend is >= $10K/mo.",
    resolution_confidence: str = "high",
):
    domain = domain or email.split("@", 1)[0].replace("_", "-") + ".quadex.ai"
    async with session_factory() as session:
        async with session.begin():
            company_id = (
                await session.execute(
                    insert(Company)
                    .values(
                        domain=domain,
                        name=name,
                        exa_target_vertical=vertical,
                        exa_stage=stage,
                        exa_data_quality="rich",
                        apollo_org_funding_stage=funding_stage,
                        apollo_org_revenue=revenue,
                        apollo_org_employee_count=employee_count,
                        exa_enrichment={
                            "what_they_do": f"{name} builds AI software.",
                            "funding_signals": funding_stage,
                            "revenue_signals": revenue or "",
                            "inference_signals": spend_text,
                            "spend_signals": spend_text,
                        },
                    )
                    .returning(Company.id)
                )
            ).scalar_one()
            person_id = (
                await session.execute(
                    insert(Person)
                    .values(
                        email=email,
                        first_name="Alex",
                        last_name="Rivera",
                        company_id=company_id,
                        resolution_confidence=resolution_confidence,
                        apollo_person_title="Founder",
                    )
                    .returning(Person.id)
                )
            ).scalar_one()
            return company_id, person_id


def test_save_classification_updates_person_current_fields(classification_storage) -> None:
    adapter, session_factory = classification_storage
    company_id, person_id = run(insert_company_person(session_factory))
    result = ClassificationResult(
        tier="P0",
        np_trigger=None,
        confidence=5,
        product_fit=["maas", "dedicated"],
        primary_product="dedicated",
        reasoning="Target vertical, Series A+, revenue, and >= $10K/mo spend.",
        key_signal="agentic / 10k_plus",
        key_risk="Classification depends on extracted evidence accuracy.",
        flags=[],
        review_status="CLASSIFIED",
        pipeline_version="test",
        vertical="agentic",
        funding_stage="series_a",
        employee_count=75,
        has_revenue=True,
        is_pre_revenue=False,
    )

    classification_id = run(adapter.save_classification(person_id, company_id, result))

    async def assertions():
        async with session_factory() as session:
            person = (await session.execute(select(Person).where(Person.id == person_id))).scalar_one()
            classification = (
                await session.execute(select(Classification).where(Classification.id == classification_id))
            ).scalar_one()

            assert person.current_tier == "P0"
            assert person.current_confidence == 5
            assert person.current_classification_id == classification_id
            assert classification.np_trigger is None
            assert classification.product_fit == ["maas", "dedicated"]
            assert classification.primary_product == "dedicated"

    run(assertions())


def test_classification_service_classifies_only_eligible_and_updates_current_fields(classification_storage) -> None:
    adapter, session_factory = classification_storage
    _, eligible_person_id = run(insert_company_person(session_factory, email="eligible@quadex.ai"))
    run(insert_company_person(session_factory, email="low@quadex.ai", resolution_confidence="low"))
    llm = StorageTierLlm()
    service = ClassificationService(adapter, Classifier(load_icp_config(), llm_client=llm))

    summary = run(service.classify_batch(ClassificationScope()))

    async def assertions():
        async with session_factory() as session:
            classification_count = await session.scalar(select(func.count()).select_from(Classification))
            eligible_person = (
                await session.execute(select(Person).where(Person.id == eligible_person_id))
            ).scalar_one()
            low_person = (await session.execute(select(Person).where(Person.email == "low@quadex.ai"))).scalar_one()

            assert classification_count == 1
            assert eligible_person.current_tier == "P0"
            assert eligible_person.current_classification_id is not None
            assert low_person.current_classification_id is None

    run(assertions())
    assert summary.classified_count == 1
    assert summary.skipped_count == 0
    assert len(llm.calls) == 1


def test_low_confidence_self_reported_luma_lead_is_classifiable(classification_storage) -> None:
    adapter, session_factory = classification_storage
    _, luma_person_id = run(
        insert_company_person(
            session_factory,
            email="luma-only@example.com",
            resolution_confidence="low",
        )
    )
    run(insert_company_person(session_factory, email="low-without-luma@example.com", resolution_confidence="low"))

    async def mark_luma_self_reported():
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(Person)
                    .where(Person.id == luma_person_id)
                    .values(
                        self_reported_company="Five Tables",
                        self_reported_title="Founder",
                        self_reported_building="AI video inference platform",
                    )
                )

    run(mark_luma_self_reported())

    records = run(adapter.get_classifiable_people(ClassificationScope()))

    assert [record.person.email for record in records] == ["luma-only@example.com"]


def test_flagged_self_reported_luma_lead_is_not_classifiable(classification_storage) -> None:
    adapter, session_factory = classification_storage
    _, person_id = run(
        insert_company_person(
            session_factory,
            email="flagged-luma@example.com",
            resolution_confidence="flagged",
        )
    )

    async def mark_luma_self_reported():
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(Person)
                    .where(Person.id == person_id)
                    .values(
                        self_reported_company="Five Tables",
                        self_reported_title="Founder",
                        self_reported_building="AI video inference platform",
                    )
                )

    run(mark_luma_self_reported())

    records = run(adapter.get_classifiable_people(ClassificationScope()))

    assert records == []


def test_event_scope_and_force_reclassify_preserve_history(classification_storage) -> None:
    adapter, session_factory = classification_storage
    _, event_person_id = run(insert_company_person(session_factory, email="event@quadex.ai"))
    run(insert_company_person(session_factory, email="other@quadex.ai", domain="other.ai"))

    async def insert_event_registration():
        async with session_factory() as session:
            async with session.begin():
                event_id = (
                    await session.execute(
                        insert(Event).values(name="Demo", luma_event_id="evt-classify").returning(Event.id)
                    )
                ).scalar_one()
                await session.execute(
                    insert(Registration).values(
                        event_id=event_id,
                        person_id=event_person_id,
                        email="event@quadex.ai",
                        approval_status="going",
                    )
                )
                return event_id

    event_id = run(insert_event_registration())
    service = ClassificationService(adapter, Classifier(load_icp_config(), llm_client=StorageTierLlm()))

    first = run(service.classify_batch(ClassificationScope(event_id=event_id)))
    second = run(service.classify_batch(ClassificationScope(event_id=event_id)))
    forced = run(service.classify_batch(ClassificationScope(event_id=event_id, force_reclassify=True)))

    async def assertions():
        async with session_factory() as session:
            classification_count = await session.scalar(select(func.count()).select_from(Classification))
            other = (await session.execute(select(Person).where(Person.email == "other@quadex.ai"))).scalar_one()
            event_person = (await session.execute(select(Person).where(Person.id == event_person_id))).scalar_one()

            assert classification_count == 2
            assert other.current_classification_id is None
            assert event_person.current_classification_id is not None

    run(assertions())
    assert first.classified_count == 1
    assert second.classified_count == 0
    assert forced.classified_count == 1


def failure_classification_result(np_trigger: str = "llm_unavailable") -> ClassificationResult:
    return ClassificationResult(
        tier="NP",
        np_trigger=np_trigger,
        confidence=2,
        product_fit=[],
        primary_product=None,
        reasoning="LLM was unavailable during classification.",
        key_signal="Classification unavailable.",
        key_risk="Needs review before outreach.",
        flags=[np_trigger],
        review_status="NEEDS_REVIEW",
        pipeline_version="test",
        vertical="agentic",
        funding_stage="series_a",
        employee_count=75,
        has_revenue=True,
        is_pre_revenue=False,
    )


def test_failed_classifications_are_retried_without_force(classification_storage) -> None:
    adapter, session_factory = classification_storage
    failed_company_id, failed_person_id = run(insert_company_person(session_factory, email="failed@quadex.ai"))
    pre_company_id, pre_person_id = run(
        insert_company_person(session_factory, email="settled@quadex.ai", domain="settled.ai")
    )
    run(adapter.save_classification(failed_person_id, failed_company_id, failure_classification_result()))
    run(
        adapter.save_classification(
            pre_person_id,
            pre_company_id,
            failure_classification_result(np_trigger="pre_revenue"),
        )
    )
    service = ClassificationService(adapter, Classifier(load_icp_config(), llm_client=StorageTierLlm()))

    summary = run(service.classify_batch(ClassificationScope()))

    async def assertions():
        async with session_factory() as session:
            failed_person = (
                await session.execute(select(Person).where(Person.id == failed_person_id))
            ).scalar_one()
            settled_person = (
                await session.execute(select(Person).where(Person.id == pre_person_id))
            ).scalar_one()
            assert failed_person.current_tier == "P0"
            assert settled_person.current_tier == "NP"

    run(assertions())
    assert summary.classified_count == 1


def test_force_reclassify_skips_human_reviewed_classifications(classification_storage) -> None:
    adapter, session_factory = classification_storage
    _, reviewed_person_id = run(insert_company_person(session_factory, email="reviewed@quadex.ai"))
    _, llm_person_id = run(insert_company_person(session_factory, email="auto@quadex.ai", domain="auto.ai"))

    async def insert_manual_classification():
        async with session_factory() as session:
            async with session.begin():
                classification_id = (
                    await session.execute(
                        insert(Classification)
                        .values(
                            person_id=reviewed_person_id,
                            tier="NP",
                            reasoning="Reviewed manually.",
                            review_status="CLASSIFIED",
                            reviewed_by="pranay",
                            reviewed_at=func.now(),
                            reclassified_to="NP",
                            pipeline_version="manual-review-v1",
                        )
                        .returning(Classification.id)
                    )
                ).scalar_one()
                await session.execute(
                    Person.__table__.update()
                    .where(Person.id == reviewed_person_id)
                    .values(current_tier="NP", current_confidence=5, current_classification_id=classification_id)
                )
                return classification_id

    manual_classification_id = run(insert_manual_classification())
    service = ClassificationService(adapter, Classifier(load_icp_config(), llm_client=StorageTierLlm()))

    summary = run(service.classify_batch(ClassificationScope(force_reclassify=True)))

    async def assertions():
        async with session_factory() as session:
            reviewed_person = (
                await session.execute(select(Person).where(Person.id == reviewed_person_id))
            ).scalar_one()
            llm_person = (
                await session.execute(select(Person).where(Person.id == llm_person_id))
            ).scalar_one()
            assert reviewed_person.current_tier == "NP"
            assert reviewed_person.current_classification_id == manual_classification_id
            assert llm_person.current_tier == "P0"

    run(assertions())
    assert summary.classified_count == 1


def test_reset_all_classifications_wipes_tiers_and_dismisses_unresolved(classification_storage) -> None:
    adapter, session_factory = classification_storage
    company_id, person_id = run(insert_company_person(session_factory))
    run(adapter.save_classification(person_id, company_id, failure_classification_result(np_trigger="pre_revenue")))

    async def add_unresolved():
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    insert(Person).values(email="flagged@unresolved.ai", resolution_confidence="flagged")
                )
                await session.execute(
                    insert(Person).values(email="low@unresolved.ai", resolution_confidence="low")
                )

    run(add_unresolved())

    counts = run(adapter.reset_all_classifications(dismiss_unresolved=True))

    assert counts["classifications_deleted"] == 1
    assert counts["people_reset"] == 1
    assert counts["people_dismissed"] == 2

    async def assertions():
        async with session_factory() as session:
            classification_count = await session.scalar(select(func.count()).select_from(Classification))
            classified = (await session.execute(select(Person).where(Person.id == person_id))).scalar_one()
            dismissed_count = await session.scalar(
                select(func.count()).select_from(Person).where(Person.review_dismissed_at.is_not(None))
            )
            assert classification_count == 0
            assert classified.current_tier is None
            assert classified.current_confidence is None
            assert classified.current_classification_id is None
            assert classified.review_dismissed_at is None
            assert dismissed_count == 2

    run(assertions())


def needs_review_result(tier: str = "P1") -> ClassificationResult:
    return ClassificationResult(
        tier=tier,
        np_trigger=None,
        confidence=2,
        product_fit=["maas"],
        primary_product="maas",
        reasoning="Thin evidence; needs review.",
        key_signal="Target vertical with possible fit.",
        key_risk="Low confidence.",
        flags=[],
        review_status="NEEDS_REVIEW",
        pipeline_version="test",
        vertical="agentic",
        funding_stage="seed",
        employee_count=10,
        has_revenue=False,
        is_pre_revenue=False,
    )


def test_pending_auto_reviews_and_marks(classification_storage) -> None:
    adapter, session_factory = classification_storage
    company_id, person_id = run(insert_company_person(session_factory))
    other_company_id, other_person_id = run(
        insert_company_person(session_factory, email="other@quadex.ai", domain="other.ai")
    )
    run(adapter.save_classification(person_id, company_id, needs_review_result()))
    run(adapter.save_classification(other_person_id, other_company_id, needs_review_result()))

    pending = run(adapter.pending_auto_reviews(limit=10))
    assert {record.person.id for record in pending} == {person_id, other_person_id}
    assert all(record.classification.review_status == "NEEDS_REVIEW" for record in pending)

    first = next(record for record in pending if record.person.id == person_id)
    second = next(record for record in pending if record.person.id == other_person_id)
    run(adapter.mark_auto_review(first.classification.id, status="confirmed", note="Structural NP."))
    run(adapter.mark_auto_review(second.classification.id, status="uncertain", note="Evidence is thin."))

    remaining = run(adapter.pending_auto_reviews(limit=10))
    assert remaining == []

    async def assertions():
        async with session_factory() as session:
            confirmed = (
                await session.execute(select(Classification).where(Classification.id == first.classification.id))
            ).scalar_one()
            uncertain = (
                await session.execute(select(Classification).where(Classification.id == second.classification.id))
            ).scalar_one()
            assert confirmed.review_status == "CLASSIFIED"
            assert confirmed.auto_review_status == "confirmed"
            assert confirmed.auto_reviewed_at is not None
            assert uncertain.review_status == "NEEDS_REVIEW"
            assert uncertain.auto_review_status == "uncertain"
            assert uncertain.auto_review_note == "Evidence is thin."

    run(assertions())


def test_pending_flagged_resolutions_and_attempt_mark(classification_storage) -> None:
    adapter, session_factory = classification_storage

    async def seed():
        async with session_factory() as session:
            async with session.begin():
                flagged_id = (
                    await session.execute(
                        insert(Person)
                        .values(
                            email="conflict@gmail.com",
                            resolution_confidence="flagged",
                            candidate_companies=[{"domain": "a.ai"}, {"domain": "b.ai"}],
                        )
                        .returning(Person.id)
                    )
                ).scalar_one()
                await session.execute(
                    insert(Person).values(
                        email="dismissed@gmail.com",
                        resolution_confidence="flagged",
                        review_dismissed_at=func.now(),
                    )
                )
                return flagged_id

    flagged_id = run(seed())

    pending = run(adapter.pending_flagged_resolutions(limit=10))
    assert [person.id for person in pending] == [flagged_id]

    run(adapter.mark_auto_resolve_attempt(flagged_id, note="Candidates are tied."))

    assert run(adapter.pending_flagged_resolutions(limit=10)) == []

    async def assertions():
        async with session_factory() as session:
            person = (await session.execute(select(Person).where(Person.id == flagged_id))).scalar_one()
            assert person.auto_resolve_attempted_at is not None
            assert person.auto_resolve_note == "Candidates are tied."

    run(assertions())


def test_np_trigger_supports_reengagement_query(classification_storage) -> None:
    adapter, session_factory = classification_storage
    run(
        insert_company_person(
            session_factory,
            email="pre@quadex.ai",
            domain="pre.quadex.ai",
            stage="pre-revenue",
            revenue=None,
        )
    )
    service = ClassificationService(adapter, Classifier(load_icp_config(), llm_client=StorageTierLlm()))

    run(service.classify_batch(ClassificationScope()))

    async def assertions():
        async with session_factory() as session:
            reengagement_rows = (
                await session.execute(
                    select(Person.email)
                    .join(Classification, Person.current_classification_id == Classification.id)
                    .join(Company, Person.company_id == Company.id)
                    .where(
                        Classification.tier == "NP",
                        Classification.np_trigger == "pre_revenue",
                        Company.exa_target_vertical.in_(
                            [
                                "research_labs",
                                "coding_platforms",
                                "agentic",
                                "data_labeling",
                                "robotics",
                                "video_ai_multimodal",
                                "adjacent",
                            ]
                        ),
                    )
                )
            ).scalars().all()
            assert reengagement_rows == ["pre@quadex.ai"]

    run(assertions())
