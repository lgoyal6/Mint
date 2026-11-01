import asyncio
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from openpyxl import Workbook

from mint.backend.app import create_app
from mint.backend.classification.service import ClassificationScope
from mint.backend.db.read_storage import (
    ClassificationProfile,
    CompanyProfile,
    FlaggedLead,
    LeadDetail,
    LeadPage,
    LeadSummary,
    NeedsReviewLead,
    PersonProfile,
    RegistrationProfile,
    ResolutionActionResult,
    ReviewPage,
)
from mint.backend.models import ClassifyResult, IngestSummary
from mint.backend.routes.events import MAX_UPLOAD_ROWS


class FakeOrchestrator:
    def __init__(self, event_id=None) -> None:
        self.event_id = event_id or uuid4()
        self.pulls: list[tuple[str, bool]] = []
        self.checkins: list[str] = []
        self.csv_uploads: list[tuple[list[dict], object | None, object | None]] = []
        self.event_backfills: list[object] = []

    async def ingest_luma_event(self, luma_event_id: str, enrich: bool = True, on_progress=None) -> IngestSummary:
        self.pulls.append((luma_event_id, enrich))
        return IngestSummary(event_id=self.event_id, total_guests=2, going_count=1, enriched_count=1)

    async def sync_luma_checkins(self, luma_event_id: str) -> dict[str, int]:
        self.checkins.append(luma_event_id)
        return {"checked_in": 1, "no_show": 1}

    async def ingest_csv(self, rows: list[dict], event, column_mapping=None, on_progress=None) -> IngestSummary:
        self.csv_uploads.append((rows, event, column_mapping))
        return IngestSummary(event_id=self.event_id if event else None, total_guests=len(rows), going_count=len(rows), enriched_count=len(rows))

    async def enrich_event_registrations(self, event_id, on_progress=None) -> IngestSummary:
        self.event_backfills.append(event_id)
        return IngestSummary(event_id=event_id, total_guests=2, going_count=2, enriched_count=2)


class FakeEventStorage:
    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.upserted_events = []
        self.upserted_event_batches = []
        self.enrichment_updates: list[tuple[object, bool]] = []

    async def list_events(self, status: str = "all") -> list[dict]:
        self.statuses.append(status)
        return [
            {
                "id": "csv-event-id",
                "luma_event_id": None,
                "name": "CSV Partner List",
                "event_date": "2026-05-28T02:00:00+00:00",
                "status": "past",
                "source": "csv",
            },
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "luma_event_id": "evt-live",
                "name": "Stored Live Luma Event",
                "event_date": "2026-06-01T02:00:00+00:00",
                "status": "past",
                "source": "luma",
                "enrichment_enabled": False,
            },
            {
                "id": "00000000-0000-0000-0000-000000000003",
                "luma_event_id": "evt-1",
                "name": "Tracked Luma Event",
                "event_date": "2026-05-27T02:00:00+00:00",
                "status": "past",
                "source": "luma",
                "enrichment_enabled": True,
            },
        ]

    async def upsert_event(self, event):
        self.upserted_events.append(event)
        if event.luma_event_id == "evt-upcoming":
            return "00000000-0000-0000-0000-000000000002"
        return "00000000-0000-0000-0000-000000000001"

    async def upsert_events(self, events):
        self.upserted_event_batches.append(events)
        self.upserted_events.extend(events)
        return {
            event.luma_event_id: (
                "00000000-0000-0000-0000-000000000002"
                if event.luma_event_id == "evt-upcoming"
                else "00000000-0000-0000-0000-000000000001"
            )
            for event in events
            if event.luma_event_id
        }

    async def set_event_enrichment_enabled(self, event_id, enabled: bool):
        self.enrichment_updates.append((event_id, enabled))
        if str(event_id) == "00000000-0000-0000-0000-000000000000":
            return None
        return {
            "id": str(event_id),
            "luma_event_id": "evt-live",
            "name": "Live Luma Event",
            "event_date": "2026-06-01T02:00:00+00:00",
            "status": "past",
            "source": "luma",
            "enrichment_enabled": enabled,
        }

    async def get_event(self, event_id):
        return {
            "id": str(event_id),
            "luma_event_id": None,
            "name": "CSV Partner List",
            "event_date": "2026-05-28T02:00:00+00:00",
            "status": "past",
            "source": "csv",
            "enrichment_enabled": True,
        }


class FakeLumaClient:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    async def list_events(self, status: str = "all") -> list[dict]:
        self.statuses.append(status)
        now = datetime.now(UTC)
        return [
            {
                "api_id": "evt-live",
                "name": "Live Luma Event",
                "start_at": (now - timedelta(days=2)).isoformat(),
                "guest_count": 12,
                "url": "https://luma.com/live",
            },
            {
                "api_id": "evt-upcoming",
                "name": "Upcoming Luma Event",
                "start_at": (now + timedelta(days=30)).isoformat(),
                "guest_count": 20,
                "url": "https://luma.com/upcoming",
            }
        ]


