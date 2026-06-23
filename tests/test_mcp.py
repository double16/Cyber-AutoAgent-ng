import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from strands.types.exceptions import MCPClientInitializationError

from modules.tools.mcp import shorten_description
from modules.tools import mcp as mod


class ShortenEnglishTests(unittest.TestCase):
    def test_returns_original_when_shorter_than_max_len(self):
        text = "Short text."
        result = shorten_description(text, 50)
        self.assertEqual(result, text)

    def test_cuts_at_sentence_boundary_before_max_len(self):
        text = (
            "This is a long paragraph of English text. "
            "It contains several sentences. "
            "We want to shorten it cleanly without breaking sentences if possible!"
        )
        result = shorten_description(text, 60)
        # Should end on a sentence terminator before the limit
        self.assertTrue(result.endswith("."))
        self.assertIn("This is a long paragraph of English text.", result)
        self.assertNotIn("It contains several sentences.", result)

    def test_falls_back_to_word_boundary_when_no_sentence_terminator(self):
        text = "This is a sentence without punctuation at the end and it is quite long"
        result = shorten_description(text, 35)
        # Should not exceed max_len
        self.assertLessEqual(len(result), 35)
        # Should end on a space boundary, not in the middle of a word
        self.assertTrue(result[-1].isalpha())
        self.assertTrue(result.endswith("sentence") or result.endswith("without"))

    def test_hard_cut_when_no_spaces(self):
        text = "averyverylongwordwithnospaces"
        result = shorten_description(text, 10)
        self.assertEqual(result, text[:10])

    def test_exact_length_returns_original(self):
        text = "Exactly twenty-five chars."
        max_len = len(text)
        result = shorten_description(text, max_len)
        self.assertEqual(result, text)

    def test_leading_and_trailing_whitespace_trimmed(self):
        text = "   This is a test sentence.   "
        result = shorten_description(text, 50)
        self.assertEqual(result, "This is a test sentence.")

    def test_small_max_len(self):
        text = "Hello world."
        result = shorten_description(text, 3)
        self.assertEqual(result, "Hel")


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


def test_discover_mcp_tools_registers_allowed_tools_and_missing(monkeypatch, capsys):
    class ToolPage(list):
        pagination_token = None

    class FakeTool:
        tool_name = "srv_allowed"
        tool_spec = {"description": "Useful. Extra sentence."}

    class FakeMCPClient:
        def __init__(self, transport, prefix):
            self.transport = transport
            self.prefix = prefix
            self.started = False

        def start(self):
            self.started = True

        def stop(self, *_):
            self.started = False

        def list_tools_sync(self, page_token=None):
            return ToolPage([FakeTool()])

    monkeypatch.setattr(mod, "MCPClient", FakeMCPClient)
    monkeypatch.setattr(mod, "start_managed_mcp_client", lambda client: lambda: client.stop())
    monkeypatch.setattr(mod.atexit, "register", lambda *_: None)
    monkeypatch.setattr(mod.signal, "signal", lambda *_: None)
    monkeypatch.setattr(mod, "stdio_client", lambda *_args, **_kwargs: "stdio")
    monkeypatch.setattr(mod, "streamablehttp_client", lambda **_kwargs: "http")
    monkeypatch.setattr(mod, "sse_client", lambda **_kwargs: "sse")

    config = SimpleNamespace(
        module="web",
        mcp_connections=[
            SimpleNamespace(
                id="srv",
                plugins=["web"],
                headers={"Authorization": "Bearer ${TOKEN}"},
                transport="stdio",
                command=["cmd", "${TOKEN}"],
                server_url="",
                timeoutSeconds=None,
                allowed_tools=["allowed", "missing"],
            )
        ],
    )
    monkeypatch.setenv("TOKEN", "secret")

    tools = mod.discover_mcp_tools(config)

    assert len(tools) == 1
    assert tools[0].tool_name == "srv_allowed"
    out = capsys.readouterr().out
    assert "tool_available" in out
    assert "tool_unavailable" in out


if __name__ == "__main__":
    unittest.main()
