"""Unit tests for Appendix A model and agent parameter-adjustment rendering."""


from modules.config.models.agent_profiles import (
    AgentSettingsRegistry,
    ReasoningLevel,
)
from modules.handlers.report_generator import _format_parameter_adjustments_section


def test_format_parameter_adjustments_section_nominal():
    registry = AgentSettingsRegistry()
    section = _format_parameter_adjustments_section(registry)

    assert "### Model & Agent Parameter Adjustments" in section
    assert "## APPENDIX C" not in section
    assert "appendix-c-model-agent-parameter-adjustments" not in section
    assert "### Agent Role Configurations" in section
    assert "| `plan_creator` | Multi-param |" in section
    assert "Reasoning: medium" in section
    assert "Nominal" in section
    assert "No runtime parameter adaptations or provider fallback events were triggered" in section


def test_format_parameter_adjustments_section_with_adaptations():
    registry = AgentSettingsRegistry()

    # Apply reasoning repair
    registry.apply_reasoning_repair("plan_creator", ReasoningLevel.NONE, "reasoning loop repair", permanent=True)

    # Apply 3-strike token increase
    registry.record_token_recovery_success("task_evaluator", boost_amount=2048)
    registry.record_token_recovery_success("task_evaluator", boost_amount=2048)
    registry.record_token_recovery_success("task_evaluator", boost_amount=2048)

    # Record provider parameter fallback
    registry.record_parameter_fallback("ollama", "qwen", "think", True, "think string rejected")

    section = _format_parameter_adjustments_section(registry)

    assert "### Model & Agent Parameter Adjustments" in section
    assert "Adjusted" in section
    assert "| `plan_creator` | Multi-param |" in section
    assert "### Runtime Parameter Adaptations and Fallback Log" in section
    assert "| `plan_creator` | `reasoning_level` | `medium` | `none` | reasoning loop repair | True |" in section
    assert (
        "| `task_evaluator` | `max_tokens` | `4096` | `6144` | "
        "3-strike token exhaustion escalation | True |" in section
    )
    assert "| `ollama/qwen` | `think` | `configured` | `True` | think string rejected | True |" in section
