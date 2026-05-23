import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from modules.agents.cyber_autoagent import AgentConfig, create_agent


@patch("modules.agents.cyber_autoagent.browser_set_headers")
@patch("modules.config.ConfigManager.validate_requirements")
@patch("modules.config.models.factory.create_ollama_model")
@patch("modules.agents.cyber_autoagent.Agent")
@patch("modules.handlers.react.react_bridge_handler.ReactBridgeHandler")
@patch("modules.agents.cyber_autoagent.prompts.get_system_prompt")
@patch("modules.agents.cyber_autoagent.initialize_memory_system")
def test_bug_bounty_headers_are_applied_and_added_to_prompt(
    mock_init_memory,
    mock_get_prompt,
    mock_react_bridge_handler,
    mock_agent_class,
    mock_create_ollama,
    mock_validate,
    mock_browser_set_headers,
):
    mock_model = Mock()
    mock_create_ollama.return_value = mock_model
    mock_agent = Mock()
    mock_agent_class.return_value = mock_agent
    mock_handler = Mock()
    mock_react_bridge_handler.return_value = mock_handler
    mock_get_prompt.return_value = "test prompt"

    headers = {
        "User-Agent": "researcher@wearehackerone.com",
        "X-HackerOne-Research": "researcher",
    }

    config = AgentConfig(
        target="example.com",
        objective="authorized test",
        provider="ollama",
        bug_bounty_headers=headers,
    )
    create_agent(target="example.com", objective="authorized test", config=config)

    mock_browser_set_headers.assert_called_once_with(headers)
    prompt_kwargs = mock_get_prompt.call_args.kwargs
    tools_context = prompt_kwargs["tools_context"]
    assert "BUG BOUNTY TRAFFIC MARKERS" in tools_context
    assert "X-HackerOne-Research: researcher" in tools_context
    assert "User-Agent: researcher@wearehackerone.com" in tools_context
    assert "MCP tools" in tools_context
