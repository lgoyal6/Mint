import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from mint.backend.agent.tools import MintAgentTools
from mint.backend.classification.service import ClassificationScope
from mint.backend.db.read_storage import LeadPage, LeadSummary
from mint.backend.exports.leads import LeadExportArtifact, LeadExportData, LeadExportRow, LeadExportSummaryRow
from mint.backend.models import ClassifyResult, IngestSummary
from mint.backend.jobs import JobManager


def run(coro):
    return asyncio.run(coro)


class FakeReadStorage:
    def __init__(self) -> None:
        self.person_id = uuid4()
        self.event_id = uuid4()
        self.list_calls = []
        self.export_calls = []
        self.detail_calls = []
        self.resolutions = []
        self.reclassifications = []

    async def list_leads(self, filters, *, sort: str = "tier", limit: int = 50, offset: int = 0):
        self.list_calls.append((filters, sort, limit, offset))
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

    async def export_leads(self, filters, *, sort: str = "tier", profile: str = "standard"):
        self.export_calls.append((filters, sort, profile))
        return LeadExportData(
            rows=[
                LeadExportRow(
                    tier="P0",
                    primary_product="maas",
                    confidence=5,
                    review_status="CLASSIFIED",
                    first_name="Alex",
                    last_name="Founder",
                    name="Alex Founder",
                    email="founder@agentco.ai",
                    title="Founder",
                    linkedin_url=None,
                    company="AgentCo",
                    domain="agentco.ai",
                    target_vertical="agentic",
                    what_they_are_building=None,
                    key_signal="Founder + inference",
                    key_risk="None",
                    reasoning="Strong fit.",
                    product_fit=["maas"],
                    np_trigger=None,
                    event_names=["Hard Problems"],
                    checked_in=True,
                    apollo_match="matched",
                    resolution_confidence="high",
                    data_quality="rich",
                )
            ],
            summary=[
                LeadExportSummaryRow(event="Hard Problems", classified_rows=1, p0=1, p1=0, p2=0, nr=0, np=0)
            ],
            profile=profile,
        )

    async def get_lead_detail(self, person_id):
        self.detail_calls.append(person_id)
        if person_id != self.person_id:
            return None
        return SimpleNamespace(model_dump=lambda mode="json": {"person": {"id": str(person_id)}})

    async def event_summary(self, event_id):
        return {"event": {"id": str(event_id)}, "counts": {"classified": 2}}

    async def event_analytics(self, event_id):
        return {"event": {"id": str(event_id)}, "radar": [], "overall_score": None}

    async def event_scorecards(self):
        return [{"event_id": "evt-1", "overall_score": 50}]

    async def pipeline_summary(self):
        return {"p0_p1_by_primary_product": {"maas": 2}}

    async def data_quality_summary(self):
        return {"resolution": {"total_people": 10}}

    async def list_flagged(self, *, limit: int = 50, offset: int = 0):
        return SimpleNamespace(model_dump=lambda mode="json": {"total": 1, "items": [{"email": "a@b.com"}]})

    async def list_needs_review(self, *, queue_type: str = "all", limit: int = 50, offset: int = 0):
        return SimpleNamespace(model_dump=lambda mode="json": {"total": 1, "items": [{"source": queue_type}]})

    async def resolve_flagged_person(self, person_id, **kwargs):
        self.resolutions.append((person_id, kwargs))
        return SimpleNamespace(model_dump=lambda mode="json": {"person_id": str(person_id), "domain": "agentco.ai"})

    async def enrichment_context_for_person(self, person_id):
        return SimpleNamespace(input="input", resolution="resolution", person="person")

    async def reclassify_person(self, person_id, *, new_tier: str, reason: str, reviewed_by: str | None = None):
        self.reclassifications.append((person_id, new_tier, reason, reviewed_by))
        return SimpleNamespace(model_dump=lambda mode="json": {"person": {"id": str(person_id)}, "tier": new_tier})


class FakeEventStorage:
    async def list_events(self, status: str = "all"):
        return [{"id": "evt-1", "status": status}]

    async def get_event(self, event_id):
        return {
            "id": str(event_id),
            "luma_event_id": "evt-hard-problems",
            "name": "Hard Problems",
        }


