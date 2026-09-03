"""Unit tests for agent model parameter registry, recommended defaults, and runtime adaptation."""


from modules.config.models import agent_profiles as profiles
from modules.config.models.agent_profiles import (
    AgentModelSettings,
    AgentSettingsRegistry,
    LLMRoleType,
    ReasoningLevel,
    mutate_agent_model_reasoning,
    normalize_agent_type,
    translate_reasoning_to_provider,
)


def test_ollama_reasoning_mutation_uses_string_think_without_transport_reasoning_effort():
    model = type(
        "OllamaModel",
        (),
        {
            "_cyber_provider": "ollama",
            "client_args": {"timeout": 30},
            "config": {"additional_args": {"think": False}},
        },
    )()

    mutate_agent_model_reasoning(model, ReasoningLevel.MEDIUM)

    assert model.client_args == {"timeout": 30}
    assert model.config["additional_args"]["think"] == "medium"


def test_reasoning_level_parsing_and_boolean_evaluation():
    assert ReasoningLevel.NONE.to_bool() is False
    assert ReasoningLevel.LOW.to_bool() is False
    assert ReasoningLevel.MEDIUM.to_bool() is True
    assert ReasoningLevel.HIGH.to_bool() is True
    assert ReasoningLevel.XHIGH.to_bool() is True
    assert ReasoningLevel.MAX.to_bool() is True

    assert ReasoningLevel.from_value("none") == ReasoningLevel.NONE
    assert ReasoningLevel.from_value("LOW") == ReasoningLevel.LOW
    assert ReasoningLevel.from_value("Medium") == ReasoningLevel.MEDIUM
    assert ReasoningLevel.from_value("HIGH") == ReasoningLevel.HIGH
    assert ReasoningLevel.from_value("xhigh") == ReasoningLevel.XHIGH
    assert ReasoningLevel.from_value("max") == ReasoningLevel.MAX
    assert ReasoningLevel.from_value(None) == ReasoningLevel.NONE
    assert ReasoningLevel.from_value("invalid_val") == ReasoningLevel.NONE
    assert ReasoningLevel.from_value(ReasoningLevel.HIGH) == ReasoningLevel.HIGH


def test_recommended_agent_defaults():
    registry = AgentSettingsRegistry()

    # plan_creator / plan_builder
    plan_creator = registry.get_settings("plan_creator")
    assert plan_creator.temperature == 0.2
    assert plan_creator.reasoning_level == ReasoningLevel.MEDIUM
    assert plan_creator.top_p is None
    assert plan_creator.top_k is None
    assert plan_creator.max_tokens == 8192

    # alias plan_builder
    plan_builder = registry.get_settings("plan_builder")
    assert plan_builder.temperature == 0.2
    assert plan_builder.reasoning_level == ReasoningLevel.MEDIUM
    assert plan_builder.max_tokens == 8192

    # plan_critic
    plan_critic = registry.get_settings("plan_critic")
    assert plan_critic.temperature == 0.0
    assert plan_critic.reasoning_level == ReasoningLevel.MEDIUM
    assert plan_critic.max_tokens == 4096

    # task_creator
    task_creator = registry.get_settings("task_creator")
    assert task_creator.temperature == 0.2
    assert task_creator.reasoning_level == ReasoningLevel.MEDIUM
    assert task_creator.max_tokens == 8192

    # task_prompt_builder
    prompt_builder = registry.get_settings("task_prompt_builder")
    assert prompt_builder.temperature == 0.2
    assert prompt_builder.reasoning_level == ReasoningLevel.MEDIUM
    assert prompt_builder.max_tokens == 8192

    # task_prompt_critic
    prompt_critic = registry.get_settings("task_prompt_critic")
    assert prompt_critic.temperature == 0.0
    assert prompt_critic.reasoning_level == ReasoningLevel.MEDIUM
    assert prompt_critic.max_tokens == 4096

    # task_executor
    task_executor = registry.get_settings("task_executor")
    assert task_executor.temperature == 0.5
    assert task_executor.reasoning_level == ReasoningLevel.MEDIUM
    assert task_executor.max_tokens == 8192

    # task_evaluator / phase_evaluator have independent active state.
    evaluator = registry.get_settings("task_evaluator")
    assert evaluator.temperature == 0.0
    assert evaluator.reasoning_level == ReasoningLevel.MEDIUM
    assert evaluator.max_tokens == 4096
    assert registry.get_settings("phase_evaluator").reasoning_level == ReasoningLevel.MEDIUM

    # report_agent / report_critic
    report_agent = registry.get_settings("report_agent")
    assert report_agent.temperature == 0.2
    assert report_agent.reasoning_level == ReasoningLevel.NONE
    assert report_agent.max_tokens == 16384

    report_critic = registry.get_settings("report_critic")
    assert report_critic.temperature == 0.0
    assert report_critic.reasoning_level == ReasoningLevel.LOW
    assert report_critic.max_tokens == 2048

    # taxonomy_annotator and attack_enricher
    tax = registry.get_settings("taxonomy_annotator")
    assert tax.temperature == 0.0
    assert tax.reasoning_level == ReasoningLevel.NONE
    assert tax.max_tokens == 4096

    attack = registry.get_settings("attack_enricher")
    assert attack.temperature == 0.0
    assert attack.reasoning_level == ReasoningLevel.NONE
    assert attack.max_tokens == 4096


