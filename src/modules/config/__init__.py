"""Configuration module for Cyber-AutoAgent."""

from modules.config.system import (
    auto_setup,
    clean_operation_memory,
    setup_logging,
    configure_sdk_logging,
)
from modules.config.types import (
    ModelProvider,
    LLMConfig,
    EmbeddingConfig,
    ServerConfig,
    AgentConfig,
    RateLimitConfig,
)
from modules.config.manager import (
    ConfigManager,
    get_config_manager,
    align_mem0_config,
    check_existing_memories,
)
from modules.config.models import (
    create_bedrock_model,
    create_ollama_model,
    create_litellm_model,
    get_capabilities,
)

__all__ = [
    # Configuration management
    "get_config_manager",
    "ConfigManager",
    # Types
    "ModelProvider",
    "LLMConfig",
    "EmbeddingConfig",
    "ServerConfig",
    "AgentConfig",
    "RateLimitConfig",
    # Environment setup
    "auto_setup",
    "setup_logging",
    "clean_operation_memory",
    # Model factory
    "create_bedrock_model",
    "create_ollama_model",
    "create_litellm_model",
    # Memory utilities
    "align_mem0_config",
    "check_existing_memories",
    # Model capabilities
    "get_capabilities",
    # Logging
    "configure_sdk_logging",
]
