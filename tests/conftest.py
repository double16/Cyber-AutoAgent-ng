#!/usr/bin/env python3

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from modules.config.models.capabilities import (
    get_model_input_limit,
    get_model_output_limit,
    get_model_pricing,
)
from modules.tools import result_cache

# Disable dotenv loading in tests
os.environ["PYTHON_DOTENV_DISABLED"] = "true"

# Disable download from models.dev, use cache or snapshot
os.environ["DEV_CLIENT_OFFLINE"] = "true"

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ["ENABLE_LANGFUSE_PROMPTS"] = "false"


@pytest.fixture(autouse=True)
def isolate_tool_result_cache(monkeypatch, tmp_path):
    """Keep filesystem-backed tool result cache entries isolated between tests."""
    monkeypatch.setattr(result_cache, "RESULT_CACHE_DIR", tmp_path / "tool-result-cache")


# Ensure provider override envs do not leak into tests expecting defaults
for _var in (
    "CYBER_AGENT_PROVIDER",
    "CYBER_AGENT_LLM_MODEL",
    "CYBER_AGENT_EMBEDDING_MODEL",
    "CYBER_AGENT_SWARM_MODEL",
    "CYBER_AGENT_EVALUATION_MODEL",
    "RAGAS_EVALUATOR_MODEL",
    "CYBER_CONTEXT_LIMIT",
    "CYBER_REASONING_ALLOW",
    "CYBER_REASONING_DENY",
    "CYBER_RATE_LIMIT_TOKENS_PER_MIN",
    "CYBER_RATE_LIMIT_REQ_PER_MIN",
    "CYBER_RATE_LIMIT_MAX_CONCURRENT",
    "AZURE_API_BASE",
    "AZURE_API_KEY",
    "AZURE_API_VERSION",
    "OLLAMA_HOST",
    "OLLAMA_CONTEXT_LENGTH",
    "OLLAMA_TIMEOUT",
    "OLLAMA_KEEP_ALIVE",
    "MAX_COMPLETION_TOKENS",
    "MAX_TOKENS",
    "ENABLE_OBSERVABILITY",
    "QDRANT_URL",
    "QDRANT_API_KEY",
):
    os.environ.pop(_var, None)


