"""Native response diagnostics are available before Strands can discard output."""

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from ollama import ChatResponse, Message
from pydantic import BaseModel

from modules.config.models import ollama as adapter
from modules.config.models import provider_capture as mod


def response(content="", thinking="reason", reason="length"):
    return ChatResponse(message=Message(role="assistant", content=content, thinking=thinking),
                        done_reason=reason, eval_count=4096, prompt_eval_count=8)


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("success,reason,retained", [(True, "stop", False), (True, "length", True),
                                                     (False, "stop", True), (True, "max_tokens", True)])
def test_capture_lifecycle(tmp_path, success, reason, retained):
    capture = mod.ProviderResponseCapture(tmp_path, "critic", "model")
    capture.request({"think": False, "stream": False, "options": {"num_predict": 4096},
                     "messages": ["private request"], "headers": {"Authorization": "private"}})
    capture.response(response(reason=reason))
    assert capture.path.stat().st_mode & 0o777 == 0o600
    capture.finish(success)
    capture.finish(success)
    assert capture.path.exists() is retained
    if retained:
        data = records(capture.path)
        assert data[1]["think"] is False
        assert data[2]["data"]["message"]["thinking"] == "reason"
        assert data[-1]["usage"]["eval_count"] == 4096
        assert "private request" not in capture.path.read_text()


def test_disabled_capture_and_bad_serialization(tmp_path):
    disabled = mod.ProviderResponseCapture(None, "critic", "model")
    disabled.request({})
    disabled.response(object())
    disabled.finish(False)
    capture = mod.ProviderResponseCapture(tmp_path, "critic", "model")
    capture.response(object())
    assert capture.file is None
    capture.finish(False)


def test_limit_retains_terminal_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "MAX_RESPONSE_BYTES", 700)
    capture = mod.ProviderResponseCapture(tmp_path, "critic", "model")
    capture.response(response("small"))
    capture.response(response("x" * 1000))
    capture.finish(False)
    data = records(capture.path)
    assert data[-1]["truncated"]
    assert data[-1]["stop_reason"] == "length"
    assert capture.bytes_written <= 700


def test_filesystem_and_telemetry_failures_are_nonfatal(tmp_path, monkeypatch):
    blocked = tmp_path / "file"
    blocked.write_text("occupied")
    assert mod.ProviderResponseCapture(blocked, "critic", "model").file is None
    capture = mod.ProviderResponseCapture(tmp_path / "ok", "critic", "model")
    capture.file = Mock()
    capture.file.write.side_effect = OSError("disk full")
    capture.request({})
    assert capture.file is None
    capture = mod.ProviderResponseCapture(tmp_path / "trace", "critic", "model")
    monkeypatch.setattr(mod.trace, "get_tracer", Mock(side_effect=RuntimeError("offline")))
    capture.finish(False)
    assert capture.path.exists()


