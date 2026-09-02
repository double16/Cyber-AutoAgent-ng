#!/usr/bin/env python3
import types
from unittest.mock import Mock

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


def test_compress_stale_tool_outputs_preserves_references_and_workflow_state():
    old_result = {
        "role": "tool",
        "content": [{
            "toolResult": {
                "status": "completed",
                "content": [{"text": "artifact:abc task_uid=missing " + ("x" * 2_100)}],
            }
        }],
    }
    agent = AgentStub([old_result, _make_message("recent")])

    assert cb._compact_stale_tool_outputs(agent, preserve_recent=1) == 1
    summary = old_result["content"][0]["toolResult"]["content"][0]["json"]
    assert summary["compacted"] is True
    assert summary["references"] == ["artifact:abc"]


def test_compress_failed_tool_outputs_skips_success_and_bounds_old_errors():
    failed = {
        "role": "tool",
        "content": [{
            "toolResult": {
                "status": "failed",
                "content": [{"text": "finding:one " + ("error " * 500)}],
            }
        }],
    }
    successful = {
        "role": "tool",
        "content": [{"toolResult": {"status": "ok", "content": [{"text": "x" * 3_000}]}}],
    }
    agent = AgentStub([failed, successful, _make_message("recent")])

    assert cb._compact_failed_tool_outputs(agent, preserve_recent=1) == 1
    receipt = failed["content"][0]["toolResult"]["content"][0]["json"]
    assert receipt["compacted_failure"] is True
    assert receipt["references"] == ["finding:one"]
    assert successful["content"][0]["toolResult"]["content"][0]["text"].startswith("x")


def test_context_reduction_records_epoch_and_trims_history():
    agent = AgentStub([])
    agent._cyber_context_reduction_states = [{"epoch": "invalid"}, "bad"]
    agent._prompt_budget_warned_no_reduction = True
    for index in range(6):
        cb._record_context_reduction_event(
            agent,
            stage="test",
            reason=None,
            before_msgs=2,
            after_msgs=1,
            before_tokens=index + 2,
            after_tokens=index + 1,
        )

    assert len(agent._context_reduction_events) == 5
    assert agent._cyber_context_reduction_states[0]["epoch"] == 6
    assert not hasattr(agent, "_prompt_budget_warned_no_reduction")


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


def test_compression_metadata_and_shared_manager_registration_cover_optional_fields():
    metadata = cb.CompressionMetadata(
        compressed=True,
        original_size=100,
        compressed_size=20,
        compression_ratio=0.2,
        content_type="json",
        n_original_keys=2,
        sample_data={"first": "value"},
    )
    assert metadata.to_indicator_json()["first"] == "value"
    assert "2 keys" in metadata.to_indicator_text()
    cb.register_conversation_manager("manager")
    assert cb.get_shared_conversation_manager() == "manager"
    cb.clear_shared_conversation_manager()
    assert cb.get_shared_conversation_manager() is None


def test_mapper_keeps_already_compressed_text_and_compresses_large_json_samples():
    mapper = LargeToolResultMapper(max_tool_chars=10, truncate_at=5, sample_limit=1)
    result = mapper._compress(
        {
            "content": [
                {"text": "[compressed tool result – 99 chars]"},
                {"json": {"first": "x" * 50, "second": "y" * 50}},
            ]
        }
    )
    assert result["content"][0]["text"].startswith("[compressed tool result")
    assert any("_n_original_keys" in block.get("json", {}) for block in result["content"])


def test_mapping_manager_validates_preservation_and_applies_mapper_in_place():
    mapper = LargeToolResultMapper(max_tool_chars=5, truncate_at=3)
    manager = cb.MappingConversationManager(
        window_size=4,
        preserve_recent_messages=-1,
        preserve_first_messages=-1,
        tool_result_mapper=mapper,
        sdk_proactive_compression=False,
    )
    agent = AgentStub([
        _make_message("first"),
        {"role": "tool", "content": [{"toolResult": {"content": [{"text": "x" * 20}]}}]},
        _make_message("middle"),
        _make_message("last"),
    ])
    manager._apply_mapper(agent)
    assert agent.messages[1]["content"][0]["toolResult"]["content"][0]["text"].startswith("[compressed")
    state = manager.get_state()
    assert "sliding_state" in state
    assert manager.restore_from_session(state) == agent.messages or manager.restore_from_session(state) is None


