#!/usr/bin/env python3

import os

# Add src to path for imports
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from modules.agents.cyber_autoagent import (
    AgentRuntimeResources,
    check_existing_memories,
    create_agent,
)
import modules.agents.cyber_autoagent as cyber_agent_module
from modules.config.manager import (
    get_config_manager,
    get_default_model_configs,
    get_ollama_host,
)
from modules.config import AgentConfig


class TestModelConfigs:
    """Test model configuration functions"""

    def test_get_default_model_configs_local(self):
        """Test local model configuration defaults"""
        config = get_default_model_configs("ollama")

        assert config["llm_model"] == "qwen3-coder:30b-a3b-q4_K_M"
        assert config["embedding_model"] == "mxbai-embed-large:latest"
        assert config["embedding_dims"] == 1024

    def test_get_default_model_configs_remote(self):
        """Test remote model configuration defaults"""
        config = get_default_model_configs("bedrock")

        assert "global.anthropic.claude" in config["llm_model"]
        assert config["embedding_model"] == "amazon.titan-embed-text-v2:0"
        assert config["embedding_dims"] == 1024

    def test_get_default_model_configs_invalid(self):
        """Test configuration for invalid server type"""
        # Should now raise an error for invalid server type
        with pytest.raises(ValueError, match="Unsupported provider type"):
            get_default_model_configs("invalid")


class TestOllamaHostDetection:
    """Test Ollama host detection functionality"""

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://custom-host:8080"}, clear=True)
    def test_get_ollama_host_env_override(self):
        """Test OLLAMA_HOST environment variable override"""
        host = get_ollama_host()
        assert host == "http://custom-host:8080"

    @patch.dict(os.environ, {"OLLAMA_HOST": "http://localhost:9999"}, clear=True)
    def test_get_ollama_host_custom_port(self):
        """Test OLLAMA_HOST with custom port"""
        host = get_ollama_host()
        assert host == "http://localhost:9999"

    @patch.dict(os.environ, {}, clear=True)
    @patch("os.path.exists")
    def test_get_ollama_host_native_execution(self, mock_exists):
        """Test host detection for native execution (not in Docker)"""
        mock_exists.return_value = False  # /.dockerenv doesn't exist

        host = get_ollama_host()
        # Should use localhost for native execution
        assert host == "http://localhost:11434"

    @patch.dict(os.environ, {}, clear=True)
    @patch("os.path.exists")
    @patch("requests.get")
    def test_get_ollama_host_docker_localhost_works(self, mock_test, mock_exists):
        """Test Docker environment where localhost works (Linux host networking)"""
        mock_exists.return_value = True  # /app exists
        # Mock localhost works, host.docker.internal doesn't
        mock_response = Mock()
        mock_response.status_code = 200

        def side_effect(url, timeout=None):
            if "localhost" in url:
                return mock_response
            else:
                raise Exception("Connection failed")

        mock_test.side_effect = side_effect

        host = get_ollama_host()
        assert host == "http://localhost:11434"

        # Verify it tested localhost first and found it working
        assert mock_test.call_count >= 1
        mock_test.assert_any_call("http://localhost:11434/api/version", timeout=2)

    @patch.dict(os.environ, {}, clear=True)
    @patch("os.path.exists")
    @patch("requests.get")
    def test_get_ollama_host_docker_host_internal_works(self, mock_test, mock_exists):
        """Test Docker environment where host.docker.internal works (macOS/Windows)"""
        mock_exists.return_value = True  # /app exists
        # Mock localhost fails, host.docker.internal works
        mock_response = Mock()
        mock_response.status_code = 200

        def side_effect(url, timeout=None):
            if "host.docker.internal" in url:
                return mock_response
            else:
                raise requests.exceptions.ConnectionError("Connection failed")

        mock_test.side_effect = side_effect

        host = get_ollama_host()
        assert host == "http://host.docker.internal:11434"

        # Verify it tested both options
        assert mock_test.call_count >= 2
        mock_test.assert_any_call("http://localhost:11434/api/version", timeout=2)
        mock_test.assert_any_call(
            "http://host.docker.internal:11434/api/version", timeout=2
        )

    @patch.dict(os.environ, {}, clear=True)
    @patch("os.path.exists")
    @patch("requests.get")
    def test_get_ollama_host_docker_no_connection(self, mock_test, mock_exists):
        """Test Docker environment where neither option works"""
        mock_exists.return_value = True  # /app exists
        mock_test.side_effect = requests.exceptions.ConnectionError(
            "Connection failed"
        )  # Neither option works

        host = get_ollama_host()
        # Should fallback to host.docker.internal
        assert host == "http://host.docker.internal:11434"


