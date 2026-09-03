#!/usr/bin/env python3
"""
Unit tests for the centralized model configuration system.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

# Import the modules we're testing
from modules.config.manager import (
    MAX_TOKENS_REASONING_LIMIT,
    ConfigManager,
    get_config_manager,
    get_default_model_configs,
    get_model_config,
    get_ollama_host,
    get_report_refinement_cycles,
)
from modules.config.system import validation
from modules.config.types import (
    DEFAULT_TEMPERATURE_EXECUTION,
    DEFAULT_TEMPERATURE_SWARM,
    BudgetConfig,
    EmbeddingConfig,
    EvaluationConfig,
    LLMConfig,
    MemoryConfig,
    MemoryEmbeddingConfig,
    MemoryLLMConfig,
    MemoryVectorStoreConfig,
    ModelConfig,
    ModelProvider,
    OutputConfig,
    SDKConfig,
    ServerConfig,
    SwarmConfig,
    get_default_base_dir,
)
from modules.tools.mcp import resolve_env_vars_in_dict, resolve_env_vars_in_list


class TestModelProvider:
    """Test ModelProvider enum."""

    def test_provider_values(self):
        """Test that all expected providers are available."""
        assert ModelProvider.AWS_BEDROCK.value == "aws_bedrock"
        assert ModelProvider.OLLAMA.value == "ollama"
        assert ModelProvider.LITELLM.value == "litellm"


class TestModelConfig:
    """Test ModelConfig dataclass."""

    def test_valid_config(self):
        """Test creating valid model configuration."""
        config = ModelConfig(
            provider=ModelProvider.OLLAMA,
            model_id="llama3.2:3b",
            parameters={"temperature": 0.5},
        )
        assert config.provider == ModelProvider.OLLAMA
        assert config.model_id == "llama3.2:3b"
        assert config.parameters["temperature"] == 0.5

    def test_empty_model_id_raises_error(self):
        """Test that empty model_id raises ValueError."""
        with pytest.raises(ValueError, match="model_id cannot be empty"):
            ModelConfig(provider=ModelProvider.OLLAMA, model_id="")

    def test_invalid_provider_raises_error(self):
        """Test that invalid provider raises ValueError."""
        with pytest.raises(ValueError, match="provider must be a ModelProvider enum"):
            ModelConfig(provider="invalid", model_id="test")


class TestLLMConfig:
    """Test LLMConfig dataclass."""

    def test_default_parameters(self):
        """Test LLM config with default parameters."""
        config = LLMConfig(provider=ModelProvider.OLLAMA, model_id="llama3.2:3b")
        assert config.temperature == DEFAULT_TEMPERATURE_EXECUTION
        assert config.max_tokens == 4096
        assert config.top_p is None  # Default is None (optional parameter)
        assert config.parameters["temperature"] == DEFAULT_TEMPERATURE_EXECUTION
        assert config.parameters["max_tokens"] == 4096
        assert "top_p" not in config.parameters  # Only included when explicitly set

    def test_custom_parameters(self):
        """Test LLM config with custom parameters."""
        config = LLMConfig(
            provider=ModelProvider.OLLAMA,
            model_id="llama3.2:3b",
            temperature=0.7,
            max_tokens=2000,
            top_p=0.8,
        )
        assert config.temperature == 0.7
        assert config.max_tokens == 2000
        assert config.top_p == 0.8
        assert config.parameters["temperature"] == 0.7


class TestEmbeddingConfig:
    """Test EmbeddingConfig dataclass."""

    def test_default_dimensions(self):
        """Test embedding config with default dimensions."""
        config = EmbeddingConfig(provider=ModelProvider.OLLAMA, model_id="mxbai-embed-large:latest")
        assert config.dimensions == 1024
        assert config.parameters["dimensions"] == 1024

    def test_custom_dimensions(self):
        """Test embedding config with custom dimensions."""
        config = EmbeddingConfig(provider=ModelProvider.OLLAMA, model_id="mxbai-embed-large:latest", dimensions=512)
        assert config.dimensions == 512
        assert config.parameters["dimensions"] == 512


class TestMemoryLLMConfig:
    """Test MemoryLLMConfig dataclass."""

    def test_default_parameters(self):
        """Test memory LLM config with default parameters."""
        config = MemoryLLMConfig(provider=ModelProvider.OLLAMA, model_id="llama3.2:3b")
        assert config.temperature == 0.1
        assert config.max_tokens == 2000
        assert config.aws_region == "us-east-1"
        assert config.parameters["temperature"] == 0.1
        assert config.parameters["max_tokens"] == 2000
        assert config.parameters["aws_region"] == "us-east-1"

    def test_custom_parameters(self):
        """Test memory LLM config with custom parameters."""
        config = MemoryLLMConfig(
            provider=ModelProvider.OLLAMA,
            model_id="llama3.2:3b",
            temperature=0.2,
            max_tokens=1500,
            aws_region="eu-west-1",
        )
        assert config.temperature == 0.2
        assert config.max_tokens == 1500
        assert config.aws_region == "eu-west-1"
        assert config.parameters["temperature"] == 0.2


class TestMemoryEmbeddingConfig:
    """Test MemoryEmbeddingConfig dataclass."""

    def test_default_parameters(self):
        """Test memory embedding config with default parameters."""
        config = MemoryEmbeddingConfig(provider=ModelProvider.OLLAMA, model_id="mxbai-embed-large:latest")
        assert config.aws_region == "us-east-1"
        assert config.dimensions == 1024
        assert config.parameters["aws_region"] == "us-east-1"
        assert config.parameters["dimensions"] == 1024

    def test_custom_parameters(self):
        """Test memory embedding config with custom parameters."""
        config = MemoryEmbeddingConfig(
            provider=ModelProvider.OLLAMA,
            model_id="mxbai-embed-large:latest",
            aws_region="eu-west-1",
            dimensions=512,
        )
        assert config.aws_region == "eu-west-1"
        assert config.dimensions == 512
        assert config.parameters["aws_region"] == "eu-west-1"


class TestMemoryVectorStoreConfig:
    """Test MemoryVectorStoreConfig dataclass."""

    def test_default_provider(self):
        """Test default vector store configuration."""
        config = MemoryVectorStoreConfig()
        assert config.provider == "qdrant"
        assert config.qdrant_config["embedding_model_dims"] == 1024

    def test_qdrant_config(self):
        """Test Qdrant configuration."""
        config = MemoryVectorStoreConfig()
        qdrant_config = config.get_config_for_provider("qdrant")
        assert qdrant_config["collection_name"] == "cyber_autoagent_memories"
        assert qdrant_config["embedding_model_dims"] == 1024

    def test_config_overrides(self):
        """Test configuration overrides."""
        config = MemoryVectorStoreConfig()
        qdrant_config = config.get_config_for_provider("qdrant", collection_name="custom")
        assert qdrant_config["collection_name"] == "custom"
        assert qdrant_config["embedding_model_dims"] == 1024

    def test_non_qdrant_provider_returns_only_overrides(self):
        assert MemoryVectorStoreConfig().get_config_for_provider("other", endpoint="memory.test") == {
            "endpoint": "memory.test"
        }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_duration_minutes": 0}, "max_duration_minutes"),
        ({"max_duration_minutes": 1, "max_tokens": 0}, "max_tokens"),
        ({"max_duration_minutes": 1, "max_cost": 0}, "max_cost"),
    ],
)
def test_budget_config_rejects_non_positive_limits(kwargs, message):
    with pytest.raises(ValueError, match=message):
        BudgetConfig(**kwargs)


class TestConfigManager:
    """Test ConfigManager class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.config_manager = ConfigManager()

    def test_initialization(self):
        """Test ConfigManager initialization."""
        assert self.config_manager._config_cache == {}
        assert "ollama" in self.config_manager._default_configs
        assert "bedrock" in self.config_manager._default_configs

    def test_get_local_server_config(self):
        """Test getting local server configuration."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear cache to ensure fresh config
            self.config_manager._config_cache = {}
            config = self.config_manager.get_server_config("ollama")

            assert config.server_type == "ollama"
            assert config.llm.provider == ModelProvider.OLLAMA
            assert config.llm.model_id == "qwen3.6:27b"
            assert config.embedding.provider == ModelProvider.OLLAMA
            assert config.embedding.model_id == "mxbai-embed-large:latest"
            assert config.region == "ollama"

    def test_get_remote_server_config(self):
        """Test getting remote server configuration."""
        config = self.config_manager.get_server_config("bedrock")

        assert config.server_type == "bedrock"
        assert config.llm.provider == ModelProvider.AWS_BEDROCK
        # Model can be sonnet or opus depending on config
        assert "claude" in config.llm.model_id.lower() or "anthropic" in config.llm.model_id.lower()
        assert config.embedding.provider == ModelProvider.AWS_BEDROCK
        assert "titan-embed" in config.embedding.model_id
        assert config.region == "us-east-1"

    def test_invalid_server_type(self):
        """Test that invalid server type raises error."""
        with pytest.raises(ValueError, match="Unsupported provider type"):
            self.config_manager.get_server_config("invalid")

    def test_config_caching(self):
        """Test that configurations are cached properly."""
        # Clear cache first
        self.config_manager._config_cache = {}

        # First call should cache the result
        config1 = self.config_manager.get_server_config("ollama")
        assert len(self.config_manager._config_cache) == 1

        # Second call should return cached result
        config2 = self.config_manager.get_server_config("ollama")
        assert config1 is config2
        assert len(self.config_manager._config_cache) == 1

    def test_get_llm_config(self):
        """Test getting LLM configuration."""
        config = self.config_manager.get_llm_config("ollama")

        assert isinstance(config, LLMConfig)
        assert config.provider == ModelProvider.OLLAMA
        assert config.model_id == "qwen3.6:27b"

    def test_get_embedding_config(self):
        """Test getting embedding configuration."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear cache to ensure fresh config
            self.config_manager._config_cache = {}
            config = self.config_manager.get_embedding_config("ollama")

            assert isinstance(config, EmbeddingConfig)
            assert config.provider == ModelProvider.OLLAMA
            assert config.model_id == "mxbai-embed-large:latest"

    def test_get_memory_config(self):
        """Test getting memory configuration."""
        config = self.config_manager.get_memory_config("ollama")

        assert isinstance(config, MemoryConfig)
        assert isinstance(config.embedder, MemoryEmbeddingConfig)
        assert config.embedder.provider == ModelProvider.OLLAMA
        assert isinstance(config.llm, MemoryLLMConfig)
        assert config.llm.provider == ModelProvider.OLLAMA
        assert isinstance(config.vector_store, MemoryVectorStoreConfig)

    def test_get_evaluation_config(self):
        """Test getting evaluation configuration."""
        config = self.config_manager.get_evaluation_config("ollama")

        assert isinstance(config, EvaluationConfig)
        assert config.llm.provider == ModelProvider.OLLAMA
        assert config.embedding.provider == ModelProvider.OLLAMA

    def test_get_sdk_config_uses_default_streaming_mode(self):
        """Test SDK streaming defaults to the dataclass default."""
        with patch.dict(os.environ, {}, clear=True):
            self.config_manager._config_cache = {}

            config = self.config_manager.get_sdk_config("ollama")

        assert isinstance(config, SDKConfig)
        assert config.enable_streaming is False

    @patch.dict(os.environ, {"CYBER_SDK_ENABLE_STREAMING": "true"}, clear=True)
    def test_get_sdk_config_reads_streaming_mode_from_environment(self):
        """Test SDK streaming can be enabled through environment config."""
        self.config_manager._config_cache = {}

        config = self.config_manager.get_sdk_config("ollama")

        assert config.enable_streaming is True

    @patch.dict(os.environ, {"CYBER_SDK_ENABLE_STREAMING": "true"}, clear=True)
    def test_get_sdk_config_streaming_override_takes_precedence(self):
        """Test explicit SDK streaming overrides take precedence over environment config."""
        self.config_manager._config_cache = {}

        config = self.config_manager.get_sdk_config("ollama", enable_streaming=False)

        assert config.enable_streaming is False

    def test_get_swarm_config(self):
        """Test getting swarm configuration."""
        # Test local swarm config
        local_config = self.config_manager.get_swarm_config("ollama")
        assert isinstance(local_config, SwarmConfig)
        assert local_config.llm.provider == ModelProvider.OLLAMA
        assert local_config.llm.model_id == "qwen3.6:27b"
        assert local_config.llm.temperature == DEFAULT_TEMPERATURE_SWARM
        assert local_config.llm.max_tokens == 16000

        # Test remote swarm config
        remote_config = self.config_manager.get_swarm_config("bedrock")
        assert isinstance(remote_config, SwarmConfig)
        assert remote_config.llm.provider == ModelProvider.AWS_BEDROCK
        assert "claude" in remote_config.llm.model_id
        assert remote_config.llm.temperature == DEFAULT_TEMPERATURE_SWARM
        assert remote_config.llm.max_tokens == 16_000

    def test_get_qdrant_memory_config(self):
        """Test Qdrant embedding configuration for local and remote providers."""
        with patch.dict(os.environ, {}, clear=True):
            self.config_manager._config_cache = {}
            local_config = self.config_manager.get_qdrant_memory_config("ollama")
            assert local_config["embedding_provider"] == "ollama"
            assert local_config["embedding_model"] == "mxbai-embed-large:latest"
            assert local_config["ollama_base_url"].startswith("http://")

            remote_config = self.config_manager.get_qdrant_memory_config("bedrock")
            assert remote_config["embedding_provider"] == "bedrock"
            assert "titan-embed" in remote_config["embedding_model"]
            assert remote_config["aws_region"] == "us-east-1"
            assert remote_config["collection_name"] == "cyber_autoagent_memories"

    @patch.dict(os.environ, {"CYBER_AGENT_LLM_MODEL": "custom-llm"})
    def test_environment_variable_override(self):
        """Test that environment variables override default config."""
        # Clear cache to force re-evaluation
        self.config_manager._config_cache = {}

        config = self.config_manager.get_server_config("ollama")
        assert config.llm.model_id == "custom-llm"

    @patch.dict(os.environ, {"CYBER_AGENT_SWARM_MODEL": "custom-swarm-model"})
    def test_swarm_model_environment_variable_override(self):
        """Test that swarm model can be overridden with environment variables."""
        # Clear cache to force re-evaluation
        self.config_manager._config_cache = {}

        config = self.config_manager.get_server_config("ollama")
        assert config.swarm.llm.model_id == "custom-swarm-model"

    @patch.dict(os.environ, {
        "CYBER_AGENT_PROVIDER": "litellm",
        "CYBER_AGENT_LLM_MODEL": "xai/grok-4-fast-reasoning",
        "CYBER_AGENT_EMBEDDING_MODEL": "bedrock/amazon.titan-embed-text-v2:0",
        "XAI_API_KEY": "test-key",
        "AWS_BEARER_TOKEN_BEDROCK": "test-token",
        "AWS_REGION": "us-east-1",
    }, clear=True)
    def test_litellm_gemini_configuration(self):
        """Ensure LiteLLM + hybrid configuration (XAI LLM + Bedrock embeddings) works."""
        self.config_manager._config_cache = {}

        config = self.config_manager.get_server_config("litellm")

        # Verify LLM configuration
        assert config.llm.model_id == "xai/grok-4-fast-reasoning"
        assert config.llm.provider == ModelProvider.LITELLM

        # Verify embedding configuration (explicit override)
        assert config.embedding.model_id == "bedrock/amazon.titan-embed-text-v2:0"
        assert config.embedding.provider == ModelProvider.LITELLM

        # Verify memory configs aligned
        assert config.memory.llm.model_id == "xai/grok-4-fast-reasoning"
        assert config.memory.llm.provider == ModelProvider.LITELLM
        assert config.swarm.llm.model_id == "xai/grok-4-fast-reasoning"

        # Should not raise for missing AWS credentials (XAI_API_KEY is present)
        self.config_manager.validate_requirements("litellm")

    def test_parameter_overrides(self):
        """Test that function parameters override configuration."""
        # This would require more complex override logic
        # For now, just test that the method accepts overrides
        config = self.config_manager.get_server_config("ollama", custom_param="value")
        assert config.server_type == "ollama"

    @patch("modules.config.manager.os.path.exists")
    @patch("modules.config.system.validation.requests.get")
    def test_get_ollama_host_docker(self, mock_get, mock_exists):
        """Test Ollama host detection in Docker environment."""
        mock_exists.return_value = True  # Simulate Docker environment
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        host = self.config_manager.get_ollama_host()
        assert host == "http://localhost:11434"

    @patch("modules.config.manager.os.path.exists")
    def test_get_ollama_host_native(self, mock_exists):
        """Test Ollama host detection in native environment."""
        mock_exists.return_value = False  # Simulate native environment

        host = self.config_manager.get_ollama_host()
        assert host == "http://localhost:11434"

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://custom:11434"})
    def test_get_ollama_host_environment_override(self):
        """Test that the OLLAMA_HOST environment variable overrides detection."""
        host = self.config_manager.get_ollama_host()
        assert host == "http://custom:11434"

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://custom:11434", "OLLAMA_TIMEOUT": "3600.2" })
    def test_get_ollama_timeout_environment_override(self):
        """Test that the OLLAMA_TIMEOUT environment variable overrides defaults."""
        timeout = self.config_manager.get_ollama_timeout()
        assert timeout == 3600.2

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://custom:11434", "OLLAMA_KEEP_ALIVE": "15m" })
    def test_get_ollama_keep_alive_environment_override(self):
        """Test that the OLLAMA_KEEP_ALIVE environment variable overrides defaults."""
        keep_alive = self.config_manager.get_ollama_keep_alive()
        assert keep_alive == "15m"

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://custom:11434"})
    def test_get_ollama_keep_alive_default(self):
        """Test the keep alive default."""
        keep_alive = self.config_manager.get_ollama_keep_alive()
        assert keep_alive == "30m"

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://custom:11434", "OLLAMA_KEEP_ALIVE": ""})
    def test_get_ollama_keep_alive_enviroment_empty(self):
        """Test the keep alive default."""
        keep_alive = self.config_manager.get_ollama_keep_alive()
        assert keep_alive == "30m"

    @patch("modules.config.system.validation.requests.get")
    def test_validate_ollama_requirements_success(self, mock_get):
        """Test successful Ollama requirements validation."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with patch("ollama.Client") as mock_client:
            # Clear environment to ensure we get the expected local config
            with patch.dict(os.environ, {}, clear=True):
                mock_client.return_value.list.return_value = {
                    "models": [{"model": "llama3.2:3b"}, {"model": "mxbai-embed-large:latest"}]
                }

                # Should not raise an exception
                self.config_manager.validate_requirements("ollama")

    @patch("modules.config.system.validation.requests.get")
    def test_validate_ollama_requirements_server_down(self, mock_get):
        """Test Ollama requirements validation when server is down."""
        mock_get.side_effect = ConnectionError("Connection refused")

        with pytest.raises(ConnectionError, match="Ollama server not accessible"):
            self.config_manager.validate_requirements("ollama")

    @patch.dict(os.environ, {}, clear=True)
    def test_validate_aws_requirements_no_credentials(self):
        """Test AWS requirements validation without credentials."""
        with pytest.raises(EnvironmentError, match="AWS credentials not configured"):
            self.config_manager.validate_requirements("bedrock")

    @patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_REGION": "us-east-1",
        },
    )
    @patch("boto3.client")
    def test_validate_bedrock_model_access_success(self, mock_boto_client):
        """Test successful Bedrock model validation."""
        # Mock bedrock client
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = {
            "modelSummaries": [
                {"modelId": "us.anthropic.claude-sonnet-4-20250514-v1:0"},
                {"modelId": "amazon.titan-embed-text-v2:0"},
            ]
        }

        # Mock bedrock-runtime client
        mock_runtime = MagicMock()
        mock_runtime.invoke_model.return_value = {"statusCode": 200}

        # Configure boto3.client to return appropriate mocks
        def client_side_effect(service_name, **kwargs):
            if service_name == "bedrock":
                return mock_bedrock
            elif service_name == "bedrock-runtime":
                return mock_runtime
            return MagicMock()

        mock_boto_client.side_effect = client_side_effect

        # Should not raise an exception
        self.config_manager.validate_requirements("bedrock")

        # Verify bedrock-runtime client was created
        mock_boto_client.assert_any_call("bedrock-runtime", region_name="us-east-1")

    @patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_REGION": "us-east-1",
        },
    )
    @patch("boto3.client")
    def test_validate_bedrock_service_access_denied(self, mock_boto_client):
        """Test Bedrock validation when service access is denied."""
        from botocore.exceptions import ClientError

        mock_runtime = MagicMock()
        mock_runtime.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
            "client",
        )

        mock_boto_client.side_effect = mock_runtime

        # Should not raise an exception - errors are handled by strands-agents
        self.config_manager.validate_requirements("bedrock")

    @patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_REGION": "us-east-1",
        },
    )
    @patch("boto3.client")
    def test_validate_bedrock_missing_models(self, mock_boto_client):
        """Test Bedrock validation when required models are missing."""
        # The new implementation delegates model validation to strands-agents
        # So this test now verifies that validation completes without error
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = {"modelSummaries": [{"modelId": "some.other.model:1.0"}]}

        mock_boto_client.return_value = mock_bedrock

        # Should not raise - model validation is handled by strands-agents
        self.config_manager.validate_requirements("bedrock")

    @patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_REGION": "us-east-1",
        },
    )
    @patch("boto3.client")
    def test_validate_bedrock_model_access_denied(self, mock_boto_client):
        """Test Bedrock validation when model access is denied."""
        from botocore.exceptions import ClientError

        # Mock runtime invoke failure
        mock_runtime = MagicMock()
        mock_runtime.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"}},
            "InvokeModel",
        )

        mock_boto_client.side_effect = mock_runtime

        # Should not raise an exception - model errors handled by strands-agents
        self.config_manager.validate_requirements("bedrock")

    @patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test"})
    @patch("boto3.client")
    def test_validate_bedrock_no_region(self, mock_boto_client):
        """Test Bedrock validation when region returns None."""
        with patch.object(self.config_manager, "get_default_region", return_value=None):
            with pytest.raises(EnvironmentError, match="AWS region not configured"):
                self.config_manager.validate_requirements("bedrock")

    @patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "test-key",
            "AWS_SECRET_ACCESS_KEY": "test-secret",
            "AWS_REGION": "us-east-1",
        },
    )
    @patch("boto3.client")
    def test_validate_aws_requirements_with_credentials(self, mock_boto_client):
        """Test AWS requirements validation with credentials."""
        # Mock bedrock client
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = {
            "modelSummaries": [
                {"modelId": "us.anthropic.claude-sonnet-4-20250514-v1:0"},
                {"modelId": "amazon.titan-embed-text-v2:0"},
            ]
        }

        # Mock bedrock-runtime client
        mock_runtime = MagicMock()
        mock_runtime.invoke_model.return_value = {"statusCode": 200}

        def client_side_effect(service_name, **kwargs):
            if service_name == "bedrock":
                return mock_bedrock
            elif service_name == "bedrock-runtime":
                return mock_runtime
            return MagicMock()

        mock_boto_client.side_effect = client_side_effect

        # Should not raise an exception
        self.config_manager.validate_requirements("bedrock")

    @patch.dict(os.environ, {"AWS_PROFILE": "test-profile", "AWS_REGION": "us-east-1"})
    @patch("boto3.client")
    def test_validate_aws_requirements_with_profile(self, mock_boto_client):
        """Test AWS requirements validation with profile."""
        # Mock bedrock client
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = {
            "modelSummaries": [
                {"modelId": "us.anthropic.claude-sonnet-4-20250514-v1:0"},
                {"modelId": "amazon.titan-embed-text-v2:0"},
            ]
        }

        # Mock bedrock-runtime client
        mock_runtime = MagicMock()
        mock_runtime.invoke_model.return_value = {"statusCode": 200}

        def client_side_effect(service_name, **kwargs):
            if service_name == "bedrock":
                return mock_bedrock
            elif service_name == "bedrock-runtime":
                return mock_runtime
            return MagicMock()

        mock_boto_client.side_effect = client_side_effect

        # Should not raise an exception
        self.config_manager.validate_requirements("bedrock")

    def test_set_environment_variables_local(self):
        """Test setting environment variables for local mode."""
        with patch.dict(os.environ, {}, clear=True):
            self.config_manager.set_environment_variables("ollama")

            assert os.environ["CYBER_AGENT_EMBEDDING_MODEL"] == "mxbai-embed-large:latest"

    def test_set_environment_variables_remote(self):
        """Test setting environment variables for remote mode."""
        with patch.dict(os.environ, {}, clear=True):
            self.config_manager.set_environment_variables("bedrock")

            assert "titan-embed" in os.environ["CYBER_AGENT_EMBEDDING_MODEL"]

    @patch.dict(os.environ, {"CYBER_MCP_ENABLED": "false"})
    def test_get_mcp_config_disabled(self):
        """Test MCP empty configuration."""
        # Clear cache to ensure fresh config
        self.config_manager._config_cache = {}

        config = self.config_manager.get_mcp_config("bedrock")

        assert not config.enabled

    @patch.dict(os.environ, {
        "CYBER_MCP_ENABLED": "true",
        "CYBER_MCP_CONNECTIONS": "[]",
    })
    def test_get_mcp_config_empty(self):
        """Test MCP empty configuration."""
        # Clear cache to ensure fresh config
        self.config_manager._config_cache = {}

        config = self.config_manager.get_mcp_config("bedrock")

        assert config.enabled

    @patch.dict(os.environ, {
        "CYBER_MCP_ENABLED": "true",
        "CYBER_MCP_CONNECTIONS": """
