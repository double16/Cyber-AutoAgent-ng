import asyncio
import base64
import contextlib
import sys
import time
from types import SimpleNamespace

import pytest

from modules.handlers.utils import get_tool_spec

MODULE_UNDER_TEST = "modules.tools.channels"

mod = __import__(MODULE_UNDER_TEST, fromlist=["*"])


# Common helpers

def b64_to_bytes(ev):
    return base64.b64decode(ev.data_b64) if getattr(ev, "data_b64", None) else b""


async def poll_until(channel_id, pred, timeout=3.0):
    """Poll repeatedly until pred(events) is True or timeout; returns accumulated events."""
    start = time.time()
    collected = []
    while time.time() - start < timeout:
        res = await mod.channel_poll(
            channel_id=channel_id,
            timeout=0.25,
            max_events=1024,
            min_events=1,
        )
        collected.extend(res.events)
        if pred(collected):
            return collected
    return collected


# Forward channel tests (Docker-free by mocking create_subprocess_exec)

ECHO_CODE = (
    "import sys\n"
    "print('ready', flush=True)\n"
    "for line in sys.stdin:\n"
    "    sys.stdout.write(line)\n"
    "    sys.stdout.flush()\n"
)


@pytest.fixture
def mock_subprocess(monkeypatch):
    orig = asyncio.create_subprocess_exec

    async def fake_create_subprocess_exec(*args, **kwargs):
        # Ignore the 'docker ... image ... cmd...' invocation and run a simple echo script instead
        return await orig(
            sys.executable, "-u", "-c", ECHO_CODE,
            stdin=kwargs.get("stdin"),
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
            limit=kwargs.get("limit", 2 ** 16),
            env=kwargs.get("env"),
            cwd=kwargs.get("cwd"),
        )

    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    yield
    # restore implicitly when fixture exits


@pytest.mark.asyncio
async def test_forward_create_status_send_poll_close(mock_subprocess):
    # Create forward channel
    res = await mod.channel_create_forward(command="/bin/bash -lc 'echo ready; while true; do read L; echo $L; done'")
    cid = res.channel_id
    assert res.kind == "forward"
    assert isinstance(res.pid, int) and res.pid > 0

    # Status should be connected and ready
    s = await mod.channel_status(channel_id=cid)
    assert s.kind == "forward"
    assert s.connected is True
    assert s.ready_for_send is True
    assert "pid" in s.details

    # Poll for the "ready" line from the echo script
    evs = await poll_until(cid, lambda es: any(b"ready" in b64_to_bytes(e) for e in es if e.stream == "output"))
    assert any(b"ready" in b64_to_bytes(e) for e in evs if e.stream == "output")

    # Send a line; expect it echoed back
    await mod.channel_send(channel_id=cid, mode="text", data="ping", append_newline=True)
    evs = await poll_until(cid, lambda es: any(b"ping" in b64_to_bytes(e) for e in es if e.stream == "output"))
    assert any(b"ping" in b64_to_bytes(e) for e in evs if e.stream == "output")

    # Close channel
    closed = await mod.channel_close(channel_id=cid)
    assert closed.success is True

    # After close, channel_status should raise (channel removed)
    with pytest.raises(KeyError):
        await mod.channel_status(channel_id=cid)


@pytest.mark.asyncio
async def test_poll_timeout_returns_quickly(mock_subprocess):
    # Create forward channel and poll with short timeout; ensure it doesn't hang
    res = await mod.channel_create_forward(command="bash -lc true")
    cid = res.channel_id
    out = await mod.channel_poll(channel_id=cid, timeout=0.010, max_events=10, min_events=1)
    assert isinstance(out.events, list)
    await mod.channel_close(channel_id=cid)


# Reverse channel tests

