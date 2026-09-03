#!/usr/bin/env python3

import os

# Add src to path for imports
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import modules.agents.cyber_autoagent as cyber_agent_module
from modules.agents.cyber_autoagent import (
    AgentRuntimeResources,
    create_agent,
)
from modules.agents.fail_fast_tool_executor import FailFastSequentialToolExecutor
from modules.config import AgentConfig
from modules.config.manager import (
    get_config_manager,
    get_default_model_configs,
    get_ollama_host,
)


class TestModelConfigs:
    """Test model configuration functions"""

    def test_get_default_model_configs_local(self):
        """Test local model configuration defaults"""
        config = get_default_model_configs("ollama")

        assert config["llm_model"] == "qwen3.6:27b"
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

        assert config["embedding_provider"] == "ollama"
        assert config["ollama_base_url"]
        assert config["target_values"] == ["test.com"]
        assert config["memory_mode"] == "operation"

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

        assert config["embedding_provider"] == "bedrock"
        assert config["aws_region"] == "us-east-1"
        assert config["target_values"] == ["test.com"]


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


def test_create_tool_repeat_guard_uses_threshold_and_max_cycle_configuration():
    values = {
        "CYBER_TOOL_REPEAT_THRESHOLD": 4,
        "CYBER_TOOL_REPEAT_MAX_CYCLE_LENGTH": 6,
    }
    config_manager = SimpleNamespace(getenv_int=lambda name, default: values.get(name, default))
    agent_logger = Mock()

    guard = cyber_agent_module._create_tool_repeat_guard(config_manager, agent_logger)

    assert guard.repeat_threshold == 4
    assert guard.max_cycle_length == 6
    assert guard.history_limit == 24
    agent_logger.info.assert_called_once_with(
        "Repeated tool-call guard threshold: %d; maximum cycle length: %d",
        4,
        6,
    )


def test_create_tool_repeat_guard_disable_switch_skips_cycle_configuration():
    calls = []

    def getenv_int(name, default):
        calls.append((name, default))
        return 0

    agent_logger = Mock()

    guard = cyber_agent_module._create_tool_repeat_guard(
        SimpleNamespace(getenv_int=getenv_int),
        agent_logger,
    )

    assert guard is None
    assert [name for name, _default in calls] == ["CYBER_TOOL_REPEAT_THRESHOLD"]
    agent_logger.info.assert_called_once_with("Repeated tool-call guard disabled")


def test_create_tool_repeat_guard_normalizes_invalid_values():
    values = {
        "CYBER_TOOL_REPEAT_THRESHOLD": 1,
        "CYBER_TOOL_REPEAT_MAX_CYCLE_LENGTH": 0,
    }
    config_manager = SimpleNamespace(getenv_int=lambda name, default: values.get(name, default))
    guard = cyber_agent_module._create_tool_repeat_guard(config_manager, Mock())
    assert guard.repeat_threshold == cyber_agent_module.DEFAULT_TOOL_REPEAT_THRESHOLD
    assert guard.max_cycle_length == cyber_agent_module.DEFAULT_TOOL_REPEAT_MAX_CYCLE_LENGTH


def test_create_tool_repeat_guard_rejects_boolean_and_string_values():
    values = {
        "CYBER_TOOL_REPEAT_THRESHOLD": True,
        "CYBER_TOOL_REPEAT_MAX_CYCLE_LENGTH": "invalid",
    }
    manager = SimpleNamespace(getenv_int=lambda name, default: values.get(name, default))
    guard = cyber_agent_module._create_tool_repeat_guard(manager, Mock())
    assert guard.repeat_threshold == cyber_agent_module.DEFAULT_TOOL_REPEAT_THRESHOLD
    assert guard.max_cycle_length == cyber_agent_module.DEFAULT_TOOL_REPEAT_MAX_CYCLE_LENGTH


