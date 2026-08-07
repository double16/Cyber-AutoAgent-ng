#!/usr/bin/env python3
"""
Centralized model configuration management for Cyber-AutoAgent.

This module provides a unified configuration system for all model-related
settings, including LLM models, embedding models, and provider configurations.
It supports multiple providers (AWS Bedrock,Litellm, Ollama) and allows for easy
environment variable overrides.

Key Components:
- ModelProvider: Enum for supported providers
- Configuration dataclasses: Type-safe configuration objects
- ConfigManager: Central configuration management
- Environment variable support with fallbacks
- Validation and error handling
"""

import json
import os
from copy import deepcopy
from functools import lru_cache
from math import ceil
from typing import Any, Dict, List, Optional, Tuple

import litellm
import ollama

from modules.config.models.factory import _resolve_prompt_token_limit
from modules.handlers.utils import get_output_path, sanitize_target_name
from modules.config.system.logger import get_logger
from modules.config.models.dev_client import get_models_client
from modules.config.types import (
    ModelConfig,
    LLMConfig,
    EmbeddingConfig,
    MemoryLLMConfig,
    MemoryEmbeddingConfig,
    MemoryVectorStoreConfig,
    MemoryConfig,
    EvaluationConfig,
    SwarmConfig,
    SDKConfig,
    OutputConfig,
    ServerConfig,
    MCPConnection,
    MCPConfig,
    get_default_base_dir,
    RateLimitConfig,
)
from modules.config.system.env_reader import EnvironmentReader
from modules.config.system.defaults import build_default_configs
from modules.config.system.validation import validate_provider
from modules.config.providers.bedrock_config import get_default_region
from modules.config.providers.ollama_config import (
    get_ollama_host as _get_ollama_host_from_env,
    get_ollama_timeout as _get_ollama_timeout_from_env,
    get_ollama_options as _get_ollama_options_from_env,
    get_ollama_keep_alive as _get_ollama_keep_alive_from_env,
)
from modules.config.providers.litellm_config import (
    align_litellm_defaults,
    get_context_window_fallbacks,
    split_litellm_model_id,
)

litellm.drop_params = True
litellm.modify_params = True
litellm.num_retries = 5
litellm.respect_retry_after_header = True

logger = get_logger("Config.Manager")

# Clamp model max tokens (a.k.a. output limit) to give more space to input and drive action (less reasoning).
# MAX_TOKENS_LIMIT = 12_000
MAX_TOKENS_LIMIT = 6144

# Clamp thinking model max tokens (a.k.a. output limit) to give more space to input and drive action (less reasoning).
# MAX_TOKENS_REASONING_LIMIT = 32_000
MAX_TOKENS_REASONING_LIMIT = 10_000