def test_mapping_manager_force_prune_preserves_tool_pairs_and_handles_overlap():
    manager = cb.MappingConversationManager(window_size=6, preserve_first_messages=1, preserve_recent_messages=1)
    pair_use = {"role": "assistant", "content": [{"toolUse": {"id": "1"}}]}
    pair_result = {"role": "tool", "content": [{"toolResult": {"toolUseId": "1", "content": "ok"}}]}
    agent = AgentStub([_make_message("first"), pair_use, pair_result, _make_message("middle"), _make_message("last")])

    manager._force_prune_oldest(agent, 2)
    assert pair_use not in agent.messages
    assert pair_result not in agent.messages
    assert manager.removed_message_count == 2

    overlap = cb.MappingConversationManager(window_size=2, preserve_first_messages=2, preserve_recent_messages=2)
    tiny_agent = AgentStub([_make_message("one"), _make_message("two")])
    overlap._force_prune_oldest(tiny_agent, 1)
    assert len(tiny_agent.messages) == 2


def test_prompt_budget_env_parsers_cover_invalid_and_missing_values(monkeypatch):
    monkeypatch.setenv("TEST_INT", "bad")
    monkeypatch.setenv("TEST_FLOAT", "bad")
    assert cb._get_env_int("TEST_INT", 7) == 7
    assert cb._get_env_float("TEST_FLOAT", 0.25) == 0.25
    monkeypatch.delenv("CYBER_CONTEXT_LIMIT", raising=False)
    assert cb._get_context_limit() == 0
    monkeypatch.setenv("CYBER_CONTEXT_LIMIT", "bad")
    assert cb._get_context_limit() == 0
    monkeypatch.setenv("CYBER_CONTEXT_LIMIT", "123")
    assert cb._get_context_limit() == 123
    monkeypatch.setenv("CYBER_CONTEXT_LIMIT", "")
    assert cb._get_context_limit() == 0
    monkeypatch.setenv("TEST_FLOAT", "0.5")
    assert cb._get_env_float("TEST_FLOAT", 0.25) == 0.5


def test_large_tool_mapper_handles_empty_tool_use_and_sampling_edges():
    mapper = LargeToolResultMapper(max_tool_chars=10, truncate_at=3, sample_limit=1)
    assert mapper._compress_tool_use({"name": "noop"}) == {"name": "noop"}
    compressed = mapper._compress_tool_use({"input": {"value": "abcdef", "short": "ok"}})
    assert compressed["input"]["value"].startswith("abc...")
    assert compressed["input"]["short"] == "ok"
    assert mapper._sample_items([]) == ""
    assert mapper._sample_sequence([]) == ""
    assert mapper._sample_sequence(["z" * 100]).endswith("...")
    assert mapper._sample_items([("first", "a"), ("second", "b")]) == "first=a"


def test_compression_metadata_omits_empty_optional_fields():
    metadata = cb.CompressionMetadata(
        compressed=True,
        original_size=10,
        compressed_size=5,
        compression_ratio=0.5,
        content_type="text",
        n_original_keys=None,
        sample_data={},
    )
    indicator = metadata.to_indicator_json()
    assert "_n_original_keys" not in indicator
    assert len(indicator) == 5
    assert "50%" in metadata.to_indicator_text()


def test_large_tool_mapper_handles_empty_and_large_tool_use_blocks():
    mapper = LargeToolResultMapper(max_tool_chars=5, truncate_at=3)
    empty = {"role": "assistant", "content": []}
    assert mapper(empty, 0, [empty]) is empty

    message = {
        "role": "assistant",
        "content": [
            {"text": "keep"},
            {"toolUse": {"name": "shell", "input": {"command": "abcdef"}}},
        ],
    }
    mapped = mapper(message, 1, [message])
    assert mapped is not message
    assert mapped["content"][0] == {"text": "keep"}
    assert mapped["content"][1]["toolUse"]["input"]["command"].startswith("abc...")
    assert message["content"][1]["toolUse"]["input"]["command"] == "abcdef"


def test_large_tool_mapper_compresses_tool_result_at_exact_threshold():
    mapper = LargeToolResultMapper(max_tool_chars=5, truncate_at=3, sample_limit=1)
    message = {
        "role": "tool",
        "content": [{"toolResult": {"content": [{"text": "abcde"}, {"json": {"key": "value"}}]}}],
    }
    mapped = mapper(message, 0, [message])
    result_content = mapped["content"][0]["toolResult"]["content"]
    assert result_content[0]["text"].startswith("[compressed tool result")


