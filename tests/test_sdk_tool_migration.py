"""Runtime contract checks for migrated SDK tools."""

from types import SimpleNamespace

import httpx
import pytest
from strands.vended_tools import sleep

from modules.tools.editor import create_absolute_path_editor
from modules.tools.http_request import http_request
from modules.tools.python_repl import python_repl


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 302, 403, 500])
async def test_http_request_preserves_raw_response_and_request_data(monkeypatch, status):
    async def respond(request):
        assert request.method == "POST"
        assert request.headers["x-marker"] == "test"
        assert request.content == b"payload"
        return httpx.Response(status, headers={"X-Result": "test"}, text="control response")

    client_type = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["trust_env"] is True
        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr("modules.tools.http_request.httpx.AsyncClient", client)
    result = await http_request(
        method="post",
        url="https://example.test",
        headers={"X-Marker": "test"},
        body="payload",
    )
    assert result["status"] == status
    assert result["headers"]["x-result"] == "test"
    assert result["body"] == "control response"


@pytest.mark.asyncio
async def test_http_transport_failures_are_tool_errors(monkeypatch):
    async def fail(request):
        raise httpx.ConnectError("unavailable")

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "modules.tools.http_request.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(fail), **kwargs),
    )
    with pytest.raises(httpx.ConnectError, match="unavailable"):
        await http_request(method="GET", url="https://example.test")


def test_schemas_match_available_commands_and_injected_context():
    from strands.vended_tools import file_editor

    for selected in (python_repl, create_absolute_path_editor(file_editor)):
        schema = selected.tool_spec["inputSchema"]["json"]
        assert "tool_context" not in schema["properties"]
    assert "reset_state" not in python_repl.tool_spec["inputSchema"]["json"]["properties"]


@pytest.mark.asyncio
async def test_sleep_duration_schema_and_validation():
    await sleep(duration=0.001)
    with pytest.raises(ValueError):
        await sleep(duration=-1)
    assert "duration" in sleep.tool_spec["inputSchema"]["json"]["properties"]


@pytest.mark.asyncio
async def test_repl_propagates_timeout_and_cancel_signal():
    from strands.sandbox.errors import SandboxTimeoutError

    signal = object()

    class TimedOut:
        async def execute_code(self, code, language, *, timeout, cancel_signal):
            assert timeout == 3
            assert cancel_signal is signal
            raise SandboxTimeoutError(3, stdout="partial output")

    context = SimpleNamespace(agent=SimpleNamespace(sandbox=TimedOut()), cancel_signal=signal)
    with pytest.raises(SandboxTimeoutError, match="partial output"):
        await python_repl(code="pass", timeout=3, tool_context=context)


@pytest.mark.asyncio
@pytest.mark.parametrize("method, expected", [(" get ", "success"), ("TRACE", "error")])
async def test_http_runtime_schema_normalizes_methods_and_rejects_unknown(monkeypatch, method, expected):
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="ok"))
    monkeypatch.setattr(
        "modules.tools.http_request.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )
    events = [
        event
        async for event in http_request.stream(
            {
                "toolUseId": "method-test",
                "name": "http_request",
                "input": {
                    "method": method,
                    "url": "https://example.test",
                },
            },
            {"agent": SimpleNamespace()},
        )
    ]
    assert events[-1]["tool_result"]["status"] == expected
    schema = http_request.tool_spec["inputSchema"]["json"]
    assert schema["properties"]["method"]["enum"] == ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
async def test_http_rejects_invalid_timeouts(timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        await http_request(method="GET", url="https://example.test", timeout=timeout)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_after_request", [False, True])
async def test_http_cancellation_before_and_during_body(monkeypatch, cancel_after_request):
    from threading import Event

    signal = Event()
    if not cancel_after_request:
        signal.set()

    def respond(request):
        signal.set()
        return httpx.Response(200, text="body")

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "modules.tools.http_request.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(RuntimeError, match="cancelled"):
        await http_request(
            method="GET",
            url="https://example.test",
            tool_context=SimpleNamespace(cancel_signal=signal),
        )
