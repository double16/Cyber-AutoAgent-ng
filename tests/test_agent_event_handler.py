import json
import math
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from modules.handlers.react import agent_event_handler as rb
from modules.handlers.react.agent_event_handler import (
    AgentEventHandler,
    OperationEventCoordinator,
    ReportBudgetEstimate,
)


def make_handler():
    handler = AgentEventHandler.__new__(AgentEventHandler)
    events = []
    handler._events = events
    handler.emit_ui_event = lambda event: events.append(event)
    handler.emitter = MagicMock()
    handler._state_lock = threading.RLock()
    handler._emit_lock = threading.RLock()
    handler.operation_id = "OP_TEST"
    handler.agent_run_id = "test-agent-1"
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
    handler._budget_limit_reached = False
    handler._budget_limit_reason = None
    handler._report_generated = False
    handler._report_generation_active = False
    handler._evaluation_report_path = None
    handler._completed_report_path = None
    handler._assessment_completion_emitted = False
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
    handler._report_metrics_input_baseline = 0
    handler._report_metrics_output_baseline = 0
    handler._report_metrics_cache_read_baseline = 0
    handler._report_metrics_cache_write_baseline = 0
    handler.pricing_input = 1.0
    handler.pricing_output = 2.0
    handler.pricing_cache_read = 0.25
    handler.pricing_cache_write = 0.5
    handler._pricing_override_configured = False
    handler.models_client = None
    return handler


def event_types(handler):
    return [event["type"] for event in handler._events]


def test_report_budget_estimator_zero_evidence_and_pricing_fallback(monkeypatch):
    monkeypatch.setattr(rb, "get_config_manager", Mock(side_effect=RuntimeError("no config")))
    coordinator = OperationEventCoordinator("OP_EST", MagicMock())

    estimate = coordinator.report_budget_estimate(
        provider_id="litellm",
        model_id="test-model",
        models_client=None,
        pricing_fallback={"input": 1.0, "output": 2.0},
    )

    assert estimate.input_tokens == 5405
    assert estimate.output_tokens == 3105
    assert estimate.total_tokens == 8510
    assert estimate.cost == pytest.approx((5405 + (3105 * 2)) / 1_000_000)
    assert estimate.findings == 0
    assert estimate.observations == 0
    assert estimate.remaining_steps == 2


def test_report_budget_estimator_pricing_override_precedes_model_pricing():
    coordinator = OperationEventCoordinator("OP_EST_OVERRIDE", MagicMock())
    models_client = SimpleNamespace(
        get_pricing=Mock(return_value=SimpleNamespace(input=10.0, output=20.0, cache_read=0.0, cache_write=0.0))
    )

    estimate = coordinator.report_budget_estimate(
        provider_id="litellm",
        model_id="test-model",
        models_client=models_client,
        pricing_fallback={"input": 1.0, "output": 2.0},
        pricing_override=True,
    )

    assert estimate.cost == pytest.approx((5405 + (3105 * 2)) / 1_000_000)
    models_client.get_pricing.assert_not_called()


def test_report_budget_estimator_categories_and_exact_progress(monkeypatch):
    monkeypatch.setattr(rb, "token_calc", lambda chars, model_id=None: int(chars))
    coordinator = OperationEventCoordinator("OP_EST_ITEMS", MagicMock())

    coordinator.record_memory(evidence=True, category="finding", content_length=100)
    coordinator.record_memory(evidence=True, category="observation", content_length=40)
    coordinator.record_memory(evidence=True, category="observation", severity="HIGH", content_length=25)

    estimate = coordinator.report_budget_estimate(
        provider_id="litellm",
        model_id="test-model",
        pricing_fallback={"input": 0.0, "output": 0.0},
    )

    assert estimate.findings == 1
    assert estimate.observations == 2
    assert estimate.input_tokens == math.ceil((2500 + 1900 + 1440 + 1425 + 2200) * 1.15)
    assert estimate.output_tokens == math.ceil((1500 + 1800 + 900 + 900 + 1200) * 1.15)
    assert estimate.remaining_steps == 5

    coordinator.set_report_items(
        [
            {"category": "finding", "severity": "MEDIUM", "content": "a" * 10},
            {"category": "discovery", "severity": "INFO", "content": "b" * 20},
        ],
        model_id="test-model",
    )
    coordinator.mark_report_step_started()
    tightened = coordinator.report_budget_estimate(provider_id="litellm", model_id="test-model")

    assert tightened.findings == 1
    assert tightened.observations == 1
    assert tightened.remaining_steps == 3
    assert tightened.input_tokens == math.ceil((1810 + 1420 + 2200) * 1.15)


