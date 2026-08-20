"""End-to-end integration tests for multi-agent parameter adaptation and report generation."""

from unittest.mock import MagicMock

from strands.types.exceptions import MaxTokensReachedException

from modules.agents.multi_agent_workflow import MultiAgentWorkflowController
from modules.config.models.agent_profiles import (
    ReasoningLevel,
    get_agent_settings_registry,
    reset_agent_settings_registry,
)
from modules.handlers.max_token_recovery import MaxTokenClassification
from modules.handlers.report_generator import _format_parameter_adjustments_appendix


def test_multi_agent_adaptation_e2e_scenario():
    reset_agent_settings_registry()
    registry = get_agent_settings_registry()

    # 1. Verify initial baseline
    assert registry.get_settings("plan_creator").reasoning_level == ReasoningLevel.MEDIUM
    assert registry.get_settings("task_prompt_builder").reasoning_level == ReasoningLevel.MEDIUM
    assert registry.get_settings("task_evaluator").max_tokens == 4096

    workflow = MultiAgentWorkflowController.__new__(MultiAgentWorkflowController)
    workflow.json_retries = 2
    workflow._workflow_activity_listeners = []
    workflow._log_workflow = MagicMock()
    workflow._record_max_token_exhaustion = MagicMock()
    workflow._emit_workflow_activity = MagicMock()
    workflow._json_max_token_retry_prompt = lambda prompt, kind: f"RETRY {prompt}"

    # Simulate Plan Creator experiencing a reasoning loop -> downgraded to NONE
    call_counts = {"plan_creator": 0, "task_evaluator": 0}

    def text_runner(role, prompt, tools, system_prompt):
        call_counts[role] += 1
        if role == "plan_creator" and call_counts["plan_creator"] == 1:
            err = MaxTokensReachedException("Hit max tokens during plan generation")
            err.max_token_classification = MaxTokenClassification(
                "reasoning_loop", 0.75, "plan_loop_hash", 2000, is_reasoning_induced=True
            )
            raise err
        if role == "task_evaluator":
            return '{"evaluation": "pass", "reasoning": "done"}'
        return '{"plan": {"phases": []}}'

    workflow.text_runner = text_runner

    res = workflow._run_json_text_agent("plan_creator", "Generate plan", [], "sys")
    assert "plan" in res
    assert registry.get_settings("plan_creator").reasoning_level == ReasoningLevel.NONE

    # Simulate Task Evaluator hitting non-reasoning token exhaustion 3 times -> promoted max_tokens
    promoted_1 = registry.record_token_recovery_success("task_evaluator", boost_amount=2048)
    promoted_2 = registry.record_token_recovery_success("task_evaluator", boost_amount=2048)
    promoted_3 = registry.record_token_recovery_success("task_evaluator", boost_amount=2048)
    assert promoted_1 is False
    assert promoted_2 is False
    assert promoted_3 is True
    assert registry.get_settings("task_evaluator").max_tokens == 6144

    # Generate Appendix C and verify all adaptations are present
    appendix_c = _format_parameter_adjustments_appendix(registry)
    assert "## APPENDIX C: MODEL & AGENT PARAMETER ADJUSTMENTS" in appendix_c
    assert "| `plan_creator` | `reasoning_level` | `medium` | `none` | reasoning loop recovery success | True |" in appendix_c
    assert "| `task_evaluator` | `max_tokens` | `4096` | `6144` | 3-strike token exhaustion escalation | True |" in appendix_c
