"""Tests for repeated identical tool-call detection and suppression."""

import asyncio
from types import SimpleNamespace

import pytest
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent
from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool

from modules.handlers.tool_repeat_guard import (
    DEFAULT_TOOL_REPEAT_MAX_CYCLE_LENGTH,
    DEFAULT_TOOL_REPEAT_THRESHOLD,
    REPEATED_TOOL_LOOP_STATE_KEY,
    ToolRepeatGuardHook,
    normalize_tool_repeat_max_cycle_length,
    normalize_tool_repeat_threshold,
)


class FakeTool(AgentTool):
    def __init__(self, name: str = "shell") -> None:
        super().__init__()
        self.name = name

    @property
    def tool_name(self):
        return self.name

    @property
    def tool_spec(self):
        return {
            "name": self.name,
            "description": "fake",
            "inputSchema": {"json": {"type": "object", "properties": {}}},
        }

    @property
    def tool_type(self):
        return "test"

    async def stream(self, tool_use, invocation_state, **kwargs):
        del invocation_state, kwargs
        yield ToolResultEvent(
            {
                "toolUseId": tool_use["toolUseId"],
                "status": "success",
                "content": [{"text": "real"}],
            }
        )


def _before(invocation_state, selected_tool, tool_id, tool_input=None, name="shell"):
    return SimpleNamespace(
        invocation_state=invocation_state,
        selected_tool=selected_tool,
        tool_use={
            "name": name,
            "toolUseId": tool_id,
            "input": tool_input or {"command": "id", "options": {"a": 1, "b": 2}},
        },
    )


def _after(before_event, result, exception=None, cancel_message=None):
    return SimpleNamespace(
        invocation_state=before_event.invocation_state,
        selected_tool=before_event.selected_tool,
        tool_use=before_event.tool_use,
        result=result,
        exception=exception,
        cancel_message=cancel_message,
    )


def _result(tool_id, text, status="success"):
    return {
        "toolUseId": tool_id,
        "status": status,
        "content": [{"text": text}],
    }


async def _stream_result(selected_tool, tool_use, invocation_state):
    events = [event async for event in selected_tool.stream(tool_use, invocation_state)]
    assert len(events) == 1
    return events[0].tool_result


def test_registers_before_and_after_callbacks():
    registrations = []
    hook = ToolRepeatGuardHook()
    registry = SimpleNamespace(add_callback=lambda event_type, callback: registrations.append((event_type, callback)))

    hook.register_hooks(registry)

    assert registrations == [
        (BeforeToolCallEvent, hook._before_tool),
        (AfterToolCallEvent, hook._after_tool),
    ]


def test_third_call_reuses_result_and_fourth_stops_normally():
    hook = ToolRepeatGuardHook(repeat_threshold=3)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    first = _before(invocation_state, real_tool, "one")
    hook._before_tool(first)
    assert first.selected_tool is real_tool
    hook._after_tool(_after(first, _result("one", "first")))

    second = _before(invocation_state, real_tool, "two")
    hook._before_tool(second)
    assert second.selected_tool is real_tool
    second_result = _result("two", "second")
    hook._after_tool(_after(second, second_result))

    third = _before(invocation_state, real_tool, "three")
    hook._before_tool(third)
    assert third.selected_tool is not real_tool
    assert invocation_state["request_state"].get("stop_event_loop") is None
    third_result = asyncio.run(_stream_result(third.selected_tool, third.tool_use, invocation_state))
    assert third_result["toolUseId"] == "three"
    assert third_result["status"] == "success"
    assert third_result["content"][0] == {"text": "second"}
    assert "use the returned result" in third_result["content"][-1]["text"]
    hook._after_tool(_after(third, third_result))

    fourth = _before(invocation_state, real_tool, "four")
    hook._before_tool(fourth)
    fourth_result = asyncio.run(_stream_result(fourth.selected_tool, fourth.tool_use, invocation_state))

    assert fourth_result["content"][0] == {"text": "second"}
    assert invocation_state["request_state"]["stop_event_loop"] is True
    assert invocation_state["request_state"][REPEATED_TOOL_LOOP_STATE_KEY] == {
        "cycle_length": 1,
        "repeat_count": 4,
        "tool_name": "shell",
        "tool_names": ["shell"],
    }