class TestMemoryConfig:
    """Test memory configuration generation"""

    @patch("modules.agents.cyber_autoagent.initialize_memory_system")
    @patch("modules.agents.cyber_autoagent.get_memory_client")
    @patch("modules.config.ConfigManager.validate_requirements")
    @patch("modules.config.models.factory.create_ollama_model")
    @patch("modules.agents.cyber_autoagent.Agent")
    @patch("modules.handlers.react.agent_event_handler.AgentEventHandler")
    @patch("modules.agents.cyber_autoagent.get_system_prompt")
    def test_memory_config_local(
            self,
            mock_system_prompt,
            mock_handler,
            mock_agent_class,
            mock_create_ollama,
            mock_validate_requirements,
            mock_get_memory_client,
            mock_init_memory,
    ):
        """Test local memory configuration is created correctly"""
        # The current implementation builds memory config inline in create_agent
        # We'll test that the right config is passed to initialize_memory_system
        mock_validate_requirements.return_value = Mock()
        mock_create_ollama.return_value = Mock()
        mock_agent_class.return_value = Mock()
        mock_handler.return_value = Mock()
        sys.path.insert(0, "../../src")
        from modules.agents.cyber_autoagent import AgentConfig

        # Call create_agent with local server
        config = AgentConfig(
            target="test.com", objective="test", provider="ollama"
        )
        create_agent(
            target="test.com", objective="test", config=config
        )

        # Check that initialize_memory_system was called
        mock_init_memory.assert_called_once()
        config = mock_init_memory.call_args[0][0]

        # Verify local config structure
        assert config["embedder"]["provider"] == "ollama"
        assert config["llm"]["provider"] == "ollama"
        assert "ollama_base_url" in config["embedder"]["config"]

    @patch("modules.agents.cyber_autoagent.initialize_memory_system")
    @patch("modules.agents.cyber_autoagent.get_memory_client")
    @patch("modules.config.ConfigManager.validate_requirements")
    @patch("modules.config.models.factory.create_bedrock_model")
    @patch("modules.agents.cyber_autoagent.Agent")
    @patch("modules.handlers.react.agent_event_handler.AgentEventHandler")
    @patch("modules.agents.cyber_autoagent.get_system_prompt")
    def test_memory_config_remote(
            self,
            mock_system_prompt,
            mock_handler,
            mock_agent_class,
            mock_create_bedrock_model,
            mock_validate_requirements,
            mock_get_memory_client,
            mock_init_memory,
    ):
        """Test remote memory configuration is created correctly"""
        sys.path.insert(0, "../../src")
        from modules.agents.cyber_autoagent import AgentConfig

        # Call create_agent with remote server
        config = AgentConfig(
            target="test.com", objective="test", provider="bedrock"
        )
        create_agent(
            target="test.com", objective="test", config=config
        )

        # Check that initialize_memory_system was called
        mock_init_memory.assert_called_once()
        config = mock_init_memory.call_args[0][0]

        # Verify remote config structure
        assert config["embedder"]["provider"] == "aws_bedrock"
        assert config["llm"]["provider"] == "aws_bedrock"
        assert "aws_region" in config["embedder"]["config"]