def test_normalize_agent_type_uses_canonical_roles_and_limits_aliases():
    assert normalize_agent_type("plan_creator") is LLMRoleType.PLAN_CREATOR
    assert normalize_agent_type("plan_builder") is LLMRoleType.PLAN_CREATOR
    assert normalize_agent_type("phase_evaluator") is LLMRoleType.PHASE_EVALUATOR
    assert normalize_agent_type("primary") is LLMRoleType.UNKNOWN
    assert normalize_agent_type("not-a-role") is LLMRoleType.UNKNOWN
    assert normalize_agent_type(None) is LLMRoleType.UNKNOWN


def test_phase_evaluator_adaptation_does_not_modify_task_evaluator():
    registry = AgentSettingsRegistry()

    registry.apply_reasoning_repair("phase_evaluator", ReasoningLevel.LOW, "phase recovery")

    assert registry.get_settings("phase_evaluator").reasoning_level is ReasoningLevel.LOW
    assert registry.get_settings("task_evaluator").reasoning_level is ReasoningLevel.MEDIUM


def test_reasoning_repair_and_permanent_lock():
    registry = AgentSettingsRegistry()

    # Initially task_creator has MEDIUM reasoning
    assert registry.get_settings("task_creator").reasoning_level == ReasoningLevel.MEDIUM

    # Apply reasoning repair to NONE
    registry.apply_reasoning_repair("task_creator", ReasoningLevel.NONE, "reasoning loop detected", permanent=True)

    # Now it is permanently NONE for all subsequent calls
    assert registry.get_settings("task_creator").reasoning_level == ReasoningLevel.NONE

    # Check adjustment audit log
    records = registry.export_adjustment_records()
    assert len(records) == 1
    assert records[0].agent_type == "task_creator"
    assert records[0].parameter_name == "reasoning_level"
    assert records[0].old_value == "medium"
    assert records[0].new_value == "none"
    assert records[0].trigger_reason == "reasoning loop detected"
    assert records[0].permanent is True


