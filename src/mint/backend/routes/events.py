import math
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from mint.backend.classification.service import ClassificationScope
from mint.backend.db.read_storage import LeadFilters
from mint.backend.ingestion.backfill import (
    classify_event,
    duration,
    ensure_persistence_succeeded,
    run_auto_review,
    run_event_backfill,
    stage_progress,
)
from mint.backend.ingestion.csv_loader import load_rows
from mint.backend.ingestion.normalizer import csv_rows_with_organization_email
from mint.backend.ingestion.orchestrator import _event_input_from_luma
from mint.backend.ingestion.poller import _guest_count, _luma_event_id
from mint.backend.models import ClassifyResult, EventInput


router = APIRouter()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_ROWS = 10000
LUMA_EVENTS_CACHE_TTL_SECONDS = 300
SUPPORTED_UPLOAD_SUFFIXES = {".csv", ".tsv", ".xlsx"}


class PullEventRequest(BaseModel):
    luma_event_id: str
    enrich: bool = True
    classify: bool = False


class CheckinsRequest(BaseModel):
    luma_event_id: str


class EventEnrichmentRequest(BaseModel):
    enrichment_enabled: bool


class BackfillEventRequest(BaseModel):
    classify: bool = True


class RetryUnresolvedRequest(BaseModel):
    classify: bool = True


class EventRatingsRequest(BaseModel):
    scores: dict[str, int] = Field(default_factory=dict)
    notes: str | None = None


class HubSpotSyncRequest(BaseModel):
    tiers: list[str] = Field(default_factory=lambda: ["P0", "P1"])
    list_name: str | None = None
    marketable: bool = False
    create_list: bool = True


class EventExportRequest(BaseModel):
    tiers: list[str] = Field(default_factory=lambda: ["P0", "P1"])
    profile: str = "mql"
    format: str = "xlsx"


class RegionBackfillRequest(BaseModel):
    retry_unresolved: bool = False


@router.post("/events/pull")
async def pull_event(body: PullEventRequest, request: Request) -> dict:
    ctx = request.app.state.ctx
    summary = await ctx.orchestrator.ingest_luma_event(body.luma_event_id, enrich=body.enrich)
    response = summary.model_dump(mode="json")
    if body.classify:
        if summary.event_id is None:
            classification = ClassifyResult(errors=["event_id unavailable for classification"])
        else:
            classification = await ctx.classification_service.classify_batch(
                ClassificationScope(event_id=summary.event_id)
            )
        response["classification"] = classification.model_dump(mode="json")
    return response


@router.post("/events/upload")
async def upload_event_file(
    request: Request,
    file: UploadFile = File(...),
    event_name: str | None = Form(None),
    event_date: str | None = Form(None),
    event_location: str | None = Form(None),
    event_url: str | None = Form(None),
    classify: bool = Form(False),
    background: bool = Form(False),
    work_emails_only: bool = Form(False),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported upload format. Upload a CSV, TSV, or XLSX file.",
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is too large. Split it into smaller files before ingesting.",
        )

    rows = _load_uploaded_rows(content, suffix)
    if work_emails_only:
        rows = csv_rows_with_organization_email(rows)
        if not rows:
            raise HTTPException(
                status_code=400,
                detail="No valid organization or work email rows were found in this file.",
            )

    # Count after filtering: the limit guards how many rows we actually ingest
    # and enrich, and work_emails_only can drop a file well under it.
    if len(rows) > MAX_UPLOAD_ROWS:
        raise HTTPException(
            status_code=400,
            detail=f"Upload has too many rows ({len(rows)}). Limit is {MAX_UPLOAD_ROWS}; split the file and try again.",
        )

    event = _event_input_from_upload(
        filename=file.filename or "uploaded-leads",
        event_name=event_name,
        event_date=event_date,
        event_location=event_location,
        event_url=event_url,
        guest_count=len(rows),
    )

    ctx = request.app.state.ctx
    if background:
        if event is None:
            raise HTTPException(status_code=400, detail="Background upload requires event metadata.")
        job = ctx.job_manager.start(
            "csv_upload",
            lambda progress: _run_csv_upload(ctx, rows, event, classify=classify, progress=progress),
        )
        return _job_started_response(job)

    try:
        response = await _run_csv_upload(ctx, rows, event, classify=classify)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return response