class TestServerValidation:
    """Test server requirements validation"""

    @patch("modules.config.system.validation.requests.get")
    @patch("modules.config.system.validation.ollama.Client")
    def test_validate_server_requirements_local_success(
        self, mock_ollama_client, mock_requests
    ):
        """Test successful local server validation"""
        # Mock Ollama server responding
        mock_requests.return_value.status_code = 200

        # Mock ollama client and list method
        mock_client_instance = mock_ollama_client.return_value
        mock_client_instance.list.return_value = {
            "models": [{"model": "llama3.2:3b"}, {"model": "mxbai-embed-large:latest"}]
        }

        # Should not raise any exception
        get_config_manager().validate_requirements("ollama")

        # Verify client was created (host is now dynamic)
        mock_ollama_client.assert_called_once()

    @patch("modules.config.system.validation.requests.get")
    def test_validate_server_requirements_local_server_down(self, mock_requests):
        """Test local server validation when Ollama is down"""
        # Mock Ollama server not responding
        mock_requests.side_effect = Exception("Connection refused")

        with pytest.raises(ConnectionError, match="Ollama server not accessible"):
            get_config_manager().validate_requirements("ollama")

    @patch("modules.config.system.validation.requests.get")
    @patch("modules.config.system.validation.ollama.Client")
    def test_validate_server_requirements_local_missing_models(
        self, mock_ollama_client, mock_requests
    ):
        """Test local server validation when models are missing"""
        # Mock Ollama server responding
        mock_requests.return_value.status_code = 200

        # Mock ollama client and list method with missing models
        mock_client_instance = mock_ollama_client.return_value
        mock_client_instance.list.return_value = {
            "models": [{"model": "some-other-model:latest"}]
        }

        with pytest.raises(ValueError, match="Required models not found"):
            get_config_manager().validate_requirements("ollama")

    @patch.dict(os.environ, {}, clear=True)
    def test_validate_server_requirements_remote_no_credentials(self):
        """Test remote server validation without AWS credentials"""
        with pytest.raises(EnvironmentError, match="AWS credentials not configured"):
            get_config_manager().validate_requirements("bedrock")

    @patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "test_key",
            "AWS_SECRET_ACCESS_KEY": "test_secret",
            "AWS_DEFAULT_REGION": "us-east-1",
        },
        clear=True,
    )
    @patch("boto3.client")
    def test_validate_server_requirements_remote_success(self, mock_boto_client):
        """Test successful remote server validation"""
        # Mock both Bedrock and Bedrock Runtime clients
        mock_bedrock_client = Mock()
        mock_bedrock_runtime_client = Mock()

        def client_side_effect(service_name, **_kwargs):
            if service_name == "bedrock":
                return mock_bedrock_client
            elif service_name == "bedrock-runtime":
                return mock_bedrock_runtime_client
            return Mock()

        mock_boto_client.side_effect = client_side_effect

        # Mock successful foundation models list
        mock_bedrock_client.list_foundation_models.return_value = {
            "modelSummaries": [
                {"modelId": "us.anthropic.claude-sonnet-4-20250514-v1:0"},
                {"modelId": "amazon.titan-embed-text-v2:0"},
            ]
        }

        # Mock successful model invocation
        mock_bedrock_runtime_client.invoke_model.return_value = {"body": Mock()}

        # Should not raise any exception
        get_config_manager().validate_requirements("bedrock")