class FakeClassificationService:
    def __init__(self) -> None:
        self.scopes: list[ClassificationScope] = []

    async def classify_batch(self, scope: ClassificationScope, on_progress=None) -> ClassifyResult:
        self.scopes.append(scope)
        return ClassifyResult(total_count=2, classified_count=2, skipped_count=0)


class FakeLumaSyncService:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def sync_managed_events(self, *, classify: bool = True):
        self.calls.append(classify)
        return SimpleNamespace(
            model_dump=lambda mode="json": {
                "events_discovered": 1,
                "events_new": 1,
                "events_processed": 1,
                "events_skipped_stale": 0,
                "total_enriched": 2,
                "total_skipped_fresh": 0,
                "total_classified": 2 if classify else 0,
                "errors": [],
            }
        )


class FakeJobManager:
    def __init__(self) -> None:
        self.started = []
        self.records = {}

    def start(self, name, runner, *, key=None):
        job_id = uuid4()
        self.started.append((name, runner))
        record = SimpleNamespace(
            id=job_id,
            name=name,
            status="queued",
            result=None,
            error=None,
            model_dump=lambda mode="json": {
                "id": str(job_id),
                "name": name,
                "status": "queued",
                "result": None,
                "error": None,
            },
        )
        self.records[job_id] = record
        return record

    def get(self, job_id):
        return self.records.get(job_id)


class FakeEnrichmentService:
    def __init__(self) -> None:
        self.resolved_calls = []
        self.refresh_person_calls = []

    async def enrich_resolved_lead(self, input, resolution, person, *, refresh_person=False):
        self.resolved_calls.append((input, resolution, person))
        self.refresh_person_calls.append(refresh_person)
        return SimpleNamespace(model_dump=lambda mode="json": {"enriched": True})


class FakeLeadExportService:
    def __init__(self) -> None:
        self.files: dict[str, Path] = {}

    def resolve_artifact(self, filename: str) -> Path | None:
        return self.files.get(filename)