[
    {
        "id": "mcp1",
        "transport": "stdio",
        "command": ["python3","-m","mymcp.server"],
        "plugins": ["web"],
        "timeoutSeconds": 900
    },
    {
        "id": "mcp2",
        "transport": "streamable-http",
        "server_url": "http://127.0.0.1:8000/mcp",
        "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
        "plugins": ["web","ctf"],
        "allowedTools": ["tool1", "tool2"]
    },
    {
        "id": "mcp3",
        "transport": "sse",
        "server_url": "http://127.0.0.1:8000/sse",
        "command": [],
        "plugins": ["*"]
    }
]
""",
    })
    def test_get_mcp_config_three(self):
        """Test two MCP servers configuration."""
        # Clear cache to ensure fresh config
        self.config_manager._config_cache = {}

        config = self.config_manager.get_mcp_config("bedrock")

        assert config.enabled
        assert len(config.connections) == 3

        mcp = config.connections[0]
        assert mcp.id == "mcp1"
        assert mcp.transport == "stdio"
        assert mcp.command == ["python3","-m","mymcp.server"]
        assert mcp.plugins == ["web"]
        assert mcp.timeoutSeconds == 900
        assert mcp.allowed_tools == ['*']

        mcp = config.connections[1]
        assert mcp.id == "mcp2"
        assert mcp.transport == "streamable-http"
        assert mcp.server_url == "http://127.0.0.1:8000/mcp"
        assert mcp.headers == {"Authorization": "Bearer ${MCP_TOKEN}"}
        assert mcp.plugins == ["web","ctf"]
        # allowed_tools can be specific tools or wildcard '*'
        assert mcp.allowed_tools is not None

        mcp = config.connections[2]
        assert mcp.id == "mcp3"
        assert mcp.transport == "sse"
        assert mcp.server_url == "http://127.0.0.1:8000/sse"
        assert mcp.command is None
        assert mcp.headers is None
        assert mcp.plugins == ["*"]
        assert mcp.allowed_tools == ['*']


    @patch.dict(os.environ, {
        "CYBER_MCP_ENABLED": "true",
        "CYBER_MCP_CONNECTIONS": """
