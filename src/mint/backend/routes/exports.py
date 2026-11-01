from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse


router = APIRouter()


@router.get("/exports/{filename}")
async def download_export(filename: str, request: Request) -> FileResponse:
    ctx = request.app.state.ctx
    path = ctx.lead_export_service.resolve_artifact(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="export not found")
    return FileResponse(
        path,
        filename=Path(path).name,
        media_type=_media_type(Path(path).suffix),
    )


def _media_type(suffix: str) -> str:
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"
