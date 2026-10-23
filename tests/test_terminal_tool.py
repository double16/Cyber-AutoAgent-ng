import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from strands import Agent, tool
from strands.hooks import AfterToolsEvent, BeforeToolCallEvent
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
    assert callbacks == [
        (callbacks[0][0], hook._after_tool), (BeforeToolCallEvent, hook._before_tool),
        (AfterToolsEvent, hook._after_tools),
    ]

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


def test_publication_checkpoint_counts_all_tools_and_only_new_digests_reset():
    progress = [""]
    hook = TerminalToolHook("task_executor")
    hook.configure(AgentRunPolicy(checkpoint_tool_calls=16, progress_key=lambda: progress[0]))
    for _ in range(15):
        event = _event("shell", "preview")
        hook._after_tool(event)
        assert not event.invocation_state
    progress[0] = "new-digest"
    event = _event("record_hypotheses", "saved")
    hook._after_tool(event)
    assert not event.invocation_state
    for _ in range(15):
        event = _event("store_observation", "duplicate")
        hook._after_tool(event)
        assert not event.invocation_state
    event = _event("record_hypotheses", "same digest")
    hook._after_tool(event)
    assert not event.invocation_state
    hook._after_tools(event)
    assert event.invocation_state["request_state"][POLICY_TERMINAL_STATE_KEY]["reason"] == "publication_checkpoint"
    queued = SimpleNamespace(invocation_state=event.invocation_state, cancel_tool=False)
    hook._before_tool(queued)
    assert queued.cancel_tool == (
        "Queued tool call canceled: hypothesis publication checkpoint reached after "
        "successful tool calls without publication progress. Return control to the workflow controller "
        "for the publication-only pass; this checkpoint does not mark the task complete."
    )
    hook.configure(AgentRunPolicy(checkpoint_tool_calls=1))
    event = _event("network_probe", "failure", status="error")
    hook._after_tool(event)
    hook._after_tools(event)
    assert not event.invocation_state


@pytest.mark.parametrize("failure", ["error", "exception", "canceled"])
def test_checkpoint_ignores_failed_attempts(failure):
    hook = TerminalToolHook("worker")
    hook.configure(AgentRunPolicy(checkpoint_tool_calls=2))
    for _ in range(4):
        event = _event("read", "bad parameters", status="error" if failure != "exception" else "success")
        if failure == "exception":
            event.exception = ValueError("invalid input")
        hook._after_tool(event)
        hook._after_tools(event)
        assert not event.invocation_state
    event = _event("read", "content")
    hook._after_tool(event)
    hook._after_tools(event)
    assert hook.checkpoint_count == 1
    assert not event.invocation_state


@pytest.mark.parametrize("publication_progress", [True, False])
def test_sdk_checkpoint_finishes_batch_and_later_publication_can_reset(publication_progress):
    calls = []
    progress = [""]

    @tool
    def read_subject(name: str) -> str:
        """Read the assigned subject."""
        calls.append("read")
        return f"observed {name}"

    @tool
    def publish_subject() -> str:
        """Publish the assigned subject."""
        calls.append("publish")
        if publication_progress:
            progress[0] = "new-publication"
        return "saved"

    hook = TerminalToolHook("worker")
    hook.configure(AgentRunPolicy(checkpoint_tool_calls=1, progress_key=lambda: progress[0]))
    agent = Agent(model=Mock(), tools=[read_subject, publish_subject], hooks=[hook], callback_handler=None)
    state = {}
    results = []

    async def collect():
        return [event async for event in SequentialToolExecutor()._execute(
            agent, [
                {"toolUseId": "invalid", "name": "read_subject", "input": {}},
                {"toolUseId": "read", "name": "read_subject", "input": {"name": "assigned"}},
                {"toolUseId": "publish", "name": "publish_subject", "input": {}},
            ], results, Mock(), Mock(), state,
        )]

    asyncio.run(collect())
    assert calls == ["read", "publish"]
    assert [result["status"] for result in results] == ["error", "success", "success"]
    assert not state.get("request_state")
    batch = AfterToolsEvent(agent=agent, message={"role": "user", "content": []}, invocation_state=state)
    agent.hooks.invoke_callbacks(batch)
    assert bool(batch.end_turn) is not publication_progress
    if publication_progress:
        assert hook.checkpoint_count == 0
    else:
        assert state["request_state"][POLICY_TERMINAL_STATE_KEY] == {
            "reason": "publication_checkpoint", "successful_tool_calls_without_progress": 2,
        }


