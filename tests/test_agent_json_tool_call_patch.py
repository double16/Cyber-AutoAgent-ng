# Notes:
# - These tests intentionally stub a fake `strands.models.ollama.OllamaModel` module/class
#   so we can exercise patch_ollama_model_json_toolcalls without importing real Strands/Ollama.
# - Uses pytest + monkeypatch.

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass

import pytest

import modules.agents.patches as m


# -----------------------------
# Helpers for building fake modules/classes
# -----------------------------

@dataclass
class _FakeOllamaEventData:
    prompt_eval_count: int | None = None
    eval_count: int | None = None


def _install_fake_ollama_module(monkeypatch, *, cls):
    """
    Install a fake `modules.config.models.ollama` module into sys.modules with an OllamaModel class.
    Returns the module object.
    """
    # Ensure package chain exists.
    strands_mod = sys.modules.get("strands") or types.ModuleType("strands")
    models_mod = sys.modules.get("strands.models") or types.ModuleType("strands.models")
    ollama_mod = types.ModuleType("modules.config.models.ollama")

    setattr(ollama_mod, "OllamaModel", cls)

    monkeypatch.setitem(sys.modules, "strands", strands_mod)
    monkeypatch.setitem(sys.modules, "strands.models", models_mod)
    monkeypatch.setitem(sys.modules, "modules.config.models.ollama", ollama_mod)
    return ollama_mod


# -----------------------------
# Unit tests: low-level helpers
# -----------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("{}", 0),
        ('{"a": 1}', 0),
        ('{"a": {"b": 2}}', 0),
        ('{"a": {"b": 2}', 1),  # missing one closing }
        ('{"a": "}" }', 0),  # brace inside string ignored
        ('{"a": "\\"}" }', 0),  # escaped quote then brace inside string ignored
        ("}{", 0 - 0),  # depth goes negative then positive: final is 0, but malformed ordering not detected here
        ("{}}", -1),
    ],
)
def test__brace_balance(text, expected):
    assert m._brace_balance(text) == expected


@pytest.mark.parametrize(
    "buf,expected",
    [
        ("", False),
        ("   ", False),
        ('{"name":"t","arguments":{}}', True),
        ('{"name":"t","arguments":{"x":1}}', True),
        ('{"name":"t","arguments":{"x":1}', False),  # missing close brace
        ("not json", False),
    ],
)
def test__json_toolcall_complete_bare_json(buf, expected):
    assert m._json_toolcall_complete(buf) is expected


def test__extract_text_from_blocks():
    content = [{"text": "a"}, {"text": ""}, {"nope": "x"}, {"text": "b"}]
    assert m._extract_text_from_blocks(content) == "ab"
    assert m._extract_text_from_blocks({"text": "x"}) == ""


@pytest.mark.parametrize(
    "event,expected",
    [
        ({"contentBlockDelta": {"delta": {"text": "hi"}}}, "hi"),
        ({"contentBlockStart": {"start": {"text": "yo"}}}, "yo"),
        ({"content": [{"text": "a"}, {"text": "b"}]}, "ab"),
        ({"message": {"content": [{"text": "c"}]}}, "c"),
        ({"delta": {"content": [{"text": "d"}]}}, "d"),
        ({}, ""),
        ("nope", ""),
    ],
)
def test__extract_text_from_event(event, expected):
    assert m._extract_text_from_event(event) == expected


def test__clear_text_in_event_clears_all_supported_shapes():
    ev = {
        "contentBlockDelta": {"delta": {"text": "A"}},
        "contentBlockStart": {"start": {"text": "B"}},
        "content": [{"text": "C"}],
        "message": {"content": [{"text": "D"}]},
        "delta": {"content": [{"text": "E"}]},
    }
    m._clear_text_in_event(ev)

    assert ev["contentBlockDelta"]["delta"]["text"] == ""
    assert ev["contentBlockStart"]["start"]["text"] == ""
    assert ev["content"][0]["text"] == ""
    assert ev["message"]["content"][0]["text"] == ""
    assert ev["delta"]["content"][0]["text"] == ""


def test__clear_text_blocks_noop_on_non_list():
    obj = {"text": "x"}
    m._clear_text_blocks(obj)  # should not crash


# -----------------------------
# Unit tests: JSON toolcall extraction + coercion
# -----------------------------

def test__extract_json_toolcall_bare_json():
    txt = '{"name":"tool_x","arguments":{"a":1,"b":"c"}}'
    tc = m._extract_json_toolcall(txt)
    assert tc == {"name": "tool_x", "arguments": {"a": 1, "b": "c"}}


