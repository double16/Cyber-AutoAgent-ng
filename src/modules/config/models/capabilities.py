"""Unified model capabilities and limits.

This module centralizes:
- Capability detection (reasoning, tool support, param allowance)
- Static INPUT token limits and provider defaults for prompt budgeting

Precedence order for all parameters:
1. models.dev (authoritative, 500+ models)
2. Static patterns (known models, version-controlled)
3. LiteLLM detection (dynamic, for unknown models)
4. Environment overrides (CYBER_REASONING_ALLOW/DENY)
"""

from __future__ import annotations

import logging
import os
import re
import ollama
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional, Tuple

from modules.config.providers import get_ollama_host
from modules.config.providers.ollama_config import get_ollama_timeout
from modules.config.system import EnvironmentReader

logger = logging.getLogger(__name__)

# Models.dev client for authoritative model metadata
try:
    from modules.config.models.dev_client import get_models_client
except ImportError:
    get_models_client = None  # type: ignore

# --- LiteLLM imports (guarded) -------------------------------------------------
try:
    import litellm  # type: ignore
    from litellm.utils import (  # type: ignore
        LlmProviders,
        ProviderConfigManager,
        supports_reasoning as llm_supports_reasoning,
        ModelInfoBase,
    )
except Exception:  # pragma: no cover
    litellm = None  # type: ignore
    ProviderConfigManager = None  # type: ignore
    LlmProviders = None  # type: ignore
    ModelInfoBase = None  # type: ignore


    def llm_supports_reasoning(
            model: str, custom_llm_provider: Optional[str] = None
    ) -> bool:  # type: ignore
        return False


# --- Helpers -------------------------------------------------------------------


def _split_prefix(model_id: str) -> Tuple[str, str]:
    if not isinstance(model_id, str):
        return "", ""
    if "/" in model_id:
        p, rest = model_id.split("/", 1)
        return p.lower(), rest
    return "", model_id


def _static_supports_reasoning_model(model_id: Optional[str]) -> bool:
    """Return True if the model is known to support extended reasoning blocks.

    This is a fast explicit check for models with native reasoning support.
    For more comprehensive capability detection, use ModelCapabilitiesResolver.

    Scope (explicit):
    - OpenAI/Azure: GPT-5 family and O-series (o3/o4 and mini variants)
    - Anthropic/Bedrock: Claude Sonnet 4 / 4.5 and Opus
    - Moonshot (LiteLLM): Kimi 'thinking' preview variants only
    """
    mid = (model_id or "").lower()

    # Fast path: OpenAI/Azure families already supported
    openai_reasoning_markers = (
        "gpt-5",
        "/o4",
        "/o3",
        "o4-mini",
        "o3-mini",
    )
    if any(marker in mid for marker in openai_reasoning_markers):
        return True

    # Moonshot Kimi 'thinking' variants via LiteLLM (tools unsupported on these models)
    moonshot_thinking_markers = (
        "moonshot/kimi-thinking",
        "kimi-thinking",
        "kimi_k2_thinking",
        "k2-thinking",
    )
    if any(marker in mid for marker in moonshot_thinking_markers):
        return True

    if "gemini-3-pro-preview" in mid or "gemini-3-pro" in mid:
        return True

    # Anthropic/Bedrock explicit allow-list (Sonnet 4/4.5 and Opus only)
    anthropic_allow_markers = (
        # Common Anthropic naming forms across providers
        "claude-sonnet-4-5",
        "sonnet-4-5",
        "claude-sonnet-4",
        "sonnet-4",
        "claude-opus",
        "/opus",  # e.g., claude-4-opus or claude-3-opus style ids
        "-opus",  # covers bedrock/other provider dash-separated ids
    )
    return any(marker in mid for marker in anthropic_allow_markers)


@dataclass(frozen=True)
class Capabilities:
    supports_reasoning: bool
    pass_reasoning_effort: bool
    supports_tools: bool
    supports_tool_choice: bool
    supports_temperature: bool


