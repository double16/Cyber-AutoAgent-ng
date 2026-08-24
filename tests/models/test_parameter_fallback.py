"""Unit and integration tests for single-parameter fallback, Ollama think transitions, and learned cache."""

from collections.abc import AsyncGenerator
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
    assert classify_parameter_error(Exception("unknown option think")) is None
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

    # Ollama retries a rejected string think level with its boolean equivalent.
    applied = apply_parameter_fallback_to_model(model, "ollama", "qwen", "think")
    assert applied is True
    assert model.config["additional_args"]["think"] is True

    # A rejected enabled boolean then falls back to False without removing think.
    assert apply_parameter_fallback_to_model(model, "ollama", "qwen", "think") is True
    assert model.config["additional_args"]["think"] is False

    # Fallback 1: top_k
    applied = apply_parameter_fallback_to_model(model, "ollama", "qwen", "top_k")
    assert applied is True
    assert model.config["top_k"] is None
    assert "top_k" not in model.config["options"]

    # Fallback 2: temperature
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
async def test_ollama_chat_with_fallback_retries_string_then_boolean_and_learns_boolean():
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
    requests = []

    async def fake_chat(**kwargs):
        call_count[0] += 1
        requests.append(kwargs)
        if call_count[0] == 1:
            raise ValueError("unsupported think parameter")
        # The boolean retry succeeds.
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
    assert call_count[0] == 2
    assert res.message.content == "recovered answer"
    assert requests[0]["think"] == "medium"
    assert requests[1]["think"] is True

    fallbacks = registry.get_learned_fallbacks("ollama", "deepseek-r1")
    assert fallbacks["think"] is True
    assert "top_k" not in fallbacks


@pytest.mark.asyncio
async def test_ollama_chat_with_fallback_retries_enabled_boolean_then_false():
    reset_agent_settings_registry()
    registry = get_agent_settings_registry()
    model = OllamaModel(
        host="http://localhost:11434",
        model_id="deepseek-r1",
        additional_args={"think": "xhigh"},
    )
    client_mock = AsyncMock()
    requests = []

    async def fake_chat(**kwargs):
        requests.append(kwargs)
        if len(requests) < 3:
            raise ValueError("unsupported think parameter")
        response = MagicMock()
        response.message.content = "recovered answer"
        response.message.thinking = None
        response.message.tool_calls = []
        response.done_reason = "stop"
        response.prompt_eval_count = 10
        response.eval_count = 5
        response.total_duration = 1000000
        return response

    client_mock.chat = fake_chat

    await model._chat_with_fallback(client_mock, model.format_request([{"role": "user", "content": [{"text": "hi"}]}]))

    assert [request["think"] for request in requests] == ["xhigh", True, False]
    assert registry.get_learned_fallbacks("ollama", "deepseek-r1")["think"] is False


@pytest.mark.asyncio
async def test_ollama_chat_with_fallback_retries_low_string_directly_to_false():
    model = OllamaModel(
        host="http://localhost:11434",
        model_id="deepseek-r1",
        additional_args={"think": "low"},
    )
    client_mock = AsyncMock()
    requests = []

    async def fake_chat(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            raise ValueError("unsupported think parameter")
        response = MagicMock()
        response.message.content = "recovered answer"
        response.message.thinking = None
        response.message.tool_calls = []
        response.done_reason = "stop"
        response.prompt_eval_count = 10
        response.eval_count = 5
        response.total_duration = 1000000
        return response

    client_mock.chat = fake_chat

    await model._chat_with_fallback(client_mock, model.format_request([{"role": "user", "content": [{"text": "hi"}]}]))

    assert [request["think"] for request in requests] == ["low", False]


@pytest.mark.asyncio
async def test_ollama_chat_with_fallback_never_removes_disabled_think():
    model = OllamaModel(
        host="http://localhost:11434",
        model_id="qwen",
        additional_args={"think": False},
    )
    client_mock = AsyncMock()
    client_mock.chat.side_effect = ValueError("unsupported think parameter")
    request = model.format_request([{"role": "user", "content": [{"text": "hi"}]}])

    with pytest.raises(ValueError, match="unsupported think parameter"):
        await model._chat_with_fallback(client_mock, request)

    client_mock.chat.assert_awaited_once_with(**request)
    assert request["think"] is False


@pytest.mark.asyncio
async def test_ollama_recursion_error_is_terminal_and_preserves_think():
    model = OllamaModel(
        host="http://localhost:11434",
        model_id="qwen",
        additional_args={"think": False},
        options={"top_k": 50},
    )
    client_mock = AsyncMock()
    client_mock.chat.side_effect = RecursionError("maximum recursion depth exceeded")
    request = model.format_request([{"role": "user", "content": [{"text": "hi"}]}])

    with pytest.raises(RecursionError, match="maximum recursion depth exceeded"):
        await model._chat_with_fallback(client_mock, request)

    client_mock.chat.assert_awaited_once()
    assert request["think"] is False
