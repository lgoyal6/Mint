import hmac

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from mint.backend.classification.service import ClassificationScope


router = APIRouter(prefix="/admin")


class SyncLumaRequest(BaseModel):
    classify: bool = True


class ResetClassificationsRequest(BaseModel):
    dismiss_unresolved: bool = True
    reclassify: bool = True


@router.post("/sync-luma", status_code=status.HTTP_202_ACCEPTED)
async def sync_luma(
    body: SyncLumaRequest,
    request: Request,
    x_mint_sync_token: str | None = Header(default=None, alias="X-Mint-Sync-Token"),
) -> dict:
    _require_sync_token(request, x_mint_sync_token)

    ctx = request.app.state.ctx
    job_manager = getattr(ctx, "job_manager", None)
    if job_manager is None:
        raise HTTPException(status_code=503, detail="job manager is not configured")

    job = job_manager.start("sync_luma", lambda progress: _sync_luma(ctx, body.classify, progress))
    return {"job_id": str(job.id), "status": job.status}


@router.post("/reset-classifications")
async def reset_classifications(
    body: ResetClassificationsRequest,
    request: Request,
    x_mint_sync_token: str | None = Header(default=None, alias="X-Mint-Sync-Token"),
) -> dict:
    _require_sync_token(request, x_mint_sync_token)

    ctx = request.app.state.ctx
    counts = await ctx.classification_storage.reset_all_classifications(
        dismiss_unresolved=body.dismiss_unresolved
    )

    job_id = None
    if body.reclassify:
        job_manager = getattr(ctx, "job_manager", None)
        if job_manager is not None:
            job = job_manager.start("reclassify_all", lambda progress: _reclassify_all(ctx, progress))
            job_id = str(job.id)
        else:
            await _reclassify_all(ctx, None)

    return {**counts, "job_id": job_id}


@router.post("/auto-review")
async def auto_review(
    request: Request,
    x_mint_sync_token: str | None = Header(default=None, alias="X-Mint-Sync-Token"),
) -> dict:
    _require_sync_token(request, x_mint_sync_token)

    service = getattr(request.app.state.ctx, "auto_review_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="auto-review is not configured")
    summary = await service.sweep()
    return summary.model_dump(mode="json")


async def _reclassify_all(ctx, progress) -> dict:
    async def on_progress(done: int, total: int) -> None:
        await progress.update(
            stage="classifying",
            done=done,
            total=total,
            message=f"Classifying {done} of {total} leads",
        )

    result = await ctx.classification_service.classify_batch(
        ClassificationScope(),
        on_progress=on_progress if progress is not None else None,
    )
    payload = result.model_dump(mode="json")
    service = getattr(ctx, "auto_review_service", None)
    if service is not None:
        try:
            payload["auto_review"] = (await service.sweep()).model_dump(mode="json")
        except Exception as exc:
            payload.setdefault("errors", []).append(f"auto_review: {exc}")
    return payload


async def _sync_luma(ctx, classify: bool, progress) -> dict:
    if progress is not None:
        await progress.update(stage="syncing", done=0, total=1, message="Syncing managed Luma events")
    summary = await ctx.luma_sync_service.sync_managed_events(classify=classify)
    if progress is not None:
        await progress.update(stage="syncing", done=1, total=1, message="Managed Luma sync complete")
    return summary.model_dump(mode="json")


def _require_sync_token(request: Request, token: str | None) -> None:
    expected = request.app.state.ctx.settings.mint_sync_token or ""
    if not expected or not token or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=401, detail="invalid sync token")
