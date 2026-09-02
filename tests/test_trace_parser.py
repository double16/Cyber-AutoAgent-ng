from types import SimpleNamespace

import pytest

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
            ParsedToolCall("store_finding", {"title": "finding"}, output=None),
        ],
    )

    assert trace.is_multi_turn is True
    assert trace.has_tool_usage is True
    assert trace.get_tool_outputs() == [
        "Tool [shell]: uid=0",
        "Tool [store_finding] executed: {'title': 'finding'}",
    ]


def test_parsed_trace_single_turn_and_output_limit_edge_cases():
    trace = ParsedTrace(
        trace_id="t",
        trace_name="Trace",
        objective="Assess",
        messages=[ParsedMessage("system", "x" * 100)],
        tool_calls=[
            ParsedToolCall("first", {}, output="None"),
            ParsedToolCall("second", {}, output="x" * 900),
        ],
    )

    assert trace.is_multi_turn is True
    assert trace.has_tool_usage is True
    assert trace.get_tool_outputs(limit=1) == [f"Tool [second]: {'x' * 800}"]
    empty = ParsedTrace("t", "Trace", "", [], [])
    assert empty.is_multi_turn is False
    assert empty.has_tool_usage is False


def test_parsed_trace_classifies_message_driven_multi_turn_and_omits_empty_outputs():
    three_messages = ParsedTrace(
        "t", "Trace", "Assess", [ParsedMessage("system", "a"), ParsedMessage("user", "b"), ParsedMessage("assistant", "c")], []
    )
    assert three_messages.is_multi_turn is True
    substantial = ParsedTrace(
        "t",
        "Trace",
        "Assess",
        [ParsedMessage("user", "u" * 31), ParsedMessage("assistant", "a" * 31)],
        [],
    )
    assert substantial.is_multi_turn is True
    outputs = ParsedTrace("t", "Trace", "Assess", [], [ParsedToolCall("empty", {}, output="None"), ParsedToolCall("blank", {}, output=None)])
    assert outputs.get_tool_outputs() == []


def test_trace_helpers_parse_unusual_outputs_and_ignore_irrelevant_observations():
    parser = TraceParser()
    assert parser._extract_content_from_output({"content": [{"type": "image", "text": "ignored"}]}) == ""
    assert parser._extract_content_from_output({"text": "direct"}) == "direct"
    assert parser._extract_content_from_output({"message": "message"}) == "message"
    assert parser._extract_content_from_output(7) is None

    assert parser._parse_observation_message(SimpleNamespace(type="EVENT", input="short")) is None
    assert parser._extract_tool_as_message(SimpleNamespace(name="unrelated", input={"x": 1}, output="no")) is None
    assert parser._parse_tool_observation(SimpleNamespace(name="unrelated", input={}, output=None)) is None

    tool = parser._parse_tool_observation({
        "name": "advanced_payload_coordinator",
        "input": [{"role": "tool"}],
        "output": [{"text": "result"}],
        "startTime": 1.5,
    })
    assert tool.name == "advanced_payload_coordinator"
    assert tool.input_data["raw_input"]
    assert tool.output == "result"


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


def test_extract_objective_handles_invalid_json_and_missing_sources():
    parser = TraceParser()
    assert parser._extract_objective(SimpleNamespace(input='{"objective":')) is None
    assert parser._extract_objective(SimpleNamespace(input=[{"content": "Objective:   "}])) == ""
    assert parser._extract_objective(SimpleNamespace(metadata={"attributes": []}, input="short")) is None
    assert parser._extract_objective(SimpleNamespace(name="Unrelated trace")) is None


def test_fetch_observations_returns_existing_objects_and_fetches_ids():
    existing = SimpleNamespace(type="GENERATION")
    parser = TraceParser()
    assert parser._fetch_observations(SimpleNamespace(observations=[existing])) == [existing]

    fetched = SimpleNamespace(type="EVENT")
    langfuse = SimpleNamespace(api=SimpleNamespace(observations=SimpleNamespace(get=lambda obs_id: fetched)))
    parser = TraceParser(langfuse_client=langfuse)
    assert parser._fetch_observations(SimpleNamespace(observations=["obs1"])) == [fetched]


