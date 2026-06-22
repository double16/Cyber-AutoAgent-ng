import json
import threading
import time
from types import SimpleNamespace

from modules.handlers.react.react_bridge_handler import ReactBridgeHandler


def make_handler():
    handler = ReactBridgeHandler.__new__(ReactBridgeHandler)
    events = []
    handler._events = events
    handler.emit_ui_event = lambda event: events.append(event)
    handler.operation_id = "OP_TEST"
    handler.current_step = 0
    handler.max_steps = 5
    handler.start_time = time.time() - 65
    handler.provider_id = "litellm"
    handler.model_id = "model"
    handler.swarm_model_id = "swarm-model"
    handler.memory_ops = 0
    handler.evidence_count = 0
    handler.tool_start_times = {}
    handler.announced_tools = set()
    handler.tool_input_buffer = {}
    handler.tool_name_buffer = {}
    handler.tools_used = set()
    handler.tool_counts = {}
    handler.tool_use_output_emitted = {}
    handler.tools_with_complete_input = set()
    handler.reasoning_buffer = []
    handler.last_reasoning_time = 0
    handler._last_reasoning_flush = 0
    handler._emitted_any_reasoning = False
    handler._recent_reasoning_by_agent = {}
    handler._recent_reasoning_ttl = 60
    handler._reasoning_required_for_current_step = False
    handler.pending_step_header = False
    handler._reasoning_step_header_emitted = False
    handler._any_step_header_emitted = False
    handler._reasoning_emitted_since_last_step_header = False
    handler._stop_tool_used = False
    handler._report_generated = False
    handler.in_swarm_operation = False
    handler.swarm_agents = []
    handler.current_swarm_agent = None
    handler.swarm_handoff_count = 0
    handler._last_swarm_signature = None
    handler._termination_emitted = False
    handler._termination_reason = None
    handler.swarm_agent_steps = {}
    handler._python_preview_emitted = set()
    handler.swarm_max_iterations = None
    handler.swarm_max_handoffs = None
    handler.swarm_iteration_count = 0
    handler.swarm_tool_id = None
    handler.swarm_agent_tools = {}
    handler.swarm_agent_details = []
    handler._tool_running_by_agent = {}
    handler._swarm_limit_announced = False
    handler._swarm_handoff_limit_announced = False
    handler.tool_emitter = SimpleNamespace(
        emit_tool_specific_events=lambda name, tool_input: events.append(
            {"type": "tool_specific", "tool_name": name, "tool_input": tool_input}
        )
    )
    handler._metrics_thread = None
    handler._stop_metrics = False
    handler._last_agent = None
    handler._metrics_lock = threading.RLock()
    handler._sdk_input_tokens = 0
    handler._sdk_output_tokens = 0
    handler._sdk_cache_read_tokens = 0
    handler._sdk_cache_write_tokens = 0
    handler.pricing_input = 1.0
    handler.pricing_output = 2.0
    handler.pricing_cache_read = 0.25
    handler.pricing_cache_write = 0.5
    handler.models_client = None
    return handler


def event_types(handler):
    return [event["type"] for event in handler._events]


def test_reasoning_termination_metrics_and_basic_helpers():
    handler = make_handler()

    handler._handle_reasoning("I should inspect the headers.")
    handler._handle_reasoning("I should inspect the headers.")
    handler._emit_accumulated_reasoning(force=True)
    handler.emit_termination("stop_tool", "done")
    handler.emit_termination("ignored", "ignored")
    handler.process_metrics(
        SimpleNamespace(
            accumulated_usage={
                "inputTokens": 100,
                "outputTokens": 50,
                "cacheReadInputTokens": 10,
                "cacheWriteInputTokens": 5,
            }
        )
    )
    handler._emit_estimated_metrics(force=True)
    handler._handle_completion()

    assert event_types(handler).count("termination_reason") == 1
    assert handler._format_duration(65) == "1m 5s"
    assert handler._extract_code_from_input({"code": "print(1)"}) == "print(1)"
    assert handler._extract_code_from_input({"value": [1, 2]}).startswith("{")
    assert handler._extract_output_text([{"json": {"a": 1}}, {"message": "m"}, "s"])
    assert handler._collapse_repeated_sentences("A. A. B.") == "A. B."


def test_tool_announcement_streaming_update_and_message_processing():
    handler = make_handler()

    handler._process_message(
        {
            "role": "assistant",
            "content": [
                {"text": "Planning"},
                {"toolUse": {"name": "shell", "toolUseId": "t1", "input": {"cmd": "id"}}},
                {"toolResult": {"toolUseId": "t1", "content": [{"text": "uid=1"}], "status": "success"}},
            ],
        }
    )

    handler._process_tool_announcement(
        {"name": "handoff_to_agent", "id": "h1", "input": {"value": '{"handoff_to": "recon"}'}}
    )
    assert "h1" in handler.announced_tools
    handler._process_tool_announcement(
        {
            "name": "handoff_to_agent",
            "id": "h1",
            "input": {"value": '{"handoff_to": "recon", "message": "continue"}'},
        }
    )

    assert "step_header" in event_types(handler)
    assert any(event["type"] == "tool_input_update" for event in handler._events) is False
    assert handler.tool_counts["shell"] == 1
    assert handler._parse_tool_input_from_stream({"value": '{"a": 1}'}) == {"a": 1}
    assert handler._parse_tool_input_from_stream("[1, 2]") == {"value": [1, 2]}
    assert handler._parse_tool_input_from_stream("plain") == {"value": "plain"}


