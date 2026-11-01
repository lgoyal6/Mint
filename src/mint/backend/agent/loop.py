from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolTraceEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    short_result: str | None = None
    error: str | None = None
    artifact: dict[str, Any] | None = None


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reply: str
    tool_trace: list[ToolTraceEntry] = Field(default_factory=list)


class AgentLoop:
    def __init__(
        self,
        client,
        tools,
        *,
        system_prompt: str,
        max_turns: int = 8,
    ) -> None:
        self.client = client
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_turns = max_turns

    async def run(self, messages: list[dict[str, Any]]) -> AgentResult:
        async for event in self.run_stream(messages):
            if event.get("type") == "result":
                return AgentResult.model_validate(event)
        raise RuntimeError("agent stream ended without a result")

    async def run_stream(self, messages: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        conversation = [dict(message) for message in messages]
        trace: list[ToolTraceEntry] = []

        direct_export = await self._direct_event_export(conversation)
        if direct_export is not None:
            tool_input, result = direct_export
            short_result = _short_result(result)
            entry = ToolTraceEntry(
                name="export_leads",
                input=tool_input,
                short_result=short_result,
                artifact=_artifact_result("export_leads", result),
            )
            yield {"type": "tool", "status": "started", "name": "export_leads", "input": tool_input}
            yield {
                "type": "tool",
                "status": "finished",
                "name": "export_leads",
                "short_result": short_result,
            }
            yield _result_event(
                f"Exported {result.get('row_count', 0)} leads. Download the XLSX below.",
                [entry],
            )
            return

        for _turn in range(self.max_turns):
            yield {"type": "thinking"}
            response = await self.client.create_message(
                system_prompt=self.system_prompt,
                messages=conversation,
                tools=self.tools.tool_definitions(),
            )
            content = _content_blocks(response)
            tool_uses = [block for block in content if block.get("type") == "tool_use"]
            if not tool_uses:
                yield _result_event(_clean_reply(_text_from_content(content)), trace)
                return

            conversation.append({"role": "assistant", "content": content})
            tool_results = []
            for tool_use in tool_uses:
                name = str(tool_use.get("name") or "")
                tool_input = tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {}
                yield {"type": "tool", "status": "started", "name": name, "input": tool_input}
                try:
                    result = await self.tools.dispatch(name, tool_input)
                    short_result = _short_result(result)
                    trace.append(
                        ToolTraceEntry(
                            name=name,
                            input=tool_input,
                            short_result=short_result,
                            artifact=_artifact_result(name, result),
                        )
                    )
                    yield {"type": "tool", "status": "finished", "name": name, "short_result": short_result}
                    result_content = json.dumps(result, default=str)
                    is_error = False
                except Exception as exc:
                    error = str(exc)
                    trace.append(ToolTraceEntry(name=name, input=tool_input, error=error))
                    yield {"type": "tool", "status": "failed", "name": name, "error": error}
                    result_content = json.dumps({"error": error})
                    is_error = True

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.get("id"),
                        "content": result_content,
                        "is_error": is_error,
                    }
                )
            conversation.append({"role": "user", "content": tool_results})

        yield _result_event(
            "I hit the tool-call limit before I could finish. Please narrow the request and try again.",
            trace,
        )

    async def _direct_event_export(
        self, messages: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if not messages:
            return None
        last = messages[-1]
        prompt = _message_text(last.get("content")).strip()
        if (
            last.get("role") != "user"
            or not re.search(r"\bexport\b", prompt, re.IGNORECASE)
            or not re.search(r"\bevent\b", prompt, re.IGNORECASE)
        ):
            return None
        tiers = [
            tier
            for tier in ("P0", "P1", "P2", "NR", "NP")
            if re.search(rf"\b{tier}\b", prompt, re.IGNORECASE)
        ]
        if not tiers:
            return None
        try:
            events = await self.tools.dispatch("list_events", {"status": "all"})
        except Exception:
            return None
        if not isinstance(events, list):
            return None
        prompt_lower = prompt.lower()
        matches = [
            event
            for event in events
            if isinstance(event, dict)
            and event.get("id")
            and event.get("name")
            and str(event["name"]).lower() in prompt_lower
        ]
        if len(matches) != 1:
            return None
        fmt = "csv" if re.search(r"\bCSV\b", prompt, re.IGNORECASE) else "xlsx"
        tool_input = {
            "event_id": str(matches[0]["id"]),
            "tiers": tiers,
            "format": fmt,
            "profile": "mql" if fmt == "xlsx" else "icp",
        }
        try:
            result = await self.tools.dispatch("export_leads", tool_input)
        except Exception:
            return None
        return tool_input, result if isinstance(result, dict) else {}


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or block.get("content") or "")
            for block in content
            if isinstance(block, dict)
        )
    return str(content or "")


def _result_event(reply: str, trace: list[ToolTraceEntry]) -> dict[str, Any]:
    return {
        "type": "result",
        "reply": reply,
        "tool_trace": [entry.model_dump(mode="json") for entry in trace],
    }


def _content_blocks(response: dict[str, Any]) -> list[dict[str, Any]]:
    content = response.get("content")
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def _text_from_content(content: list[dict[str, Any]]) -> str:
    parts = [str(block.get("text")) for block in content if block.get("type") == "text" and block.get("text")]
    return "\n".join(parts).strip()


def _clean_reply(reply: str) -> str:
    """Prevent malformed repeated punctuation from expanding a chat response."""
    return re.sub(r"([!?])\1{7,}", r"\1\1\1", reply)


def _short_result(result: Any) -> str:
    if isinstance(result, dict):
        parts = []
        for key, value in result.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                parts.append(f"{key}={value}")
            elif isinstance(value, list):
                parts.append(f"{key}={len(value)} items")
            elif isinstance(value, dict):
                parts.append(f"{key}={len(value)} fields")
            if len(parts) >= 4:
                break
        return ", ".join(parts) or "ok"
    if isinstance(result, list):
        return f"{len(result)} items"
    return str(result)


def _artifact_result(name: str, result: Any) -> dict[str, Any] | None:
    if name != "export_leads" or not isinstance(result, dict):
        return None
    download_url = result.get("download_url")
    filename = result.get("filename")
    if not isinstance(download_url, str) or not isinstance(filename, str):
        return None
    return {
        "filename": filename,
        "download_url": download_url,
        "row_count": result.get("row_count"),
        "format": result.get("format"),
    }