def test__extract_json_toolcall_accepts_parameters_alias():
    txt = '{"name":"tool_x","parameters":{"a":1}}'
    tc = m._extract_json_toolcall(txt)
    assert tc == {"name": "tool_x", "arguments": {"a": 1}}


def test__extract_json_toolcall_accepts_tool_call_wrapper():
    txt = '{"tool_call":{"name":"tool_x","arguments":{"a":1}}}'
    tc = m._extract_json_toolcall(txt)
    assert tc == {"name": "tool_x", "arguments": {"a": 1}}


def test__extract_json_toolcall_allow_missing_end_brace_recovery():
    # Missing final } in the *outer* object
    txt = '{"name":"tool_x","arguments":{"a":1}'
    assert m._extract_json_toolcall(txt) is None
    recovered = m._extract_json_toolcall(txt, allow_missing_end_brace=True)
    assert recovered == {"name": "tool_x", "arguments": {"a": 1}}


def test__to_openai_tool_calls_generates_expected_shape_and_serializes_args():
    tc_obj = {"name": "do_thing", "arguments": {"x": 1}}
    calls = m._to_openai_tool_calls(tc_obj, id_factory=lambda: "call_123")
    assert calls == [
        {
            "id": "call_123",
            "type": "function",
            "function": {"name": "do_thing", "arguments": json.dumps({"x": 1})},
        }
    ]


def test__coerce_message_json_toolcall_rewrites_message_in_place():
    msg = {
        "content": [
            {"text": '{"name":"t","arguments":{"x":1}}'},
        ]
    }
    assert m._coerce_message_json_toolcall(msg) is True
    assert msg["content"] == []
    assert msg["tool_calls"][0]["type"] == "function"
    assert msg["tool_calls"][0]["function"]["name"] == "t"
    assert json.loads(msg["tool_calls"][0]["function"]["arguments"]) == {"x": 1}


def test__coerce_message_json_toolcall_noop_if_tool_calls_present():
    msg = {
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
        "content": [{"text": '{"name":"t","arguments":{}}'}],
    }
    assert m._coerce_message_json_toolcall(msg) is False
    assert msg["tool_calls"][0]["function"]["name"] == "x"


def test__coerce_message_json_toolcall_ignores_non_matching_text():
    msg = {"content": [{"text": "hello world"}]}
    assert m._coerce_message_json_toolcall(msg) is False
    assert msg["content"][0]["text"] == "hello world"


# -----------------------------
# Unit tests: _JsonToolcallStreamState
# -----------------------------

def test__JsonToolcallStreamState_buffers_and_extracts_bare_toolcall():
    st = m._JsonToolcallStreamState()
    frag1 = '{"name":"t","arguments":'
    frag2 = '{"x":1}}'
    assert st.feed(frag1) is None
    assert st.is_buffering() is True
    tc = st.feed(frag2)
    assert tc == {"name": "t", "arguments": {"x": 1}}
    assert st.is_buffering() is False  # reset() called on success


def test__JsonToolcallStreamState_rejects_large_buffer():
    st = m._JsonToolcallStreamState(max_len=10)
    assert st.feed("{" * 11) is None
    assert st.pop_rejected() != ""
    assert st.is_buffering() is False


# -----------------------------
# Unit tests: patch_ollama_model_json_toolcalls (format_chunk path)
# -----------------------------

def test_patch_ollama_model_json_toolcalls_streaming_synthesizes_toolUse_events(monkeypatch):
    class OllamaModel:
        # Minimal original formatter: returns dict "out" unchanged, including streaming deltas.
        def format_chunk(self, event):
            return event["out"]

    _install_fake_ollama_module(monkeypatch, cls=OllamaModel)

    # Apply patch
    patched = m.patch_ollama_model_json_toolcalls(validate=True)
    assert patched is True

    model = OllamaModel()

    # 1) first chunk: begins JSON tool call, should be buffered and suppressed (text cleared)
    out1 = model.format_chunk({"out": {"contentBlockDelta": {"delta": {"text": '{"name":"t","arguments":'}}}})
    assert out1["contentBlockDelta"]["delta"]["text"] == ""

    # 2) second chunk: completes JSON tool call => should immediately emit contentBlockStart toolUse
    out2 = model.format_chunk({"out": {"contentBlockDelta": {"delta": {"text": '{"x":1}}'}}}})
    assert "contentBlockStart" in out2
    tu = out2["contentBlockStart"]["start"]["toolUse"]
    assert tu["name"] == "t"
    assert tu["toolUseId"] == "t"

    # 3) next call emits toolUse input delta (pending_stage=2 => delta)
    out3 = model.format_chunk({"out": {"contentBlockDelta": {"delta": {"text": "IGNORED"}}}})
    assert "contentBlockDelta" in out3
    dtu = out3["contentBlockDelta"]["delta"]["toolUse"]
    assert json.loads(dtu["input"]) == {"x": 1}

    # 4) provider stop arrives => patch replaces with messageStop stopReason tool_use
    out4 = model.format_chunk({"out": {"messageStop": {"stopReason": "stop"}}})
    assert out4 == {"messageStop": {"stopReason": "tool_use"}}