def test_tool_result_success_error_task_stop_and_memory_paths():
    handler = make_handler()

    handler.tool_name_buffer["err"] = "shell"
    handler.tool_input_buffer["err"] = {"timeout": 30}
    handler.tool_start_times["err"] = time.time() - 1
    handler._process_tool_result_from_message(
        {
            "toolUseId": "err",
            "status": "error",
            "content": [{"text": "Command timed out after 30 seconds"}],
        }
    )

    task_payload = {
        "closed": {"task_uid": "old", "title": "Old task", "status": "done"},
        "task": {"task_uid": "new", "title": "New task", "status": "active"},
    }
    handler.tool_name_buffer["task"] = "get_active_task"
    handler._process_tool_result_from_message(
        {
            "toolUseId": "task",
            "status": "success",
            "content": [{"text": f"<active_task>{json.dumps(task_payload)}</active_task>"}],
        }
    )

    handler.tool_name_buffer["mem"] = "mem0_store"
    handler.tool_input_buffer["mem"] = {"metadata": {"category": "finding"}}
    handler._process_tool_result_from_message(
        {"toolUseId": "mem", "status": "success", "content": [{"text": "stored"}]}
    )

    handler.tool_name_buffer["stop"] = "stop"
    handler.tool_input_buffer["stop"] = {"reason": "operator requested stop"}
    handler._process_tool_result_from_message(
        {"toolUseId": "stop", "status": "success", "content": [{"text": "stopped"}]}
    )

    types = event_types(handler)
    assert "error" in types
    assert "task_done" in types
    assert "task_started" in types
    assert handler.memory_ops == 1
    assert handler.evidence_count == 1
    assert handler._stop_tool_used is True


def test_python_repl_preview_and_empty_result_paths(monkeypatch):
    handler = make_handler()
    handler.tool_name_buffer["py"] = "python_repl"
    handler.tool_input_buffer["py"] = {"code": "print('hello')"}

    monkeypatch.setattr(
        "modules.handlers.react.react_bridge_handler.get_buffered_output",
        lambda: "\n".join(str(i) for i in range(12)),
    )
    monkeypatch.setattr(
        "modules.handlers.react.react_bridge_handler.get_buffered_error_output",
        lambda: "warning\ntrace",
    )
    handler._process_tool_result_from_message(
        {"toolUseId": "py", "status": "success", "content": []}
    )

    outputs = [event for event in handler._events if event["type"] in {"output", "tool_output"}]
    assert any(event.get("metadata", {}).get("preview") for event in outputs)
    assert any(event.get("tool") == "python_repl" for event in outputs)


def test_swarm_start_handoff_output_parsing_completion_and_inference():
    handler = make_handler()
    swarm_input = {
        "task": "Assess target",
        "max_handoffs": 3,
        "max_iterations": 4,
        "agents": [
            {"name": "recon_agent", "system_prompt": "Recon", "tools": ["shell"], "model_settings": {"model_id": "m1", "params": {"temperature": 0.2}}},
            {"name": "web_agent", "system_prompt": "Web", "tools": ["http_request"]},
        ],
    }

    handler._track_swarm_start(swarm_input, "swarm-id")
    handler._track_swarm_start(swarm_input, "swarm-id")
    assert handler.in_swarm_operation is True
    assert handler.current_swarm_agent == "recon_agent"
    assert handler._infer_active_swarm_agent("shell") == "recon_agent"

    assert handler._detect_swarm_agent_from_callback({"agent": SimpleNamespace(name="Web Agent")}) == "web_agent"
    assert handler._detect_swarm_agent_from_callback({"message": {"metadata": {"agent_name": "recon_agent"}}}) == "recon_agent"

    handler._track_agent_handoff({"handoff_to": "web-agent", "message": "Use HTTP", "context": {"x": 1}})
    assert handler.current_swarm_agent == "web_agent"

    handler._parse_swarm_output_for_events(
        "**RECON_AGENT:**\nfound host\nrequires root privileges\n**WEB_AGENT:**\nchecking app"
    )
    assert handler._extract_swarm_reasoning_from_output("I need to scan\nthen should test") == "I need to scan then should test"

    handler.sdk_input_tokens = 100
    handler.sdk_output_tokens = 50
    handler.swarm_agent_steps = {"recon_agent": 2, "web_agent": 1}
    handler._track_swarm_complete()

    types = event_types(handler)
    assert types.count("swarm_start") == 1
    assert "swarm_handoff" in types
    assert "swarm_agent_active" in types
    assert "swarm_complete" in types
    assert handler.in_swarm_operation is False


def test_swarm_tool_announcement_and_result_paths(monkeypatch):
    handler = make_handler()
    handler._track_swarm_start(
        {
            "task": "Assess",
            "agents": [
                {"name": "recon_agent", "tools": ["shell"]},
                {"name": "web_agent", "tools": ["http_request"]},
            ],
        },
        "swarm",
    )
    handler._process_tool_announcement({"name": "shell", "id": "s1", "input": {}})
    handler._process_tool_announcement({"name": "shell", "id": "s1", "input": {"cmd": "id"}})
    handler.reasoning_buffer = ["Tool finished, found output."]
    handler.tool_name_buffer["s1"] = "shell"
    handler.tool_input_buffer["s1"] = {"cmd": "id"}
    monkeypatch.setattr("modules.handlers.react.react_bridge_handler.get_buffered_output", lambda: "")
    handler._process_tool_result_from_message(
        {"toolUseId": "s1", "status": "success", "content": [{"text": "uid=1"}]}
    )

    assert "tool_start" in event_types(handler)
    assert "reasoning" in event_types(handler)