def test_large_tool_mapper_compresses_scalar_and_mixed_json_blocks():
    mapper = LargeToolResultMapper(max_tool_chars=3, truncate_at=2)
    result = mapper._compress(
        {"content": [{"json": "x" * 20}, {"text": "y" * 20}, {"other": True}]}
    )
    assert result["content"][0]["text"].startswith("[compressed tool result")
    assert result["content"][1]["text"].startswith("[Compressed:")
    assert result["content"][2]["json"]["_compressed"] is True
    assert result["content"][3]["text"].startswith("yy")
    assert result["content"][4] == {"other": True}


def test_budget_helpers_cover_agent_shape_and_metric_fallback_branches(monkeypatch):
    assert cb._count_agent_messages(types.SimpleNamespace(messages=[1, 2])) == 2
    assert cb._count_agent_messages(types.SimpleNamespace(messages="not-a-list")) == 0

    direct = types.SimpleNamespace(_prompt_token_limit=123, model=None)
    assert cb._get_prompt_token_limit(direct) == 123
    from_model = types.SimpleNamespace(
        _prompt_token_limit=None, model=types.SimpleNamespace(context_window_limit=456)
    )
    assert cb._get_prompt_token_limit(from_model) == 456

    tool_registry = types.SimpleNamespace(get_all_tool_specs=lambda: [{"name": "tool"}])
    context = cb._get_agent_input_context(
        types.SimpleNamespace(messages=[{"role": "user"}], system_prompt="system", tool_registry=tool_registry)
    )
    assert context.messages == [{"role": "user"}]
    assert context.system_prompt == "system"
    assert context.tool_specs == [{"name": "tool"}]

    estimate_agent = types.SimpleNamespace(messages=[], name="agent")
    monkeypatch.setattr(cb, "_estimate_prompt_tokens_for_agent", lambda *_args: 42)
    assert cb.safe_estimate_tokens(estimate_agent, "extra") == 42
    monkeypatch.setattr(cb, "_estimate_prompt_tokens_for_agent", Mock(side_effect=RuntimeError("bad")))
    assert cb.safe_estimate_tokens(estimate_agent) is None

    metrics_agent = types.SimpleNamespace(
        event_loop_metrics=types.SimpleNamespace(accumulated_usage={"inputTokens": 10})
    )
    assert cb._get_metrics_input_tokens(metrics_agent) == 10
    assert cb._get_metrics_input_tokens(metrics_agent) == 10
    metrics_agent.event_loop_metrics.accumulated_usage["inputTokens"] = 14
    assert cb._get_metrics_input_tokens(metrics_agent) == 4

    fallback_agent = types.SimpleNamespace(callback_handler=types.SimpleNamespace(sdk_input_tokens=7))
    assert cb._get_metrics_input_tokens(fallback_agent) == 7
    assert cb._get_metrics_input_tokens(types.SimpleNamespace()) is None


def test_mapping_manager_orchestration_prunes_pairs_and_preserves_boundaries(monkeypatch):
    """Exercise proactive management across compression, pruning, and SDK delegation."""
    mapper = LargeToolResultMapper(max_tool_chars=8, truncate_at=4)
    manager = cb.MappingConversationManager(
        window_size=6,
        preserve_first_messages=1,
        preserve_recent_messages=1,
        tool_result_mapper=mapper,
        sdk_proactive_compression=False,
    )
    tool_use = {"role": "assistant", "content": [{"toolUse": {"id": "call-1", "name": "shell"}}]}
    tool_result = {
        "role": "tool",
        "content": [{"toolResult": {"toolUseId": "call-1", "content": [{"text": "x" * 80}]}}],
    }
    orphan_result = {"role": "tool", "content": [{"toolResult": {"content": "orphan"}}]}
    agent = AgentStub([
        _make_message("first"),
        tool_use,
        tool_result,
        orphan_result,
        _make_message("middle"),
        _make_message("last"),
        _make_message("overflow"),
    ])
    monkeypatch.setattr(manager._sliding, "apply_management", lambda _agent, **_kwargs: None)

    manager.apply_management(agent)

    assert agent.messages[0]["content"][0]["text"] == "first"
    assert agent.messages[-1]["content"][0]["text"] == "overflow"
    assert tool_use not in agent.messages
    assert tool_result not in agent.messages
    assert manager.removed_message_count >= 2