class TestCreateAgent:
    """Test agent creation functionality"""

    @pytest.mark.ollama
    @patch("modules.config.ConfigManager.validate_requirements")
    @patch("modules.config.models.factory.create_bedrock_model")
    @patch("modules.agents.cyber_autoagent.Agent")
    @patch("modules.handlers.react.agent_event_handler.AgentEventHandler")
    @patch("modules.agents.cyber_autoagent.get_system_prompt")
    @patch("modules.agents.cyber_autoagent.initialize_memory_system")
    def test_create_agent_remote_success(
        self,
        mock_init_memory,
        mock_get_prompt,
        mock_react_bridge_handler,
        mock_agent_class,
        mock_create_remote,
        mock_validate,
    ):
        """Test successful remote agent creation"""
        # Setup mocks
        mock_model = Mock()
        mock_create_remote.return_value = mock_model
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_handler = Mock()
        mock_react_bridge_handler.return_value = mock_handler
        mock_get_prompt.return_value = "test prompt"

        # Call function
        from modules.agents.cyber_autoagent import AgentConfig

        config = AgentConfig(
            target="test.com", objective="test objective", provider="bedrock"
        )
        agent = create_agent(
            target="test.com", objective="test objective", config=config
        )

        # Verify calls
        mock_validate.assert_called_once_with("bedrock")
        mock_create_remote.assert_called_once()
        mock_agent_class.assert_called_once()

        assert agent == mock_agent

    @patch("modules.config.ConfigManager.validate_requirements")
    @patch("modules.config.models.factory.create_ollama_model")
    @patch("modules.agents.cyber_autoagent.Agent")
    @patch("modules.handlers.react.agent_event_handler.AgentEventHandler")
    @patch("modules.agents.cyber_autoagent.get_system_prompt")
    @patch("modules.agents.cyber_autoagent.initialize_memory_system")
    @patch("modules.agents.cyber_autoagent.get_memory_client")
    def test_create_agent_local_success(
        self,
            mock_get_memory_client,
        mock_init_memory,
        mock_get_prompt,
        mock_react_bridge_handler,
        mock_agent_class,
        mock_create_ollama,
        mock_validate,
    ):
        """Test successful local agent creation"""
        # Setup mocks
        mock_model = Mock()
        mock_create_ollama.return_value = mock_model
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_handler = Mock()
        mock_react_bridge_handler.return_value = mock_handler
        mock_get_prompt.return_value = "test prompt"

        # Call function
        from modules.agents.cyber_autoagent import AgentConfig

        config = AgentConfig(
            target="test.com", objective="test objective", provider="ollama"
        )
        agent = create_agent(
            target="test.com", objective="test objective", config=config
        )

        # Verify calls
        mock_validate.assert_called_once_with("ollama")
        mock_create_ollama.assert_called_once()
        mock_agent_class.assert_called_once()

        assert agent == mock_agent

    @patch("modules.config.ConfigManager.validate_requirements")
    def test_create_agent_validation_failure(self, mock_validate):
        """Test agent creation when validation fails"""
        mock_validate.side_effect = ConnectionError("Test error")

        with pytest.raises(ConnectionError):
            from modules.agents.cyber_autoagent import AgentConfig

            config = AgentConfig(
                target="test.com", objective="test objective", provider="ollama"
            )
            create_agent(target="test.com", objective="test objective", config=config)

    @patch("modules.config.ConfigManager.validate_requirements")
    @patch("modules.config.models.factory.create_ollama_model")
    @patch("modules.config.models.factory._handle_model_creation_error")
    @patch("modules.agents.cyber_autoagent.initialize_memory_system")
    @patch("modules.agents.cyber_autoagent.get_memory_client")
    def test_create_agent_model_creation_failure(
        self,
            mock_get_memory_client,
        mock_init_memory,
        mock_handle_error,
        mock_create_ollama,
        mock_validate,
    ):
        """Test agent creation when model creation fails"""
        mock_create_ollama.side_effect = Exception("Model creation failed")

        with pytest.raises(Exception):
            from modules.agents.cyber_autoagent import AgentConfig

            config = AgentConfig(
                target="test.com", objective="test objective", provider="ollama"
            )
            create_agent(target="test.com", objective="test objective", config=config)

        mock_handle_error.assert_called_once()


