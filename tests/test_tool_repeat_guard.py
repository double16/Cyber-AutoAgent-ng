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
from modules.handlers.tool_recovery import TaskFailureRecoveryHook, ToolOutcomeJournal


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
        cancel_tool=False,
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


def _run_combined_before(repeat_hook, recovery_hook, event):
    """Run before callbacks in production registration order."""

    repeat_hook._before_tool(event)
    recovery_hook._before_tool(event)


def _run_combined_after(recovery_hook, repeat_hook, before_event, result, cancel_message=None):
    """Run after callbacks in Strands' reverse callback order."""

    event = _after(before_event, result, cancel_message=cancel_message)
    recovery_hook._after_tool(event)
    repeat_hook._after_tool(event)
    return event


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


def test_default_three_call_cycle_works_with_failure_recovery_hook():
    repeat_hook = ToolRepeatGuardHook()
    journal = ToolOutcomeJournal()
    recovery_hook = TaskFailureRecoveryHook(journal)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    commands = ["alpha", "beta", "gamma", "alpha", "beta", "gamma", "alpha", "beta"]
    for index, command in enumerate(commands, start=1):
        event = _before(invocation_state, real_tool, str(index), {"command": command})
        _run_combined_before(repeat_hook, recovery_hook, event)
        assert event.selected_tool is real_tool
        assert event.cancel_tool is False
        _run_combined_after(recovery_hook, repeat_hook, event, _result(str(index), f"result-{index}"))

    completes_third_cycle = _before(invocation_state, real_tool, "nine", {"command": "gamma"})
    _run_combined_before(repeat_hook, recovery_hook, completes_third_cycle)
    cached_gamma = asyncio.run(
        _stream_result(
            completes_third_cycle.selected_tool,
            completes_third_cycle.tool_use,
            invocation_state,
        )
    )
    _run_combined_after(recovery_hook, repeat_hook, completes_third_cycle, cached_gamma)

    assert cached_gamma["content"][0] == {"text": "result-6"}
    assert "cycle length 3, repeat 3" in cached_gamma["content"][-1]["text"]
    assert invocation_state["request_state"].get("stop_event_loop") is None

    rotated_cycle = _before(invocation_state, real_tool, "ten", {"command": "alpha"})
    _run_combined_before(repeat_hook, recovery_hook, rotated_cycle)
    cached_alpha = asyncio.run(
        _stream_result(rotated_cycle.selected_tool, rotated_cycle.tool_use, invocation_state)
    )
    _run_combined_after(recovery_hook, repeat_hook, rotated_cycle, cached_alpha)

    assert cached_alpha["content"][0] == {"text": "result-7"}
    assert recovery_hook.unresolved is False
    assert len(journal.entries()) == 10
    assert all(outcome.success and outcome.recovery_role == "normal" for outcome in journal.entries())
    assert invocation_state["request_state"]["stop_event_loop"] is True
    assert invocation_state["request_state"][REPEATED_TOOL_LOOP_STATE_KEY] == {
        "cycle_length": 3,
        "repeat_count": 3,
        "tool_name": "shell",
        "tool_names": ["shell", "shell", "shell"],
    }


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


def test_alternating_canceled_calls_are_detected_but_not_cached():
    hook = ToolRepeatGuardHook(repeat_threshold=3)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    commands = ["alpha", "beta", "alpha", "beta", "alpha", "beta"]
    for index, command in enumerate(commands, start=1):
        event = _before(invocation_state, real_tool, str(index), {"command": command})
        hook._before_tool(event)
        hook._after_tool(
            _after(
                event,
                _result(str(index), "Recovery may only use a corrected call", status="error"),
                cancel_message="Recovery may only use a corrected call",
            )
        )
        assert event.selected_tool is real_tool

    repeat_state = invocation_state["_cyber_tool_repeat_guard"]
    assert len(repeat_state.history) == 6
    assert repeat_state.completed_results == {}
    assert invocation_state["request_state"]["stop_event_loop"] is True
    assert invocation_state["request_state"][REPEATED_TOOL_LOOP_STATE_KEY] == {
        "cycle_length": 2,
        "repeat_count": 3,
        "tool_name": "shell",
        "tool_names": ["shell", "shell"],
    }


def test_repeated_single_canceled_call_stops_at_threshold_without_cached_result():
    hook = ToolRepeatGuardHook(repeat_threshold=3)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    for tool_id in ("one", "two", "three"):
        event = _before(invocation_state, real_tool, tool_id)
        hook._before_tool(event)
        hook._after_tool(
            _after(event, _result(tool_id, "blocked", status="error"), cancel_message="blocked by recovery")
        )
        assert event.selected_tool is real_tool

    assert invocation_state["request_state"][REPEATED_TOOL_LOOP_STATE_KEY]["cycle_length"] == 1
    assert invocation_state["request_state"][REPEATED_TOOL_LOOP_STATE_KEY]["repeat_count"] == 3


def test_interrupted_canceled_cycle_does_not_stop_or_cache_results():
    hook = ToolRepeatGuardHook(repeat_threshold=3, max_cycle_length=2)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    for index, command in enumerate(["alpha", "beta", "alpha", "changed"], start=1):
        event = _before(invocation_state, real_tool, str(index), {"command": command})
        hook._before_tool(event)
        hook._after_tool(
            _after(event, _result(str(index), "blocked", status="error"), cancel_message="blocked by recovery")
        )

    repeat_state = invocation_state["_cyber_tool_repeat_guard"]
    assert repeat_state.completed_results == {}
    assert invocation_state["request_state"] == {}


