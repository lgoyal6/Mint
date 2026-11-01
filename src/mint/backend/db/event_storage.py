from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import distinct, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mint.backend.db.read_storage import MANUAL_RATING_KEYS
from mint.backend.classification.classifier import FAILURE_NP_TRIGGERS
from mint.backend.db.tables import Classification, Event, EventRating, Person, Registration
from mint.backend.models import EnrichmentInput, EventInput, RegistrationInput


@dataclass(frozen=True)
class StoredEvent:
    id: UUID
    luma_event_id: str | None
    enrichment_enabled: bool


class EventStorage:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def upsert_event(self, event: EventInput) -> UUID:
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(UTC)
                values = {
                    "luma_event_id": event.luma_event_id,
                    "name": event.name,
                    "event_date": event.event_date,
                    "location": event.location,
                    "url": event.url,
                    "status": event.status,
                    "guest_count": event.guest_count,
                    "enrichment_enabled": event.enrichment_enabled,
                    "synced_at": now,
                }
                if event.luma_event_id:
                    stmt = pg_insert(Event).values(**values)
                    upsert = (
                        stmt.on_conflict_do_update(
                            index_elements=[Event.luma_event_id],
                            set_={
                                "name": stmt.excluded.name,
                                "event_date": stmt.excluded.event_date,
                                "location": stmt.excluded.location,
                                "url": stmt.excluded.url,
                                "status": stmt.excluded.status,
                                "guest_count": stmt.excluded.guest_count,
                                "synced_at": stmt.excluded.synced_at,
                                "updated_at": func.now(),
                            },
                        )
                        .returning(Event.id)
                    )
                    return (await session.execute(upsert)).scalar_one()

                return (
                    await session.execute(
                        insert(Event).values(**values).returning(Event.id)
                    )
                ).scalar_one()

    async def upsert_events(self, events: list[EventInput]) -> dict[str, UUID]:
        luma_events = [event for event in events if event.luma_event_id]
        if not luma_events:
            return {}

        now = datetime.now(UTC)
        rows = [
            {
                "luma_event_id": event.luma_event_id,
                "name": event.name,
                "event_date": event.event_date,
                "location": event.location,
                "url": event.url,
                "status": event.status,
                "guest_count": event.guest_count,
                "enrichment_enabled": event.enrichment_enabled,
                "synced_at": now,
            }
            for event in luma_events
        ]
        async with self.session_factory() as session:
            async with session.begin():
                stmt = pg_insert(Event).values(rows)
                result = await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[Event.luma_event_id],
                        set_={
                            "name": stmt.excluded.name,
                            "event_date": stmt.excluded.event_date,
                            "location": stmt.excluded.location,
                            "url": stmt.excluded.url,
                            "status": stmt.excluded.status,
                            "guest_count": stmt.excluded.guest_count,
                            "synced_at": stmt.excluded.synced_at,
                            "updated_at": func.now(),
                        },
                    ).returning(Event.luma_event_id, Event.id)
                )
                return {row.luma_event_id: row.id for row in result if row.luma_event_id}

    async def get_event_by_luma_id(self, luma_event_id: str) -> StoredEvent | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(Event.id, Event.luma_event_id, Event.enrichment_enabled).where(
                        Event.luma_event_id == luma_event_id
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        return StoredEvent(
            id=row.id,
            luma_event_id=row.luma_event_id,
            enrichment_enabled=bool(row.enrichment_enabled),
        )

    async def get_event(self, event_id: UUID) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            event = (await session.execute(select(Event).where(Event.id == event_id))).scalar_one_or_none()
            return _event_dict(event) if event is not None else None

    async def list_events(self, status: str = "all") -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            stmt = select(Event)
            rows = await session.execute(
                stmt.order_by(Event.event_date.desc().nulls_last(), Event.created_at.desc())
            )
            events = [_event_dict(event) for event in rows.scalars()]
            if status != "all":
                events = [event for event in events if event["status"] == status]
            return events

    async def set_event_enrichment_enabled(self, event_id: UUID, enabled: bool) -> dict[str, Any] | None:
        async with self.session_factory() as session:
            async with session.begin():
                event = (
                    await session.execute(
                        update(Event)
                        .where(Event.id == event_id)
                        .values(enrichment_enabled=enabled, updated_at=func.now())
                        .returning(Event)
                    )
                ).scalar_one_or_none()
                return _event_dict(event) if event is not None else None

    async def upsert_event_rating(
        self,
        event_id: UUID,
        *,
        scores: dict[str, Any],
        notes: str | None,
        rated_by: str | None = None,
    ) -> None:
        cleaned = {
            key: int(value)
            for key, value in (scores or {}).items()
            if key in MANUAL_RATING_KEYS and isinstance(value, (int, float)) and 1 <= int(value) <= 5
        }
        async with self.session_factory() as session:
            async with session.begin():
                stmt = pg_insert(EventRating).values(
                    event_id=event_id,
                    scores=cleaned,
                    notes=notes,
                    rated_by=rated_by,
                )
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[EventRating.event_id],
                        set_={
                            "scores": stmt.excluded.scores,
                            "notes": stmt.excluded.notes,
                            "rated_by": stmt.excluded.rated_by,
                            "updated_at": func.now(),
                        },
                    )
                )

    async def upsert_registration(
        self,
        reg: RegistrationInput,
        event_id: UUID,
        person_id: UUID | None,
    ) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                now = datetime.now(UTC)
                stmt = pg_insert(Registration).values(
                    person_id=person_id,
                    event_id=event_id,
                    email=reg.email,
                    approval_status=reg.approval_status,
                    checked_in_at=reg.checked_in_at,
                    survey_responses=reg.survey_responses,
                    synced_at=now,
                )
                await session.execute(
                    stmt.on_conflict_do_update(
                        constraint="uq_registrations_event_email",
                        set_={
                            "person_id": func.coalesce(stmt.excluded.person_id, Registration.person_id),
                            "approval_status": stmt.excluded.approval_status,
                            "checked_in_at": func.coalesce(
                                stmt.excluded.checked_in_at,
                                Registration.checked_in_at,
                            ),
                            "survey_responses": stmt.excluded.survey_responses,
                            "synced_at": stmt.excluded.synced_at,
                        },
                    )
                )

    async def upsert_registrations(
        self,
        registrations: list[RegistrationInput],
        event_id: UUID,
        person_ids_by_email: dict[str, UUID],
    ) -> None:
        if not registrations:
            return
        now = datetime.now(UTC)
        unique_regs = {reg.email: reg for reg in registrations}
        rows = [
            {
                "person_id": person_ids_by_email.get(reg.email),
                "event_id": event_id,
                "email": reg.email,
                "approval_status": reg.approval_status,
                "checked_in_at": reg.checked_in_at,
                "survey_responses": reg.survey_responses,
                "synced_at": now,
            }
            for reg in unique_regs.values()
        ]
        async with self.session_factory() as session:
            async with session.begin():
                stmt = pg_insert(Registration).values(rows)
                await session.execute(
                    stmt.on_conflict_do_update(
                        constraint="uq_registrations_event_email",
                        set_={
                            "person_id": func.coalesce(stmt.excluded.person_id, Registration.person_id),
                            "approval_status": stmt.excluded.approval_status,
                            "checked_in_at": func.coalesce(
                                stmt.excluded.checked_in_at,
                                Registration.checked_in_at,
                            ),
                            "survey_responses": stmt.excluded.survey_responses,
                            "synced_at": stmt.excluded.synced_at,
                        },
                    )
                )

    async def link_registration_person(self, email: str, event_id: UUID, person_id: UUID) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(Registration)
                    .where(
                        Registration.event_id == event_id,
                        Registration.email == email.strip().lower(),
                    )
                    .values(person_id=person_id, synced_at=datetime.now(UTC))
                )

    async def update_checkins(self, event_id: UUID, checked_in_emails: list[str]) -> dict[str, int]:
        normalized_emails = sorted({email.strip().lower() for email in checked_in_emails if email.strip()})
        async with self.session_factory() as session:
            async with session.begin():
                if normalized_emails:
                    await session.execute(
                        update(Registration)
                        .where(
                            Registration.event_id == event_id,
                            Registration.email.in_(normalized_emails),
                        )
                        .values(checked_in_at=func.now(), synced_at=datetime.now(UTC))
                    )
                checked_in = await session.scalar(
                    select(func.count())
                    .select_from(Registration)
                    .where(
                        Registration.event_id == event_id,
                        Registration.approval_status.in_(["going", "approved"]),
                        Registration.checked_in_at.is_not(None),
                    )
                )
                no_show = await session.scalar(
                    select(func.count())
                    .select_from(Registration)
                    .where(
                        Registration.event_id == event_id,
                        Registration.approval_status.in_(["going", "approved"]),
                        Registration.checked_in_at.is_(None),
                    )
                )
        return {"checked_in": int(checked_in or 0), "no_show": int(no_show or 0)}

    async def update_event_attendance_counts(self, person_id: UUID) -> None:
        async with self.session_factory() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(
                            func.count(Registration.id),
                            func.count(Registration.checked_in_at),
                            func.max(Event.event_date),
                        )
                        .select_from(Registration)
                        .join(Event, Registration.event_id == Event.id)
                        .where(Registration.person_id == person_id)
                    )
                ).one()
                await session.execute(
                    update(Person)
                    .where(Person.id == person_id)
                    .values(
                        events_registered=int(row[0] or 0),
                        events_attended=int(row[1] or 0),
                        last_event_at=row[2],
                        updated_at=func.now(),
                    )
                )

    async def get_person_id_by_email(self, email: str) -> UUID | None:
        async with self.session_factory() as session:
            return await session.scalar(select(Person.id).where(Person.email == email.strip().lower()))

    async def person_ids_by_email(self, emails: list[str]) -> dict[str, UUID]:
        normalized = sorted({email.strip().lower() for email in emails if email.strip()})
        if not normalized:
            return {}
        async with self.session_factory() as session:
            rows = await session.execute(select(Person.email, Person.id).where(Person.email.in_(normalized)))
            return {row.email: row.id for row in rows}

    async def person_exists(self, email: str) -> bool:
        return await self.get_person_id_by_email(email) is not None

    async def existing_person_emails(self, emails: list[str]) -> set[str]:
        normalized = sorted({email.strip().lower() for email in emails if email.strip()})
        if not normalized:
            return set()
        async with self.session_factory() as session:
            rows = await session.execute(select(Person.email).where(Person.email.in_(normalized)))
            return {row[0] for row in rows if row[0]}

    async def enrichment_inputs_for_event(self, event_id: UUID) -> list[EnrichmentInput]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(Registration, Person)
                .outerjoin(Person, Registration.person_id == Person.id)
                .where(Registration.event_id == event_id)
                .order_by(Registration.created_at.asc())
            )
            return [
                _enrichment_input_from_registration(registration, person)
                for registration, person in rows
            ]

    async def retryable_leads_for_event(
        self, event_id: UUID
    ) -> tuple[list[EnrichmentInput], list[UUID]]:
        """Return unresolved/flagged leads to re-enrich and transient failures to reclassify."""
        async with self.session_factory() as session:
            rows = await session.execute(
                select(Registration, Person, Classification)
                .join(Person, Registration.person_id == Person.id)
                .join(Classification, Person.current_classification_id == Classification.id)
                .where(
                    Registration.event_id == event_id,
                    Classification.reviewed_at.is_(None),
                    or_(
                        Person.resolution_confidence.in_(["low", "flagged"]),
                        Classification.np_trigger == "sparse_data",
                        Classification.np_trigger.in_(FAILURE_NP_TRIGGERS),
                    ),
                )
                .order_by(Registration.created_at.asc())
            )
            sparse_inputs: list[EnrichmentInput] = []
            person_ids: list[UUID] = []
            for registration, person, classification in rows:
                person_ids.append(person.id)
                if (
                    person.resolution_confidence in {"low", "flagged"}
                    or classification.np_trigger == "sparse_data"
                ):
                    sparse_inputs.append(_enrichment_input_from_registration(registration, person))
            return sparse_inputs, person_ids

    async def person_ids_for_event(self, event_id: UUID) -> list[UUID]:
        async with self.session_factory() as session:
            rows = await session.execute(
                select(distinct(Registration.person_id)).where(
                    Registration.event_id == event_id,
                    Registration.person_id.is_not(None),
                )
            )
            return [row[0] for row in rows if row[0] is not None]