class ModelCapabilitiesResolver:
    """Model capability detection with the authoritative models.dev source.

    Precedence: models.dev → static patterns → LiteLLM → env overrides
    Cached per (provider, model_id).
    """

    @staticmethod
    @lru_cache(maxsize=512)
    def capabilities(provider: str, model_id: str) -> Capabilities:
        provider = (provider or "").lower()
        model = model_id or ""
        base_provider = provider

        if provider == "litellm":
            pfx, provider_model = _split_prefix(model)
            if pfx:
                base_provider = pfx

        if base_provider != "ollama" and ":" in model:
            model_no_variant = model.split(":")[0]
        else:
            model_no_variant = model

        supports_reason = False
        pass_reasoning_effort = False
        supports_tools = True
        supports_tool_choice = True
        supports_temp = True

        # Priority 1: models.dev (authoritative source for 500+ models)
        if get_models_client is not None:
            try:
                client = get_models_client()
                info = client.get_model_info(model)
                if info and info.capabilities:
                    caps = info.capabilities
                    supports_reason = bool(caps.reasoning)
                    supports_tools = bool(caps.tool_call)
                    supports_tool_choice = supports_tools
                    supports_temp = bool(caps.temperature)
                    logger.debug(
                        "Using models.dev: model=%s reasoning=%s tools=%s temperature=%s",
                        model,
                        supports_reason,
                        supports_tools,
                        supports_temp,
                    )
            except Exception as e:
                logger.debug("models.dev lookup failed for %s: %s", model, e)

        # Priority 2: Static patterns (known models, when models.dev unavailable)
        if not supports_reason:
            supports_reason = _static_supports_reasoning_model(model)
            if supports_reason:
                logger.debug("Using static pattern: model=%s reasoning=%s", model, True)

        # Priority 3: LiteLLM detection (fallback for unknown models)
        if not supports_reason:
            try:
                custom = (
                    base_provider
                    if base_provider and base_provider != "litellm"
                    else None
                )
                supports_reason = bool(
                    llm_supports_reasoning(model=model_no_variant, custom_llm_provider=custom)  # type: ignore[arg-type]
                )
                if supports_reason:
                    logger.debug(
                        "Using LiteLLM detection: model=%s reasoning=%s", model, True
                    )
            except Exception:
                supports_reason = False

        # Check provider params for reasoning_effort and tools support
        allowed_params: list[str] = []
        try:
            if (
                    ProviderConfigManager is not None
                    and LlmProviders is not None
                    and ModelInfoBase is not None
                    and base_provider
            ):
                prov_enum = LlmProviders(base_provider)  # type: ignore[call-arg]
                cfg = ProviderConfigManager.get_provider_chat_config(
                    model=model_no_variant, provider=prov_enum
                )
                if cfg is not None and hasattr(cfg, "get_supported_openai_params"):
                    allowed_params.extend(
                        cfg.get_supported_openai_params(model=model_no_variant) or []
                    )
                if cfg is not None and hasattr(cfg, "get_model_info"):
                    model_info_base: ModelInfoBase = cfg.get_model_info(model=model_no_variant)
                    if model_info_base is not None:
                        if model_info_base.get("supports_function_calling"):
                            allowed_params.extend(["tools", "tool_choice"])
        except Exception as e:
            logger.debug(
                "Provider config lookup failed for %s/%s: %s",
                base_provider,
                model,
                e,
            )

        # Check Ollama capabilities
        if base_provider == "ollama":
            # LiteLLM can list reasoning_effort for Ollama despite it not being an
            # Ollama request parameter. Ollama exposes reasoning support as
            # ``thinking`` and receives its setting through ``think``.
            if "reasoning_effort" in allowed_params:
                allowed_params.remove("reasoning_effort")

            env_reader = EnvironmentReader()
            ollama_client = ollama.Client(host=get_ollama_host(env_reader), timeout=get_ollama_timeout(env_reader))

            try:
                show_response = ollama_client.show(model=model)
                if show_response.capabilities:
                    if "tools" in show_response.capabilities:
                        allowed_params.extend(["tools", "tool_choice"])
                    if "thinking" in show_response.capabilities:
                        allowed_params.append("thinking")
                    else:
                        if "thinking" in allowed_params:
                            allowed_params.remove("thinking")

            except Exception:
                logger.warning(
                    f"OllamaError: Error getting model info for {model}. Set Ollama API Base via `OLLAMA_HOST` environment variable."
                )

        lowered = {p.lower() for p in allowed_params}
        if ("thinking" in lowered) or ("reasoning_effort" in lowered):
            supports_reason = True
        pass_reasoning_effort = "reasoning_effort" in lowered
        if base_provider == "ollama" and "thinking" in lowered:
            # This existing internal flag selects string-valued reasoning levels
            # during model construction. It does not cause a reasoning_effort
            # parameter to be sent to Ollama.
            pass_reasoning_effort = True

        # Update tool and temperature support from provider params if available
        if lowered:
            supports_tools = "tools" in lowered
            supports_tool_choice = "tool_choice" in lowered
            if "temperature" in lowered:
                supports_temp = True
            elif base_provider != "ollama":  # Ollama doesn't always report temperature in show
                # If LiteLLM explicitly knows about params and temperature is NOT there
                if ProviderConfigManager is not None and "temperature" not in lowered:
                    supports_temp = False

        # Priority 4: Environment overrides (highest precedence)
        model_l = model.lower()
        allow = os.getenv("CYBER_REASONING_ALLOW", "").lower().split(",")
        deny = os.getenv("CYBER_REASONING_DENY", "").lower().split(",")
        allow = [a.strip() for a in allow if a and a.strip()]
        deny = [d.strip() for d in deny if d and d.strip()]

        if any(tok in model_l for tok in allow):
            supports_reason = True
            logger.info("ENV override: forcing reasoning=True for %s", model)
        if any(tok in model_l for tok in deny):
            supports_reason = False
            pass_reasoning_effort = False
            logger.info("ENV override: forcing reasoning=False for %s", model)

        return Capabilities(
            supports_reasoning=supports_reason,
            pass_reasoning_effort=pass_reasoning_effort,
            supports_tools=supports_tools,
            supports_tool_choice=supports_tool_choice,
            supports_temperature=supports_temp,
        )


