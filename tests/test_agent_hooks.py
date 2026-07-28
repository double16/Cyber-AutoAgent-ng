import importlib
from types import SimpleNamespace
from unittest.mock import Mock

from strands.types.exceptions import MaxTokensReachedException

# this import helps the hooks import avoid a circular dependency
importlib.import_module("cyberautoagent")
from modules.handlers.agent_repair_hook import AgentRepairHook  # noqa: E402
from modules.handlers.react.hooks import ReactHooks  # noqa: E402


class RecordingEmitter:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)



def test_react_hooks_lifecycle_and_result_processing():
    emitter = RecordingEmitter()
    hooks = ReactHooks(emitter=emitter, operation_id="OP")
    registry = SimpleNamespace(add_callback=Mock())
    hooks.register_hooks(registry)
    assert registry.add_callback.call_count == 2

    before = SimpleNamespace(
        tool_use={
            "name": "shell",
            "toolUseId": "tool-1",
            "input": {"command": '["id", "whoami"]'},
        }
    )
    hooks._on_before_tool(before)
    assert emitter.events[0]["type"] == "tool_start"
    assert emitter.events[0]["tool_input"]["command"] == ["id", "whoami"]
    assert any(event["type"] == "tool_input_corrected" for event in emitter.events)

    after = SimpleNamespace(
        tool_use={"name": "shell", "toolUseId": "tool-1"},
        result={"status": "error", "content": [{"text": "bad"}, {"text": "news"}]},
    )
    hooks._on_after_tool(after)
    assert any(event["type"] == "thinking_end" for event in emitter.events)
    assert any(event["type"] == "tool_invocation_end" for event in emitter.events)
    assert any(event["type"] == "tool_end" for event in emitter.events)
    assert hooks._process_tool_result(None) == (True, "")
    assert hooks._process_tool_result({"status": "success", "content": "ok"}) == (True, "ok")
    assert hooks._process_tool_result("plain") == (True, "plain")
    assert hooks._calculate_duration("missing") == 0.0


def test_react_hooks_can_disable_tool_lifecycle_when_callback_handler_owns_it():
    emitter = RecordingEmitter()
    hooks = ReactHooks(emitter=emitter, operation_id="OP", emit_tool_lifecycle=False)

    before = SimpleNamespace(
        tool_use={
            "name": "shell",
            "toolUseId": "tool-1",
            "input": {"command": '["id", "whoami"]'},
        }
    )
    hooks._on_before_tool(before)

    assert [event["type"] for event in emitter.events] == ["tool_input_corrected"]
    assert emitter.events[0]["tool_input"]["command"] == ["id", "whoami"]

    after = SimpleNamespace(
        tool_use={"name": "shell", "toolUseId": "tool-1"},
        result={"status": "success", "content": [{"text": "ok"}]},
    )
    hooks._on_after_tool(after)

    event_types = [event["type"] for event in emitter.events]
    assert "thinking_end" in event_types
    assert "tool_start" not in event_types
    assert "tool_invocation_start" not in event_types
    assert "tool_invocation_end" not in event_types
    assert "tool_end" not in event_types


def test_react_hooks_swarm_rewrite():
    hooks = ReactHooks(emitter=RecordingEmitter())
    event = SimpleNamespace(
        tool_use={
            "name": "swarm",
            "input": {
                "agents": [
                    {
                        "name": "a",
                        "model_provider": "bedrock",
                        "model_settings": {"model_id": "m", "params": {"temperature": 0.1}},
                    },
                    "plain-agent",
                ]
            },
        }
    )

    hooks._rewrite_swarm_args(event)

    agent = event.tool_use["input"]["agents"][0]
    assert "model_provider" not in agent
    assert agent["model_settings"] == {"params": {"temperature": 0.1}}
    assert event.tool_use["input"]["agents"][1] == "plain-agent"