def test_reasoning_termination_metrics_and_basic_helpers():
    handler = make_handler()
    handler._stop_metrics_thread = Mock()

    handler._handle_reasoning("I should inspect the headers.")
    handler._handle_reasoning("I should inspect the headers.")
    handler._emit_accumulated_reasoning(force=True)
    handler.emit_termination("complete", "done")
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

    handler._stop_metrics_thread.assert_not_called()
    assert event_types(handler).count("termination_reason") == 1
    assert handler.termination_message == "done"
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
    handler.tool_name_buffer["task"] = "create_tasks"
    handler._process_tool_result_from_message(
        {
            "toolUseId": "task",
            "status": "success",
            "content": [{"text": f"<active_task>{json.dumps(task_payload)}</active_task>"}],
        }
    )

    handler.tool_name_buffer["mem"] = "store_finding"
    handler.tool_input_buffer["mem"] = {"claim": "finding", "severity": "HIGH"}
    handler._process_tool_result_from_message(
        {"toolUseId": "mem", "status": "success", "content": [{"text": "stored"}]}
    )

    types = event_types(handler)
    assert "error" in types
    assert "task_done" not in types
    assert "task_started" not in types
    assert handler.memory_ops == 1
    assert handler.evidence_count == 1
    assert handler.coordinator.report_findings == 1


def test_shell_help_text_with_timeout_option_is_not_reported_as_timeout():
    handler = make_handler()
    handler.tool_name_buffer["help"] = "shell"

    handler._process_tool_result_from_message(
        {
            "toolUseId": "help",
            "status": "error",
            "content": [
                {
                    "text": (
                        "Command: scanner --help\nStatus: error\nExit Code: 1\n"
                        "Output: --timeout request timeout\nError: missing required input"
                    )
                }
            ],
        }
    )

    timeout_events = [
        event
        for event in handler._events
        if event["type"] == "error" and event.get("metadata", {}).get("type") == "timeout"
    ]
    assert timeout_events == []


@pytest.mark.parametrize(
    "tool_name",
    ["create_tasks"],
)
def test_task_state_tool_results_do_not_emit_task_lifecycle_events(tool_name):
    handler = make_handler()
    tool_use_id = f"{tool_name}-1"
    handler.tool_name_buffer[tool_use_id] = tool_name
    task_payload = {
        "closed": {"task_uid": "closed-1", "title": "Closed task", "status": "done"},
        "task": {"task_uid": "active-1", "title": "Active task", "status": "active"},
    }

    handler._process_tool_result_from_message(
        {
            "toolUseId": tool_use_id,
            "status": "success",
            "content": [{"text": f"<active_task>{json.dumps(task_payload)}</active_task>"}],
        }
    )

    task_done_events = [event for event in handler._events if event["type"] == "task_done"]
    task_started_events = [event for event in handler._events if event["type"] == "task_started"]

    assert task_done_events == []
    assert task_started_events == []


def test_non_task_state_tool_result_does_not_emit_task_lifecycle_events():
    handler = make_handler()
    handler.tool_name_buffer["shell-1"] = "shell"
    task_payload = {
        "closed": {"task_uid": "closed-1", "title": "Closed task", "status": "done"},
        "task": {"task_uid": "active-1", "title": "Active task", "status": "active"},
    }

    handler._process_tool_result_from_message(
        {
            "toolUseId": "shell-1",
            "status": "success",
            "content": [{"text": f"<active_task>{json.dumps(task_payload)}</active_task>"}],
        }
    )

    assert "task_done" not in event_types(handler)
    assert "task_started" not in event_types(handler)


