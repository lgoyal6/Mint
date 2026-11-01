from typing import Any

import httpx


class LumaClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://public-api.luma.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            timeout=45,
            headers={"x-luma-api-key": api_key},
            transport=transport,
        )

    async def get_event(self, event_id: str) -> dict:
        data = await self._get("/v1/event/get", params={"api_id": event_id})
        return _unwrap_event(data)

    async def get_guests(self, event_id: str, status: str = "going") -> list[dict]:
        guests: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"event_id": event_id, "pagination_limit": "100"}
            if cursor:
                params["pagination_cursor"] = cursor
            data = await self._get("/v1/event/get-guests", params=params)
            for guest in _unwrap_entries(data):
                if _matches_status(guest, status):
                    guests.append(guest)
            cursor = _next_cursor(data)
            if not cursor:
                return guests

    async def list_events(self, status: str = "all") -> list[dict]:
        events: list[dict] = []
        cursor: str | None = None
        while True:
            params = {
                "pagination_limit": "100",
                "sort_column": "start_at",
                "sort_direction": "desc nulls last",
            }
            if status != "all":
                params["status"] = status
            if cursor:
                params["pagination_cursor"] = cursor
            data = await self._get("/v1/calendar/list-events", params=params)
            events.extend(_unwrap_entries(data, event_key="event"))
            cursor = _next_cursor(data)
            if not cursor:
                return events

    async def health_check(self) -> dict[str, Any]:
        """Validate the calendar-scoped API key without reading event or guest data."""
        try:
            response = await self.client.get(f"{self.base_url}/v1/users/get-self")
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return {
                "configured": True,
                "verified": False,
                "status": "invalid_key" if status_code in {401, 403} else "error",
                "http_status": status_code,
                "credit_cost": "none",
                "detail": (
                    "Luma rejected the configured API key."
                    if status_code in {401, 403}
                    else "Luma returned an error while checking the API key."
                ),
            }
        except httpx.HTTPError:
            return {
                "configured": True,
                "verified": False,
                "status": "unreachable",
                "credit_cost": "none",
                "detail": "Mint could not reach Luma's API.",
            }
        return {
            "configured": True,
            "verified": True,
            "status": "ok",
            "http_status": response.status_code,
            "credit_cost": "none",
            "detail": "Calendar-scoped API key accepted; no event or guest data fetched.",
        }

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = await self.client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()


def _unwrap_event(data: Any) -> dict:
    if not isinstance(data, dict):
        return {}
    event = data.get("event")
    return event if isinstance(event, dict) else data


def _unwrap_entries(data: Any, event_key: str | None = None) -> list[dict]:
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    if not isinstance(data, dict):
        return []
    entries = data.get("entries") or data.get("guests") or data.get("events") or []
    if not isinstance(entries, list):
        return []
    output: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = event_key or "guest"
        nested = entry.get(key)
        output.append(nested if isinstance(nested, dict) else entry)
    return output


def _matches_status(guest: dict, requested_status: str) -> bool:
    if requested_status == "all":
        return True
    actual = str(guest.get("approval_status") or guest.get("status") or "").strip().lower()
    if requested_status == "going":
        return actual in {"going", "approved"}
    return actual == requested_status


def _next_cursor(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    cursor = (
        data.get("next_cursor")
        or data.get("next")
        or data.get("pagination_cursor")
        or data.get("next_pagination_cursor")
    )
    if isinstance(cursor, str) and cursor.strip():
        return cursor.strip()
    pagination = data.get("pagination")
    if isinstance(pagination, dict):
        nested = pagination.get("next_cursor") or pagination.get("cursor")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None
