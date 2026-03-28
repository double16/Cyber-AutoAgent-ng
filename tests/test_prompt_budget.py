#!/usr/bin/env python3
import types
import pytest

from modules.handlers.conversation_budget import (
    _ensure_prompt_within_budget,
    _estimate_prompt_tokens_for_agent,
    _strip_reasoning_content,
    _dedupe_state_markers,
    strip_reflection_snapshot_messages,
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
    setattr(agent, "_allow_reasoning_content", False)
    _strip_reasoning_content(agent)
    assert len(agent.messages) == 0


def test_strip_reasoning_content_keeps_when_allowed():
    message = _make_reasoning_message()
    agent = AgentStub([message])
    setattr(agent, "_allow_reasoning_content", True)
    _strip_reasoning_content(agent)
    assert agent.messages[0]["content"] == message["content"]


def test_strip_reasoning_content_removes_when_forced():
    message = _make_reasoning_message()
    agent = AgentStub([message])
    setattr(agent, "_allow_reasoning_content", True)
    _strip_reasoning_content(agent, force=True)
    assert len(agent.messages) == 0


def test_strip_reasoning_content_removes_when_forced_shared_message_content():
    message = _make_reasoning_message()
    message["content"].append({"type": "text", "text": "keep me"})
    agent = AgentStub([message])
    setattr(agent, "_allow_reasoning_content", True)
    _strip_reasoning_content(agent, force=True)
    assert len(agent.messages) == 1
    assert len(agent.messages[0]["content"]) == 1
    assert "reasoningContent" not in agent.messages[0]["content"][0]
    assert agent.messages[0]["content"][0]["text"] == "keep me"


@pytest.mark.parametrize("message_count", [1, 2, 5])
def test_strip_reasoning_content_removes_preserving_recent_messages(message_count):
    agent = AgentStub([ _make_reasoning_message() for _ in range(message_count)])
    setattr(agent, "_allow_reasoning_content", False)
    _strip_reasoning_content(agent, preserve_recent_messages=1)
    assert len(agent.messages) == 1
    assert len(agent.messages[0]["content"]) > 0


def test_dedupe_state_markers_strips_reflection_snapshot():
    messages = [_make_message(''), _make_message("<reflection_snapshot>\n"), _make_message("xyz")]
    agent = AgentStub(messages)
    _dedupe_state_markers(agent)
    assert len(agent.messages) == 2
    assert agent.messages[0]["content"][0]["text"] == ''
    assert agent.messages[1]["content"][0]["text"] == 'xyz'


def test_dedupe_state_markers_keeps_only_most_recent_active_task():
    messages = [
        _make_message("pre"),
        _make_message("<active_task version=\"1\">{\"task\":{\"task_uid\":\"1\"}}</active_task>"),
        _make_message("mid"),
        _make_message("<active_task version=\"1\">{\"task\":{\"task_uid\":\"2\"}}</active_task>"),
        _make_message("post"),
    ]
    agent = AgentStub(messages)
    _dedupe_state_markers(agent)

    # Only the most recent active_task message should remain
    texts = [m["content"][0]["text"] for m in agent.messages]
    assert "<active_task" in "".join(texts)
    assert sum(1 for t in texts if t.startswith("<active_task")) == 1
    assert any("task_uid\":\"2\"" in t for t in texts)
    assert not any("task_uid\":\"1\"" in t for t in texts)


def test_dedupe_state_markers_strips_reflection_and_retains_active_task():
    messages = [
        _make_message("a"),
        _make_message("<reflection_snapshot>\nBudget Used: 10%\n</reflection_snapshot>"),
        _make_message("<active_task version=\"1\">{\"task\":{\"task_uid\":\"X\"}}</active_task>"),
        _make_message("**ACTION**: Call `get_active_task()`.\n<reflection_snapshot>\nBudget Used: 20%\n</reflection_snapshot>"),
        _make_message("b"),
    ]
    agent = AgentStub(messages)
    _dedupe_state_markers(agent)

    texts = [m["content"][0]["text"] for m in agent.messages]
    assert all(not t.startswith("<reflection_snapshot>") for t in texts)
    assert sum(1 for t in texts if t.startswith("<active_task")) == 1
    assert any("task_uid\":\"X\"" in t for t in texts)


def test_dedupe_state_markers_no_active_task_strips_reflection_only():
    messages = [
        _make_message("a"),
        _make_message("<reflection_snapshot>\nBudget Used: 10%\n</reflection_snapshot>"),
        _make_message("b"),
    ]
    agent = AgentStub(messages)
    _dedupe_state_markers(agent)

    texts = [m["content"][0]["text"] for m in agent.messages]
    assert texts == ["a", "b"]


def test_dedupe_state_markers_removes_older_active_task_even_if_interleaved():
    messages = [
        _make_message("<active_task version=\"1\">{\"task\":{\"task_uid\":\"OLD\"}}</active_task>"),
        _make_message("x"),
        _make_message("<active_task version=\"1\">{\"task\":{\"task_uid\":\"NEW\"}}</active_task>"),
        _make_message("y"),
    ]
    agent = AgentStub(messages)
    _dedupe_state_markers(agent)

    texts = [m["content"][0]["text"] for m in agent.messages]
    assert sum(1 for t in texts if t.startswith("<active_task")) == 1
    assert any("task_uid\":\"NEW\"" in t for t in texts)
    assert not any("task_uid\":\"OLD\"" in t for t in texts)


def test_strip_reflection_snapshot_messages():
    messages = [_make_message(''), _make_message("<reflection_snapshot>\n"), _make_message("xyz")]
    agent = AgentStub(messages)
    strip_reflection_snapshot_messages(agent)
    assert len(agent.messages) == 2
    assert agent.messages[0]["content"][0]["text"] == ''
    assert agent.messages[1]["content"][0]["text"] == 'xyz'