class FakeAgentLoop:
    def __init__(self) -> None:
        self.messages = []

    async def run(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(
            model_dump=lambda mode="json": {
                "reply": "Found one lead.",
                "tool_trace": [{"name": "list_leads", "input": {"tier": "P0"}}],
            }
        )

    async def run_stream(self, messages):
        self.messages.append(messages)
        yield {"type": "thinking"}
        yield {"type": "tool", "status": "started", "name": "list_leads", "input": {"tier": "P0"}}
        yield {"type": "tool", "status": "finished", "name": "list_leads", "short_result": "items=1 items"}
        yield {
            "type": "result",
            "reply": "Found one lead.",
            "tool_trace": [{"name": "list_leads", "input": {"tier": "P0"}}],
        }


class SlowAgentLoop(FakeAgentLoop):
    async def run_stream(self, messages):
        self.messages.append(messages)
        await asyncio.sleep(0.02)
        yield {
            "type": "result",
            "reply": "Found one lead.",
            "tool_trace": [{"name": "list_leads", "input": {"tier": "P0"}}],
        }


class FakeReadStorage:
    def __init__(self) -> None:
        self.person_id = uuid4()
        self.company_id = uuid4()
        self.event_id = uuid4()
        self.lead_filters = []
        self.flagged_resolutions = []
        self.reclassifications = []

    async def list_leads(self, filters, *, sort: str = "tier", limit: int = 50, offset: int = 0) -> LeadPage:
        self.lead_filters.append((filters, sort, limit, offset))
        return LeadPage(
            total=1,
            items=[
                LeadSummary(
                    email="founder@agentco.ai",
                    name="Alex Founder",
                    title="Founder",
                    resolved_company="AgentCo",
                    resolved_domain="agentco.ai",
                    tier="P0",
                    primary_product="maas",
                    product_fit=["maas"],
                    confidence=5,
                    target_vertical="agentic",
                    review_status="CLASSIFIED",
                    key_signal="Founder + inference",
                    current_classification_id=uuid4(),
                    person_id=self.person_id,
                )
            ],
        )

    async def get_lead_detail(self, person_id) -> LeadDetail | None:
        if person_id != self.person_id:
            return None
        return self._detail("P0")

    async def list_flagged(self, *, limit: int = 50, offset: int = 0) -> ReviewPage:
        return ReviewPage(
            total=1,
            items=[
                FlaggedLead(
                    person_id=self.person_id,
                    email="founder@agentco.ai",
                    candidate_companies=[{"domain": "agentco.ai", "name": "AgentCo"}],
                    resolution_evidence={"chain": ["conflict"]},
                )
            ],
        )

    async def resolve_flagged_person(self, person_id, **kwargs) -> ResolutionActionResult:
        self.flagged_resolutions.append((person_id, kwargs))
        return ResolutionActionResult(
            person_id=person_id,
            company_id=self.company_id,
            domain=kwargs.get("chosen_domain") or "agentco.ai",
            company_name="AgentCo",
            affiliation_state="verified",
            resolution_confidence="high",
        )

    async def enrichment_context_for_person(self, person_id):
        return SimpleNamespace(input="input", resolution="resolution", person="person")

    async def list_needs_review(
        self,
        *,
        queue_type: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> ReviewPage:
        return ReviewPage(
            total=1,
            items=[
                NeedsReviewLead(
                    person_id=self.person_id,
                    email="founder@agentco.ai",
                    reason="confidence <= 2",
                    source="classification",
                    tier="P1",
                    confidence=2,
                    review_status="NEEDS_REVIEW",
                )
            ],
        )

    async def reclassify_person(self, person_id, *, new_tier: str, reason: str, reviewed_by: str | None = None):
        self.reclassifications.append((person_id, new_tier, reason, reviewed_by))
        return self._detail(new_tier, reviewed_by=reviewed_by)

    async def event_summary(self, event_id):
        return {"event": {"id": str(event_id)}, "counts": {"classified": 1}, "tier_breakdown": {"P0": 1}}

    async def pipeline_summary(self):
        return {"p0_p1_by_primary_product": {"maas": 1}}

    async def data_quality_summary(self):
        return {"resolution": {"total_people": 1, "high_medium": 1}, "np_trigger_breakdown": {}}

    def _detail(self, tier: str, reviewed_by: str | None = None) -> LeadDetail:
        return LeadDetail(
            person=PersonProfile(id=self.person_id, email="founder@agentco.ai", name="Alex Founder"),
            company=CompanyProfile(id=self.company_id, name="AgentCo", domain="agentco.ai"),
            current_classification=ClassificationProfile(
                id=uuid4(),
                tier=tier,
                product_fit=["maas"],
                primary_product="maas",
                confidence=5,
                reasoning="Current classification.",
                key_signal="Founder + inference",
                key_risk="None",
                review_status="CLASSIFIED",
                reviewed_by=reviewed_by,
            ),
            classification_history=[],
            registrations=[
                RegistrationProfile(
                    id=uuid4(),
                    event_id=self.event_id,
                    event_name="Hard Problems",
                    approval_status="going",
                )
            ],
        )


def run_async(coro):
    return asyncio.run(coro)


class FakeAutoReviewService:
    def __init__(self) -> None:
        self.sweeps = 0

    async def sweep(self):
        self.sweeps += 1
        return SimpleNamespace(
            model_dump=lambda mode="json": {
                "reviews_confirmed": 2,
                "reviews_adjusted": 1,
                "reviews_uncertain": 1,
                "conflicts_resolved": 0,
                "conflicts_uncertain": 0,
                "errors": [],
            }
        )


class FakeClassificationResetStorage:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def reset_all_classifications(self, *, dismiss_unresolved: bool):
        self.calls.append(dismiss_unresolved)
        return {"classifications_deleted": 3, "people_reset": 5, "people_dismissed": 2}


def make_context(event_id=None):
    return SimpleNamespace(
        classification_storage=FakeClassificationResetStorage(),
        auto_review_service=FakeAutoReviewService(),
        settings=SimpleNamespace(
            mint_auth_enabled=False,
            mint_auth_password="test-password",
            mint_session_secret="test-session-secret",
            mint_session_cookie_name="mint_session",
            mint_session_max_age_seconds=3600,
            mint_cookie_secure=False,
            mint_cookie_samesite="lax",
            mint_sync_token="sync-token-test",
        ),
        orchestrator=FakeOrchestrator(event_id=event_id),
        event_storage=FakeEventStorage(),
        luma_client=FakeLumaClient(),
        classification_service=FakeClassificationService(),
        enrichment_service=FakeEnrichmentService(),
        read_storage=FakeReadStorage(),
        agent_loop=FakeAgentLoop(),
        luma_sync_service=FakeLumaSyncService(),
        job_manager=FakeJobManager(),
        lead_export_service=FakeLeadExportService(),
    )


def test_app_lifespan_builds_context_once_and_health_is_live() -> None:
    calls = {"build": 0, "close": 0}

    async def build_context(_settings):
        calls["build"] += 1
        return make_context()

    async def close_context(_ctx):
        calls["close"] += 1

    app = create_app(context_builder=build_context, context_closer=close_context)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "deployment": "agentbox-scoring-v3"}
    assert calls == {"build": 1, "close": 1}


def test_integration_health_checks_apollo_without_querying_exa() -> None:
    class FakeLuma:
        async def health_check(self):
            return {"configured": True, "verified": True, "status": "ok", "credit_cost": "none"}

    class FakeApollo:
        async def health_check(self):
            return {"configured": True, "verified": True, "status": "ok", "credit_cost": "none"}

    class FakeExa:
        def health_check(self):
            return {
                "configured": True,
                "verified": False,
                "status": "configured_unverified",
                "credit_cost": "not_checked",
            }

    ctx = make_context()
    ctx.luma_client = FakeLuma()
    ctx.apollo = FakeApollo()
    ctx.exa = FakeExa()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.get("/health/integrations")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["luma"]["verified"] is True
    assert response.json()["apollo"]["verified"] is True
    assert response.json()["exa"]["status"] == "configured_unverified"


def test_live_enrichment_integration_test_is_capped() -> None:
    class FakeApollo:
        async def health_check(self):
            return {"configured": True, "verified": True, "status": "ok", "credit_cost": "none"}

    class FakeExa:
        async def live_check(self):
            return {
                "configured": True,
                "verified": True,
                "status": "ok",
                "estimated_cost_usd": 0.007,
            }

    ctx = make_context()
    ctx.apollo = FakeApollo()
    ctx.exa = FakeExa()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post("/health/integrations/test-enrichment")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["estimated_cost_usd"] == 0.007


def test_app_allows_local_frontend_cors_for_agent_chat() -> None:
    async def build_context(_settings):
        return make_context()

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.options(
            "/agent/chat",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_auth_disabled_allows_app_routes_without_cookie() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.get("/events")

    assert response.status_code == 200


def test_exports_route_serves_existing_artifact(tmp_path) -> None:
    ctx = make_context()
    artifact_path = tmp_path / "mint-leads-test.csv"
    artifact_path.write_text("Tier,Email\nP0,founder@agentco.ai\n")
    ctx.lead_export_service.files["mint-leads-test.csv"] = artifact_path

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.get("/exports/mint-leads-test.csv")

    assert response.status_code == 200
    assert response.text == "Tier,Email\nP0,founder@agentco.ai\n"
    assert response.headers["content-disposition"].startswith('attachment; filename="mint-leads-test.csv"')


def test_exports_route_returns_404_for_missing_or_invalid_filename() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        missing = client.get("/exports/missing.csv")
        invalid = client.get("/exports/../settings.py")

    assert missing.status_code == 404
    assert invalid.status_code == 404


def test_events_default_syncs_luma_list_and_sorts_by_date() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.get("/events")

    assert response.status_code == 200
    events = response.json()
    assert ctx.luma_client.statuses == ["all"]
    assert [event.luma_event_id for event in ctx.event_storage.upserted_events] == ["evt-live", "evt-upcoming"]
    assert [event["name"] for event in events] == [
        "Upcoming Luma Event",
        "Live Luma Event",
        "CSV Partner List",
        "Tracked Luma Event",
    ]
    assert [event["source"] for event in events] == ["luma", "luma", "csv", "luma"]
    assert events[0]["status"] == "upcoming"
    assert events[1]["enrichment_enabled"] is False


def test_events_source_filter_csv_does_not_call_luma() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.get("/events", params={"source": "csv"})

    assert response.status_code == 200
    assert ctx.luma_client.statuses == []
    assert [event["source"] for event in response.json()] == ["csv"]


def test_events_can_skip_luma_refresh_for_fast_local_load() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.get("/events", params={"refresh_luma": "false"})

    assert response.status_code == 200
    assert ctx.luma_client.statuses == []
    assert ctx.event_storage.upserted_events == []
    assert [event["name"] for event in response.json()] == [
        "Stored Live Luma Event",
        "CSV Partner List",
        "Tracked Luma Event",
    ]


def test_events_source_filter_luma_returns_luma_events_only() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.get("/events", params={"source": "luma"})

    assert response.status_code == 200
    assert [event["source"] for event in response.json()] == ["luma", "luma", "luma"]


def test_events_luma_source_uses_short_lived_cache() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        first = client.get("/events", params={"source": "luma"})
        second = client.get("/events", params={"source": "luma"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert ctx.luma_client.statuses == ["all"]


def test_events_skips_luma_upserts_when_cache_is_warm() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        first = client.get("/events")
        upserts_after_first = len(ctx.event_storage.upserted_events)
        second = client.get("/events")

    assert first.status_code == 200
    assert second.status_code == 200
    assert ctx.luma_client.statuses == ["all"]
    assert len(ctx.event_storage.upserted_events) == upserts_after_first


def test_events_enrichment_toggle_updates_event_storage() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    event_id = "00000000-0000-0000-0000-000000000001"
    with TestClient(app) as client:
        disabled = client.patch(
            f"/events/{event_id}/enrichment",
            json={"enrichment_enabled": False},
        )
        missing = client.patch(
            "/events/00000000-0000-0000-0000-000000000000/enrichment",
            json={"enrichment_enabled": True},
        )

    assert disabled.status_code == 200
    assert disabled.json()["enrichment_enabled"] is False
    assert missing.status_code == 404
    assert [(str(event_id), enabled) for event_id, enabled in ctx.event_storage.enrichment_updates] == [
        (event_id, False),
        ("00000000-0000-0000-0000-000000000000", True),
    ]


def test_auth_login_sets_http_only_cookie_and_protects_app_routes() -> None:
    ctx = make_context()
    ctx.settings.mint_auth_enabled = True

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        blocked = client.get("/events")
        wrong = client.post("/auth/login", json={"password": "wrong"})
        login = client.post("/auth/login", json={"password": "test-password"})
        allowed = client.get("/events")
        me = client.get("/auth/me")

    assert blocked.status_code == 401
    assert wrong.status_code == 401
    assert login.status_code == 200
    assert login.json() == {"authenticated": True}
    assert "httponly" in login.headers["set-cookie"].lower()
    assert allowed.status_code == 200
    assert me.json() == {"authenticated": True}


def test_auth_exempts_health_and_admin_sync_token_check() -> None:
    ctx = make_context()
    ctx.settings.mint_auth_enabled = True

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        health = client.get("/health")
        data_quality = client.get("/health/data-quality")
        missing_token = client.post("/admin/sync-luma", json={"classify": True})
        valid_token = client.post(
            "/admin/sync-luma",
            json={"classify": True},
            headers={"X-Mint-Sync-Token": "sync-token-test"},
        )

    assert health.status_code == 200
    assert data_quality.status_code == 401
    assert missing_token.status_code == 401
    assert valid_token.status_code == 202
    assert valid_token.json()["job_id"]
    assert [name for name, _ in ctx.job_manager.started] == ["sync_luma"]


def test_events_pull_delegates_to_orchestrator() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post("/events/pull", json={"luma_event_id": "evt-1", "enrich": False})

    assert response.status_code == 200
    assert response.json()["enriched_count"] == 1
    assert ctx.orchestrator.pulls == [("evt-1", False)]
    assert ctx.classification_service.scopes == []


def test_events_pull_can_run_classification_for_the_event() -> None:
    event_id = uuid4()
    ctx = make_context(event_id=event_id)

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/events/pull",
            json={"luma_event_id": "evt-1", "enrich": True, "classify": True},
        )

    assert response.status_code == 200
    assert response.json()["classification"] == {
        "total_count": 2,
        "classified_count": 2,
        "skipped_count": 0,
        "errors": [],
    }
    assert ctx.classification_service.scopes == [
        ClassificationScope(event_id=event_id, force_reclassify=False)
    ]


def test_events_upload_parses_csv_and_delegates_to_orchestrator() -> None:
    event_id = uuid4()
    ctx = make_context(event_id=event_id)

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/events/upload",
            data={
                "event_name": "Partner Dinner",
                "event_date": "2026-05-27T18:00:00-07:00",
                "event_location": "San Francisco",
                "event_url": "https://example.com/partner-dinner",
            },
            files={
                "file": (
                    "leads.csv",
                    b"Email,First Name,Company\nalex@example.com,Alex,Quadex AI\n",
                    "text/csv",
                )
            },
        )

    assert response.status_code == 200
    assert response.json()["event_id"] == str(event_id)
    rows, event, column_mapping = ctx.orchestrator.csv_uploads[0]
    assert rows == [{"Email": "alex@example.com", "First Name": "Alex", "Company": "Quadex AI"}]
    assert event.name == "Partner Dinner"
    assert event.event_date == datetime(2026, 5, 28, 1, 0, tzinfo=UTC)
    assert event.location == "San Francisco"
    assert event.url == "https://example.com/partner-dinner"
    assert event.status == "past"
    assert event.guest_count == 1
    assert column_mapping is None


def test_events_upload_can_ingest_without_event_fields() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/events/upload",
            files={
                "file": (
                    "leads.csv",
                    b"Email,First Name\nalex@example.com,Alex\n",
                    "text/csv",
                )
            },
        )

    assert response.status_code == 200
    assert response.json()["event_id"] is None
    assert ctx.orchestrator.csv_uploads[0][1] is None


def test_events_upload_can_limit_paid_enrichment_to_organization_emails() -> None:
    event_id = uuid4()
    ctx = make_context(event_id=event_id)

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/events/upload",
            data={"event_name": "Work-email pass", "work_emails_only": "true"},
            files={
                "file": (
                    "leads.csv",
                    b"email,first_name\nfounder@gmail.com,Gina\nbuyer@agentco.ai,Ada\nresearcher@mit.edu,Max\n",
                    "text/csv",
                )
            },
        )

    assert response.status_code == 200
    rows, event, _ = ctx.orchestrator.csv_uploads[0]
    assert [row["email"] for row in rows] == ["buyer@agentco.ai", "researcher@mit.edu"]
    assert event.guest_count == 2


