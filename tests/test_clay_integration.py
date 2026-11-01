import asyncio
import json

import httpx

from mint.backend.integrations.clay import ClayClient


def run(coro):
    return asyncio.run(coro)


def test_clay_webhook_receives_identity_review_context() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"accepted": True})

    client = ClayClient("https://clay.example/webhook", transport=httpx.MockTransport(handler))
    result = run(
        client.enqueue_identity_review(
            {
                "person": {
                    "id": "person-1",
                    "email": "founder@example.ai",
                    "first_name": "Alex",
                    "last_name": "Founder",
                    "resolution_confidence": "low",
                    "candidate_companies": [{"domain": "example.ai"}],
                },
                "company": {"name": "Example AI", "domain": "example.ai"},
            }
        )
    )
    run(client.close())

    assert result["queued"] is True
    assert requests[0]["mint_person_id"] == "person-1"
    assert requests[0]["email"] == "founder@example.ai"
    assert requests[0]["mint_callback_path"] == "/integrations/clay/identity-callback"