@pytest.fixture(autouse=True)
def restore_provider_override_environment():
    """Prevent in-process CLI tests from leaking provider overrides to later tests."""
    keys = (
        "CYBER_AGENT_PROVIDER",
        "CYBER_AGENT_LLM_MODEL",
        "CYBER_AGENT_EMBEDDING_MODEL",
        "CYBER_AGENT_SWARM_MODEL",
        "CYBER_AGENT_EVALUATION_MODEL",
        "CYBER_MEMORY_MODE",
        "QDRANT_URL",
        "QDRANT_API_KEY",
        "QDRANT_COLLECTION",
    )
    original = {key: os.environ.get(key) for key in keys}
    yield
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def temp_data_dir():
    """Create a temporary directory for test data"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def outputs_dir():
    """Return the path to the expected output directory"""
    cwd = Path.cwd()
    if (cwd / "tests").exists():
        yield cwd / "outputs"
    else:
        yield Path.cwd() / ".." / "outputs"


@pytest.fixture
def ollama_taxonomy_client(request):
    """Return an installed local Ollama model or skip without downloading one."""

    import ollama

    model = request.config.getoption("ollama_model")
    client = ollama.Client(
        host=request.config.getoption("ollama_host"),
        timeout=request.config.getoption("ollama_timeout"),
    )
    try:
        response = client.list()
    except Exception as error:
        pytest.skip(f"Ollama is unavailable: {error}")
    models = getattr(response, "models", None)
    if models is None and isinstance(response, dict):
        models = response.get("models", [])
    installed = set()
    for item in models or []:
        name = item.get("model", "") if isinstance(item, dict) else getattr(item, "model", "")
        if name:
            installed.add(str(name))
    if model not in installed:
        pytest.skip(f"Ollama model {model!r} is not installed")
    return client, model


@pytest.fixture
def mock_ollama_available():
    """Mock Ollama availability"""
    with patch("modules.agents.cyber_autoagent.OLLAMA_AVAILABLE", True):
        yield


@pytest.fixture
def mock_ollama_unavailable():
    """Mock Ollama unavailability"""
    with patch("modules.agents.cyber_autoagent.OLLAMA_AVAILABLE", False):
        yield


@pytest.fixture
def mock_aws_credentials():
    """Mock AWS credentials in environment"""
    with patch.dict(
        os.environ,
        {"AWS_ACCESS_KEY_ID": "test_key", "AWS_SECRET_ACCESS_KEY": "test_secret"},
    ):
        yield


@pytest.fixture
def mock_no_aws_credentials():
    """Mock no AWS credentials in environment"""
    # Clear AWS-related environment variables
    env_vars_to_clear = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE"]
    original_values = {}

    for var in env_vars_to_clear:
        if var in os.environ:
            original_values[var] = os.environ[var]
            del os.environ[var]

    yield

    # Restore original values
    for var, value in original_values.items():
        os.environ[var] = value


@pytest.fixture
def mock_ollama_server_running():
    """Mock Ollama server running successfully"""
    with patch("modules.agents.cyber_autoagent.requests.get") as mock_get:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response
        yield mock_get


@pytest.fixture
def mock_ollama_server_down():
    """Mock Ollama server not running"""
    with patch("modules.agents.cyber_autoagent.requests.get") as mock_get:
        mock_get.side_effect = Exception("Connection refused")
        yield mock_get


@pytest.fixture
def mock_ollama_models_available():
    """Mock Ollama models being available"""
    with patch("modules.agents.cyber_autoagent.ollama.Client") as mock_client:
        mock_client_instance = mock_client.return_value
        mock_client_instance.list.return_value = {
            "models": [
                {"model": "llama3.2:3b"},
                {"model": "mxbai-embed-large:latest"},
                {"model": "other-model:latest"},
            ]
        }
        yield mock_client


@pytest.fixture
def mock_ollama_models_missing():
    """Mock Ollama models not available"""
    with patch("modules.agents.cyber_autoagent.ollama.list") as mock_list:
        mock_list.return_value = {"models": [{"name": "some-other-model:latest"}]}
        yield mock_list


@pytest.fixture
def mock_memory_tools():
    """Mock memory tools module"""
    with patch("modules.agents.cyber_autoagent.memory_tools") as mock_tools:
        mock_tools.memory_instance = None
        mock_tools.operation_id = None
        yield mock_tools


@pytest.fixture
def mock_strands_components():
    """Mock Strands framework components"""
    with (
        patch("modules.agents.cyber_autoagent.Agent") as mock_agent,
        patch("modules.agents.cyber_autoagent.BedrockModel") as mock_bedrock,
        patch("modules.handlers.react.agent_event_handler.AgentEventHandler") as mock_handler,
        patch("modules.agents.cyber_autoagent.Memory.from_config") as mock_memory,
        patch("modules.agents.cyber_autoagent.get_system_prompt") as mock_prompt,
    ):
        mock_prompt.return_value = "test system prompt"
        yield {
            "agent": mock_agent,
            "bedrock": mock_bedrock,
            "handler": mock_handler,
            "memory": mock_memory,
            "prompt": mock_prompt,
        }


@pytest.fixture
def mock_ollama_model():
    """Mock OllamaModel when available"""
    with patch("modules.agents.cyber_autoagent.OllamaModel") as mock_model:
        yield mock_model


@pytest.fixture
def sample_agent_config():
    """Sample configuration for agent creation"""
    return {
        "target": "test.example.com",
        "objective": "Test security assessment",
        "budget": {"max_duration_minutes": 60},
        "available_tools": ["nmap", "nikto"],
        "model_id": None,
        "region_name": "us-east-1",
        "server": "remote",
    }


@pytest.fixture
def caplog_with_level():
    """Pytest caplog fixture with specific log level"""
    import logging

    def _caplog_with_level(level=logging.INFO):
        import _pytest.logging

        return _pytest.logging.LogCaptureFixture(pytest_config=None)

    return _caplog_with_level


@pytest.fixture
def clear_lru_caches():
    fns = [
        get_model_input_limit,
        get_model_output_limit,
        get_model_pricing,
    ]
    for fn in fns:
        fn.cache_clear()
    yield
    for fn in fns:
        fn.cache_clear()


def pytest_addoption(parser):
    ollama_group = parser.getgroup("ollama")
    ollama_group.addoption(
        "--ollama-model",
        action="store",
        default="qwen3.6:27b-mlx",
        help="Installed Ollama model for tests marked ollama.",
    )
    ollama_group.addoption(
        "--ollama-host",
        action="store",
        default="http://localhost:11434",
        help="Ollama host for tests marked ollama.",
    )
    ollama_group.addoption(
        "--ollama-timeout",
        action="store",
        type=float,
        default=120.0,
        help="Per-request timeout in seconds for tests marked ollama.",
    )
    parser.addoption(
        "--browser",
        action="store_true",
        default=False,
        help="Run tests that require the browser."
    )
    parser.addoption(
        "--external",
        action="store_true",
        default=False,
        help="Run tests that reach out to the Internet."
    )


def pytest_runtest_setup(item):
    if "browser" in item.keywords and not item.config.getoption("--browser"):
        pytest.skip("Test requires --browser option to run.")

    if "external" in item.keywords and not item.config.getoption("--external"):
        pytest.skip("Test requires --external option to run.")

    if "ollama" in item.keywords:
        ollama_host = item.config.getoption("ollama_host")
        if "://" not in ollama_host:
            ollama_host = "http://" + ollama_host
        try:
            r = requests.get(f"{ollama_host}/api/tags", timeout=5)
            r.raise_for_status()
        except (requests.RequestException, ValueError):
            pytest.skip(f"Skipping tests: Ollama is not available at {ollama_host}", allow_module_level=True)
