import hmac

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from mint.backend.auth import (
    auth_configured,
    auth_enabled,
    clear_session_cookie,
    create_session_token,
    set_session_cookie,
    valid_session_token,
)


router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, bool]:
    settings = request.app.state.ctx.settings
    if not auth_enabled(settings):
        return {"authenticated": True}
    if not auth_configured(settings):
        raise HTTPException(status_code=503, detail="auth is not configured")
    if not hmac.compare_digest(settings.mint_auth_password or "", body.password):
        raise HTTPException(status_code=401, detail="invalid password")

    set_session_cookie(response, settings, create_session_token(settings))
    return {"authenticated": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    clear_session_cookie(response, request.app.state.ctx.settings)
    return {"authenticated": False}


@router.get("/me")
async def me(request: Request) -> dict[str, bool]:
    settings = request.app.state.ctx.settings
    if not auth_enabled(settings):
        return {"authenticated": True}
    token = request.cookies.get(settings.mint_session_cookie_name)
    return {"authenticated": valid_session_token(settings, token)}