class FakeOrchestrator:
    def __init__(self, event_id) -> None:
        self.event_id = event_id
        self.pulls = []
        self.backfills = []

    async def ingest_luma_event(self, luma_event_id: str, enrich: bool = True, on_progress=None):
        self.pulls.append((luma_event_id, enrich))
        return IngestSummary(event_id=self.event_id, total_guests=3, going_count=2, enriched_count=2)

    async def enrich_event_registrations(self, event_id, on_progress=None):
        self.backfills.append(event_id)
        if on_progress is not None:
            await on_progress(0, 3)
            await on_progress(3, 3)
        return IngestSummary(event_id=event_id, total_guests=3, going_count=2, enriched_count=3)


class FakeClassificationService:
    def __init__(self) -> None:
        self.scopes = []

    async def classify_batch(self, scope: ClassificationScope, on_progress=None):
        self.scopes.append(scope)
        if on_progress is not None:
            await on_progress(0, 2)
            await on_progress(2, 2)
        return ClassifyResult(total_count=2, classified_count=2, skipped_count=0)


class FakeEnrichmentService:
    def __init__(self) -> None:
        self.calls = []

    async def enrich_resolved_lead(self, input, resolution, person):
        self.calls.append((input, resolution, person))
        return SimpleNamespace(model_dump=lambda mode="json": {"enriched": True})


class FakeLeadExportService:
    def __init__(self) -> None:
        self.calls = []

    def create_artifact(self, export, *, fmt: str = "xlsx"):
        self.calls.append((export, fmt))
        return LeadExportArtifact(
            filename=f"mint-leads-test.{fmt}",
            download_url=f"/exports/mint-leads-test.{fmt}",
            row_count=len(export.rows),
            format=fmt,
        )


class FakeHubSpotClient:
    def __init__(self) -> None:
        self.calls = []

    async def import_contacts(self, export, *, list_name: str, marketable: bool = False, create_list: bool = True):
        self.calls.append((export, list_name, marketable, create_list))
        return SimpleNamespace(
            model_dump=lambda mode="json": {
                "import_id": "123",
                "state": "STARTED",
                "row_count": len(export.rows),
                "list_name": list_name,
                "response": {"id": "123"},
            }
        )


def fake_context():
    read_storage = FakeReadStorage()
    return SimpleNamespace(
        read_storage=read_storage,
        event_storage=FakeEventStorage(),
        orchestrator=FakeOrchestrator(read_storage.event_id),
        classification_service=FakeClassificationService(),
        enrichment_service=FakeEnrichmentService(),
        lead_export_service=FakeLeadExportService(),
        hubspot_client=FakeHubSpotClient(),
        job_manager=JobManager(),
        job_store=None,
    )


def test_list_leads_maps_filters_and_caps_rows() -> None:
    ctx = fake_context()
    tools = MintAgentTools(ctx)

    result = run(
        tools.dispatch(
            "list_leads",
            {"tier": "P0", "primary_product": "maas", "search": "agentco", "limit": 400},
        )
    )

    filters, sort, limit, offset = ctx.read_storage.list_calls[0]
    assert filters.tier == "P0"
    assert filters.primary_product == "maas"
    assert filters.search == "agentco"
    assert sort == "tier"
    assert limit == 25
    assert offset == 0
    assert result["total"] == 1
    assert result["items"][0]["email"] == "founder@agentco.ai"


def test_export_leads_maps_filters_and_returns_artifact_metadata() -> None:
    ctx = fake_context()
    tools = MintAgentTools(ctx)

    result = run(
        tools.dispatch(
            "export_leads",
            {
                "format": "csv",
                "profile": "icp",
                "tiers": ["P0", "P1"],
                "primary_product": "maas",
                "search": "agentco",
                "sort": "confidence",
            },
        )
    )

    filters, sort, profile = ctx.read_storage.export_calls[0]
    assert filters.tiers == ["P0", "P1"]
    assert filters.primary_product == "maas"
    assert filters.search == "agentco"
    assert sort == "confidence"
    assert profile == "icp"
    assert ctx.lead_export_service.calls[0][1] == "csv"
    assert result == {
        "filename": "mint-leads-test.csv",
        "download_url": "/exports/mint-leads-test.csv",
        "row_count": 1,
        "format": "csv",
    }


def test_export_leads_rejects_an_empty_result_instead_of_creating_a_dead_download() -> None:
    ctx = fake_context()

    async def empty_export(*args, **kwargs):
        return LeadExportData(rows=[], summary=[])

    ctx.read_storage.export_leads = empty_export
    with pytest.raises(ValueError, match="No classified leads matched"):
        run(MintAgentTools(ctx).dispatch("export_leads", {"format": "xlsx"}))


