import json
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest

from modules.handlers.react import agent_event_handler as rb
from modules.handlers.react.agent_event_handler import OperationEventCoordinator, AgentEventHandler


def make_handler():
    handler = AgentEventHandler.__new__(AgentEventHandler)
    events = []
    handler._events = events
    handler.emit_ui_event = lambda event: events.append(event)
    handler._state_lock = threading.RLock()
    handler._emit_lock = threading.RLock()
    handler.operation_id = "OP_TEST"
    handler.coordinator = OperationEventCoordinator(operation_id="unittest", emitter=MagicMock())
    handler.emit_operation_init = True
    handler.start_metrics_thread = False
    # Budget-only model
    handler.budget_max_duration = 5
    handler.budget_max_tokens = None
    handler.budget_max_cost = None
    handler.start_time = time.time() - 65
    handler.provider_id = "litellm"
    handler.model_id = "model"
    handler.specialist_model_id = "specialist-model"
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
    handler.action_count = 0
    handler._reasoning_required_for_current_action = False
    handler.pending_action_header = False
    handler._reasoning_action_header_emitted = False
    handler._any_action_header_emitted = False
    handler._reasoning_emitted_since_last_action_header = False
    handler._stop_tool_used = False
    handler._budget_limit_reached = False
    handler._budget_limit_reason = None
    handler._report_generated = False
    handler._termination_emitted = False
    handler._termination_reason = None
    handler._python_preview_emitted = set()
    handler.tool_emitter = SimpleNamespace(
        emit_tool_specific_events=lambda name, tool_input: events.append(
            {"type": "tool_specific", "tool_name": name, "tool_input": tool_input}
        )
    )
    handler._metrics_thread = None
    handler._stop_metrics = False
    handler._stop_metrics_event = threading.Event()
    handler._last_agent = None
    handler._metrics_lock = handler._state_lock
    handler._sdk_input_tokens = 0
    handler._sdk_output_tokens = 0
    handler._sdk_cache_read_tokens = 0
    handler._sdk_cache_write_tokens = 0
    handler._aggregate_input_tokens = 0
    handler._aggregate_output_tokens = 0
    handler._aggregate_cache_read_tokens = 0
    handler._aggregate_cache_write_tokens = 0
    handler._aggregate_cost = 0.0
    handler._agent_usage_cache = OrderedDict()
    handler._agent_usage_cache_size = 128
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

    progress_updates = [event for event in handler._events if event["type"] == "progress_update"]
    assert progress_updates
    assert progress_updates[0]["step"] == 1
    assert progress_updates[0]["progressPercent"] == handler.get_budget_progress()
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
        "modules.handlers.react.agent_event_handler.get_buffered_output",
        lambda: "\n".join(str(i) for i in range(12)),
    )
    monkeypatch.setattr(
        "modules.handlers.react.agent_event_handler.get_buffered_error_output",
        lambda: "warning\ntrace",
    )
    handler._process_tool_result_from_message(
        {"toolUseId": "py", "status": "success", "content": []}
    )

    outputs = [event for event in handler._events if event["type"] in {"output", "tool_output"}]
    assert any(event.get("metadata", {}).get("preview") for event in outputs)
    assert any(event.get("tool") == "python_repl" for event in outputs)


def test_constructor_adds_generic_agent_metadata_and_alias(monkeypatch):
    events = []
    monkeypatch.setattr(rb, "get_models_client", lambda: SimpleNamespace())
    monkeypatch.setattr(AgentEventHandler, "_start_metrics_thread", lambda self: None)
    emitter = SimpleNamespace(emit=lambda event: events.append(event))

    coordinator = OperationEventCoordinator("OP_AGENT", emitter)
    handler = AgentEventHandler(
        operation_id="OP_AGENT",
        provider_id="litellm",
        model_id="model",
        emitter=emitter,
        coordinator=coordinator,
        agent_name="validation_specialist",
        agent_type="validation_specialist",
        parent_agent_run_id="main-1",
        init_context={"budget": {"maxDurationMinutes": 60}},
    )

    assert AgentEventHandler is AgentEventHandler
    assert handler.agent_name == "validation_specialist"
    assert handler.agent_type == "validation_specialist"
    assert handler.parent_agent_run_id == "main-1"
    assert any(event["type"] == "operation_init" and event["agent_name"] == "validation_specialist" for event in events)