@router.post("/events/checkins")
async def checkins(body: CheckinsRequest, request: Request) -> dict[str, int]:
    ctx = request.app.state.ctx
    return await ctx.orchestrator.sync_luma_checkins(body.luma_event_id)


@router.get("/events")
async def events(
    request: Request,
    status: str = "all",
    source: str = "all",
    refresh_luma: bool = True,
) -> list[dict]:
    ctx = request.app.state.ctx
    normalized_source = _normalize_source(source)
    stored_events = [_with_source(event) for event in await ctx.event_storage.list_events(status="all")]
    live_luma_events: list[dict[str, Any]] = []

    if refresh_luma and normalized_source in {"all", "luma"}:
        luma_client = getattr(ctx, "luma_client", None)
        if luma_client is None:
            if normalized_source == "luma":
                raise HTTPException(status_code=503, detail="Luma client is not configured.")
        else:
            try:
                raw_events, fetched_fresh = await _cached_luma_list_events(request, luma_client, status="all")
                # Cache hits skip the row sync entirely: the upserts from the last
                # fresh fetch are already in stored_events, and re-writing every
                # Luma event per request made this endpoint slow.
                if fetched_fresh:
                    live_luma_events = await _live_luma_event_rows(
                        ctx.event_storage,
                        raw_events,
                        stored_events,
                    )
            except Exception as exc:
                if normalized_source == "luma":
                    raise HTTPException(status_code=502, detail="Could not list Luma events.") from exc

    events = _merge_event_rows(stored_events, live_luma_events)
    events = _filter_event_rows(events, status=status, source=normalized_source)
    return _sort_event_rows(events)


@router.patch("/events/{event_id}/enrichment")
async def update_event_enrichment(event_id: UUID, body: EventEnrichmentRequest, request: Request) -> dict:
    ctx = request.app.state.ctx
    event = await ctx.event_storage.set_event_enrichment_enabled(event_id, body.enrichment_enabled)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event


@router.post("/events/{event_id}/backfill")
async def backfill_event(event_id: UUID, body: BackfillEventRequest, request: Request) -> JSONResponse:
    ctx = request.app.state.ctx
    event = await ctx.event_storage.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    job = ctx.job_manager.start(
        "event_backfill",
        lambda progress: run_event_backfill(ctx, event_id, classify=body.classify, progress=progress),
        key=f"event_backfill:{event_id}",
    )
    return _job_started_response(job)


@router.post("/events/{event_id}/retry-unresolved")
async def retry_unresolved_event(
    event_id: UUID, body: RetryUnresolvedRequest, request: Request
) -> JSONResponse:
    ctx = request.app.state.ctx
    event = await ctx.event_storage.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    job = ctx.job_manager.start(
        "event_retry_unresolved",
        lambda progress: _run_retry_unresolved(
            ctx, event_id, classify=body.classify, progress=progress
        ),
        key=f"event_retry_unresolved:{event_id}",
    )
    return _job_started_response(job)


@router.post("/events/{event_id}/region-backfill")
async def backfill_event_regions(
    event_id: UUID, body: RegionBackfillRequest, request: Request
) -> JSONResponse:
    ctx = request.app.state.ctx
    event = await ctx.event_storage.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    job = ctx.job_manager.start(
        "event_region_backfill",
        lambda progress: _run_region_backfill(
            ctx, event_id, progress, retry_unresolved=body.retry_unresolved
        ),
        key=f"event_region_backfill:{event_id}",
    )
    return _job_started_response(job)


@router.get("/events/{event_id}/summary")
async def event_summary(event_id: UUID, request: Request) -> dict:
    ctx = request.app.state.ctx
    return await ctx.read_storage.event_summary(event_id)


@router.get("/usage/events")
async def event_api_usage(request: Request) -> dict:
    ctx = request.app.state.ctx
    return {
        "events": await ctx.read_storage.api_usage_by_event(),
        "apollo_dollar_rate_configured": ctx.settings.apollo_credit_cost_usd is not None,
        "pricing": {
            "apollo": (
                "Dollar estimate uses the configured Apollo cost per credit."
                if ctx.settings.apollo_credit_cost_usd is not None
                else "Credits are estimated from qualifying matches. Set APOLLO_CREDIT_COST_USD for dollar estimates."
            ),
            "exa": "Dollar cost is estimated from published search and page-content prices.",
        },
    }


