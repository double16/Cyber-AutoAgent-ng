"""Unit tests for Report Appendix C (Model & Agent Parameter Adjustments) generation."""


from modules.config.models.agent_profiles import (
    AgentSettingsRegistry,
    ReasoningLevel,
)
from modules.handlers.report_generator import _format_parameter_adjustments_appendix


def test_format_parameter_adjustments_appendix_nominal():
    registry = AgentSettingsRegistry()
    appendix = _format_parameter_adjustments_appendix(registry)

    assert "## APPENDIX C: MODEL & AGENT PARAMETER ADJUSTMENTS" in appendix
    assert '<a name="appendix-c-model-agent-parameter-adjustments"></a>' in appendix
    assert "### Agent Role Configurations" in appendix
    assert "| `plan_creator` | Multi-param |" in appendix
    assert "Reasoning: medium" in appendix
    assert "Nominal" in appendix
    assert "No runtime parameter adaptations or provider fallback events were triggered" in appendix


def test_format_parameter_adjustments_appendix_with_adaptations():
    registry = AgentSettingsRegistry()

    # Apply reasoning repair
    registry.apply_reasoning_repair("plan_creator", ReasoningLevel.NONE, "reasoning loop repair", permanent=True)

    # Apply 3-strike token increase
    registry.record_token_recovery_success("task_evaluator", boost_amount=2048)
    registry.record_token_recovery_success("task_evaluator", boost_amount=2048)
    registry.record_token_recovery_success("task_evaluator", boost_amount=2048)

    # Record provider parameter fallback
    registry.record_parameter_fallback("ollama", "qwen", "think", True, "think string rejected")

    appendix = _format_parameter_adjustments_appendix(registry)

    assert "## APPENDIX C: MODEL & AGENT PARAMETER ADJUSTMENTS" in appendix
    assert "Adjusted" in appendix
    assert "| `plan_creator` | Multi-param |" in appendix
    assert "### Runtime Parameter Adaptations and Fallback Log" in appendix
    assert "| `plan_creator` | `reasoning_level` | `medium` | `none` | reasoning loop repair | True |" in appendix
    assert "| `task_evaluator` | `max_tokens` | `4096` | `6144` | 3-strike token exhaustion escalation | True |" in appendix
    assert "| `ollama/qwen` | `think` | `configured` | `True` | think string rejected | True |" in appendix
