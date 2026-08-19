"""Unit and integration tests for single-parameter fallback, Ollama think transitions, and learned cache."""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.config.models.agent_profiles import (
    get_agent_settings_registry,
    reset_agent_settings_registry,
)
from modules.config.models.capabilities import (
    apply_parameter_fallback_to_model,
    classify_parameter_error,
    wrap_model_with_fallback,
)
from modules.config.models.ollama import OllamaModel


def test_classify_parameter_error():
    assert classify_parameter_error(ValueError("unsupported parameter: temperature")) == "temperature"
    assert classify_parameter_error(RuntimeError("Model does not support temperature setting")) == "temperature"
    assert classify_parameter_error(Exception("invalid parameter top_k")) == "top_k"
    assert classify_parameter_error(ValueError("top_p is not allowed with this configuration")) == "top_p"
    assert classify_parameter_error(Exception("unknown option think")) == "think"
    assert classify_parameter_error(RuntimeError("unsupported reasoning_effort parameter")) == "reasoning_effort"
    assert classify_parameter_error(ValueError("thinking budget is not supported")) == "thinking"
    assert classify_parameter_error(RuntimeError("effort level is invalid")) == "effort"
    assert classify_parameter_error(RuntimeError("generic network timeout")) is None


def test_apply_parameter_fallback_ollama():
    model = OllamaModel(
        host="http://localhost:11434",
        model_id="qwen",
        temperature=0.7,
        top_k=40,
        top_p=0.9,
        additional_args={"think": "medium"},
        options={"top_k": 40, "top_p": 0.9, "temperature": 0.7},
    )

    # Fallback 1: think string -> bool
    applied = apply_parameter_fallback_to_model(model, "ollama", "qwen", "think")
    assert applied is True
    assert model.config["additional_args"]["think"] is True

    # Fallback 2: think bool -> omit
    applied = apply_parameter_fallback_to_model(model, "ollama", "qwen", "think")
    assert applied is True
    assert "think" not in model.config["additional_args"]

    # Fallback 3: top_k
    applied = apply_parameter_fallback_to_model(model, "ollama", "qwen", "top_k")
    assert applied is True
    assert model.config["top_k"] is None
    assert "top_k" not in model.config["options"]

    # Fallback 4: temperature
    applied = apply_parameter_fallback_to_model(model, "ollama", "qwen", "temperature")
    assert applied is True
    assert model.config["temperature"] is None
    assert "temperature" not in model.config["options"]


@pytest.mark.asyncio
async def test_wrap_model_with_fallback_progressive_retry():
    reset_agent_settings_registry()
    registry = get_agent_settings_registry()

    # Mock a model that fails on 1st attempt with top_k error, then succeeds on 2nd attempt
    attempts = [0]

    class FakeModel:
        def __init__(self):
            self.params = {"temperature": 0.7, "top_k": 50}
            self.client_args = {}

        async def stream(self, *args, **kwargs) -> AsyncGenerator:
            attempts[0] += 1
            if attempts[0] == 1:
                raise ValueError("unsupported parameter: top_k is not allowed")
            yield {"contentBlockDelta": {"delta": {"text": "hello"}}}

    fake = FakeModel()
    wrapped = wrap_model_with_fallback(fake, "litellm", "test-model")

    events = []
    async for event in wrapped.stream("prompt"):
        events.append(event)

    assert attempts[0] == 2
    assert len(events) == 1
    assert events[0]["contentBlockDelta"]["delta"]["text"] == "hello"
    assert "top_k" not in fake.params

    # Verify that the constraint was recorded in the registry
    records = registry.export_adjustment_records()
    assert len(records) == 1
    assert records[0].parameter_name == "top_k"


@pytest.mark.asyncio
async def test_ollama_chat_with_fallback_think_and_options():
    reset_agent_settings_registry()
    registry = get_agent_settings_registry()

    model = OllamaModel(
        host="http://localhost:11434",
        model_id="deepseek-r1",
        additional_args={"think": "medium"},
        options={"top_k": 50},
    )

    client_mock = AsyncMock()
    call_count = [0]

    async def fake_chat(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # String think failed
            raise ValueError("invalid think option: expected boolean")
        if call_count[0] == 2:
            # top_k rejected
            raise ValueError("unsupported option top_k")
        # 3rd attempt succeeds
        res = MagicMock()
        res.message.content = "recovered answer"
        res.message.thinking = None
        res.message.tool_calls = []
        res.done_reason = "stop"
        res.prompt_eval_count = 10
        res.eval_count = 5
        res.total_duration = 1000000
        return res

    client_mock.chat = fake_chat

    res = await model._chat_with_fallback(client_mock, model.format_request([{"role": "user", "content": [{"text": "hi"}]}]))
    assert call_count[0] == 3
    assert res.message.content == "recovered answer"

    fallbacks = registry.get_learned_fallbacks("ollama", "deepseek-r1")
    assert "think" in fallbacks
    assert "top_k" in fallbacks
