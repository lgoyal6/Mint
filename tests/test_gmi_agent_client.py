import asyncio
import json

import httpx

from mint.backend.agent.client import GMIAgentClient


def run(coro):
    return asyncio.run(coro)


def test_gmi_agent_client_supports_openai_compatible_tool_calls() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.read())
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "I will list leads.",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "list_leads",
                                        "arguments": '{"tier":"P0"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    client = GMIAgentClient(
        "test-key",
        base_url="https://example.test",
        api_format="openai",
        model="tencent/Hy3",
        retry_backoff_seconds=0,
    )
    client.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))

    try:
        result = run(
            client.create_message(
                system_prompt="system",
                messages=[{"role": "user", "content": "Show P0 leads"}],
                tools=[
                    {
                        "name": "list_leads",
                        "description": "List leads",
                        "input_schema": {"type": "object", "properties": {"tier": {"type": "string"}}},
                    }
                ],
            )
        )
    finally:
        run(client.close())

    assert seen["path"] == "/v1/chat/completions"
    assert seen["payload"]["messages"][0] == {"role": "system", "content": "system"}
    assert seen["payload"]["tools"][0]["function"]["name"] == "list_leads"
    assert result["content"] == [
        {"type": "text", "text": "I will list leads."},
        {"type": "tool_use", "id": "call_1", "name": "list_leads", "input": {"tier": "P0"}},
    ]


def test_gmi_agent_client_converts_tool_results_back_to_openai_messages() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.read())
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"role": "assistant", "content": "Done."}}]},
        )

    client = GMIAgentClient("test-key", base_url="https://example.test", api_format="openai")
    client.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))

    try:
        result = run(
            client.create_message(
                system_prompt="system",
                messages=[
                    {"role": "user", "content": "Show P0 leads"},
                    {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "toolu_1", "name": "list_leads", "input": {"tier": "P0"}}],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": '{"total":1}',
                                "is_error": False,
                            }
                        ],
                    },
                ],
                tools=[],
            )
        )
    finally:
        run(client.close())

    assert seen["payload"]["messages"][-2]["tool_calls"][0]["id"] == "toolu_1"
    assert seen["payload"]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "toolu_1",
        "content": '{"total":1}',
    }
    assert result["content"] == [{"type": "text", "text": "Done."}]


def test_gmi_agent_client_falls_back_to_haiku_when_openai_model_is_rejected() -> None:
    models = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        models.append(payload["model"])
        if len(models) == 1:
            return httpx.Response(400, request=request, json={"error": "model not supported"})
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"role": "assistant", "content": "Recovered."}}]},
        )

    client = GMIAgentClient(
        "test-key",
        base_url="https://example.test",
        api_format="openai",
        model="anthropic/claude-sonnet-4-6",
    )
    client.client = httpx.AsyncClient(
        base_url="https://example.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = run(
            client.create_message(
                system_prompt="system",
                messages=[{"role": "user", "content": "Hello"}],
                tools=[],
            )
        )
    finally:
        run(client.close())

    assert models == ["anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4.5"]
    assert result["content"] == [{"type": "text", "text": "Recovered."}]