def test_generic_tool_announcement_and_result_paths(monkeypatch):
    handler = make_handler()
    handler._process_tool_announcement({"name": "shell", "id": "s1", "input": {}})
    handler._process_tool_announcement({"name": "shell", "id": "s1", "input": {"cmd": "id"}})
    handler.reasoning_buffer = ["Tool finished, found output."]
    handler.tool_name_buffer["s1"] = "shell"
    handler.tool_input_buffer["s1"] = {"cmd": "id"}
    monkeypatch.setattr("modules.handlers.react.agent_event_handler.get_buffered_output", lambda: "")
    handler._process_tool_result_from_message(
        {"toolUseId": "s1", "status": "success", "content": [{"text": "uid=1"}]}
    )

    assert "tool_start" in event_types(handler)
    assert "reasoning" in event_types(handler)


def test_operation_coordinator_aggregates_multiple_handlers(monkeypatch):
    events = []
    emitter = SimpleNamespace(emit=lambda event: events.append(event))
    coordinator = OperationEventCoordinator("OP_MULTI", emitter)

    monkeypatch.setattr(rb, "get_models_client", lambda: SimpleNamespace())
    monkeypatch.setattr(AgentEventHandler, "_start_metrics_thread", lambda self: None)

    main = AgentEventHandler(
        operation_id="OP_MULTI",
        provider_id="litellm",
        model_id="model",
        emitter=emitter,
        coordinator=coordinator,
        agent_name="main",
        agent_type="main",
        init_context={"budget": {"maxDurationMinutes": 60}},
    )
    sub = AgentEventHandler(
        operation_id="OP_MULTI",
        provider_id="litellm",
        model_id="model",
        emitter=emitter,
        coordinator=coordinator,
        agent_name="sub",
        agent_type="validation_specialist",
        parent_agent_run_id=main.agent_run_id,
        emit_operation_init=False,
        start_metrics_thread=False,
    )

    main.process_metrics(SimpleNamespace(accumulated_usage={"inputTokens": 10, "outputTokens": 5}))
    sub.process_metrics(SimpleNamespace(accumulated_usage={"inputTokens": 7, "outputTokens": 3}))
    main._emit_estimated_metrics(force=True)

    metrics_events = [event for event in events if event["type"] == "metrics_update"]
    assert metrics_events[-1]["metrics"]["inputTokens"] == 17
    assert metrics_events[-1]["metrics"]["outputTokens"] == 8
    assert sub.parent_agent_run_id == main.agent_run_id


def test_constructor_emits_init_and_metrics(monkeypatch):
    events = []
    monkeypatch.setattr(rb, "get_models_client", lambda: SimpleNamespace())
    monkeypatch.setattr(AgentEventHandler, "_start_metrics_thread", lambda self: None)
    emitter = SimpleNamespace(emit=lambda event: events.append(event))

    handler = AgentEventHandler(
        operation_id="OP_INIT",
        provider_id="ollama",
        model_id="ollama/llama3",
        emitter=emitter,
        init_context={
            "target": "example.com",
            "memory": {"backend": "custom"},
            "budget": {"maxDurationMinutes": 60, "maxTokens": None, "maxCost": None},
        },
    )

    assert handler.operation_id == "OP_INIT"
    assert any(event["type"] == "operation_init" and event["memory"]["backend"] == "custom" for event in events)
    assert any(event["type"] == "thinking" for event in events)


def test_budget_minutes_progress_and_internal_step_tracking(monkeypatch):
    handler = make_handler()
    handler.budget_max_duration = 1
    handler.budget_max_tokens = 200
    handler.budget_max_cost = 0.0001
    handler.start_time = time.time() - 30
    handler.sdk_input_tokens = 40
    handler.sdk_output_tokens = 10

    progress, percent = handler._calculate_budget_progress(total_tokens=50, cost=0.000025)

    assert progress == pytest.approx(0.5)
    assert percent == 50

    handler.action_count = 7
    handler._record_action_boundary()
    progress_event = [event for event in handler._events if event["type"] == "progress_update"][-1]
    assert progress_event["step"] == 7
    assert progress_event["progressPercent"] == handler.get_budget_progress()


