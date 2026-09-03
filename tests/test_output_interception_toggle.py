#!/usr/bin/env python3

from types import SimpleNamespace
from unittest.mock import Mock, patch


class FakeReactHooks:
    def __init__(self, *args, **kwargs):
        pass

    def register_hooks(self, registry, **kwargs):
        pass


def _stateless_model_mock():
    model = Mock()
    model.stateful = False
    return model


def _default_getenv(name, default=None):
    return default


def _minimal_server_config():
    return SimpleNamespace(
        llm=SimpleNamespace(model_id="claude-3-sonnet", max_tokens=256000, temperature=0.55),
        output=SimpleNamespace(base_dir="./outputs"),
        swarm=SimpleNamespace(llm=SimpleNamespace(model_id="claude-3-sonnet")),
        sdk=SimpleNamespace(conversation_window_size=64),
    )


@patch("modules.handlers.output_interceptor.setup_output_interception")
@patch("modules.agents.cyber_autoagent.get_config_manager")
@patch("modules.config.models.factory.create_bedrock_model")
@patch("modules.handlers.react.hooks.ReactHooks")
@patch("modules.handlers.react.agent_event_handler.AgentEventHandler")
@patch("modules.agents.cyber_autoagent.initialize_memory_system")
@patch("modules.agents.cyber_autoagent.get_memory_client", return_value=None)
def test_output_interception_react_only(
    mock_get_memory_client,
    mock_init_memory,
    mock_rbh,
    mock_hooks,
    mock_create_model,
    mock_get_cfg,
    mock_setup_intercept,
    monkeypatch,
):
    mock_rbh.return_value = SimpleNamespace(emitter=None, emit_ui_event=lambda _event: None)
    mock_hooks.side_effect = FakeReactHooks
    mock_model = _stateless_model_mock()
    mock_create_model.return_value = mock_model

    mock_cfg = Mock()
    mock_cfg.validate_requirements.return_value = None
    mock_cfg.getenv.side_effect = _default_getenv
    mock_cfg.get_server_config.return_value = _minimal_server_config()
    mock_cfg.get_default_region.return_value = "us-east-1"
    mock_cfg.get_qdrant_memory_config.return_value = {
        "embedding_provider": "bedrock", "embedding_model": "test", "embedding_dimensions": 1024,
    }
    mock_get_cfg.return_value = mock_cfg

    from modules.agents.cyber_autoagent import AgentConfig, create_agent

    # CLI mode: should NOT setup interception
    monkeypatch.setenv("CYBER_UI_MODE", "cli")
    config = AgentConfig(target="t", objective="o", provider="bedrock", op_id="OP_TEST")
    create_agent(target="t", objective="o", config=config)
    assert mock_setup_intercept.call_count == 0

    # React mode: should setup interception
    monkeypatch.setenv("CYBER_UI_MODE", "react")
    config = AgentConfig(
        target="t2", objective="o2", provider="bedrock", op_id="OP_TEST2"
    )
    create_agent(target="t2", objective="o2", config=config)
    assert mock_setup_intercept.call_count == 1