def test_tool_name_and_role_selection_helpers_filter_optional_and_task_creation_tools():
    def core_tool():
        return None

    def create_tasks():
        return None

    def optional_tool():
        return None

    runtime = SimpleNamespace(
        core_tools_list=[core_tool, create_tasks],
        tools_list=[],
        optional_tools_list=[optional_tool],
    )

    assert cyber_agent_module._tool_names([core_tool, optional_tool]) == {"core_tool", "optional_tool"}
    assert cyber_agent_module.build_role_tools(runtime) == [core_tool]
    assert cyber_agent_module.build_role_tools(
        runtime,
        selected_optional_tool_names=["optional_tool"],
        include_create_tasks=True,
    ) == [core_tool, create_tasks, optional_tool]


def test_build_role_tools_falls_back_to_tools_list_and_ignores_unselected_optional():
    def fallback_tool():
        return None

    def optional_tool():
        return None

    runtime = SimpleNamespace(
        core_tools_list=[],
        tools_list=[fallback_tool],
        optional_tools_list=[optional_tool],
    )

    assert cyber_agent_module.build_role_tools(runtime) == [fallback_tool]
    assert cyber_agent_module.build_role_tools(runtime, selected_optional_tool_names=["missing"]) == [fallback_tool]


def test_build_role_tools_excludes_create_tasks_by_default_and_accepts_tuple_names():
    def create_tasks():
        return None

    def other_tool():
        return None

    runtime = SimpleNamespace(
        core_tools_list=[create_tasks, other_tool],
        tools_list=[],
        optional_tools_list=[create_tasks],
    )
    assert cyber_agent_module.build_role_tools(runtime) == [other_tool]
    assert cyber_agent_module.build_role_tools(
        runtime, selected_optional_tool_names=["create_tasks"], include_create_tasks=True
    ) == [create_tasks, other_tool, create_tasks]


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
    assert kwargs["trace_attributes"]["operation.id"] == "OP_TEST"
    assert kwargs["trace_attributes"]["cyber.agent.run_id"] == str(callback_handler.agent_run_id)
    assert agent._prompt_token_limit == 123
    assert agent.system_prompt == "system text"
    assert runtime.callback_handler is callback_handler


def test_create_agent_strips_executor_protocols_for_prompt_builder(monkeypatch):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.tool_registry = Mock()

    config = AgentConfig(target="example.com", objective="test", provider="ollama", model_id="llama")
    runtime = AgentRuntimeResources(
        config=config,
        operation_id="OP_TEST",
        server_config=SimpleNamespace(),
        config_manager=SimpleNamespace(),
        callback_handler=Mock(),
        tools_list=[],
        tool_executor=object(),
        system_prompt_payload="base payload",
        system_prompt="base text",
        hooks=[],
        conversation_manager=None,
        sdk_context_manager=None,
        trace_attributes={},
        prompt_token_limit=123,
    )
    system_prompt = "<tools_and_capabilities>call tool_catalog</tools_and_capabilities>"

    monkeypatch.setattr(cyber_agent_module, "create_strands_model", Mock(return_value=SimpleNamespace(stateful=False)))
    monkeypatch.setattr(cyber_agent_module, "create_agent_with_stateful_retry", Mock(return_value=FakeAgent()))
    monkeypatch.setattr(cyber_agent_module, "get_capabilities", Mock(return_value=SimpleNamespace(supports_reasoning=True)))
    monkeypatch.setattr(cyber_agent_module, "tool_catalog_wrapper", Mock(return_value="catalog"))

    create_agent(
        "example.com",
        "test",
        runtime_resources=runtime,
        system_prompt=system_prompt,
        tools=[],
        agent_type="task_prompt_builder",
    )

    kwargs = cyber_agent_module.create_agent_with_stateful_retry.call_args.args[0]
    assert "tool_catalog" not in kwargs["system_prompt"]
    assert "controller support role" in kwargs["system_prompt"]


