import httpx
from fastapi import APIRouter, Request
from sqlalchemy import text


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "deployment": "agentbox-scoring-v3"}


@router.get("/health/db")
async def health_db(request: Request) -> dict[str, str]:
    ctx = request.app.state.ctx
    async with ctx.session_factory() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/health/integrations")
async def health_integrations(request: Request) -> dict:
    ctx = request.app.state.ctx
    luma_client = getattr(ctx, "luma_client", None)
    if luma_client is None:
        luma_status = {"configured": False, "verified": False, "status": "disabled", "credit_cost": "none"}
    else:
        try:
            luma_status = await luma_client.health_check()
        except Exception:
            luma_status = {"configured": True, "verified": False, "status": "error", "credit_cost": "none"}

    apollo_client = getattr(ctx, "apollo", None)
    if apollo_client is None:
        apollo_status = {"configured": False, "verified": False, "status": "disabled", "credit_cost": "none"}
    else:
        try:
            apollo_status = await apollo_client.health_check()
        except Exception:
            apollo_status = {"configured": True, "verified": False, "status": "error", "credit_cost": "none"}

    exa_client = getattr(ctx, "exa", None)
    if exa_client is None:
        exa_status = {"configured": False, "verified": False, "status": "disabled", "credit_cost": "none"}
    else:
        try:
            exa_status = exa_client.health_check()
        except Exception:
            exa_status = {"configured": True, "verified": False, "status": "error", "credit_cost": "none"}

    hubspot = getattr(ctx, "hubspot_client", None)
    if hubspot is None:
        hubspot_status = {"configured": False, "status": "disabled"}
    else:
        try:
            check = await hubspot.check_access()
        except Exception as exc:
            response = getattr(exc, "response", None)
            hubspot_status = {
                "configured": True,
                "status": "error",
                "http_status": getattr(response, "status_code", None),
            }
        else:
            hubspot_status = {"configured": True, **check}
    return {
        "status": (
            "ok"
            if luma_status["status"] in {"ok", "disabled"}
            and hubspot_status["status"] in {"ok", "disabled"}
            and apollo_status["status"] in {"ok", "disabled"}
            and exa_status["status"] in {"configured_unverified", "disabled"}
            else "degraded"
        ),
        "luma": luma_status,
        "apollo": apollo_status,
        "exa": exa_status,
        "hubspot": hubspot_status,
        "clay": {"configured": getattr(ctx, "clay_client", None) is not None},
    }


@router.post("/health/integrations/test-enrichment")
async def test_enrichment_integrations(request: Request) -> dict:
    """Make the smallest practical live checks before a costly enrichment run."""
    ctx = request.app.state.ctx
    try:
        apollo = await ctx.apollo.health_check()
    except Exception as exc:
        response = getattr(exc, "response", None)
        apollo = {
            "configured": True,
            "verified": False,
            "status": "error",
            "http_status": getattr(response, "status_code", None),
            "detail": "Apollo verification failed.",
        }
    try:
        exa = await ctx.exa.live_check()
    except httpx.HTTPStatusError as exc:
        exa = {
            "configured": True,
            "verified": False,
            "status": "error",
            "http_status": exc.response.status_code,
            "estimated_cost_usd": 0,
            "detail": "Exa rejected the live verification request.",
        }
    except Exception:
        exa = {
            "configured": True,
            "verified": False,
            "status": "error",
            "estimated_cost_usd": 0,
            "detail": "Exa verification could not complete.",
        }
    return {
        "status": "ok" if apollo.get("verified") and exa.get("verified") else "error",
        "apollo": apollo,
        "exa": exa,
        "estimated_cost_usd": exa.get("estimated_cost_usd", 0),
    }


@router.get("/health/data-quality")
async def data_quality(request: Request) -> dict:
    ctx = request.app.state.ctx
    return await ctx.read_storage.data_quality_summary()
