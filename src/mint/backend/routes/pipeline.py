from fastapi import APIRouter, Request


router = APIRouter()


@router.get("/pipeline/summary")
async def pipeline_summary(request: Request) -> dict:
    ctx = request.app.state.ctx
    return await ctx.read_storage.pipeline_summary()
