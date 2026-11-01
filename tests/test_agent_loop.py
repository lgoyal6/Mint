import asyncio

from mint.backend.agent.loop import AgentLoop, _clean_reply
from mint.backend.agent.tools import AgentTool


def run(coro):
    return asyncio.run(coro)


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create_message(self, *, system_prompt, messages, tools):
        self.calls.append({"system_prompt": system_prompt, "messages": messages, "tools": tools})
        return self.responses.pop(0)


class FakeTools:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []
        self.tools = [
            AgentTool(
                name="list_leads",
                description="List leads",
                input_schema={"type": "object", "properties": {}},
                side_effecting=False,
            ),
            AgentTool(
                name="event_summary",
                description="Summarize event",
                input_schema={"type": "object", "properties": {}},
                side_effecting=False,
            ),
            AgentTool(
                name="export_leads",
                description="Export leads",
                input_schema={"type": "object", "properties": {}},
                side_effecting=False,
            ),
        ]

    def tool_definitions(self):
        return [tool.model_dump(exclude={"side_effecting"}) for tool in self.tools]

    async def dispatch(self, name, tool_input):
        self.calls.append((name, tool_input))
        if self.fail:
            raise RuntimeError("storage unavailable")
        if name == "export_leads":
            return {
                "filename": "mint-leads-test.xlsx",
                "download_url": "/exports/mint-leads-test.xlsx",
                "row_count": 48,
                "format": "xlsx",
            }
        return {"ok": True, "tool": name}


def tool_response(tool_id: str, name: str, tool_input: dict):
    return {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I will check mint."},
            {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input},
        ],
    }


def final_response(text: str):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def test_loop_executes_tool_use_and_returns_final_text_with_trace() -> None:
    client = ScriptedClient(
        [
            tool_response("toolu_1", "list_leads", {"tier": "P0"}),
            final_response("Found one P0 lead."),
        ]
    )
    tools = FakeTools()
    loop = AgentLoop(client, tools, system_prompt="system", max_turns=4)

    result = run(loop.run([{"role": "user", "content": "Show P0 leads"}]))

    assert result.reply == "Found one P0 lead."
    assert tools.calls == [("list_leads", {"tier": "P0"})]
    assert result.tool_trace[0].name == "list_leads"
    assert result.tool_trace[0].short_result == "ok=True, tool=list_leads"
    second_messages = client.calls[1]["messages"]
    assert second_messages[-1]["role"] == "user"
    assert second_messages[-1]["content"][0]["type"] == "tool_result"


def test_agent_reply_collapses_malformed_repeated_punctuation() -> None:
    assert _clean_reply("Export complete!!!!!!!!!!!!!!!!!!!!") == "Export complete!!!"


def test_loop_executes_multiple_tool_uses_in_order() -> None:
    client = ScriptedClient(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_1", "name": "list_leads", "input": {"limit": 3}},
                    {"type": "tool_use", "id": "toolu_2", "name": "event_summary", "input": {"event_id": "evt"}},
                ],
            },
            final_response("I checked both."),
        ]
    )
    tools = FakeTools()
    loop = AgentLoop(client, tools, system_prompt="system", max_turns=4)

    result = run(loop.run([{"role": "user", "content": "Summarize"}]))

    assert tools.calls == [
        ("list_leads", {"limit": 3}),
        ("event_summary", {"event_id": "evt"}),
    ]
    assert [entry.name for entry in result.tool_trace] == ["list_leads", "event_summary"]
    assert result.reply == "I checked both."


def test_tool_error_is_returned_to_model_without_crashing() -> None:
    client = ScriptedClient(
        [
            tool_response("toolu_1", "list_leads", {}),
            final_response("The query failed because storage is unavailable."),
        ]
    )
    tools = FakeTools(fail=True)
    loop = AgentLoop(client, tools, system_prompt="system", max_turns=4)

    result = run(loop.run([{"role": "user", "content": "Show leads"}]))

    assert result.reply == "The query failed because storage is unavailable."
    assert result.tool_trace[0].error == "storage unavailable"
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert "storage unavailable" in tool_result["content"]


