import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace

from modules.handlers.events.emitters import StdoutEventEmitter, get_emitter


def _events(output):
    parts = output.split("__CYBER_EVENT__")[1:]
    return [json.loads(part.split("__CYBER_EVENT_END__", 1)[0]) for part in parts]


def test_output_event_with_dict_content_is_stringified_before_signature():
    emitter = StdoutEventEmitter(operation_id="OP")
    buf = io.StringIO()

    with redirect_stdout(buf):
        emitter.emit({"type": "output", "content": {"answer": 42}})

    [event] = _events(buf.getvalue())
    assert event["content"] == '{"answer": 42}'


def test_unserializable_non_output_event_is_cleaned_for_json_and_signature():
    emitter = StdoutEventEmitter(operation_id="OP")
    buf = io.StringIO()
    payload = SimpleNamespace(value=("tuple", object()))

    with redirect_stdout(buf):
        emitter.emit({"type": "metadata", "payload": payload})

    [event] = _events(buf.getvalue())
    assert event["type"] == "metadata"
    assert event["payload"]["value"][0] == "tuple"
    assert isinstance(event["payload"]["value"][1], str)


def test_tool_events_are_not_deduplicated():
    emitter = StdoutEventEmitter(operation_id="OP")
    buf = io.StringIO()

    with redirect_stdout(buf):
        emitter.emit({"type": "tool_start", "tool_name": "shell"})
        emitter.emit({"type": "tool_start", "tool_name": "shell"})

    assert len(_events(buf.getvalue())) == 2


def test_get_emitter_uses_environment_and_unknown_transport_falls_back(monkeypatch):
    monkeypatch.setenv("EVENT_TRANSPORT", "not-real")

    emitter = get_emitter(operation_id="ENV_OP")

    assert isinstance(emitter, StdoutEventEmitter)
    assert emitter.operation_id == "ENV_OP"