def test_recovery_allows_independent_diagnostics_to_reach_repeat_guard():
    repeat_hook = ToolRepeatGuardHook(repeat_threshold=3, max_cycle_length=2)
    recovery_hook = TaskFailureRecoveryHook(ToolOutcomeJournal(), max_policy_violations=100)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    failed = _before(
        invocation_state,
        real_tool,
        "failed",
        {"command": "feroxbuster -u http://target -w /missing.txt"},
    )
    _run_combined_before(repeat_hook, recovery_hook, failed)
    _run_combined_after(
        recovery_hook,
        repeat_hook,
        failed,
        _result("failed", "Could not open /missing.txt", status="error"),
    )
    assert recovery_hook.unresolved is True

    diagnostic = _before(invocation_state, real_tool, "diagnostic", {"command": "ls /usr/share/wordlists"})
    _run_combined_before(repeat_hook, recovery_hook, diagnostic)
    assert diagnostic.cancel_tool is False
    _run_combined_after(recovery_hook, repeat_hook, diagnostic, _result("diagnostic", "dirb"))

    commands = ["ls -la /missing", "ls -F /missing"]
    for index, command in enumerate(commands, start=1):
        allowed = _before(invocation_state, real_tool, f"allowed-{index}", {"command": command})
        _run_combined_before(repeat_hook, recovery_hook, allowed)
        assert allowed.cancel_tool is False
        _run_combined_after(
            recovery_hook,
            repeat_hook,
            allowed,
            _result(allowed.tool_use["toolUseId"], "missing", status="error"),
        )

    state = invocation_state["_cyber_tool_repeat_guard"]
    assert state.completed_results
    assert REPEATED_TOOL_LOOP_STATE_KEY not in invocation_state["request_state"]


def test_failed_diagnostic_does_not_seed_recovery_blocked_calls():
    repeat_hook = ToolRepeatGuardHook()
    journal = ToolOutcomeJournal()
    recovery_hook = TaskFailureRecoveryHook(journal, max_policy_violations=100)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    diagnostic = _before(invocation_state, real_tool, "diagnostic", {"command": "ls /missing"})
    _run_combined_before(repeat_hook, recovery_hook, diagnostic)
    _run_combined_after(
        recovery_hook,
        repeat_hook,
        diagnostic,
        _result("diagnostic", "No such file or directory", status="error"),
    )

    following = _before(invocation_state, real_tool, "following", {"command": "curl -I http://target"})
    _run_combined_before(repeat_hook, recovery_hook, following)

    assert following.cancel_tool is False
    assert following.selected_tool is real_tool
    assert recovery_hook.unresolved is False
    assert journal.entries()[0].correctable is False
    assert invocation_state["request_state"] == {}


def test_recovery_allows_unrelated_three_call_sequence():
    repeat_hook = ToolRepeatGuardHook()
    journal = ToolOutcomeJournal()
    recovery_hook = TaskFailureRecoveryHook(journal, max_policy_violations=100)
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    failed = _before(
        invocation_state,
        real_tool,
        "failed",
        {"command": "feroxbuster -u http://target -w /missing.txt"},
    )
    _run_combined_before(repeat_hook, recovery_hook, failed)
    _run_combined_after(
        recovery_hook,
        repeat_hook,
        failed,
        _result("failed", "Could not open /missing.txt", status="error"),
    )
    assert recovery_hook.unresolved is True

    commands = ["printf alpha", "printf beta", "printf gamma"]
    for index, command in enumerate(commands, start=1):
        allowed = _before(invocation_state, real_tool, f"allowed-{index}", {"command": command})
        _run_combined_before(repeat_hook, recovery_hook, allowed)
        assert allowed.selected_tool is real_tool
        assert allowed.cancel_tool is False
        _run_combined_after(
            recovery_hook,
            repeat_hook,
            allowed,
            _result(allowed.tool_use["toolUseId"], command),
        )

    state = invocation_state["_cyber_tool_repeat_guard"]
    assert len(state.completed_results) == 4
    assert recovery_hook.unresolved is False
    assert [outcome.recovery_role for outcome in journal.entries()] == ["normal", "alternative", "normal", "normal"]
    assert invocation_state["request_state"] == {}


def test_successful_recovery_correction_remains_executable_with_repeat_guard():
    repeat_hook = ToolRepeatGuardHook(repeat_threshold=3)
    recovery_hook = TaskFailureRecoveryHook(ToolOutcomeJournal())
    invocation_state = {"request_state": {}}
    real_tool = FakeTool()

    failed = _before(
        invocation_state,
        real_tool,
        "failed",
        {"command": "feroxbuster -u http://target -w /missing.txt"},
    )
    _run_combined_before(repeat_hook, recovery_hook, failed)
    _run_combined_after(
        recovery_hook,
        repeat_hook,
        failed,
        _result("failed", "Could not open /missing.txt", status="error"),
    )

    correction = _before(
        invocation_state,
        real_tool,
        "correction",
        {"command": "feroxbuster -u http://target -w /valid.txt"},
    )
    _run_combined_before(repeat_hook, recovery_hook, correction)
    assert correction.cancel_tool is False
    assert correction.selected_tool is real_tool
    _run_combined_after(
        recovery_hook,
        repeat_hook,
        correction,
        _result("correction", "200 /login.php"),
    )

    assert recovery_hook.unresolved is False
    assert invocation_state["request_state"] == {}
    assert [outcome.recovery_role for outcome in recovery_hook.journal.entries()] == ["normal", "correction"]


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
