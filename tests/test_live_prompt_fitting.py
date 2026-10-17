"""Fit finalized Strands input, including SDK structured-output tools, without provider calls."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, Field
from strands import Agent
from strands.hooks import BeforeModelCallEvent
from strands.models.model import Model

from modules.agents import cyber_autoagent as agent_module
from modules.agents.cyber_autoagent import AgentRuntimeResources
from modules.agents.multi_agent_workflow import default_structured_runner, default_text_runner
from modules.agents.prompt_context import PromptInputTooLarge, attach_live_prompt_fitter
from modules.agents.structured_outputs import TaskPromptCritiqueOutput, TaskPromptOutput
from modules.config import AgentConfig
from modules.handlers import conversation_budget as budget
from modules.handlers.conversation_budget import PromptBudgetHook
from modules.prompts.factory import get_role_system_prompt


class Review(BaseModel):
    status: str = Field(description="Final evaluation classification. " * 300)


class LocalModel(Model):
    """An offline provider that records the exact input at the model boundary."""

    def __init__(self, output=None):
        self.output = {"status": "done"} if output is None else output
        self.config = {"model_id": "fixture/live", "context_window_limit": 40000}
        self._output_tokens = 100
        self.calls = []

    def update_config(self, **config):
        self.config.update(config)

    def get_config(self):
        return self.config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        yield {"output": output_model(status="done")}

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls.append({"messages": deepcopy(messages), "tools": deepcopy(tool_specs),
                           "system": system_prompt,
                           "estimate": budget.estimate_prompt_tokens(
                               self.config["model_id"], messages, system_prompt, tool_specs,
                           )})
        yield {"messageStart": {"role": "assistant"}}
        if tool_specs:
            name = tool_specs[-1]["name"]
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "result", "name": name}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(self.output)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("subject", ["https://target.test:8443", "tcp://target.test:9000", "/workspace/source"])
def test_final_fitting_matches_actual_agent_budget_and_registered_schema(structured, subject):
    model = LocalModel()
    events = []
    checked = []
    prompt = f"## Frozen task\nAssess {subject}; preserve every criterion.\n"
    prompt += "## Eligible Semantic Memories\n" + "advisory " * 10000
    system = get_role_system_prompt("Final system instructions.", "task_prompt_critic")

    def check(agent):
        checked.append(budget._estimate_prompt_tokens_for_agent(agent))
        budget._ensure_prompt_within_budget(agent)

    agent = Agent(model=model, system_prompt=system, tools=[], load_tools_from_directory=False,
                  hooks=[PromptBudgetHook(check)], callback_handler=None)
    agent._prompt_token_limit = 6000
    attach_live_prompt_fitter(agent, prompt, events.append, ("Eligible Semantic Memories",))
    result = agent(prompt, **({"structured_output_model": Review} if structured else {}))
    if structured:
        assert result.structured_output.status == "done"
    else:
        assert any(block.get("text") == "done" for block in result.message["content"])
    assert len(model.calls) == 1
    sent = model.calls[0]
    assert sent["estimate"] == checked[0] == events[0]["estimated_tokens_after"]
    assert events[0]["input_limit"] == 5900
    assert sent["estimate"] <= events[0]["input_limit"]
    assert events[0]["reductions"] == ["Eligible Semantic Memories"]
    assert "preserve every criterion" in json.dumps(sent["messages"])
    assert "advisory advisory" not in json.dumps(sent["messages"])
    assert "<role_boundary>" in sent["system"]
    assert bool(sent["tools"]) is structured
    if structured:
        assert sent["tools"][0]["inputSchema"]["json"]["properties"]["status"]["description"]


def test_live_required_overflow_stops_before_provider_submission():
    model = LocalModel()
    agent = Agent(model=model, tools=[], load_tools_from_directory=False, callback_handler=None,
                  hooks=[PromptBudgetHook(budget._ensure_prompt_within_budget)])
    agent._prompt_token_limit = 100
    events = []
    prompt = "## Frozen task\n" + "required " * 10000
    attach_live_prompt_fitter(agent, prompt, events.append, ("Eligible Semantic Memories",))
    with pytest.raises(PromptInputTooLarge):
        agent(prompt, structured_output_model=Review)
    assert model.calls == []
    assert events[0]["estimated_tokens_after"] > events[0]["input_limit"]


def test_live_fitting_includes_other_messages_and_refits_on_correction():
    model = LocalModel()
    prompt = "## Frozen task\nimmutable\n## Task history\n" + "old history " * 3000
    agent = Agent(model=model, tools=[], load_tools_from_directory=False, callback_handler=None)
    agent._prompt_token_limit = 10000
    agent.messages = [{"role": "assistant", "content": [{"text": "earlier context"}]},
                      {"role": "user", "content": [{"text": prompt}, {"image": {}}]}]
    events = []
    attach_live_prompt_fitter(agent, prompt, events.append, ("Task history",))
    agent._cyber_prompt_fitter(agent)
    first = events[-1]["estimated_tokens_after"]
    agent.messages.append({"role": "user", "content": [{"text": "Correct the output. " * 100}]})
    agent._cyber_prompt_fitter(agent)
    assert events[-1]["estimated_tokens_after"] == budget._estimate_prompt_tokens_for_agent(agent)
    assert events[-1]["estimated_tokens_after"] > first
    assert agent.messages[0]["content"][0]["text"] == "earlier context"


def test_missing_request_or_limit_is_not_rewritten(monkeypatch):
    agent = SimpleNamespace(model=LocalModel(), messages=[{"role": "user", "content": [{"text": "other"}]}])
    original = deepcopy(agent.messages)
    log = Mock()
    attach_live_prompt_fitter(agent, "request", log, ())
    agent._cyber_prompt_fitter(agent)
    assert agent.messages == original
    agent.messages = [{"role": "user", "content": [{"text": "request"}]}]
    monkeypatch.setattr("modules.agents.prompt_context.get_agent_prompt_limits", lambda _agent: (None, 0))
    agent._cyber_prompt_fitter(agent)
    log.assert_not_called()


def test_live_fitter_runs_before_proactive_management():
    calls = []
    agent = SimpleNamespace(
        messages=[], _cyber_prompt_fitter=lambda _agent: calls.append("fit"),
        conversation_manager=SimpleNamespace(apply_management=lambda _agent: calls.append("management")),
    )
    hook = PromptBudgetHook(lambda _agent: calls.append("check"))
    hook._on_before_model_call(BeforeModelCallEvent(agent=agent))
    assert calls == ["fit", "management", "check"]


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("role", ["task_prompt_builder", "task_prompt_critic", "task_executor"])
@pytest.mark.parametrize("subject", ["https://target.test:8443", "tcp://target.test:9000", "/workspace/source"])
def test_production_runners_preserve_role_prompt_at_model_boundary(monkeypatch, structured, role, subject):
    output = ({"issues": [], "repairable": False} if role == "task_prompt_critic"
              else {"prompt": "Inspect the assigned subject and retain evidence.", "tools": [], "shell_commands": []})
    model = LocalModel(output)
    monkeypatch.setattr(agent_module, "create_strands_model", lambda *_args: model)
    monkeypatch.setattr(agent_module, "get_capabilities", lambda *_args: SimpleNamespace(supports_reasoning=False))
    original = ("Preserve target scope. <tools_and_capabilities>executor-only-protocol"
                "</tools_and_capabilities> Preserve evidence policy.")
    runtime = AgentRuntimeResources(
        config=AgentConfig(target=subject, objective="Inspect", provider="ollama", model_id="fixture/live"),
        operation_id="OP_ROLE", server_config=SimpleNamespace(), config_manager=SimpleNamespace(),
        callback_handler=Mock(), tools_list=[], tool_executor=None, system_prompt_payload=original,
        system_prompt=original, hooks=[], conversation_manager=None, sdk_context_manager=None,
        trace_attributes={}, prompt_token_limit=40000,
    )
    assigned = "Assigned role policy. " + original
    prompt = f"Inspect only {subject}."
    if structured:
        schema = TaskPromptCritiqueOutput if role == "task_prompt_critic" else TaskPromptOutput
        result = default_structured_runner(runtime)(role, prompt, [], assigned, schema)
        assert result == schema(**output).model_dump(exclude_none=True)
    else:
        assert default_text_runner(runtime)(role, prompt, [], assigned) == "done"
    sent = model.calls[0]
    assert "Assigned role policy." in sent["system"]
    assert "Preserve target scope." in sent["system"]
    assert "Preserve evidence policy." in sent["system"]
    assert ("executor-only-protocol" in sent["system"]) == (role == "task_executor")
    assert ("<role_boundary>" in sent["system"]) == (role != "task_executor")
    assert subject in json.dumps(sent["messages"])
    if structured:
        assert [tool["name"] for tool in sent["tools"]] == [schema.__name__]
    else:
        assert not sent["tools"]
