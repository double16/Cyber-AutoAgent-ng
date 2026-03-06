"""Model-related configuration modules."""

from modules.config.models.factory import (
    create_bedrock_model,
    create_ollama_model,
    create_litellm_model,
    create_strands_model,
)
from modules.config.models.capabilities import (
    get_capabilities,
    get_model_input_limit,
    get_model_output_limit,
    get_provider_default_limit,
    get_model_pricing,
)
from modules.config.models.dev_client import get_models_client

__all__ = [
    # Model factory
    "create_bedrock_model",
    "create_ollama_model",
    "create_litellm_model",
    "create_strands_model",
    "DEFAULT_TEMPERATURE_EXECUTION",
    "DEFAULT_TEMPERATURE_SWARM",
    "DEFAULT_TEMPERATURE_EXPLOITATION",
    # Capabilities
    "get_capabilities",
    "get_model_input_limit",
    "get_model_output_limit",
    "get_provider_default_limit",
    "get_model_pricing",
    # Models.dev client
    "get_models_client",
]

# 0.2–0.5
DEFAULT_TEMPERATURE_EXECUTION = 0.5
DEFAULT_TEMPERATURE_SWARM = 0.4
DEFAULT_TEMPERATURE_EXPLOITATION = 0.6
