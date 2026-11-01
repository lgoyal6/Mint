import asyncio

import httpx

from mint.backend.ingestion.luma import LumaClient


def run(coro):
    return asyncio.run(coro)


def test_luma_client_fetches_event_lists_events_and_pages_guests() -> None:
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append((request.url.path, params))
        assert request.headers["x-luma-api-key"] == "luma-key"
        if request.url.path == "/v1/event/get":
            return httpx.Response(200, json={"event": {"api_id": params["api_id"], "name": "Demo"}})
        if request.url.path == "/v1/calendar/list-events":
            if "pagination_cursor" not in params:
                return httpx.Response(
                    200,
                    json={
                        "entries": [{"event": {"api_id": "evt-1"}}],
                        "next_cursor": "events-page-2",
                    },
                )
            return httpx.Response(200, json={"entries": [{"event": {"api_id": "evt-2"}}]})
        if request.url.path == "/v1/event/get-guests" and "pagination_cursor" not in params:
            return httpx.Response(
                200,
                json={
                    "entries": [
                        {"guest": {"email": "a@example.com", "approval_status": "going"}},
                        {"guest": {"email": "b@example.com", "approval_status": "pending"}},
                    ],
                    "next_cursor": "cursor-2",
                },
            )
        if request.url.path == "/v1/event/get-guests":
            return httpx.Response(
                200,
                json={"entries": [{"guest": {"email": "c@example.com", "approval_status": "going"}}]},
            )
        raise AssertionError(f"unexpected path {request.url.path}")

    client = LumaClient(
        api_key="luma-key",
        base_url="https://public-api.luma.com",
        transport=httpx.MockTransport(handler),
    )

    event = run(client.get_event("evt-1"))
    events = run(client.list_events(status="all"))
    guests = run(client.get_guests("evt-1", status="going"))

    assert event == {"api_id": "evt-1", "name": "Demo"}
    assert events == [{"api_id": "evt-1"}, {"api_id": "evt-2"}]
    assert guests == [
        {"email": "a@example.com", "approval_status": "going"},
        {"email": "c@example.com", "approval_status": "going"},
    ]
    assert seen == [
        ("/v1/event/get", {"api_id": "evt-1"}),
        (
            "/v1/calendar/list-events",
            {"pagination_limit": "100", "sort_column": "start_at", "sort_direction": "desc nulls last"},
        ),
        (
            "/v1/calendar/list-events",
            {
                "pagination_limit": "100",
                "sort_column": "start_at",
                "sort_direction": "desc nulls last",
                "pagination_cursor": "events-page-2",
            },
        ),
        ("/v1/event/get-guests", {"event_id": "evt-1", "pagination_limit": "100"}),
        (
            "/v1/event/get-guests",
            {"event_id": "evt-1", "pagination_limit": "100", "pagination_cursor": "cursor-2"},
        ),
    ]
    run(client.close())


def test_luma_client_accepts_plain_list_guest_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/event/get-guests"
        return httpx.Response(
            200,
            json=[
                {"email": "a@example.com", "approval_status": "approved"},
                {"email": "b@example.com", "approval_status": "pending"},
            ],
        )

    client = LumaClient(
        api_key="luma-key",
        base_url="https://public-api.luma.com",
        transport=httpx.MockTransport(handler),
    )

    guests = run(client.get_guests("evt-1", status="going"))

    assert guests == [{"email": "a@example.com", "approval_status": "approved"}]
    run(client.close())


def test_luma_health_check_verifies_key_without_fetching_calendar_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/users/get-self"
        assert request.headers["x-luma-api-key"] == "luma-key"
        return httpx.Response(200, json={"id": "usr-test"})

    client = LumaClient(api_key="luma-key", transport=httpx.MockTransport(handler))

    assert run(client.health_check()) == {
        "configured": True,
        "verified": True,
        "status": "ok",
        "http_status": 200,
        "credit_cost": "none",
        "detail": "Calendar-scoped API key accepted; no event or guest data fetched.",
    }
    run(client.close())


def test_luma_health_check_reports_rejected_key() -> None:
    client = LumaClient(
        api_key="bad-key",
        transport=httpx.MockTransport(lambda _request: httpx.Response(401, json={"message": "unauthorized"})),
    )

    result = run(client.health_check())

    assert result["configured"] is True
    assert result["verified"] is False
    assert result["status"] == "invalid_key"
    assert result["http_status"] == 401
    run(client.close())