class TestCheckExistingMemories:
    """Test the check_existing_memories function"""

    @patch("modules.agents.cyber_autoagent.os.environ.get")
    def test_check_existing_memories_mem0_platform(self, mock_env_get):
        """Test check_existing_memories with Mem0 Platform"""
        mock_env_get.side_effect = lambda key, default=None: (
            "test-key" if key == "MEM0_API_KEY" else default
        )

        result = check_existing_memories("test.com", "bedrock")
        assert result is True

    @patch("modules.agents.cyber_autoagent.os.environ.get")
    def test_check_existing_memories_opensearch(self, mock_env_get):
        """Test check_existing_memories with OpenSearch"""
        mock_env_get.side_effect = lambda key, default=None: (
            "test-host" if key == "OPENSEARCH_HOST" else default
        )

        result = check_existing_memories("test.com", "bedrock")
        assert result is True

    @patch("modules.agents.cyber_autoagent.os.environ.get")
    @patch("modules.agents.cyber_autoagent.os.path.exists")
    @patch("modules.agents.cyber_autoagent.os.path.getsize")
    def test_check_existing_memories_faiss_exists(
        self, mock_getsize, mock_exists, mock_env_get
    ):
        """Test check_existing_memories with FAISS backend - directory exists with content"""
        mock_env_get.return_value = None  # No Mem0 or OpenSearch
        mock_exists.side_effect = lambda path: True  # All paths exist
        mock_getsize.return_value = 100  # Non-zero file size

        result = check_existing_memories("test.com", "ollama")
        assert result is True

    @patch("modules.agents.cyber_autoagent.os.environ.get")
    @patch("modules.agents.cyber_autoagent.os.path.exists")
    def test_check_existing_memories_faiss_not_exists(self, mock_exists, mock_env_get):
        """Test check_existing_memories with FAISS backend - directory doesn't exist"""
        mock_env_get.return_value = None  # No Mem0 or OpenSearch
        mock_exists.return_value = False

        result = check_existing_memories("test.com", "ollama")
        assert result is False

    @patch("modules.agents.cyber_autoagent.os.environ.get")
    @patch("modules.agents.cyber_autoagent.os.path.exists")
    @patch("modules.agents.cyber_autoagent.os.path.getsize")
    def test_check_existing_memories_faiss_empty(
        self, mock_getsize, mock_exists, mock_env_get
    ):
        """Test check_existing_memories with FAISS backend - directory exists but empty"""
        mock_env_get.return_value = None  # No Mem0 or OpenSearch

        # Directory exists but no FAISS files
        def exists_side_effect(path):
            if "outputs/test_com/memory" in path and not path.endswith(
                (".faiss", ".pkl")
            ):
                return True
            return False

        mock_exists.side_effect = exists_side_effect
        mock_getsize.return_value = 0

        result = check_existing_memories("test.com", "ollama")
        assert result is False

    @patch("modules.agents.cyber_autoagent.os.environ.get")
    @patch("modules.agents.cyber_autoagent.os.path.exists")
    @patch("modules.agents.cyber_autoagent.os.path.getsize")
    def test_check_existing_memories_faiss_zero_size_files(
        self, mock_getsize, mock_exists, mock_env_get
    ):
        """Test check_existing_memories with FAISS backend - files exist but are zero size"""
        mock_env_get.return_value = None  # No Mem0 or OpenSearch
        mock_exists.side_effect = lambda path: True  # All paths exist
        mock_getsize.return_value = 0  # Zero file size

        result = check_existing_memories("test.com", "ollama")
        assert result is False  # Should return False for zero-size files

    @patch("modules.agents.cyber_autoagent.os.environ.get")
    @patch("modules.agents.cyber_autoagent.os.path.exists")
    def test_check_existing_memories_sanitizes_target(self, mock_exists, mock_env_get):
        """Test check_existing_memories properly sanitizes target names"""
        mock_env_get.return_value = None  # No Mem0 or OpenSearch
        mock_exists.side_effect = lambda path: path.endswith("pyproject.toml")

        result = check_existing_memories("https://test.com/path", "ollama")
        assert result is False
        mock_exists.assert_called_with(str((Path.cwd() / "outputs" / "test.com" / "memory").resolve()))

    @patch("modules.config.manager.os.environ.get")
    @patch("modules.config.manager.os.path.exists")
    def test_check_existing_memories_exception_handling(
        self, mock_exists, mock_env_get
    ):
        """Test check_existing_memories handles exceptions gracefully"""
        mock_env_get.return_value = None  # No Mem0 or OpenSearch
        mock_exists.side_effect = Exception("File system error")

        with patch("modules.config.manager.logger") as mock_logger:
            result = check_existing_memories("test.com", "ollama")
            assert result is False
            mock_logger.debug.assert_called_once()


def test_create_agent_reuses_runtime_resources(monkeypatch):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.tool_registry = Mock()

    config = AgentConfig(target="example.com", objective="test", provider="ollama", model_id="llama")
    callback_handler = Mock()
    conversation_manager = object()
    runtime = AgentRuntimeResources(
        config=config,
        operation_id="OP_TEST",
        server_config=SimpleNamespace(),
        config_manager=SimpleNamespace(),
        callback_handler=callback_handler,
        tools_list=["tool"],
        tool_executor=object(),
        system_prompt_payload="system payload",
        system_prompt="system text",
        hooks=[],
        conversation_manager=conversation_manager,
        sdk_context_manager=None,
        trace_attributes={"operation.id": "OP_TEST"},
        prompt_token_limit=123,
    )

    monkeypatch.setattr(cyber_agent_module, "create_agent_runtime_resources", Mock(side_effect=AssertionError))
    monkeypatch.setattr(cyber_agent_module, "create_strands_model", Mock(return_value=SimpleNamespace(stateful=False)))
    monkeypatch.setattr(cyber_agent_module, "create_agent_with_stateful_retry", Mock(return_value=FakeAgent()))
    monkeypatch.setattr(cyber_agent_module, "get_capabilities", Mock(return_value=SimpleNamespace(supports_reasoning=True)))
    monkeypatch.setattr(cyber_agent_module, "tool_catalog_wrapper", Mock(return_value="catalog"))

    agent = create_agent("example.com", "test", runtime_resources=runtime)

    kwargs = cyber_agent_module.create_agent_with_stateful_retry.call_args.args[0]
    assert kwargs["conversation_manager"] is conversation_manager
    assert kwargs["callback_handler"] is callback_handler
    assert kwargs["trace_attributes"] == {"operation.id": "OP_TEST"}
    assert agent._prompt_token_limit == 123
    assert agent.system_prompt == "system text"
    assert runtime.callback_handler is callback_handler


