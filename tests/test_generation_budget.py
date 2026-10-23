"""Generation allocation invariants at controller and final transport boundaries."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field
from strands.types.exceptions import ContextWindowOverflowException

from modules.config.models.generation_budget import (
    GenerationRequest,
    allocate_generation,
    attach_generation_budget,
    model_generation_request,
    prepare_generation_model,
    schema_answer_tokens,
    summary_generation_budget,
)
from modules.config.models.invocation import model_output_budget


@pytest.mark.parametrize("input_tokens,answer,cap,expected,reason", [
    (1000, 1024, None, 8192, "preferred_target"),
    (35000, 1024, None, 4200, "capacity_limited"),
    (1000, 10000, None, 10000, "required_answer"),
    (1000, 1024, 2000, 2000, "capacity_limited"),
    (38176, 1024, None, 1024, "capacity_limited"),
])
def test_allocation(input_tokens, answer, cap, expected, reason):
    result = allocate_generation(GenerationRequest(40000, input_tokens, 8192, answer, output_ceiling=cap))
    assert result.generation_tokens == expected
    assert result.reason == reason
    assert result.safety_tokens == 800


@pytest.mark.parametrize("budget_request", [
    GenerationRequest(0, 0, 1, 1), GenerationRequest(1000, -1, 1, 1),
    GenerationRequest(1000, 0, 0, 1), GenerationRequest(1000, 0, 1, 0),
    GenerationRequest(1000, 0, 1, 1, -1), GenerationRequest(1000, 0, 1, 1, output_ceiling=0),
])
def test_invalid_request(budget_request):
    with pytest.raises(ValueError):
        allocate_generation(budget_request)


@pytest.mark.parametrize("budget_request", [
    GenerationRequest(40000, 39000, 8192, 1024),
    GenerationRequest(40000, 1, 8192, 1024, output_ceiling=1000),
    GenerationRequest(40000, 35000, 8192, 1024, 4096),
])
def test_impossible_answer_or_reasoning_fails_before_call(budget_request):
    with pytest.raises(ContextWindowOverflowException, match="Required generation minimum"):
        allocate_generation(budget_request)


class Result(BaseModel):
    status: str


class RequiredRows(BaseModel):
    rows: list[Result] = Field(min_length=385)


class Transport:
    def __init__(self, provider="ollama"):
        self.config = {"model_id": "fixture", "context_window_limit": 40000, "max_tokens": 8192}
        self._output_tokens = 8192
        self._cyber_llm_role = "task_executor"
        self.client_args = {}
        self.calls = []
        attach_generation_budget(self, provider, "fixture", None)

    def update_config(self, **kwargs):
        self.config.update(kwargs)

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        await asyncio.sleep(0)
        self.calls.append((deepcopy(self.config), deepcopy(messages), self._output_tokens))
        yield {"done": True}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        async for event in self.stream(prompt, system_prompt=system_prompt):
            yield event


def test_final_transport_growth_shrink_and_isolation(monkeypatch):
    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens",
                        lambda _m, messages, *_a, **_kw: len(messages[0]["content"][0]["text"]))
    model = Transport()

    async def invoke(size):
        return [event async for event in model.stream([{"role": "user", "content": [{"text": "x" * size}]}])]

    async def run():
        await asyncio.gather(invoke(35000), invoke(1000))
        await invoke(1000)

    asyncio.run(run())
    assert [call[2] for call in model.calls] == [4200, 8192, 8192]
    assert model.config["max_tokens"] == model._output_tokens == 8192
    assert all(config["max_tokens"] == reservation for config, _, reservation in model.calls)


@pytest.mark.parametrize("provider", ["ollama", "litellm", "bedrock", "gemini"],
                         ids=["local-wire", "litellm-wire", "bedrock-wire", "gemini-wire"])
def test_provider_setting_and_reservation_match(provider, monkeypatch):
    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens", lambda *_a, **_kw: 35000)
    model = Transport(provider)
    if provider == "gemini":
        model.config["params"] = {"temperature": 0.4, "max_output_tokens": 8192}
    asyncio.run(_consume(model.stream([{"role": "user", "content": [{"text": "task"}]}])))
    config, _, reserved = model.calls[0]
    assert reserved == 4200
    key = "max_output_tokens" if provider == "gemini" else "max_tokens"
    assert (config["params"][key] if provider in {"gemini", "litellm"} else config[key]) == reserved


async def _consume(generator):
    return [item async for item in generator]


def test_native_schema_and_tools_are_counted_and_required_rows_are_protected(monkeypatch):
    model = Transport()
    seen = []
    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens",
                        lambda *args, **kwargs: seen.append((args, kwargs)) or 10)
    asyncio.run(_consume(model.structured_output(Result, [{"role": "user", "content": [{"text": "task"}]}])))
    assert seen[0][0][3][0]["output_schema"] == Result.model_json_schema()
    assert schema_answer_tokens(RequiredRows.model_json_schema(), "fixture") > 385 * 256
    with pytest.raises(ContextWindowOverflowException):
        asyncio.run(_consume(model.structured_output(RequiredRows, [])))
    assert len(model.calls) == 1


def test_reasoning_exclusion_and_required_signed_replay(monkeypatch):
    messages = [{"role": "assistant", "content": [{"reasoningContent": {
        "reasoningText": {"text": "private", "signature": "signed"}}}, {"text": "visible"}]}]
    original = deepcopy(messages)
    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens", lambda *_a, **_kw: 10)
    monkeypatch.setattr("modules.config.models.capabilities.allows_reasoning_content_replay",
                        lambda provider, _id: provider == "bedrock")
    for provider in ("ollama", "bedrock"):
        model = Transport(provider)
        asyncio.run(_consume(model.stream(messages)))
        assert ("reasoningContent" in model.calls[0][1][0]["content"][0]) == (provider == "bedrock")
    assert messages == original


def test_thinking_minimums_and_scoped_floor(monkeypatch):
    model = Transport("bedrock")
    model.config["additional_request_fields"] = {"thinking": {"type": "enabled", "budget_tokens": 4096}}
    assert model_generation_request(model, 100).minimum_tokens == 5120
    with model_output_budget("task_executor", 12000, minimum_tokens=10000):
        assert model_generation_request(model, 100).minimum_tokens == 10000
    model.config["additional_request_fields"] = {}
    model.config["additional_args"] = {"think": "low"}
    assert model_generation_request(model, 100).reasoning_tokens == 1024
    model.config["additional_args"] = {"think": False}
    assert model_generation_request(model, 100).reasoning_tokens == 0
    model.config["params"] = {"thinking_config": {"thinking_budget": 2000}}
    assert model_generation_request(model, 100).reasoning_tokens == 2000
    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens", lambda *_a, **_kw: 10)
    local, _ = prepare_generation_model(model, "fixture", [])
    local.config["params"]["thinking_config"]["thinking_budget"] = 0
    assert model.config["params"]["thinking_config"]["thinking_budget"] == 2000


def test_agent_reduction_failure_can_shrink_output_then_restore_target(monkeypatch):
    from modules.handlers import conversation_budget as budget

    model = Transport()
    agent = SimpleNamespace(model=model)
    monkeypatch.setattr(budget, "_enforce_prompt_input_budget",
                        lambda _agent: (_ for _ in ()).throw(ContextWindowOverflowException("pinned")))
    monkeypatch.setattr(budget, "safe_estimate_tokens", lambda _agent: 35000)
    budget._ensure_prompt_within_budget(agent)
    assert model._output_tokens == 4200
    monkeypatch.setattr(budget, "safe_estimate_tokens", lambda _agent: 1000)
    budget._ensure_prompt_within_budget(agent)
    assert model._output_tokens == 8192
    monkeypatch.setattr(budget, "safe_estimate_tokens", lambda _agent: None)
    with pytest.raises(ContextWindowOverflowException, match="estimation unavailable"):
        budget._ensure_prompt_within_budget(agent)


def test_nested_summary_has_an_independent_floor_and_restores_parent_on_error():
    model = Transport()
    with model_output_budget("task_executor", 12000, minimum_tokens=10000):
        assert model_generation_request(model, 100).minimum_tokens == 10000
        with pytest.raises(RuntimeError), summary_generation_budget():
            request = model_generation_request(model, 100)
            assert request.preferred_tokens == 4096
            assert request.minimum_tokens == 1024
            raise RuntimeError("summary failed")
        assert model_generation_request(model, 100).minimum_tokens == 10000


def test_answer_floor_is_separate_from_a_larger_thinking_budget():
    with model_output_budget("task_executor", 12000, minimum_tokens=3024, answer_tokens=2000):
        model = Transport("bedrock")
    model.config["additional_request_fields"] = {"thinking": {"budget_tokens": 4096}}
    request = model_generation_request(model, 100)
    assert request.answer_tokens == 2000
    assert request.minimum_tokens == 6096


@pytest.mark.parametrize("provider", ["ollama", "bedrock", "litellm", "gemini"])
@pytest.mark.parametrize("completion_key", ["max_tokens", "max_completion_tokens"])
def test_actual_provider_wire_serialization(monkeypatch, provider, completion_key):
    from strands.models import BedrockModel
    from strands.models.gemini import GeminiModel
    from strands.models.litellm import LiteLLMModel

    from modules.config.models.ollama import OllamaModel

    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens", lambda *_a, **_kw: 35000)
    types = {"ollama": OllamaModel, "bedrock": BedrockModel, "litellm": LiteLLMModel, "gemini": GeminiModel}
    cls = types[provider]
    # Only exercise serialization: constructing remote clients is unnecessary for this contract.
    model = object.__new__(cls)
    model.config = {"model_id": "fixture", "context_window_limit": 40000, "max_tokens": 8192,
                    "params": {completion_key: 8192}, "stream": False}
    model.client_args = {}
    if provider in {"gemini", "litellm"}:
        model.config.pop("max_tokens")
    if provider == "gemini":
        model.config["params"] = {"max_output_tokens": 8192, "thinking_config": {"thinking_budget": 0}}
    model._output_tokens = 8192
    model._cyber_llm_role = "task_executor"
    model._tool_result_turn_separation_model_id = None
    attach_generation_budget(model, provider, "fixture", None)
    messages = [{"role": "user", "content": [{"text": "task"}]}]
    local, messages = prepare_generation_model(model, "fixture", messages)
    if provider == "ollama":
        assert local.format_request(messages)["options"]["num_predict"] == 4200
    elif provider == "bedrock":
        assert local.format_request(messages)["inferenceConfig"]["maxTokens"] == 4200
    elif provider == "litellm":
        wire = local.format_request(messages)
        assert wire[completion_key] == 4200
        if completion_key == "max_completion_tokens":
            assert "max_tokens" not in wire
    else:
        assert local._format_request(messages, None, None, local.config["params"])["config"]["max_output_tokens"] == 4200
    assert local._output_tokens == 4200
    assert model._output_tokens == 8192


def test_litellm_native_structured_output_forwards_actual_generation_limit(monkeypatch):
    import strands.models.litellm as sdk

    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens", lambda *_a, **_kw: 35000)
    monkeypatch.setattr(sdk, "supports_response_schema", lambda _model: True)
    captured = []

    async def completion(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"status":"done"}'))])

    monkeypatch.setattr(sdk.litellm, "acompletion", completion)
    model = sdk.LiteLLMModel(model_id="fixture", params={"max_tokens": 8192, "temperature": 0.2},
                             context_window_limit=40000)
    model._output_tokens = 8192
    model._cyber_llm_role = "task_executor"
    attach_generation_budget(model, "litellm", "fixture", None)
    result = asyncio.run(_consume(model.structured_output(Result, [{"role": "user", "content": [{"text": "task"}]}])))
    assert result[0]["output"].status == "done"
    assert captured[0]["max_tokens"] == 4200
    assert captured[0]["temperature"] == 0.2
    assert captured[0]["stream"] is False
    assert "max_tokens" not in model.client_args


def test_assembled_agent_and_direct_sdk_summary_have_separate_allocations(monkeypatch):
    from strands import Agent
    from strands.agent.conversation_manager.compression.context_compression import generate_summary

    from modules.handlers import conversation_budget as budget
    from tests.test_live_prompt_fitting import LocalModel

    class RecordingModel(LocalModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.allocations.append(self._output_tokens)
            async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield event

    model = RecordingModel()
    model.allocations = []
    model._output_tokens = 8192
    model._cyber_llm_role = "task_executor"
    attach_generation_budget(model, "ollama", "fixture/live", None)
    monkeypatch.setattr(budget, "estimate_prompt_tokens", lambda *_a, **_kw: 35000)
    monkeypatch.setattr(budget, "safe_estimate_tokens", lambda _agent: 35000)
    monkeypatch.setattr(budget, "_SHARED_CONVERSATION_MANAGER", None)
    agent = Agent(model=model, system_prompt="Assigned task and frozen acceptance.", callback_handler=None,
                  hooks=[budget.PromptBudgetHook(budget._ensure_prompt_within_budget)])
    agent("Assess the assigned HTTP, network, or source subject.")
    assert model.allocations == [4200]
    with model_output_budget("task_executor", 12000, minimum_tokens=10000, answer_tokens=8000):
        with summary_generation_budget():
            summary = asyncio.run(generate_summary(agent.messages, model))
    assert summary["content"][0]["text"] == "done"
    assert model.allocations == [4200, 4096]
    assert model._cyber_preferred_output_tokens == 8192
    assert model._output_tokens == 4200


def test_genuine_output_truncation_increases_only_its_invocation_target():
    from strands.types.exceptions import MaxTokensReachedException

    from modules.agents.multi_agent_workflow import MultiAgentWorkflowController
    from modules.config.models.agent_profiles import get_agent_settings_registry
    from modules.config.models.invocation import invocation_output_tokens, invocation_reasoning
    from modules.config.types import BudgetConfig
    from modules.handlers.max_token_recovery import MaxTokenClassification
    from tests.test_multi_agent_workflow import FakeState, _plan, _runtime

    registry = get_agent_settings_registry()
    original = registry.get_settings("plan_creator").to_dict()
    calls = []

    def runner(role, *_args):
        calls.append((invocation_output_tokens(role), invocation_reasoning(role)))
        if len(calls) == 1:
            error = MaxTokensReachedException("length")
            error.max_token_classification = MaxTokenClassification("output_truncation", 0.0, "hash", 2000)
            raise error
        return '{"status":"done"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=FakeState(_plan()),
        text_runner=runner,
    )
    result = controller._run_json_text_agent("plan_creator", "Create assigned result", [], "system",
                                            output_model_override=Result)
    assert result == {"status": "done"}
    assert calls == [(original["max_tokens"], None), (original["max_tokens"] + 2048, None)]
    assert registry.get_settings("plan_creator").to_dict() == original
    assert invocation_output_tokens("plan_creator") is None
