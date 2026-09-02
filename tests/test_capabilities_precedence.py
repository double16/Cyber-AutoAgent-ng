"""Test unified precedence order for model capabilities."""

import os
from unittest.mock import MagicMock, patch

import pytest

from modules.config.models import capabilities as capabilities_module
from modules.config.models.capabilities import (
    Capabilities,
    ModelCapabilitiesResolver,
    allows_reasoning_content_replay,
    apply_parameter_fallback_to_model,
    classify_parameter_error,
    get_capabilities,
    get_model_input_limit,
    get_model_output_limit,
    get_model_pricing,
    wrap_model_with_fallback,
)


class TestCapabilitiesPrecedence:
    """Validate models.dev takes precedence over LiteLLM for capabilities."""

    def setup_method(self):
        ModelCapabilitiesResolver.capabilities.cache_clear()

    def test_moonshot_kimi_k2_reasoning_via_models_dev(self):
        """Verify moonshot/kimi-k2-thinking uses models.dev data."""
        caps = get_capabilities("litellm", "moonshot/kimi-k2-thinking")
        assert caps.supports_reasoning is True, "Should detect reasoning via models.dev"

    def test_azure_gpt5_reasoning_via_models_dev(self):
        """Verify azure/gpt-5 uses models.dev data."""
        caps = get_capabilities("litellm", "azure/gpt-5")
        assert caps.supports_reasoning is True

    def test_claude_sonnet_45_reasoning(self):
        """Verify Claude Sonnet 4.5 detected correctly."""
        caps = get_capabilities("bedrock", "claude-sonnet-4-5-20250929")
        assert caps.supports_reasoning is True

    def test_env_override_allows_reasoning(self):
        """Verify CYBER_REASONING_ALLOW forces reasoning support."""
        os.environ["CYBER_REASONING_ALLOW"] = "test-model-xyz"
        ModelCapabilitiesResolver.capabilities.cache_clear()

        caps = get_capabilities("litellm", "test-model-xyz")
        assert caps.supports_reasoning is True

        del os.environ["CYBER_REASONING_ALLOW"]
        ModelCapabilitiesResolver.capabilities.cache_clear()

    def test_env_override_denies_reasoning(self):
        """Verify CYBER_REASONING_DENY disables reasoning support."""
        os.environ["CYBER_REASONING_DENY"] = "gpt-5"
        ModelCapabilitiesResolver.capabilities.cache_clear()

        caps = get_capabilities("litellm", "azure/gpt-5")
        assert caps.supports_reasoning is False

        del os.environ["CYBER_REASONING_DENY"]
        ModelCapabilitiesResolver.capabilities.cache_clear()

    def test_litellm_reasoning_does_not_enable_chat_completion_replay(self):
        caps = Capabilities(
            supports_reasoning=True,
            pass_reasoning_effort=True,
            supports_tools=True,
            supports_tool_choice=True,
            supports_temperature=False,
        )

        assert allows_reasoning_content_replay("litellm", "openai/gpt-5", caps) is False
        assert allows_reasoning_content_replay("bedrock", "claude-sonnet-4", caps) is True

    def test_ollama_thinking_sets_internal_string_reasoning_flag(self):
        show_response = MagicMock(capabilities=["thinking"])
        ollama_client = MagicMock()
        ollama_client.show.return_value = show_response

        with (
            patch("modules.config.models.capabilities.get_models_client", None),
            patch("modules.config.models.capabilities.ProviderConfigManager", None),
            patch("modules.config.models.capabilities.LlmProviders", None),
            patch("modules.config.models.capabilities.ModelInfoBase", None),
            patch("modules.config.models.capabilities.ollama.Client", return_value=ollama_client),
        ):
            ModelCapabilitiesResolver.capabilities.cache_clear()
            caps = get_capabilities("ollama", "any-thinking-model")

        assert caps.supports_reasoning is True
        assert caps.pass_reasoning_effort is True