def test_events_upload_returns_clear_error_when_no_usable_leads() -> None:
    ctx = make_context()

    async def ingest_csv(_rows, event, column_mapping=None, on_progress=None):
        raise ValueError("No usable leads found. Make sure the file has an email column and going/approved guests.")

    ctx.orchestrator.ingest_csv = ingest_csv

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/events/upload",
            data={"event_name": "Partner Dinner"},
            files={
                "file": (
                    "leads.csv",
                    b"Name\nNo Email\n",
                    "text/csv",
                )
            },
        )

    assert response.status_code == 400
    assert "No usable leads found" in response.json()["detail"]


def test_events_upload_classify_true_runs_classification_for_created_event() -> None:
    event_id = uuid4()
    ctx = make_context(event_id=event_id)

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/events/upload",
            data={"event_name": "Partner Dinner", "classify": "true"},
            files={
                "file": (
                    "leads.csv",
                    b"Email,First Name\nalex@example.com,Alex\n",
                    "text/csv",
                )
            },
        )

    assert response.status_code == 200
    assert response.json()["classification"]["classified_count"] == 2
    assert ctx.classification_service.scopes == [
        ClassificationScope(event_id=event_id, force_reclassify=True)
    ]


def test_events_upload_background_starts_job_without_blocking() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/events/upload",
            data={"event_name": "Partner Dinner", "classify": "true", "background": "true"},
            files={
                "file": (
                    "leads.csv",
                    b"Email,First Name\nalex@example.com,Alex\n",
                    "text/csv",
                )
            },
        )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["job_id"]
    assert ctx.job_manager.started[0][0] == "csv_upload"