def test_metrics_aggregate_multiple_agents_with_lru_eviction(monkeypatch):
    handler = make_handler()
    handler._agent_usage_cache_size = 1
    handler.models_client = SimpleNamespace(
        get_pricing=lambda model_id: {
            "model-a": SimpleNamespace(input=10.0, output=20.0, cache_read=1.0, cache_write=2.0),
            "model-b": SimpleNamespace(input=1.0, output=2.0, cache_read=0.1, cache_write=0.2),
        }[model_id]
    )
    agent_models = {}

    def fake_model_id(agent):
        return agent_models[id(agent)]

    monkeypatch.setattr(rb, "get_provider_from_agent", lambda _agent: "litellm")
    monkeypatch.setattr(rb, "get_model_id_from_agent", fake_model_id)

    agent_a = SimpleNamespace(event_loop_metrics=None)
    agent_b = SimpleNamespace(event_loop_metrics=None)
    agent_models[id(agent_a)] = "model-a"
    agent_models[id(agent_b)] = "model-b"

    handler.process_metrics(
        SimpleNamespace(accumulated_usage={"inputTokens": 1_000_000, "outputTokens": 10, "cacheReadInputTokens": 0}),
        agent=agent_a,
    )
    agent_a_uuid = getattr(agent_a, rb._AGENT_USAGE_UUID_ATTR)
    handler.process_metrics(
        SimpleNamespace(accumulated_usage={"inputTokens": 20, "outputTokens": 1_000_000, "cacheWriteInputTokens": 0}),
        agent=agent_b,
    )

    assert getattr(agent_b, rb._AGENT_USAGE_UUID_ATTR) != agent_a_uuid
    assert len(handler._agent_usage_cache) == 1
    assert handler._aggregate_input_tokens == 1_000_000
    assert handler.sdk_input_tokens == 1_000_020
    assert handler.sdk_output_tokens == 1_000_010
    assert handler._compute_total_cost_from_usage() == pytest.approx(12.00022)


def test_metrics_without_agent_stays_in_legacy_usage_path():
    handler = make_handler()

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

    assert handler._agent_usage_cache == OrderedDict()
    assert handler.sdk_input_tokens == 100
    assert handler.sdk_output_tokens == 50
    assert handler._compute_total_cost_from_usage() == pytest.approx(0.000205)