# Public helper
_resolver = ModelCapabilitiesResolver()


def get_capabilities(provider: str, model_id: str) -> Capabilities:
    return _resolver.capabilities(provider, model_id)


def allows_reasoning_content_replay(
    provider: str,
    model_id: str,
    capabilities: Optional[Capabilities] = None,
) -> bool:
    """Return whether prior reasoning blocks may be replayed to the model API."""

    if (provider or "").lower() == "litellm":
        # Strands' LiteLLMModel uses Chat Completions, which can return reasoning
        # but cannot accept reasoningContent in later conversation turns.
        return False
    resolved = capabilities or get_capabilities(provider, model_id)
    return bool(resolved.supports_reasoning)


# --- Input limits (static registry) --------------------------------------------
# Accurate INPUT token limits (context window capacity) for known models
# These are NOT output limits.

MODEL_FAMILY_PATTERNS = [
    # Azure/OpenAI GPT-5-Chat variants (128K)
    (r"azure.*gpt-5-chat", 128000),
    (r"^gpt-5-chat", 128000),
    # Azure/OpenAI GPT-5 variants (272K)
    (r"azure.*gpt-5", 272000),
    (r"^gpt-5", 272000),
    # Azure/OpenAI GPT-OSS variants (131K)
    (r"azure.*gpt-oss", 131072),
    (r"^gpt-oss", 131072),
    # Azure/OpenAI GPT-4 variants
    (r"azure.*gpt-4", 128000),
    (r"^gpt-4", 128000),
    # Bedrock Claude Sonnet 4.x = 1M context
    (r"bedrock/.*claude.*sonnet.*4[-.]5", 1000000),
    (r"claude.*sonnet.*4[-.]5", 1000000),
    # Bedrock Claude 3.5 variants = 200K
    (r"bedrock/.*claude.*3-5", 200000),
    (r"claude.*3-5", 200000),
    # Bedrock Claude 3 variants = 200K
    (r"bedrock/.*claude.*3-opus", 200000),
    (r"bedrock/.*claude.*3-sonnet", 200000),
    # OpenRouter with claude
    (r"openrouter/.*claude", 200000),
    # Gemini variants = 1M
    (r"gemini.*2\.[05].*flash", 1000000),
    (r"gemini.*1\.5", 1000000),
    (r"vertex_ai/.*gemini", 1000000),
    # Ollama llama3.1 variants = 128K
    (r"ollama/.*llama3\.1", 128000),
]


@lru_cache
def get_model_input_limit(model_id: str) -> Optional[int]:
    """Get INPUT token limit for a model (context window capacity).

    Precedence:
    1. models.dev (authoritative)
    2. Static registry (exact match)
    3. Pattern matching (family match)
    4. None (caller should use provider defaults)
    """
    if not model_id:
        return None

    # Priority 1: models.dev
    if get_models_client is not None:
        try:
            client = get_models_client()
            info = client.get_model_info(model_id)
            if info and info.limits and info.limits.context:
                return info.limits.context
        except Exception:
            pass

    # Priority 2: Pattern matching (family match)
    for pattern, limit in MODEL_FAMILY_PATTERNS:
        if re.search(pattern, model_id, re.IGNORECASE):
            return limit

    return None