def test_job_status_endpoint_returns_job_record() -> None:
    ctx = make_context()
    job = ctx.job_manager.start("csv_upload", lambda: None)

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.get(f"/jobs/{job.id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(job.id)
    assert response.json()["status"] == "queued"


class FakeChatStorage:
    def __init__(self) -> None:
        self.threads: dict[str, dict] = {}
        self.messages: dict[str, list[dict]] = {}

    async def list_threads(self):
        return list(self.threads.values())

    async def get_thread(self, thread_id):
        thread = self.threads.get(str(thread_id))
        if thread is None:
            return None
        return {**thread, "messages": self.messages.get(str(thread_id), [])}

    async def upsert_thread(self, thread_id, title):
        thread = {"id": str(thread_id), "title": title, "created_at": None, "updated_at": None}
        self.threads[str(thread_id)] = thread
        return thread

    async def append_messages(self, thread_id, messages):
        if str(thread_id) not in self.threads:
            return False
        self.messages.setdefault(str(thread_id), []).extend(
            {**message, "id": str(message["id"])} for message in messages
        )
        return True

    async def delete_thread(self, thread_id):
        return self.threads.pop(str(thread_id), None) is not None


def test_chat_thread_routes_round_trip() -> None:
    ctx = make_context()
    ctx.chat_storage = FakeChatStorage()
    thread_id = str(uuid4())
    message_id = str(uuid4())

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        created = client.put(f"/chat/threads/{thread_id}", json={"title": "Show me the P0s"})
        assert created.status_code == 200
        assert created.json()["title"] == "Show me the P0s"

        appended = client.post(
            f"/chat/threads/{thread_id}/messages",
            json={"messages": [{"id": message_id, "role": "user", "content": "Show me the P0s"}]},
        )
        assert appended.status_code == 200
        assert appended.json() == {"appended": 1}

        listed = client.get("/chat/threads")
        assert [item["id"] for item in listed.json()] == [thread_id]

        fetched = client.get(f"/chat/threads/{thread_id}")
        assert fetched.status_code == 200
        assert fetched.json()["messages"][0]["content"] == "Show me the P0s"

        deleted = client.delete(f"/chat/threads/{thread_id}")
        assert deleted.status_code == 200
        assert client.get(f"/chat/threads/{thread_id}").status_code == 404


def test_chat_message_append_404s_for_unknown_thread() -> None:
    ctx = make_context()
    ctx.chat_storage = FakeChatStorage()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            f"/chat/threads/{uuid4()}/messages",
            json={"messages": [{"id": str(uuid4()), "role": "user", "content": "hello"}]},
        )

    assert response.status_code == 404