@pytest.mark.asyncio
async def test_reverse_connect_duplex_send_both_ways_and_close():
    r = await mod.channel_create_reverse(target=None, listener_host="127.0.0.1", listener_port=0)
    cid = r.channel_id
    assert r.listen_port > 0
    assert r.listen_address == "127.0.0.1"

    # Before client connect
    s0 = await mod.channel_status(channel_id=cid)
    assert s0.connected is False
    assert s0.details.get("listening") == "true"
    assert s0.details.get("port") == str(r.listen_port)

    # Connect a client
    reader, writer = await asyncio.open_connection(r.listen_address, r.listen_port)

    # Wait for server to report 'client_connected'
    evs = await poll_until(cid,
                           lambda es: any((e.stream == "status" and (e.note or "") == "client_connected") for e in es))
    assert any((e.stream == "status" and (e.note or "") == "client_connected") for e in evs)

    # Server → client
    await mod.channel_send(channel_id=cid, mode="text", data="srv2cli", append_newline=True)
    line = await asyncio.wait_for(reader.readline(), timeout=1.0)
    assert line.strip() == b"srv2cli"

    # Client → server
    writer.write(b"cli2srv\n")
    await writer.drain()
    evs = await poll_until(cid, lambda es: any(b"cli2srv" in b64_to_bytes(e) for e in es if e.stream == "output"))
    assert any(b"cli2srv" in b64_to_bytes(e) for e in evs if e.stream == "output")

    # Close server side and then client
    closed = await mod.channel_close(channel_id=cid)
    assert closed.success is True
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_reverse_send_when_not_connected_returns_zero():
    r = await mod.channel_create_reverse(target=None, listener_host="127.0.0.1", listener_port=0)
    cid = r.channel_id
    out = await mod.channel_send(channel_id=cid, mode="text", data="hello", append_newline=False)
    assert out.bytes_sent == 0
    await mod.channel_close(channel_id=cid)


# Close-all & cleanup

@pytest.mark.asyncio
async def test_close_all(mock_subprocess):
    r1 = await mod.channel_create_forward(command="bash -lc 'echo a'")
    r2 = await mod.channel_create_forward(command="bash -lc 'echo b'")
    res = await mod.channel_close_all()
    assert res["closed"] >= 2

    # Any subsequent status on the old channels should fail
    for cid in (r1.channel_id, r2.channel_id):
        with pytest.raises(KeyError):
            await mod.channel_status(channel_id=cid)


@pytest.mark.asyncio
async def test_channel_manager_poll_send_status_and_close(monkeypatch):
    manager = mod.ChannelManager()
    ch = manager.add(mod.Channel(id="manual", kind="forward"))
    await ch.put_event(mod.PollEvent(ts=1.0, stream="output", data_b64="aGVsbG8="))
    await ch.mark_status("ready")
    assert manager.get("manual") is ch

    monkeypatch.setattr(mod, "_CHANNEL_MANAGER", manager)
    poll = await mod.channel_poll("manual", timeout=0, max_events=5)
    assert poll.events[0].data_b64 == "aGVsbG8="
    status = await mod.channel_status("manual")
    assert status.kind == "forward"
    with pytest.raises(KeyError):
        await mod.channel_send("missing", "x")
    assert (await mod.channel_close("manual")).success is True
    assert (await mod.channel_close("manual")).success is False
    assert (await mod.channel_close_all())["closed"] == 0


@pytest.mark.asyncio
async def test_channel_reverse_send_status_and_reader_errors(monkeypatch):
    class Server:
        sockets = []

        def close(self):
            self.closed = True

        async def wait_closed(self):
            pass

    manager = mod.ChannelManager()
    reverse = manager.add(mod.Channel(id="rev", kind="reverse", server=Server()))
    monkeypatch.setattr(mod, "_CHANNEL_MANAGER", manager)

    disconnected = await mod.channel_status("rev")
    assert disconnected.ready_for_send is False
    assert (await mod.channel_send("rev", "abc")).bytes_sent == 0
    assert (await mod.channel_poll("rev", timeout=0, max_events=5)).events[0].note == "client_not_connected"

    class Writer:
        def __init__(self):
            self.payload = b""

        def is_closing(self):
            return False

        def write(self, payload):
            self.payload += payload

        async def drain(self):
            pass

    writer = Writer()
    reverse._client_writer = writer
    sent = await mod.channel_send("rev", "aGk=", mode="base64", append_newline=True)
    assert sent.bytes_sent == 2
    assert writer.payload == b"hi"
    connected = await mod.channel_status("rev")
    assert connected.connected is True

    class BadReader:
        async def read(self, _chunk):
            raise RuntimeError("read failed")

    await mod._read_output(BadReader(), reverse)
    events = await mod.channel_poll("rev", timeout=0, max_events=10)
    assert any(ev.note and ev.note.startswith("output_reader_error") for ev in events.events)


