from unittest.mock import MagicMock, patch

from modules.agents.report_agent import ReportExecutionTelemetryHook, ReportGenerator


@patch("modules.agents.report_agent.get_config_manager")
@patch("modules.agents.report_agent.Agent")
@patch("modules.agents.report_agent.create_strands_model")
def test_create_report_agent_custom_system_prompt(mock_create_model, mock_agent, mock_cfg):
    # Setup mocks
    mock_cfg.return_value.get_llm_config.return_value.model_id = "test-model"
    # Test with custom system prompt
    ReportGenerator.create_report_agent(provider="bedrock", system_prompt="Custom System Prompt")

    # Verify Agent was created with custom system prompt
    _, kwargs = mock_agent.call_args
    assert kwargs["system_prompt"] == "Custom System Prompt"
    assert "context_manager" not in kwargs
    assert kwargs["tools"] == []
    assert any(isinstance(hook, ReportExecutionTelemetryHook) for hook in kwargs["hooks"])
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
    assert kwargs["tools"] == []
    assert "context_manager" not in kwargs


@patch("modules.agents.report_agent.get_config_manager")
@patch("modules.agents.report_agent.Agent")
@patch("modules.agents.report_agent.create_strands_model")
def test_create_report_agent_supports_stateful_model_without_context_manager(
    mock_create_model, mock_agent, mock_cfg
):
    mock_cfg.return_value.get_llm_config.return_value.model_id = "test-model"
    stateful_agent = object()
    mock_agent.return_value = stateful_agent

    result = ReportGenerator.create_report_agent(provider="litellm", system_prompt="Report Prompt")

    assert result is stateful_agent
    mock_agent.assert_called_once()
    assert "context_manager" not in mock_agent.call_args.kwargs
    assert mock_agent.call_args.kwargs["tools"] == []


def test_report_execution_telemetry_records_model_and_unexpected_tool_events(caplog):
    caplog.set_level("INFO")
    hook = ReportExecutionTelemetryHook()
    model_start = MagicMock(invocation_state={"report_section": "Executive summary"}, projected_input_tokens=42)
    model_stop = MagicMock(
        invocation_state={"report_section": "Executive summary"},
        stop_response=MagicMock(stop_reason="tool_use"),
        exception=None,
    )
    tool_start = MagicMock(
        invocation_state={"report_section": "Executive summary"},
        selected_tool=MagicMock(tool_name="read_artifact"),
        tool_use={"name": "read_artifact"},
    )
    tool_stop = MagicMock(
        invocation_state={"report_section": "Executive summary"},
        selected_tool=MagicMock(tool_name="read_artifact"),
        tool_use={"name": "read_artifact"},
        exception=RuntimeError("blocked"),
    )

    hook.before_model_call(model_start)
    hook.after_model_call(model_stop)
    hook.before_tool_call(tool_start)
    hook.after_tool_call(tool_stop)

    assert "projected_input_tokens=42" in caplog.text
    assert "attempted unexpected tool use" in caplog.text
    assert "unexpected tool completed" in caplog.text


def test_report_execution_telemetry_registers_hooks_and_records_invocation_fallbacks(caplog):
    hook = ReportExecutionTelemetryHook()
    callbacks = []
    registry = MagicMock()
    registry.add_callback.side_effect = lambda event, callback: callbacks.append((event, callback))
    hook.register_hooks(registry)

    caplog.set_level("INFO")
    hook.after_model_call(MagicMock(invocation_state={}, stop_response=None, exception=RuntimeError("failed")))
    hook.before_tool_call(MagicMock(invocation_state={}, selected_tool=None, tool_use={"name": "fallback"}))
    hook.after_invocation(
        MagicMock(
            invocation_state={},
            result=MagicMock(stop_reason="complete", metrics=MagicMock(latest_agent_invocation=MagicMock(cycles=[1, 2]))),
        )
    )

    assert len(callbacks) == 5
    assert "section=report" in caplog.text
    assert "turns=2" in caplog.text