def get_provider_default_limit(provider: str) -> Optional[int]:
    """Conservative default INPUT limit for a provider (last resort)."""
    defaults = {
        "bedrock": 200000,  # Conservative for Claude 3.5
        "ollama": 32000,  # Varies widely locally
        "litellm": 128000,  # Unknown LiteLLM models
    }
    return defaults.get((provider or "").lower())


@lru_cache
def get_model_output_limit(model_id: str) -> Optional[int]:
    """Get OUTPUT token limit for a model (max completion length).

    Precedence:
    1. MAX_COMPLETION_TOKENS env var (UI reasoning models)
    2. MAX_TOKENS env var (UI general setting)
    3. models.dev (authoritative)
    4. None (caller should use safe defaults)
    """
    if not model_id:
        return None

    # Priority 1: MAX_COMPLETION_TOKENS (UI reasoning models)
    override = os.getenv("MAX_COMPLETION_TOKENS")
    if override:
        try:
            return int(override)
        except ValueError:
            logger.warning("Invalid MAX_COMPLETION_TOKENS: %s", override)

    # Priority 2: MAX_TOKENS (UI general setting)
    override = os.getenv("MAX_TOKENS")
    if override:
        try:
            return int(override)
        except ValueError:
            logger.warning("Invalid MAX_TOKENS: %s", override)

    # Priority 3: models.dev
    if get_models_client is not None:
        try:
            client = get_models_client()
            info = client.get_model_info(model_id)
            if info and info.limits and info.limits.output:
                return info.limits.output
        except Exception:
            pass

    return None


@lru_cache
def get_model_pricing(model_id: str) -> Optional[tuple[float, float]]:
    """Get pricing for a model (cost per million tokens).

    Returns:
        Tuple of (input_cost, output_cost) in USD per million tokens
        None if pricing unavailable
    """
    if not model_id:
        return None

    if get_models_client is not None:
        try:
            client = get_models_client()
            info = client.get_model_info(model_id)
            if info and info.pricing:
                return info.pricing.input, info.pricing.output
        except Exception:
            pass

    return None


def classify_parameter_error(error: Exception) -> Optional[str]:
    """Classify an exception to identify if a specific LLM parameter caused the failure."""
    err_msg = str(error).lower()

    if "temperature" in err_msg and any(
        w in err_msg
        for w in [
            "unsupported",
            "invalid",
            "not supported",
            "unknown",
            "unexpected",
            "extra fields",
            "must be 1",
            "fixed",
            "does not support",
        ]
    ):
        return "temperature"
    if "top_k" in err_msg and any(
        w in err_msg
        for w in ["unsupported", "invalid", "not supported", "unknown", "unexpected", "extra fields", "does not support"]
    ):
        return "top_k"
    if "top_p" in err_msg and any(
        w in err_msg
        for w in [
            "unsupported",
            "invalid",
            "not supported",
            "unknown",
            "unexpected",
            "extra fields",
            "not allowed with",
            "does not support",
        ]
    ):
        return "top_p"
    if "reasoning_effort" in err_msg and any(
        w in err_msg
        for w in ["unsupported", "invalid", "not supported", "unknown", "unexpected", "does not support"]
    ):
        return "reasoning_effort"
    if "thinking" in err_msg and any(
        w in err_msg
        for w in ["unsupported", "invalid", "not supported", "unknown", "unexpected", "budget", "does not support"]
    ):
        return "thinking"
    if "effort" in err_msg and any(
        w in err_msg
        for w in ["unsupported", "invalid", "not supported", "unknown", "unexpected", "does not support"]
    ):
        return "effort"

    return None