@pytest.mark.parametrize(
    ("agent_type", "uses_fail_fast_executor"),
    [
        ("task_evaluator", True),
        ("plan_critic", True),
        ("task_prompt_critic", True),
        ("task_executor", False),
    ],
)
def test_create_agent_assigns_review_roles_a_fail_fast_executor(
    monkeypatch,
    agent_type,
    uses_fail_fast_executor,
):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.tool_registry = Mock()

    config = AgentConfig(target="example.com", objective="test", provider="ollama", model_id="llama")
    runtime = AgentRuntimeResources(
        config=config,
        operation_id="OP_EVALUATOR",
        server_config=SimpleNamespace(),
        config_manager=SimpleNamespace(),
        callback_handler=Mock(),
        tools_list=[],
        tool_executor=object(),
        system_prompt_payload="system payload",
        system_prompt="system text",
        hooks=[],
        conversation_manager=None,
        sdk_context_manager=None,
        trace_attributes={},
        prompt_token_limit=0,
    )
    monkeypatch.setattr(cyber_agent_module, "create_strands_model", Mock(return_value=SimpleNamespace(stateful=False)))
    monkeypatch.setattr(cyber_agent_module, "create_agent_with_stateful_retry", Mock(return_value=FakeAgent()))
    monkeypatch.setattr(cyber_agent_module, "get_capabilities", Mock(return_value=SimpleNamespace(supports_reasoning=False)))
    monkeypatch.setattr(cyber_agent_module, "tool_catalog_wrapper", Mock(return_value="catalog"))

    agent = create_agent(
        "example.com",
        "test",
        runtime_resources=runtime,
        tools=[],
        agent_type=agent_type,
        include_tool_catalog=False,
    )

    kwargs = cyber_agent_module.create_agent_with_stateful_retry.call_args.args[0]
    if agent_type == "task_evaluator":
        assert agent._cyber_evaluator_artifact_read_limit_hook in kwargs["hooks"]
        assert agent._cyber_evaluator_artifact_read_limit_hook.exhausted is False
    if uses_fail_fast_executor:
        assert isinstance(kwargs["tool_executor"], FailFastSequentialToolExecutor)
    else:
        assert kwargs["tool_executor"] is runtime.tool_executor


def test_create_agent_uses_role_specific_event_handler(monkeypatch):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.tool_registry = Mock()

    config = AgentConfig(target="example.com", objective="test", provider="ollama", model_id="llama")
    events = []
    root_handler = cyber_agent_module.AgentEventHandler(
        operation_id="OP_ROLE",
        provider_id="ollama",
        model_id="llama",
        emitter=SimpleNamespace(emit=lambda event: events.append(event)),
        agent_name="Cyber-AutoAgent OP_ROLE",
        agent_type="operation_controller",
        init_context={"budget": {"maxDurationMinutes": 60}},
        start_metrics_thread=False,
    )
    runtime = AgentRuntimeResources(
        config=config,
        operation_id="OP_ROLE",
        server_config=SimpleNamespace(swarm=SimpleNamespace(llm=SimpleNamespace(model_id="swarm-model"))),
        config_manager=SimpleNamespace(
            getenv=lambda _name, default=None: default,
            getenv_int=lambda _name, default=None: default,
        ),
        callback_handler=root_handler,
        tools_list=[],
        tool_executor=object(),
        system_prompt_payload="system payload",
        system_prompt="system text",
        hooks=[],
        conversation_manager=object(),
        sdk_context_manager=None,
        trace_attributes={"operation.id": "OP_ROLE"},
        prompt_token_limit=0,
    )

    monkeypatch.setattr(cyber_agent_module, "create_strands_model", Mock(return_value=SimpleNamespace(stateful=False)))
    monkeypatch.setattr(cyber_agent_module, "create_agent_with_stateful_retry", Mock(return_value=FakeAgent()))
    monkeypatch.setattr(cyber_agent_module, "get_capabilities", Mock(return_value=SimpleNamespace(supports_reasoning=False)))
    monkeypatch.setattr(cyber_agent_module, "tool_catalog_wrapper", Mock(return_value="catalog"))

    agent = create_agent(
        "example.com",
        "test",
        runtime_resources=runtime,
        name="Cyber-AutoAgent task_executor",
        agent_type="task_executor",
    )

    kwargs = cyber_agent_module.create_agent_with_stateful_retry.call_args.args[0]
    callback_handler = kwargs["callback_handler"]
    assert callback_handler is not root_handler
    assert callback_handler.agent_type == "task_executor"
    assert callback_handler.agent_name == "Cyber-AutoAgent task_executor"
    assert callback_handler.parent_agent_run_id == root_handler.agent_run_id
    assert callback_handler.coordinator is root_handler.coordinator
    assert kwargs["trace_attributes"]["agent.role"] == "task_executor"
    assert kwargs["trace_attributes"]["langfuse.agent.type"] == "task_executor"
    assert agent._cyber_agent_type == "task_executor"
    assert agent._cyber_agent_name == "Cyber-AutoAgent task_executor"
    assert agent._cyber_callback_handler is callback_handler


