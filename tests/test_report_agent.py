from unittest.mock import patch

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
    assert kwargs["context_manager"] == "auto"
    mock_bedrock.return_value.update_config.assert_called_once_with(context_window_limit=200000)


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
    mock_litellm.return_value.update_config.assert_called_once_with(context_window_limit=128000)


@patch("modules.agents.report_agent.get_config_manager")
@patch("modules.agents.report_agent.Agent")
@patch("modules.agents.report_agent.LiteLLMModel")
def test_create_report_critic_uses_distinct_role_metadata(mock_litellm, mock_agent, mock_cfg):
    mock_cfg.return_value.get_llm_config.return_value.model_id = "test-model"

    ReportGenerator.create_report_agent(
        provider="litellm",
        system_prompt="Critic Prompt",
        operation_id="OP-CRITIC",
        agent_role="report_critic",
    )

    kwargs = mock_agent.call_args.kwargs
    assert kwargs["name"] == "Cyber-ReportCritic OP-CRITIC"
    assert kwargs["trace_attributes"]["langfuse.agent.type"] == "report_critic"
    assert kwargs["trace_attributes"]["agent.role"] == "report_critic"


@patch("modules.agents.report_agent.get_config_manager")
@patch("modules.agents.report_agent.Agent")
@patch("modules.agents.report_agent.LiteLLMModel")
def test_create_report_agent_retries_without_context_manager_for_stateful_model(
    mock_litellm, mock_agent, mock_cfg
):
    mock_cfg.return_value.get_llm_config.return_value.model_id = "test-model"
    fallback_agent = object()
    mock_agent.side_effect = [
        ValueError("context_manager and conversation_manager cannot be used with a stateful model"),
        fallback_agent,
    ]

    result = ReportGenerator.create_report_agent(provider="litellm", system_prompt="Report Prompt")

    assert result is fallback_agent
    assert mock_agent.call_count == 2
    assert mock_agent.call_args_list[0].kwargs["context_manager"] == "auto"
    assert "context_manager" not in mock_agent.call_args_list[1].kwargs
