from __future__ import annotations

import hmac
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from mint.backend.routes.review import _resolve_followup


router = APIRouter(prefix="/integrations")


class ClayIdentityCallback(BaseModel):
    model_config = ConfigDict(extra="ignore")

    person_id: UUID
    company_domain: str
    company_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    linkedin_url: str | None = None
    evidence: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


@router.post("/clay/identity-callback")
async def clay_identity_callback(
    body: ClayIdentityCallback,
    request: Request,
    x_mint_clay_token: str | None = Header(default=None, alias="X-Mint-Clay-Token"),
) -> dict[str, Any]:
    ctx = request.app.state.ctx
    expected = ctx.settings.clay_callback_token or ""
    if not expected or not x_mint_clay_token or not hmac.compare_digest(expected, x_mint_clay_token):
        raise HTTPException(status_code=401, detail="invalid Clay callback token")

    clay_data = body.model_dump(mode="json", exclude={"person_id", "company_domain", "company_name"})
    await ctx.read_storage.save_clay_identity_data(body.person_id, clay_data)
    try:
        resolution = await ctx.read_storage.resolve_flagged_person(
            body.person_id,
            company_domain=body.company_domain,
            company_name=body.company_name,
        )
    except ValueError as exc:
        status_code = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    job = ctx.job_manager.start(
        "clay_identity_followup",
        lambda progress: _resolve_followup(ctx, body.person_id, progress),
    )
    return {
        "accepted": True,
        "person_id": str(body.person_id),
        "resolution": resolution.model_dump(mode="json"),
        "job_id": str(job.id),
    }