def test_three_strike_token_recovery_escalation():
    registry = AgentSettingsRegistry()

    assert registry.get_settings("task_evaluator").max_tokens == 4096

    # 1st success - should not escalate permanent default
    promoted1 = registry.record_token_recovery_success("task_evaluator", boost_amount=2048)
    assert promoted1 is False
    assert registry.get_settings("task_evaluator").max_tokens == 4096

    # 2nd success - should not escalate permanent default
    promoted2 = registry.record_token_recovery_success("task_evaluator", boost_amount=2048)
    assert promoted2 is False
    assert registry.get_settings("task_evaluator").max_tokens == 4096

    # 3rd success - should escalate permanent default to 4096 + 2048 = 6144
    promoted3 = registry.record_token_recovery_success("task_evaluator", boost_amount=2048, ceiling=8192)
    assert promoted3 is True
    assert registry.get_settings("task_evaluator").max_tokens == 6144

    # Verify audit record
    records = registry.export_adjustment_records()
    assert len(records) == 1
    assert records[0].agent_type == "task_evaluator"
    assert records[0].parameter_name == "max_tokens"
    assert records[0].old_value == 4096
    assert records[0].new_value == 6144
    assert records[0].permanent is True


def test_profile_comparison_export():
    registry = AgentSettingsRegistry()
    registry.apply_reasoning_repair("plan_creator", "none", "reasoning loop", permanent=True)

    comparison = registry.export_profile_comparison()
    assert "plan_creator" in comparison
    assert comparison["plan_creator"]["baseline"]["reasoning_level"] == "medium"
    assert comparison["plan_creator"]["final"]["reasoning_level"] == "none"
    assert comparison["plan_creator"]["adjusted"] is True

    assert comparison["plan_critic"]["adjusted"] is False


def test_translate_reasoning_to_provider():
    # Bedrock
    bedrock_none = translate_reasoning_to_provider("bedrock", "anthropic.claude-v3", ReasoningLevel.NONE)
    assert bedrock_none.get("effort") is None

    bedrock_high = translate_reasoning_to_provider("bedrock", "anthropic.claude-v3", ReasoningLevel.HIGH, max_tokens=8192)
    assert bedrock_high["effort"] == "high"
    assert bedrock_high["budget_tokens"] == 4096

    # LiteLLM
    litellm_none = translate_reasoning_to_provider("litellm", "gpt-5", ReasoningLevel.NONE)
    assert litellm_none.get("reasoning_effort") is None

    litellm_med = translate_reasoning_to_provider("litellm", "gpt-5", ReasoningLevel.MEDIUM, max_tokens=4096)
    assert litellm_med["reasoning_effort"] == "medium"
    assert litellm_med["thinking"]["type"] == "enabled"
    assert litellm_med["thinking"]["budget_tokens"] == int(4096 * 0.8)

    # Ollama
    ollama_none = translate_reasoning_to_provider("ollama", "qwen", ReasoningLevel.NONE)
    assert ollama_none["think"] is False

    ollama_low = translate_reasoning_to_provider("ollama", "qwen", ReasoningLevel.LOW)
    assert ollama_low["think"] == "low"

    ollama_xhigh = translate_reasoning_to_provider("ollama", "qwen", ReasoningLevel.XHIGH)
    assert ollama_xhigh["think"] == "xhigh"

    ollama_max = translate_reasoning_to_provider("ollama", "qwen", ReasoningLevel.MAX)
    assert ollama_max["think"] == "max"

    # Gemini
    gemini_none = translate_reasoning_to_provider("gemini", "gemini-2.5", ReasoningLevel.NONE)
    assert gemini_none["thinking_budget"] == 0

    gemini_high = translate_reasoning_to_provider("gemini", "gemini-2.5", ReasoningLevel.HIGH, max_tokens=8192)
    assert gemini_high["thinking_budget"] == 8192

    gemini_xhigh = translate_reasoning_to_provider("gemini", "gemini-2.5", ReasoningLevel.XHIGH, max_tokens=16384)
    assert gemini_xhigh["thinking_budget"] == 12288