def test_fetch_observations_skips_failed_ids_and_content_parsing_fallbacks():
    client = SimpleNamespace(
        api=SimpleNamespace(
            observations=SimpleNamespace(
                get=lambda obs_id: (_ for _ in ()).throw(RuntimeError("missing")) if obs_id == "bad" else None
            )
        )
    )
    parser = TraceParser(langfuse_client=client)
    assert parser._fetch_observations(SimpleNamespace(observations=["bad", "missing"])) == []
    assert parser._extract_content_from_output({"content": "plain content"}) == "plain content"
    assert parser._extract_content_from_output(["long enough output"]) == "['long enough output']"


def test_trace_parser_handles_tool_input_output_edge_shapes_and_generic_tool_names():
    parser = TraceParser()
    tool = parser._parse_tool_observation(
        SimpleNamespace(
            name="build_report",
            input=[{"content": "not json"}],
            output=[{"unexpected": "shape"}],
            statusMessage="failed",
            startTime=4,
        )
    )

    assert tool is not None
    assert tool.input_data == {"raw_input": "not json"}
    assert tool.output == "[{'unexpected': 'shape'}]"
    assert tool.success is True


def test_trace_parser_covers_reference_topics_and_tool_message_variants():
    parser = TraceParser()
    assert parser._extract_reference_topics(ParsedTrace("t", "T", "objective", [], [])) == ["objective"]
    assert parser._extract_reference_topics(ParsedTrace("t", "T", "", [], [])) == []
    assert parser._extract_tool_as_message(SimpleNamespace(name="shell", input={"cmd": "id"}, output=None)) is not None
    assert parser._extract_tool_as_message(SimpleNamespace(name="shell", input=None, output="uid")) is not None
    assert parser._extract_tool_as_message(SimpleNamespace(name="shell", input=None, output=None)) is None
    assert parser._extract_content_from_output({"text": "text field"}) == "text field"
    assert parser._extract_content_from_output({"message": "message field"}) == "message field"
    assert parser._extract_content_from_output("") == ""
    assert parser._extract_content_from_output(3) is None

    failed = parser._parse_tool_observation(
        {"name": "shell", "input": ["raw"], "output": {"text": "uid"}, "statusMessage": "error"}
    )
    assert failed is not None and failed.success is False


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


def test_extract_messages_uses_trace_output_when_no_observation_messages_exist():
    parser = TraceParser()
    trace = SimpleNamespace(
        metadata={},
        input="",
        output={"content": [{"type": "text", "text": "final response"}]},
    )
    messages = parser._extract_messages(trace, [])
    assert [message.content for message in messages] == ["final response"]
    assert parser._parse_observation_message(SimpleNamespace(type="EVENT", input="short")) is None
    assert parser._parse_observation_message(SimpleNamespace(type="GENERATION", output={"content": []})) is None