@router.get("/events/{event_id}/analytics")
async def event_analytics(event_id: UUID, request: Request) -> dict:
    ctx = request.app.state.ctx
    try:
        return await ctx.read_storage.event_analytics(event_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/events/{event_id}/ratings")
async def update_event_ratings(event_id: UUID, body: EventRatingsRequest, request: Request) -> dict:
    ctx = request.app.state.ctx
    event = await ctx.event_storage.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    await ctx.event_storage.upsert_event_rating(event_id, scores=body.scores, notes=body.notes)
    return await ctx.read_storage.event_analytics(event_id)


@router.post("/events/{event_id}/hubspot-sync")
async def sync_event_to_hubspot(event_id: UUID, body: HubSpotSyncRequest, request: Request) -> dict:
    ctx = request.app.state.ctx
    hubspot = getattr(ctx, "hubspot_client", None)
    if hubspot is None:
        raise HTTPException(status_code=503, detail="HubSpot is not configured. Set HUBSPOT_ACCESS_TOKEN.")
    event = await ctx.event_storage.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")

    tiers = _normalized_tiers(body.tiers)
    export = await ctx.read_storage.export_leads(
        LeadFilters(event_id=event_id, tiers=tiers),
        sort="tier",
        profile="icp",
    )
    if not export.rows:
        raise HTTPException(status_code=400, detail="No classified leads matched the HubSpot sync filters.")

    list_name = body.list_name or _hubspot_list_name(event, tiers)
    result = await hubspot.import_contacts(
        export,
        list_name=list_name,
        marketable=body.marketable,
        create_list=body.create_list,
    )
    return result.model_dump(mode="json")


@router.post("/events/{event_id}/export")
async def export_event_leads(event_id: UUID, body: EventExportRequest, request: Request) -> dict:
    ctx = request.app.state.ctx
    event = await ctx.event_storage.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    profile = body.profile if body.profile in {"standard", "icp", "mql"} else "mql"
    fmt = body.format.lower()
    if fmt not in {"xlsx", "csv"}:
        raise HTTPException(status_code=400, detail="format must be xlsx or csv")
    export = await ctx.read_storage.export_leads(
        LeadFilters(event_id=event_id, tiers=_normalized_tiers(body.tiers)),
        sort="tier",
        profile=profile,
    )
    if not export.rows:
        raise HTTPException(status_code=400, detail="No classified leads matched the export filters.")
    artifact = ctx.lead_export_service.create_artifact(export, fmt=fmt)
    return artifact.model_dump(mode="json")


def _load_uploaded_rows(content: bytes, suffix: str) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as handle:
        handle.write(content)
        handle.flush()
        try:
            return load_rows(handle.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _run_csv_upload(ctx, rows: list[dict], event: EventInput | None, *, classify: bool, progress=None) -> dict:
    if progress is not None:
        initial_eta = math.ceil(len(rows) / 20 * 60)
        await progress.update(
            stage="enriching",
            done=0,
            total=len(rows),
            message=f"Preparing {len(rows)} leads; initial ETA {duration(initial_eta)} (calibrating from live throughput)",
            eta_seconds=initial_eta,
            elapsed_seconds=0,
            rate_per_minute=None,
        )
    summary = await ctx.orchestrator.ingest_csv(
        rows,
        event=event,
        column_mapping=None,
        on_progress=stage_progress(progress, "enriching", "Enriching {done} of {total} leads"),
    )
    ensure_persistence_succeeded(summary)
    response = summary.model_dump(mode="json")
    if classify:
        response["classification"] = (await classify_event(ctx, summary.event_id, progress=progress)).model_dump(mode="json")
        if progress is not None and getattr(ctx, "job_manager", None) is not None:
            review_job = ctx.job_manager.start(
                "auto_review",
                lambda review_progress: _run_auto_review_job(ctx, review_progress),
                key="auto_review",
            )
            response["auto_review_job_id"] = str(review_job.id)
        else:
            await run_auto_review(ctx, response)
    return response


async def _run_auto_review_job(ctx, progress) -> dict[str, Any]:
    service = getattr(ctx, "auto_review_service", None)
    if service is None:
        return {"skipped": True}
    await progress.update(
        stage="reviewing",
        done=0,
        total=1,
        message="Reviewing uncertain classifications in the background.",
    )
    summary = await service.sweep()
    await progress.update(
        stage="reviewing",
        done=1,
        total=1,
        message="Automated review complete.",
    )
    return summary.model_dump(mode="json")


async def _run_retry_unresolved(
    ctx, event_id: UUID, *, classify: bool, progress
) -> dict[str, Any]:
    sparse_inputs, retry_person_ids = await ctx.event_storage.retryable_leads_for_event(event_id)
    if not retry_person_ids:
        return {
            "event_id": str(event_id),
            "retry_candidates": 0,
            "reenriched_count": 0,
            "reclassified_count": 0,
        }

    summary = await ctx.orchestrator.retry_event_registrations(
        sparse_inputs,
        event_id,
        on_progress=stage_progress(
            progress, "enriching", "Retrying unresolved lead {done} of {total}"
        ),
    )
    ensure_persistence_succeeded(summary)
    response = summary.model_dump(mode="json")
    response["retry_candidates"] = len(retry_person_ids)
    response["reenrichment_candidates"] = len(sparse_inputs)
    if classify:
        classification = await ctx.classification_service.classify_batch(
            ClassificationScope(person_ids=retry_person_ids, force_reclassify=True),
            on_progress=stage_progress(
                progress, "classifying", "Reclassifying retried lead {done} of {total}"
            ),
        )
        response["classification"] = classification.model_dump(mode="json")
        response["reclassified_count"] = classification.classified_count
    return response


async def _run_region_backfill(
    ctx,
    event_id: UUID,
    progress,
    *,
    retry_unresolved: bool = False,
) -> dict[str, Any]:
    candidates = await ctx.read_storage.region_backfill_candidates(
        event_id, include_attempted=retry_unresolved
    )
    total = len(candidates)
    found = 0
    unresolved = 0
    errors: list[dict[str, str]] = []
    await progress.update(
        stage="regions",
        done=0,
        total=total,
        message=f"Found {total} attendees missing region data.",
    )
    for index, candidate in enumerate(candidates, start=1):
        try:
            person = await ctx.apollo.match_person(
                candidate["email"],
                first_name=candidate.get("first_name"),
                last_name=candidate.get("last_name"),
                company_name=candidate.get("company_name"),
                linkedin_url=candidate.get("linkedin_url"),
            )
            await ctx.enrichment_storage.save_region_backfill_person(
                candidate["person_id"], event_id, person
            )
            if person.location:
                found += 1
            else:
                unresolved += 1
        except Exception as exc:
            errors.append({"email": candidate["email"], "error": str(exc)})
        await progress.update(
            stage="regions",
            done=index,
            total=total,
            message=f"Backfilled regions for {index} of {total} attendees.",
        )
    return {
        "event_id": str(event_id),
        "candidates": total,
        "regions_found": found,
        "still_missing": unresolved,
        "errors": errors,
    }


def _job_started_response(job) -> JSONResponse:
    payload = job.model_dump(mode="json")
    payload["job_id"] = payload["id"]
    return JSONResponse(payload, status_code=202)


def _event_input_from_upload(
    *,
    filename: str,
    event_name: str | None,
    event_date: str | None,
    event_location: str | None,
    event_url: str | None,
    guest_count: int,
) -> EventInput | None:
    has_event = any(_text(value) for value in [event_name, event_date, event_location, event_url])
    if not has_event:
        return None

    parsed_date = _parse_event_date(event_date)
    return EventInput(
        luma_event_id=None,
        name=_text(event_name) or Path(filename).stem or "Uploaded leads",
        event_date=parsed_date,
        location=_text(event_location),
        url=_text(event_url),
        status=_manual_event_status(parsed_date),
        guest_count=guest_count,
    )


def _parse_event_date(value: str | None) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid event_date. Use an ISO-8601 datetime.") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _manual_event_status(event_date: datetime | None) -> str:
    if event_date is None:
        return "past"
    return "upcoming" if event_date > datetime.now(UTC) else "past"


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _normalize_source(source: str) -> str:
    normalized = source.strip().lower()
    if normalized in {"all", "luma", "csv"}:
        return normalized
    if normalized in {"manual", "upload", "uploaded"}:
        return "csv"
    raise HTTPException(status_code=400, detail="source must be one of: all, luma, csv")


def _normalized_tiers(tiers: list[str]) -> list[str]:
    allowed = {"P0", "P1", "P2", "NR", "NP"}
    normalized: list[str] = []
    for tier in tiers:
        value = str(tier).strip().upper()
        if value in allowed and value not in normalized:
            normalized.append(value)
    return normalized


def _hubspot_list_name(event: dict[str, Any], tiers: list[str]) -> str:
    prefix = "Mint Luma Event" if event.get("luma_event_id") else "Mint Event"
    event_name = _text(str(event.get("name") or "")) or str(event.get("id"))
    suffix = "All Classified" if not tiers else "/".join(tiers)
    return f"{prefix} - {event_name} - {suffix}"


async def _cached_luma_list_events(request: Request, luma_client, *, status: str) -> tuple[list[dict], bool]:
    """Return (events, fetched_fresh): fetched_fresh is False on a cache hit."""
    cache = getattr(request.app.state, "luma_events_cache", None)
    if cache is None:
        cache = {}
        request.app.state.luma_events_cache = cache

    now = datetime.now(UTC)
    cached = cache.get(status)
    if cached and cached["expires_at"] > now:
        return cached["events"], False

    events = await luma_client.list_events(status=status)
    cache[status] = {
        "events": events,
        "expires_at": now + timedelta(seconds=LUMA_EVENTS_CACHE_TTL_SECONDS),
    }
    return events, True


async def _live_luma_event_rows(
    event_storage,
    raw_events: list[dict],
    stored_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stored_by_luma_id = {
        str(event.get("luma_event_id")): event
        for event in stored_events
        if event.get("luma_event_id")
    }
    event_inputs: list[EventInput] = []
    for raw_event in raw_events:
        luma_event_id = _luma_event_id(raw_event)
        if not luma_event_id:
            continue
        event_inputs.append(
            _event_input_from_luma(
                raw_event,
                fallback_luma_event_id=luma_event_id,
                guest_count=_guest_count(raw_event),
            )
        )

    event_ids_by_luma_id = await event_storage.upsert_events(event_inputs)
    rows: list[dict[str, Any]] = []
    for event_input in event_inputs:
        luma_event_id = event_input.luma_event_id
        if not luma_event_id:
            continue
        event_id = event_ids_by_luma_id.get(luma_event_id)
        if event_id is None:
            continue
        existing = stored_by_luma_id.get(luma_event_id, {})
        rows.append(
            {
                "id": str(event_id),
                "luma_event_id": event_input.luma_event_id,
                "name": event_input.name,
                "event_date": event_input.event_date.isoformat() if event_input.event_date else None,
                "location": event_input.location,
                "url": event_input.url,
                "status": event_input.status,
                "guest_count": event_input.guest_count,
                "enrichment_enabled": existing.get("enrichment_enabled", event_input.enrichment_enabled),
                "synced_at": existing.get("synced_at"),
                "source": "luma",
            }
        )
    return rows


def _with_source(event: dict[str, Any]) -> dict[str, Any]:
    output = dict(event)
    output["source"] = output.get("source") or ("luma" if output.get("luma_event_id") else "csv")
    return output


def _merge_event_rows(
    stored_events: list[dict[str, Any]],
    live_luma_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for event in stored_events:
        merged[_event_key(event)] = event
    for event in live_luma_events:
        merged[_event_key(event)] = event
    return list(merged.values())


def _event_key(event: dict[str, Any]) -> str:
    luma_event_id = event.get("luma_event_id")
    if luma_event_id:
        return f"luma:{luma_event_id}"
    return f"event:{event.get('id')}"


def _filter_event_rows(
    events: list[dict[str, Any]],
    *,
    status: str,
    source: str,
) -> list[dict[str, Any]]:
    normalized_status = status.strip().lower()
    output = events
    if normalized_status != "all":
        output = [event for event in output if str(event.get("status") or "").lower() == normalized_status]
    if source != "all":
        output = [event for event in output if event.get("source") == source]
    return output


def _sort_event_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda event: (
            _event_date_for_sort(event) is not None,
            _event_date_for_sort(event) or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )


def _event_date_for_sort(event: dict[str, Any]) -> datetime | None:
    value = event.get("event_date")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
