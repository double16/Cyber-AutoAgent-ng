"""Emergency recovery preserves evidence while releasing inline context."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.handlers import conversation_budget as cb
from modules.handlers.context_recovery import _controller_record, compact_recent_results, preserve_reasoning_turn


def result_message(payload):
    return {"role": "user", "content": [{"toolResult": {
        "toolUseId": "call-1", "status": "success", "content": [{"json": payload}],
    }}]}


@pytest.mark.parametrize("payload", [
    {"response": "HTTP response " * 600},
    {"ports": [{"port": number, "service": "example"} for number in range(200)]},
    {"source": "def example(): pass\n" * 600},
])
def test_six_message_deadlock_archives_recent_result(tmp_path, payload):
    messages = [{"role": "user", "content": [{"text": "frozen task"}]}]
    messages.extend({"role": "assistant", "content": [{"text": "context"}]} for _ in range(3))
    messages.append({"role": "assistant", "content": [{"toolUse": {
        "toolUseId": "call-1", "name": "inspect", "input": {},
    }}]})
    messages.append(result_message(payload))
    before = copy.deepcopy(messages)
    def estimate():
        return len(json.dumps(messages))
    assert compact_recent_results(messages, tmp_path, 2500, estimate, set()) == 1
    assert estimate() <= 2500
    assert messages[:-1] == before[:-1]
    result = messages[-1]["content"][0]["toolResult"]
    assert result["toolUseId"] == "call-1" and result["status"] == "success"
    notice = result["content"][0]["json"]
    path = tmp_path / notice["artifact_ref"].removeprefix("artifact:")
    assert json.loads(path.read_text()) == before[-1]["content"][0]["toolResult"]["content"]
    assert compact_recent_results(messages, tmp_path, 2500, estimate, set()) == 0


@pytest.mark.parametrize("value,expected", [
    ({"criterion_id": "c", "details": "x"}, True),
    ([{"nested": {"receipt_uid": "r"}}], True),
    ('{"acceptance_results": []}', True),
    ("[invalid", False), ("plain", False), (None, False),
])
def test_controller_records(value, expected):
    assert _controller_record(value) is expected


def test_failure_and_protection_leave_payloads_unchanged(tmp_path, monkeypatch):
    messages = [result_message("x" * 5000), result_message({"criterion_id": "c", "data": "x" * 5000}),
                result_message("x" * 5000), {"role": "system", "content": [{"text": "system"}]},
                {"role": "assistant", "content": ["unknown", {"text": "keep"}]},
                result_message("short"), result_message("y" * 5000), result_message(b"binary")]
    original = copy.deepcopy(messages)
    def fail(*args, **kwargs):
        raise OSError("unavailable")
    monkeypatch.setattr(Path, "write_text", fail)
    assert compact_recent_results(messages, tmp_path, 1, lambda: 10000, {2}) == 0
    assert messages == original
    assert compact_recent_results(messages, tmp_path, 1, lambda: None, set()) == 0


def test_reasoning_preserved_across_tool_result_and_removed_for_ollama():
    signed = {"reasoningContent": {"reasoningText": {"text": "reason", "signature": "signed"}}}
    messages = [
        {"role": "assistant", "content": [copy.deepcopy(signed), {"text": "old"}]},
        {"role": "assistant", "content": [copy.deepcopy(signed), {"text": "new"}]},
        result_message("result"),
    ]
    agent = SimpleNamespace(messages=messages, _allow_reasoning_content=True)
    cb._strip_reasoning_content(agent, force=True)
    assert messages[0]["content"] == [{"text": "old"}]
    assert messages[1]["content"][0] == signed
    agent._allow_reasoning_content = False
    context = cb._get_agent_input_context(agent)
    assert context.messages == messages
    assert messages[1]["content"] == [{"text": "new"}]


def test_emergency_budget_hook_recovers_recent_results(tmp_path, monkeypatch):
    from modules.tools import memory

    messages = [{"role": "user", "content": [{"text": "task"}]}, result_message("x" * 6000)]
    agent = SimpleNamespace(
        name="test", messages=messages, model=SimpleNamespace(_output_tokens=100),
        _prompt_token_limit=3000, _allow_reasoning_content=False, system_prompt="system",
        conversation_manager=SimpleNamespace(reduce_context=lambda agent: None),
    )
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(cb, "safe_estimate_tokens", lambda agent: len(json.dumps(agent.messages)))
    monkeypatch.setattr(cb, "get_model_id_from_agent", lambda agent: "test")
    cb._ensure_prompt_within_budget(agent)
    assert len(json.dumps(messages)) < 2900
    assert list((tmp_path / "artifacts/context_reduction").glob("*.json"))


@pytest.mark.parametrize("metadata", [None, {"custom": {"existing": "value"}}])
def test_reasoning_pin_is_temporary_and_preserves_sdk_tool_pair(metadata):
    from strands.agent.conversation_manager.compression.pin_message import is_pinned

    messages = [{"role": "assistant", "content": [
        {"reasoningContent": {"redactedContent": "encrypted"}},
        {"toolUse": {"toolUseId": "call-1", "name": "inspect", "input": {}}},
    ]}, result_message("result")]
    if metadata is not None:
        messages[0]["metadata"] = copy.deepcopy(metadata)
    with pytest.raises(ValueError), preserve_reasoning_turn(messages):
        assert is_pinned(messages, 0) and is_pinned(messages, 1)
        raise ValueError("reduction failed")
    assert messages[0].get("metadata") == metadata
    with preserve_reasoning_turn([]):
        pass


def test_reasoning_does_not_inflate_disabled_replay_estimate():
    agent = SimpleNamespace(
        name="test", model=SimpleNamespace(), _allow_reasoning_content=False,
        messages=[{"role": "assistant", "content": [
            {"reasoningContent": {"reasoningText": {"text": "x" * 50000}}}, {"text": "answer"},
        ]}],
    )
    assert cb.safe_estimate_tokens(agent) < 100
    assert agent.messages == [{"role": "assistant", "content": [{"text": "answer"}]}]


def test_sliding_fallback_preserves_reasoning_and_tool_exchange():
    messages = [{"role": "user", "content": [{"text": "task"}]}]
    signed = {"role": "assistant", "content": [
        {"reasoningContent": {"reasoningText": {"text": "reason", "signature": "signed"}}},
        {"toolUse": {"toolUseId": "call-1", "name": "inspect", "input": {}}},
    ]}
    result = result_message("required result")
    messages.extend([signed, result])
    messages.extend({"role": "user", "content": [{"text": str(index)}]} for index in range(8))
    agent = SimpleNamespace(messages=messages)
    manager = cb.SlidingWindowConversationManagerWithPreservation(window_size=3)
    manager._force_preservation_trim(agent)
    assert signed in messages and result in messages
    assert messages[0]["content"] == [{"text": "task"}]
    assert len(messages) == 6
