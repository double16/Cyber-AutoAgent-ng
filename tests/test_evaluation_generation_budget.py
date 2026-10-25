"""Final evaluation transport allocation for application and library-generated prompts."""

import asyncio
from copy import deepcopy

import pytest
from langchain_core.messages import HumanMessage
from strands.types.exceptions import ContextWindowOverflowException

from modules.evaluation.generation_budget import attach_evaluation_budget


class Chat:
    def __init__(self):
        self.num_predict = 99
        self.num_ctx = 500
        self.calls = []

    def _generate(self, messages, **kwargs):
        self.calls.append((self.num_predict, self.num_ctx, deepcopy(kwargs)))
        return "done"

    async def _agenerate(self, messages, **kwargs):
        await asyncio.sleep(0)
        return self._generate(messages, **kwargs)

    def _stream(self, messages, **kwargs):
        yield self._generate(messages, **kwargs)

    async def _astream(self, messages, **kwargs):
        yield await self._agenerate(messages, **kwargs)


@pytest.mark.parametrize("provider,key", [
    ("ollama", None), ("gemini", "max_output_tokens"), ("litellm", "max_tokens"), ("bedrock", "max_tokens"),
])
def test_all_entrypoints_allocate_final_prompt_without_mutating_model(monkeypatch, provider, key):
    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens", lambda *_a, **_kw: 35000)
    model = Chat()
    attach_evaluation_budget(model, provider, "fixture", 40000, 8192, None)
    messages = [HumanMessage(content="Full Ragas generated prompt")]
    assert model._generate(messages) == "done"
    assert asyncio.run(model._agenerate(messages)) == "done"
    assert list(model._stream(messages)) == ["done"]

    async def run():
        return [event async for event in model._astream(messages)]

    assert asyncio.run(run()) == ["done"]
    assert len(model.calls) == 4
    for num_predict, num_ctx, kwargs in model.calls:
        if key:
            assert kwargs[key] == 4200
        else:
            assert (num_predict, num_ctx) == (4200, 40000)
            assert "max_tokens" not in kwargs
    assert model.num_predict == 99
    assert model.num_ctx == 500


def test_explicit_cap_bound_override_options_and_unknown_method(monkeypatch):
    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens", lambda *_a, **_kw: 100)
    model = Chat()
    attach_evaluation_budget(model, "ollama", "fixture", 40000, 800, 700)
    with pytest.raises(ContextWindowOverflowException):
        model._generate([HumanMessage(content="task")])
    assert not model.calls
    attach_evaluation_budget(model, "ollama", "fixture", 40000, 800, 5000)
    model._generate([HumanMessage(content="task")], max_tokens=2000, options={"temperature": 0.2})
    assert model.calls[0][2]["options"] == {"temperature": 0.2, "num_predict": 2000, "num_ctx": 40000}
    model._generate([HumanMessage(content="task")], max_tokens=None)
    assert model.calls[1][0] == 800
    attach_evaluation_budget(object(), "ollama", "fixture", 40000, 800, None)


def test_structured_schemas_count_and_required_cardinality_cannot_be_truncated(monkeypatch):
    seen = []
    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens",
                        lambda *args, **kwargs: seen.append((args, kwargs)) or 100)
    model = Chat()
    attach_evaluation_budget(model, "litellm", "fixture", 40000, 4096, None)
    schema = {"type": "object", "required": ["rows"], "properties": {
        "rows": {"type": "array", "minItems": 385, "items": {"type": "string"}},
    }}
    tools = [{"type": "function", "function": {"parameters": schema}}]
    with pytest.raises(ContextWindowOverflowException):
        model._generate([HumanMessage(content="task")], tools=tools)
    assert "385" in seen[0][1]["extra_content"]
    assert not model.calls


def test_real_ollama_wire_options_are_dynamic(monkeypatch):
    from langchain_ollama import ChatOllama

    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens", lambda *_a, **_kw: 35000)
    monkeypatch.setattr(ChatOllama, "_generate", lambda self, messages, **kwargs: self._chat_params(messages, **kwargs))
    model = ChatOllama(model="fixture", reasoning=False, num_predict=8192)
    attach_evaluation_budget(model, "ollama", "fixture", 40000, 8192, None)
    wire = model._generate([HumanMessage(content="task")], max_tokens=8192)
    assert wire["options"]["num_predict"] == 4200
    assert wire["options"]["num_ctx"] == 40000
    assert wire["think"] is False
    assert model.num_predict == 8192


@pytest.mark.parametrize("format_key", ["format", "response_format", "functions"])
def test_native_evaluation_schema_answer_floor(format_key, monkeypatch):
    monkeypatch.setattr("modules.handlers.conversation_budget.estimate_prompt_tokens", lambda *_a, **_kw: 100)
    model = Chat()
    attach_evaluation_budget(model, "ollama", "fixture", 40000, 4096, None)
    schema = {"type": "array", "minItems": 385, "items": {"type": "string"}}
    formats = {"format": schema, "response_format": {"json_schema": {"schema": schema}},
               "functions": [{"parameters": schema}]}
    with pytest.raises(ContextWindowOverflowException):
        model._generate([HumanMessage(content="task")], **{format_key: formats[format_key]})
    assert not model.calls
