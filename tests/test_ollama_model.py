import re

import pytest
from pydantic import BaseModel
from strands.types.exceptions import ContextWindowOverflowException

from modules.config.models import ollama as mod


def _model():
    return mod.OllamaModel(
        host="http://ollama.test",
        model_id="llama3",
        max_tokens=128,
        temperature=0.2,
        top_p=0.9,
        stop_sequences=["STOP"],
        keep_alive="5m",
        options={"num_ctx": 4096},
        additional_args={"extra": "value"},
    )


def _non_streaming_model():
    return mod.OllamaModel(
        host="http://ollama.test",
        model_id="llama3",
        max_tokens=128,
        stream=False,
    )


def test_format_request_flattens_messages_tools_and_options():
    model = _model()
    messages = [
        {"role": "user", "content": [{"text": "hello"}]},
        {
            "role": "assistant",
            "content": [{"toolUse": {"toolUseId": "scan", "input": {"target": "x"}}}],
        },
        {
            "role": "user",
            "content": [{"toolResult": {"content": [{"json": {"ok": True}}]}}],
        },
    ]
    tool_specs = [
        {
            "name": "scan",
            "description": "Run scan",
            "inputSchema": {"json": {"type": "object"}},
        }
    ]

    request = model.format_request(messages, tool_specs, system_prompt="be useful")

    assert request["model"] == "llama3"
    assert request["messages"][0] == {"role": "system", "content": "be useful"}
    assert request["messages"][1] == {"role": "user", "content": "hello"}
    assert request["messages"][2]["tool_calls"][0]["function"] == {
        "name": "scan",
        "arguments": {"target": "x"},
    }
    assert request["messages"][3] == {"role": "tool", "content": '{"ok": true}'}
    assert request["tools"][0]["function"]["name"] == "scan"
    assert request["stream"] is True
    assert request["options"] == {
        "num_ctx": 4096,
        "num_predict": 128,
        "temperature": 0.2,
        "top_p": 0.9,
        "stop": ["STOP"],
    }
    assert request["keep_alive"] == "5m"
    assert request["extra"] == "value"


def test_format_request_uses_tool_name_when_it_differs_from_tool_use_id():
    model = _model()

    request = model.format_request(
        [
            {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "name": "scan",
                            "toolUseId": "tooluse_0123456789abcdef01234567",
                            "input": {"target": "x"},
                        }
                    }
                ],
            }
        ]
    )

    assert request["messages"] == [
        {
            "role": "assistant",
            "tool_calls": [{"function": {"name": "scan", "arguments": {"target": "x"}}}],
        }
    ]


def test_format_request_uses_configured_non_streaming_mode():
    model = _non_streaming_model()

    request = model.format_request([{"role": "user", "content": [{"text": "hello"}]}])

    assert request["stream"] is False


def test_format_request_supports_reasoning_and_images_and_rejects_unknown_content():
    model = _model()

    assert model._format_request_message_contents("assistant", {"reasoningContent": {"text": "why"}}) == [
        {"role": "assistant", "reasoningContent": {"text": "why"}}
    ]
    assert model._format_request_message_contents(
        "user", {"image": {"source": {"bytes": b"image-bytes"}}}
    ) == [{"role": "user", "images": [b"image-bytes"]}]

    with pytest.raises(TypeError, match="unsupported type"):
        model._format_request_message_contents("user", {"document": "bad"})