def _event_dict(event: Event) -> dict[str, Any]:
    status = _effective_event_status(event.status, event.event_date)
    return {
        "id": str(event.id),
        "luma_event_id": event.luma_event_id,
        "name": event.name,
        "event_date": event.event_date.isoformat() if event.event_date else None,
        "location": event.location,
        "url": event.url,
        "status": status,
        "guest_count": event.guest_count,
        "enrichment_enabled": event.enrichment_enabled,
        "synced_at": event.synced_at.isoformat() if event.synced_at else None,
        "source": "luma" if event.luma_event_id else "csv",
    }


def _enrichment_input_from_registration(registration: Registration, person: Person | None) -> EnrichmentInput:
    survey_responses = dict(registration.survey_responses or {})
    context = survey_responses.pop("_mint_lead_context", {})
    if not isinstance(context, dict):
        context = {}
    return EnrichmentInput(
        email=registration.email,
        first_name=(person.first_name if person else None) or context.get("first_name"),
        last_name=(person.last_name if person else None) or context.get("last_name"),
        company_name=(person.self_reported_company if person else None) or context.get("company_name"),
        website_url=context.get("website_url")
        or _survey_answer(
            survey_responses,
            {"website", "website url", "company website", "organization website"},
        ),
        title=(person.self_reported_title if person else None) or context.get("title"),
        linkedin_url=(person.apollo_person_linkedin_url if person else None) or context.get("linkedin_url"),
        what_they_are_building=(person.self_reported_building if person else None)
        or context.get("what_they_are_building"),
        source="csv",
        event_id=str(registration.event_id),
        survey_responses=survey_responses or None,
    )


def _survey_answer(responses: dict[str, Any], aliases: set[str]) -> str | None:
    for key, value in responses.items():
        normalized = " ".join(
            str(key).strip().lower().replace("_", " ").replace("-", " ").split()
        ).rstrip("?")
        if normalized not in aliases or value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _effective_event_status(status: str | None, event_date: datetime | None) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "draft":
        return "draft"
    if event_date is not None:
        event_date = event_date if event_date.tzinfo else event_date.replace(tzinfo=UTC)
        return "past" if event_date.astimezone(UTC) <= datetime.now(UTC) else "upcoming"
    if normalized in {"past", "completed", "complete", "ended"}:
        return "past"
    return "upcoming"