def test_store_observation_success_updates_report_estimate_without_memory_reads(monkeypatch):
    handler = make_handler()
    monkeypatch.setattr(rb, "token_calc", lambda chars, model_id=None: int(chars))

    handler.tool_name_buffer["high_obs"] = "store_observation"
    handler.tool_input_buffer["high_obs"] = {
        "content": "x" * 37,
        "metadata": {"category": "observation", "severity": "HIGH"},
    }
    handler._process_tool_result_from_message(
        {"toolUseId": "high_obs", "status": "success", "content": [{"text": "stored"}]}
    )

    assert handler.memory_ops == 1
    assert handler.evidence_count == 1
    assert handler.coordinator.report_findings == 0
    assert handler.coordinator.report_observations == 1
    assert handler.coordinator.report_observation_content_tokens == 37


@pytest.mark.parametrize(
    ("tool_name", "tool_input"),
    [
        ("store_knowledge", {"content": "Use a negative control"}),
        (
            "record_finding_validation",
            {"finding_uid": "finding-1", "summary": "Direct evidence reproduced"},
        ),
    ],
)
def test_non_evidence_memory_tools_increment_only_memory_operations(tool_name, tool_input):
    handler = make_handler()
    handler.tool_name_buffer["memory"] = tool_name
    handler.tool_input_buffer["memory"] = tool_input

    handler._process_tool_result_from_message(
        {"toolUseId": "memory", "status": "success", "content": [{"text": "stored"}]}
    )

    assert handler.memory_ops == 1
    assert handler.evidence_count == 0
    assert handler.coordinator.memory_ops == 1
    assert handler.coordinator.evidence_count == 0
    assert handler.coordinator.report_findings == 0
    assert handler.coordinator.report_observations == 0


def test_failed_typed_memory_tool_does_not_increment_metrics():
    handler = make_handler()
    handler.tool_name_buffer["memory"] = "record_finding_validation"
    handler.tool_input_buffer["memory"] = {"finding_uid": "missing", "summary": "failed"}

    handler._process_tool_result_from_message(
        {"toolUseId": "memory", "status": "error", "content": [{"text": "unknown finding"}]}
    )

    assert handler.memory_ops == 0
    assert handler.evidence_count == 0
    assert handler.coordinator.memory_ops == 0


def test_tool_completion_preserves_execution_outcome_metadata():
    handler = make_handler()
    handler.tool_name_buffer["blocked"] = "shell"

    handler._process_tool_result_from_message(
        {
            "toolUseId": "blocked",
            "status": "error",
            "content": [{"text": "blocked by recovery"}],
            "_cyber_outcome": "blocked",
            "_cyber_executed": False,
        }
    )

    invocation_end = [event for event in handler._events if event["type"] == "tool_invocation_end"][-1]
    tool_end = [event for event in handler._events if event["type"] == "tool_end"][-1]
    assert invocation_end["success"] is False
    assert invocation_end["outcome"] == "blocked"
    assert invocation_end["executed"] is False
    assert tool_end["outcome"] == "blocked"
    assert tool_end["executed"] is False


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


def test_callback_events_use_agent_attached_metadata(monkeypatch):
    events = []
    monkeypatch.setattr(rb, "get_models_client", lambda: SimpleNamespace())
    monkeypatch.setattr(AgentEventHandler, "_start_metrics_thread", lambda self: None)
    emitter = SimpleNamespace(emit=lambda event: events.append(event))
    handler = AgentEventHandler(
        operation_id="OP_AGENT_META",
        provider_id="litellm",
        model_id="model",
        emitter=emitter,
        agent_name="operation",
        agent_type="operation_controller",
        init_context={"budget": {"maxDurationMinutes": 60}},
    )
    agent = SimpleNamespace(
        _cyber_agent_type="task_executor",
        _cyber_agent_name="Cyber-AutoAgent task_executor",
        _cyber_agent_run_id="task_executor-1",
        _cyber_parent_agent_run_id="operation-1",
    )

    handler(agent=agent, complete=True)

    complete_event = [event for event in events if event["type"] == "operation_complete"][-1]
    assert complete_event["agent_type"] == "task_executor"
    assert complete_event["agent_name"] == "Cyber-AutoAgent task_executor"
    assert complete_event["agent_run_id"] == "task_executor-1"
    assert complete_event["parent_agent_run_id"] == "operation-1"
    assert handler.agent_type == "operation_controller"


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


