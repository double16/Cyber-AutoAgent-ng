"""Unit and integration tests for reasoning loop recovery, reasoning token exhaustion, and 3-strike escalation."""

from unittest.mock import MagicMock

from strands.types.exceptions import MaxTokensReachedException

from modules.config.models.agent_profiles import (
    ReasoningLevel,
    get_agent_settings_registry,
    mutate_agent_model_max_tokens,
    mutate_agent_model_reasoning,
    reset_agent_settings_registry,
)
from modules.handlers.max_token_recovery import (
    MaxTokenClassification,
    capture_and_discard_max_token_output,
    classify_max_token_output,
)


def test_classify_max_token_output_reasoning_and_loop():
    # 1. Output truncation without reasoning
    c1 = classify_max_token_output("short incomplete text", has_reasoning=False, active_reasoning_level="none")
    assert c1.kind == "output_truncation"
    assert c1.is_reasoning_induced is False

    # 2. Output truncation with active reasoning -> reasoning_exhaustion
    c2 = classify_max_token_output("short incomplete text", has_reasoning=True, active_reasoning_level="high")
    assert c2.kind == "reasoning_exhaustion"
    assert c2.is_reasoning_induced is True

    # 3. Reasoning loop (repetitive units)
    repeated_phrase = "I will scan the target ports again and again to make sure.\n" * 40
    c3 = classify_max_token_output(repeated_phrase, has_reasoning=True, active_reasoning_level="high")
    assert c3.kind == "reasoning_loop"
    assert c3.is_reasoning_induced is True
    assert c3.repetition_ratio > 0.4
    assert c3.pattern_hash is not None


def test_max_token_snapshot_retains_exact_internal_secret_values():
    agent = MagicMock()
    agent.messages = [
        {
            "content": [
                {"text": "Partial result API_KEY=internal-secret"},
                {"reasoningContent": {"reasoningText": {"text": "Authorization: Bearer internal-token"}}},
            ]
        }
    ]
    agent.event_loop_metrics.accumulated_usage = {}

    _classification, _removed, snapshot = capture_and_discard_max_token_output(agent)

    assert "internal-secret" in snapshot.partial_output
    assert "internal-token" in snapshot.recorded_reasoning


def test_max_token_snapshot_bounds_internal_secret_diagnostics():
    agent = MagicMock()
    agent.messages = [
        {
            "content": [
                {"text": "API_KEY=internal-secret " + "x" * 4_100},
                {"reasoningContent": {"reasoningText": {"text": "Bearer internal-token " + "y" * 4_100}}},
            ]
        }
    ]
    agent.event_loop_metrics.accumulated_usage = {}

    _classification, _removed, snapshot = capture_and_discard_max_token_output(agent)

    assert snapshot.partial_output.endswith("…[truncated]")
    assert snapshot.recorded_reasoning.endswith("…[truncated]")
    assert "internal-secret" in snapshot.partial_output
    assert "internal-token" in snapshot.recorded_reasoning


def test_mutate_agent_model_reasoning_and_tokens():
    class FakeModel:
        def __init__(self):
            self._output_tokens = 4096
            self.client_args = {"reasoning_effort": "high", "thinking": {"type": "enabled", "budget_tokens": 3000}}
            self.additional_request_fields = {"output_config": {"effort": "high"}}
            self.config = {"max_tokens": 4096, "additional_args": {"think": "high"}}
            self.params = {"max_tokens": 4096}

    class FakeAgent:
        def __init__(self):
            self.model = FakeModel()

    agent = FakeAgent()

    # Mutate to NONE
    mutate_agent_model_reasoning(agent, ReasoningLevel.NONE)
    assert "reasoning_effort" not in agent.model.client_args
    assert "output_config" not in agent.model.additional_request_fields
    assert agent.model.config["additional_args"]["think"] is False

    # Mutate to LOW
    mutate_agent_model_reasoning(agent, ReasoningLevel.LOW)
    assert agent.model.client_args["reasoning_effort"] == "low"
    assert agent.model.additional_request_fields["output_config"]["effort"] == "low"
    assert agent.model.config["additional_args"]["think"] == "low"

    # Mutate max tokens
    new_limit = mutate_agent_model_max_tokens(agent, boost_amount=2048, ceiling=8192)
    assert new_limit == 6144
    assert agent.model._output_tokens == 6144
    assert agent.model.config["max_tokens"] == 6144
    assert agent.model.params["max_tokens"] == 6144


