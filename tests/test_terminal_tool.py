from types import SimpleNamespace

import pytest

from modules.handlers.terminal_tool import (
    TERMINAL_TOOL_COMPLETED_STATE_KEY,
    TERMINAL_TOOL_REJECTED_STATE_KEY,
    TerminalToolHook,
)


def _event(tool_name, payload, *, status="success"):
    return SimpleNamespace(
        tool_use={"name": tool_name},
        result={"status": status, "content": [{"text": payload}]},
        exception=None,
        invocation_state={},
    )


def test_terminal_tool_hook_stops_after_successful_task_creation():
    event = _event("create_tasks", '{"complete": true}')

    TerminalToolHook("task_creator")._after_tool(event)

    state = event.invocation_state["request_state"]
    assert state["stop_event_loop"] is True
    assert state[TERMINAL_TOOL_COMPLETED_STATE_KEY] == {"tool_name": "create_tasks"}


def test_terminal_tool_hook_leaves_executor_completion_to_run_policy():
    event = _event("record_task_acceptance", '{"complete": true}')

    TerminalToolHook("task_executor")._after_tool(event)

    assert event.invocation_state == {}


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


def test_task_creator_hook_stops_each_failed_attempt_for_controller_retry():
    hook = TerminalToolHook("task_creator")
    event = _event("create_tasks", "Error", status="error")

    hook._after_tool(event)
    state = event.invocation_state["request_state"]
    assert state["stop_event_loop"] is True
    assert state[TERMINAL_TOOL_REJECTED_STATE_KEY] == {
        "tool_name": "create_tasks",
        "error": "Error",
    }
