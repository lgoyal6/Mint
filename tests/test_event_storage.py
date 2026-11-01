import asyncio
import importlib.util
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mint.backend.db.base import Base
from mint.backend.db.event_storage import EventStorage
from mint.backend.db.tables import Event, Person, Registration
from mint.backend.models import EventInput, RegistrationInput
from mint.backend.settings import Settings


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
def event_storage():
    url = storage_database_url()

    async def setup():
        engine = create_async_engine(url, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return engine, session_factory, EventStorage(session_factory)

    engine, session_factory, adapter = run(setup())
    try:
        yield adapter, session_factory
    finally:
        async def teardown():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

        run(teardown())


def event_input(name: str = "Hard Problems") -> EventInput:
    return EventInput(
        luma_event_id="evt-hard-problems",
        name=name,
        event_date=datetime(2026, 5, 27, 18, 0, tzinfo=UTC),
        location="San Francisco",
        url="https://luma.com/gmicloud-u1rb",
        status="past",
        guest_count=3,
        enrichment_enabled=True,
    )


def registration_input(email: str = "alex@example.com") -> RegistrationInput:
    return RegistrationInput(
        email=email,
        approval_status="going",
        checked_in_at=None,
        survey_responses={"GPU count?": "8"},
    )


def test_event_and_registration_upserts_are_idempotent(event_storage) -> None:
    adapter, session_factory = event_storage

    event_id = run(adapter.upsert_event(event_input()))
    same_event_id = run(adapter.upsert_event(event_input(name="Hard Problems Updated")))
    run(adapter.upsert_registration(registration_input(), event_id, person_id=None))
    run(
        adapter.upsert_registration(
            RegistrationInput(
                email="alex@example.com",
                approval_status="going",
                checked_in_at=datetime(2026, 5, 28, 1, 15, tzinfo=UTC),
                survey_responses={"GPU count?": "16"},
            ),
            event_id,
            person_id=None,
        )
    )

    async def assertions():
        async with session_factory() as session:
            event_count = await session.scalar(select(func.count()).select_from(Event))
            registration_count = await session.scalar(select(func.count()).select_from(Registration))
            event = (await session.execute(select(Event).where(Event.id == event_id))).scalar_one()
            registration = (
                await session.execute(select(Registration).where(Registration.email == "alex@example.com"))
            ).scalar_one()

            assert same_event_id == event_id
            assert event_count == 1
            assert event.name == "Hard Problems Updated"
            assert event.enrichment_enabled is True
            assert registration_count == 1
            assert registration.checked_in_at is not None
            assert registration.survey_responses == {"GPU count?": "16"}

    run(assertions())


def test_event_lookup_respects_enrichment_enabled(event_storage) -> None:
    adapter, _ = event_storage
    event_id = run(
        adapter.upsert_event(
            EventInput(
                luma_event_id="evt-disabled",
                name="Disabled Event",
                event_date=None,
                location=None,
                url=None,
                status="upcoming",
                guest_count=0,
                enrichment_enabled=False,
            )
        )
    )

    event = run(adapter.get_event_by_luma_id("evt-disabled"))
    missing = run(adapter.get_event_by_luma_id("evt-missing"))

    assert event is not None
    assert event.id == event_id
    assert event.enrichment_enabled is False
    assert missing is None


def test_bulk_event_upsert_preserves_existing_enrichment_toggle(event_storage) -> None:
    adapter, _ = event_storage
    disabled_id = run(
        adapter.upsert_event(
            EventInput(
                luma_event_id="evt-disabled",
                name="Disabled Event",
                event_date=None,
                location=None,
                url=None,
                status="upcoming",
                guest_count=0,
                enrichment_enabled=False,
            )
        )
    )

    ids_by_luma_id = run(
        adapter.upsert_events(
            [
                EventInput(
                    luma_event_id="evt-disabled",
                    name="Disabled Event Updated",
                    event_date=datetime(2026, 6, 16, 18, 0, tzinfo=UTC),
                    location="San Francisco",
                    url="https://luma.com/disabled",
                    status="upcoming",
                    guest_count=10,
                    enrichment_enabled=True,
                ),
                EventInput(
                    luma_event_id="evt-new",
                    name="New Event",
                    event_date=None,
                    location=None,
                    url=None,
                    status="past",
                    guest_count=5,
                    enrichment_enabled=True,
                ),
            ]
        )
    )
    disabled = run(adapter.get_event_by_luma_id("evt-disabled"))
    created = run(adapter.get_event_by_luma_id("evt-new"))

    assert ids_by_luma_id["evt-disabled"] == disabled_id
    assert disabled is not None
    assert disabled.enrichment_enabled is False
    assert created is not None
    assert ids_by_luma_id["evt-new"] == created.id
    assert created.enrichment_enabled is True


def test_event_enrichment_toggle_updates_existing_event(event_storage) -> None:
    adapter, _ = event_storage
    event_id = run(adapter.upsert_event(event_input()))

    disabled = run(adapter.set_event_enrichment_enabled(event_id, False))
    enabled = run(adapter.set_event_enrichment_enabled(event_id, True))
    missing = run(adapter.set_event_enrichment_enabled("00000000-0000-0000-0000-000000000000", False))

    assert disabled is not None
    assert disabled["id"] == str(event_id)
    assert disabled["enrichment_enabled"] is False
    assert enabled is not None
    assert enabled["enrichment_enabled"] is True
    assert missing is None


def test_list_events_uses_event_date_for_effective_past_status(event_storage) -> None:
    adapter, _ = event_storage
    run(
        adapter.upsert_event(
            EventInput(
                luma_event_id="evt-stale-upcoming",
                name="Stale Upcoming",
                event_date=datetime(2026, 5, 27, 18, 0, tzinfo=UTC),
                location=None,
                url=None,
                status="upcoming",
                guest_count=0,
                enrichment_enabled=True,
            )
        )
    )

    all_events = run(adapter.list_events(status="all"))
    past_events = run(adapter.list_events(status="past"))
    upcoming_events = run(adapter.list_events(status="upcoming"))

    assert all_events[0]["status"] == "past"
    assert [event["luma_event_id"] for event in past_events] == ["evt-stale-upcoming"]
    assert upcoming_events == []


def test_registration_linking_checkins_and_attendance_counts(event_storage) -> None:
    adapter, session_factory = event_storage
    event_id = run(adapter.upsert_event(event_input()))
    run(adapter.upsert_registration(registration_input(), event_id, person_id=None))

    async def insert_person():
        async with session_factory() as session:
            async with session.begin():
                person_id = (
                    await session.execute(
                        insert(Person)
                        .values(email="alex@example.com", first_name="Alex", last_name="Rivera")
                        .returning(Person.id)
                    )
                ).scalar_one()
                return person_id

    person_id = run(insert_person())

    run(adapter.link_registration_person("alex@example.com", event_id, person_id))
    counts = run(adapter.update_checkins(event_id, ["alex@example.com"]))
    run(adapter.update_event_attendance_counts(person_id))

    async def assertions():
        async with session_factory() as session:
            registration = (
                await session.execute(select(Registration).where(Registration.email == "alex@example.com"))
            ).scalar_one()
            person = (await session.execute(select(Person).where(Person.id == person_id))).scalar_one()

            assert counts == {"checked_in": 1, "no_show": 0}
            assert registration.person_id == person_id
            assert registration.checked_in_at is not None
            assert person.events_registered == 1
            assert person.events_attended == 1
            assert person.last_event_at == datetime(2026, 5, 27, 18, 0, tzinfo=UTC)

    run(assertions())
