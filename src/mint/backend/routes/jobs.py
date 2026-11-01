from uuid import UUID

from fastapi import APIRouter, HTTPException, Request


router = APIRouter()


@router.get("/jobs/{job_id}")
async def job_status(job_id: UUID, request: Request) -> dict:
    manager = getattr(request.app.state.ctx, "job_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="job manager is not configured")
    record = manager.get(job_id)
    if record is None:
        store = getattr(request.app.state.ctx, "job_store", None)
        if store is not None:
            record = await store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return record.model_dump(mode="json")