[
    {
        "id": "mcp1",
        "transport": "stdio",
        "command": ["python3","-m","mymcp.server"]
    },
    {
        "id": "mcp1",
        "transport": "streamable-http",
        "server_url": "http://127.0.0.1:8000/mcp"
    }
]
""",
    })
    def test_get_mcp_config_duplicate_id_validation(self):
        """Test MCP duplicate ID configuration."""
        # Clear cache to ensure fresh config
        self.config_manager._config_cache = {}

        with pytest.raises(ValueError, match="id property must be unique"):
            self.config_manager.get_mcp_config("bedrock")

    @patch.dict(os.environ, {
        "CYBER_MCP_ENABLED": "true",
        "CYBER_MCP_CONNECTIONS": """[{"id": "mcp1","transport": "stdio","server_url": "http://127.0.0.1:8000/mcp"}]""",
    })
    def test_get_mcp_config_stdio_command_validation(self):
        """Test MCP stdio requires command property."""
        # Clear cache to ensure fresh config
        self.config_manager._config_cache = {}

        with pytest.raises(ValueError, match="stdio transport requires the command property"):
            self.config_manager.get_mcp_config("bedrock")

    @patch.dict(os.environ, {
        "CYBER_MCP_ENABLED": "true",
        "CYBER_MCP_CONNECTIONS": """[{"id": "mcp1","transport": "sse","command": ["python3","-m","mymcp.server"]}]""",
    })
    def test_get_mcp_config_sse_command_validation(self):
        """Test MCP see does not use the command property."""
        # Clear cache to ensure fresh config
        self.config_manager._config_cache = {}

        with pytest.raises(ValueError, match="network transports do not use the command property"):
            self.config_manager.get_mcp_config("bedrock")

    @patch.dict(os.environ, {
        "CYBER_MCP_ENABLED": "true",
        "CYBER_MCP_CONNECTIONS": """[{"id": "mcp1","transport": "streamable-http"}]""",
    })
    def test_get_mcp_config_streamable_http_server_url_validation(self):
        """Test MCP streamable-http requires server_url property."""
        # Clear cache to ensure fresh config
        self.config_manager._config_cache = {}

        with pytest.raises(ValueError, match="network transports require the server_url property"):
            self.config_manager.get_mcp_config("bedrock")

    @patch.dict(os.environ, {
        "CYBER_MCP_ENABLED": "true",
        "CYBER_MCP_CONNECTIONS": """[{"id": "mcp1","transport": "telnet"}]""",
    })
    def test_get_mcp_config_transport_validation(self):
        """Test MCP validate transport property."""
        # Clear cache to ensure fresh config
        self.config_manager._config_cache = {}

        with pytest.raises(ValueError, match="does not have a valid transport"):
            self.config_manager.get_mcp_config("bedrock")

    def test_resolve_env_vars_in_dict_none_input(self):
        env = {"VAR": "value"}
        assert resolve_env_vars_in_dict(None, env) == {}

    def test_resolve_env_vars_in_dict_empty(self):
        env = {"VAR": "value"}
        assert resolve_env_vars_in_dict({}, env) == {}

    def test_resolve_env_vars_in_dict_single_var(self):
        env = {"TOKEN": "secret-token"}
        input_dict = {"Authorization": "Bearer ${TOKEN}"}
        result = resolve_env_vars_in_dict(input_dict, env)
        assert result == {"Authorization": "Bearer secret-token"}

    def test_resolve_env_vars_in_dict_multiple_vars_in_one_value(self):
        env = {"USER": "alice", "ID": "42"}
        input_dict = {"info": "user=${USER}, id=${ID}"}
        result = resolve_env_vars_in_dict(input_dict, env)
        assert result == {"info": "user=alice, id=42"}

    def test_resolve_env_vars_in_dict_repeated_var(self):
        env = {"VAR": "x"}
        input_dict = {"pattern": "${VAR}-${VAR}-${VAR}"}
        result = resolve_env_vars_in_dict(input_dict, env)
        assert result == {"pattern": "x-x-x"}

    def test_resolve_env_vars_in_dict_unknown_var_left_intact(self):
        env = {}
        input_dict = {"Authorization": "Bearer ${MISSING}"}
        result = resolve_env_vars_in_dict(input_dict, env)
        assert result == {"Authorization": "Bearer ${MISSING}"}

    def test_resolve_env_vars_in_dict_mixed_known_and_unknown(self):
        env = {"KNOWN": "yes"}
        input_dict = {"value": "${KNOWN}/${UNKNOWN}"}
        result = resolve_env_vars_in_dict(input_dict, env)
        assert result == {"value": "yes/${UNKNOWN}"}

    def test_resolve_env_vars_in_dict_value_without_placeholders_unchanged(self):
        env = {"VAR": "x"}
        input_dict = {"plain": "no placeholders here"}
        result = resolve_env_vars_in_dict(input_dict, env)
        assert result == {"plain": "no placeholders here"}

    def test_resolve_env_vars_in_dict_keys_unchanged(self):
        env = {"VAR": "x"}
        input_dict = {"${VAR}": "value ${VAR}"}
        result = resolve_env_vars_in_dict(input_dict, env)
        # keys are not touched, only values
        assert "${VAR}" in result
        assert result["${VAR}"] == "value x"

    def test_resolve_env_vars_in_list_none_input(self):
        env = {"VAR": "value"}
        assert resolve_env_vars_in_list(None, env) == []

    def test_resolve_env_vars_in_list_empty(self):
        env = {"VAR": "value"}
        assert resolve_env_vars_in_list([], env) == []

    def test_resolve_env_vars_in_list_single_element(self):
        env = {"HOST": "localhost", "PORT": "8080"}
        input_list = ["http://${HOST}:${PORT}/api"]
        result = resolve_env_vars_in_list(input_list, env)
        assert result == ["http://localhost:8080/api"]

    def test_resolve_env_vars_in_list_multiple_elements(self):
        env = {"USER": "alice", "HOME": "/home/alice"}
        input_list = [
            "user=${USER}",
            "home=${HOME}",
            "no-vars-here",
        ]
        result = resolve_env_vars_in_list(input_list, env)
        assert result == [
            "user=alice",
            "home=/home/alice",
            "no-vars-here",
        ]

    def test_resolve_env_vars_in_list_unknown_var_left_intact(self):
        env = {}
        input_list = ["${UNKNOWN} and more"]
        result = resolve_env_vars_in_list(input_list, env)
        assert result == ["${UNKNOWN} and more"]

    def test_resolve_env_vars_in_list_repeated_var(self):
        env = {"X": "1"}
        input_list = ["${X}${X}${X}"]
        result = resolve_env_vars_in_list(input_list, env)
        assert result == ["111"]

    def test_resolve_env_vars_in_list_adjacent_placeholders(self):
        env = {"A": "foo", "B": "bar"}
        input_list = ["${A}${B}"]
        result = resolve_env_vars_in_list(input_list, env)
        assert result == ["foobar"]


class TestGlobalFunctions:
    """Test global convenience functions."""

    def test_get_config_manager_singleton(self):
        """Test that get_config_manager returns singleton instance."""
        manager1 = get_config_manager()
        manager2 = get_config_manager()
        assert manager1 is manager2

    def test_get_report_refinement_cycles_clamps_invalid_values(self):
        manager = MagicMock()
        manager.getenv_int.side_effect = [3, -1, True]

        assert get_report_refinement_cycles(manager) == 3
        assert get_report_refinement_cycles(manager) == 0
        assert get_report_refinement_cycles(manager) == 2

    def test_get_model_config(self):
        """Test get_model_config function."""
        config = get_model_config("ollama")
        assert isinstance(config, ServerConfig)
        assert config.server_type == "ollama"

    def test_get_default_model_configs_backward_compatibility(self):
        """Test backward compatibility function."""
        config = get_default_model_configs("ollama")

        assert isinstance(config, dict)
        assert "llm_model" in config
        assert "embedding_model" in config
        assert "embedding_dims" in config
        assert config["llm_model"] == "qwen3.6:27b"
        assert config["embedding_model"] == "mxbai-embed-large:latest"
        assert config["embedding_dims"] == 1024

    def test_get_ollama_host_backward_compatibility(self):
        """Test backward compatibility function."""
        host = get_ollama_host()
        assert host.startswith("http://")
        assert "11434" in host


class TestEnvironmentIntegration:
    """Test environment variable integration."""

    def test_multiple_environment_overrides(self):
        """Test multiple environment variable overrides."""
        env_vars = {
            "CYBER_AGENT_LLM_MODEL": "custom-llm",
            "CYBER_AGENT_EMBEDDING_MODEL": "custom-embedding",
            "CYBER_AGENT_EVALUATION_MODEL": "custom-evaluator",
            "AWS_REGION": "us-west-2",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config_manager = ConfigManager()
            config = config_manager.get_server_config("bedrock")

            assert config.llm.model_id == "custom-llm"
            assert config.embedding.model_id == "custom-embedding"
            assert config.evaluation.llm.model_id == "custom-evaluator"
            assert config.region == "us-west-2"

    def test_centralized_region_configuration(self):
        """Test that AWS regions are centralized and consistent across all components."""
        # Test with custom region
        with patch.dict(os.environ, {"AWS_REGION": "eu-west-1"}, clear=True):
            config_manager = ConfigManager()

            # Test get_default_region method
            assert config_manager.get_default_region() == "eu-west-1"

            # Test server config uses centralized region
            server_config = config_manager.get_server_config("bedrock")
            assert server_config.region == "eu-west-1"

            # Test memory config uses centralized region
            memory_config = config_manager.get_memory_config("bedrock")
            assert memory_config.llm.aws_region == "eu-west-1"
            assert memory_config.embedder.aws_region == "eu-west-1"

            qdrant_config = config_manager.get_qdrant_memory_config("bedrock")
            assert qdrant_config["aws_region"] == "eu-west-1"

        # Test without environment variable (should use default)
        with patch.dict(os.environ, {}, clear=True):
            config_manager = ConfigManager()

            # Test get_default_region method
            assert config_manager.get_default_region() == "us-east-1"

            # Test server config uses default region
            server_config = config_manager.get_server_config("bedrock")
            assert server_config.region == "us-east-1"

    def test_thinking_models_configuration(self):
        """Test centralized thinking models configuration."""
        config_manager = ConfigManager()

        assert not config_manager.is_thinking_model("bedrock", "global.anthropic.claude-opus-4-5-20251101-v1:0")
        assert not config_manager.is_thinking_model("bedrock", "us.anthropic.claude-opus-4-5-20251101-v1:0")
        assert not config_manager.is_thinking_model("bedrock", "us.anthropic.claude-opus-4-20250514-v1:0")
        assert config_manager.is_thinking_model("bedrock", "us.anthropic.claude-sonnet-4-20250514-v1:0")
        assert not config_manager.is_thinking_model("bedrock", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")
        assert config_manager.is_thinking_model("litellm", "nvidia_nim/moonshotai/kimi-k2.6")
        assert not config_manager.is_thinking_model("venice", "llama-3.2-3b")
        assert not config_manager.is_thinking_model("not_a_provider", "llama-3.2-3b")

    @patch.dict(os.environ, {"OLLAMA_CONTEXT_LENGTH": "32768"}, clear=True)
    def test_centralized_model_configuration_methods(self):
        """Test the new centralized model configuration methods."""
        config_manager = ConfigManager()

        # Test thinking model configuration
        thinking_config = config_manager.get_thinking_model_config(
            "us.anthropic.claude-opus-4-20250514-v1:0", "us-east-1"
        )
        assert thinking_config["temperature"] == 1.0
        assert thinking_config["max_tokens"] == 32_000
        assert "additional_request_fields" in thinking_config
        assert "anthropic_beta" in thinking_config["additional_request_fields"]
        assert "thinking" in thinking_config["additional_request_fields"]

        # Test standard model configuration
        standard_config = config_manager.get_standard_model_config(
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "us-east-1", "bedrock"
        )
        assert standard_config["temperature"] == DEFAULT_TEMPERATURE_EXECUTION
        assert standard_config["max_tokens"] == MAX_TOKENS_REASONING_LIMIT  # clamped
        # top_p is now optional (not included for Anthropic models to avoid conflicts)

        # Test local model configuration
        local_config = config_manager.get_local_model_config("llama3.2:3b", "ollama")
        assert local_config["temperature"] == DEFAULT_TEMPERATURE_EXECUTION
        assert local_config["max_tokens"] == 8192
        assert "host" in local_config
        assert local_config["host"].startswith("http://")

    def test_centralized_qdrant_memory_config_local_vs_remote(self):
        """Test local and remote embedding configuration for Qdrant."""
        config_manager = ConfigManager()

        local_config = config_manager.get_qdrant_memory_config("ollama")
        assert local_config["ollama_base_url"].startswith("http://")
        remote_config = config_manager.get_qdrant_memory_config("bedrock")
        assert remote_config["ollama_base_url"] is None
        assert remote_config["aws_region"]


class TestOutputConfig:
    """Test OutputConfig dataclass."""

    def test_default_output_config(self):
        """Test default output configuration."""
        config = OutputConfig()
        assert config.base_dir == get_default_base_dir()
        assert config.target_name is None
        assert not hasattr(config, "enable_unified_output")

    def test_custom_output_config(self):
        """Test custom output configuration."""
        config = OutputConfig(
            base_dir="/tmp/custom_outputs",
            target_name="test_target",
        )
        assert config.base_dir == "/tmp/custom_outputs"
        assert config.target_name == "test_target"

    def test_get_default_base_dir_project_root(self):
        """Test get_default_base_dir when in project root."""
        # Since we're running tests from project root, this should return ./outputs
        base_dir = get_default_base_dir()
        assert base_dir.endswith("outputs")

    def test_get_default_base_dir_detects_project_root(self):
        """Test that get_default_base_dir can detect project root."""
        # The method should find the project root by looking for pyproject.toml
        base_dir = get_default_base_dir()
        project_root = os.path.dirname(base_dir)
        assert os.path.exists(os.path.join(project_root, "pyproject.toml"))

    def test_get_default_base_dir_prefers_environment_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CYBER_AGENT_OUTPUT_DIR", str(tmp_path / "configured-output"))

        assert get_default_base_dir() == str(tmp_path / "configured-output")

    def test_get_default_base_dir_walks_to_parent_project_root(self, monkeypatch, tmp_path):
        project_root = tmp_path / "project"
        child = project_root / "nested" / "working"
        child.mkdir(parents=True)
        (project_root / "pyproject.toml").touch()
        monkeypatch.delenv("CYBER_AGENT_OUTPUT_DIR", raising=False)
        monkeypatch.chdir(child)

        assert get_default_base_dir() == str(project_root / "outputs")


class TestOutputConfigIntegration:
    """Test output configuration integration with ConfigManager."""

    def test_get_output_config_default(self):
        """Test getting default output configuration."""
        config_manager = ConfigManager()
        output_config = config_manager.get_output_config("bedrock")

        assert isinstance(output_config, OutputConfig)
        assert output_config.base_dir == get_default_base_dir()
        assert output_config.target_name is None
        assert not hasattr(output_config, "enable_unified_output")

    def test_get_output_config_with_overrides(self):
        """Test getting output configuration with overrides."""
        config_manager = ConfigManager()
        output_config = config_manager.get_output_config(
            "bedrock",
            output_dir="/tmp/custom",
            target_name="test_target",
        )

        assert output_config.base_dir == "/tmp/custom"
        assert output_config.target_name == "test_target"

    @patch.dict(
        os.environ,
        {
            "CYBER_AGENT_OUTPUT_DIR": "/env/outputs",
            "CYBER_AGENT_ENABLE_UNIFIED_OUTPUT": "false",
        },
    )
    def test_get_output_config_with_env_vars(self):
        """Test getting output configuration with environment variables."""
        config_manager = ConfigManager()
        output_config = config_manager.get_output_config("bedrock")

        assert output_config.base_dir == "/env/outputs"

    def test_output_config_in_server_config(self):
        """Test that output configuration is included in server configuration."""
        config_manager = ConfigManager()
        server_config = config_manager.get_server_config("bedrock")

        assert hasattr(server_config, "output")
        assert isinstance(server_config.output, OutputConfig)
        assert server_config.output.base_dir == get_default_base_dir()

    def test_output_config_precedence(self):
        """Test that overrides take precedence over environment variables."""
        with patch.dict(os.environ, {"CYBER_AGENT_OUTPUT_DIR": "/env/outputs"}):
            config_manager = ConfigManager()
            output_config = config_manager.get_output_config("bedrock", output_dir="/override/outputs")

            # Override should take precedence over environment variable
            assert output_config.base_dir == "/override/outputs"


def test_validation_provider_and_litellm_paths(monkeypatch):
    env = SimpleNamespace(
        get=lambda name, default=None: {
            "OPENAI_API_KEY": "sk",
            "ANTHROPIC_API_KEY": None,
            "AWS_ACCESS_KEY_ID": "id",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "GEMINI_API_KEY": "g",
        }.get(name, default)
    )
    monkeypatch.setattr(validation.requests, "get", Mock(return_value=SimpleNamespace(status_code=200)))
    monkeypatch.setattr(
        validation.ollama,
        "Client",
        lambda host: SimpleNamespace(list=Mock(return_value={"models": [{"model": "llama"}]})),
    )
    validation.validate_provider("bedrock", env, region="us-east-1")
    validation.validate_provider("ollama", env, ollama_host="http://localhost:11434")
    validation.validate_provider("litellm", env)
    validation.validate_provider("gemini", env)
    with pytest.raises(ValueError):
        validation.validate_provider("bad", env)

    validation.validate_litellm_requirements(env, "openai/gpt-4o")

    missing = SimpleNamespace(get=lambda _name, default=None: default)
    with pytest.raises(EnvironmentError):
        validation.validate_litellm_requirements(missing, "openai/gpt-4o")
    with pytest.raises(EnvironmentError):
        validation.validate_gemini_requirements(missing)


def test_validation_aws_and_ollama_requirements(monkeypatch):
    env = SimpleNamespace(
        get=lambda name, default=None: {
            "AWS_ACCESS_KEY_ID": "id",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_SESSION_TOKEN": "token",
        }.get(name, default)
    )
    validation.validate_aws_requirements(env, "us-east-1")

    missing = SimpleNamespace(get=lambda _name, default=None: default)
    with pytest.raises(EnvironmentError):
        validation.validate_aws_requirements(missing, "us-east-1")

    monkeypatch.setattr(validation.requests, "get", Mock(return_value=SimpleNamespace(status_code=200)))
    monkeypatch.setattr(
        validation.ollama,
        "Client",
        lambda host: SimpleNamespace(list=Mock(return_value={"models": [{"model": "llama"}]})),
    )
    validation.validate_ollama_requirements(env, "http://localhost:11434")
    monkeypatch.setattr(validation.requests, "get", Mock(side_effect=RuntimeError("down")))
    with pytest.raises(ConnectionError):
        validation.validate_ollama_requirements(env, "http://localhost:11434")


def test_config_manager_models_and_swarm_fallback_paths(monkeypatch):
    manager = ConfigManager()
    assert manager.is_thinking_model("", "sonnet") is False

    monkeypatch.setenv("BEDROCK_EFFORT", "high")
    standard = manager.get_standard_model_config(
        "claude-sonnet-4-5-20250929", "us-east-1", "bedrock"
    )
    assert standard["additional_request_fields"]["anthropic_beta"] == [
        "context-1m-2025-08-07", "effort-2025-11-24"
    ]
    assert standard["additional_request_fields"]["output_config"]["effort"] == "high"

    thinking = manager.get_thinking_model_config("claude-sonnet-4-5-20250929", "us-east-1")
    assert thinking["max_tokens"] == 16000
    assert thinking["additional_request_fields"]["thinking"]["budget_tokens"] == 7000

    manager.get_provider = lambda: "bedrock"
    manager.get_server_config = lambda *_args, **_kwargs: SimpleNamespace(
        swarm=None, llm=SimpleNamespace(model_id="primary")
    )
    assert manager.get_swarm_model_id() == "primary"
    manager.get_server_config = Mock(side_effect=RuntimeError("unavailable"))
    assert manager.get_swarm_model_id() == "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"id": "not-a-list"}', "not an array"),
        ("not-json", "not valid JSON"),
        ('[{"id": "", "transport": "sse", "server_url": "url"}]', "requires an id"),
        ('[{"id": "a", "transport": "stdio", "command": 1}]', "expected to be a list"),
        ('[{"id": "a", "transport": "sse", "server_url": "url", "headers": []}]', "headers property"),
        ('[{"id": "a", "transport": "sse", "server_url": "url", "plugins": "all"}]', "plugins property"),
        ('[{"id": "a", "transport": "sse", "server_url": "url", "timeoutSeconds": -1}]', "positive integer"),
        ('[{"id": "a", "transport": "sse", "server_url": "url", "allowed_tools": "all"}]', "allowed_tools property"),
    ],
)
def test_mcp_config_rejects_remaining_invalid_connection_shapes(payload, message):
    manager = ConfigManager()
    with pytest.raises(ValueError, match=message):
        manager._get_mcp_config("bedrock", {}, {"mcp_enabled": True, "mcp_conns": payload})


def test_report_refinement_cycles_and_compatibility_helpers_cover_invalid_inputs(monkeypatch):
    assert get_report_refinement_cycles(SimpleNamespace(getenv_int=lambda *_args: True)) == 2
    assert get_report_refinement_cycles(SimpleNamespace(getenv_int=lambda *_args: -3)) == 0
    assert get_report_refinement_cycles(SimpleNamespace(getenv_int=Mock(side_effect=RuntimeError))) == 2

    import modules.config.manager as manager_module

    sentinel = SimpleNamespace(get_ollama_host=lambda: "host")
    monkeypatch.setattr(manager_module, "get_config_manager", lambda: sentinel)
    assert get_ollama_host() == "host"
    assert get_ollama_host(SimpleNamespace(get=lambda *_args: "direct")) == "direct"


def test_environment_overrides_update_all_configured_models(monkeypatch):
    manager = ConfigManager()
    monkeypatch.setenv("CYBER_AGENT_LLM_MODEL", "replacement")
    monkeypatch.setenv("CYBER_AGENT_TEMPERATURE", "0.25")
    monkeypatch.setenv("CYBER_AGENT_TOP_P", "0.75")
    monkeypatch.setenv("MAX_TOKENS", "123")
    monkeypatch.setenv("CYBER_AGENT_EMBEDDING_MODEL", "embedding-replacement")
    monkeypatch.setenv("CYBER_AGENT_EVALUATION_MODEL", "evaluation-replacement")
    monkeypatch.setenv("CYBER_AGENT_SWARM_MODEL", "swarm-replacement")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    defaults = manager._default_configs["bedrock"]
    updated = manager._apply_environment_overrides("bedrock", defaults)
    assert updated["llm"].model_id == "replacement"
    assert updated["llm"].temperature == 0.25
    assert updated["llm"].top_p == 0.75
    assert updated["llm"].max_tokens == 123
    assert updated["embedding"].model_id == "embedding-replacement"
    assert updated["evaluation_llm"].model_id == "evaluation-replacement"
    assert updated["swarm_llm"].model_id == "swarm-replacement"
    assert updated["region"] == "eu-west-1"
    assert updated["memory_llm"].aws_region == "eu-west-1"


def test_server_model_override_handles_ollama_embedding_fallbacks(monkeypatch):
    import modules.config.manager as manager_module

    manager = ConfigManager()
    monkeypatch.setattr(manager.env, "has_changed", lambda: False)
    monkeypatch.setattr(manager, "get_ollama_host", lambda: "http://ollama")
    monkeypatch.setattr(manager_module.ollama, "Client", lambda **_kwargs: SimpleNamespace(
        list=lambda: {"models": [{"model": "llama"}]}
    ))
    config = manager.get_server_config("ollama", model_id="chosen")
    assert config.llm.model_id == "chosen"
    assert config.embedding.model_id == "chosen"

    manager._config_cache.clear()
    monkeypatch.setattr(manager_module.ollama, "Client", Mock(side_effect=RuntimeError("down")))
    config = manager.get_server_config("ollama", model_id="chosen-again")
    assert config.embedding.model_id == "chosen-again"


def test_safe_token_swarm_and_rate_limit_helpers_cover_default_paths(monkeypatch):
    manager = ConfigManager()
    manager.models_client = None
    manager.get_safe_max_tokens.cache_clear()
    assert manager.get_safe_max_tokens("unavailable", buffer=2) == 4096

    manager.models_client = SimpleNamespace(get_model_info=lambda _model: SimpleNamespace(
        limits=SimpleNamespace(output=8000, context=12000),
        capabilities=SimpleNamespace(reasoning=True),
    ))
    manager.get_max_tokens = lambda *_args, **_kwargs: 6000
    manager.get_safe_max_tokens.cache_clear()
    assert manager.get_safe_max_tokens("available", buffer=0.5) == 3000

    defaults = manager._default_configs["bedrock"]
    manager.get_safe_max_tokens = lambda _model: 4321
    monkeypatch.setattr(manager, "getenv_int", lambda key, default=0: 99 if key == "CYBER_AGENT_SWARM_MAX_TOKENS" else default)
    assert manager._get_swarm_llm_config("bedrock", defaults).max_tokens == 99

    manager.get_rate_limit_config.cache_clear()
    manager.get_provider = lambda: "ollama"
    monkeypatch.setattr(manager, "getenv_float", lambda _key, default=0.0: 1.0)
    monkeypatch.setattr(manager, "getenv_int", lambda _key, default=0: 0)
    assert manager.get_rate_limit_config().max_concurrent == 1

    manager.get_rate_limit_config.cache_clear()
    assert manager.get_rate_limit_config("bedrock").max_concurrent == 0
