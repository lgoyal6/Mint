import asyncio
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mint.backend.db.base import Base
from mint.backend.db.event_storage import EventStorage, _enrichment_input_from_registration
from mint.backend.db.storage import EnrichmentStorage
from mint.backend.db.tables import Event, Person, Registration
from mint.backend.ingestion.orchestrator import IngestionOrchestrator, _event_input_from_luma
from mint.backend.models import (
    ApolloPersonResult,
    EnrichmentInput,
    EnrichmentResult,
    EventInput,
    RegistrationInput,
    ResolvedIdentity,
)
from mint.backend.settings import Settings


FIXTURES = Path(__file__).parent / "fixtures"


def run(coro):
    return asyncio.run(coro)


def test_legacy_csv_website_survey_answer_is_recovered_for_retry() -> None:
    registration = Registration(
        event_id=uuid4(),
        email="founder@gmail.com",
        survey_responses={
            "Website URL": "https://agentco.ai",
            "_mint_lead_context": {
                "first_name": "Ada",
                "company_name": "AgentCo",
            },
        },
    )

    lead = _enrichment_input_from_registration(registration, None)

    assert lead.website_url == "https://agentco.ai"
    assert lead.company_name == "AgentCo"
    assert lead.survey_responses == {"Website URL": "https://agentco.ai"}


def storage_database_url() -> str:
    url = Settings().mint_test_database_url
    if not url:
        pytest.skip("MINT_TEST_DATABASE_URL is not set")
    if importlib.util.find_spec("asyncpg") is None:
        pytest.skip("asyncpg is not installed")
    return url


@pytest.fixture()
def orchestrator_storage():
    url = storage_database_url()

    async def setup():
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        enrichment_storage = EnrichmentStorage(session_factory)
        event_storage = EventStorage(session_factory)
        return engine, session_factory, enrichment_storage, event_storage

    engine, session_factory, enrichment_storage, event_storage = run(setup())
    try:
        yield session_factory, enrichment_storage, event_storage
    finally:
        async def teardown():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

        run(teardown())


class FakeLumaClient:
    def __init__(self, event: dict, guests: list[dict]) -> None:
        self.event = event
        self.guests = guests
        self.guest_statuses: list[str] = []

    async def get_event(self, event_id: str) -> dict:
        assert event_id == self.event["api_id"]
        return self.event

    async def get_guests(self, event_id: str, status: str = "going") -> list[dict]:
        assert event_id == self.event["api_id"]
        self.guest_statuses.append(status)
        if status == "all":
            return self.guests
        if status == "going":
            return [guest for guest in self.guests if guest.get("approval_status") in {"going", "approved"}]
        return [guest for guest in self.guests if guest.get("approval_status") == status]

    async def list_events(self, status: str = "all") -> list[dict]:
        return [self.event]


class FakeEnrichmentService:
    def __init__(self, storage: EnrichmentStorage) -> None:
        self.storage = storage
        self.inputs: list[EnrichmentInput] = []

    async def enrich_batch(
        self, inputs: list[EnrichmentInput], max_concurrent: int = 10, on_progress=None
    ) -> list[EnrichmentResult]:
        self.inputs.extend(inputs)
        results = []
        for lead in inputs:
            result = await self._result_for(lead)
            await self.storage.save(lead, result)
            results.append(result)
        return results

    async def _result_for(self, lead: EnrichmentInput) -> EnrichmentResult:
        if await self.storage.get_freshness(lead.email) is not None:
            return EnrichmentResult(
                resolution=self._resolution(lead, confidence="low"),
                data_quality="sparse",
                enriched=False,
                enrichment_sources=["skipped_fresh"],
                errors=[],
            )
        return EnrichmentResult(
            resolution=self._resolution(lead, confidence="high"),
            person=ApolloPersonResult(
                matched=True,
                match_status="matched",
                email=lead.email,
                first_name=lead.first_name,
                last_name=lead.last_name,
                name=" ".join(part for part in [lead.first_name, lead.last_name] if part) or None,
                title="Founder",
                organization_name=lead.company_name,
                raw_data={"requestId": f"apollo-{lead.email}"},
            ),
            data_quality="moderate",
            enriched=False,
            enrichment_sources=["apollo_person"],
            errors=[],
        )

    @staticmethod
    def _resolution(lead: EnrichmentInput, confidence: str) -> ResolvedIdentity:
        return ResolvedIdentity(
            person_source="apollo" if confidence == "high" else "self_reported",
            name=" ".join(part for part in [lead.first_name, lead.last_name] if part) or None,
            title="Founder",
            seniority=None,
            company_source="none",
            domain=None,
            company_name=lead.company_name,
            affiliation_state="unverified",
            confidence=confidence,
            evidence={
                "person_state": "verified" if confidence == "high" else "self_reported",
                "company_state": "unresolved",
                "chain": [f"-> confidence: {confidence}"],
            },
        )


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_luma_event_input_marks_past_events_past_even_when_luma_status_is_upcoming() -> None:
    event_input = _event_input_from_luma(
        {
            "api_id": "evt-past",
            "name": "Past Event",
            "start_at": "2026-05-27T18:00:00-07:00",
            "status": "upcoming",
        },
        fallback_luma_event_id="evt-past",
        guest_count=10,
    )

    assert event_input.status == "past"