class TestTokenLimitPrecedence:
    """Validate models.dev used for token limits."""

    def test_moonshot_context_limit_from_models_dev(self):
        """Verify context limit retrieved from models.dev."""
        limit = get_model_input_limit("moonshot/kimi-k2-thinking")
        assert limit is not None
        assert limit > 200000, "Kimi K2 should have large context window"

    def test_azure_gpt5_context_limit(self):
        """Verify GPT-5 context limit from models.dev."""
        limit = get_model_input_limit("azure/gpt-5")
        assert limit == 400000, "GPT-5 has a 400K context window in the bundled models.dev snapshot"

    def test_output_limit_from_models_dev(self):
        """Verify output limit retrieved from models.dev."""
        limit = get_model_output_limit("azure/gpt-5")
        assert limit is not None
        assert limit > 100000, "GPT-5 should have large output limit"

    def test_output_limit_env_override_reasoning(self):
        """Verify MAX_COMPLETION_TOKENS (UI reasoning models) overrides models.dev."""
        os.environ["MAX_COMPLETION_TOKENS"] = "50000"

        get_model_output_limit.cache_clear()
        limit = get_model_output_limit("azure/gpt-5")
        assert limit == 50000, "MAX_COMPLETION_TOKENS should override models.dev"

        del os.environ["MAX_COMPLETION_TOKENS"]

    def test_output_limit_env_override_general(self):
        """Verify MAX_TOKENS (UI general setting) overrides models.dev."""
        os.environ["MAX_TOKENS"] = "60000"

        get_model_output_limit.cache_clear()
        limit = get_model_output_limit("azure/gpt-5")
        assert limit == 60000, "MAX_TOKENS should override models.dev"

        del os.environ["MAX_TOKENS"]

    def test_output_limit_precedence_order(self):
        """Verify correct precedence: MAX_COMPLETION_TOKENS > MAX_TOKENS."""
        os.environ["MAX_COMPLETION_TOKENS"] = "50000"
        os.environ["MAX_TOKENS"] = "60000"

        get_model_output_limit.cache_clear()
        limit = get_model_output_limit("azure/gpt-5")
        assert limit == 50000, "MAX_COMPLETION_TOKENS should have highest precedence"

        del os.environ["MAX_COMPLETION_TOKENS"]

        get_model_output_limit.cache_clear()
        limit = get_model_output_limit("azure/gpt-5")
        assert limit == 60000, "MAX_TOKENS should be second priority"

        del os.environ["MAX_TOKENS"]


class TestPricingSupport:
    """Validate pricing data from models.dev."""

    def test_get_pricing_for_known_model(self):
        """Verify pricing retrieved from models.dev."""
        pricing = get_model_pricing("azure/gpt-5")
        assert pricing is not None
        assert len(pricing) == 2
        input_cost, output_cost = pricing
        assert input_cost > 0, "Should have input cost"
        assert output_cost > 0, "Should have output cost"

    def test_get_pricing_for_moonshot(self):
        """Verify pricing for Moonshot models."""
        pricing = get_model_pricing("moonshot/kimi-k2-thinking")
        assert pricing is not None
        input_cost, output_cost = pricing
        assert input_cost == 0.6, "Kimi K2 input: $0.60/M"
        assert output_cost == 2.5, "Kimi K2 output: $2.50/M"

    def test_get_pricing_unknown_model(self):
        """Verify unknown model returns None."""
        pricing = get_model_pricing("unknown/fake-model")
        assert pricing is None


class TestPrecedenceOrder:
    """Validate precedence order across all parameters."""

    def setup_method(self):
        ModelCapabilitiesResolver.capabilities.cache_clear()

    def test_models_dev_preferred_over_litellm(self):
        """Verify models.dev takes precedence over LiteLLM."""
        # moonshot/kimi-k2-thinking:
        # - models.dev says: reasoning=True
        # - LiteLLM says: reasoning=False
        # - Should use models.dev
        caps = get_capabilities("litellm", "moonshot/kimi-k2-thinking")
        assert caps.supports_reasoning is True, "models.dev should win"

    def test_static_patterns_fallback(self):
        """Verify static patterns work when models.dev unavailable."""
        # Test with model that might not be in models.dev
        caps = get_capabilities("bedrock", "claude-4-opus")
        # Static pattern should detect opus models
        assert caps.supports_reasoning is True

    def test_env_override_highest_precedence(self):
        """Verify ENV overrides everything else."""
        os.environ["CYBER_REASONING_DENY"] = "kimi"
        ModelCapabilitiesResolver.capabilities.cache_clear()

        # Even though models.dev says True, ENV should force False
        caps = get_capabilities("litellm", "moonshot/kimi-k2-thinking")
        assert caps.supports_reasoning is False, "ENV override highest precedence"

        del os.environ["CYBER_REASONING_DENY"]
        ModelCapabilitiesResolver.capabilities.cache_clear()

    def test_multiple_models_consistent(self):
        """Verify precedence works consistently across multiple models."""
        test_cases = [
            ("bedrock", "claude-sonnet-4-5", True),
            ("litellm", "azure/gpt-5", True),
            ("litellm", "moonshot/kimi-k2-thinking", True),
        ]

        for provider, model, expected_reasoning in test_cases:
            caps = get_capabilities(provider, model)
            assert (
                caps.supports_reasoning == expected_reasoning
            ), f"{model} reasoning should be {expected_reasoning}"


