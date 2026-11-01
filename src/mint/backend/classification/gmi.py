import asyncio
import json
import re
from typing import Any

import httpx


class LLMParseError(ValueError):
    def __init__(self, message: str, *, raw_text: str | None = None) -> None:
        super().__init__(message)
        self.raw_text = raw_text


class GMIClassificationClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.gmi-serving.com",
        api_format: str = "anthropic",
        model: str = "anthropic/claude-haiku-4.5",
        max_tokens: int = 1400,
        max_retries: int = 6,
        retry_backoff_seconds: float = 1.0,
        rate_limit_backoff_seconds: float = 10.0,
    ) -> None:
        self.api_format = api_format
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.rate_limit_backoff_seconds = rate_limit_backoff_seconds
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=90,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def classify_tier(
        self,
        context: dict[str, Any],
        *,
        system_prompt: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "model": model or self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        if self.api_format == "openai":
            payload["messages"] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(context, sort_keys=True, default=str)},
            ]
            payload["response_format"] = {"type": "json_object"}
            endpoint = "/v1/chat/completions"
        else:
            payload["system"] = system_prompt
            payload["messages"] = [
                {
                    "role": "user",
                    "content": json.dumps(context, sort_keys=True, default=str),
                }
            ]
            endpoint = "/v1/messages"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.client.post(endpoint, json=payload)
                response.raise_for_status()
                return _parse_json_response(response.json())
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                if not _is_retryable(exc) or attempt >= self.max_retries:
                    raise
                last_error = exc
                await asyncio.sleep(_retry_delay(exc, attempt, self.retry_backoff_seconds, self.rate_limit_backoff_seconds))
        if last_error is not None:
            raise last_error
        raise RuntimeError("GMI classification request failed without an exception")

    async def close(self) -> None:
        await self.client.aclose()


def _parse_json_response(response: dict[str, Any]) -> dict[str, Any]:
    text = _content_text(response)
    try:
        parsed = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError:
        parsed = _json_object_from_text(text)
    if not isinstance(parsed, dict):
        raise LLMParseError("LLM response JSON was not an object", raw_text=text)
    return parsed


def _content_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and message.get("content"):
                return str(message["content"])
    content = response.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(parts)
    if isinstance(content, str):
        return content
    return ""


def _json_object_from_text(text: str) -> dict[str, Any]:
    block = _first_balanced_json_object(_strip_json_fence(text))
    if block is None:
        raise LLMParseError("LLM response did not contain a JSON object", raw_text=text)
    try:
        return json.loads(block)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"LLM response JSON parse failed: {exc}", raw_text=text) from exc


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _first_balanced_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or status_code >= 500
    return False


def _retry_delay(
    exc: Exception,
    attempt: int,
    retry_backoff_seconds: float,
    rate_limit_backoff_seconds: float,
) -> float:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        retry_after = exc.response.headers.get("retry-after")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        return rate_limit_backoff_seconds if retry_backoff_seconds else 0.0
    return retry_backoff_seconds * (2**attempt)