def test_sub_agent_progress_and_metrics_use_operation_aggregates(monkeypatch):
    events = []
    emitter = SimpleNamespace(emit=lambda event: events.append(event))
    coordinator = OperationEventCoordinator(
        "OP_SUB_PROGRESS",
        emitter,
        budget_max_duration=10,
        budget_max_tokens=1000,
        budget_max_cost=1.0,
    )
    coordinator.start_time = time.time() - 300

    monkeypatch.setattr(rb, "get_models_client", lambda: None)
    monkeypatch.setattr(AgentEventHandler, "_start_metrics_thread", lambda self: None)

    main = AgentEventHandler(
        operation_id="OP_SUB_PROGRESS",
        provider_id="litellm",
        model_id="model",
        emitter=emitter,
        coordinator=coordinator,
        agent_name="main",
        agent_type="main",
        init_context={"budget": {"maxDurationMinutes": 10, "maxTokens": 1000, "maxCost": 1.0}},
    )
    sub = AgentEventHandler(
        operation_id="OP_SUB_PROGRESS",
        provider_id="litellm",
        model_id="model",
        emitter=emitter,
        coordinator=coordinator,
        agent_name="validation_specialist",
        agent_type="validation_specialist",
        parent_agent_run_id=main.agent_run_id,
        emit_operation_init=False,
        start_metrics_thread=False,
    )
    sub.start_time = time.time()
    main.pricing_input = 1.0
    main.pricing_output = 2.0
    sub.pricing_input = 1.0
    sub.pricing_output = 2.0

    main.process_metrics(SimpleNamespace(accumulated_usage={"inputTokens": 100, "outputTokens": 50}))
    sub.process_metrics(SimpleNamespace(accumulated_usage={"inputTokens": 25, "outputTokens": 10}))

    sub.action_count = 1
    sub._record_action_boundary()
    sub._emit_estimated_metrics(force=True)

    progress_event = [event for event in events if event["type"] == "progress_update"][-1]
    metrics_event = [event for event in events if event["type"] == "metrics_update"][-1]

    assert progress_event["agent_name"] == "validation_specialist"
    assert progress_event["duration"] == "5m 0s"
    assert progress_event["progressPercent"] == 869
    assert metrics_event["metrics"]["inputTokens"] == 125
    assert metrics_event["metrics"]["outputTokens"] == 60
    assert metrics_event["metrics"]["totalTokens"] == 185
    assert metrics_event["metrics"]["cost"] == pytest.approx(0.000245)
    assert metrics_event["metrics"]["duration"] == "5m 0s"
    assert metrics_event["metrics"]["progressPercent"] == 869
    assert metrics_event["metrics"]["reportEstimate"]["totalTokens"] == 8510


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


def test_budget_progress_and_stop_include_report_reservation_for_tokens_and_cost(monkeypatch):
    handler = make_handler()
    handler.budget_max_duration = 0
    handler.budget_max_tokens = 100
    handler.budget_max_cost = None
    handler.sdk_input_tokens = 45
    handler.sdk_output_tokens = 5
    monkeypatch.setattr(
        handler,
        "_report_budget_estimate",
        lambda: ReportBudgetEstimate(input_tokens=55, output_tokens=0, total_tokens=55, cost=0.0),
    )

    assert handler.get_budget_progress() == 105
    assert handler.should_stop() is True
    assert handler._budget_limit_reason == "tokens"

    handler = make_handler()
    handler.budget_max_duration = 0
    handler.budget_max_tokens = None
    handler.budget_max_cost = 0.001
    handler._aggregate_cost = 0.0004
    monkeypatch.setattr(
        handler,
        "_report_budget_estimate",
        lambda: ReportBudgetEstimate(input_tokens=0, output_tokens=0, total_tokens=0, cost=0.0007),
    )

    assert handler.get_budget_progress() == 110
    assert handler.should_stop() is True
    assert handler._budget_limit_reason == "cost"