class TestTemperature:
    """Validate temperature capability."""

    def test_supports_temperature_default(self):
        """Verify temperature support defaults to True."""
        caps = get_capabilities("litellm", "unknown-model")
        assert caps.supports_temperature is True

    def test_supports_temperature_models_dev(self):
        """Verify temperature support from models.dev (if we had a model that doesn't support it)."""
        # We don't have a specific model in mind that has temperature=False in snapshot,
        # but we can verify it's present in the Capabilities object.
        caps = get_capabilities("litellm", "azure/gpt-5")
        assert hasattr(caps, "supports_temperature")
        assert isinstance(caps.supports_temperature, bool)

    def test_supports_temperature_false_via_models_dev_mock(self):
        """Verify supports_temperature is False when models.dev returns False."""
        with patch("modules.config.models.capabilities.get_models_client") as mock_get_client:
            mock_client = MagicMock()
            mock_get_client.return_value = mock_client
            
            # Create a mock ModelInfo object
            mock_info = MagicMock()
            mock_info.capabilities.temperature = False
            mock_info.capabilities.reasoning = True
            mock_info.capabilities.tool_call = True
            
            mock_client.get_model_info.return_value = mock_info
            
            # Clear cache to ensure our mock is used
            ModelCapabilitiesResolver.capabilities.cache_clear()
            
            caps = get_capabilities("litellm", "mock-model-no-temp")
            
            assert caps.supports_temperature is False, "Should be False when models.dev says so"
            assert caps.supports_reasoning is True
            assert caps.supports_tools is True


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        (None, ("", "")),
        ("OpenAI/GPT-5", ("openai", "GPT-5")),
        ("plain", ("", "plain")),
    ],
)
def test_capability_helpers_handle_prefixes_and_static_reasoning_models(model_id, expected):
    assert capabilities_module._split_prefix(model_id) == expected
    assert capabilities_module._static_supports_reasoning_model("moonshot/kimi-thinking") is True
    assert capabilities_module._static_supports_reasoning_model("gemini-3-pro-preview") is True
    assert capabilities_module._static_supports_reasoning_model("ordinary-model") is False
    assert capabilities_module.get_provider_default_limit("unknown") is None


def test_capability_limits_ignore_invalid_environment_overrides(monkeypatch):
    monkeypatch.setenv("MAX_COMPLETION_TOKENS", "not-a-number")
    monkeypatch.setenv("MAX_TOKENS", "also-bad")
    capabilities_module.get_model_output_limit.cache_clear()
    with patch.object(capabilities_module, "get_models_client", None):
        assert capabilities_module.get_model_output_limit("unknown") is None

    capabilities_module.get_model_output_limit.cache_clear()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("temperature is unsupported", "temperature"),
        ("top_k unexpected parameter", "top_k"),
        ("top_p is not allowed with this model", "top_p"),
        ("reasoning_effort invalid", "reasoning_effort"),
        ("thinking budget unsupported", "thinking"),
        ("effort does not support this request", "effort"),
        ("connection refused", None),
    ],
)
def test_classify_parameter_error_handles_supported_parameter_failures(message, expected):
    assert classify_parameter_error(RuntimeError(message)) == expected


