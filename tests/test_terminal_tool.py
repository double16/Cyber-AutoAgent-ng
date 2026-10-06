import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from strands import Agent, tool
from strands.hooks import BeforeToolCallEvent
from strands.tools.executors import SequentialToolExecutor

from modules.agents.run_policy import AgentRunPolicy
from modules.handlers.terminal_tool import (
    POLICY_TERMINAL_STATE_KEY,
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


def test_terminal_tool_hook_stops_after_successful_task_acceptance():
    event = _event("record_task_acceptance", '{"complete": true}')

    TerminalToolHook("task_executor")._after_tool(event)

    state = event.invocation_state["request_state"]
    assert state["stop_event_loop"] is True
    assert state[TERMINAL_TOOL_COMPLETED_STATE_KEY] == {"tool_name": "record_task_acceptance"}


@pytest.mark.parametrize(
    ("payload", "status"),
    [("{}", "success"), ("not-json", "success")],
)
def test_terminal_tool_hook_does_not_stop_incomplete_success_results(payload, status):
    event = _event("record_task_acceptance", payload, status=status)

    TerminalToolHook("task_executor")._after_tool(event)

    assert event.invocation_state == {}


def test_terminal_tool_hook_stops_failed_acceptance_for_controller_correction():
    event = _event("record_task_acceptance", "validation error", status="error")

    TerminalToolHook("task_executor")._after_tool(event)

    state = event.invocation_state["request_state"]
    assert state["stop_event_loop"] is True
    assert state[TERMINAL_TOOL_REJECTED_STATE_KEY] == {
        "tool_name": "record_task_acceptance",
        "error": "validation error",
    }


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


def test_terminal_tool_hook_registers_and_ignores_unusable_request_state():
    callbacks = []
    registry = SimpleNamespace(add_callback=lambda event, callback: callbacks.append((event, callback)))
    hook = TerminalToolHook("task_executor")

    hook.register_hooks(registry)
    assert callbacks == [(callbacks[0][0], hook._after_tool), (BeforeToolCallEvent, hook._before_tool)]

    event = _event("record_task_acceptance", '{"complete": true}')
    event.invocation_state["request_state"] = "not-a-mapping"
    hook._after_tool(event)
    assert event.invocation_state["request_state"] == "not-a-mapping"

    rejected = _event("ignored", "failure", status="error")
    TerminalToolHook("other")._record_terminal_failure(rejected, "ignored")
    assert rejected.invocation_state == {}


@pytest.mark.parametrize("subject", ["https://service.test/login", "tcp://service.test:22", "/workspace/source.py"])
@pytest.mark.parametrize("reason", ["worker_done", "subject_rejected"])
def test_controller_terminal_check_stops_and_cancels_queued_calls(subject, reason):
    hook = TerminalToolHook("worker")
    ready = {"value": None}
    hook.configure(AgentRunPolicy(terminal_check=lambda: ready["value"]))
    first = _event("execute", subject)
    hook._after_tool(first)
    assert not first.invocation_state
    ready["value"] = reason
    hook._after_tool(first)
    state = first.invocation_state["request_state"]
    assert state[POLICY_TERMINAL_STATE_KEY] == {"reason": reason}
    queued = SimpleNamespace(invocation_state=first.invocation_state, cancel_tool=False)
    hook._before_tool(queued)
    assert queued.cancel_tool
    # An unrelated invocation has no terminal marker.
    unrelated = SimpleNamespace(invocation_state={}, cancel_tool=False)
    hook._before_tool(unrelated)
    assert unrelated.cancel_tool is False


@pytest.mark.parametrize("match", ["all", "any"])
def test_policy_hook_matches_required_tools_and_resets_between_invocations(match):
    hook = TerminalToolHook("worker")
    policy = AgentRunPolicy(
        required_tool_names={"first", "second"},
        required_tool_match=match,
        require_successful_required_tools=True,
        terminal_after_required_tools=True,
        min_tool_calls=2,
        ignored_terminal_tool_names={"noise"},
    )
    hook.configure(policy)
    hook._after_tool(_event("noise", "ok"))
    failed = _event("first", "failed", status="error")
    hook._after_tool(failed)
    assert not failed.invocation_state
    first = _event("first", "ok")
    hook._after_tool(first)
    assert bool(first.invocation_state) is (match == "any")
    second = _event("second", "ok")
    hook._after_tool(second)
    assert second.invocation_state["request_state"]["stop_event_loop"] is True
    hook.configure(policy)
    assert not hook.successful_tools
    assert not hook.called_tools
    assert hook.tool_count == 0


def test_policy_predicate_is_authoritative_over_successful_tool_status():
    hook = TerminalToolHook("worker")
    hook.configure(
        AgentRunPolicy(
            required_tool_names={"capture"},
            terminal_after_required_tools=True,
            require_successful_required_tools=True,
            terminal_check=lambda: None,
        )
    )
    event = _event("capture", "available")
    hook._after_tool(event)
    assert not event.invocation_state


@pytest.mark.parametrize("state", [{}, {"request_state": "invalid"}])
def test_policy_hook_tolerates_non_mapping_request_state(state):
    hook = TerminalToolHook("worker")
    hook.configure(AgentRunPolicy(terminal_check=lambda: "done"))
    event = _event("capture", "ok")
    event.invocation_state = state
    hook._after_tool(event)
    queued = SimpleNamespace(invocation_state=state, cancel_tool=False)
    hook._before_tool(queued)
    assert bool(queued.cancel_tool) is isinstance(state["request_state"], dict)


def test_policy_hook_can_stop_after_attempt_when_success_is_not_required():
    hook = TerminalToolHook("worker")
    hook.configure(AgentRunPolicy(required_tool_names={"attempt"}, terminal_after_required_tools=True))
    event = _event("attempt", "failed", status="error")
    hook._after_tool(event)
    assert event.invocation_state["request_state"]["stop_event_loop"] is True


@pytest.mark.parametrize("outcome", ["success", "rejection"])
def test_sdk_sequential_batch_never_executes_queued_mutation_after_terminal_result(outcome):
    calls = []
    terminal = {"reason": None}

    @tool
    def execute_subject() -> str:
        """Execute the assigned subject."""
        calls.append("execute")
        terminal["reason"] = "worker_done" if outcome == "success" else "subject_rejected"
        return "result"

    @tool
    def mutate_subject() -> str:
        """Mutate the subject after execution."""
        calls.append("mutate")
        return "changed"

    hook = TerminalToolHook("worker")
    hook.configure(AgentRunPolicy(terminal_check=lambda: terminal["reason"]))
    agent = Agent(model=Mock(), tools=[execute_subject, mutate_subject], hooks=[hook], callback_handler=None)
    uses = [{"toolUseId": name, "name": name, "input": {}} for name in ("execute_subject", "mutate_subject")]
    results = []
    state = {}

    async def collect():
        return [
            event
            async for event in SequentialToolExecutor()._execute(
                agent,
                uses,
                results,
                Mock(),
                Mock(),
                state,
            )
        ]

    asyncio.run(collect())
    assert calls == ["execute"]
    assert [result["status"] for result in results] == ["success", "error"]
    assert state["request_state"][POLICY_TERMINAL_STATE_KEY]["reason"] == terminal["reason"]
