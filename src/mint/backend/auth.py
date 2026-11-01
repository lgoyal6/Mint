from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import Response


AUTH_EXEMPT_PATHS = {
    "/auth/login",
    "/auth/logout",
    "/auth/me",
    "/admin/sync-luma",
    "/health",
    "/health/db",
    "/health/integrations",
    "/integrations/clay/identity-callback",
}


def auth_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "mint_auth_enabled", False))


def auth_configured(settings: Any) -> bool:
    return bool(getattr(settings, "mint_auth_password", None) and getattr(settings, "mint_session_secret", None))


def is_exempt_path(path: str) -> bool:
    return path in AUTH_EXEMPT_PATHS


def create_session_token(settings: Any, *, now: int | None = None) -> str:
    payload = {
        "iat": int(time.time() if now is None else now),
        "nonce": secrets.token_urlsafe(16),
    }
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = _sign(encoded_payload, _secret(settings))
    return f"{encoded_payload}.{signature}"


def valid_session_token(settings: Any, token: str | None, *, now: int | None = None) -> bool:
    if not token or "." not in token:
        return False
    encoded_payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(encoded_payload, _secret(settings)), signature):
        return False
    try:
        payload = json.loads(_b64decode(encoded_payload).decode())
    except (ValueError, json.JSONDecodeError):
        return False
    issued_at = payload.get("iat")
    if not isinstance(issued_at, int):
        return False
    current = int(time.time() if now is None else now)
    max_age = int(getattr(settings, "mint_session_max_age_seconds", 0) or 0)
    return max_age > 0 and issued_at <= current and current - issued_at <= max_age


def set_session_cookie(response: Response, settings: Any, token: str) -> None:
    response.set_cookie(
        key=getattr(settings, "mint_session_cookie_name", "mint_session"),
        value=token,
        max_age=int(getattr(settings, "mint_session_max_age_seconds", 0) or 0),
        httponly=True,
        secure=bool(getattr(settings, "mint_cookie_secure", True)),
        samesite=_cookie_samesite(settings),
        path="/",
    )


def clear_session_cookie(response: Response, settings: Any) -> None:
    response.delete_cookie(
        key=getattr(settings, "mint_session_cookie_name", "mint_session"),
        httponly=True,
        secure=bool(getattr(settings, "mint_cookie_secure", True)),
        samesite=_cookie_samesite(settings),
        path="/",
    )


def _secret(settings: Any) -> str:
    return str(getattr(settings, "mint_session_secret", "") or "")


def _sign(encoded_payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), encoded_payload.encode(), hashlib.sha256).hexdigest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _cookie_samesite(settings: Any) -> str:
    value = str(getattr(settings, "mint_cookie_samesite", "none") or "none").lower()
    return value if value in {"lax", "strict", "none"} else "none"