def test_trace_excerpt_redacted_and_invocations_isolated(tmp_path, monkeypatch):
    exported = []

    @contextmanager
    def span(name, attributes):
        exported.append(attributes)
        yield

    monkeypatch.setattr(mod.trace, "get_tracer", lambda _: Mock(start_as_current_span=span))
    one = mod.ProviderResponseCapture(tmp_path, "critic", "model")
    two = mod.ProviderResponseCapture(tmp_path, "executor", "model")
    one.response(response("password=topsecret"))
    two.response(response("second"))
    one.finish(False)
    two.finish(False)
    assert one.path != two.path
    assert "topsecret" in one.path.read_text()
    assert "topsecret" not in json.dumps(exported)
    assert "second" not in one.path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_adapter_captures_before_conversion_and_stop(tmp_path, monkeypatch, streaming):
    native = response(thinking="native reasoning")

    async def events():
        yield native

    client = Mock(chat=AsyncMock(return_value=events() if streaming else native), aclose=AsyncMock())
    monkeypatch.setattr(adapter.ollama, "AsyncClient", lambda *a, **k: client)
    model = adapter.OllamaModel(None, model_id="model", stream=streaming)
    model.capture_directory = tmp_path
    model.capture_role = "task_prompt_critic"
    original = model.format_chunk

    def convert(event):
        if event["chunk_type"] == "content_delta":
            assert "native reasoning" in next(tmp_path.glob("*.jsonl")).read_text()
        if event["chunk_type"] == "message_stop":
            assert records(next(tmp_path.glob("*.jsonl")))[-1]["type"] == "end"
        return original(event)

    monkeypatch.setattr(model, "format_chunk", convert)
    chunks = [chunk async for chunk in model.stream([])]
    assert {"messageStop": {"stopReason": "max_tokens"}} in chunks
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["conversion", "provider", "interrupted", "success"])
async def test_adapter_failure_cleanup(tmp_path, monkeypatch, failure):
    client = Mock(chat=AsyncMock(return_value=response(reason="stop")), aclose=AsyncMock())
    if failure == "provider":
        client.chat.side_effect = RuntimeError("offline")
    monkeypatch.setattr(adapter.ollama, "AsyncClient", lambda *a, **k: client)
    model = adapter.OllamaModel(None, model_id="model", stream=False)
    model.capture_directory = tmp_path
    if failure == "conversion":
        original = model.format_chunk
        model.format_chunk = lambda event: (_ for _ in ()).throw(ValueError("bad")) if (
            event["chunk_type"] == "content_delta") else original(event)
    if failure in {"conversion", "provider"}:
        with pytest.raises((ValueError, RuntimeError)):
            _ = [chunk async for chunk in model.stream([])]
    elif failure == "interrupted":
        generator = model.stream([])
        await anext(generator)
        await generator.aclose()
    else:
        _ = [chunk async for chunk in model.stream([])]
    assert bool(list(tmp_path.glob("*.jsonl"))) is (failure != "success")
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_structured_capture_before_validation(tmp_path, monkeypatch, valid):
    class Output(BaseModel):
        answer: int

    client = Mock(chat=AsyncMock(return_value=response('{"answer":7}' if valid else 'invalid', reason="stop")),
                  aclose=AsyncMock())
    monkeypatch.setattr(adapter.ollama, "AsyncClient", lambda *a, **k: client)
    model = adapter.OllamaModel(None, model_id="model")
    model.capture_directory = tmp_path
    if valid:
        assert [chunk async for chunk in model.structured_output(Output, [])] == [{"output": Output(answer=7)}]
        assert not list(tmp_path.glob("*.jsonl"))
    else:
        with pytest.raises(ValueError):
            _ = [chunk async for chunk in model.structured_output(Output, [])]
        assert "invalid" in next(tmp_path.glob("*.jsonl")).read_text()
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_effective_fallback_settings(tmp_path, monkeypatch):
    client = Mock(chat=AsyncMock(side_effect=[RuntimeError("think must be boolean"), response()]), aclose=AsyncMock())
    monkeypatch.setattr(adapter.ollama, "AsyncClient", lambda *a, **k: client)
    model = adapter.OllamaModel(None, model_id="capture-fallback", additional_args={"think": "medium"})
    model.capture_directory = tmp_path
    _ = [chunk async for chunk in model.stream([])]
    settings = [item for item in records(next(tmp_path.glob("*.jsonl"))) if item["type"] == "settings"]
    assert settings[0]["think"] == "medium"
    assert settings[1]["think"] is True


def test_capture_close_failures_and_valid_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.trace, "get_current_span", lambda: Mock(
        get_span_context=lambda: Mock(is_valid=True, trace_id=123, span_id=456)))
    capture = mod.ProviderResponseCapture(tmp_path, "critic", "model")
    assert records(capture.path)[0]["trace_id"] == format(123, "032x")
    original = capture.file
    capture.file = Mock()
    capture.file.write.side_effect = OSError("full")
    capture.file.close.side_effect = OSError("close failed")
    capture.request({})
    assert capture.file is None
    original.close()


@pytest.mark.asyncio
async def test_stream_partial_failure_is_retained(tmp_path, monkeypatch):
    async def events():
        yield ChatResponse(message=Message(role="assistant", content="partial"), done=False)
        raise RuntimeError("disconnected")

    client = Mock(chat=AsyncMock(return_value=events()), aclose=AsyncMock())
    monkeypatch.setattr(adapter.ollama, "AsyncClient", lambda *a, **k: client)
    model = adapter.OllamaModel(None, model_id="model", stream=True)
    model.capture_directory = tmp_path
    with pytest.raises(RuntimeError, match="disconnected"):
        _ = [chunk async for chunk in model.stream([])]
    data = records(next(tmp_path.glob("*.jsonl")))
    assert data[-2]["data"]["message"]["content"] == "partial"
    assert not data[-1]["complete"]
    client.aclose.assert_awaited_once()
