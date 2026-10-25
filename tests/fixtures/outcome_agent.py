"""Production agent construction with a deterministic SDK transport for outcome tests."""

from types import SimpleNamespace
from unittest.mock import Mock

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookRegistry

from modules.agents import cyber_autoagent as agent_module
from modules.agents.cyber_autoagent import AgentRuntimeResources
from modules.config import AgentConfig
from modules.handlers.react import AgentEventHandler


def create_outcome_agent(monkeypatch, role, tools=()):
    """Use production hook selection with an SDK event registry and no model calls."""

    class TransportAgent:
        def __init__(self, **kwargs):
            self.tool_registry = Mock()
            self.hooks = HookRegistry()
            self.messages = []
            self.cleanup = Mock()
            for hook in kwargs["hooks"]:
                self.hooks.add_hook(hook)

    config = AgentConfig(target="https://target.test", objective="Map one flow", provider="ollama", model_id="test")
    callback = AgentEventHandler(
        operation_id="OP_TEST", emitter=Mock(), emit_operation_init=False, start_metrics_thread=False,
    )
    runtime = AgentRuntimeResources(
        config=config, operation_id="OP_TEST", server_config=SimpleNamespace(),
        config_manager=SimpleNamespace(getenv_int=lambda _name, default: default),
        callback_handler=callback, tools_list=[], tool_executor=object(),
        system_prompt_payload="system", system_prompt="system", hooks=[], conversation_manager=None,
        sdk_context_manager=None, trace_attributes={}, prompt_token_limit=0,
    )
    monkeypatch.setattr(agent_module, "create_strands_model", Mock(return_value=SimpleNamespace(stateful=False)))
    monkeypatch.setattr(
        agent_module, "create_agent_with_stateful_retry", lambda kwargs, *_args: TransportAgent(**kwargs),
    )
    monkeypatch.setattr(agent_module, "get_capabilities", Mock(return_value=SimpleNamespace(supports_reasoning=False)))
    monkeypatch.setattr(agent_module, "tool_catalog_wrapper", Mock(return_value="catalog"))
    return agent_module.create_agent(
        config.target, config.objective, config=config, runtime_resources=runtime,
        tools=list(tools), agent_type=role, include_tool_catalog=False,
    )


def complete_tool_call(agent, name, tool_input, output, *, tool_id="observed-tool", status="success"):
    """Dispatch SDK events rather than bypassing registered production hooks."""

    tool_use = {"toolUseId": tool_id, "name": name, "input": tool_input}
    state = {}
    agent.hooks.invoke_callbacks(BeforeToolCallEvent(
        agent=agent, selected_tool=None, tool_use=tool_use, invocation_state=state,
    ))
    event = AfterToolCallEvent(
        agent=agent, selected_tool=None, tool_use=tool_use, invocation_state=state,
        result={"status": status, "toolUseId": tool_id, "content": [{"text": output}]},
    )
    agent.hooks.invoke_callbacks(event)
    return event