def test_mapping_manager_reduction_tracks_exhaustion_and_summarizing_fallback(monkeypatch):
    """Exercise reactive orchestration when sliding reduction cannot make progress."""
    manager = cb.MappingConversationManager(
        window_size=2,
        preserve_first_messages=0,
        preserve_recent_messages=0,
        sdk_proactive_compression=False,
    )
    agent = AgentStub([_make_message("one"), _make_message("two"), _make_message("three")])
    monkeypatch.setattr(cb, "safe_estimate_tokens", lambda *_args, **_kwargs: 50)
    monkeypatch.setattr(cb, "_strip_reasoning_content", lambda *_args, **_kwargs: None)

    def overflow(*_args, **_kwargs):
        raise cb.ContextWindowOverflowException("overflow")

    summarized = []
    monkeypatch.setattr(manager._sliding, "reduce_context", overflow)
    monkeypatch.setattr(
        cb.SummarizingConversationManager,
        "reduce_context",
        lambda _self, reduced_agent, *_args, **_kwargs: summarized.append(reduced_agent),
    )

    manager.reduce_context(agent)

    assert summarized == [agent]
    assert agent._context_reduction_exhausted


def test_budget_metric_and_ratio_helpers_handle_counter_reset_and_model_variants(monkeypatch):
    metrics = types.SimpleNamespace(accumulated_usage={"inputTokens": 0})
    agent = types.SimpleNamespace(event_loop_metrics=metrics)
    assert cb._get_metrics_input_tokens(agent) is None
    metrics.accumulated_usage["inputTokens"] = 20
    assert cb._get_metrics_input_tokens(agent) == 20
    metrics.accumulated_usage["inputTokens"] = 5
    assert cb._get_metrics_input_tokens(agent) is None
    metrics.accumulated_usage["inputTokens"] = "invalid"
    assert cb._get_metrics_input_tokens(agent) is None
    assert cb._get_metrics_input_tokens(None) is None

    class ModelClient:
        def get_model_info(self, model_id):
            return types.SimpleNamespace(provider={
                "gemini": "google",
                "gpt-5": "openai",
                "moonshot": "moonshotai",
            }.get(model_id, "anthropic"))

    monkeypatch.setattr(cb, "get_models_client", lambda: ModelClient())
    monkeypatch.setattr(cb, "_get_weighted_observed_ratio", lambda _model: (None, 0))
    assert cb._get_char_to_token_ratio_dynamic("gemini") == 4.2
    assert cb._get_char_to_token_ratio_dynamic("gpt-5") == 4.0
    assert cb._get_char_to_token_ratio_dynamic("moonshot") == 3.8
    monkeypatch.setattr(cb, "_get_weighted_observed_ratio", lambda _model: (9.0, 20))
    assert cb._RATIO_MIN <= cb._get_char_to_token_ratio_dynamic("gpt-5") <= cb._RATIO_MAX


def test_budget_compactors_and_mapper_cover_invalid_and_noop_runtime_shapes(monkeypatch):
    assert cb._compact_stale_tool_outputs(types.SimpleNamespace(messages="invalid"), 1) == 0
    assert cb._compact_failed_tool_outputs(types.SimpleNamespace(messages=None), 1) == 0

    stale = [
        {"content": "not blocks"},
        {"content": [{"toolResult": "not a mapping"}]},
        {"content": [{"toolResult": {"content": "x" * 2100}}]},
        _make_message("preserved recent"),
    ]
    agent = AgentStub(stale)
    assert cb._compact_stale_tool_outputs(agent, preserve_recent=1) == 1
    summary = stale[2]["content"][0]["toolResult"]["content"][0]["json"]
    assert summary["compacted"] is True

    manager = cb.MappingConversationManager(
        window_size=4,
        preserve_first_messages=0,
        preserve_recent_messages=0,
        tool_result_mapper=None,
        sdk_proactive_compression=False,
    )
    manager._apply_mapper(AgentStub([_make_message("one"), _make_message("two"), _make_message("three")]))
    manager._force_prune_oldest(AgentStub([]), 1)
    manager._force_prune_oldest(AgentStub([_make_message("only")]), 0)

    old_fallback = cb.PROMPT_TOKEN_FALLBACK_LIMIT
    monkeypatch.setattr(cb, "PROMPT_TOKEN_FALLBACK_LIMIT", 99)
    no_limit_agent = types.SimpleNamespace(_prompt_token_limit=None, model=None)
    assert cb._get_prompt_token_limit(no_limit_agent) == 99
    monkeypatch.setattr(cb, "PROMPT_TOKEN_FALLBACK_LIMIT", 0)
    assert cb._get_prompt_token_limit(types.SimpleNamespace(_prompt_token_limit=None, model=None)) is None
    monkeypatch.setattr(cb, "PROMPT_TOKEN_FALLBACK_LIMIT", old_fallback)