def test_job_status_endpoint_falls_back_to_job_store() -> None:
    ctx = make_context()
    job_id = uuid4()

    class FakeJobStore:
        async def get(self, requested_id):
            assert requested_id == job_id
            return SimpleNamespace(
                model_dump=lambda mode="json": {
                    "id": str(job_id),
                    "name": "csv_upload",
                    "status": "failed",
                    "error": "The backend restarted while this job was running. Start it again.",
                }
            )

    ctx.job_store = FakeJobStore()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"


def test_event_backfill_starts_background_job() -> None:
    event_id = uuid4()
    ctx = make_context(event_id=event_id)

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(f"/events/{event_id}/backfill", json={"classify": True})

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert ctx.job_manager.started[0][0] == "event_backfill"


def test_events_upload_rejects_bad_file_type_and_too_many_rows() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        bad_type = client.post(
            "/events/upload",
            files={"file": ("leads.pdf", b"%PDF", "application/pdf")},
        )
        too_many_rows = client.post(
            "/events/upload",
            files={
                "file": (
                    "leads.csv",
                    _csv_bytes_with_rows(MAX_UPLOAD_ROWS + 1),
                    "text/csv",
                )
            },
        )

    assert bad_type.status_code == 400
    assert "Unsupported upload format" in bad_type.json()["detail"]
    assert too_many_rows.status_code == 400
    assert "too many rows" in too_many_rows.json()["detail"]


