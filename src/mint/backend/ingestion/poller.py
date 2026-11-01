from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from mint.backend.classification.service import ClassificationScope
from mint.backend.ingestion.orchestrator import _event_input_from_luma
from mint.backend.models import SyncSummary


class LumaSyncService:
    def __init__(
        self,
        luma_client: Any,
        event_storage: Any,
        orchestrator: Any,
        classification_service: Any,
        *,
        max_event_age_days: int = 14,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.luma_client = luma_client
        self.event_storage = event_storage
        self.orchestrator = orchestrator
        self.classification_service = classification_service
        self.max_event_age_days = max_event_age_days
        self._now = now or (lambda: datetime.now(UTC))

    async def sync_managed_events(self, *, classify: bool = True) -> SyncSummary:
        summary = SyncSummary()
        events = await self.luma_client.list_events(status="all")
        summary.events_discovered = len(events)

        for event in events:
            try:
                event_input = _event_input_from_luma(
                    event,
                    fallback_luma_event_id=_luma_event_id(event),
                    guest_count=_guest_count(event),
                )
                if not event_input.luma_event_id:
                    summary.errors.append("missing luma_event_id")
                    continue
                if not _should_process_event(
                    event_input.event_date,
                    event_input.status,
                    now=self._now(),
                    max_event_age_days=self.max_event_age_days,
                ):
                    summary.events_skipped_stale += 1
                    continue

                existing = (
                    await self.event_storage.get_event_by_luma_id(event_input.luma_event_id)
                    if event_input.luma_event_id
                    else None
                )
                if existing is None:
                    summary.events_new += 1

                event_id = await self.event_storage.upsert_event(event_input)
                enrichment_enabled = existing.enrichment_enabled if existing is not None else event_input.enrichment_enabled
                if not enrichment_enabled:
                    continue

                ingest = await self.orchestrator.ingest_luma_event(event_input.luma_event_id, enrich=True)
                summary.events_processed += 1
                summary.total_enriched += ingest.enriched_count
                summary.total_skipped_fresh += ingest.skipped_fresh_count
                summary.errors.extend(ingest.errors)

                if classify and ingest.enriched_count > 0:
                    classification = await self.classification_service.classify_batch(
                        ClassificationScope(event_id=ingest.event_id or event_id)
                    )
                    summary.total_classified += classification.classified_count
                    summary.errors.extend(classification.errors)
            except Exception as exc:
                summary.errors.append(f"{_luma_event_id(event)}: {exc}")

        return summary


def _should_process_event(
    event_date: datetime | None,
    status: str | None,
    *,
    now: datetime,
    max_event_age_days: int,
) -> bool:
    normalized = (status or "").strip().lower()
    if normalized == "draft":
        return False
    if event_date is None:
        return normalized == "upcoming"
    event_date = event_date if event_date.tzinfo else event_date.replace(tzinfo=UTC)
    event_date = event_date.astimezone(UTC)
    now = now if now.tzinfo else now.replace(tzinfo=UTC)
    now = now.astimezone(UTC)
    return event_date > now or event_date >= now - timedelta(days=max_event_age_days)


def _luma_event_id(event: dict) -> str:
    value = event.get("api_id") or event.get("event_id") or event.get("id")
    return str(value or "").strip()


def _guest_count(event: dict) -> int:
    value = event.get("guest_count") or event.get("guests_count") or event.get("registration_count") or 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
