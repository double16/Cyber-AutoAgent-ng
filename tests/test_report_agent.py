import pytest
from unittest.mock import patch, MagicMock
from modules.agents.report_agent import ReportGenerator

@patch("modules.agents.report_agent.get_config_manager")
@patch("modules.agents.report_agent.Agent")
@patch("modules.agents.report_agent.BedrockModel")
def test_create_report_agent_custom_system_prompt(mock_bedrock, mock_agent, mock_cfg):
    # Setup mocks
    mock_cfg.return_value.get_llm_config.return_value.model_id = "test-model"
    mock_cfg.return_value.get_server_config.return_value.region = "us-east-1"
    
    # Test with custom system prompt
    ReportGenerator.create_report_agent(provider="bedrock", system_prompt="Custom System Prompt")
    
    # Verify Agent was created with custom system prompt
    args, kwargs = mock_agent.call_args
    assert kwargs["system_prompt"] == "Custom System Prompt"

@patch("modules.agents.report_agent.get_config_manager")
@patch("modules.agents.report_agent.Agent")
@patch("modules.agents.report_agent.LiteLLMModel")
def test_create_report_agent_litellm(mock_litellm, mock_agent, mock_cfg):
    # Setup mocks
    mock_cfg.return_value.get_llm_config.return_value.model_id = "test-model"
    
    ReportGenerator.create_report_agent(provider="litellm", system_prompt="Report Prompt")
    
    # Verify LiteLLMModel was created
    mock_litellm.assert_called()
    assert mock_litellm.call_args[1]["model_id"] == "test-model"