def test_alternating_two_call_cycle_reuses_each_result_then_stops():
    hook = ToolRepeatGuardHook(repeat_threshold=3, max_cycle_length=8)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()
    calls = ["alpha", "beta", "alpha", "beta", "alpha"]

    for index, command in enumerate(calls, start=1):
        event = _before(invocation_state, real_tool, str(index), {"command": command})
        hook._before_tool(event)
        assert event.selected_tool is real_tool
        hook._after_tool(_after(event, _result(str(index), f"result-{index}")))

    completes_third_cycle = _before(invocation_state, real_tool, "six", {"command": "beta"})
    hook._before_tool(completes_third_cycle)
    cached_beta = asyncio.run(
        _stream_result(completes_third_cycle.selected_tool, completes_third_cycle.tool_use, invocation_state)
    )
    hook._after_tool(_after(completes_third_cycle, cached_beta))

    assert cached_beta["content"][0] == {"text": "result-4"}
    assert "cycle length 2, repeat 3" in cached_beta["content"][-1]["text"]
    assert invocation_state["request_state"].get("stop_event_loop") is None

    rotated_cycle = _before(invocation_state, real_tool, "seven", {"command": "alpha"})
    hook._before_tool(rotated_cycle)
    cached_alpha = asyncio.run(_stream_result(rotated_cycle.selected_tool, rotated_cycle.tool_use, invocation_state))

    assert cached_alpha["content"][0] == {"text": "result-5"}
    assert invocation_state["request_state"]["stop_event_loop"] is True
    assert invocation_state["request_state"][REPEATED_TOOL_LOOP_STATE_KEY] == {
        "cycle_length": 2,
        "repeat_count": 3,
        "tool_name": "shell",
        "tool_names": ["shell", "shell"],
    }


def test_three_call_cycle_is_detected_at_configured_threshold():
    hook = ToolRepeatGuardHook(repeat_threshold=2, max_cycle_length=3)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    for index, command in enumerate(["alpha", "beta", "gamma", "alpha", "beta"], start=1):
        event = _before(invocation_state, real_tool, str(index), {"command": command})
        hook._before_tool(event)
        assert event.selected_tool is real_tool
        hook._after_tool(_after(event, _result(str(index), f"result-{index}")))

    repeated = _before(invocation_state, real_tool, "six", {"command": "gamma"})
    hook._before_tool(repeated)
    cached = asyncio.run(_stream_result(repeated.selected_tool, repeated.tool_use, invocation_state))

    assert cached["content"][0] == {"text": "result-3"}
    assert "cycle length 3, repeat 2" in cached["content"][-1]["text"]


def test_incomplete_or_oversized_cycles_are_not_suppressed():
    real_tool = FakeTool()

    incomplete_hook = ToolRepeatGuardHook(repeat_threshold=3, max_cycle_length=2)
    incomplete_state = {"request_state": {}}
    for index, command in enumerate(["alpha", "beta", "alpha", "beta", "alpha"], start=1):
        event = _before(incomplete_state, real_tool, str(index), {"command": command})
        incomplete_hook._before_tool(event)
        assert event.selected_tool is real_tool
        incomplete_hook._after_tool(_after(event, _result(str(index), f"result-{index}")))

    bounded_hook = ToolRepeatGuardHook(repeat_threshold=2, max_cycle_length=2)
    bounded_state = {"request_state": {}}
    for index, command in enumerate(["alpha", "beta", "gamma", "alpha", "beta", "gamma"], start=1):
        event = _before(bounded_state, real_tool, str(index), {"command": command})
        bounded_hook._before_tool(event)
        assert event.selected_tool is real_tool
        bounded_hook._after_tool(_after(event, _result(str(index), f"result-{index}")))


def test_unrelated_call_resets_cycle_recovery_state():
    hook = ToolRepeatGuardHook(repeat_threshold=2, max_cycle_length=2)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    for index, command in enumerate(["alpha", "beta", "alpha"], start=1):
        event = _before(invocation_state, real_tool, str(index), {"command": command})
        hook._before_tool(event)
        hook._after_tool(_after(event, _result(str(index), f"result-{index}")))

    repeated = _before(invocation_state, real_tool, "four", {"command": "beta"})
    hook._before_tool(repeated)
    assert repeated.selected_tool is not real_tool

    changed = _before(invocation_state, real_tool, "changed", {"command": "whoami"})
    hook._before_tool(changed)

    assert changed.selected_tool is real_tool
    assert invocation_state["request_state"].get("stop_event_loop") is None


def test_structurally_equal_arguments_repeat_but_changed_arguments_reset():
    hook = ToolRepeatGuardHook(repeat_threshold=3)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()
    inputs = [
        {"command": "id", "options": {"a": 1, "b": 2}},
        {"options": {"b": 2, "a": 1}, "command": "id"},
        {"command": "id", "options": {"a": 1, "b": 2}},
    ]

    for index, tool_input in enumerate(inputs[:2], start=1):
        event = _before(invocation_state, real_tool, str(index), tool_input)
        hook._before_tool(event)
        hook._after_tool(_after(event, _result(str(index), f"result-{index}")))

    repeated = _before(invocation_state, real_tool, "three", inputs[2])
    hook._before_tool(repeated)
    assert repeated.selected_tool is not real_tool

    changed = _before(invocation_state, real_tool, "changed", {"command": "whoami"})
    hook._before_tool(changed)
    assert changed.selected_tool is real_tool