def test_create_agent_stateful_model_uses_runtime_handler_without_conversation_manager(monkeypatch):
    class FakeAgent:
        def __init__(self):
            self.tool_registry = Mock()

    config = AgentConfig(target="example.com", objective="test", provider="litellm", model_id="stateful")
    callback_handler = Mock()
    runtime = AgentRuntimeResources(
        config=config,
        operation_id="OP_STATEFUL",
        server_config=SimpleNamespace(),
        config_manager=SimpleNamespace(),
        callback_handler=callback_handler,
        tools_list=[],
        tool_executor=object(),
        system_prompt_payload="system payload",
        system_prompt="system text",
        hooks=[],
        conversation_manager=object(),
        sdk_context_manager="auto",
        trace_attributes={"operation.id": "OP_STATEFUL"},
        prompt_token_limit=0,
    )

    monkeypatch.setattr(cyber_agent_module, "create_strands_model", Mock(return_value=SimpleNamespace(stateful=True)))
    monkeypatch.setattr(cyber_agent_module, "create_agent_with_stateful_retry", Mock(return_value=FakeAgent()))
    monkeypatch.setattr(cyber_agent_module, "get_capabilities", Mock(side_effect=RuntimeError("caps unavailable")))
    monkeypatch.setattr(cyber_agent_module, "tool_catalog_wrapper", Mock(return_value="catalog"))

    agent = create_agent("example.com", "test", runtime_resources=runtime)

    kwargs = cyber_agent_module.create_agent_with_stateful_retry.call_args.args[0]
    assert "conversation_manager" not in kwargs
    assert "context_manager" not in kwargs
    assert kwargs["callback_handler"] is callback_handler
    assert agent._allow_reasoning_content is False


def test_create_agent_runtime_resources_applies_sdk_context_manager(monkeypatch):
    class FakeAgent:
        def __init__(self):
            self.tool_registry = Mock()

        def __setattr__(self, name, value):
            if name == "system_prompt":
                raise RuntimeError("read only")
            super().__setattr__(name, value)

    config = AgentConfig(target="example.com", objective="test", provider="ollama", model_id="llama")
    runtime = AgentRuntimeResources(
        config=config,
        operation_id="OP_CONTEXT",
        server_config=SimpleNamespace(),
        config_manager=SimpleNamespace(),
        callback_handler=Mock(),
        tools_list=[],
        tool_executor=object(),
        system_prompt_payload="system payload",
        system_prompt="system text",
        hooks=[],
        conversation_manager=object(),
        sdk_context_manager="auto",
        trace_attributes={"operation.id": "OP_CONTEXT"},
        prompt_token_limit=0,
    )

    monkeypatch.setattr(cyber_agent_module, "create_strands_model", Mock(return_value=SimpleNamespace(stateful=False)))
    monkeypatch.setattr(cyber_agent_module, "create_agent_with_stateful_retry", Mock(return_value=FakeAgent()))
    monkeypatch.setattr(cyber_agent_module, "get_capabilities", Mock(return_value=SimpleNamespace(supports_reasoning=False)))
    monkeypatch.setattr(cyber_agent_module, "tool_catalog_wrapper", Mock(return_value="catalog"))

    agent = create_agent("example.com", "test", runtime_resources=runtime)

    kwargs = cyber_agent_module.create_agent_with_stateful_retry.call_args.args[0]
    assert kwargs["conversation_manager"] is runtime.conversation_manager
    assert kwargs["context_manager"] == "auto"
    assert agent._allow_reasoning_content is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