@pytest.mark.asyncio
async def test_channel_send_normalizes_semantic_mode_aliases(monkeypatch):
    class Writer:
        def __init__(self):
            self.payload = b""

        def is_closing(self):
            return False

        def write(self, payload):
            self.payload += payload

        async def drain(self):
            pass

    manager = mod.ChannelManager()
    channel = manager.add(mod.Channel(id="alias-channel", kind="reverse"))
    writer = Writer()
    channel._client_writer = writer
    monkeypatch.setattr(mod, "_CHANNEL_MANAGER", manager)

    sent = await mod.channel_send("alias-channel", "aGk=", mode="b64")

    assert sent.bytes_sent == 2
    assert writer.payload == b"hi"


@pytest.mark.asyncio
async def test_channel_send_rejects_unknown_semantic_mode():
    with pytest.raises(ValueError, match="mode must be"):
        await mod.channel_send("missing", "x", mode="hex")


def test_channel_send_runtime_schema_accepts_aliases_and_advertises_canonical_values():
    validated = mod.channel_send._metadata.validate_input({
        "channel_id": "channel-1",
        "data": "aGk=",
        "mode": "b64",
    })

    assert validated["mode"] == "b64"
    schema = get_tool_spec(mod.channel_send)["inputSchema"]["json"]
    assert schema["properties"]["mode"]["enum"] == ["text", "base64"]


@pytest.mark.asyncio
async def test_channel_orchestration_handles_reverse_listener_and_broken_forward_pipe(monkeypatch):
    """Exercise listener selection, duplicate clients, and transport failures without binding sockets."""
    class Server:
        sockets = [SimpleNamespace(getsockname=lambda: ("10.0.0.5", 4545))]

        def close(self):
            return None

        async def wait_closed(self):
            return None

    captured = {}

    async def start_server(callback, host, port):
        captured.update(callback=callback, host=host, port=port)
        return Server()

    monkeypatch.setattr(mod, "_CHANNEL_MANAGER", mod.ChannelManager())
    monkeypatch.setattr(mod, "pick_local_addr", lambda _target: ("10.0.0.5", "en0"))
    monkeypatch.setattr(mod.asyncio, "start_server", start_server)
    reverse = await mod.channel_create_reverse(target="target.test")
    assert reverse.listen_address == "10.0.0.5"
    assert reverse.listen_port == 4545

    class DuplicateWriter:
        closed = False

        def close(self):
            self.closed = True

        async def wait_closed(self):
            return None

    channel = mod._mgr().get(reverse.channel_id)
    channel._client_writer = object()
    duplicate = DuplicateWriter()
    await captured["callback"](SimpleNamespace(), duplicate)
    assert duplicate.closed is True

    class ClosedStdin:
        def write(self, _payload):
            raise BrokenPipeError()

        async def drain(self):
            return None

        def is_closing(self):
            return True

    forward = mod._mgr().add(
        mod.Channel(
            id="broken-forward",
            kind="forward",
            proc=SimpleNamespace(stdin=ClosedStdin(), returncode=None, pid=1),
        )
    )
    sent = await mod.channel_send(forward.id, "payload")
    assert sent.bytes_sent == 0
    events = await mod.channel_poll(forward.id, timeout=0, max_events=10)
    assert events.events[-1].note == "stdin_closed"