def test_json_agent_workflow_adaptation_recovery():
    reset_agent_settings_registry()
    registry = get_agent_settings_registry()

    from modules.agents.multi_agent_workflow import MultiAgentWorkflowController

    workflow = MultiAgentWorkflowController.__new__(MultiAgentWorkflowController)
    workflow.json_retries = 2
    workflow._workflow_activity_listeners = []
    workflow._log_workflow = MagicMock()
    workflow._record_max_token_exhaustion = MagicMock()
    workflow._emit_workflow_activity = MagicMock()
    workflow._json_max_token_retry_prompt = lambda prompt, kind: f"RETRY {prompt}"

    # Verify task_creator defaults
    assert registry.get_settings("task_creator").reasoning_level == ReasoningLevel.MEDIUM

    call_count = [0]

    def failing_then_succeeding_runner(role, prompt, tools, system_prompt):
        call_count[0] += 1
        if call_count[0] == 1:
            err = MaxTokensReachedException("Hit max tokens")
            err.max_token_classification = MaxTokenClassification("reasoning_loop", 0.8, "hash123", 1000, is_reasoning_induced=True)
            raise err
        return '{"tasks": []}'

    workflow.text_runner = failing_then_succeeding_runner

    result = workflow._run_json_text_agent(
        role="task_creator",
        prompt="Create initial tasks",
        tools=[],
        system_prompt="sys prompt",
    )

    assert result == {"tasks": []}
    assert call_count[0] == 2
    # Verify task_creator has been adapted to reasoning level NONE permanently
    assert registry.get_settings("task_creator").reasoning_level == ReasoningLevel.NONE
    records = registry.export_adjustment_records()
    assert any(r.agent_type == "task_creator" and r.parameter_name == "reasoning_level" and r.new_value == "none" for r in records)


def test_plan_critic_max_tokens_reduction_and_retry():
    reset_agent_settings_registry()
    registry = get_agent_settings_registry()

    from modules.agents.multi_agent_workflow import MultiAgentWorkflowController

    workflow = MultiAgentWorkflowController.__new__(MultiAgentWorkflowController)
    workflow.json_retries = 1
    workflow._workflow_activity_listeners = []
    workflow._log_workflow = MagicMock()
    workflow._record_max_token_exhaustion = MagicMock()
    workflow._emit_workflow_activity = MagicMock()
    workflow._json_max_token_retry_prompt = lambda prompt, kind: f"RETRY {prompt}"

    # Verify plan_critic initial reasoning level is MEDIUM.
    assert registry.get_settings("plan_critic").reasoning_level == ReasoningLevel.MEDIUM

    attempt_reasoning_levels = []

    def plan_critic_runner(role, prompt, tools, system_prompt):
        current_reasoning = registry.get_settings(role).reasoning_level
        attempt_reasoning_levels.append(current_reasoning)
        if len(attempt_reasoning_levels) == 1:
            err = MaxTokensReachedException("Hit max tokens in plan_critic")
            err.max_token_classification = MaxTokenClassification(
                "reasoning_exhaustion", 0.0, None, 1000, is_reasoning_induced=True
            )
            raise err
        return '{"approved": true, "critique": "looks good"}'

    workflow.text_runner = plan_critic_runner

    result = workflow._run_json_text_agent(
        role="plan_critic",
        prompt="Critique plan draft",
        tools=[],
        system_prompt="sys prompt",
    )

    assert result == {"approved": True, "critique": "looks good"}
    # First attempt ran with MEDIUM, retry attempt ran with reasoning disabled.
    assert attempt_reasoning_levels == [ReasoningLevel.MEDIUM, ReasoningLevel.NONE]
    # Now permanently disabled.
    assert registry.get_settings("plan_critic").reasoning_level == ReasoningLevel.NONE
    records = registry.export_adjustment_records()
    assert any(
        r.agent_type == "plan_critic"
        and r.parameter_name == "reasoning_level"
        and r.old_value == "medium"
        and r.new_value == "none"
        for r in records
    )


