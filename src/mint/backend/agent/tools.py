from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from mint.backend.classification.service import ClassificationScope
from mint.backend.db.read_storage import LeadFilters
from mint.backend.ingestion.backfill import run_event_backfill


MAX_TOOL_ROWS = 25
TIERS = ["P0", "P1", "P2", "NR", "NP"]


class AgentTool(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str
    input_schema: dict[str, Any]
    side_effecting: bool = False


class MintAgentTools:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.tools = _tool_definitions()
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "list_leads": self._list_leads,
            "export_leads": self._export_leads,
            "get_lead": self._get_lead,
            "list_events": self._list_events,
            "event_summary": self._event_summary,
            "event_analytics": self._event_analytics,
            "pipeline_summary": self._pipeline_summary,
            "data_quality_summary": self._data_quality_summary,
            "list_flagged": self._list_flagged,
            "list_needs_review": self._list_needs_review,
            "resolve_flagged": self._resolve_flagged,
            "reclassify_lead": self._reclassify_lead,
            "pull_event": self._pull_event,
            "enrich_event": self._enrich_event,
            "job_status": self._job_status,
            "classify_event": self._classify_event,
            "sync_event_to_hubspot": self._sync_event_to_hubspot,
            "send_identity_review_to_clay": self._send_identity_review_to_clay,
            "search_chat_history": self._search_chat_history,
        }

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            tool.model_dump(exclude={"side_effecting"}, mode="json")
            for tool in self.tools
        ]

    async def dispatch(self, name: str, tool_input: dict[str, Any]) -> Any:
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(f"unknown tool: {name}")
        return await handler(tool_input or {})

    async def _list_leads(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        filters = _lead_filters(tool_input)
        page = await self.ctx.read_storage.list_leads(
            filters,
            sort=str(tool_input.get("sort") or "tier"),
            limit=_limit(tool_input.get("limit")),
            offset=max(int(tool_input.get("offset") or 0), 0),
        )
        return page.model_dump(mode="json")

    async def _export_leads(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        export = await self.ctx.read_storage.export_leads(
            _lead_filters(tool_input),
            sort=str(tool_input.get("sort") or "tier"),
            profile=str(tool_input.get("profile") or "standard"),
        )
        if not export.rows:
            raise ValueError(
                "No classified leads matched that export. Verify the event ID and filters before exporting again."
            )
        artifact = self.ctx.lead_export_service.create_artifact(
            export,
            fmt=str(tool_input.get("format") or "xlsx"),
        )
        return artifact.model_dump(mode="json")

    async def _get_lead(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        person_id = tool_input.get("person_id")
        if person_id:
            detail = await self.ctx.read_storage.get_lead_detail(UUID(str(person_id)))
            return detail.model_dump(mode="json") if detail else {"found": False}

        email = str(tool_input.get("email") or "").strip().lower()
        if not email:
            raise ValueError("get_lead requires person_id or email")
        detail_by_email = getattr(self.ctx.read_storage, "get_lead_detail_by_email", None)
        if detail_by_email is not None:
            detail = await detail_by_email(email)
            return detail.model_dump(mode="json") if detail else {"found": False}
        page = await self.ctx.read_storage.list_leads(
            LeadFilters(search=email),
            sort="tier",
            limit=5,
            offset=0,
        )
        matches = [item for item in page.items if item.email.lower() == email]
        if not matches and len(page.items) == 1:
            matches = page.items
        if not matches:
            return {"found": False}
        detail = await self.ctx.read_storage.get_lead_detail(matches[0].person_id)
        return detail.model_dump(mode="json") if detail else {"found": False}

    async def _list_events(self, tool_input: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.ctx.event_storage.list_events(status=str(tool_input.get("status") or "all"))

    async def _search_chat_history(self, tool_input: dict[str, Any]) -> list[dict[str, Any]]:
        query = str(tool_input.get("query") or "").strip()
        if not query:
            raise ValueError("search_chat_history requires a query")
        return await self.ctx.chat_storage.search_threads(query, limit=_limit(tool_input.get("limit")))

    async def _event_summary(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.read_storage.event_summary(UUID(str(tool_input["event_id"])))

    async def _event_analytics(self, tool_input: dict[str, Any]) -> Any:
        event_id = tool_input.get("event_id")
        if event_id:
            return await self.ctx.read_storage.event_analytics(UUID(str(event_id)))
        return await self.ctx.read_storage.event_scorecards()

    async def _pipeline_summary(self, _tool_input: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.read_storage.pipeline_summary()

    async def _data_quality_summary(self, _tool_input: dict[str, Any]) -> dict[str, Any]:
        return await self.ctx.read_storage.data_quality_summary()

    async def _list_flagged(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        page = await self.ctx.read_storage.list_flagged(
            limit=_limit(tool_input.get("limit")),
            offset=max(int(tool_input.get("offset") or 0), 0),
        )
        return page.model_dump(mode="json")

    async def _list_needs_review(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        page = await self.ctx.read_storage.list_needs_review(
            queue_type=str(tool_input.get("type") or "all"),
            limit=_limit(tool_input.get("limit")),
            offset=max(int(tool_input.get("offset") or 0), 0),
        )
        return page.model_dump(mode="json")

    async def _resolve_flagged(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        person_id = UUID(str(tool_input["person_id"]))
        resolution = await self.ctx.read_storage.resolve_flagged_person(
            person_id,
            chosen_domain=tool_input.get("chosen_domain"),
            company_domain=tool_input.get("company_domain"),
            company_name=tool_input.get("company_name"),
            chosen_company_id=(
                UUID(str(tool_input["chosen_company_id"]))
                if tool_input.get("chosen_company_id")
                else None
            ),
        )

        enrichment = None
        enrichment_context = await self.ctx.read_storage.enrichment_context_for_person(person_id)
        enrichment_service = getattr(self.ctx, "enrichment_service", None)
        if enrichment_context is not None and enrichment_service is not None:
            enrichment = await enrichment_service.enrich_resolved_lead(
                enrichment_context.input,
                enrichment_context.resolution,
                enrichment_context.person,
            )

        classification = await self.ctx.classification_service.classify_batch(
            ClassificationScope(person_ids=[person_id], force_reclassify=True)
        )
        updated = await self.ctx.read_storage.get_lead_detail(person_id)
        return {
            "resolution": _dump(resolution),
            "enrichment": _dump(enrichment) if enrichment else None,
            "classification": _dump(classification),
            "updated_lead": _dump(updated) if updated else None,
        }

    async def _reclassify_lead(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        detail = await self.ctx.read_storage.reclassify_person(
            UUID(str(tool_input["person_id"])),
            new_tier=str(tool_input["new_tier"]),
            reason=str(tool_input["reason"]),
            reviewed_by=str(tool_input.get("reviewed_by") or "agent"),
        )
        return _dump(detail)

    async def _pull_event(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        summary = await self.ctx.orchestrator.ingest_luma_event(
            str(tool_input["luma_event_id"]),
            enrich=bool(tool_input.get("enrich", True)),
        )
        result = summary.model_dump(mode="json")
        if bool(tool_input.get("classify", False)) and summary.event_id is not None:
            classification = await self.ctx.classification_service.classify_batch(
                ClassificationScope(event_id=summary.event_id, force_reclassify=True)
            )
            result["classification"] = classification.model_dump(mode="json")
        return result

    async def _enrich_event(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        event_id = UUID(str(tool_input["event_id"]))
        if await self.ctx.event_storage.get_event(event_id) is None:
            raise ValueError("event not found")
        manager = getattr(self.ctx, "job_manager", None)
        if manager is None:
            raise ValueError("job manager is not configured")

        job = manager.start(
            "enrich_event",
            lambda progress: run_event_backfill(
                self.ctx,
                event_id,
                classify=bool(tool_input.get("classify", False)),
                progress=progress,
            ),
            key=f"event_backfill:{event_id}",
        )
        payload = job.model_dump(mode="json")
        payload["job_id"] = payload["id"]
        payload["event_id"] = str(event_id)
        return payload

    async def _job_status(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        job_id = UUID(str(tool_input["job_id"]))
        manager = getattr(self.ctx, "job_manager", None)
        record = manager.get(job_id) if manager is not None else None
        if record is None:
            store = getattr(self.ctx, "job_store", None)
            if store is not None:
                record = await store.get(job_id)
        if record is None:
            raise ValueError("job not found")
        return record.model_dump(mode="json")

    async def _classify_event(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        result = await self.ctx.classification_service.classify_batch(
            ClassificationScope(
                event_id=UUID(str(tool_input["event_id"])),
                force_reclassify=True,
                evidence_mode=str(tool_input.get("evidence_mode") or "hybrid"),
            )
        )
        return result.model_dump(mode="json")

    async def _sync_event_to_hubspot(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        hubspot = getattr(self.ctx, "hubspot_client", None)
        if hubspot is None:
            raise ValueError("HubSpot is not configured. Set HUBSPOT_ACCESS_TOKEN.")
        event_id = UUID(str(tool_input["event_id"]))
        event = await self.ctx.event_storage.get_event(event_id)
        if event is None:
            raise ValueError("event not found")
        tiers = _tiers(tool_input.get("tiers")) or ["P0", "P1"]
        export = await self.ctx.read_storage.export_leads(
            LeadFilters(event_id=event_id, tiers=tiers),
            sort=str(tool_input.get("sort") or "tier"),
            profile="icp",
        )
        if not export.rows:
            raise ValueError("No classified leads matched the HubSpot sync filters.")
        list_name = str(tool_input.get("list_name") or _hubspot_list_name(event, tiers))
        result = await hubspot.import_contacts(
            export,
            list_name=list_name,
            marketable=bool(tool_input.get("marketable", False)),
            create_list=bool(tool_input.get("create_list", True)),
        )
        return result.model_dump(mode="json")

    async def _send_identity_review_to_clay(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        clay = getattr(self.ctx, "clay_client", None)
        if clay is None:
            raise ValueError("Clay is not deployed. Configure CLAY_WEBHOOK_URL before using it.")
        person_id = UUID(str(tool_input["person_id"]))
        detail = await self.ctx.read_storage.get_lead_detail(person_id)
        if detail is None:
            raise ValueError("person not found")
        return await clay.enqueue_identity_review(detail.model_dump(mode="json"))


def _tool_definitions() -> list[AgentTool]:
    return [
        AgentTool(
            name="list_leads",
            description="List and filter currently classified mint leads. Returns total count and up to 25 lead summaries.",
            input_schema={
                "type": "object",
                "properties": {
                    "tier": {"type": "string", "enum": TIERS},
                    "primary_product": {"type": "string", "enum": ["maas", "dedicated", "gpu"]},
                    "event_id": {"type": "string"},
                    "vertical": {"type": "string"},
                    "min_confidence": {"type": "integer", "minimum": 1, "maximum": 5},
                    "review_status": {"type": "string", "enum": ["CLASSIFIED", "NEEDS_REVIEW"]},
                    "resolution_confidence": {"type": "string", "enum": ["high", "medium", "low", "flagged"]},
                    "search": {"type": "string"},
                    "sort": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                    "offset": {"type": "integer", "minimum": 0},
                },
            },
        ),
        AgentTool(
            name="export_leads",
            description="Export currently classified mint leads to a CSV or XLSX artifact using lead filters. Returns a download URL and row count.",
            input_schema={
                "type": "object",
                "properties": {
                    "format": {"type": "string", "enum": ["xlsx", "csv"], "default": "xlsx"},
                    "profile": {"type": "string", "enum": ["standard", "icp", "mql"], "default": "standard"},
                    "tier": {"type": "string", "enum": TIERS},
                    "tiers": {
                        "type": "array",
                        "items": {"type": "string", "enum": TIERS},
                    },
                    "primary_product": {"type": "string", "enum": ["maas", "dedicated", "gpu"]},
                    "event_id": {"type": "string"},
                    "vertical": {"type": "string"},
                    "min_confidence": {"type": "integer", "minimum": 1, "maximum": 5},
                    "review_status": {"type": "string", "enum": ["CLASSIFIED", "NEEDS_REVIEW"]},
                    "resolution_confidence": {"type": "string", "enum": ["high", "medium", "low", "flagged"]},
                    "search": {"type": "string"},
                    "sort": {"type": "string"},
                },
            },
        ),
        AgentTool(
            name="get_lead",
            description="Fetch a full lead profile by person_id or email.",
            input_schema={
                "type": "object",
                "properties": {
                    "person_id": {"type": "string"},
                    "email": {"type": "string"},
                },
            },
        ),
        AgentTool(
            name="list_events",
            description="List tracked mint events.",
            input_schema={
                "type": "object",
                "properties": {"status": {"type": "string"}},
            },
        ),
        AgentTool(
            name="search_chat_history",
            description="Search saved event-linked and miscellaneous chat history for prior context.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "maximum": 20},
                },
                "required": ["query"],
            },
        ),
        AgentTool(
            name="event_summary",
            description="Get tier, resolution, product-fit, and lead rollups for one event.",
            input_schema={
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
                "required": ["event_id"],
            },
        ),
        AgentTool(
            name="event_analytics",
            description=(
                "Get the event scorecard: Turnout and Audience Fit (computed from attendance and "
                "lead tiers) plus any manual Engagement/Content/Follow-up ratings, each on a 0-100 "
                "scale. Pass event_id for one event's full radar detail, or omit event_id to get "
                "compact scorecards for ALL events so you can compare performance and recommend "
                "improvements grounded in past events."
            ),
            input_schema={
                "type": "object",
                "properties": {"event_id": {"type": "string"}},
            },
        ),
        AgentTool(
            name="pipeline_summary",
            description="Get cross-event pipeline rollups.",
            input_schema={"type": "object", "properties": {}},
        ),
        AgentTool(
            name="data_quality_summary",
            description="Get data-quality and failure-mode rollups.",
            input_schema={"type": "object", "properties": {}},
        ),
        AgentTool(
            name="list_flagged",
            description="List contradicted-affiliation leads needing company disambiguation.",
            input_schema={"type": "object", "properties": {"limit": {"type": "integer", "maximum": 25}}},
        ),
        AgentTool(
            name="list_needs_review",
            description="List low-confidence classification or promising low-confidence resolution leads.",
            input_schema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["classification", "resolution", "all"]},
                    "limit": {"type": "integer", "maximum": 25},
                },
            },
        ),
        AgentTool(
            name="resolve_flagged",
            description="Resolve a flagged lead by selecting or supplying the correct company, then enrich and classify it.",
            input_schema={
                "type": "object",
                "properties": {
                    "person_id": {"type": "string"},
                    "chosen_domain": {"type": "string"},
                    "company_domain": {"type": "string"},
                    "company_name": {"type": "string"},
                    "chosen_company_id": {"type": "string"},
                },
                "required": ["person_id"],
            },
            side_effecting=True,
        ),
        AgentTool(
            name="reclassify_lead",
            description="Write a manual classification override for a lead.",
            input_schema={
                "type": "object",
                "properties": {
                    "person_id": {"type": "string"},
                    "new_tier": {"type": "string", "enum": TIERS},
                    "reason": {"type": "string"},
                    "reviewed_by": {"type": "string"},
                },
                "required": ["person_id", "new_tier", "reason"],
            },
            side_effecting=True,
        ),
        AgentTool(
            name="pull_event",
            description="Pull a Luma event into mint and optionally enrich/classify it.",
            input_schema={
                "type": "object",
                "properties": {
                    "luma_event_id": {"type": "string"},
                    "enrich": {"type": "boolean"},
                    "classify": {"type": "boolean"},
                },
                "required": ["luma_event_id"],
            },
            side_effecting=True,
        ),
        AgentTool(
            name="enrich_event",
            description=(
                "Start background enrichment for an existing mint event by its mint event_id. "
                "Use this for CSV imports; it also safely handles Luma-backed events. Optionally classify "
                "after enrichment. Returns a job_id; use job_status before exporting."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "classify": {"type": "boolean", "default": False},
                },
                "required": ["event_id"],
            },
            side_effecting=True,
        ),
        AgentTool(
            name="job_status",
            description="Check a background mint job. Do not export until enrichment/classification succeeded.",
            input_schema={
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        ),
        AgentTool(
            name="classify_event",
            description=(
                "Reclassify eligible event leads without fresh enrichment. Use evidence_mode=self_reported "
                "to tier only from attendee answers while reusing stored Apollo/Exa company stage."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "evidence_mode": {
                        "type": "string",
                        "enum": ["hybrid", "self_reported"],
                        "default": "hybrid",
                    },
                },
                "required": ["event_id"],
            },
            side_effecting=True,
        ),
        AgentTool(
            name="sync_event_to_hubspot",
            description=(
                "Upload event leads to HubSpot as contacts and create a static HubSpot list/segment "
                "from the import. Defaults to P0/P1 MQLs."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "tiers": {
                        "type": "array",
                        "items": {"type": "string", "enum": TIERS},
                        "default": ["P0", "P1"],
                    },
                    "list_name": {"type": "string"},
                    "marketable": {"type": "boolean", "default": False},
                    "create_list": {"type": "boolean", "default": True},
                    "sort": {"type": "string"},
                },
                "required": ["event_id"],
            },
            side_effecting=True,
        ),
        AgentTool(
            name="send_identity_review_to_clay",
            description=(
                "Queue one low-confidence or conflicted person in the configured Clay identity waterfall. "
                "Clay is local-only/undeployed until its webhook and callback workflow are configured."
            ),
            input_schema={
                "type": "object",
                "properties": {"person_id": {"type": "string"}},
                "required": ["person_id"],
            },
            side_effecting=True,
        ),
    ]


def _lead_filters(tool_input: dict[str, Any]) -> LeadFilters:
    return LeadFilters.model_validate(
        {
            "tier": tool_input.get("tier"),
            "tiers": _tiers(tool_input.get("tiers")),
            "primary_product": tool_input.get("primary_product"),
            "event_id": tool_input.get("event_id"),
            "vertical": tool_input.get("vertical"),
            "min_confidence": tool_input.get("min_confidence"),
            "review_status": tool_input.get("review_status"),
            "resolution_confidence": tool_input.get("resolution_confidence"),
            "search": tool_input.get("search"),
        }
    )


def _tiers(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, list):
        raw = value
    else:
        return []
    tiers = []
    for item in raw:
        tier = str(item).strip().upper()
        if tier in set(TIERS) and tier not in tiers:
            tiers.append(tier)
    return tiers


def _limit(value: Any) -> int:
    try:
        limit = int(value or MAX_TOOL_ROWS)
    except (TypeError, ValueError):
        limit = MAX_TOOL_ROWS
    return min(max(limit, 1), MAX_TOOL_ROWS)


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return jsonable_encoder(value)


def _hubspot_list_name(event: dict[str, Any], tiers: list[str]) -> str:
    prefix = "Mint Luma Event" if event.get("luma_event_id") else "Mint Event"
    event_name = str(event.get("name") or event.get("id") or "Event").strip()
    suffix = "All Classified" if not tiers else "/".join(tiers)
    return f"{prefix} - {event_name} - {suffix}"
