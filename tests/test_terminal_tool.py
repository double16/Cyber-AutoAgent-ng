from types import SimpleNamespace

import pytest

from modules.handlers.terminal_tool import (
    TASK_CREATOR_CORRECTIONS_EXHAUSTED_STATE_KEY,
    TERMINAL_TOOL_COMPLETED_STATE_KEY,
    TerminalToolHook,
)


def _event(tool_name, payload, *, status="success"):
    return SimpleNamespace(
        tool_use={"name": tool_name},
        result={"status": status, "content": [{"text": payload}]},
        exception=None,
        invocation_state={},
    )


@pytest.mark.parametrize(
    ("role", "tool_name"),
    [("task_creator", "create_tasks"), ("task_executor", "record_task_acceptance")],
)
def test_terminal_tool_hook_stops_after_successful_complete_result(role, tool_name):
    event = _event(tool_name, '{"complete": true}')

    TerminalToolHook(role)._after_tool(event)

    state = event.invocation_state["request_state"]
    assert state["stop_event_loop"] is True
    assert state[TERMINAL_TOOL_COMPLETED_STATE_KEY] == {"tool_name": tool_name}


@pytest.mark.parametrize(
    ("payload", "status"),
    [('{}', "success"), ('{"complete": true}', "error"), ("not-json", "success")],
)
def test_terminal_tool_hook_does_not_stop_incomplete_or_failed_results(payload, status):
    event = _event("record_task_acceptance", payload, status=status)

    TerminalToolHook("task_executor")._after_tool(event)

    assert event.invocation_state == {}


def test_terminal_tool_hook_ignores_non_terminal_tool():
    event = _event("store_observation", '{"complete": true}')

    TerminalToolHook("task_executor")._after_tool(event)

    assert event.invocation_state == {}


def test_task_creator_hook_stops_after_configured_corrections_are_exhausted():
    hook = TerminalToolHook("task_creator", max_task_creator_corrections=2)
    events = [_event("create_tasks", "Error", status="error") for _ in range(3)]

    hook._after_tool(events[0])
    hook._after_tool(events[1])
    hook._after_tool(events[2])

    assert events[0].invocation_state == {}
    assert events[1].invocation_state == {}
    state = events[2].invocation_state["request_state"]
    assert state["stop_event_loop"] is True
    assert state[TASK_CREATOR_CORRECTIONS_EXHAUSTED_STATE_KEY] == {
        "tool_name": "create_tasks",
        "failed_attempts": 3,
        "max_corrections": 2,
    }


def test_task_creator_hook_can_disable_corrections():
    event = _event("create_tasks", "Error", status="error")

    TerminalToolHook("task_creator", max_task_creator_corrections=0)._after_tool(event)

    assert event.invocation_state["request_state"]["stop_event_loop"] is True