def test_events_upload_parses_xlsx() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/events/upload",
            files={
                "file": (
                    "leads.xlsx",
                    _xlsx_bytes([["Email", "First Name"], ["alex@example.com", "Alex"]]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )

    assert response.status_code == 200
    rows, event, _column_mapping = ctx.orchestrator.csv_uploads[0]
    assert rows == [{"Email": "alex@example.com", "First Name": "Alex"}]
    assert event is None


def test_events_upload_counts_rows_after_work_email_filter() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    # Over the raw row limit, but the personal-domain rows are dropped first.
    lines = ["Email,First Name"]
    lines.extend(f"lead{index}@example.com,Lead {index}" for index in range(MAX_UPLOAD_ROWS))
    lines.extend(f"personal{index}@gmail.com,Personal {index}" for index in range(500))
    payload = ("\n".join(lines) + "\n").encode()

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        filtered = client.post(
            "/events/upload",
            files={"file": ("leads.csv", payload, "text/csv")},
            data={"work_emails_only": "true"},
        )
        unfiltered = client.post(
            "/events/upload",
            files={"file": ("leads.csv", payload, "text/csv")},
            data={"work_emails_only": "false"},
        )

    assert filtered.status_code == 200
    rows, _event, _column_mapping = ctx.orchestrator.csv_uploads[0]
    assert len(rows) == MAX_UPLOAD_ROWS
    assert unfiltered.status_code == 400
    assert "too many rows" in unfiltered.json()["detail"]


def test_events_checkins_and_list_delegate_to_context() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        checkins = client.post("/events/checkins", json={"luma_event_id": "evt-1"})
        events = client.get("/events", params={"status": "past"})

    assert checkins.json() == {"checked_in": 1, "no_show": 1}
    assert [event["status"] for event in events.json()] == ["past", "past", "past"]
    assert [event["source"] for event in events.json()] == ["luma", "csv", "luma"]
    assert ctx.orchestrator.checkins == ["evt-1"]
    assert ctx.event_storage.statuses == ["all"]
    assert len(ctx.event_storage.upserted_event_batches) == 1
    assert [event.luma_event_id for event in ctx.event_storage.upserted_events] == [
        "evt-live",
        "evt-upcoming",
    ]


def test_leads_routes_delegate_to_read_storage() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    person_id = ctx.read_storage.person_id
    with TestClient(app) as client:
        listed = client.get(
            "/leads",
            params={"tier": "P0", "primary_product": "maas", "search": "agentco", "limit": 25},
        )
        detail = client.get(f"/leads/{person_id}")
        missing = client.get(f"/leads/{uuid4()}")

    assert listed.status_code == 200
    assert listed.json()["items"][0]["email"] == "founder@agentco.ai"
    filters, sort, limit, offset = ctx.read_storage.lead_filters[0]
    assert filters.tier == "P0"
    assert filters.primary_product == "maas"
    assert filters.search == "agentco"
    assert sort == "tier"
    assert limit == 25
    assert offset == 0
    assert detail.status_code == 200
    assert detail.json()["company"]["domain"] == "agentco.ai"
    assert missing.status_code == 404


def test_review_routes_delegate_actions_and_classification() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    person_id = ctx.read_storage.person_id
    with TestClient(app) as client:
        flagged = client.get("/review/flagged")
        resolved = client.post(f"/review/flagged/{person_id}/resolve", json={"chosen_domain": "agentco.ai"})
        needs_review = client.get("/review/needs-review", params={"type": "classification"})
        reclassified = client.post(
            f"/review/{person_id}/reclassify",
            json={"new_tier": "P1", "reason": "Manual review", "reviewed_by": "pynay"},
        )

    assert flagged.status_code == 200
    assert flagged.json()["items"][0]["candidate_companies"][0]["domain"] == "agentco.ai"
    assert resolved.status_code == 200
    assert resolved.json()["updated_lead"]["current_classification"]["tier"] == "P0"
    assert resolved.json()["job_id"]
    assert ctx.read_storage.flagged_resolutions[0][1]["chosen_domain"] == "agentco.ai"

    # Enrichment and classification run in a background job, not in the request.
    assert ctx.enrichment_service.resolved_calls == []
    assert ctx.classification_service.scopes == []
    job_name, runner = ctx.job_manager.started[0]
    assert job_name == "resolve_followup"

    followup = run_async(runner(None))
    assert followup["enrichment"] == {"enriched": True}
    assert ctx.enrichment_service.resolved_calls == [("input", "resolution", "person")]
    assert ctx.classification_service.scopes == [
        ClassificationScope(person_ids=[person_id], force_reclassify=True)
    ]
    assert needs_review.status_code == 200
    assert needs_review.json()["items"][0]["source"] == "classification"
    assert reclassified.status_code == 200
    assert reclassified.json()["current_classification"]["tier"] == "P1"
    assert ctx.read_storage.reclassifications == [(person_id, "P1", "Manual review", "pynay")]


def test_rollup_routes_delegate_to_read_storage() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    event_id = uuid4()
    with TestClient(app) as client:
        event_summary = client.get(f"/events/{event_id}/summary")
        pipeline_summary = client.get("/pipeline/summary")
        data_quality = client.get("/health/data-quality")

    assert event_summary.status_code == 200
    assert event_summary.json()["counts"]["classified"] == 1
    assert pipeline_summary.status_code == 200
    assert pipeline_summary.json()["p0_p1_by_primary_product"] == {"maas": 1}
    assert data_quality.status_code == 200
    assert data_quality.json()["resolution"]["total_people"] == 1


def test_agent_chat_route_runs_agent_loop() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/agent/chat",
            json={"messages": [{"role": "user", "content": "Show P0 leads"}]},
        )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "Found one lead.",
        "tool_trace": [{"name": "list_leads", "input": {"tier": "P0"}}],
    }
    assert ctx.agent_loop.messages == [[{"role": "user", "content": "Show P0 leads"}]]


