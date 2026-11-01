import asyncio

import httpx

from mint.backend.classification.gmi import GMIClassificationClient


def run(coro):
    return asyncio.run(coro)


def test_gmi_client_retries_transient_server_errors() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(502, request=request, text="bad gateway")
        return httpx.Response(
            200,
            request=request,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": '{"product_fit":["maas"],"primary_product":"maas","tier":"P1","confidence":3,"np_trigger":null,"key_signal":"API inference","key_risk":"Early","reasoning":"Grounded."}',
                    }
                ]
            },
        )

    client = GMIClassificationClient("test-key", base_url="https://example.test", retry_backoff_seconds=0)
    client.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))

    try:
        result = run(client.classify_tier({}, system_prompt="prompt"))
    finally:
        run(client.close())

    assert calls == 2
    assert result["tier"] == "P1"
    assert result["product_fit"] == ["maas"]


def test_gmi_client_supports_openai_compatible_chat_completions() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = request.read()
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"product_fit":["gpu"],"primary_product":"gpu","tier":"P0","confidence":4,"np_trigger":null,"key_signal":"Training workload","key_risk":"Needs buyer check","reasoning":"Strong fit."}'
                        }
                    }
                ]
            },
        )

    client = GMIClassificationClient(
        "test-key",
        base_url="https://example.test",
        api_format="openai",
        model="tencent/Hy3",
        retry_backoff_seconds=0,
    )
    client.client = httpx.AsyncClient(base_url="https://example.test", transport=httpx.MockTransport(handler))

    try:
        result = run(client.classify_tier({"lead": "x"}, system_prompt="prompt"))
    finally:
        run(client.close())

    assert seen["path"] == "/v1/chat/completions"
    assert result["tier"] == "P0"
    assert result["primary_product"] == "gpu"
