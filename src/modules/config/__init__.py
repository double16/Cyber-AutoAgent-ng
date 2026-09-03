"""Configuration module for Cyber-AutoAgent."""

from modules.config.manager import (
    ConfigManager,
    get_config_manager,
    get_report_refinement_cycles,
)
from modules.config.models import (
    create_bedrock_model,
    create_litellm_model,
    create_ollama_model,
    get_capabilities,
)
from modules.config.system import (
    auto_setup,
    clean_operation_memory,
    configure_sdk_logging,
    setup_logging,
)
from modules.config.types import (
    AgentConfig,
    EmbeddingConfig,
    LLMConfig,
    ModelProvider,
    RateLimitConfig,
    ServerConfig,
)

__all__ = [
    "AgentConfig",
    "ConfigManager",
    "EmbeddingConfig",
    "LLMConfig",
    # Types
    "ModelProvider",
    "RateLimitConfig",
    "ServerConfig",
    # Environment setup
    "auto_setup",
    "clean_operation_memory",
    # Logging
    "configure_sdk_logging",
    # Model factory
    "create_bedrock_model",
    "create_litellm_model",
    "create_ollama_model",
    # Model capabilities
    "get_capabilities",
    # Configuration management
    "get_config_manager",
    "get_report_refinement_cycles",
    "setup_logging",
]
