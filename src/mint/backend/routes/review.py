from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from mint.backend.classification.service import ClassificationScope


router = APIRouter()


class ResolveFlaggedRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chosen_domain: str | None = None
    company_domain: str | None = None
    company_name: str | None = None
    chosen_company_id: UUID | None = None
    refresh_person: bool = False
    remember_aliases: bool = False


class ReclassifyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    new_tier: Literal["P0", "P1", "P2", "NR", "NP"]
    reason: str
    reviewed_by: str | None = None


@router.get("/review/flagged")
async def flagged_queue(
    request: Request,
    event_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    ctx = request.app.state.ctx
    kwargs = {"limit": limit, "offset": offset}
    if event_id is not None:
        kwargs["event_id"] = event_id
    page = await ctx.read_storage.list_flagged(**kwargs)
    return page.model_dump(mode="json")


@router.post("/review/flagged/{person_id}/resolve")
async def resolve_flagged(person_id: UUID, body: ResolveFlaggedRequest, request: Request) -> dict:
    ctx = request.app.state.ctx
    try:
        resolution = await ctx.read_storage.resolve_flagged_person(
            person_id,
            chosen_domain=body.chosen_domain,
            company_domain=body.company_domain,
            company_name=body.company_name,
            chosen_company_id=body.chosen_company_id,
            remember_aliases=body.remember_aliases,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc

    # Enrichment (Exa deep research) and reclassification take tens of seconds;
    # run them as a background job so the resolve request returns immediately.
    job = None
    job_manager = getattr(ctx, "job_manager", None)
    if job_manager is not None:
        job = job_manager.start(
            "resolve_followup",
            lambda progress: _resolve_followup(ctx, person_id, progress, refresh_person=body.refresh_person),
        )
    else:
        await _resolve_followup(ctx, person_id, None, refresh_person=body.refresh_person)

    updated = await ctx.read_storage.get_lead_detail(person_id)
    return {
        "resolution": resolution.model_dump(mode="json"),
        "job_id": str(job.id) if job is not None else None,
        "updated_lead": updated.model_dump(mode="json") if updated else None,
    }


@router.post("/review/{person_id}/resolve-identity")
async def resolve_identity(person_id: UUID, body: ResolveFlaggedRequest, request: Request) -> dict:
    """Resolve either a contradicted or low-confidence identity review item."""
    return await resolve_flagged(person_id, body, request)


@router.post("/review/{person_id}/clay")
async def send_identity_to_clay(person_id: UUID, request: Request) -> dict:
    ctx = request.app.state.ctx
    clay = getattr(ctx, "clay_client", None)
    if clay is None:
        raise HTTPException(status_code=503, detail="Clay is not deployed. Configure CLAY_WEBHOOK_URL.")
    detail = await ctx.read_storage.get_lead_detail(person_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="person not found")
    return await clay.enqueue_identity_review(detail.model_dump(mode="json"))


async def _resolve_followup(ctx, person_id: UUID, progress, *, refresh_person: bool = False) -> dict:
    async def report(stage: str, message: str) -> None:
        if progress is not None:
            await progress.update(stage=stage, done=0, total=1, message=message)

    enrichment = None
    enrichment_context = await ctx.read_storage.enrichment_context_for_person(person_id)
    enrichment_service = getattr(ctx, "enrichment_service", None)
    if enrichment_context is not None and enrichment_service is not None:
        await report("enriching", "Refreshing Apollo profile and researching the company" if refresh_person else "Researching the company")
        if refresh_person:
            enrichment = await enrichment_service.enrich_resolved_lead(
                enrichment_context.input,
                enrichment_context.resolution,
                enrichment_context.person,
                refresh_person=True,
            )
        else:
            enrichment = await enrichment_service.enrich_resolved_lead(
                enrichment_context.input,
                enrichment_context.resolution,
                enrichment_context.person,
            )

    await report("classifying", "Reclassifying the lead")
    classification = await ctx.classification_service.classify_batch(
        ClassificationScope(person_ids=[person_id], force_reclassify=True)
    )
    return {
        "enrichment": enrichment.model_dump(mode="json") if enrichment else None,
        "classification": classification.model_dump(mode="json"),
    }


@router.get("/review/needs-review")
async def needs_review_queue(
    request: Request,
    queue_type: Literal["classification", "resolution", "all"] = Query("all", alias="type"),
    event_id: UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    ctx = request.app.state.ctx
    kwargs = {"queue_type": queue_type, "limit": limit, "offset": offset}
    if event_id is not None:
        kwargs["event_id"] = event_id
    page = await ctx.read_storage.list_needs_review(**kwargs)
    return page.model_dump(mode="json")


@router.post("/review/clear")
async def clear_review_queues(request: Request, event_id: UUID | None = Query(None)) -> dict:
    """Dismiss unresolved identity rows and mark classification reviews handled."""
    return await request.app.state.ctx.read_storage.clear_review_queues(event_id=event_id)


@router.post("/review/{person_id}/reclassify")
async def reclassify(person_id: UUID, body: ReclassifyRequest, request: Request) -> dict:
    ctx = request.app.state.ctx
    try:
        detail = await ctx.read_storage.reclassify_person(
            person_id,
            new_tier=body.new_tier,
            reason=body.reason,
            reviewed_by=body.reviewed_by,
        )
    except ValueError as exc:
        raise _http_error(exc) from exc
    return detail.model_dump(mode="json")


@router.post("/review/{person_id}/supply-context")
async def supply_context(person_id: UUID, body: ResolveFlaggedRequest, request: Request) -> dict:
    return await resolve_flagged(person_id, body, request)


def _http_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    status_code = 404 if "not found" in message else 400
    return HTTPException(status_code=status_code, detail=message)