def test_get_lead_can_resolve_by_email_then_fetch_profile() -> None:
    ctx = fake_context()
    tools = MintAgentTools(ctx)

    result = run(tools.dispatch("get_lead", {"email": "founder@agentco.ai"}))

    assert ctx.read_storage.list_calls[0][0].search == "founder@agentco.ai"
    assert ctx.read_storage.detail_calls == [ctx.read_storage.person_id]
    assert result["person"]["id"] == str(ctx.read_storage.person_id)


def test_event_analytics_routes_detail_with_id_and_scorecards_without() -> None:
    ctx = fake_context()
    tools = MintAgentTools(ctx)

    detail = run(tools.dispatch("event_analytics", {"event_id": str(ctx.read_storage.event_id)}))
    assert detail["event"]["id"] == str(ctx.read_storage.event_id)

    overview = run(tools.dispatch("event_analytics", {}))
    assert overview == [{"event_id": "evt-1", "overall_score": 50}]

    metadata = {tool.name: tool.side_effecting for tool in tools.tools}
    assert metadata["event_analytics"] is False


def test_side_effecting_tools_are_marked_and_dispatch_existing_services() -> None:
    ctx = fake_context()
    tools = MintAgentTools(ctx)
    metadata = {tool.name: tool.side_effecting for tool in tools.tools}

    assert metadata["pull_event"] is True
    assert metadata["enrich_event"] is True
    assert metadata["job_status"] is False
    assert metadata["classify_event"] is True
    assert metadata["resolve_flagged"] is True
    assert metadata["reclassify_lead"] is True
    assert metadata["list_leads"] is False
    assert metadata["export_leads"] is False

    resolved = run(
        tools.dispatch(
            "resolve_flagged",
            {"person_id": str(ctx.read_storage.person_id), "chosen_domain": "agentco.ai"},
        )
    )
    classified = run(tools.dispatch("classify_event", {"event_id": str(ctx.read_storage.event_id)}))
    reclassified = run(
        tools.dispatch(
            "reclassify_lead",
            {"person_id": str(ctx.read_storage.person_id), "new_tier": "P1", "reason": "Reviewed"},
        )
    )

    assert resolved["resolution"]["domain"] == "agentco.ai"
    assert resolved["enrichment"] == {"enriched": True}
    assert ctx.classification_service.scopes[0] == ClassificationScope(
        person_ids=[ctx.read_storage.person_id],
        force_reclassify=True,
    )
    assert classified["classified_count"] == 2
    assert ctx.classification_service.scopes[1] == ClassificationScope(
        event_id=ctx.read_storage.event_id,
        force_reclassify=True,
    )
    assert reclassified["tier"] == "P1"
    assert ctx.read_storage.reclassifications == [
        (ctx.read_storage.person_id, "P1", "Reviewed", "agent")
    ]


def test_enrich_event_starts_csv_backfill_job_and_exposes_status() -> None:
    async def scenario():
        ctx = fake_context()

        async def csv_event(event_id):
            return {"id": str(event_id), "luma_event_id": None, "name": "CSV Event"}

        ctx.event_storage.get_event = csv_event
        tools = MintAgentTools(ctx)
        started = await tools.dispatch(
            "enrich_event",
            {"event_id": str(ctx.read_storage.event_id), "classify": False},
        )
        while True:
            status = await tools.dispatch("job_status", {"job_id": started["job_id"]})
            if status["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(0)

        assert status["status"] == "succeeded"
        assert status["result"]["enriched_count"] == 3
        assert ctx.orchestrator.backfills == [ctx.read_storage.event_id]

    run(scenario())


def test_sync_event_to_hubspot_exports_p0_p1_and_creates_list() -> None:
    ctx = fake_context()
    tools = MintAgentTools(ctx)

    result = run(tools.dispatch("sync_event_to_hubspot", {"event_id": str(ctx.read_storage.event_id)}))

    filters, sort, profile = ctx.read_storage.export_calls[0]
    assert filters.event_id == ctx.read_storage.event_id
    assert filters.tiers == ["P0", "P1"]
    assert sort == "tier"
    assert profile == "icp"
    export, list_name, marketable, create_list = ctx.hubspot_client.calls[0]
    assert len(export.rows) == 1
    assert list_name == "Mint Luma Event - Hard Problems - P0/P1"
    assert marketable is False
    assert create_list is True
    assert result["import_id"] == "123"
    assert result["row_count"] == 1