def test_active_low_reasoning_max_tokens_retries_with_reasoning_disabled():
    reset_agent_settings_registry()
    registry = get_agent_settings_registry()
    registry.apply_reasoning_repair("plan_critic", ReasoningLevel.LOW, "test setup", permanent=True)

    from modules.agents.multi_agent_workflow import MultiAgentWorkflowController

    workflow = MultiAgentWorkflowController.__new__(MultiAgentWorkflowController)
    workflow.json_retries = 1
    workflow._workflow_activity_listeners = []
    workflow._log_workflow = MagicMock()
    workflow._record_max_token_exhaustion = MagicMock()
    workflow._emit_workflow_activity = MagicMock()
    workflow._json_max_token_retry_prompt = lambda prompt, kind: f"RETRY {prompt}"

    attempt_reasoning_levels = []

    def plan_critic_runner(role, prompt, tools, system_prompt):
        attempt_reasoning_levels.append(registry.get_settings(role).reasoning_level)
        if len(attempt_reasoning_levels) == 1:
            error = MaxTokensReachedException("Hit max tokens in plan_critic")
            error.max_token_classification = MaxTokenClassification(
                "reasoning_exhaustion", 0.0, None, 1000, is_reasoning_induced=True
            )
            raise error
        return '{"approved": true, "critique": "looks good"}'

    workflow.text_runner = plan_critic_runner

    result = workflow._run_json_text_agent(
        role="plan_critic",
        prompt="Critique plan draft",
        tools=[],
        system_prompt="sys prompt",
    )

    assert result == {"approved": True, "critique": "looks good"}
    assert attempt_reasoning_levels == [ReasoningLevel.LOW, ReasoningLevel.NONE]
    assert registry.get_settings("plan_critic").reasoning_level == ReasoningLevel.NONE
    records = registry.export_adjustment_records()
    assert any(
        r.agent_type == "plan_critic"
        and r.parameter_name == "reasoning_level"
        and r.new_value == "none"
        for r in records
    )


def test_non_reasoning_max_tokens_boost_and_retry():
    reset_agent_settings_registry()
    registry = get_agent_settings_registry()

    from modules.agents.multi_agent_workflow import MultiAgentWorkflowController

    workflow = MultiAgentWorkflowController.__new__(MultiAgentWorkflowController)
    workflow.json_retries = 1
    workflow._workflow_activity_listeners = []
    workflow._log_workflow = MagicMock()
    workflow._record_max_token_exhaustion = MagicMock()
    workflow._emit_workflow_activity = MagicMock()
    workflow._json_max_token_retry_prompt = lambda prompt, kind: f"RETRY {prompt}"

    # taxonomy_annotator has reasoning NONE and max_tokens 4096
    assert registry.get_settings("taxonomy_annotator").reasoning_level == ReasoningLevel.NONE
    assert registry.get_settings("taxonomy_annotator").max_tokens == 4096

    attempt_max_tokens = []

    def evaluator_runner(role, prompt, tools, system_prompt):
        current_tokens = registry.get_settings(role).max_tokens
        attempt_max_tokens.append(current_tokens)
        if len(attempt_max_tokens) == 1:
            err = MaxTokensReachedException("Hit output token limit")
            err.max_token_classification = MaxTokenClassification(
                "output_truncation", 0.0, None, 1000, is_reasoning_induced=False
            )
            raise err
        return '{"status": "satisfied", "summary": "done"}'

    workflow.text_runner = evaluator_runner

    result = workflow._run_json_text_agent(
        role="taxonomy_annotator",
        prompt="Evaluate taxonomy",
        tools=[],
        system_prompt="sys prompt",
    )

    assert result == {"status": "satisfied", "summary": "done"}
    # First attempt ran with 4096, retry attempt ran with boosted 6144
    assert attempt_max_tokens == [4096, 6144]
    # Since 1st recovery (< 3 strikes), baseline remains 4096
    assert registry.get_settings("taxonomy_annotator").max_tokens == 4096