def apply_parameter_fallback_to_model(model: Any, provider: str, model_id: str, param_name: str) -> bool:
    """Strip or downgrade the offending parameter from the model instance."""
    modified = False

    # 1. Handle OllamaModel
    if hasattr(model, "config") and isinstance(model.config, dict):
        if provider == "ollama" and param_name == "think":
            additional_args = model.config.get("additional_args")
            if isinstance(additional_args, dict) and additional_args.get("think") is not False:
                previous_value = additional_args["think"]
                if isinstance(previous_value, str):
                    from modules.config.models.agent_profiles import ReasoningLevel

                    additional_args["think"] = ReasoningLevel.from_value(previous_value).to_bool()
                    modified = True
                elif previous_value is True:
                    additional_args["think"] = False
                    modified = True
        if param_name in ("temperature", "top_p", "top_k", "max_tokens"):
            if model.config.get(param_name) is not None:
                model.config[param_name] = None
                modified = True
            options = model.config.get("options")
            if isinstance(options, dict) and param_name in options:
                options.pop(param_name, None)
                modified = True

    # 2. Handle LiteLLMModel / Strands models with params and client_args
    if hasattr(model, "params") and isinstance(model.params, dict):
        if param_name in model.params:
            model.params.pop(param_name, None)
            modified = True
    if hasattr(model, "client_args") and isinstance(model.client_args, dict):
        if param_name in model.client_args:
            model.client_args.pop(param_name, None)
            modified = True
        if param_name in ("reasoning_effort", "thinking", "effort"):
            if "reasoning_effort" in model.client_args:
                model.client_args.pop("reasoning_effort", None)
                modified = True
            if "thinking" in model.client_args:
                model.client_args.pop("thinking", None)
                modified = True
        if param_name == "thinking_config" or param_name == "thinking":
            if "thinking_config" in model.client_args:
                model.client_args.pop("thinking_config", None)
                modified = True

    # 3. Handle BedrockModel
    if hasattr(model, "temperature") and param_name == "temperature":
        if getattr(model, "temperature", None) is not None:
            setattr(model, "temperature", None)
            modified = True
    if hasattr(model, "additional_request_fields") and isinstance(model.additional_request_fields, dict):
        if param_name in ("effort", "thinking", "reasoning_effort"):
            if "output_config" in model.additional_request_fields:
                model.additional_request_fields.pop("output_config", None)
                modified = True
            if "thinking" in model.additional_request_fields:
                model.additional_request_fields.pop("thinking", None)
                modified = True

    return modified


def wrap_model_with_fallback(model: Any, provider: str, model_id: str) -> Any:
    """Wrap model stream and structured_output methods with progressive parameter fallback."""
    import functools
    from modules.config.models.agent_profiles import get_agent_settings_registry

    original_stream = getattr(model, "stream", None)
    original_structured_output = getattr(model, "structured_output", None)

    if callable(original_stream):
        @functools.wraps(original_stream)
        async def fallback_stream(*args, **kwargs):
            registry = get_agent_settings_registry()
            while True:
                try:
                    async for event in original_stream(*args, **kwargs):
                        yield event
                    return
                except Exception as exc:
                    param_name = classify_parameter_error(exc)
                    if param_name:
                        logger.warning(
                            "Model API call failed due to parameter '%s' on %s/%s (%s). Retrying with parameter stripped.",
                            param_name,
                            provider,
                            model_id,
                            exc,
                        )
                        applied = apply_parameter_fallback_to_model(model, provider, model_id, param_name)
                        registry.record_parameter_fallback(
                            provider, model_id, param_name, None, f"Provider rejected parameter {param_name}"
                        )
                        if applied:
                            continue
                    raise

        setattr(model, "stream", fallback_stream)

    if callable(original_structured_output):
        @functools.wraps(original_structured_output)
        async def fallback_structured_output(*args, **kwargs):
            registry = get_agent_settings_registry()
            while True:
                try:
                    async for event in original_structured_output(*args, **kwargs):
                        yield event
                    return
                except Exception as exc:
                    param_name = classify_parameter_error(exc)
                    if param_name:
                        logger.warning(
                            "Model structured output failed due to parameter '%s' on %s/%s (%s). Retrying with parameter stripped.",
                            param_name,
                            provider,
                            model_id,
                            exc,
                        )
                        applied = apply_parameter_fallback_to_model(model, provider, model_id, param_name)
                        registry.record_parameter_fallback(
                            provider, model_id, param_name, None, f"Provider rejected parameter {param_name}"
                        )
                        if applied:
                            continue
                    raise

        setattr(model, "structured_output", fallback_structured_output)

    return model


__all__ = [
    # Capabilities
    "Capabilities",
    "ModelCapabilitiesResolver",
    "get_capabilities",
    # Limits
    "get_model_input_limit",
    "get_model_output_limit",
    "get_provider_default_limit",
    "MODEL_FAMILY_PATTERNS",
    # Pricing
    "get_model_pricing",
    # Parameter Fallback
    "classify_parameter_error",
    "apply_parameter_fallback_to_model",
    "wrap_model_with_fallback",
]