def test_report_generation_active_disables_budget_stop_checks():
    handler = make_handler()
    handler.budget_max_duration = 1
    handler.budget_max_tokens = 1
    handler.budget_max_cost = 0.000001
    handler.start_time = time.time() - 120
    handler.sdk_input_tokens = 10
    handler._aggregate_cost = 1.0
    handler._budget_limit_reached = True
    handler._report_generation_active = True

    assert handler.should_stop() is False
    assert handler.has_reached_limit() is True


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


def test_compute_cost_without_agent_uses_handler_model_id():
    handler = make_handler()
    handler.model_id = "fallback-model"
    handler.provider_id = "litellm"
    handler.models_client = SimpleNamespace(
        get_pricing=lambda model_id: {
            "fallback-model": SimpleNamespace(input=10.0, output=20.0, cache_read=1.0, cache_write=2.0),
        }[model_id]
    )

    cost = handler._compute_cost_from_metrics(
        input_tokens=1_000_000,
        output_tokens=2_000_000,
        cache_read_tokens=3_000_000,
        cache_write_tokens=4_000_000,
    )

    assert cost == pytest.approx(61.0)


def test_compute_cost_pricing_env_override_precedes_model_pricing():
    handler = make_handler()
    handler.model_id = "fallback-model"
    handler.provider_id = "litellm"
    handler._pricing_override_configured = True
    handler.models_client = SimpleNamespace(
        get_pricing=Mock(return_value=SimpleNamespace(input=10.0, output=20.0, cache_read=1.0, cache_write=2.0))
    )

    cost = handler._compute_cost_from_metrics(
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
        cache_write_tokens=5,
    )

    assert cost == pytest.approx(0.000205)
    handler.models_client.get_pricing.assert_not_called()


def test_compute_cost_uses_evaluation_model_override_for_models_dev_pricing():
    handler = make_handler()
    pricing = SimpleNamespace(input=3.0, output=6.0, cache_read=0.5, cache_write=4.0)
    handler.models_client = SimpleNamespace(get_pricing=Mock(return_value=pricing))

    cost = handler._compute_cost_from_metrics(
        input_tokens=1_000,
        output_tokens=500,
        cache_read_tokens=800,
        cache_write_tokens=200,
        provider_override="litellm",
        model_id_override="provider/evaluation-model",
    )

    assert cost == pytest.approx(0.0072)
    handler.models_client.get_pricing.assert_called_once_with("provider/evaluation-model")


def test_compute_cost_without_agent_skips_model_pricing_for_ollama():
    handler = make_handler()
    handler.model_id = "local-model"
    handler.provider_id = "ollama"
    handler.models_client = SimpleNamespace(get_pricing=Mock(side_effect=AssertionError("pricing should not be used")))

    cost = handler._compute_cost_from_metrics(
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
        cache_write_tokens=5,
    )

    assert cost == pytest.approx(0.000205)


def test_report_metrics_without_agent_accumulate_across_reset_steps():
    handler = make_handler()
    handler.sdk_input_tokens = 1_000
    handler.sdk_output_tokens = 100
    handler._report_generation_active = True

    handler.mark_report_step_started()
    handler.record_report_metrics(
        SimpleNamespace(accumulated_usage={"inputTokens": 300, "outputTokens": 50})
    )
    handler._emit_estimated_metrics(force=True)

    handler.mark_report_step_started()
    handler.record_report_metrics(
        SimpleNamespace(accumulated_usage={"inputTokens": 200, "outputTokens": 75})
    )
    handler._emit_estimated_metrics(force=True)

    metrics_events = [event for event in handler._events if event["type"] == "metrics_update"]
    assert [event["metrics"]["totalTokens"] for event in metrics_events[-2:]] == [1_450, 1_725]
    assert metrics_events[-1]["metrics"]["inputTokens"] == 1_500
    assert metrics_events[-1]["metrics"]["outputTokens"] == 225


def test_report_metrics_without_agent_ignore_within_step_decrease():
    handler = make_handler()
    handler.sdk_input_tokens = 1_000
    handler.sdk_output_tokens = 100
    handler._report_generation_active = True

    handler.mark_report_step_started()
    handler.record_report_metrics(
        SimpleNamespace(accumulated_usage={"inputTokens": 300, "outputTokens": 80})
    )
    handler.record_report_metrics(
        SimpleNamespace(accumulated_usage={"inputTokens": 290, "outputTokens": 70})
    )
    handler.record_report_metrics(
        SimpleNamespace(accumulated_usage={"inputTokens": 330, "outputTokens": 90})
    )

    assert handler.sdk_input_tokens == 1_330
    assert handler.sdk_output_tokens == 190
    assert handler._aggregate_input_tokens == 330
    assert handler._aggregate_output_tokens == 90


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