def test_format_chunk_translates_all_supported_chunk_types():
    model = _model()

    tool_call = type(
        "ToolCall",
        (),
        {"function": type("Function", (), {"name": "scan", "arguments": {"x": 1}})()},
    )()
    metadata = type(
        "Meta",
        (),
        {"prompt_eval_count": "3", "eval_count": "4", "total_duration": 5_000_000},
    )()

    assert model.format_chunk({"chunk_type": "message_start"}) == {"messageStart": {"role": "assistant"}}
    assert model.format_chunk({"chunk_type": "content_start", "data_type": "text"}) == {
        "contentBlockStart": {"start": {}}
    }
    tool_start = model.format_chunk({"chunk_type": "content_start", "data_type": "tool", "data": tool_call})
    tool_use = tool_start["contentBlockStart"]["start"]["toolUse"]
    assert tool_use["name"] == "scan"
    assert re.fullmatch(r"tooluse_[0-9a-f]{24}", tool_use["toolUseId"])
    assert model.format_chunk({"chunk_type": "content_delta", "data_type": "tool", "data": tool_call}) == {
        "contentBlockDelta": {"delta": {"toolUse": {"input": '{"x": 1}'}}}
    }
    assert model.format_chunk({"chunk_type": "content_delta", "data_type": "reasoning_text", "data": "think"}) == {
        "contentBlockDelta": {"delta": {"reasoningContent": {"text": "think"}}}
    }
    assert model.format_chunk({"chunk_type": "content_delta", "data_type": "text", "data": "hi"}) == {
        "contentBlockDelta": {"delta": {"text": "hi"}}
    }
    assert model.format_chunk({"chunk_type": "content_stop"}) == {"contentBlockStop": {}}
    assert model.format_chunk({"chunk_type": "message_stop", "data": "tool_use"}) == {
        "messageStop": {"stopReason": "tool_use"}
    }
    assert model.format_chunk({"chunk_type": "message_stop", "data": "length"}) == {
        "messageStop": {"stopReason": "max_tokens"}
    }
    assert model.format_chunk({"chunk_type": "message_stop", "data": "stop"}) == {
        "messageStop": {"stopReason": "end_turn"}
    }
    assert model.format_chunk({"chunk_type": "metadata", "data": metadata}) == {
        "metadata": {
            "usage": {"inputTokens": 3, "outputTokens": 4, "totalTokens": 7},
            "metrics": {"latencyMs": 5},
        }
    }

    with pytest.raises(RuntimeError, match="unknown type"):
        model.format_chunk({"chunk_type": "bad"})


def test_metadata_chunk_returns_empty_for_missing_or_invalid_counts():
    model = _model()

    missing = type("Meta", (), {"prompt_eval_count": None, "eval_count": 1, "total_duration": 1})()
    invalid = type("Meta", (), {"prompt_eval_count": "bad", "eval_count": 1, "total_duration": 1})()

    assert model.format_chunk({"chunk_type": "metadata", "data": missing}) == {}
    assert model.format_chunk({"chunk_type": "metadata", "data": invalid}) == {}


@pytest.mark.asyncio
async def test_structured_output_parses_non_streaming_response(monkeypatch):
    class Output(BaseModel):
        answer: int

    class FakeClient:
        def __init__(self, host, **kwargs):
            self.host = host
            self.kwargs = kwargs

        async def chat(self, **request):
            assert request["stream"] is False
            assert request["format"] == Output.model_json_schema()
            return type("Response", (), {"message": type("Msg", (), {"content": '{"answer": 7}'})()})()

    monkeypatch.setattr(mod.ollama, "AsyncClient", FakeClient)
    model = _model()

    chunks = [chunk async for chunk in model.structured_output(Output, [{"role": "user", "content": [{"text": "go"}]}])]

    assert chunks == [{"output": Output(answer=7)}]


@pytest.mark.asyncio
async def test_stream_handles_non_streaming_chat_response(monkeypatch):
    response = mod.ChatResponse(
        message=mod.ollama.Message(role="assistant", content="done", thinking="thought"),
        done_reason="stop",
        prompt_eval_count=2,
        eval_count=3,
        total_duration=4_000_000,
    )

    class FakeClient:
        async def chat(self, **request):
            assert request["stream"] is False
            return response

    monkeypatch.setattr(mod.ollama, "AsyncClient", lambda host, **kwargs: FakeClient())

    chunks = [chunk async for chunk in _non_streaming_model().stream([{"role": "user", "content": [{"text": "go"}]}])]

    assert chunks == [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {"start": {}}},
        {"contentBlockDelta": {"delta": {"text": "done"}}},
        {"contentBlockDelta": {"delta": {"reasoningContent": {"text": "thought"}}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "end_turn"}},
        {
            "metadata": {
                "usage": {"inputTokens": 2, "outputTokens": 3, "totalTokens": 5},
                "metrics": {"latencyMs": 4},
            }
        },
    ]


