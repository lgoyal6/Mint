from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx


class GMIAgentClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.gmi-serving.com",
        api_format: str = "anthropic",
        model: str = "anthropic/claude-sonnet-4-6",
        max_tokens: int = 2400,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.api_format = api_format
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=120,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def create_message(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        if self.api_format == "openai":
            payload["messages"] = _openai_messages(system_prompt, messages)
            payload["tools"] = _openai_tools(tools)
            payload["tool_choice"] = "auto"
            endpoint = "/v1/chat/completions"
        else:
            payload["system"] = system_prompt
            payload["messages"] = messages
            payload["tools"] = tools
            endpoint = "/v1/messages"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.post(endpoint, json=payload)
                if (
                    response.status_code == 400
                    and self.api_format == "openai"
                    and payload["model"] != "anthropic/claude-haiku-4.5"
                ):
                    payload["model"] = "anthropic/claude-haiku-4.5"
                    response = await self.client.post(endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
                return _anthropicish_response(data) if self.api_format == "openai" else data
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                if not _is_retryable(exc) or attempt >= self.max_retries:
                    raise
                last_error = exc
                await asyncio.sleep(self.retry_backoff_seconds * (2**attempt))
        if last_error is not None:
            raise last_error
        raise RuntimeError("GMI agent request failed without an exception")

    async def close(self) -> None:
        await self.client.aclose()


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False


def _openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def _openai_messages(system_prompt: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role == "assistant" and isinstance(content, list):
            converted.append(_openai_assistant_message(content))
            continue
        if role == "user" and isinstance(content, list) and _is_tool_results(content):
            converted.extend(_openai_tool_result_messages(content))
            continue
        converted.append({"role": role, "content": _text_content(content)})
    return converted


def _openai_assistant_message(content: list[dict[str, Any]]) -> dict[str, Any]:
    text_parts = [str(block.get("text")) for block in content if block.get("type") == "text" and block.get("text")]
    tool_calls = []
    for block in content:
        if block.get("type") != "tool_use":
            continue
        tool_calls.append(
            {
                "id": str(block.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(block.get("name") or ""),
                    "arguments": json.dumps(block.get("input") or {}),
                },
            }
        )
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(text_parts).strip() or None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _openai_tool_result_messages(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "role": "tool",
            "tool_call_id": str(block.get("tool_use_id") or ""),
            "content": _text_content(block.get("content")),
        }
        for block in content
        if block.get("type") == "tool_result"
    ]


def _is_tool_results(content: list[dict[str, Any]]) -> bool:
    return bool(content) and all(block.get("type") == "tool_result" for block in content)


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
                elif block.get("content"):
                    parts.append(str(block["content"]))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _anthropicish_response(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return {"role": "assistant", "content": ""}
    first = choices[0]
    if not isinstance(first, dict):
        return {"role": "assistant", "content": ""}
    message = first.get("message")
    if not isinstance(message, dict):
        return {"role": "assistant", "content": ""}

    content: list[dict[str, Any]] = []
    text = message.get("content")
    if text:
        content.append({"type": "text", "text": str(text)})
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                continue
            content.append(
                {
                    "type": "tool_use",
                    "id": str(call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "input": _json_object(function.get("arguments")),
                }
            )
    return {"role": "assistant", "content": content}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
