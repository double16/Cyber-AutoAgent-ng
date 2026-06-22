import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from strands.types.exceptions import MCPClientInitializationError

from modules.tools import mcp as mod


def test_env_var_resolution_handles_none_known_and_unknown_values():
    env = {"TOKEN": "secret", "HOST": "localhost"}

    assert mod.resolve_env_vars_in_dict(None, env) == {}
    assert mod.resolve_env_vars_in_list(None, env) == []
    assert mod.resolve_env_vars_in_dict({"h": "Bearer ${TOKEN}", "x": "${MISSING}"}, env) == {
        "h": "Bearer secret",
        "x": "${MISSING}",
    }
    assert mod.resolve_env_vars_in_list(["${HOST}:8080", "${MISSING}"], env) == [
        "localhost:8080",
        "${MISSING}",
    ]


def test_keepalive_start_stop_and_ping(monkeypatch):
    monkeypatch.setattr(mod, "MCP_HEARTBEAT_INTERVAL", 1)

    client = SimpleNamespace()
    handle = mod._start_keepalive(client)

    assert handle is mod._start_keepalive(client)
    mod._stop_keepalive(client)
    assert getattr(client, "_cyber_keepalive_handle") is None


def test_send_ping_requires_active_session():
    client = Mock()
    client._is_session_active.return_value = False

    with pytest.raises(MCPClientInitializationError):
        mod._send_ping(client)


@pytest.mark.asyncio
async def test_resilient_adapter_restarts_and_retries_recoverable_errors(monkeypatch):
    events = [{"ok": True}]
    calls = {"stream": 0, "restart": 0}

    class Inner:
        tool_name = "demo"
        tool_spec = {"name": "demo"}

        async def stream(self, *_args, **_kwargs):
            calls["stream"] += 1
            if calls["stream"] == 1:
                raise RuntimeError("MCP server was closed")
            for event in events:
                yield event

    monkeypatch.setattr(mod, "_restart_client", lambda _client: calls.__setitem__("restart", calls["restart"] + 1))
    async def fast_sleep(*_):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(mod, "MCP_MAX_RETRIES", 2)
    monkeypatch.setattr(mod, "MCP_RESTART_BACKOFF", 0)

    adapter = mod.ResilientMCPToolAdapter(Inner(), Mock())

    assert [event async for event in adapter.stream({}, {})] == events
    assert calls == {"stream": 2, "restart": 1}


@pytest.mark.asyncio
async def test_resilient_adapter_raises_nonrecoverable_errors():
    class Inner:
        tool_name = "demo"
        tool_spec = {}

        async def stream(self, *_args, **_kwargs):
            raise ValueError("bad")
            yield

    adapter = mod.ResilientMCPToolAdapter(Inner(), Mock())

    with pytest.raises(ValueError):
        [event async for event in adapter.stream({}, {})]


def test_discover_mcp_tools_no_connections_emits_ready_event(capsys):
    config = SimpleNamespace(mcp_connections=[], module="web")

    assert mod.discover_mcp_tools(config) == []
    captured = capsys.readouterr().out
    assert "tool_discovery_start" in captured
    assert "environment_ready" in captured