def test_export_tool_trace_keeps_download_artifact() -> None:
    client = ScriptedClient(
        [
            tool_response("toolu_1", "export_leads", {"tiers": ["P0", "P1"], "format": "xlsx"}),
            final_response("Exported 48 leads."),
        ]
    )
    tools = FakeTools()
    loop = AgentLoop(client, tools, system_prompt="system", max_turns=4)

    result = run(loop.run([{"role": "user", "content": "Export P0s and P1s"}]))

    assert tools.calls == [("export_leads", {"tiers": ["P0", "P1"], "format": "xlsx"})]
    assert result.tool_trace[0].artifact == {
        "filename": "mint-leads-test.xlsx",
        "download_url": "/exports/mint-leads-test.xlsx",
        "row_count": 48,
        "format": "xlsx",
    }


def test_event_export_with_explicit_tiers_bypasses_llm_and_resolves_event_name() -> None:
    class EventExportTools(FakeTools):
        async def dispatch(self, name, tool_input):
            self.calls.append((name, tool_input))
            if name == "list_events":
                return [{"id": "event-123", "name": "ClawCamp Berlin"}]
            if name == "export_leads":
                return {
                    "filename": "clawcamp-actionable.xlsx",
                    "download_url": "/exports/clawcamp-actionable.xlsx",
                    "row_count": 38,
                    "format": "xlsx",
                }
            raise AssertionError(name)

    client = ScriptedClient([])
    tools = EventExportTools()
    loop = AgentLoop(client, tools, system_prompt="system")

    result = run(
        loop.run(
            [
                {
                    "role": "user",
                    "content": "For the ClawCamp Berlin event, export all P0, P1, and P2 leads as XLSX.",
                }
            ]
        )
    )

    assert client.calls == []
    assert tools.calls == [
        ("list_events", {"status": "all"}),
        (
            "export_leads",
            {
                "event_id": "event-123",
                "tiers": ["P0", "P1", "P2"],
                "format": "xlsx",
                "profile": "mql",
            },
        ),
    ]
    assert result.reply == "Exported 38 leads. Download the XLSX below."
    assert result.tool_trace[0].artifact["filename"] == "clawcamp-actionable.xlsx"


def test_loop_stops_at_max_turns() -> None:
    client = ScriptedClient(
        [
            tool_response("toolu_1", "list_leads", {}),
            tool_response("toolu_2", "list_leads", {}),
        ]
    )
    loop = AgentLoop(client, FakeTools(), system_prompt="system", max_turns=1)

    result = run(loop.run([{"role": "user", "content": "Loop"}]))

    assert result.reply == "I hit the tool-call limit before I could finish. Please narrow the request and try again."
    assert len(result.tool_trace) == 1


def collect_events(loop, messages):
    async def consume():
        return [event async for event in loop.run_stream(messages)]

    return run(consume())


def test_run_stream_yields_tool_events_and_final_result() -> None:
    client = ScriptedClient(
        [
            tool_response("toolu_1", "list_leads", {"tier": "P0"}),
            final_response("Found one P0 lead."),
        ]
    )
    loop = AgentLoop(client, FakeTools(), system_prompt="system", max_turns=4)

    events = collect_events(loop, [{"role": "user", "content": "Show P0 leads"}])

    assert [event["type"] for event in events] == ["thinking", "tool", "tool", "thinking", "result"]
    assert events[1] == {"type": "tool", "status": "started", "name": "list_leads", "input": {"tier": "P0"}}
    assert events[2]["status"] == "finished"
    assert events[2]["short_result"] == "ok=True, tool=list_leads"
    assert events[-1]["reply"] == "Found one P0 lead."
    assert events[-1]["tool_trace"][0]["name"] == "list_leads"


def test_run_stream_reports_tool_failures() -> None:
    client = ScriptedClient(
        [
            tool_response("toolu_1", "list_leads", {}),
            final_response("The query failed."),
        ]
    )
    loop = AgentLoop(client, FakeTools(fail=True), system_prompt="system", max_turns=4)

    events = collect_events(loop, [{"role": "user", "content": "Show leads"}])

    failed = [event for event in events if event["type"] == "tool" and event["status"] == "failed"]
    assert failed == [{"type": "tool", "status": "failed", "name": "list_leads", "error": "storage unavailable"}]
    assert events[-1]["type"] == "result"
    assert events[-1]["tool_trace"][0]["error"] == "storage unavailable"


def test_run_stream_emits_limit_result_at_max_turns() -> None:
    client = ScriptedClient([tool_response("toolu_1", "list_leads", {})])
    loop = AgentLoop(client, FakeTools(), system_prompt="system", max_turns=1)

    events = collect_events(loop, [{"role": "user", "content": "Loop"}])

    assert events[-1]["type"] == "result"
    assert "tool-call limit" in events[-1]["reply"]
