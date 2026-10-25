"""Exercise the real SDK lifecycle with a provider exposing native schema output."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from strands import Agent, tool
from strands.models.model import Model
from strands.tools.structured_output.structured_output_tool import StructuredOutputTool
from strands.types.exceptions import MaxTokensReachedException

from modules.agents.native_structured import (
    _UNSUPPORTED_NATIVE,
    FINALIZATION_GUIDANCE,
    INVESTIGATION_GUIDANCE,
    NativeFinalizer,
    invoke_structured,
    native_unavailable,
    retained_finalization_prompt,
)


class Answer(BaseModel):
    values: list[int]
    instructions: str | None = None


class Provider(Model):
    def __init__(self, provider="ollama", failures=()):
        self._cyber_provider = provider
        self._cyber_native_structured_output = provider != "bedrock"
        self.config = {"model_id": "fixture"}
        self.host = "fixture://local"
        self.calls = []
        self.failures = list(failures)

    def get_config(self):
        return self.config

    def update_config(self, **kwargs):
        self.config.update(kwargs)

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        self.calls.append(("native", deepcopy(prompt), output_model, system_prompt))
        if self.failures:
            raise self.failures.pop(0)
        yield {"metadata": {"usage": {"inputTokens": 21, "outputTokens": 9, "totalTokens": 30},
                            "metrics": {"latencyMs": 1}}}
        yield {"output": output_model(values=[1, 2], instructions=None)}

    async def stream(self, messages, tool_specs=None, system_prompt=None, tool_choice=None, **kwargs):
        self.calls.append(("tools", deepcopy(messages), deepcopy(tool_specs), system_prompt))
        if tool_specs and tool_specs[0]["name"] == "Answer":
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "answer", "name": "Answer"}},
                                         "contentBlockIndex": 0}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": '{"values":[3]}'}},
                                         "contentBlockIndex": 0}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockDelta": {"delta": {"text": "Evidence inspected; boundary remains conditional."},
                                         "contentBlockIndex": 0}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "end_turn"}}


@pytest.fixture(autouse=True)
def reset_capability_cache():
    _UNSUPPORTED_NATIVE.clear()
    yield
    _UNSUPPORTED_NATIVE.clear()


@pytest.mark.parametrize("provider", ["ollama", "litellm", "gemini"], ids=["local", "gateway", "google"])
def test_native_finalization_uses_sdk_lifecycle_and_usage(provider):
    model = Provider(provider)
    agent = Agent(model=model, callback_handler=None, system_prompt="Use supplied evidence only.")
    result = invoke_structured(agent, "Decide against this task", Answer)
    assert result.structured_output == Answer(values=[1, 2])
    assert [call[0] for call in model.calls] == ["native"]
    assert "Decide against this task" in str(model.calls[0][1])
    assert "Decide against this task" in str(model.calls[0][1][0]["content"][0]["text"])
    assert result.metrics.accumulated_usage["totalTokens"] == 30
    assert agent.model is model
    assert model._cyber_native_schema is False


@pytest.mark.parametrize("target", ["https://target.test/resource", "tcp://target.test:123", "/src/handler.py"])
def test_investigation_handoff_retains_evidence_and_hides_tools(target):
    @tool
    def inspect_evidence() -> str:
        """Inspect assigned evidence."""
        return "evidence"

    model = Provider()
    agent = Agent(model=model, tools=[inspect_evidence], callback_handler=None)
    result = invoke_structured(agent, f"Review {target}; preserve revision reasons", Answer, investigate=True)
    assert result.structured_output.values == [1, 2]
    assert [call[0] for call in model.calls] == ["tools", "native"]
    investigation, finalization = model.calls
    assert INVESTIGATION_GUIDANCE in investigation[3]
    assert [spec["name"] for spec in investigation[2]] == ["inspect_evidence"]
    assert target in str(finalization[1])
    assert "boundary remains conditional" in str(finalization[1])
    assert "revision reasons" in str(finalization[1])


def test_unsupported_native_falls_back_without_repeating_investigation_and_is_cached():
    model = Provider(failures=[NotImplementedError("native output unavailable")])
    agent = Agent(model=model, callback_handler=None)
    result = invoke_structured(agent, "Inspect current evidence", Answer, investigate=True)
    assert result.structured_output.values == [3]
    assert [call[0] for call in model.calls] == ["tools", "native", "tools"]
    assert [spec["name"] for spec in model.calls[-1][2]] == ["Answer"]
    second = Provider()
    result = invoke_structured(Agent(model=second, callback_handler=None), "Decide", Answer)
    assert result.structured_output.values == [3]
    assert [call[0] for call in second.calls] == ["tools"]
    second.host = "fixture://different-endpoint"
    assert invoke_structured(Agent(model=second, callback_handler=None), "Decide", Answer).structured_output.values == [1, 2]


def test_invalid_native_output_retains_evidence_for_caller_owned_correction():
    model = Provider(failures=[ValueError("malformed array")])
    agent = Agent(model=model, callback_handler=None)
    with pytest.raises(ValueError) as caught:
        invoke_structured(agent, "Inspect current evidence", Answer, investigate=True)
    prompt = retained_finalization_prompt("Inspect current evidence", caught.value)
    assert "boundary remains conditional" in prompt
    assert FINALIZATION_GUIDANCE in prompt
    result = invoke_structured(agent, prompt, Answer)
    assert result.structured_output.values == [1, 2]
    assert [call[0] for call in model.calls] == ["tools", "native", "native"]
    assert not _UNSUPPORTED_NATIVE
    assert retained_finalization_prompt("prompt", ValueError()) is None


@pytest.mark.parametrize("error", [ConnectionError("offline"), MaxTokensReachedException("length")])
def test_operational_failure_propagates_and_restores_model(error):
    model = Provider(failures=[error])
    agent = Agent(model=model, callback_handler=None)
    with pytest.raises(type(error)):
        invoke_structured(agent, "Decide", Answer)
    assert agent.model is model
    assert not _UNSUPPORTED_NATIVE


@pytest.mark.parametrize("stop_reason", ["limit_turns", "interrupt", "checkpoint", "cancelled",
                                        "guardrail_intervened", "content_filtered"])
def test_interrupted_investigation_never_finalizes(stop_reason):
    model = Provider()
    class Interrupted:
        system_prompt = "system"
        def __call__(self, *args, **kwargs):
            return SimpleNamespace(stop_reason=stop_reason)
    agent = Interrupted()
    agent.model = model
    with pytest.raises(RuntimeError, match="Investigation stopped"):
        invoke_structured(agent, "Inspect", Answer, investigate=True)
    assert model.calls == []


def test_external_agent_compatibility_and_non_native_provider():
    seen = []
    def external(prompt, **kwargs):
        seen.append((prompt, kwargs))
        return "external"
    assert invoke_structured(external, "prompt", Answer) == "external"
    assert seen == [("prompt", {"structured_output_model": Answer})]
    model = Provider("bedrock")
    assert invoke_structured(Agent(model=model, callback_handler=None), "Decide", Answer).structured_output.values == [3]
    assert [call[0] for call in model.calls] == ["tools"]


@pytest.mark.parametrize("error,expected", [
    (NotImplementedError(), True),
    (RuntimeError("response_format not supported"), True),
    (RuntimeError("json_schema unsupported"), True),
    (RuntimeError("format schema invalid"), False),
    (RuntimeError("context format unsupported"), False),
    (ConnectionError("offline"), False),
])
def test_native_unavailability_is_narrow(error, expected):
    assert native_unavailable(error) is expected


def test_native_adapter_requires_output_tool_and_delegates_configuration():
    model = Provider()
    adapter = NativeFinalizer(model, Answer)
    adapter.update_config(temperature=0)
    assert adapter.get_config()["temperature"] == 0
    assert adapter.host == model.host
    async def collect():
        return [event async for event in adapter.stream([], tool_specs=[])]
    with pytest.raises(ValueError, match="exactly"):
        asyncio.run(collect())
    async def direct():
        return [event async for event in adapter.structured_output(Answer, [])]
    assert asyncio.run(direct())[-1]["output"].values == [1, 2]


def test_native_adapter_rejects_missing_output():
    class Empty(Provider):
        async def structured_output(self, *args, **kwargs):
            if False:
                yield
    adapter = NativeFinalizer(Empty(), Answer)
    async def collect():
        return [event async for event in adapter.stream([], [StructuredOutputTool(Answer).tool_spec])]
    with pytest.raises(ValueError, match="no output"):
        asyncio.run(collect())


def test_finalization_preserves_registry_and_stops_at_artifact_cutoff():
    model = Provider()
    agent = Agent(model=model, callback_handler=None)
    registry = agent.tool_registry
    seen = []
    def check():
        seen.append(True)
        raise RuntimeError("artifact cutoff")
    with pytest.raises(RuntimeError, match="artifact cutoff"):
        invoke_structured(agent, "Inspect", Answer, investigate=True, before_finalize=check)
    assert seen == [True]
    assert [call[0] for call in model.calls] == ["tools"]
    assert agent.tool_registry is registry


def test_invalid_native_output_has_only_one_local_correction():
    model = Provider(failures=[ValueError("invalid"), ValueError("still invalid")])
    agent = Agent(model=model, callback_handler=None)
    registry = agent.tool_registry
    with pytest.raises(ValueError):
        invoke_structured(agent, "Review", Answer, investigate=True)
    assert [call[0] for call in model.calls] == ["tools", "native"]
    assert agent.tool_registry is registry
    assert agent.model is model


@pytest.mark.parametrize("message", ["response_format invalid schema", "json_schema unsupported schema keyword maxItems"])
def test_schema_rejection_does_not_disable_native_transport(message):
    assert not native_unavailable(RuntimeError(message))


def test_sdk_summary_retains_text_transport_during_native_finalization():
    from modules.config.models.generation_budget import summary_generation_budget

    model = Provider()
    adapter = NativeFinalizer(model, Answer)
    async def collect():
        with summary_generation_budget():
            return [event async for event in adapter.stream([], [])]
    events = asyncio.run(collect())
    assert any("contentBlockDelta" in event for event in events)
    assert [call[0] for call in model.calls] == ["tools"]