def test_luma_event_input_marks_future_events_upcoming_when_luma_status_is_missing() -> None:
    event_input = _event_input_from_luma(
        {
            "api_id": "evt-future",
            "name": "Future Event",
            "start_at": "2099-05-27T18:00:00-07:00",
        },
        fallback_luma_event_id="evt-future",
        guest_count=10,
    )

    assert event_input.status == "upcoming"


def test_ingest_luma_event_persists_and_enriches_all_registration_statuses(orchestrator_storage) -> None:
    session_factory, enrichment_storage, event_storage = orchestrator_storage
    luma = FakeLumaClient(load_fixture("luma_event.json"), load_fixture("luma_guests.json"))
    enrichment = FakeEnrichmentService(enrichment_storage)
    orchestrator = IngestionOrchestrator(luma, enrichment, enrichment_storage, event_storage)

    summary = run(orchestrator.ingest_luma_event("evt-hard-problems", enrich=True))

    async def assertions():
        async with session_factory() as session:
            event_count = await session.scalar(select(func.count()).select_from(Event))
            registration_count = await session.scalar(select(func.count()).select_from(Registration))
            person_count = await session.scalar(select(func.count()).select_from(Person))
            linked_count = await session.scalar(
                select(func.count()).select_from(Registration).where(Registration.person_id.is_not(None))
            )
            pending = (
                await session.execute(select(Registration).where(Registration.email == "pending@example.com"))
            ).scalar_one()

            assert event_count == 1
            assert registration_count == 3
            assert person_count == 3
            assert linked_count == 3
            assert pending.person_id is not None

    run(assertions())
    assert luma.guest_statuses == ["all"]
    assert [lead.email for lead in enrichment.inputs] == [
        "alex@example.com",
        "pending@example.com",
        "declined@example.com",
    ]
    assert summary.total_guests == 3
    assert summary.going_count == 1
    assert summary.enriched_count == 3
    assert summary.skipped_fresh_count == 0
    assert summary.flagged_count == 0


def test_ingest_csv_with_event_links_registration_to_created_person(orchestrator_storage) -> None:
    session_factory, enrichment_storage, event_storage = orchestrator_storage
    enrichment = FakeEnrichmentService(enrichment_storage)
    orchestrator = IngestionOrchestrator(None, enrichment, enrichment_storage, event_storage)

    summary = run(
        orchestrator.ingest_csv(
            [{"Email": "alex@example.com", "First Name": "Alex", "Company": "Quadex AI"}],
            EventInput(
                luma_event_id=None,
                name="CSV Event",
                event_date=datetime(2026, 5, 27, 18, 0, tzinfo=UTC),
                location=None,
                url=None,
                status="past",
                guest_count=1,
                enrichment_enabled=True,
            ),
        )
    )

    async def assertions():
        async with session_factory() as session:
            registration = (
                await session.execute(select(Registration).where(Registration.email == "alex@example.com"))
            ).scalar_one()
            assert registration.person_id is not None
            assert registration.survey_responses["_mint_lead_context"] == {
                "first_name": "Alex",
                "company_name": "Quadex AI",
            }

    run(assertions())
    reconstructed = run(event_storage.enrichment_inputs_for_event(summary.event_id))
    assert reconstructed[0].email == "alex@example.com"
    assert reconstructed[0].first_name == "Alex"
    assert reconstructed[0].company_name == "Quadex AI"
    assert summary.total_guests == 1
    assert summary.going_count == 1
    assert summary.enriched_count == 1


def test_ingest_csv_enriches_all_statuses_and_preserves_event_approval(orchestrator_storage) -> None:
    session_factory, enrichment_storage, event_storage = orchestrator_storage
    enrichment = FakeEnrichmentService(enrichment_storage)
    orchestrator = IngestionOrchestrator(None, enrichment, enrichment_storage, event_storage)

    summary = run(
        orchestrator.ingest_csv(
            [
                {"email": "approved@example.com", "approval_status": "approved", "company": "Approved AI"},
                {"email": "pending@example.com", "approval_status": "pending_approval", "company": "Pending AI"},
                {"email": "declined@example.com", "approval_status": "declined", "company": "Declined AI"},
            ],
            EventInput(name="All Guests", status="upcoming", guest_count=3),
        )
    )

    async def assertions():
        async with session_factory() as session:
            registrations = list(
                (
                    await session.execute(
                        select(Registration).order_by(Registration.email)
                    )
                ).scalars()
            )
            assert [(row.email, row.approval_status) for row in registrations] == [
                ("approved@example.com", "approved"),
                ("declined@example.com", "declined"),
                ("pending@example.com", "pending"),
            ]
            assert all(row.person_id is not None for row in registrations)

    run(assertions())
    reconstructed = run(event_storage.enrichment_inputs_for_event(summary.event_id))
    assert sorted(lead.email for lead in reconstructed) == [
        "approved@example.com",
        "declined@example.com",
        "pending@example.com",
    ]
    assert sorted(lead.email for lead in enrichment.inputs) == [
        "approved@example.com",
        "declined@example.com",
        "pending@example.com",
    ]
    assert summary.total_guests == 3
    assert summary.going_count == 1
    assert summary.enriched_count == 3


