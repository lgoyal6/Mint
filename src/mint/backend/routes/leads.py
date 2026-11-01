from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from mint.backend.db.read_storage import LeadFilters


router = APIRouter()


@router.get("/leads")
async def list_leads(
    request: Request,
    tier: Literal["P0", "P1", "P2", "NR", "NP"] | None = None,
    primary_product: Literal["maas", "dedicated", "gpu"] | None = None,
    event_id: UUID | None = None,
    vertical: str | None = None,
    min_confidence: int | None = None,
    review_status: str | None = None,
    resolution_confidence: Literal["high", "medium", "low", "flagged"] | None = None,
    search: str | None = None,
    sort: str = "tier",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    ctx = request.app.state.ctx
    page = await ctx.read_storage.list_leads(
        LeadFilters(
            tier=tier,
            primary_product=primary_product,
            event_id=event_id,
            vertical=vertical,
            min_confidence=min_confidence,
            review_status=review_status,
            resolution_confidence=resolution_confidence,
            search=search,
        ),
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return page.model_dump(mode="json")


@router.get("/leads/lookup")
async def get_lead_by_email(email: str, request: Request) -> dict:
    ctx = request.app.state.ctx
    detail = await ctx.read_storage.get_lead_detail_by_email(email)
    if detail is None:
        raise HTTPException(status_code=404, detail="lead not found")
    return detail.model_dump(mode="json")


@router.get("/leads/{person_id}")
async def get_lead(person_id: UUID, request: Request) -> dict:
    ctx = request.app.state.ctx
    detail = await ctx.read_storage.get_lead_detail(person_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="lead not found")
    return detail.model_dump(mode="json")


@router.get("/leads/{person_id}/enrichment")
async def get_lead_enrichment(person_id: UUID, request: Request) -> dict:
    ctx = request.app.state.ctx
    detail = await ctx.read_storage.get_lead_detail(person_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="lead not found")
    return {
        "person_id": str(person_id),
        "apollo_person_data": detail.person.apollo_person_data,
        "exa_person_role_signals": detail.person.exa_person_role_signals,
        "exa_people_search_results": detail.person.exa_people_search_results,
        "company": detail.company.model_dump(mode="json") if detail.company else None,
    }