def test_error_results_are_reused_without_mutating_authoritative_result():
    hook = ToolRepeatGuardHook(repeat_threshold=2)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()
    original = _result("one", "permission denied", status="error")

    first = _before(invocation_state, real_tool, "one")
    hook._before_tool(first)
    hook._after_tool(_after(first, original, exception=RuntimeError("failed")))
    repeated = _before(invocation_state, real_tool, "two")
    hook._before_tool(repeated)
    cached = asyncio.run(_stream_result(repeated.selected_tool, repeated.tool_use, invocation_state))

    assert cached["status"] == "error"
    assert cached["toolUseId"] == "two"
    assert cached["content"][0] == {"text": "permission denied"}
    assert original == _result("one", "permission denied", status="error")


def test_canceled_calls_are_removed_from_repeat_history_and_not_cached():
    hook = ToolRepeatGuardHook(repeat_threshold=2)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    for tool_id in ("one", "two", "three"):
        event = _before(invocation_state, real_tool, tool_id, {"command": "curl http://target"})
        hook._before_tool(event)
        hook._after_tool(
            _after(
                event,
                _result(tool_id, "Recovery may only use a corrected call", status="error"),
                cancel_message="Recovery may only use a corrected call",
            )
        )
        assert event.selected_tool is real_tool

    repeat_state = invocation_state["_cyber_tool_repeat_guard"]
    assert repeat_state.history == []
    assert repeat_state.completed_results == {}
    assert invocation_state["request_state"] == {}


def test_cancellation_rolls_back_repeat_suppression_and_stop_state():
    hook = ToolRepeatGuardHook(repeat_threshold=2)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    first = _before(invocation_state, real_tool, "one")
    hook._before_tool(first)
    hook._after_tool(_after(first, _result("one", "first")))

    suppressed = _before(invocation_state, real_tool, "two")
    hook._before_tool(suppressed)
    assert suppressed.selected_tool is not real_tool
    suppressed_result = asyncio.run(
        _stream_result(suppressed.selected_tool, suppressed.tool_use, invocation_state)
    )
    hook._after_tool(_after(suppressed, suppressed_result))

    canceled_stop = _before(invocation_state, real_tool, "three")
    hook._before_tool(canceled_stop)
    assert canceled_stop.selected_tool is not real_tool
    assert invocation_state["request_state"]["stop_event_loop"] is True
    hook._after_tool(
        _after(
            canceled_stop,
            _result("three", "blocked", status="error"),
            cancel_message="blocked by recovery",
        )
    )

    assert invocation_state["request_state"] == {}


def test_repeat_state_is_scoped_to_invocation_and_requires_completed_result():
    hook = ToolRepeatGuardHook(repeat_threshold=2)
    real_tool = FakeTool()
    first_state = {"request_state": {}}

    pending = _before(first_state, real_tool, "pending")
    hook._before_tool(pending)
    still_pending = _before(first_state, real_tool, "still-pending")
    hook._before_tool(still_pending)
    assert still_pending.selected_tool is real_tool

    second_state = {"request_state": {}}
    separate = _before(second_state, real_tool, "separate")
    hook._before_tool(separate)
    assert separate.selected_tool is real_tool


def test_threshold_must_allow_an_initial_execution():
    with pytest.raises(ValueError, match="at least 2"):
        ToolRepeatGuardHook(repeat_threshold=1)

    with pytest.raises(ValueError, match="max_cycle_length must be at least 1"):
        ToolRepeatGuardHook(max_cycle_length=0)


@pytest.mark.parametrize("value", [-10, -1, 1, None, "3", SimpleNamespace()])
def test_invalid_configured_threshold_uses_default(value):
    assert normalize_tool_repeat_threshold(value) == DEFAULT_TOOL_REPEAT_THRESHOLD


@pytest.mark.parametrize("value", [0, 2, 7])
def test_supported_configured_threshold_is_preserved(value):
    assert normalize_tool_repeat_threshold(value) == value


@pytest.mark.parametrize("value", [-10, -1, 0, None, "8", True, SimpleNamespace()])
def test_invalid_max_cycle_length_uses_default(value):
    assert normalize_tool_repeat_max_cycle_length(value) == DEFAULT_TOOL_REPEAT_MAX_CYCLE_LENGTH


@pytest.mark.parametrize("value", [1, 2, 8, 32])
def test_supported_max_cycle_length_is_preserved(value):
    assert normalize_tool_repeat_max_cycle_length(value) == value
