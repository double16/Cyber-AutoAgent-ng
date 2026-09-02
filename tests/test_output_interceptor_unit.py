import io
import json
import sys

import pytest

from modules.handlers import output_interceptor as mod


@pytest.fixture(autouse=True)
def reset_interceptor_state():
    while mod.is_in_tool_execution():
        mod.set_tool_execution_state(False)
    mod.get_buffered_output()
    mod.get_buffered_error_output()
    yield
    while mod.is_in_tool_execution():
        mod.set_tool_execution_state(False)
    mod.get_buffered_output()
    mod.get_buffered_error_output()


def _payloads(stream):
    parts = stream.getvalue().split("__CYBER_EVENT__")[1:]
    return [json.loads(part.split("__CYBER_EVENT_END__", 1)[0]) for part in parts]


def test_interceptor_emits_complete_lines_and_flushes_partial_line():
    stream = io.StringIO()
    interceptor = mod.OutputInterceptor(stream, "output")

    assert interceptor.write("MISSION PARAMETERS\npartial") == len("MISSION PARAMETERS\npartial")
    interceptor.flush()

    payloads = _payloads(stream)
    assert [payload["type"] for payload in payloads] == ["initialization", "output"]
    assert [payload["content"] for payload in payloads] == ["MISSION PARAMETERS", "partial"]
    assert payloads[0]["metadata"] == {"source": "python_backend"}


def test_structured_events_pass_through_without_wrapping():
    stream = io.StringIO()
    interceptor = mod.OutputInterceptor(stream, "output")
    event = "__CYBER_EVENT__{\"type\":\"existing\"}__CYBER_EVENT_END__\n"

    assert interceptor.write(event) == len(event)

    assert stream.getvalue() == event


def test_tool_execution_buffers_stdout_and_stderr_until_read():
    stdout = mod.OutputInterceptor(io.StringIO(), "output")
    stderr = mod.OutputInterceptor(io.StringIO(), "error")

    mod.set_tool_execution_state(True)
    stdout.write("line one\n")
    stderr.write("bad news\n")
    mod.set_tool_execution_state(False)

    assert mod.get_buffered_output() == "line one"
    assert mod.get_buffered_error_output() == "bad news"
    assert mod.get_buffered_output() == ""
    assert mod.get_buffered_error_output() == ""


def test_intercept_output_replaces_streams_only_in_react_mode(monkeypatch):
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    monkeypatch.setenv("CYBER_UI_MODE", "cli")
    with mod.intercept_output():
        assert sys.stdout is original_stdout
        assert sys.stderr is original_stderr

    monkeypatch.setenv("CYBER_UI_MODE", "react")
    with mod.intercept_output():
        assert isinstance(sys.stdout, mod.OutputInterceptor)
        assert isinstance(sys.stderr, mod.OutputInterceptor)

    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr


@pytest.mark.parametrize(
    ("content", "expected_type"),
    [
        ("────────────────────", "separator"),
        ("✅ completed", "status"),
        ("ordinary", "output"),
    ],
)
def test_interceptor_classifies_special_output(content, expected_type):
    stream = io.StringIO()
    interceptor = mod.OutputInterceptor(stream)

    interceptor.write(f"{content}\n")

    assert _payloads(stream)[0]["type"] == expected_type


def test_interceptor_recursion_delegates_and_stream_capabilities_are_preserved():
    stream = io.StringIO()
    interceptor = mod.OutputInterceptor(stream)
    interceptor._in_event_emission = True

    assert interceptor.write("raw") == 3
    assert stream.getvalue() == "raw"
    assert interceptor.readable() is False
    assert interceptor.writable() is True
    assert interceptor.seekable() is False
    assert interceptor.isatty() is False


def test_interceptor_buffers_nested_tool_execution_and_flushes_error():
    stderr = mod.OutputInterceptor(io.StringIO(), "error")
    mod.set_tool_execution_state(True)
    mod.set_tool_execution_state(True)
    stderr.write("first\nsecond\n")
    mod.set_tool_execution_state(False)
    assert mod.is_in_tool_execution() is True
    mod.set_tool_execution_state(False)

    assert mod.get_buffered_error_output() == "first\nsecond"


def test_setup_output_interception_installs_streams_and_print_wrapper(monkeypatch):
    original_stdout, original_stderr = sys.stdout, sys.stderr
    original_print = __import__("builtins").print
    stream = io.StringIO()
    monkeypatch.setenv("CYBER_UI_MODE", "react")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)

    mod.setup_output_interception()
    try:
        __import__("builtins").print("hello", "world", flush=True)
        assert _payloads(stream)[0]["content"] == "hello world"
    finally:
        monkeypatch.setattr(__import__("builtins"), "print", original_print)
        monkeypatch.setattr(sys, "stdout", original_stdout)
        monkeypatch.setattr(sys, "stderr", original_stderr)