def test_agent_repair_hook_json_patch_and_state_paths(monkeypatch):
    hook = AgentRepairHook()
    monkeypatch.setattr("modules.handlers.agent_repair_hook._JSON_TOOL_CALL_PATCH_ATTEMPT", False)
    patch_call = Mock(return_value=True)
    monkeypatch.setattr("modules.handlers.agent_repair_hook.patch_ollama_model_json_toolcalls", patch_call)

    event = SimpleNamespace(
        agent=SimpleNamespace(
            callback_handler=SimpleNamespace(get_budget_progress=lambda: {"progress_percent": 1}),
            messages=[],
        ),
        exception=None,
        stop_response=SimpleNamespace(
            stop_reason="end_turn",
            message={"content": [{"text": '```json\n{"name":"shell","arguments":{"cmd":"id"}}\n```'}]},
        ),
        retry=False,
        state={},
    )
    hook.after_model_call_check(event)
    assert event.retry is True
    patch_call.assert_called_once()

    parse_error_event = SimpleNamespace(
        agent=SimpleNamespace(
            callback_handler=SimpleNamespace(get_budget_progress=lambda: {"progress_percent": 2}),
            messages=[],
        ),
        exception=RuntimeError("error parsing tool call invalid character"),
        stop_response=None,
        retry=False,
        context={},
    )
    hook.after_model_call_check(parse_error_event)
    assert parse_error_event.retry is True
    hook.before_model_call_inject(
        SimpleNamespace(agent=SimpleNamespace(messages=[]), context=parse_error_event.context)
    )
    assert parse_error_event.context == {}

    fallback_event = SimpleNamespace(agent=SimpleNamespace(messages=[]))
    assert hook._state_bag(fallback_event) is fallback_event.agent._hook_state
    assert hook._state_bag(SimpleNamespace()) == {}


def test_agent_repair_hook_unwraps_registered_generic_tool_use():
    hook = AgentRepairHook()
    shell_tool = object()
    event = SimpleNamespace(
        agent=SimpleNamespace(tool_registry=SimpleNamespace(registry={"shell": shell_tool})),
        selected_tool=None,
        tool_use={
            "name": "tool_use",
            "toolUseId": "wrapper-1",
            "input": {"tool_name": "shell", "parameters": {"command": "id"}},
        },
        cancel_tool=False,
    )

    hook.before_tool_call_repair(event)

    assert event.selected_tool is shell_tool
    assert event.tool_use == {
        "name": "shell",
        "toolUseId": "wrapper-1",
        "input": {"command": "id"},
    }
    assert event.cancel_tool is False


def test_agent_repair_hook_rejects_unregistered_generic_tool_use():
    hook = AgentRepairHook()
    event = SimpleNamespace(
        agent=SimpleNamespace(tool_registry=SimpleNamespace(registry={"create_tasks": object()})),
        selected_tool=None,
        tool_use={
            "name": "tool_use",
            "toolUseId": "wrapper-1",
            "input": {"tool_name": "dirb", "parameters": {"target": "http://target"}},
        },
        cancel_tool=False,
    )

    hook.before_tool_call_repair(event)

    assert event.selected_tool is None
    assert "create_tasks" in event.cancel_tool


def test_agent_repair_hook_ignores_direct_tool_calls_and_rejects_invalid_wrapper_input():
    hook = AgentRepairHook()
    direct = SimpleNamespace(tool_use={"name": "shell"}, cancel_tool=False)
    hook.before_tool_call_repair(direct)
    assert direct.cancel_tool is False

    invalid = SimpleNamespace(
        agent=SimpleNamespace(tool_registry=SimpleNamespace(registry={})),
        tool_use={"name": "tool_use", "input": "shell"},
        cancel_tool=False,
    )
    hook.before_tool_call_repair(invalid)
    assert invalid.cancel_tool


def test_agent_repair_hook_leaves_max_tokens_for_controller_recovery():
    hook = AgentRepairHook()
    agent = SimpleNamespace(
        callback_handler=SimpleNamespace(get_budget_progress=lambda: {"progress_percent": 1}, reasoning_buffer=[]),
        messages=[
            {"role": "assistant", "content": [{"text": "repeat repeat repeat"}]},
        ],
    )
    state = {}
    event = SimpleNamespace(
        agent=agent,
        exception=MaxTokensReachedException("max_tokens"),
        stop_response=SimpleNamespace(
            stop_reason="max_tokens",
            message={"content": [{"text": "repeat repeat repeat"}]},
        ),
        retry=False,
        state=state,
    )
    hook.after_model_call_check(event)
    assert event.retry is False
    assert state == {}
    assert agent.messages[-1]["content"][0]["text"] == "repeat repeat repeat"