@pytest.mark.parametrize("terminal_key", [
    POLICY_TERMINAL_STATE_KEY, TERMINAL_TOOL_COMPLETED_STATE_KEY, TERMINAL_TOOL_REJECTED_STATE_KEY,
])
def test_immediate_terminal_states_take_precedence_over_checkpoint(terminal_key):
    hook = TerminalToolHook("worker")
    hook.configure(AgentRunPolicy(checkpoint_tool_calls=1))
    hook._after_tool(_event("read", "ok"))
    event = SimpleNamespace(invocation_state={"request_state": {terminal_key: {"reason": "immediate"}}})
    hook._after_tools(event)
    assert event.invocation_state["request_state"] == {terminal_key: {"reason": "immediate"}}


def test_checkpoint_hook_tolerates_missing_policy_and_unusable_state():
    hook = TerminalToolHook("worker")
    event = _event("read", "ok")
    hook._after_tools(event)
    hook.configure(AgentRunPolicy(checkpoint_tool_calls=1))
    hook._after_tool(event)
    event.invocation_state["request_state"] = "invalid"
    hook._after_tools(event)
    assert event.invocation_state["request_state"] == "invalid"


def test_existing_safety_stop_prevents_publication_checkpoint():
    hook = TerminalToolHook("worker")
    hook.configure(AgentRunPolicy(checkpoint_tool_calls=1))
    hook._after_tool(_event("read", "ok"))
    event = SimpleNamespace(invocation_state={"request_state": {
        "stop_event_loop": True, "tool_recovery_exhausted": {"reason": "policy_violation_limit"},
    }})
    hook._after_tools(event)
    assert POLICY_TERMINAL_STATE_KEY not in event.invocation_state["request_state"]


@pytest.mark.parametrize("with_state", [True, False])
def test_sdk_cleanup_order_preserves_safety_stop_before_checkpoint(with_state):
    hook = TerminalToolHook("worker")
    hook.configure(AgentRunPolicy(checkpoint_tool_calls=1))
    hook._after_tool(_event("read", "ok"))

    def safety_stop(event):
        if with_state:
            event.invocation_state["request_state"] = {"stop_event_loop": True}
        event.end_turn = "Safety stop"

    safety_hook = SimpleNamespace(register_hooks=lambda registry: registry.add_callback(AfterToolsEvent, safety_stop))
    agent = Agent(model=Mock(), tools=[], hooks=[hook, safety_hook], callback_handler=None)
    event = AfterToolsEvent(agent=agent, message={"role": "user", "content": []}, invocation_state={})
    agent.hooks.invoke_callbacks(event)
    assert event.end_turn == "Safety stop"
    assert POLICY_TERMINAL_STATE_KEY not in event.invocation_state.get("request_state", {})


@pytest.mark.parametrize("terminal", [None, {}, {"reason": "task_completed"}])
def test_other_terminal_reasons_keep_completion_message(terminal):
    hook = TerminalToolHook("task_executor")
    queued = SimpleNamespace(
        invocation_state={"request_state": {POLICY_TERMINAL_STATE_KEY: terminal}},
        cancel_tool=False,
    )
    hook._before_tool(queued)
    assert queued.cancel_tool == "Worker completed; return control to the workflow controller."
