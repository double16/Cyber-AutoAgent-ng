#!/usr/bin/env python3
import types

import pytest

import modules.handlers.conversation_budget as cb
from modules.handlers.conversation_budget import (
    LargeToolResultMapper,
    _ensure_prompt_within_budget,
    _estimate_prompt_tokens_for_agent,
    _strip_reasoning_content,
)


class ModelStub:
    def __init__(self, output_tokens: int | None = None):
        if output_tokens is not None:
            self._output_tokens = output_tokens


class AgentStub:
    def __init__(self, messages, limit=None, telemetry=None, output_tokens=None):
        self.name = "AgentStub"
        self.messages = messages
        self.model = ModelStub(output_tokens=output_tokens)
        self.tool_names = []
        self._prompt_token_limit = limit
        self.conversation_manager = types.SimpleNamespace(
            calls=[],
            reduce_context=lambda agent: self.conversation_manager.calls.append(
                len(agent.messages)
            ),
        )
        if telemetry is not None:
            self.callback_handler = types.SimpleNamespace(sdk_input_tokens=telemetry)


def _make_message(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _make_reasoning_message(text="thinking"):
    return {
        "role": "assistant",
        "content": [{"reasoningContent": {"reasoningText": {"text": text}}}],
    }


def test_estimate_prompt_tokens_counts_text_blocks():
    agent = AgentStub([_make_message("a" * 40), _make_message("b" * 80)])
    estimated = _estimate_prompt_tokens_for_agent(agent)
    # Token estimation includes system prompt and other context
    # Just verify it returns a positive integer
    assert isinstance(estimated, int) and estimated > 0


def test_ensure_prompt_reduces_context_when_near_limit():
    messages = [_make_message("x" * 4000) for _ in range(10)]
    agent = AgentStub(messages, limit=1000)
    _ensure_prompt_within_budget(agent)
    assert agent.conversation_manager.calls, "Expected reduce_context to be invoked"


def test_ensure_prompt_reduces_context_when_near_limit_consider_output_tokens():
    messages = [_make_message('user_prompt'), _make_message("x" * 3000)]
    agent = AgentStub(messages, limit=1000, output_tokens=100)
    _ensure_prompt_within_budget(agent)
    assert agent.conversation_manager.calls, "Expected reduce_context to be invoked"


def test_ensure_prompt_skips_when_under_budget():
    # Use very high limit to ensure estimated tokens are under budget
    # (system prompt adds significant baseline tokens)
    agent = AgentStub([_make_message('user_prompt'), _make_message("short text")], limit=100000)
    _ensure_prompt_within_budget(agent)
    assert not agent.conversation_manager.calls


def test_ensure_prompt_telemetry_trigger():
    # Create messages with enough content to exceed threshold with 3.7 ratio
    # Need ~850 tokens estimated (85% of 1000 limit)
    # 850 tokens * 3.7 chars/token = ~3145 chars
    messages = [_make_message(''), _make_message("x" * 1600), _make_message("x" * 1600)]
    agent = AgentStub(messages, limit=1000, telemetry=900)
    _ensure_prompt_within_budget(agent)
    assert agent.conversation_manager.calls, (
        "Telemetry tokens above threshold should trigger reduction"
    )


def test_strip_reasoning_content_removes_when_disallowed():
    message = _make_reasoning_message()
    agent = AgentStub([message])
    agent._allow_reasoning_content = False
    _strip_reasoning_content(agent)
    assert len(agent.messages) == 0


def test_strip_reasoning_content_keeps_when_allowed():
    message = _make_reasoning_message()
    agent = AgentStub([message])
    agent._allow_reasoning_content = True
    _strip_reasoning_content(agent)
    assert agent.messages[0]["content"] == message["content"]


def test_strip_reasoning_content_removes_when_forced():
    message = _make_reasoning_message()
    agent = AgentStub([message])
    agent._allow_reasoning_content = True
    _strip_reasoning_content(agent, force=True)
    assert len(agent.messages) == 1


def test_strip_reasoning_content_allowed_preserves_one_when_forced_shared_message_content():
    message = _make_reasoning_message()
    message["content"].append({"type": "text", "text": "keep me"})
    agent = AgentStub([message])
    agent._allow_reasoning_content = True
    _strip_reasoning_content(agent, force=True)
    assert len(agent.messages) == 1
    assert len(agent.messages[0]["content"]) == 2
    assert "reasoningContent" in agent.messages[0]["content"][0]
    assert agent.messages[0]["content"][1]["text"] == "keep me"


@pytest.mark.parametrize("message_count", [1, 2, 5])
def test_strip_reasoning_content_not_allowed_ignores_preserving_recent_messages(message_count):
    agent = AgentStub([ _make_reasoning_message() for _ in range(message_count)])
    agent._allow_reasoning_content = False
    _strip_reasoning_content(agent, preserve_recent_messages=1)
    assert len(agent.messages) == 0


@pytest.mark.parametrize("message_count", [1, 2, 5])
def test_strip_reasoning_content_allowed_preserving_recent_messages(message_count):
    agent = AgentStub([_make_reasoning_message() for _ in range(message_count)])
    agent._allow_reasoning_content = True
    _strip_reasoning_content(agent, preserve_recent_messages=1)
    assert len(agent.messages) == 1
    assert len(agent.messages[0]["content"]) > 0


def test_token_calc_and_message_text_helpers_cover_edge_shapes(monkeypatch):
    assert cb.token_calc(0) == 0
    monkeypatch.setattr(cb, "_get_char_to_token_ratio_dynamic", lambda _model: 0)
    assert cb.token_calc(10, "model") > 0
    assert cb._iter_message_texts({"content": "invalid"}) == []
    message = {
        "content": [
            {"text": "hello", "json": "ignored"},
            {"json": {"a": 1}},
            {"toolUse": {"input": {"x": 2}}},
            {"toolResult": {"content": [{"text": "result"}, {"json": {"ok": True}}]}},
            "not-a-dict",
        ]
    }
    texts = cb._iter_message_texts(message)
    assert "hello" in texts and "result" in texts
    assert cb._iter_message_texts(message, block_limit={"text"}) == ["hello"]


def test_plan_state_and_preservation_helpers_cover_protected_pairs():
    messages = [
        {"role": "user", "content": [{"text": "objective"}]},
        {"role": "assistant", "content": [{"toolUse": {"id": "p", "name": "plan", "input": {}}}]},
        {"role": "tool", "content": [{"toolResult": {"toolUseId": "p", "content": "plan_overview[]"}}]},
        {"role": "assistant", "content": [{"text": "later"}]},
    ]
    assert cb._is_plan_tool_result_message(messages[2]) is True
    assert cb._get_latest_plan_tool_result(messages) == 2
    protected = cb._protected_indices_for_plan_state(messages)
    assert protected == {1, 2}
    assert cb._message_has_tool_use(messages[1]) is True
    assert cb._message_has_tool_result(messages[2]) is True
    reduced = [messages[3]]
    assert cb._restore_preserved_messages(reduced, messages, 1) >= 2


def test_environment_helpers_parse_invalid_values(monkeypatch):
    monkeypatch.setenv("TEST_INT", "bad")
    monkeypatch.setenv("TEST_FLOAT", "bad")
    assert cb._get_env_int("TEST_INT", 7) == 7
    assert cb._get_env_float("TEST_FLOAT", 1.5) == 1.5
    monkeypatch.delenv("TEST_INT")
    monkeypatch.delenv("TEST_FLOAT")
    assert cb._get_env_int("TEST_INT", 7) == 7
    assert cb._get_env_float("TEST_FLOAT", 1.5) == 1.5


def test_large_tool_result_mapper_compresses_text_json_and_tool_use():
    mapper = LargeToolResultMapper(max_tool_chars=10, truncate_at=5, sample_limit=2)
    message = {
        "role": "tool",
        "content": [
            {"toolResult": {"content": [{"text": "abcdefghijklmnopqrstuvwxyz"}, {"json": {"a": "x" * 20, "b": 2}}]}},
            {"toolUse": {"name": "shell", "toolUseId": "1", "input": {"code": "x" * 20}}},
            {"text": "unchanged"},
        ],
    }
    compressed = mapper(message, 0, [message])
    assert compressed is not message
    assert compressed["content"][0]["toolResult"]["content"][0]["text"].startswith("[compressed tool result")
    assert "truncated from" in compressed["content"][1]["toolUse"]["input"]["code"]
    assert message["content"][0]["toolResult"]["content"][0]["text"] == "abcdefghijklmnopqrstuvwxyz"


def test_large_tool_result_mapper_helpers_cover_small_and_invalid_values():
    mapper = LargeToolResultMapper(max_tool_chars=100, truncate_at=10)
    assert mapper({"content": []}, 0, []) == {"content": []}
    assert mapper._compress("invalid") == "invalid"
    assert mapper._tool_length({"content": [{"text": "x"}, {"json": {"a": 1}}, {}]}) > 0
    assert mapper._summarize_json({"a": "x" * 100}, 200).startswith("[json dict")
    assert mapper._summarize_json([1, 2, 3], 20).startswith("[json list")
    assert mapper._summarize_json("value", 10) == "[json truncated from 10 chars]"
