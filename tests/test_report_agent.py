from unittest.mock import patch

from modules.agents.report_agent import ReportGenerator


@patch("modules.agents.report_agent.get_config_manager")
@patch("modules.agents.report_agent.Agent")
@patch("modules.agents.report_agent.create_strands_model")
def test_create_report_agent_custom_system_prompt(mock_create_model, mock_agent, mock_cfg):
    # Setup mocks
    mock_cfg.return_value.get_llm_config.return_value.model_id = "test-model"
    # Test with custom system prompt
    ReportGenerator.create_report_agent(provider="bedrock", system_prompt="Custom System Prompt")

    # Verify Agent was created with custom system prompt
    args, kwargs = mock_agent.call_args
    assert kwargs["system_prompt"] == "Custom System Prompt"
    assert kwargs["context_manager"] == "auto"
    mock_create_model.assert_called_once_with("bedrock", "test-model", "report_agent")


@patch("modules.agents.report_agent.get_config_manager")
@patch("modules.agents.report_agent.Agent")
@patch("modules.agents.report_agent.create_strands_model")
def test_create_report_agent_litellm(mock_create_model, mock_agent, mock_cfg):
    # Setup mocks
    mock_cfg.return_value.get_llm_config.return_value.model_id = "test-model"
    ReportGenerator.create_report_agent(provider="litellm", system_prompt="Report Prompt")

    mock_create_model.assert_called_once_with("litellm", "test-model", "report_agent")


@patch("modules.agents.report_agent.get_config_manager")
@patch("modules.agents.report_agent.Agent")
@patch("modules.agents.report_agent.create_strands_model")
def test_create_report_critic_uses_distinct_role_metadata(mock_create_model, mock_agent, mock_cfg):
    mock_cfg.return_value.get_llm_config.return_value.model_id = "test-model"

    ReportGenerator.create_report_agent(
        provider="litellm",
        system_prompt="Critic Prompt",
        operation_id="OP-CRITIC",
        agent_role="report_critic",
    )

    kwargs = mock_agent.call_args.kwargs
    mock_create_model.assert_called_once_with("litellm", "test-model", "report_critic")
    assert kwargs["name"] == "Cyber-ReportCritic OP-CRITIC"
    assert kwargs["trace_attributes"]["langfuse.agent.type"] == "report_critic"
    assert kwargs["trace_attributes"]["agent.role"] == "report_critic"


@patch("modules.agents.report_agent.get_config_manager")
@patch("modules.agents.report_agent.Agent")
@patch("modules.agents.report_agent.create_strands_model")
def test_create_report_agent_retries_without_context_manager_for_stateful_model(
    mock_create_model, mock_agent, mock_cfg
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