def test_concurrent_agent_metrics_capture_is_thread_safe():
    handler = make_handler()
    handler._agent_usage_cache_size = 4
    agents = [SimpleNamespace(event_loop_metrics=None) for _ in range(20)]
    barrier = threading.Barrier(len(agents))

    def capture(index, agent):
        barrier.wait(timeout=5)
        handler.process_metrics(
            SimpleNamespace(
                accumulated_usage={
                    "inputTokens": index + 1,
                    "outputTokens": (index + 1) * 2,
                    "cacheReadInputTokens": 1,
                    "cacheWriteInputTokens": 2,
                }
            ),
            agent=agent,
        )

    threads = [threading.Thread(target=capture, args=(index, agent)) for index, agent in enumerate(agents)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len({getattr(agent, rb._AGENT_USAGE_UUID_ATTR) for agent in agents}) == len(agents)
    assert len(handler._agent_usage_cache) == 4
    assert handler.sdk_input_tokens == sum(range(1, 21))
    assert handler.sdk_output_tokens == sum(value * 2 for value in range(1, 21))
    assert handler.sdk_cache_read_tokens == 20
    assert handler.sdk_cache_write_tokens == 40


def test_concurrent_termination_emits_once():
    handler = make_handler()

    def terminate():
        handler.emit_termination("budget_limit", "done")

    threads = [threading.Thread(target=terminate) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert event_types(handler).count("termination_reason") == 1
    assert handler.termination_reason == "budget_limit"


def test_internal_step_and_event_defensive_paths(monkeypatch):
    handler = make_handler()

    handler.emitter = SimpleNamespace(emit=Mock(side_effect=BrokenPipeError()))
    handler.emit_ui_event({"type": "progress_update"})
    handler.emitter = SimpleNamespace(emit=Mock(side_effect=RuntimeError("emit failed")))
    handler.emit_ui_event({"type": "progress_update"})

    handler._termination_emitted = False
    handler._emit_accumulated_reasoning = Mock(side_effect=RuntimeError("flush failed"))
    calls = []

    def emit_with_thinking_failure(event):
        calls.append(event)
        if event["type"] == "thinking_end":
            raise RuntimeError("thinking failed")

    handler.emit_ui_event = emit_with_thinking_failure
    handler.emit_termination("budget_limit", "done")

    assert handler._termination_emitted is True
    assert any(event["type"] == "termination_reason" for event in calls)


def test_constructor_defaults_invalid_budget_values(monkeypatch):
    events = []
    monkeypatch.setattr(rb, "get_models_client", Mock(side_effect=RuntimeError("models unavailable")))
    monkeypatch.setattr(AgentEventHandler, "_start_metrics_thread", lambda self: None)
    emitter = SimpleNamespace(emit=lambda event: events.append(event))

    handler = AgentEventHandler(
        operation_id="OP_BAD_BUDGET",
        provider_id="litellm",
        model_id="model",
        emitter=emitter,
        init_context={
            "budget": {
                "maxDurationMinutes": "not-a-number",
                "maxTokens": "not-a-number",
                "maxCost": "not-a-number",
            }
        },
    )

    assert handler.budget_max_duration == 0
    assert handler.budget_max_tokens is None
    assert handler.budget_max_cost is None
    assert any(event["type"] == "metrics_update" for event in events)


def test_constructor_budget_context_and_memory_fallback_branches(monkeypatch):
    events = []
    monkeypatch.setattr(rb, "get_models_client", lambda: SimpleNamespace())
    monkeypatch.setattr(AgentEventHandler, "_start_metrics_thread", lambda self: None)
    emitter = SimpleNamespace(emit=lambda event: events.append(event))

    class BadContext(dict):
        def get(self, *_args, **_kwargs):
            raise RuntimeError("bad context")

    handler = AgentEventHandler(
        operation_id="OP_BAD_CONTEXT",
        provider_id="litellm",
        model_id="model",
        emitter=emitter,
        init_context=BadContext(),
    )
    assert handler.budget_max_duration == 60

    events.clear()
    monkeypatch.setenv("MEM0_API_KEY", "token")
    AgentEventHandler(
        operation_id="OP_MEM0",
        provider_id="litellm",
        model_id="model",
        emitter=emitter,
        init_context={},
    )
    assert any(event.get("memory", {}).get("backend") == "mem0_cloud" for event in events)

    events.clear()
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    monkeypatch.setenv("OPENSEARCH_HOST", "https://opensearch.example")
    AgentEventHandler(
        operation_id="OP_OPENSEARCH",
        provider_id="litellm",
        model_id="model",
        emitter=emitter,
        init_context={},
    )
    assert any(event.get("memory", {}).get("backend") == "opensearch" for event in events)


def test_generate_final_report_skip_and_success(monkeypatch, tmp_path):
    handler = make_handler()
    handler.operation_id = "OP_REPORT"
    handler.memory_ops = 0
    handler.evidence_count = 0
    handler.ensure_report_generated(
        agent=SimpleNamespace(model=type("OllamaThing", (), {})()),
        target="example.com",
        objective="assess",
        module="web",
    )
    assert any(event["type"] == "assessment_complete" and event["report_path"] is None for event in handler._events)

    handler = make_handler()
    handler.operation_id = "OP_REPORT"
    handler.memory_ops = 2
    handler.evidence_count = 1
    handler.tool_counts = {"shell": 2, "http_request": 1}
    handler.emitter = SimpleNamespace(flush_immediate=lambda: handler._events.append({"type": "flushed"}))

    output_dir = tmp_path / "example.com" / "OP_REPORT"
    monkeypatch.setattr(rb, "get_output_path", lambda *_args: str(output_dir))
    monkeypatch.setattr(rb, "sanitize_target_name", lambda value: value.replace(".", "_"))

    import modules.handlers.report_generator as report_generator

    def fake_generate_security_report(**kwargs):
        assert kwargs["config_params"]["tools_used"].count("shell") == 2
        with open(kwargs["filename"], "w", encoding="utf-8") as report:
            report.write("# Report\nConfirmed finding")

    monkeypatch.setattr(report_generator, "generate_security_report", fake_generate_security_report)
    monkeypatch.setattr(
        "modules.config.manager.get_config_manager",
        lambda: SimpleNamespace(get_llm_config=lambda _provider: SimpleNamespace(model_id="report-model")),
    )

    handler.ensure_report_generated(
        agent=SimpleNamespace(model=type("LiteLLMThing", (), {})()),
        target="example.com",
        objective="assess",
        module="web",
    )

    types = event_types(handler)
    assert "report_content" in types
    assert "assessment_complete" in types
    assert output_dir.joinpath("security_assessment_report.md").exists()


def test_generate_final_report_error_and_evaluation_paths(monkeypatch):
    handler = make_handler()
    handler.memory_ops = 1
    monkeypatch.setattr(rb, "get_output_path", Mock(side_effect=RuntimeError("path error")))
    handler.ensure_report_generated(SimpleNamespace(model=SimpleNamespace()), "target", "obj", "web")
    assert "error" in event_types(handler)

    handler = make_handler()
    monkeypatch.delenv("ENABLE_OBSERVABILITY", raising=False)
    handler.trigger_evaluation_on_completion()
    assert "evaluation_complete" not in event_types(handler)

    handler = make_handler()
    handler.emitter = SimpleNamespace(emit=lambda _event: None)
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("ENABLE_AUTO_EVALUATION", "true")
    monkeypatch.setenv("VERBOSE", "true")

    class FakeEvaluationManager:
        def __init__(self, operation_id, emitter):
            self.operation_id = operation_id
            self.emitter = emitter

        def register_trace(self, **kwargs):
            self.trace = kwargs

        async def evaluate_all_traces(self):
            return [{"score": 1}]

    monkeypatch.setattr("modules.evaluation.manager.EvaluationManager", FakeEvaluationManager)
    handler.trigger_evaluation_on_completion()
    assert "evaluation_complete" in event_types(handler)
    handler.wait_for_evaluation_completion(timeout=1)
    # Budget-based stop check: simulate duration exceeded
    handler.budget_max_duration = 1
    handler.start_time = time.time() - 61
    assert handler.should_stop() is True
    assert handler.has_reached_limit() is True
    summary = handler.get_summary()
    assert isinstance(summary.get("duration"), str)


def test_transform_sdk_event_alternate_payloads_and_streaming_updates(monkeypatch):
    handler = make_handler()
    monkeypatch.setattr(rb, "get_buffered_output", lambda: "testphp.vulnweb.com output")
    monkeypatch.setattr(rb, "get_buffered_error_output", lambda: "")

    metrics = SimpleNamespace(
        accumulated_usage={
            "inputTokens": 12,
            "outputTokens": 7,
            "cacheReadInputTokens": 3,
            "cacheWriteInputTokens": 2,
        }
    )
    agent = SimpleNamespace(event_loop_metrics=metrics)

    handler._transform_sdk_event(
        {
            "reasoningText": "Need to test auth",
            "data": "ignored because reasoningText wins",
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "pre tool rationale"},
                    {"type": "tool_use", "id": "a1", "name": "shell", "input": {"cmd": "id"}},
                    {"type": "tool_result", "toolUseId": "a1", "status": "success", "content": [{"text": "uid=1"}]},
                    {"toolResponse": {"toolUseId": "missing", "content": [{"text": "ignored"}]}},
                ],
            },
            "current_tool_use": {"name": "handoff_to_agent", "id": "h2", "input": {"value": '{"handoff_to": "web"'}},
            "toolResult": {"toolUseId": "h2", "status": "success", "content": [{"text": "partial handoff"}]},
            "tool_result": {"toolUseId": "a1", "status": "success", "content": [{"text": "duplicate skipped"}]},
            "output": "alternate output",
            "complete": True,
            "error": "MaxTokensReached",
            "event_loop_metrics": metrics,
            "agent": agent,
        }
    )

    assert handler.sdk_input_tokens == 12
    assert handler.sdk_output_tokens == 7
    assert "error" in event_types(handler)
    assert "operation_complete" in event_types(handler)

    handler._process_tool_announcement(
        {"name": "handoff_to_agent", "id": "h2", "input": {"handoff_to": "web", "message": "go"}}
    )
    assert "tool_start" in event_types(handler)

    handler._transform_sdk_event(
        {
            "data": "streaming thought",
            "complete": False,
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "agent should explain"},
                    {"toolUse": {"name": "advanced_payload_coordinator", "toolUseId": "apc", "input": {"target": "x"}}},
                    {"text": "trailing rationale"},
                ],
            },
            "response": {"toolUseId": "apc", "status": "success", "content": [{"text": "done"}]},
        }
    )
    tool_start_events = [event for event in handler._events if event.get("type") == "tool_start"]
    assert any(event.get("tool_name") == "advanced_payload_coordinator" for event in tool_start_events)
    assert all("synthetic" not in event for event in tool_start_events)
    assert all(not key.startswith("sw" + "arm_") for event in tool_start_events for key in event)

    handler = make_handler()
    # No step limit anymore; ensure processing works without raising
    handler._process_tool_announcement({"name": "shell", "id": "limit", "input": {"cmd": "id"}})