def test_registry_applies_learned_fallbacks_and_token_boost_controls():
    registry = AgentSettingsRegistry()
    registry.record_parameter_fallback("OpenAI", "Model-A", "temperature", None)
    registry.record_parameter_fallback("OpenAI", "Model-A", "top_k", None)
    registry.record_parameter_fallback("OpenAI", "Model-A", "top_p", None)

    settings = registry.get_settings("task_executor", provider="openai", model_id="model-a")
    assert (settings.temperature, settings.top_k, settings.top_p) == (None, None, None)
    assert registry.get_learned_fallbacks("OPENAI", "MODEL-A") == {
        "temperature": None,
        "top_k": None,
        "top_p": None,
    }

    previous = settings.max_tokens
    assert registry.boost_max_tokens_for_retry("task_executor", boost_amount=10_000, ceiling=9_000) == 9_000
    registry.revert_token_boost("task_executor", previous)
    assert registry.get_settings("task_executor").max_tokens == previous
    registry.apply_reasoning_repair("task_executor", "low", "temporary", permanent=False)
    assert registry.export_adjustment_records()[-1].parameter_name == "top_p"


def test_agent_profile_mutators_cover_litellm_bedrock_and_missing_models():
    assert profiles.mutate_agent_model_max_tokens(None) == 0

    model = type("LiteLLM", (), {"client_args": {}, "_output_tokens": 2000})()
    profiles.mutate_agent_model_reasoning(model, ReasoningLevel.HIGH)
    assert model.client_args["reasoning_effort"] == "high"
    assert model.client_args["thinking"]["budget_tokens"] == 1600
    profiles.mutate_agent_model_reasoning(model, ReasoningLevel.NONE)
    assert model.client_args == {}

    bedrock = type("Bedrock", (), {"additional_request_fields": {}})()
    profiles.mutate_agent_model_reasoning(bedrock, ReasoningLevel.XHIGH)
    assert bedrock.additional_request_fields["output_config"]["effort"] == "max"

    configurable = type("Configurable", (), {"config": {"max_tokens": 100}, "params": {"max_output_tokens": 100}})()
    assert profiles.mutate_agent_model_max_tokens(configurable, 50, ceiling=120) == 120
    assert configurable.params["max_output_tokens"] == 120


def test_custom_profile_registry_creates_unknown_role_baseline():
    registry = AgentSettingsRegistry({LLMRoleType.PLAN_CREATOR: AgentModelSettings(max_tokens=12)})

    assert registry.get_settings("missing").max_tokens == 8192


def test_registry_reset_and_global_helpers_restore_default_state(monkeypatch):
    registry = AgentSettingsRegistry()
    registry.record_parameter_fallback("provider", "model", "temperature", None)
    registry.apply_reasoning_repair("task_executor", "none", "temporary")

    registry.reset()

    assert registry.get_settings("task_executor").reasoning_level is ReasoningLevel.MEDIUM
    assert registry.get_learned_fallbacks("provider", "model") == {}
    monkeypatch.setattr(profiles, "_GLOBAL_AGENT_SETTINGS_REGISTRY", None)
    assert profiles.get_agent_settings_registry() is profiles.get_agent_settings_registry()
    assert profiles.reset_agent_settings_registry() is not None


def test_agent_profile_mutators_cover_bedrock_cleanup_and_token_parameter_sources():
    bedrock = type(
        "Bedrock",
        (),
        {"additional_request_fields": {"output_config": {}, "thinking": {}, "anthropic_beta": ["effort-2025", "other"]}},
    )()
    profiles.mutate_agent_model_reasoning(bedrock, ReasoningLevel.NONE)
    assert bedrock.additional_request_fields == {"anthropic_beta": ["other"]}

    config_model = type("Config", (), {"config": {}})()
    profiles.mutate_agent_model_reasoning(config_model, ReasoningLevel.HIGH)
    assert config_model.config["additional_args"]["think"] == "high"

    params_model = type("Params", (), {"params": {"max_tokens": 10}})()
    assert profiles.mutate_agent_model_max_tokens(params_model, 5) == 15
    assert params_model.params["max_tokens"] == 15