def test_child_termination_is_agent_scoped_and_does_not_end_operation():
    handler = make_handler()
    handler.parent_agent_run_id = "operation-controller-1"

    handler.emit_termination("stalled", "No actions taken")

    assert event_types(handler) == ["agent_termination"]
    assert handler._events[0]["scope"] == "agent"
    assert handler.coordinator.termination_reason is None


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
    output_dir = tmp_path / "example.com" / "OP_REPORT"
    monkeypatch.setattr(rb, "get_output_path", lambda *_args: str(output_dir))
    monkeypatch.setattr(rb, "sanitize_target_name", lambda value: value.replace(".", "_"))
    monkeypatch.setattr(
        "modules.config.manager.get_config_manager",
        lambda: SimpleNamespace(get_llm_config=lambda _provider: SimpleNamespace(model_id="report-model")),
    )

    import modules.handlers.report_generator as report_generator

    generated_calls = []

    def fake_empty_generate_security_report(**kwargs):
        generated_calls.append(kwargs)

    monkeypatch.setattr(report_generator, "generate_security_report", fake_empty_generate_security_report)

    handler = make_handler()
    handler.operation_id = "OP_REPORT"
    handler.memory_ops = 0
    handler.evidence_count = 0
    handler.ensure_report_generated(
        agent=None,
        target="example.com",
        objective="assess",
        module="web",
    )
    assert generated_calls
    assert generated_calls[0]["config_params"]["provider"] == "litellm"
    assert generated_calls[0]["config_params"]["completion_status"] is None
    assert "assessment_complete" not in event_types(handler)
    handler.emitter = SimpleNamespace()
    handler._stop_metrics_thread = Mock()
    handler.emit_assessment_complete()
    assert any(event["type"] == "assessment_complete" and event["report_path"] is None for event in handler._events)
    handler._stop_metrics_thread.assert_called_once_with()
    handler.emit_assessment_complete()
    assert event_types(handler).count("assessment_complete") == 1
    handler._stop_metrics_thread.assert_called_once_with()

    handler = make_handler()
    handler.operation_id = "OP_REPORT"
    handler.memory_ops = 2
    handler.evidence_count = 1
    handler.tool_counts = {"shell": 2, "http_request": 1}
    handler.emitter = SimpleNamespace(flush_immediate=lambda: handler._events.append({"type": "flushed"}))

    def fake_generate_security_report(**kwargs):
        assert kwargs["config_params"]["tools_used"].count("shell") == 2
        assert kwargs["config_params"]["completion_status"]["assessment_complete"] is False
        with open(kwargs["filename"], "w", encoding="utf-8") as report:
            report.write("# Report\nConfirmed finding")

    monkeypatch.setattr(report_generator, "generate_security_report", fake_generate_security_report)

    handler.ensure_report_generated(
        agent=SimpleNamespace(model=type("LiteLLMThing", (), {})()),
        target="example.com",
        objective="assess",
        module="web",
        completion_status={
            "assessment_complete": False,
            "workflow_complete": False,
            "termination_reason": "stalled",
            "termination_message": "No actions taken",
            "incomplete_reason": "Workflow stalled before assessment completion.",
        },
    )

    types = event_types(handler)
    assert "report_content" in types
    report_index = types.index("report_content")
    assert handler._events[report_index + 1]["type"] == "progress_update"
    assert handler._events[report_index + 1]["progressPercent"] == handler.get_budget_progress()
    assert "assessment_complete" not in types
    assert output_dir.joinpath("security_assessment_report.md").exists()
    handler.emit_assessment_complete()
    assert any(
        event["type"] == "assessment_complete"
        and event["report_path"] == str(output_dir / "security_assessment_report.md")
        for event in handler._events
    )