def test_agent_chat_stream_route_emits_sse_events() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/agent/chat/stream",
            json={"messages": [{"role": "user", "content": "Show P0 leads"}]},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[len("data: "):])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert [event["type"] for event in events] == ["thinking", "tool", "tool", "result"]
    assert events[-1]["reply"] == "Found one lead."
    assert ctx.agent_loop.messages == [[{"role": "user", "content": "Show P0 leads"}]]


def test_agent_chat_stream_route_emits_heartbeat_while_waiting(monkeypatch) -> None:
    from mint.backend.routes import agent as agent_routes

    monkeypatch.setattr(agent_routes, "STREAM_HEARTBEAT_SECONDS", 0.001)
    ctx = make_context()
    ctx.agent_loop = SlowAgentLoop()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/agent/chat/stream",
            json={"messages": [{"role": "user", "content": "Show P0 leads"}]},
        )

    events = [
        json.loads(line[len("data: "):])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1]["type"] == "result"
    assert any(event["type"] == "heartbeat" for event in events[:-1])


def test_admin_sync_luma_requires_token_and_starts_sync_job() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        missing = client.post("/admin/sync-luma", json={"classify": True})
        wrong = client.post(
            "/admin/sync-luma",
            json={"classify": True},
            headers={"X-Mint-Sync-Token": "wrong"},
        )
        correct = client.post(
            "/admin/sync-luma",
            json={"classify": False},
            headers={"X-Mint-Sync-Token": "sync-token-test"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert correct.status_code == 202
    assert correct.json()["job_id"]
    assert correct.json()["status"] == "queued"
    assert [name for name, _ in ctx.job_manager.started] == ["sync_luma"]

    _, runner = ctx.job_manager.started[0]
    run_async(runner(None))
    assert ctx.luma_sync_service.calls == [False]


def test_admin_reset_classifications_requires_token_and_starts_reclassify_job() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        missing = client.post("/admin/reset-classifications", json={})
        wrong = client.post(
            "/admin/reset-classifications",
            json={},
            headers={"X-Mint-Sync-Token": "wrong"},
        )
        correct = client.post(
            "/admin/reset-classifications",
            json={},
            headers={"X-Mint-Sync-Token": "sync-token-test"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert correct.status_code == 200
    body = correct.json()
    assert body["classifications_deleted"] == 3
    assert body["people_dismissed"] == 2
    assert body["job_id"]
    assert ctx.classification_storage.calls == [True]
    assert [name for name, _ in ctx.job_manager.started] == ["reclassify_all"]

    _, runner = ctx.job_manager.started[0]
    run_async(runner(None))
    scope = ctx.classification_service.scopes[-1]
    assert scope.event_id is None
    assert scope.person_ids is None
    assert scope.force_reclassify is False
    assert ctx.auto_review_service.sweeps == 1


def test_admin_auto_review_requires_token_and_runs_sweep() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        missing = client.post("/admin/auto-review", json={})
        correct = client.post(
            "/admin/auto-review",
            json={},
            headers={"X-Mint-Sync-Token": "sync-token-test"},
        )

    assert missing.status_code == 401
    assert correct.status_code == 200
    assert correct.json()["reviews_confirmed"] == 2
    assert ctx.auto_review_service.sweeps == 1


def test_events_upload_with_classify_runs_auto_review_sweep() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post(
            "/events/upload",
            data={"event_name": "Partner Dinner", "classify": "true"},
            files={
                "file": (
                    "leads.csv",
                    b"Email,First Name,Company\nalex@example.com,Alex,Quadex AI\n",
                    "text/csv",
                )
            },
        )

    assert response.status_code == 200
    assert "classification" in response.json()
    assert ctx.auto_review_service.sweeps == 1


def test_luma_webhook_route_is_removed_in_favor_of_scheduled_sync() -> None:
    ctx = make_context()

    async def build_context(_settings):
        return ctx

    app = create_app(context_builder=build_context)
    with TestClient(app) as client:
        response = client.post("/webhooks/luma", json={"type": "guest.updated"})
        sync = client.post(
            "/admin/sync-luma",
            json={"classify": True},
            headers={"X-Mint-Sync-Token": "sync-token-test"},
        )

    assert response.status_code == 404
    assert sync.status_code == 202
    assert [name for name, _ in ctx.job_manager.started] == ["sync_luma"]


def _csv_bytes_with_rows(count: int) -> bytes:
    lines = ["Email,First Name"]
    lines.extend(f"lead{index}@example.com,Lead {index}" for index in range(count))
    return ("\n".join(lines) + "\n").encode()


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
