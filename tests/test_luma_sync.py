import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from mint.backend.classification.service import ClassificationScope
from mint.backend.ingestion.poller import LumaSyncService
from mint.backend.models import ClassifyResult, IngestSummary


def run(coro):
    return asyncio.run(coro)


class FakeLumaClient:
    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.statuses: list[str] = []

    async def list_events(self, status: str = "all") -> list[dict]:
        self.statuses.append(status)
        return self.events


class FakeEventStorage:
    def __init__(
        self,
        existing_luma_ids: set[str] | None = None,
        disabled_luma_ids: set[str] | None = None,
    ) -> None:
        self.existing_luma_ids = existing_luma_ids or set()
        self.disabled_luma_ids = disabled_luma_ids or set()
        self.ids_by_luma_id: dict[str, object] = {}
        self.upserted = []

    async def get_event_by_luma_id(self, luma_event_id: str):
        if luma_event_id not in self.existing_luma_ids:
            return None
        event_id = self.ids_by_luma_id.setdefault(luma_event_id, uuid4())
        return SimpleNamespace(
            id=event_id,
            luma_event_id=luma_event_id,
            enrichment_enabled=luma_event_id not in self.disabled_luma_ids,
        )

    async def upsert_event(self, event):
        self.upserted.append(event)
        self.existing_luma_ids.add(event.luma_event_id)
        return self.ids_by_luma_id.setdefault(event.luma_event_id, uuid4())


class FakeOrchestrator:
    def __init__(self) -> None:
        self.pulls: list[tuple[str, bool]] = []
        self.summaries: dict[str, IngestSummary] = {}

    async def ingest_luma_event(self, luma_event_id: str, enrich: bool = True) -> IngestSummary:
        self.pulls.append((luma_event_id, enrich))
        return self.summaries.get(
            luma_event_id,
            IngestSummary(event_id=uuid4(), total_guests=3, going_count=2, enriched_count=2),
        )


class StatefulDeltaOrchestrator:
    def __init__(self) -> None:
        self.calls = 0
        self.event_id = uuid4()

    async def ingest_luma_event(self, luma_event_id: str, enrich: bool = True) -> IngestSummary:
        self.calls += 1
        if self.calls == 1:
            return IngestSummary(event_id=self.event_id, total_guests=2, going_count=1, enriched_count=1)
        return IngestSummary(
            event_id=self.event_id,
            total_guests=2,
            going_count=1,
            enriched_count=0,
            skipped_fresh_count=1,
        )


class ConcurrencyTrackingOrchestrator:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def ingest_luma_event(self, luma_event_id: str, enrich: bool = True) -> IngestSummary:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        return IngestSummary(event_id=uuid4(), total_guests=1, going_count=1, enriched_count=1)


class FakeClassificationService:
    def __init__(self) -> None:
        self.scopes: list[ClassificationScope] = []

    async def classify_batch(self, scope: ClassificationScope) -> ClassifyResult:
        self.scopes.append(scope)
        return ClassifyResult(total_count=2, classified_count=2, skipped_count=0)


def event(api_id: str, when: datetime, *, status: str = "upcoming") -> dict:
    return {
        "api_id": api_id,
        "name": api_id,
        "start_at": when.isoformat(),
        "status": status,
    }


def test_sync_managed_events_processes_recent_and_upcoming_events_and_skips_stale() -> None:
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    recent = event("evt-recent", now - timedelta(days=2), status="past")
    upcoming = event("evt-upcoming", now + timedelta(days=3), status="upcoming")
    stale = event("evt-stale", now - timedelta(days=30), status="past")
    luma = FakeLumaClient([recent, upcoming, stale])
    event_storage = FakeEventStorage(existing_luma_ids={"evt-recent"})
    orchestrator = FakeOrchestrator()
    classification = FakeClassificationService()
    service = LumaSyncService(
        luma,
        event_storage,
        orchestrator,
        classification,
        max_event_age_days=14,
        now=lambda: now,
    )

    summary = run(service.sync_managed_events(classify=True))

    assert luma.statuses == ["all"]
    assert [event.luma_event_id for event in event_storage.upserted] == ["evt-recent", "evt-upcoming"]
    assert orchestrator.pulls == [("evt-recent", True)]
    assert summary.events_discovered == 3
    assert summary.events_new == 1
    assert summary.events_processed == 1
    assert summary.events_skipped_stale == 1
    assert summary.total_enriched == 2
    assert summary.total_classified == 2
    assert len(classification.scopes) == 1