def test_assessment_complete_stops_metrics_when_event_emission_fails():
    handler = make_handler()
    handler.emit_ui_event = Mock(side_effect=RuntimeError("transport failed"))
    handler._stop_metrics_thread = Mock()

    with pytest.raises(RuntimeError, match="transport failed"):
        handler.emit_assessment_complete()

    handler._stop_metrics_thread.assert_called_once_with()


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
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("ENABLE_AUTO_EVALUATION", "false")
    manager = Mock(side_effect=AssertionError("evaluation manager must not be created"))
    monkeypatch.setattr("modules.evaluation.manager.EvaluationManager", manager)
    handler.trigger_evaluation_on_completion()
    manager.assert_not_called()

    handler = make_handler()
    handler.emitter = SimpleNamespace(emit=lambda _event: None)
    handler._pricing_override_configured = True
    handler.models_client = SimpleNamespace(
        get_pricing=Mock(side_effect=AssertionError("environment pricing must take precedence"))
    )
    monkeypatch.setenv("ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("ENABLE_AUTO_EVALUATION", "true")

    class FakeEvaluationManager:
        def __init__(
            self,
            operation_id,
            emitter,
            report_path=None,
            usage_callback=None,
            progress_callback=None,
        ):
            self.operation_id = operation_id
            self.emitter = emitter
            self.report_path = report_path
            self.usage_callback = usage_callback
            self.progress_callback = progress_callback

        def register_trace(self, **kwargs):
            self.trace = kwargs

        async def evaluate_all_traces(self):
            self.usage_callback(
                {
                    "modelId": "evaluation-model",
                    "providerId": "litellm",
                    "inputTokens": 10,
                    "outputTokens": 5,
                    "cacheReadTokens": 7,
                    "cacheWriteTokens": 3,
                }
            )
            return {"OP_TEST": {"operation/evidence_quality": 0.8, "operation/goal_accuracy": 0.6}}

    monkeypatch.setattr("modules.evaluation.manager.EvaluationManager", FakeEvaluationManager)
    handler.trigger_evaluation_on_completion()
    assert "evaluation_complete" in event_types(handler)
    evaluation_index = event_types(handler).index("evaluation_complete")
    evaluation_event = handler._events[evaluation_index]
    assert handler._events[evaluation_index + 1]["type"] == "progress_update"
    assert handler._events[evaluation_index + 1]["progressPercent"] == handler.get_budget_progress()
    assert evaluation_event["status"] == "completed"
    assert evaluation_event["metrics_evaluated"] == 2
    assert evaluation_event["average_score"] == pytest.approx(0.7)
    evaluation_metrics = [event for event in handler._events if event["type"] == "metrics_update"][-1]
    assert evaluation_metrics["metrics"]["inputTokens"] == 10
    assert evaluation_metrics["metrics"]["outputTokens"] == 5
    assert evaluation_metrics["metrics"]["cacheReadTokens"] == 7
    assert evaluation_metrics["metrics"]["cacheWriteTokens"] == 3
    assert evaluation_metrics["metrics"]["cost"] == pytest.approx(0.00002325)

    class NoResultsEvaluationManager(FakeEvaluationManager):
        async def evaluate_all_traces(self):
            return {}

    handler = make_handler()
    monkeypatch.setattr("modules.evaluation.manager.EvaluationManager", NoResultsEvaluationManager)
    handler.trigger_evaluation_on_completion()
    no_results_index = event_types(handler).index("evaluation_complete")
    no_results = handler._events[no_results_index]
    assert handler._events[no_results_index + 1]["type"] == "progress_update"
    assert no_results["status"] == "no_results"
    assert no_results["success"] is False

    class FailedEvaluationManager(FakeEvaluationManager):
        async def evaluate_all_traces(self):
            raise RuntimeError("provider unavailable")

    handler = make_handler()
    monkeypatch.setattr("modules.evaluation.manager.EvaluationManager", FailedEvaluationManager)
    handler.trigger_evaluation_on_completion()
    failed_index = event_types(handler).index("evaluation_complete")
    failed = handler._events[failed_index]
    assert handler._events[failed_index + 1]["type"] == "progress_update"
    assert failed["status"] == "failed"
    assert failed["message"] == "Evaluation failed; see logs for details"
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
