"""Model-related configuration modules."""

from modules.config.models.agent_profiles import (
    DEFAULT_AGENT_PROFILES,
    AgentModelSettings,
    AgentSettingsRegistry,
    LLMRoleType,
    ParameterAdjustmentRecord,
    ReasoningLevel,
    get_agent_settings_registry,
    normalize_agent_type,
    reset_agent_settings_registry,
    translate_reasoning_to_provider,
)
from modules.config.models.capabilities import (
    allows_reasoning_content_replay,
    get_capabilities,
    get_model_input_limit,
    get_model_output_limit,
    get_model_pricing,
    get_provider_default_limit,
)
from modules.config.models.dev_client import get_models_client
from modules.config.models.factory import (
    create_bedrock_model,
    create_litellm_model,
    create_ollama_model,
    create_strands_model,
)

__all__ = [
    # Agent Profiles and Settings Registry
    "DEFAULT_AGENT_PROFILES",
    "AgentModelSettings",
    "AgentSettingsRegistry",
    "LLMRoleType",
    "ParameterAdjustmentRecord",
    "ReasoningLevel",
    # Capabilities
    "allows_reasoning_content_replay",
    # Model factory
    "create_bedrock_model",
    "create_litellm_model",
    "create_ollama_model",
    "create_strands_model",
    "get_agent_settings_registry",
    "get_capabilities",
    "get_model_input_limit",
    "get_model_output_limit",
    "get_model_pricing",
    # Models.dev client
    "get_models_client",
    "get_provider_default_limit",
    "normalize_agent_type",
    "reset_agent_settings_registry",
    "translate_reasoning_to_provider",
]
