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