def test_sync_managed_events_reports_delta_skipped_fresh_and_does_not_reclassify_without_new_enrichment() -> None:
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    luma = FakeLumaClient([event("evt-recent", now - timedelta(days=1), status="past")])
    event_storage = FakeEventStorage(existing_luma_ids={"evt-recent"})
    orchestrator = FakeOrchestrator()
    orchestrator.summaries["evt-recent"] = IngestSummary(
        event_id=uuid4(),
        total_guests=3,
        going_count=2,
        enriched_count=0,
        skipped_fresh_count=2,
    )
    classification = FakeClassificationService()
    service = LumaSyncService(
        luma,
        event_storage,
        orchestrator,
        classification,
        now=lambda: now,
    )

    summary = run(service.sync_managed_events(classify=True))

    assert summary.events_processed == 1
    assert summary.total_enriched == 0
    assert summary.total_skipped_fresh == 2
    assert summary.total_classified == 0
    assert classification.scopes == []


def test_sync_managed_events_skips_disabled_existing_events() -> None:
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    luma = FakeLumaClient([event("evt-disabled", now + timedelta(days=1), status="upcoming")])
    event_storage = FakeEventStorage(
        existing_luma_ids={"evt-disabled"},
        disabled_luma_ids={"evt-disabled"},
    )
    orchestrator = FakeOrchestrator()
    classification = FakeClassificationService()
    service = LumaSyncService(luma, event_storage, orchestrator, classification, now=lambda: now)

    summary = run(service.sync_managed_events(classify=True))

    assert [event.luma_event_id for event in event_storage.upserted] == ["evt-disabled"]
    assert orchestrator.pulls == []
    assert classification.scopes == []
    assert summary.events_processed == 0
    assert summary.total_enriched == 0


def test_sync_managed_events_second_run_relies_on_freshness_delta() -> None:
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    luma = FakeLumaClient([event("evt-recent", now, status="upcoming")])
    event_storage = FakeEventStorage(existing_luma_ids={"evt-recent"})
    orchestrator = StatefulDeltaOrchestrator()
    classification = FakeClassificationService()
    service = LumaSyncService(luma, event_storage, orchestrator, classification, now=lambda: now)

    first = run(service.sync_managed_events(classify=True))
    second = run(service.sync_managed_events(classify=True))

    assert first.total_enriched == 1
    assert first.total_skipped_fresh == 0
    assert first.total_classified == 2
    assert second.total_enriched == 0
    assert second.total_skipped_fresh == 1
    assert second.total_classified == 0


def test_sync_managed_events_processes_events_sequentially() -> None:
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    luma = FakeLumaClient(
        [
            event("evt-one", now, status="upcoming"),
            event("evt-two", now, status="upcoming"),
            event("evt-three", now, status="upcoming"),
        ]
    )
    event_storage = FakeEventStorage(existing_luma_ids={"evt-one", "evt-two", "evt-three"})
    orchestrator = ConcurrencyTrackingOrchestrator()
    classification = FakeClassificationService()
    service = LumaSyncService(luma, event_storage, orchestrator, classification, now=lambda: now)

    summary = run(service.sync_managed_events(classify=False))

    assert summary.events_processed == 3
    assert orchestrator.max_active == 1


def test_sync_managed_events_respects_classify_flag() -> None:
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    luma = FakeLumaClient([event("evt-recent", now, status="upcoming")])
    event_storage = FakeEventStorage(existing_luma_ids={"evt-recent"})
    orchestrator = FakeOrchestrator()
    classification = FakeClassificationService()
    service = LumaSyncService(luma, event_storage, orchestrator, classification, now=lambda: now)

    summary = run(service.sync_managed_events(classify=False))

    assert summary.events_new == 0
    assert summary.events_processed == 1
    assert summary.total_enriched == 2
    assert summary.total_classified == 0
    assert classification.scopes == []


def test_sync_does_not_run_auto_review_sweep_after_processing() -> None:
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    service = LumaSyncService(
        FakeLumaClient([event("evt-1", now + timedelta(days=1))]),
        FakeEventStorage(),
        FakeOrchestrator(),
        FakeClassificationService(),
        now=lambda: now,
    )

    summary = run(service.sync_managed_events())

    assert summary.errors == []