class ConfigManager:
    """Central manager for model, memory, and SDK configuration.

    Provides provider defaults with environment overrides and lightweight
    validation helpers. Caches computed ServerConfig objects per provider.

    Serves as the single source of truth for all configuration access,
    including environment variables. All env var access should go through
    this class to ensure consistent behavior and proper cache invalidation.
    """

    def __init__(self):
        """Initialize configuration manager."""
        self._config_cache = {}
        self.env = EnvironmentReader()
        self._default_configs = build_default_configs()

        # Initialize models.dev client with error handling
        try:
            self.models_client = get_models_client()
            logger.debug("models.dev client initialized successfully")
        except Exception as e:
            logger.warning(
                "Failed to initialize models.dev client, using fallback mode: %s", e
            )
            # Create a minimal fallback that always returns None
            # This allows ConfigManager to work with safe defaults
            self.models_client = None

    # Environment variable access methods

    def getenv(self, key: str, default: str = "") -> str:
        """Get environment variable value."""
        return self.env.get(key, default)

    def getenv_bool(self, key: str, default: bool = False) -> bool:
        """Get environment variable as boolean."""
        return self.env.get_bool(key, default)

    def getenv_int(self, key: str, default: int = 0) -> int:
        """Get environment variable as integer."""
        return self.env.get_int(key, default)

    def getenv_float(self, key: str, default: float = 0.0) -> float:
        """Get environment variable as float."""
        return self.env.get_float(key, default)

    def get_provider(self) -> str:
        provider = self.getenv("CYBER_AGENT_PROVIDER", "bedrock")
        return provider

    def get_default_region(self) -> str:
        """Get the default AWS region with environment override support."""
        return get_default_region(self.env)

    def is_thinking_model(self, provider: str, model_id: str) -> bool:
        """Check if a model supports thinking capabilities."""
        if "opus" in model_id:
            return False

        from modules.config import get_capabilities
        if not provider:
            return False
        return get_capabilities(provider, model_id).supports_reasoning

    def get_max_tokens(
            self,
            provider: str,
            model_id: str,
            *,
            input_tokens: Optional[int] = None,
            supports_reasoning: bool = False
    ) -> int:
        from modules.config import get_capabilities

        if supports_reasoning or (provider and get_capabilities(provider, model_id).supports_reasoning):
            max_tokens_limit = self.getenv_int("MAX_TOKENS_REASONING_LIMIT", MAX_TOKENS_REASONING_LIMIT)
        else:
            max_tokens_limit = self.getenv_int("MAX_TOKENS_LIMIT", MAX_TOKENS_LIMIT)

        if input_tokens is None and provider:
            input_tokens = _resolve_prompt_token_limit(provider, model_id)
        if input_tokens:
            max_tokens_limit = min(max_tokens_limit, ceil(input_tokens / 8))

        return max_tokens_limit

    def get_thinking_model_config(
        self, model_id: str, region_name: str
    ) -> Dict[str, Any]:
        """Get configuration for thinking-enabled models."""
        # Base beta flags for thinking models
        beta_flags = ["interleaved-thinking-2025-05-14"]

        # Add 1M context flag for Claude Sonnet 4 and 4.5
        if (
            "claude-sonnet-4-20250514" in model_id
            or "claude-sonnet-4-5-20250929" in model_id
        ):
            beta_flags.append("context-1m-2025-08-07")

        # Claude Sonnet 4.5 supports extended thinking with higher token limits
        if "claude-sonnet-4-5-20250929" in model_id:
            default_max_tokens = 16000
            default_thinking_budget = 7000
        else:
            default_max_tokens = 32000
            default_thinking_budget = 10000

        # Allow override via environment variables
        max_tokens_limit = self.getenv_int("MAX_TOKENS_REASONING_LIMIT", MAX_TOKENS_REASONING_LIMIT)
        max_tokens = self.getenv_int("MAX_TOKENS", min(default_max_tokens, max_tokens_limit))
        thinking_budget = self.getenv_int("THINKING_BUDGET", default_thinking_budget)

        # FIXME: opus 4.[67] doesn't have "type=enabled", it has something like "adaptive"
        # {"type":"invalid_request_error","message":""thinking.type.enabled" is not supported for this model. Use "thinking.type.adaptive" and "output_config.effort" to control thinking behavior."}}
        return {
            "model_id": model_id,
            "region_name": region_name,
            "temperature": 1.0,
            "max_tokens": max_tokens,
            "additional_request_fields": {
                "anthropic_beta": beta_flags,
                "thinking": {"type": "enabled", "budget_tokens": thinking_budget},
            },
        }

    def get_standard_model_config(
        self, model_id: str, region_name: str, provider: str
    ) -> Dict[str, Any]:
        """Get configuration for standard (non-thinking) models."""
        provider_config = self.get_server_config(provider)
        llm_config = provider_config.llm
        max_tokens = min(llm_config.max_tokens, self.get_max_tokens(provider, model_id))

        config = {
            "model_id": model_id,
            "region_name": region_name,
            "temperature": llm_config.temperature,
            "max_tokens": max_tokens,
        }

        if "max_tokens" in llm_config.parameters:
            llm_config.parameters["max_tokens"] = max_tokens

        # Only include top_p if set (avoid conflicts with providers like Anthropic)
        if llm_config.top_p is not None:
            config["top_p"] = llm_config.top_p

        # Initialize additional_request_fields for Bedrock beta features
        additional_fields = {}
        
        # Add 1M context support for Claude Sonnet 4 and 4.5
        if (
            "claude-sonnet-4-20250514" in model_id
            or "claude-sonnet-4-5-20250929" in model_id
        ):
            additional_fields["anthropic_beta"] = ["context-1m-2025-08-07"]
        
        
        # Add effort parameter if BEDROCK_EFFORT is set (Opus 4.5, Sonnet 4.5, Haiku 4.5 feature)
        effort_level = self.getenv("BEDROCK_EFFORT")
        if effort_level and provider == "bedrock":
            if "anthropic_beta" not in additional_fields:
                additional_fields["anthropic_beta"] = []
            if "effort-2025-11-24" not in additional_fields["anthropic_beta"]:
                additional_fields["anthropic_beta"].append("effort-2025-11-24")
            
            additional_fields["output_config"] = {"effort": effort_level}
            logger.debug(
                "BEDROCK_EFFORT=%s configured for model %s", effort_level, model_id
            )
        
        # Tool Search & Tool Examples: Not supported on Converse API
        # See BEDROCK_BETA_FEATURES.md for details and future updates

        # Only add to config if we have any fields
        if additional_fields:
            config["additional_request_fields"] = additional_fields

        return config

    def get_local_model_config(self, model_id: str, provider: str) -> Dict[str, Any]:
        """Get configuration for local Ollama models."""
        provider_config = self.get_server_config(provider)
        llm_config = provider_config.llm
        max_tokens = min(llm_config.max_tokens, self.get_max_tokens(provider, model_id))

        return {
            "model_id": model_id,
            "host": self.get_ollama_host(),
            "timeout": self.get_ollama_timeout(),
            "keep_alive": self.get_ollama_keep_alive(),
            "temperature": llm_config.temperature,
            "max_tokens": max_tokens,
            "options": self.get_ollama_options(),
        }

    # Default configs now built by build_default_configs() from defaults.py

    def get_server_config(self, provider: str, **overrides) -> ServerConfig:
        """Get complete provider configuration with optional overrides."""
        logger.debug("Getting server config for provider: %s", provider)

        # Invalidate cache if environment has changed
        if self.env.has_changed():
            logger.debug("Environment changed, invalidating config cache")
            self._config_cache.clear()

        # Build stable cache key from known scalar overrides only
        allowed_keys = (
            "model_id",
            "enable_hooks",
            "enable_streaming",
            "conversation_window_size",
        )
        parts: list[str] = [f"provider={provider}"]
        unsupported: list[str] = []
        for key in allowed_keys:
            if key in overrides:
                val = overrides.get(key)
                if isinstance(val, (str, int, float, bool)) or val is None:
                    parts.append(f"{key}={val}")
                else:
                    unsupported.append(key)
        if unsupported:
            logger.debug(
                "Ignoring non-scalar override keys for cache: %s",
                ", ".join(unsupported),
            )
        cache_key = "|".join(parts)
        if cache_key in self._config_cache:
            return self._config_cache[cache_key]

        if provider not in self._default_configs:
            logger.error(
                "Provider %s not in available configs: %s",
                provider,
                list(self._default_configs.keys()),
            )
            raise ValueError(f"Unsupported provider type: {provider}")

        # Environment overrides mutate nested provider configuration objects. A
        # deep copy prevents one operation's override from changing defaults
        # used by later operations in this process.
        defaults = deepcopy(self._default_configs[provider])

        # Apply environment variable overrides
        defaults = self._apply_environment_overrides(provider, defaults)

        # Apply function parameter overrides
        defaults.update(overrides)

        # Special handling for model_id override - apply to LLM configs
        if "model_id" in overrides:
            user_model = overrides["model_id"]
            # Update main LLM
            if "llm" in defaults and isinstance(defaults["llm"], LLMConfig):
                defaults["llm"].model_id = user_model
            # Update swarm LLM
            if "swarm_llm" in defaults and isinstance(defaults["swarm_llm"], LLMConfig):
                defaults["swarm_llm"].model_id = user_model
            # Update evaluation LLM
            if "evaluation_llm" in defaults and isinstance(
                defaults["evaluation_llm"], LLMConfig
            ):
                defaults["evaluation_llm"].model_id = user_model
            # Don't override swarm LLM with user model - keep swarm using v2 for better performance
            # Sub-agent model can be overridden via env var if needed
            # For Ollama, also use the same model for embeddings if mxbai-embed-large:latest is not available
            if (
                provider == "ollama"
                and "embedding" in defaults
                and isinstance(defaults["embedding"], EmbeddingConfig)
            ):
                # Check if the default embedding model is available
                try:
                    client = ollama.Client(host=self.get_ollama_host())
                    models_response = client.list()
                    available_models = [
                        m.get("model", m.get("name", ""))
                        for m in models_response["models"]
                    ]
                    if not any(
                        "mxbai-embed-large" in model for model in available_models
                    ):
                        # Use the user's model for embeddings too
                        defaults["embedding"].model_id = user_model
                except Exception:
                    # Fallback to user's model if availability check fails
                    defaults["embedding"].model_id = user_model

        if provider == "litellm":
            self._align_litellm_defaults(defaults)

        # Build memory configuration
        memory_config = MemoryConfig(
            embedder=self._get_memory_embedder_config(provider, defaults),
            llm=self._get_memory_llm_config(provider, defaults),
            vector_store=MemoryVectorStoreConfig(),
        )

        # Build evaluation configuration (with env-aware defaults)
        evaluation_config_default = EvaluationConfig(llm=None, embedding=None)
        evaluation_config = EvaluationConfig(
            llm=self._get_evaluation_llm_config(provider, defaults),
            embedding=self._get_evaluation_embedding_config(provider, defaults),
            min_tool_calls=self.getenv_int("EVAL_MIN_TOOL_CALLS", evaluation_config_default.min_tool_calls),
            min_evidence=self.getenv_int("EVAL_MIN_EVIDENCE", evaluation_config_default.min_evidence),
            max_wait_secs=self.getenv_int("EVALUATION_MAX_WAIT_SECS", evaluation_config_default.max_wait_secs),
            poll_interval_secs=self.getenv_int("EVALUATION_POLL_INTERVAL_SECS", evaluation_config_default.poll_interval_secs),
            summary_max_chars=self.getenv_int("EVAL_SUMMARY_MAX_CHARS", evaluation_config_default.summary_max_chars),
            rubric_enabled=self.getenv_bool("EVAL_RUBRIC_ENABLED", evaluation_config_default.rubric_enabled),
            judge_temperature=self.getenv_float("EVAL_JUDGE_TEMPERATURE", evaluation_config_default.judge_temperature),
            judge_max_tokens=self.getenv_int("EVAL_JUDGE_MAX_TOKENS", evaluation_config_default.judge_max_tokens),
            rubric_profile=self.getenv("EVAL_RUBRIC_PROFILE", evaluation_config_default.rubric_profile),
            judge_system_prompt=self.getenv("EVAL_JUDGE_SYSTEM_PROMPT"),
            judge_user_template=self.getenv("EVAL_JUDGE_USER_TEMPLATE"),
            skip_if_insufficient_evidence=self.getenv_bool(
                "EVAL_SKIP_IF_INSUFFICIENT_EVIDENCE", evaluation_config_default.skip_if_insufficient_evidence
            ),
            rationale_persist_mode=self.getenv(
                "EVAL_RATIONALE_PERSIST_MODE", evaluation_config_default.rationale_persist_mode
            ),
        )

        # Build swarm configuration
        swarm_config = SwarmConfig(llm=self._get_swarm_llm_config(provider, defaults))

        # Build MCP configuration
        mcp_config = self._get_mcp_config(provider, defaults, overrides)

        # Build output configuration
        output_config = self._get_output_config(provider, defaults, overrides)

        # Resolve host for ollama provider
        host = self.get_ollama_host() if provider == "ollama" else None

        # Build SDK configuration with environment overrides
        sdk_config_default = SDKConfig()
        sdk_config = SDKConfig(
            enable_hooks=overrides.get(
                "enable_hooks",
                self.getenv_bool("CYBER_SDK_ENABLE_HOOKS", sdk_config_default.enable_hooks)
            ),
            enable_streaming=overrides.get(
                "enable_streaming",
                self.getenv_bool("CYBER_SDK_ENABLE_STREAMING", sdk_config_default.enable_streaming)
            ),
            conversation_window_size=overrides.get(
                "conversation_window_size",
                self.getenv_int("CYBER_CONVERSATION_WINDOW", sdk_config_default.conversation_window_size)
            ),
            enable_telemetry=overrides.get(
                "enable_telemetry",
                self.getenv_bool("ENABLE_SDK_TELEMETRY", sdk_config_default.enable_telemetry),
            )
        )

        config = ServerConfig(
            server_type=provider,
            llm=defaults["llm"],
            embedding=defaults["embedding"],
            memory=memory_config,
            evaluation=evaluation_config,
            swarm=swarm_config,
            mcp=mcp_config,
            output=output_config,
            sdk=sdk_config,
            host=host,
            region=defaults["region"],
        )

        self._config_cache[cache_key] = config
        return config

    def get_llm_config(self, server: str, **overrides) -> LLMConfig:
        """Get LLM configuration for the specified server."""
        server_config = self.get_server_config(server, **overrides)
        return server_config.llm

    def get_embedding_config(self, server: str, **overrides) -> EmbeddingConfig:
        """Get embedding configuration for the specified server."""
        server_config = self.get_server_config(server, **overrides)
        return server_config.embedding

    def get_memory_config(self, server: str, **overrides) -> MemoryConfig:
        """Get memory configuration for the specified server."""
        server_config = self.get_server_config(server, **overrides)
        return server_config.memory

    def get_evaluation_config(self, server: str, **overrides) -> EvaluationConfig:
        """Get evaluation configuration for the specified server."""
        server_config = self.get_server_config(server, **overrides)
        return server_config.evaluation

    def get_swarm_config(self, server: str, **overrides) -> SwarmConfig:
        """Get swarm configuration for the specified server."""
        server_config = self.get_server_config(server, **overrides)
        return server_config.swarm

    def get_output_config(self, server: str, **overrides) -> OutputConfig:
        """Get output configuration for the specified server."""
        server_config = self.get_server_config(server, **overrides)
        return server_config.output

    def get_sdk_config(self, server: str, **overrides) -> SDKConfig:
        """Get SDK configuration for the specified server."""
        server_config = self.get_server_config(server, **overrides)
        return server_config.sdk

    def get_mcp_config(self, server: str, **overrides) -> MCPConfig:
        """Get MCP configuration for the specified server."""
        server_config = self.get_server_config(server, **overrides)
        return server_config.mcp

    # ---------------------------------------------------------------------
    # Swarm helpers (used by specialist sub-agents)
    # ---------------------------------------------------------------------
    def get_swarm_model_id(self, server: Optional[str] = None, **overrides) -> str:
        """Return the configured swarm model_id for the given provider.

        Args:
            server: Provider key (e.g., "bedrock", "ollama", "litellm"). If omitted,
                    will use CYBER_AGENT_PROVIDER (default "bedrock").
            **overrides: Optional overrides forwarded to get_server_config

        Returns:
            The model_id string for the swarm LLM. Falls back to primary llm.model_id
            if swarm_llm is unavailable for the provider.
        """
        try:
            provider = self.get_provider()
            server_config = self.get_server_config(provider, **overrides)
            # Prefer explicit swarm config when available
            if (
                server_config
                and server_config.swarm
                and server_config.swarm.llm
                and server_config.swarm.llm.model_id
            ):
                return server_config.swarm.llm.model_id
            # Fallback to main llm
            if server_config and server_config.llm and server_config.llm.model_id:
                return server_config.llm.model_id
        except Exception:
            pass
        # Final fallback to safe default aligned with Bedrock memory/evaluation defaults
        return "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

    def get_unified_output_path(
        self,
        server: str,
        target_name: str,
        operation_id: str,
        subdir: str = "",
        **overrides,
    ) -> str:
        """Get unified output path using configuration system.

        Args:
            server: Server type for configuration
            target_name: Target name for organization
            operation_id: Operation ID for uniqueness
            subdir: Optional subdirectory within operation
            **overrides: Configuration overrides

        Returns:
            Full unified output path
        """
        output_config = self.get_output_config(server, **overrides)
        sanitized_target = sanitize_target_name(target_name)

        return os.path.abspath(get_output_path(
            target_name=sanitized_target,
            operation_id=operation_id,
            subdir=subdir,
            base_dir=output_config.base_dir,
        ))

    def ensure_operation_output_dirs(
        self,
        server: str,
        target_name: str,
        operation_id: str,
        module: str = "web",
        **overrides,
    ) -> Dict[str, str]:
        """Ensure operation output directories exist and return absolute paths.

        Creates operation-specific directories using configured base_dir:
        - root: outputs/<target>/<operation_id>/
        - artifacts: outputs/<target>/<operation_id>/artifacts/
        - tools: outputs/<target>/<operation_id>/tools/ (for editor+load_tool meta-tooling)

        Safe to call multiple times.

        Returns:
            Dict[str, str]: Absolute paths to {'root', 'artifacts', 'tools'}
        """
        # Build operation-specific paths from config
        root = self.get_unified_output_path(
            server, target_name, operation_id, "", **overrides
        )
        artifacts = self.get_unified_output_path(
            server, target_name, operation_id, "artifacts", **overrides
        )
        tools = self.get_unified_output_path(
            server, target_name, operation_id, "tools", **overrides
        )
        try:
            os.makedirs(root, exist_ok=True)
            os.makedirs(artifacts, exist_ok=True)
            os.makedirs(tools, exist_ok=True)

        except Exception as e:
            logger.debug("ensure_operation_output_dirs: could not create dirs: %s", e)
        return {"root": root, "artifacts": artifacts, "tools": tools}

    def get_unified_memory_path(
        self, server: str, target_name: str, **overrides
    ) -> str:
        """Get unified memory path for target.

        Args:
            server: Server type for configuration
            target_name: Target name for organization
            **overrides: Configuration overrides

        Returns:
            Memory path for the target
        """
        output_config = self.get_output_config(server, **overrides)
        sanitized_target = sanitize_target_name(target_name)

        return os.path.join(output_config.base_dir, sanitized_target, "memory")

    def get_qdrant_memory_config(self, server: str, **overrides) -> Dict[str, Any]:
        """Return the embedding and Qdrant settings used by semantic memory."""
        server_config = self.get_server_config(server, **overrides)
        embedding = server_config.embedding
        return {
            "embedding_provider": server,
            "embedding_model": embedding.model_id,
            "embedding_dimensions": embedding.dimensions,
            "aws_region": self.get_default_region(),
            "ollama_base_url": self.get_ollama_host() if server == "ollama" else None,
            "collection_name": self.getenv("QDRANT_COLLECTION", "cyber_autoagent_memories"),
        }

    def validate_requirements(self, provider: str) -> None:
        """Validate that all requirements are met for the specified provider."""
        # Delegate to validation module
        ollama_host = _get_ollama_host_from_env(self.env) if provider == "ollama" else None
        region = self.get_default_region() if provider == "bedrock" else None
        server_config = self.get_server_config(provider) if provider == "ollama" else None

        validate_provider(provider, self.env, ollama_host, region, server_config)

    def get_context_window_fallbacks(
        self, provider: str
    ) -> Optional[List[Dict[str, List[str]]]]:
        """Optional model fallback mappings for context window resolution."""
        return get_context_window_fallbacks(provider)

    def get_ollama_host(self) -> str:
        """Determine appropriate Ollama host based on environment."""
        return _get_ollama_host_from_env(self.env)

    def get_ollama_timeout(self) -> float:
        """Get Ollama timeout."""
        return _get_ollama_timeout_from_env(self.env)

    def get_ollama_keep_alive(self) -> str:
        """Get Ollama keep alive."""
        return _get_ollama_keep_alive_from_env(self.env)

    def get_ollama_options(self) -> Dict[str, Any]:
        """Get Ollama options, such as num_ctx."""
        return _get_ollama_options_from_env(self.env)

    def set_environment_variables(self, server: str) -> None:
        """Publish the configured embedding model for memory and evaluation."""
        server_config = self.get_server_config(server)
        os.environ["CYBER_AGENT_EMBEDDING_MODEL"] = server_config.embedding.model_id

    def _apply_environment_overrides(
        self, _server: str, defaults: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply environment variable overrides to default configuration."""
        llm_cfg = (
            defaults.get("llm") if isinstance(defaults.get("llm"), LLMConfig) else None
        )

        llm_model = self.getenv("CYBER_AGENT_LLM_MODEL")
        if llm_model and llm_cfg is not None:
            if llm_model != llm_cfg.model_id:
                logger.info(
                    "ENV override: CYBER_AGENT_LLM_MODEL=%s replaces config model=%s",
                    llm_model,
                    llm_cfg.model_id,
                )
            llm_cfg = LLMConfig(
                provider=llm_cfg.provider,
                model_id=llm_model,
                temperature=llm_cfg.temperature,
                max_tokens=llm_cfg.max_tokens,
                top_p=llm_cfg.top_p,
            )
            defaults["llm"] = llm_cfg

        temperature_override = self.getenv("CYBER_AGENT_TEMPERATURE")
        if temperature_override and llm_cfg is not None:
            temperature = self.getenv_float(
                "CYBER_AGENT_TEMPERATURE", llm_cfg.temperature
            )
            if temperature != llm_cfg.temperature:
                llm_cfg.temperature = temperature
                llm_cfg.parameters["temperature"] = temperature

        top_p_override = self.getenv("CYBER_AGENT_TOP_P")
        if top_p_override and llm_cfg is not None:
            top_p = self.getenv_float(
                "CYBER_AGENT_TOP_P", llm_cfg.top_p if llm_cfg.top_p is not None else 0.0
            )
            if top_p != llm_cfg.top_p:
                llm_cfg.top_p = top_p
                llm_cfg.parameters["top_p"] = top_p

        if llm_cfg is not None:
            if self.getenv("MAX_TOKENS"):
                max_tokens = self.getenv_int("MAX_TOKENS", llm_cfg.max_tokens)
                if max_tokens != llm_cfg.max_tokens:
                    llm_cfg.max_tokens = max_tokens
                    llm_cfg.parameters["max_tokens"] = max_tokens
            else:
                # apply limit to max tokens so we use the space for input context
                max_tokens_limit = self.get_max_tokens(llm_cfg.provider.value, llm_cfg.model_id)
                if 0 < max_tokens_limit < llm_cfg.max_tokens:
                    llm_cfg.max_tokens = max_tokens_limit
                    llm_cfg.parameters["max_tokens"] = max_tokens_limit

        embedding_model = self.getenv("CYBER_AGENT_EMBEDDING_MODEL")
        if embedding_model and isinstance(defaults.get("embedding"), EmbeddingConfig):
            embedding_cfg = defaults["embedding"]
            if embedding_model != embedding_cfg.model_id:
                logger.info(
                    "ENV override: CYBER_AGENT_EMBEDDING_MODEL=%s replaces config=%s",
                    embedding_model,
                    embedding_cfg.model_id,
                )
            embedding_cfg.model_id = embedding_model
            embedding_cfg.parameters["dimensions"] = embedding_cfg.dimensions

        eval_model = self.getenv("CYBER_AGENT_EVALUATION_MODEL")
        if eval_model and isinstance(defaults.get("evaluation_llm"), LLMConfig):
            evaluation_cfg = defaults["evaluation_llm"]
            evaluation_cfg.model_id = eval_model

        swarm_model = self.getenv("CYBER_AGENT_SWARM_MODEL")
        if swarm_model and isinstance(defaults.get("swarm_llm"), LLMConfig):
            swarm_cfg = defaults["swarm_llm"]
            swarm_cfg.model_id = swarm_model

        # Apply AWS_REGION to region and aws_region fields (but not for ollama)
        if _server not in ("ollama",):
            aws_region = self.getenv("AWS_REGION", "us-east-1")
            if defaults.get("region"):
                defaults["region"] = aws_region
            if isinstance(defaults.get("memory_llm"), MemoryLLMConfig):
                defaults["memory_llm"].aws_region = aws_region

        return defaults

    def _split_litellm_model_id(self, model_id: str) -> Tuple[str, str, str]:
        """Split LiteLLM model id into provider prefix and base id."""
        return split_litellm_model_id(model_id)

    def _align_litellm_defaults(self, defaults: Dict[str, Any]) -> None:
        """Ensure LiteLLM configuration components stay aligned with the selected model."""
        align_litellm_defaults(defaults, self.env)

    def _get_memory_embedder_config(
        self, _server: str, defaults: Dict[str, Any]
    ) -> MemoryEmbeddingConfig:
        """Get memory embedder configuration."""
        embedding_config = defaults["embedding"]
        return MemoryEmbeddingConfig(
            provider=embedding_config.provider,
            model_id=embedding_config.model_id,
            aws_region=defaults.get("region", self.get_default_region()),
            dimensions=embedding_config.dimensions,
        )

    def _get_memory_llm_config(
        self, _server: str, defaults: Dict[str, Any]
    ) -> MemoryLLMConfig:
        """Get memory LLM configuration."""
        return defaults["memory_llm"]

    def _get_evaluation_llm_config(
        self, _server: str, defaults: Dict[str, Any]
    ) -> ModelConfig:
        """Get evaluation LLM configuration."""
        return defaults["evaluation_llm"]

    def _get_evaluation_embedding_config(
        self, _server: str, defaults: Dict[str, Any]
    ) -> ModelConfig:
        """Get evaluation embedding configuration."""
        return defaults["embedding"]

    @lru_cache
    def get_safe_max_tokens(self, model_id: str, buffer: float = 0.5) -> int:
        """Get safe max_tokens using models.dev (50% of limit by default).

        Args:
            model_id: Model identifier (e.g., "azure/gpt-5", "bedrock/...")
            buffer: Safety buffer (0.5 = 50% of limit, must be between 0 and 1)

        Returns:
            Safe max_tokens value
        """
        # Validate buffer parameter
        if not (0 < buffer <= 1.0):
            logger.warning(
                "Invalid buffer %.2f (must be between 0 and 1), using default 0.5",
                buffer
            )
            buffer = 0.5

        # Try models.dev first (authoritative)
        try:
            if self.models_client is None:
                raise ValueError("models.dev client not available")

            info = self.models_client.get_model_info(model_id)
            if info:
                if info.limits and info.limits.output > 0:
                    max_tokens_limit = self.get_max_tokens("", model_id, input_tokens=info.limits.context, supports_reasoning=info.capabilities.reasoning)
                    output_limit = min(info.limits.output, max_tokens_limit)
                    safe = int(output_limit * buffer)
                    logger.debug(
                        "Safe max_tokens from models.dev: model=%s, limit=%d, safe=%d (%.0f%%)",
                        model_id, output_limit, safe, buffer * 100
                    )
                    return safe
        except (ValueError, KeyError, AttributeError) as e:
            logger.debug("models.dev lookup failed for %s: %s", model_id, e)
        except Exception as e:
            logger.error(
                "Unexpected error in models.dev lookup for %s: %s",
                model_id, e, exc_info=True
            )

        # Fallback to 4096 if model not found
        logger.warning(
            "Model not found in models.dev, using safe default: model=%s, safe=4096",
            model_id
        )
        return 4096

    def _get_swarm_llm_config(
        self, _server: str, defaults: Dict[str, Any]
    ) -> LLMConfig:
        """Get swarm LLM configuration with model-aware token limits."""
        swarm_cfg = defaults["swarm_llm"]

        # Get safe max_tokens from models.dev (50% of actual limit)
        safe_max = self.get_safe_max_tokens(swarm_cfg.model_id)

        # Allow explicit override via dedicated env var (don't inherit from main LLM)
        explicit_max = self.getenv_int("CYBER_AGENT_SWARM_MAX_TOKENS", 0)
        if explicit_max:
            swarm_cfg.max_tokens = explicit_max
            logger.info(
                "Swarm config: model=%s, max_tokens=%d (source=env override)",
                swarm_cfg.model_id,
                swarm_cfg.max_tokens
            )
        else:
            swarm_cfg.max_tokens = safe_max
            logger.info(
                "Swarm config: model=%s, max_tokens=%d (source=models.dev safe default)",
                swarm_cfg.model_id,
                swarm_cfg.max_tokens
            )

        return swarm_cfg

    def _get_mcp_config(self, _server: str, defaults: Dict[str, Any], overrides: Dict[str, Any]) -> MCPConfig:
        """Get MCP configuration with validation."""
        enabled = overrides.get("mcp_enabled") or os.getenv("CYBER_MCP_ENABLED", "false").lower() == "true"

        connections = []

        if enabled:
            conns_json = overrides.get("mcp_conns") or os.getenv("CYBER_MCP_CONNECTIONS")
            if conns_json and conns_json.strip():
                try:
                    conns = json.loads(conns_json)
                    if not isinstance(conns, list):
                        raise ValueError("CYBER_MCP_CONNECTIONS is not an array")
                except json.JSONDecodeError:
                    raise ValueError("CYBER_MCP_CONNECTIONS is not valid JSON")
                for conn in conns:
                    mcp_id = conn.get("id")
                    if mcp_id is None or len(mcp_id) == 0:
                        raise ValueError("CYBER_MCP_CONNECTIONS requires an id property")
                    if mcp_id in map(lambda x: x.id, connections):
                        raise ValueError("CYBER_MCP_CONNECTIONS id property must be unique")

                    mcp_transport = conn.get("transport")
                    if mcp_transport not in ["stdio", "sse", "streamable-http"]:
                        raise ValueError(f"CYBER_MCP_CONNECTIONS {mcp_id} does not have a valid transport: {mcp_transport}")

                    mcp_command = conn.get("command") or None
                    if mcp_transport == "stdio":
                        if not mcp_command:
                            raise ValueError("CYBER_MCP_CONNECTIONS stdio transport requires the command property")
                        if isinstance(mcp_command, str):
                            mcp_command = [str]
                        if not isinstance(mcp_command, list):
                            raise ValueError("CYBER_MCP_CONNECTIONS command property is expected to be a list")
                    else:
                        if mcp_command is not None:
                            raise ValueError("CYBER_MCP_CONNECTIONS network transports do not use the command property")

                    mcp_server_url = conn.get("server_url") or None
                    if mcp_transport == "stdio":
                        if mcp_server_url:
                            raise ValueError("CYBER_MCP_CONNECTIONS stdio transport does not use the server_url property")
                    else:
                        if mcp_server_url is None:
                            raise ValueError("CYBER_MCP_CONNECTIONS network transports require the server_url property")

                    mcp_headers = conn.get("headers")
                    if mcp_headers is not None and not isinstance(mcp_headers, dict):
                        raise ValueError("CYBER_MCP_CONNECTIONS headers property is expected to be a dictionary")

                    mcp_plugins = conn.get("plugins")
                    if mcp_plugins is not None and not isinstance(mcp_plugins, list):
                        raise ValueError("CYBER_MCP_CONNECTIONS plugins property is expected to be a list")
                    if not mcp_plugins or "*" in mcp_plugins:
                        mcp_plugins = ["*"]

                    mcp_timeout = conn.get("timeoutSeconds")
                    if mcp_timeout is not None and not isinstance(mcp_timeout, int):
                        raise ValueError("CYBER_MCP_CONNECTIONS timeoutSeconds is expected to be an integer")
                    if mcp_timeout is not None and mcp_timeout < 0:
                        raise ValueError("CYBER_MCP_CONNECTIONS timeoutSeconds is expected to be a positive integer")

                    mcp_allowed_tools = conn.get("allowed_tools")  
                    if mcp_allowed_tools is not None and not isinstance(mcp_allowed_tools, list):
                        raise ValueError("CYBER_MCP_CONNECTIONS allowed_tools property is expected to be a list")
                    if not mcp_allowed_tools or "*" in mcp_allowed_tools:
                        mcp_allowed_tools = ["*"]

                    mcp_conn = MCPConnection(
                        id=mcp_id,
                        transport=mcp_transport,
                        command=mcp_command,
                        server_url=mcp_server_url,
                        headers=mcp_headers,
                        plugins=mcp_plugins,
                        timeoutSeconds=mcp_timeout,
                        allowed_tools=mcp_allowed_tools,
                    )
                    connections.append(mcp_conn)

        return MCPConfig(enabled=enabled, connections=connections)

    def _get_output_config(
        self, _server: str, _defaults: Dict[str, Any], overrides: Dict[str, Any]
    ) -> OutputConfig:
        """Get output configuration with environment variable and override support."""
        # Get base output directory
        base_dir = os.path.abspath(
            overrides.get("output_dir")
            or self.getenv("CYBER_AGENT_OUTPUT_DIR")
            or get_default_base_dir()
        )

        # Get target name
        target_name = overrides.get("target_name")

        # Get operation ID
        operation_id = overrides.get("operation_id")

        return OutputConfig(
            base_dir=base_dir,
            target_name=target_name,
            operation_id=operation_id,
        )

    @lru_cache
    def get_rate_limit_config(self, provider: Optional[str] = None) -> Optional[RateLimitConfig]:
        request_per_minute = self.getenv_float("CYBER_RATE_LIMIT_REQ_PER_MIN")
        tokens_per_minute = self.getenv_float("CYBER_RATE_LIMIT_TOKENS_PER_MIN")
        max_concurrent = self.getenv_int("CYBER_RATE_LIMIT_MAX_CONCURRENT")
        assume_output_tokens = 1024  # looking at langfuse stats to get this number

        if not provider:
            provider = self.get_provider()

        if provider == "ollama" and not max_concurrent:
            logger.info(
                "Ollama default concurrency limited to 1, set CYBER_RATE_LIMIT_MAX_CONCURRENT to make it higher")
            max_concurrent = 1

        # Always return a value to enable rate limit cool down
        return RateLimitConfig(
            rpm=request_per_minute,
            tpm=tokens_per_minute,
            max_concurrent=max_concurrent,
            assume_output_tokens=assume_output_tokens
        )


# Global configuration manager instance
CONFIG_MANAGER_INSTANCE = None


def get_config_manager() -> ConfigManager:
    """Get the global configuration manager instance."""
    global CONFIG_MANAGER_INSTANCE
    if CONFIG_MANAGER_INSTANCE is None:
        CONFIG_MANAGER_INSTANCE = ConfigManager()
    return CONFIG_MANAGER_INSTANCE


def get_model_config(server: str, **overrides) -> ServerConfig:
    """Get model configuration for the specified server.

    Args:
        server: Server type ("local" or "remote")
        **overrides: Configuration overrides

    Returns:
        ServerConfig: Complete server configuration
    """
    return get_config_manager().get_server_config(server, **overrides)


# Backward compatibility functions
def get_default_model_configs(server: str) -> Dict[str, Any]:
    """Get default model configurations (backward compatibility)."""
    config = get_model_config(server)
    return {
        "llm_model": config.llm.model_id,
        "embedding_model": config.embedding.model_id,
        "embedding_dims": config.embedding.dimensions,
    }


def get_ollama_host(env_reader=None) -> str:
    """Get Ollama host (backward compatibility wrapper)."""
    if env_reader is None:
        return get_config_manager().get_ollama_host()
    # When called with env_reader, delegate to providers module
    return _get_ollama_host_from_env(env_reader)