def test_checkin_sync_updates_no_show_count(orchestrator_storage) -> None:
    session_factory, enrichment_storage, event_storage = orchestrator_storage
    guests = load_fixture("luma_guests.json")
    guests.append({"email": "noshow@example.com", "name": "No Show", "approval_status": "going"})
    luma = FakeLumaClient(load_fixture("luma_event.json"), guests)
    enrichment = FakeEnrichmentService(enrichment_storage)
    orchestrator = IngestionOrchestrator(luma, enrichment, enrichment_storage, event_storage)
    run(orchestrator.ingest_luma_event("evt-hard-problems", enrich=False))

    counts = run(orchestrator.sync_luma_checkins("evt-hard-problems"))

    async def assertions():
        async with session_factory() as session:
            checked_in = (
                await session.execute(select(Registration).where(Registration.email == "alex@example.com"))
            ).scalar_one()
            no_show = (
                await session.execute(select(Registration).where(Registration.email == "noshow@example.com"))
            ).scalar_one()
            assert checked_in.checked_in_at is not None
            assert no_show.checked_in_at is None

    run(assertions())
    assert counts == {"checked_in": 1, "no_show": 1}


def test_enrich_and_link_uses_bulk_existing_people_lookup() -> None:
    class BulkEventStorage:
        def __init__(self) -> None:
            self.bulk_lookup_emails: list[list[str]] = []

        async def existing_person_emails(self, emails: list[str]) -> set[str]:
            self.bulk_lookup_emails.append(emails)
            return {"known@example.com"}

        async def person_exists(self, email: str) -> bool:
            raise AssertionError(f"unexpected per-person lookup for {email}")

        async def get_person_id_by_email(self, email: str):
            return None

    class NoopEnrichment:
        async def enrich_batch(self, inputs: list[EnrichmentInput], on_progress=None) -> list[EnrichmentResult]:
            return [
                EnrichmentResult(
                    resolution=ResolvedIdentity(
                        person_source="self_reported",
                        company_source="none",
                        affiliation_state="unverified",
                        confidence="low",
                        evidence={
                            "person_state": "self_reported",
                            "company_state": "unresolved",
                            "chain": ["-> confidence: low"],
                        },
                    ),
                    data_quality="sparse",
                    enriched=False,
                )
                for _ in inputs
            ]

    event_storage = BulkEventStorage()
    orchestrator = IngestionOrchestrator(None, NoopEnrichment(), None, event_storage)
    leads = [
        EnrichmentInput(email="known@example.com"),
        EnrichmentInput(email="new@example.com"),
    ]

    summary = run(
        orchestrator._enrich_and_link(
            leads,
            event_id=uuid4(),
            total_guests=2,
            going_count=2,
        )
    )

    assert event_storage.bulk_lookup_emails == [["known@example.com", "new@example.com"]]
    assert summary.already_known_count == 1
    assert summary.enriched_count == 2


def test_persist_registrations_uses_bulk_upsert_and_person_id_lookup() -> None:
    class BulkEventStorage:
        def __init__(self) -> None:
            self.lookup_emails: list[list[str]] = []
            self.bulk_calls: list[tuple[list[RegistrationInput], dict[str, object]]] = []

        async def person_ids_by_email(self, emails: list[str]) -> dict[str, object]:
            self.lookup_emails.append(emails)
            return {"known@example.com": "person-id"}

        async def upsert_registrations(
            self,
            registrations: list[RegistrationInput],
            event_id,
            person_ids_by_email: dict[str, object],
        ) -> None:
            self.bulk_calls.append((registrations, person_ids_by_email))

        async def upsert_registration(self, reg, event_id, person_id) -> None:
            raise AssertionError(f"unexpected per-registration upsert for {reg.email}")

    event_storage = BulkEventStorage()
    orchestrator = IngestionOrchestrator(None, None, None, event_storage)
    guests = [
        {"email": "known@example.com", "approval_status": "going"},
        {"email": "new@example.com", "approval_status": "pending"},
        {"name": "Missing Email", "approval_status": "going"},
    ]

    run(orchestrator._persist_registrations(guests, uuid4()))

    assert event_storage.lookup_emails == [["known@example.com", "new@example.com"]]
    assert len(event_storage.bulk_calls) == 1
    registrations, person_ids = event_storage.bulk_calls[0]
    assert [reg.email for reg in registrations] == ["known@example.com", "new@example.com"]
    assert person_ids == {"known@example.com": "person-id"}
