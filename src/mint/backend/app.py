from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from mint.backend.auth import auth_configured, auth_enabled, is_exempt_path, valid_session_token
from mint.backend.deps import build_context, close_context
from mint.backend.routes import admin, agent, auth, chat, events, exports, health, integrations, jobs, leads, pipeline, review, source
from mint.backend.settings import Settings


ContextBuilder = Callable[[Settings], Awaitable[Any]]
ContextCloser = Callable[[Any], Awaitable[None]]


def create_app(
    settings: Settings | None = None,
    *,
    context_builder: ContextBuilder = build_context,
    context_closer: ContextCloser = close_context,
) -> FastAPI:
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ctx = await context_builder(resolved_settings)
        try:
            yield
        finally:
            await context_closer(app.state.ctx)

    app = FastAPI(title="mint", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(resolved_settings),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_session(request, call_next):
        settings = request.app.state.ctx.settings
        if (
            request.method == "OPTIONS"
            or not auth_enabled(settings)
            or is_exempt_path(request.url.path)
        ):
            return await call_next(request)
        if not auth_configured(settings):
            return JSONResponse({"detail": "auth is not configured"}, status_code=503)
        token = request.cookies.get(settings.mint_session_cookie_name)
        if not valid_session_token(settings, token):
            return JSONResponse({"detail": "not authenticated"}, status_code=401)
        return await call_next(request)

    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(exports.router)
    app.include_router(jobs.router)
    app.include_router(leads.router)
    app.include_router(review.router)
    app.include_router(integrations.router)
    app.include_router(pipeline.router)
    app.include_router(source.router)
    app.include_router(agent.router)
    app.include_router(chat.router)
    app.include_router(admin.router)
    return app


def _allowed_origins(settings: Settings) -> list[str]:
    origins = ["http://127.0.0.1:3000", "http://localhost:3000"]
    if settings.mint_frontend_origin:
        origins.append(settings.mint_frontend_origin.rstrip("/"))
    return sorted(set(origins))


app = create_app()