def test_create_agent_can_disable_tool_catalog_for_restricted_role(monkeypatch):
    class FakeAgent:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.tool_registry = Mock()

    config = AgentConfig(target="example.com", objective="test", provider="ollama", model_id="llama")
    root_handler = cyber_agent_module.AgentEventHandler(
        operation_id="OP_TASK_CREATOR",
        provider_id="ollama",
        model_id="llama",
        emitter=SimpleNamespace(emit=lambda _event: None),
        agent_name="Cyber-AutoAgent OP_TASK_CREATOR",
        agent_type="operation_controller",
        start_metrics_thread=False,
    )
    runtime = AgentRuntimeResources(
        config=config,
        operation_id="OP_TASK_CREATOR",
        server_config=SimpleNamespace(swarm=SimpleNamespace(llm=SimpleNamespace(model_id="swarm-model"))),
        config_manager=SimpleNamespace(
            getenv=lambda _name, default=None: default,
            getenv_int=lambda _name, default=None: default,
        ),
        callback_handler=root_handler,
        tools_list=[],
        tool_executor=object(),
        system_prompt_payload="system payload",
        system_prompt="system text",
        hooks=[],
        conversation_manager=object(),
        sdk_context_manager=None,
        trace_attributes={"operation.id": "OP_TASK_CREATOR"},
        prompt_token_limit=0,
    )
    create_tasks_tool = Mock(__name__="create_tasks")

    monkeypatch.setattr(cyber_agent_module, "create_strands_model", Mock(return_value=SimpleNamespace(stateful=False)))
    monkeypatch.setattr(cyber_agent_module, "create_agent_with_stateful_retry", Mock(return_value=FakeAgent()))
    monkeypatch.setattr(cyber_agent_module, "get_capabilities", Mock(return_value=SimpleNamespace(supports_reasoning=False)))
    tool_catalog_wrapper = Mock(return_value="catalog")
    monkeypatch.setattr(cyber_agent_module, "tool_catalog_wrapper", tool_catalog_wrapper)

    agent = create_agent(
        "example.com",
        "test",
        runtime_resources=runtime,
        tools=[create_tasks_tool],
        name="Cyber-AutoAgent task_creator",
        agent_type="task_creator",
        include_tool_catalog=False,
    )

    kwargs = cyber_agent_module.create_agent_with_stateful_retry.call_args.args[0]
    assert kwargs["tools"] == [create_tasks_tool]
    assert kwargs["load_tools_from_directory"] is False
    agent.tool_registry.register_tool.assert_not_called()
    tool_catalog_wrapper.assert_not_called()


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