def test_parse_tool_observations_and_counts():
    parser = TraceParser()
    observations = [
        {
            "type": "TOOL",
            "name": "Tool: store_finding",
            "input": [{"content": '{"claim":"critical finding","severity":"CRITICAL"}'}],
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

    assert [tool.name for tool in tools] == ["store_finding", "http_request"]
    assert tools[0].input_data == {"claim": "critical finding", "severity": "CRITICAL"}
    assert tools[1].success is False
    assert parser.count_memory_operations(tools) == 1
    assert parser.count_evidence_findings(tools) == 1


def test_tool_parsing_rejects_non_security_names_and_counts_only_supported_findings():
    parser = TraceParser()
    observations = [
        SimpleNamespace(type="SPAN", name="execute_tool shell", input="id", output={"text": "uid"}),
        SimpleNamespace(type="SPAN", name="execute_tool unrelated", input={}, output="ignored"),
        SimpleNamespace(type="TOOL", name="store_finding", input={"title": "no indicator"}, output="stored"),
        SimpleNamespace(type="TOOL", name="store_finding", input={"vulnerability": "XSS"}, output="stored"),
    ]
    calls = parser._extract_tool_calls(SimpleNamespace(), observations)
    assert [call.name for call in calls] == ["shell", "store_finding", "store_finding"]
    assert parser.count_evidence_findings(calls) == 1


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
                "store_finding",
                {"claim": "SQL injection", "severity": "HIGH"},
                output="stored",
            ),
            ParsedToolCall(
                "store_knowledge",
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
    assert "[Security Finding - unknown/unknown] SQL injection" in contexts
    assert "[System] finding: exposed token" in contexts
    assert parser.count_current_evidence_findings(trace) == 1


def test_trace_context_handles_memory_retrieval_operation_filtering_and_single_turn_tool_fallback():
    parser = TraceParser()
    trace = ParsedTrace(
        trace_id="t",
        trace_name="Trace",
        objective="",
        messages=[ParsedMessage("user", "Assess API")],
        metadata={"operation_id": "OP1"},
        tool_calls=[
            ParsedToolCall("memory_retrieve", {"metadata": {"operation_id": "OP1"}}, output="current finding"),
            ParsedToolCall("store_finding", {"claim": "old", "metadata": {"operation_id": "OP2"}}, output="stored"),
            ParsedToolCall("swarm", {}, output="worker result"),
        ],
    )

    assert parser._extract_memory_findings(trace) == ["[Retrieved Finding] current finding"]
    assert parser.count_current_evidence_findings(trace) == 0
    sample = parser._create_single_turn_sample(trace)
    assert sample.user_input == "Assess API"
    assert "Tool [swarm]: worker result" in sample.response
    assert parser._format_tool_context(trace.tool_calls[-1]) == "[Swarm Agent] worker result"


@pytest.mark.asyncio
async def test_multi_turn_sample_adds_operation_summary_when_messages_are_sparse():
    parser = TraceParser()
    trace = ParsedTrace(
        trace_id="t",
        trace_name="Trace",
        objective="",
        messages=[],
        tool_calls=[ParsedToolCall("shell", {"cmd": "id"}, output=None)],
    )

    sample = await parser._create_multi_turn_sample(trace)

    assert any("Executed 1 operations" in message.content for message in sample.user_input)


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


@pytest.mark.asyncio
async def test_sample_creation_and_topic_generation_fallbacks():
    parser = TraceParser()
    trace = ParsedTrace(
        trace_id="t",
        trace_name="Trace",
        objective="",
        messages=[ParsedMessage("user", "Run assessment")],
        tool_calls=[ParsedToolCall("shell", {"cmd": "id"}, output="uid=0")],
    )

    single = parser._create_single_turn_sample(trace)
    assert single.user_input == "Run assessment"
    assert "Tool [shell]" in single.response

    topics = await parser._generate_reference_topics_from_trace(
        ParsedTrace("t", "Trace", "", [], [])
    )
    assert topics == ["cybersecurity assessment"]

    parser_no_generate = TraceParser(llm=SimpleNamespace())
    topics = await parser_no_generate._generate_reference_topics_from_trace(
        ParsedTrace("t", "Trace", "Assess API", [], [])
    )
    assert topics == ["Assess API"]

    multi_trace = ParsedTrace(
        trace_id="m",
        trace_name="Multi",
        objective="Assess auth",
        messages=[ParsedMessage("user", "Objective: Assess auth", metadata={"source": "objective"})],
        tool_calls=[
            ParsedToolCall("shell", {"cmd": "id"}, output="uid=0"),
            ParsedToolCall("http_request", {"url": "/"}, output="HTTP 200"),
        ],
    )
    multi = await parser._create_multi_turn_sample(multi_trace)
    assert multi.reference_topics == ["Assess auth"]
    assert len(multi.user_input) >= 3


@pytest.mark.asyncio
async def test_topic_generation_reports_progress_before_llm_call(monkeypatch):
    calls = []

    async def generate(_self, **_kwargs):
        calls.append("generate")
        return SimpleNamespace(topics=["authentication", "authorization"])

    monkeypatch.setattr("ragas.prompt.PydanticPrompt.generate", generate)
    parser = TraceParser(
        llm=SimpleNamespace(generate=object()),
        progress_callback=lambda kind, status="started": calls.append(f"{kind}:{status}"),
    )

    topics = await parser._generate_reference_topics_from_trace(
        ParsedTrace("t", "Trace", "Assess auth", [], [])
    )

    assert topics == ["authentication", "authorization"]
    assert calls == ["reference_topics:started", "generate", "reference_topics:completed"]


@pytest.mark.asyncio
async def test_topic_generation_ignores_progress_callback_failure(monkeypatch):
    async def generate(_self, **_kwargs):
        return SimpleNamespace(topics=["network security"])

    def fail_progress(_kind):
        raise RuntimeError("event stream disconnected")

    monkeypatch.setattr("ragas.prompt.PydanticPrompt.generate", generate)
    parser = TraceParser(
        llm=SimpleNamespace(generate=object()),
        progress_callback=fail_progress,
    )

    topics = await parser._generate_reference_topics_from_trace(
        ParsedTrace("t", "Trace", "Assess network", [], [])
    )

    assert topics == ["network security"]