def test_patch_ollama_model_json_toolcalls_messageStop_recovers_missing_final_brace(monkeypatch):
    class OllamaModel:
        def format_chunk(self, event):
            return event["out"]

    _install_fake_ollama_module(monkeypatch, cls=OllamaModel)
    assert m.patch_ollama_model_json_toolcalls(validate=True) is True
    model = OllamaModel()

    # Start buffering with missing outer brace (brace balance = 1)
    _ = model.format_chunk({"out": {"contentBlockDelta": {"delta": {"text": '{"name":"t","arguments":{"x":1}'}}}})
    # Now messageStop arrives, patch should attempt allow_missing_end_brace=True and synthesize full tool_use triplet
    out = model.format_chunk({"out": {"messageStop": {"stopReason": "stop"}}})

    assert "contentBlockStart" in out
    assert "contentBlockDelta" in out
    assert "messageStop" in out
    assert out["messageStop"]["stopReason"] == "tool_use"
    tu = out["contentBlockStart"]["start"]["toolUse"]
    assert tu["name"] == "t"
    dtu = out["contentBlockDelta"]["delta"]["toolUse"]
    assert json.loads(dtu["input"]) == {"x": 1}


def test_patch_ollama_model_json_toolcalls_idempotent(monkeypatch):
    class OllamaModel:
        def format_chunk(self, event):
            return event["out"]

    _install_fake_ollama_module(monkeypatch, cls=OllamaModel)

    assert m.patch_ollama_model_json_toolcalls(validate=True) is True
    # second call should be a no-op and return False (already patched)
    assert m.patch_ollama_model_json_toolcalls(validate=True) is False


# -----------------------------
# Unit tests: patch_ollama_model_json_toolcalls (non-format_chunk formatter path)
# -----------------------------

def test_patch_ollama_model_json_toolcalls_format_response_rewrites_message(monkeypatch):
    class OllamaModel:
        def format_response(self, *_args, **_kwargs):
            return {
                "message": {"content": [{"text": '{"name":"t","arguments":{"x":1}}'}]},
                "stop_reason": "stop",
            }

    _install_fake_ollama_module(monkeypatch, cls=OllamaModel)
    assert m.patch_ollama_model_json_toolcalls(validate=True) is True

    model = OllamaModel()
    out = model.format_response()

    assert out["stop_reason"] == "tool_use"
    assert out["message"]["content"] == []
    assert out["message"]["tool_calls"][0]["function"]["name"] == "t"
    assert json.loads(out["message"]["tool_calls"][0]["function"]["arguments"]) == {"x": 1}


# -----------------------------
# Unit tests: patch_ollama_model_json_toolcalls (fallback invoke path)
# -----------------------------

def test_patch_ollama_model_json_toolcalls_fallback_invoke(monkeypatch):
    class OllamaModel:
        # No format_chunk/format_response/etc => should patch invoke
        def invoke(self, *_args, **_kwargs):
            # Return dict shape to exercise dict coercion
            return {"message": {"content": [{"text": '{"name":"t","arguments":{"x":1}}'}]}, "stop_reason": "stop"}

    _install_fake_ollama_module(monkeypatch, cls=OllamaModel)
    assert m.patch_ollama_model_json_toolcalls(validate=True) is True

    model = OllamaModel()
    out = model.invoke()

    assert out["stop_reason"] == "tool_use"
    assert out["message"]["content"] == []
    assert out["message"]["tool_calls"][0]["function"]["name"] == "t"
    assert json.loads(out["message"]["tool_calls"][0]["function"]["arguments"]) == {"x": 1}


# -----------------------------
# Extra: sanity test for _extract_json_toolcall fenced JSON (if/when supported)
# -----------------------------

def test__extract_json_toolcall_fenced_json_block():
    txt = "```json\n" + '{"name":"t","arguments":{"x":1}}' + "\n```"
    tc = m._extract_json_toolcall(txt)
    assert tc == {"name": "t", "arguments": {"x": 1}}
