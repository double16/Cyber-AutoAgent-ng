from types import SimpleNamespace

from modules.evaluation.trace_parser import (
    ParsedMessage,
    ParsedToolCall,
    ParsedTrace,
    TraceParser,
)


def test_parsed_trace_properties_and_tool_outputs():
    trace = ParsedTrace(
        trace_id="t",
        trace_name="Trace",
        objective="Assess",
        messages=[ParsedMessage("user", "short"), ParsedMessage("assistant", "x" * 40)],
        tool_calls=[
            ParsedToolCall("shell", {"cmd": "id"}, output="uid=0"),
            ParsedToolCall("mem0_store", {"content": "finding"}, output=None),
        ],
    )

    assert trace.is_multi_turn is True
    assert trace.has_tool_usage is True
    assert trace.get_tool_outputs() == [
        "Tool [shell]: uid=0",
        "Tool [mem0_store] executed: {'content': 'finding'}",
    ]


def test_extract_objective_from_metadata_input_and_name():
    parser = TraceParser()

    assert (
            parser._extract_objective(SimpleNamespace(metadata={"attributes": {"objective.description": "Find bugs"}}))
            == "Find bugs"
    )
    assert parser._extract_objective(SimpleNamespace(metadata={"objective": "Check auth"})) == "Check auth"
    assert parser._extract_objective(SimpleNamespace(input='{"objective": "Map api"}')) == "Map api"
    assert (
            parser._extract_objective(SimpleNamespace(input=[{"content": "Objective: Test login\nOther: x"}]))
            == "Test login"
    )
    assert (
            parser._extract_objective(SimpleNamespace(name="Security Assessment - example.com - OP"))
            == "Security assessment of example.com"
    )


def test_fetch_observations_returns_existing_objects_and_fetches_ids():
    existing = SimpleNamespace(type="GENERATION")
    parser = TraceParser()
    assert parser._fetch_observations(SimpleNamespace(observations=[existing])) == [existing]

    fetched = SimpleNamespace(type="EVENT")
    langfuse = SimpleNamespace(api=SimpleNamespace(observations=SimpleNamespace(get=lambda obs_id: fetched)))
    parser = TraceParser(langfuse_client=langfuse)
    assert parser._fetch_observations(SimpleNamespace(observations=["obs1"])) == [fetched]


def test_parse_messages_and_content_from_observations():
    parser = TraceParser()
    trace = SimpleNamespace(
        metadata={"objective": "Assess target"},
        input="trace input with enough length",
        output={"content": [{"type": "text", "text": "assistant text"}]},
    )
    observations = [
        SimpleNamespace(type="GENERATION", output={"message": "generated"}, id="g1", model="m", startTime=1.0),
        SimpleNamespace(type="EVENT", input="user event with enough length", id="e1", startTime=2.0),
        SimpleNamespace(type="SPAN", name="Tool: shell", input={"cmd": "id"}, output="uid=0", startTime=3.0),
    ]

    messages = parser._extract_messages(trace, observations)

    assert [message.role for message in messages] == ["user", "assistant", "user", "user"]
    assert messages[0].content == "Assess target"
    assert messages[1].content == "generated"
    tool_message = parser._extract_tool_as_message(observations[2])
    assert tool_message.role == "system"
    assert "Tool tool: shell called" in tool_message.content


def test_parse_tool_observations_and_counts():
    parser = TraceParser()
    observations = [
        {
            "type": "TOOL",
            "name": "Tool: mem0_store",
            "input": [{"content": '{"action":"store","content":"critical finding"}'}],
            "output": {"message": "stored"},
            "statusMessage": None,
        },
        SimpleNamespace(
            type="SPAN",
            name="execute_tool http_request",
            input={"url": "https://example.test"},
            output=[{"text": "HTTP/1.1 200"}],
            statusMessage="error",
        ),
    ]

    tools = parser._extract_tool_calls(SimpleNamespace(), observations)

    assert [tool.name for tool in tools] == ["mem0_store", "http_request"]
    assert tools[0].input_data == {"action": "store", "content": "critical finding"}
    assert tools[1].success is False
    assert parser.count_memory_operations(tools) == 1
    assert parser.count_evidence_findings(tools) == 1


def test_context_formatting_memory_findings_and_current_counts():
    parser = TraceParser()
    trace = ParsedTrace(
        trace_id="t",
        trace_name="Trace",
        objective="Assess",
        messages=[ParsedMessage("system", "finding: exposed token")],
        metadata={"operation_id": "OP1"},
        tool_calls=[
            ParsedToolCall("shell", {}, output="whoami"),
            ParsedToolCall(
                "mem0_store",
                {"content": "SQL injection",
                 "metadata": {"operation_id": "OP1", "severity": "high", "category": "sqli"}},
                output="stored",
            ),
            ParsedToolCall(
                "mem0_store",
                {"content": "Other op", "metadata": {"operation_id": "OP2"}},
                output="stored",
            ),
            ParsedToolCall("http_request", {}, output="HTTP 500"),
        ],
    )

    contexts = parser._prepare_tool_contexts(trace)

    assert "[Shell Command Output] whoami" in contexts
    assert "[Memory Store] SQL injection" in contexts
    assert "[HTTP Response] HTTP 500" in contexts
    assert "[Security Finding - high/sqli] SQL injection" in contexts
    assert "[System] finding: exposed token" in contexts
    assert parser.count_current_evidence_findings(trace) == 1


def test_parse_trace_returns_none_on_error_and_metadata_extraction():
    parser = TraceParser()
    assert parser.parse_trace(object()) is not None

    token_usage = SimpleNamespace(input=1, output=2, total=3)
    metadata = parser._extract_metadata(
        SimpleNamespace(
            metadata={"attributes": {"operation.id": "OP1"}},
            session_id="S1",
            latency=123,
            tokenUsage=token_usage,
        )
    )

    assert metadata["operation_id"] == "OP1"
    assert metadata["session_id"] == "S1"
    assert metadata["latency_ms"] == 123
    assert metadata["token_usage"] == {"input": 1, "output": 2, "total": 3}