def test_create_agent_disables_reasoning_replay_for_litellm_chat_completions(monkeypatch):
    class FakeAgent:
        def __init__(self):
            self.tool_registry = Mock()

    config = AgentConfig(target="example.com", objective="test", provider="litellm", model_id="openai/gpt-5")
    runtime = AgentRuntimeResources(
        config=config,
        operation_id="OP_LITELLM_REASONING",
        server_config=SimpleNamespace(),
        config_manager=SimpleNamespace(),
        callback_handler=Mock(),
        tools_list=[],
        tool_executor=object(),
        system_prompt_payload="system payload",
        system_prompt="system text",
        hooks=[],
        conversation_manager=object(),
        sdk_context_manager=None,
        trace_attributes={"operation.id": "OP_LITELLM_REASONING"},
        prompt_token_limit=0,
    )

    monkeypatch.setattr(cyber_agent_module, "create_strands_model", Mock(return_value=SimpleNamespace(stateful=False)))
    monkeypatch.setattr(cyber_agent_module, "create_agent_with_stateful_retry", Mock(return_value=FakeAgent()))
    monkeypatch.setattr(
        cyber_agent_module,
        "get_capabilities",
        Mock(return_value=SimpleNamespace(supports_reasoning=True)),
    )
    monkeypatch.setattr(cyber_agent_module, "tool_catalog_wrapper", Mock(return_value="catalog"))

    agent = create_agent("example.com", "test", runtime_resources=runtime)

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