@pytest.mark.asyncio
async def test_stream_preserves_tool_name_with_generated_tool_use_id(monkeypatch):
    tool_call = mod.ollama.Message.ToolCall(
        function=mod.ollama.Message.ToolCall.Function(name="scan", arguments={"target": "x"})
    )
    response = mod.ChatResponse(
        message=mod.ollama.Message(role="assistant", content="", tool_calls=[tool_call]),
        done_reason="stop",
        prompt_eval_count=2,
        eval_count=3,
        total_duration=4_000_000,
    )

    class FakeClient:
        async def chat(self, **request):
            return response

    monkeypatch.setattr(mod.ollama, "AsyncClient", lambda host, **kwargs: FakeClient())

    chunks = [chunk async for chunk in _non_streaming_model().stream([{"role": "user", "content": [{"text": "go"}]}])]

    tool_use = chunks[2]["contentBlockStart"]["start"]["toolUse"]
    assert tool_use["name"] == "scan"
    assert re.fullmatch(r"tooluse_[0-9a-f]{24}", tool_use["toolUseId"])
    assert chunks[3] == {"contentBlockDelta": {"delta": {"toolUse": {"input": '{"target": "x"}'}}}}
    assert chunks[-2] == {"messageStop": {"stopReason": "tool_use"}}


@pytest.mark.asyncio
async def test_stream_raises_value_error_for_invalid_response_type(monkeypatch):
    class FakeClient:
        async def chat(self, **request):
            return object()

    monkeypatch.setattr(mod.ollama, "AsyncClient", lambda host, **kwargs: FakeClient())

    with pytest.raises(ValueError, match="Invalid response type"):
        async for _chunk in _non_streaming_model().stream([{"role": "user", "content": [{"text": "go"}]}]):
            pass


@pytest.mark.asyncio
async def test_structured_output_raises_value_error_for_invalid_json(monkeypatch):
    class Output(BaseModel):
        answer: int

    class FakeClient:
        async def chat(self, **request):
            return type("Response", (), {"message": type("Msg", (), {"content": "not-json"})()})()

    monkeypatch.setattr(mod.ollama, "AsyncClient", lambda host, **kwargs: FakeClient())
    model = _model()

    with pytest.raises(ValueError, match="Failed to parse"):
        async for _chunk in model.structured_output(Output, [{"role": "user", "content": [{"text": "go"}]}]):
            pass


@pytest.mark.asyncio
async def test_structured_output_translates_context_overflow_response_error(monkeypatch):
    class Output(BaseModel):
        answer: int

    class FakeClient:
        async def chat(self, **request):
            raise mod.ollama.ResponseError("The prompt is longer than the context length", status_code=400)

    monkeypatch.setattr(mod.ollama, "AsyncClient", lambda host, **kwargs: FakeClient())

    with pytest.raises(ContextWindowOverflowException, match="context length"):
        async for _chunk in _model().structured_output(Output, [{"role": "user", "content": [{"text": "go"}]}]):
            pass


@pytest.mark.asyncio
async def test_structured_output_preserves_non_overflow_response_error(monkeypatch):
    class Output(BaseModel):
        answer: int

    class FakeClient:
        async def chat(self, **request):
            raise mod.ollama.ResponseError("model is unavailable", status_code=503)

    monkeypatch.setattr(mod.ollama, "AsyncClient", lambda host, **kwargs: FakeClient())

    with pytest.raises(mod.ollama.ResponseError, match="model is unavailable"):
        async for _chunk in _model().structured_output(Output, [{"role": "user", "content": [{"text": "go"}]}]):
            pass