def test_apply_parameter_fallback_handles_ollama_litellm_and_bedrock_models():
    ollama_model = type(
        "OllamaModel",
        (),
        {"config": {"additional_args": {"think": "medium"}, "temperature": 0.3, "options": {"top_p": 0.9}}},
    )()
    assert apply_parameter_fallback_to_model(ollama_model, "ollama", "model", "think") is True
    assert ollama_model.config["additional_args"]["think"] is True
    assert apply_parameter_fallback_to_model(ollama_model, "ollama", "model", "temperature") is True
    assert ollama_model.config["temperature"] is None
    assert apply_parameter_fallback_to_model(ollama_model, "ollama", "model", "top_p") is True
    assert "top_p" not in ollama_model.config["options"]

    litellm_model = type(
        "LiteLLMModel",
        (),
        {"params": {"top_k": 5}, "client_args": {"reasoning_effort": "high", "thinking": {}, "thinking_config": {}}},
    )()
    assert apply_parameter_fallback_to_model(litellm_model, "litellm", "model", "top_k") is True
    assert litellm_model.params == {}
    assert apply_parameter_fallback_to_model(litellm_model, "litellm", "model", "thinking") is True
    assert litellm_model.client_args == {}

    bedrock_model = type(
        "BedrockModel",
        (),
        {"temperature": 0.2, "additional_request_fields": {"output_config": {}, "thinking": {}}},
    )()
    assert apply_parameter_fallback_to_model(bedrock_model, "bedrock", "model", "temperature") is True
    assert bedrock_model.temperature is None
    assert apply_parameter_fallback_to_model(bedrock_model, "bedrock", "model", "effort") is True
    assert bedrock_model.additional_request_fields == {}


@pytest.mark.asyncio
async def test_model_fallback_wrapper_retries_stream_and_structured_output():
    class Model:
        def __init__(self):
            self.config = {"temperature": 0.2}
            self.stream_attempts = 0
            self.structured_attempts = 0

        async def stream(self):
            self.stream_attempts += 1
            if self.stream_attempts == 1:
                raise RuntimeError("temperature unsupported")
            yield {"kind": "stream"}

        async def structured_output(self):
            self.structured_attempts += 1
            if self.structured_attempts == 1:
                raise RuntimeError("temperature unsupported")
            yield {"kind": "structured"}

    model = wrap_model_with_fallback(Model(), "ollama", "test-model")

    assert [event async for event in model.stream()] == [{"kind": "stream"}]
    model.config["temperature"] = 0.2
    assert [event async for event in model.structured_output()] == [{"kind": "structured"}]
    assert (model.stream_attempts, model.structured_attempts) == (2, 2)
    assert model.config["temperature"] is None


def test_capability_resolver_uses_provider_parameter_metadata_without_network(monkeypatch):
    class ProviderConfig:
        def get_supported_openai_params(self, **_kwargs):
            return ["reasoning_effort", "tools", "tool_choice"]

        def get_model_info(self, **_kwargs):
            return {"supports_function_calling": True}

    class ProviderManager:
        @staticmethod
        def get_provider_chat_config(**_kwargs):
            return ProviderConfig()

    monkeypatch.setattr(capabilities_module, "get_models_client", None)
    monkeypatch.setattr(capabilities_module, "llm_supports_reasoning", lambda **_kwargs: False)
    monkeypatch.setattr(capabilities_module, "ProviderConfigManager", ProviderManager)
    monkeypatch.setattr(capabilities_module, "LlmProviders", lambda provider: provider)
    monkeypatch.setattr(capabilities_module, "ModelInfoBase", dict)
    ModelCapabilitiesResolver.capabilities.cache_clear()

    capabilities = get_capabilities("litellm", "openai/custom-model:variant")

    assert capabilities == Capabilities(
        supports_reasoning=True,
        pass_reasoning_effort=True,
        supports_tools=True,
        supports_tool_choice=True,
        supports_temperature=False,
    )


def test_capability_limit_and_pricing_helpers_cover_empty_static_and_unavailable_paths(monkeypatch):
    monkeypatch.setattr(capabilities_module, "get_models_client", None)
    capabilities_module.get_model_input_limit.cache_clear()
    capabilities_module.get_model_output_limit.cache_clear()
    capabilities_module.get_model_pricing.cache_clear()

    assert capabilities_module.get_model_input_limit("") is None
    assert capabilities_module.get_model_input_limit("gpt-4-test") == 128000
    assert capabilities_module.get_model_input_limit("unrecognized-model") is None
    assert capabilities_module.get_model_output_limit("") is None
    assert capabilities_module.get_model_output_limit("unrecognized-model") is None
    assert capabilities_module.get_model_pricing("") is None
    assert capabilities_module.get_model_pricing("unrecognized-model") is None
    assert capabilities_module.get_provider_default_limit("bedrock") == 200000