def test_create_agent_runtime_resources_builds_integrated_runtime_with_module_fallback(monkeypatch, tmp_path):
    """Exercise the runtime initializer without external browser, memory, or telemetry services."""

    class FakeConfigManager:
        def validate_requirements(self, _provider):
            return None

        def get_server_config(self, _provider, **_overrides):
            return SimpleNamespace(
                llm=SimpleNamespace(model_id="default-model", max_tokens=256, temperature=0.1),
                swarm=SimpleNamespace(llm=SimpleNamespace(model_id="swarm-model")),
                output=SimpleNamespace(base_dir=str(tmp_path)),
                sdk=SimpleNamespace(conversation_window_size=12),
            )

        def get_default_region(self):
            return "us-test-1"

        def get_qdrant_memory_config(self, _provider):
            return {}

        def ensure_operation_output_dirs(self, *_args, **_kwargs):
            return {"root": str(tmp_path), "artifacts": str(tmp_path / "artifacts"), "tools": str(tmp_path / "tools")}

        def getenv(self, name, default=None):
            return {"CYBER_SDK_CONTEXT_MANAGER": "", "CYBER_UI_MODE": "cli"}.get(name, default)

        def getenv_bool(self, _name, default=False):
            return default

        def getenv_int(self, _name, default):
            return default

    class FakeLoader:
        last_loaded_execution_prompt_source = None
        last_loaded_termination_policy_source = None

        def discover_module_tools(self, _module):
            return [str(tmp_path / "unloaded_tool.py")], ["web_search"]

        def load_module_execution_prompt(self, _module, operation_root=None):
            return "module guidance"

        def load_module_termination_policy(self, _module):
            return "finish when evidence is complete"

    class FakeCallback:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.emitter = object()
            self.coordinator = object()
            self.agent_run_id = "run-1"

        def emit_ui_event(self, _event):
            return None

    class FakeHooks:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    config = AgentConfig(target="example.test", objective="assess", provider="ollama", op_id="OP_INTEGRATED")
    config.module = "web"
    config.available_tools = ["web_search"]
    memory_client = SimpleNamespace(
        get_memory_overview=lambda: {"has_memories": False},
        get_active_plan=lambda **_kwargs: None,
    )

    monkeypatch.setattr(cyber_agent_module, "configure_sdk_logging", lambda **_kwargs: None)
    monkeypatch.setattr(cyber_agent_module, "get_config_manager", lambda: FakeConfigManager())
    monkeypatch.setattr(cyber_agent_module, "resolve_operation_targets", lambda *_args: [SimpleNamespace(value="https://example.test")])
    monkeypatch.setattr(cyber_agent_module, "sanitize_target_name", lambda _target: "example_test")
    monkeypatch.setattr(cyber_agent_module, "require_prompt_token_limit", lambda *_args: 32_000)
    monkeypatch.setattr(cyber_agent_module, "resolve_tool_result_max_chars", lambda *_args: 6000)
    monkeypatch.setattr(cyber_agent_module, "initialize_browser", lambda **_kwargs: None)
    monkeypatch.setattr(cyber_agent_module, "initialize_memory_system", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cyber_agent_module, "get_memory_client", lambda **_kwargs: memory_client)
    monkeypatch.setattr(cyber_agent_module.prompts, "get_module_loader", lambda: FakeLoader())
    monkeypatch.setattr(cyber_agent_module.prompts, "get_system_prompt", lambda **_kwargs: "base prompt")
    monkeypatch.setattr(cyber_agent_module, "discover_mcp_tools", lambda _config: [])
    monkeypatch.setattr(cyber_agent_module, "resolve_seclists_root", lambda: str(tmp_path / "seclists"))
    monkeypatch.setattr(cyber_agent_module, "tool_append_description", lambda *_args: None)
    monkeypatch.setattr(cyber_agent_module, "create_absolute_path_editor", lambda editor: editor)
    monkeypatch.setattr(cyber_agent_module, "create_artifact_reader", lambda *_args, **_kwargs: "artifact_reader")
    monkeypatch.setattr(cyber_agent_module, "get_capabilities", lambda *_args: SimpleNamespace(supports_tools=True))
    monkeypatch.setattr(cyber_agent_module, "set_memory_event_emitter", lambda _emitter: None)
    monkeypatch.setattr(cyber_agent_module, "init_agent_factory", lambda _config: None)
    monkeypatch.setattr(cyber_agent_module, "ConcurrentToolExecutor", lambda: "executor")
    monkeypatch.setattr(cyber_agent_module, "print_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cyber_agent_module, "ToolRepeatGuardHook", lambda *_args: "repeat-guard")

    import modules.handlers.react.agent_event_handler as event_handler_module
    import modules.handlers.react.hooks as hooks_module

    monkeypatch.setattr(event_handler_module, "AgentEventHandler", FakeCallback)
    monkeypatch.setattr(hooks_module, "ReactHooks", FakeHooks)

    runtime = cyber_agent_module.create_agent_runtime_resources("example.test", "assess", config)

    assert runtime.operation_id == "OP_INTEGRATED"
    assert runtime.config.model_id == "default-model"
    assert runtime.config.region_name == "us-test-1"
    assert runtime.sdk_context_manager is None
    assert runtime.termination_policy == "finish when evidence is complete"
    assert "MODULE EXECUTION GUIDANCE" in runtime.system_prompt
    assert runtime.tool_executor == "executor"
    assert runtime.optional_tools_list


def test_create_agent_runtime_resources_handles_degraded_integrations(monkeypatch, tmp_path):
    """Keep agent construction usable when optional runtime integrations fail."""

    class FailingConfigManager:
        def validate_requirements(self, _provider):
            return None

        def get_server_config(self, _provider, **overrides):
            assert overrides == {"model_id": "chosen-model"}
            return SimpleNamespace(
                llm=SimpleNamespace(model_id="ignored-default", max_tokens=512, temperature=0.2),
                swarm=SimpleNamespace(llm=SimpleNamespace(model_id="swarm-model")),
                output=SimpleNamespace(base_dir=str(tmp_path)),
                sdk=SimpleNamespace(conversation_window_size=8),
            )

        def get_default_region(self):
            raise AssertionError("explicit region must not be replaced")

        def get_qdrant_memory_config(self, _provider):
            return {}

        def ensure_operation_output_dirs(self, *_args, **_kwargs):
            raise OSError("read-only output")

        def getenv(self, name, default=None):
            return {"CYBER_SDK_CONTEXT_MANAGER": "auto", "CYBER_UI_MODE": "headless"}.get(name, default)

        def getenv_bool(self, _name, default=False):
            return default

        def getenv_int(self, _name, default):
            return default

    class EmptyLoader:
        def discover_module_tools(self, _module):
            return [], []

        def load_module_execution_prompt(self, _module, operation_root=None):
            return ""

        def load_module_termination_policy(self, _module):
            return ""

    class FakeCallback:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.emitter = object()
            self.coordinator = object()
            self.agent_run_id = "run-degraded"

        def emit_ui_event(self, _event):
            return None

    class FakeHooks:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    config = AgentConfig(
        target="example.test",
        objective="assess",
        provider="ollama",
        model_id="chosen-model",
        region_name="explicit-region",
        op_id="OP_DEGRADED",
        bug_bounty_headers={"X-Research": "approved"},
    )
    config.module = "web"
    config.available_tools = ["http_request"]

    monkeypatch.setattr(cyber_agent_module, "configure_sdk_logging", lambda **_kwargs: None)
    monkeypatch.setattr(cyber_agent_module, "get_config_manager", lambda: FailingConfigManager())
    monkeypatch.setattr(cyber_agent_module, "resolve_operation_targets", lambda *_args: [])
    monkeypatch.setattr(cyber_agent_module, "sanitize_target_name", lambda _target: "example_test")
    monkeypatch.setattr(cyber_agent_module, "require_prompt_token_limit", lambda *_args: 16_000)
    monkeypatch.setattr(cyber_agent_module, "resolve_tool_result_max_chars", lambda *_args: 3_000)
    monkeypatch.setattr(cyber_agent_module, "initialize_browser", lambda **_kwargs: None)
    monkeypatch.setattr(cyber_agent_module, "initialize_memory_system", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cyber_agent_module, "get_memory_client", lambda **_kwargs: SimpleNamespace(
        get_memory_overview=Mock(side_effect=RuntimeError("memory offline")),
        get_active_plan=lambda **_kwargs: None,
    ))
    prompt_builder = Mock(return_value="base prompt")
    monkeypatch.setattr(cyber_agent_module.prompts, "get_module_loader", lambda: EmptyLoader())
    monkeypatch.setattr(cyber_agent_module.prompts, "get_system_prompt", prompt_builder)
    monkeypatch.setattr(cyber_agent_module, "discover_mcp_tools", lambda _config: ["mcp-tool"])
    monkeypatch.setattr(cyber_agent_module, "resolve_seclists_root", lambda: str(tmp_path / "seclists"))
    monkeypatch.setattr(cyber_agent_module, "tool_append_description", lambda *_args: None)
    monkeypatch.setattr(cyber_agent_module, "create_absolute_path_editor", lambda editor: editor)
    monkeypatch.setattr(cyber_agent_module, "create_artifact_reader", lambda *_args, **_kwargs: "artifact-reader")
    monkeypatch.setattr(cyber_agent_module, "get_capabilities", lambda *_args: SimpleNamespace(supports_tools=False))
    def unavailable_browser(coroutine):
        coroutine.close()
        raise RuntimeError("no browser")

    monkeypatch.setattr("asyncio.run", unavailable_browser)
    monkeypatch.setattr(cyber_agent_module, "set_memory_event_emitter", lambda _emitter: None)
    monkeypatch.setattr(cyber_agent_module, "init_agent_factory", lambda _config: None)
    monkeypatch.setattr(cyber_agent_module, "ConcurrentToolExecutor", lambda: "executor")
    monkeypatch.setattr(cyber_agent_module, "print_status", lambda *_args, **_kwargs: None)

    import modules.handlers.react.agent_event_handler as event_handler_module
    import modules.handlers.react.hooks as hooks_module

    monkeypatch.setattr(event_handler_module, "AgentEventHandler", FakeCallback)
    monkeypatch.setattr(hooks_module, "ReactHooks", FakeHooks)

    runtime = cyber_agent_module.create_agent_runtime_resources("example.test", "assess", config)

    assert runtime.config.model_id == "chosen-model"
    assert runtime.config.region_name == "explicit-region"
    assert runtime.sdk_context_manager == "auto"
    assert runtime.termination_policy == ""
    assert "BUG BOUNTY TRAFFIC MARKERS" in prompt_builder.call_args.kwargs["tools_context"]
    assert "mcp-tool" in runtime.tools_list


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
