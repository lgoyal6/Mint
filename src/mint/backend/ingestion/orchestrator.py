from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from mint.backend.ingestion.normalizer import (
    csv_approval_status_by_email,
    normalize_csv_rows,
    normalize_luma_guests,
)
from mint.backend.models import EnrichmentInput, EventInput, IngestSummary, LeadError, RegistrationInput


class IngestionOrchestrator:
    def __init__(self, luma_client: Any, enrichment_service: Any, storage: Any, event_storage: Any) -> None:
        self.luma_client = luma_client
        self.enrichment_service = enrichment_service
        self.storage = storage
        self.event_storage = event_storage

    async def ingest_luma_event(
        self,
        luma_event_id: str,
        enrich: bool = True,
        on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> IngestSummary:
        event = await self.luma_client.get_event(luma_event_id)
        guests = await self.luma_client.get_guests(luma_event_id, status="all")
        event_input = _event_input_from_luma(event, fallback_luma_event_id=luma_event_id, guest_count=len(guests))
        event_id = await self.event_storage.upsert_event(event_input)

        await self._persist_registrations(guests, event_id)
        leads = normalize_luma_guests(guests, event_id=luma_event_id, include_all_statuses=True)
        return await self._enrich_and_link(
            leads if enrich else [],
            event_id=event_id,
            total_guests=len(guests),
            going_count=sum(
                1 for guest in guests if _approval_status(guest) in {"going", "approved"}
            ),
            on_progress=on_progress,
        )

    async def ingest_csv(
        self,
        rows: list[dict],
        event: EventInput | None,
        column_mapping=None,
        on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> IngestSummary:
        leads = normalize_csv_rows(rows, column_mapping)
        if rows and not leads:
            raise ValueError("No usable leads found. Make sure the file has a valid email column.")

        approval_statuses = csv_approval_status_by_email(rows, column_mapping)

        event_id: UUID | None = None
        if event is not None:
            event_id = await self.event_storage.upsert_event(event)
            registrations = [
                RegistrationInput(
                    email=lead.email,
                    approval_status=approval_statuses.get(lead.email, "going"),
                    checked_in_at=None,
                    survey_responses=_registration_survey_responses(lead),
                )
                for lead in leads
            ]
            await self._persist_csv_registrations(registrations, event_id)

        return await self._enrich_and_link(
            leads,
            event_id=event_id,
            total_guests=len(rows),
            going_count=sum(
                1 for lead in leads if approval_statuses.get(lead.email, "going") in {"going", "approved"}
            ),
            on_progress=on_progress,
        )

    async def _persist_csv_registrations(
        self,
        registrations: list[RegistrationInput],
        event_id: UUID,
    ) -> None:
        bulk_upsert = getattr(self.event_storage, "upsert_registrations", None)
        if bulk_upsert is not None:
            await bulk_upsert(registrations, event_id, {})
            return
        for registration in registrations:
            await self.event_storage.upsert_registration(registration, event_id, person_id=None)

    async def enrich_event_registrations(
        self,
        event_id: UUID,
        on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> IngestSummary:
        leads = await self.event_storage.enrichment_inputs_for_event(event_id)
        return await self._enrich_and_link(
            leads,
            event_id=event_id,
            total_guests=len(leads),
            going_count=len(leads),
            on_progress=on_progress,
        )

    async def retry_event_registrations(
        self,
        leads: list[EnrichmentInput],
        event_id: UUID,
        on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    ) -> IngestSummary:
        """Force provider retries for a preselected subset of unresolved registrations."""
        return await self._enrich_and_link(
            leads,
            event_id=event_id,
            total_guests=len(leads),
            going_count=len(leads),
            on_progress=on_progress,
            force_refresh=True,
        )

    async def sync_luma_checkins(self, luma_event_id: str) -> dict[str, int]:
        event = await self.event_storage.get_event_by_luma_id(luma_event_id)
        if event is None:
            return {"checked_in": 0, "no_show": 0}

        guests = await self.luma_client.get_guests(luma_event_id, status="all")
        checked_in_emails = [
            email
            for guest in guests
            if _is_checked_in(guest)
            for email in [_guest_email(guest)]
            if email
        ]
        counts = await self.event_storage.update_checkins(event.id, checked_in_emails)
        for person_id in await self.event_storage.person_ids_for_event(event.id):
            await self.event_storage.update_event_attendance_counts(person_id)
        return counts

    async def _persist_registrations(self, guests: list[dict], event_id: UUID) -> None:
        registrations = [
            reg
            for guest in guests
            for reg in [_registration_input_from_guest(guest)]
            if reg is not None
        ]
        bulk_upsert = getattr(self.event_storage, "upsert_registrations", None)
        person_ids_lookup = getattr(self.event_storage, "person_ids_by_email", None)
        if bulk_upsert is not None and person_ids_lookup is not None:
            person_ids = await person_ids_lookup([reg.email for reg in registrations])
            await bulk_upsert(registrations, event_id, person_ids)
            return

        for reg in registrations:
            person_id = await self.event_storage.get_person_id_by_email(reg.email)
            await self.event_storage.upsert_registration(reg, event_id, person_id=person_id)

    async def _enrich_and_link(
        self,
        leads: list[EnrichmentInput],
        *,
        event_id: UUID | None,
        total_guests: int,
        going_count: int,
        on_progress: Callable[[int, int], Awaitable[None]] | None = None,
        force_refresh: bool = False,
    ) -> IngestSummary:
        if event_id is not None:
            leads = [lead.model_copy(update={"event_id": str(event_id)}) for lead in leads]
        already_known_count = await self._already_known_count(leads)

        if not leads:
            results = []
        elif force_refresh:
            results = await self.enrichment_service.enrich_batch(
                leads,
                on_progress=on_progress,
                force_refresh=True,
            )
        else:
            results = await self.enrichment_service.enrich_batch(leads, on_progress=on_progress)
        linked_person_ids: set[UUID] = set()
        for lead, result in zip(leads, results, strict=False):
            if event_id is None:
                continue
            person_id = await self.event_storage.get_person_id_by_email(lead.email)
            if person_id is None:
                continue
            await self.event_storage.link_registration_person(lead.email, event_id, person_id)
            linked_person_ids.add(person_id)

        for person_id in linked_person_ids:
            await self.event_storage.update_event_attendance_counts(person_id)

        skipped_fresh_count = sum(1 for result in results if result.enrichment_sources == ["skipped_fresh"])
        return IngestSummary(
            event_id=event_id,
            total_guests=total_guests,
            going_count=going_count,
            enriched_count=len(results) - skipped_fresh_count,
            skipped_fresh_count=skipped_fresh_count,
            already_known_count=already_known_count,
            flagged_count=sum(1 for result in results if result.resolution.confidence == "flagged"),
            errors=[error for result in results for error in result.errors],
            lead_errors=_lead_errors(leads, results),
        )

    async def _already_known_count(self, leads: list[EnrichmentInput]) -> int:
        lookup = getattr(self.event_storage, "existing_person_emails", None)
        if lookup is not None:
            existing_emails = await lookup([lead.email for lead in leads])
            return sum(1 for lead in leads if lead.email in existing_emails)

        already_known_count = 0
        for lead in leads:
            if await self.event_storage.person_exists(lead.email):
                already_known_count += 1
        return already_known_count


def _lead_errors(leads: list[EnrichmentInput], results: list) -> list[LeadError]:
    output: list[LeadError] = []
    seen: set[str] = set()
    for lead, result in zip(leads, results, strict=False):
        if not result.errors or lead.email in seen:
            continue
        seen.add(lead.email)
        output.append(LeadError(email=lead.email, errors=list(result.errors)))
    return output


def _registration_survey_responses(lead: EnrichmentInput) -> dict[str, Any] | None:
    responses = dict(lead.survey_responses or {})
    context = {
        key: value
        for key, value in {
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "company_name": lead.company_name,
            "website_url": lead.website_url,
            "title": lead.title,
            "linkedin_url": lead.linkedin_url,
            "what_they_are_building": lead.what_they_are_building,
        }.items()
        if value
    }
    if context:
        responses["_mint_lead_context"] = context
    return responses or None


def _event_input_from_luma(event: dict, *, fallback_luma_event_id: str, guest_count: int) -> EventInput:
    event_date = _parse_datetime(event.get("start_at") or event.get("start_time") or event.get("event_date"))
    return EventInput(
        luma_event_id=_text(event.get("api_id") or event.get("event_id") or event.get("id")) or fallback_luma_event_id,
        name=_text(event.get("name") or event.get("title")) or fallback_luma_event_id,
        event_date=event_date,
        location=_location_text(event.get("location")),
        url=_text(event.get("url") or event.get("event_url")),
        status=_event_status_from_luma(event, event_date),
        guest_count=guest_count,
    )


def _event_status_from_luma(event: dict, event_date: datetime | None) -> str:
    raw_status = (_text(event.get("status")) or "").lower()
    if raw_status == "draft":
        return "draft"

    end_at = _parse_datetime(event.get("end_at") or event.get("end_time"))
    comparison_date = end_at or event_date
    if comparison_date is not None:
        comparison_date = comparison_date if comparison_date.tzinfo else comparison_date.replace(tzinfo=UTC)
        return "past" if comparison_date.astimezone(UTC) <= datetime.now(UTC) else "upcoming"

    if raw_status in {"past", "completed", "complete", "ended"}:
        return "past"
    return "upcoming"


def _registration_input_from_guest(guest: dict) -> RegistrationInput | None:
    email = _guest_email(guest)
    if not email:
        return None
    return RegistrationInput(
        email=email,
        approval_status=_approval_status(guest),
        checked_in_at=_checked_in_at(guest),
        survey_responses=_survey_responses(guest) or None,
    )


def _guest_email(guest: dict) -> str | None:
    return _text(guest.get("email") or guest.get("guest_email"))


def _approval_status(guest: dict) -> str:
    status = _text(guest.get("approval_status") or guest.get("status")) or "going"
    status = status.lower()
    if status == "approved":
        return "going"
    if status in {"cancelled", "canceled"}:
        return "declined"
    return status


def _checked_in_at(guest: dict) -> datetime | None:
    value = (
        guest.get("checked_in_at")
        or guest.get("check_in_at")
        or guest.get("checked_in_time")
        or guest.get("check_in_time")
    )
    parsed = _parse_datetime(value)
    if parsed is not None:
        return parsed
    if guest.get("checked_in") is True or guest.get("is_checked_in") is True:
        return datetime.now(UTC)
    return None


def _is_checked_in(guest: dict) -> bool:
    return _checked_in_at(guest) is not None


def _survey_responses(guest: dict) -> dict[str, Any]:
    answers = (
        guest.get("registration_answers")
        or guest.get("answers")
        or guest.get("custom_fields")
        or guest.get("survey_responses")
        or []
    )
    if isinstance(answers, dict):
        return {str(key): value for key, value in answers.items() if value not in (None, "")}
    if not isinstance(answers, list):
        return {}
    output: dict[str, Any] = {}
    for item in answers:
        if not isinstance(item, dict):
            continue
        question = item.get("question") or item.get("label") or item.get("question_text") or item.get("name")
        answer = item.get("answer") or item.get("value") or item.get("response")
        if question and answer not in (None, ""):
            output[str(question)] = answer
    return output


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _location_text(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("name", "address", "city"):
            text = _text(value.get(key))
            if text:
                return text
        return None
    return _text(value)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
