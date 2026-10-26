from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.techniques import cli, roles


def test_cli_only_writes_markdown_to_stdout_and_creates_no_use_receipt(scripted_service, monkeypatch, capsys):
    state = scripted_service
    revision = state.publish()
    state.selection = revision

    def build(*args):
        print("Diagnostic message")
        return state.service

    monkeypatch.setattr(cli, "build_service", build)
    assert cli.main(["JWT handling", "--provider", "ollama", "--output-dir", "/unused"]) == 0
    output = capsys.readouterr()
    assert output.out.startswith("---\n")
    assert "Diagnostic message" not in output.out
    assert "Diagnostic message" in output.err
    assert not state.service.repository.pending_uses("", "")


def test_cli_failure_has_nonzero_exit_and_empty_stdout(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_service", Mock(side_effect=RuntimeError("Storage unavailable")))
    assert cli.main(["JWT handling", "--provider", "ollama"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Storage unavailable" in output.err


def test_cli_module_entrypoint_supports_help_without_initializing_storage(monkeypatch):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["caa-technique", "--help"])
    monkeypatch.delitem(sys.modules, "modules.techniques.cli", raising=False)
    with pytest.raises(SystemExit) as error:
        runpy.run_module("modules.techniques.cli", run_name="__main__")
    assert error.value.code == 0


def test_role_turn_limit_prevents_unbounded_research_loops():
    limit = roles.RoleTurnLimit()
    registry = Mock()
    limit.register_hooks(registry)
    registry.add_callback.assert_called_once()
    for _ in range(12):
        limit.before_model(None)
    with pytest.raises(RuntimeError, match="twelve-turn"):
        limit.before_model(None)


@pytest.mark.parametrize("stateful", [True, False])
def test_role_runner_uses_fresh_restricted_agents_with_assembled_instructions(monkeypatch, stateful):
    model = SimpleNamespace(stateful=stateful)
    monkeypatch.setattr(roles, "create_strands_model", lambda *args: model)
    monkeypatch.setattr(roles, "require_prompt_token_limit", lambda *args: 48_000)
    result = SimpleNamespace(message={"content": [{"text": "Final document"}, {"reasoningContent": {}}]})
    agents = []
    calls = []

    def create(kwargs, model_id):
        calls.append(kwargs)
        agent = Mock(return_value=result)
        agents.append(agent)
        return agent

    monkeypatch.setattr(roles, "create_agent_with_stateful_retry", create)
    callback = Mock(return_value=Mock())
    runner = roles.TechniqueRoleRunner("ollama", "model", callback)
    for role in (
        "technique_researcher",
        "technique_judge",
        "technique_author",
        "technique_critic",
        "technique_evaluator",
    ):
        assert runner(role, "Assigned bounded role", []) == "Final document"
    assert len(agents) == 5
    for kwargs in calls:
        assert kwargs["tools"] == []
        assert kwargs["load_tools_from_directory"] is False
        assert "Python 3.13+" in kwargs["system_prompt"]
        assert "untrusted data" in kwargs["system_prompt"]
        assert ("conversation_manager" in kwargs) is not stateful
    if not stateful:
        assert calls[0]["conversation_manager"] is not calls[1]["conversation_manager"]


@pytest.mark.parametrize("synchronize", [True, False])
def test_cli_build_and_bootstrap_reuse_storage_without_fake_target(store, monkeypatch, tmp_path, synchronize):
    from modules.config import manager as manager_module
    from modules.techniques.bootstrap import operation_techniques
    from modules.tools import memory

    manager = SimpleNamespace(
        get_qdrant_memory_config=lambda provider: {
            "embedding_dimensions": 3,
            "embedding_model": "test",
            "embeddings": store.embeddings,
        },
        get_llm_config=lambda provider: SimpleNamespace(model_id="test"),
    )
    monkeypatch.setattr(manager_module, "get_config_manager", lambda: manager)
    monkeypatch.setattr("modules.config.get_config_manager", lambda: manager)
    monkeypatch.setattr("modules.storage.qdrant.open_qdrant", lambda output: store.qdrant)
    service = cli.build_service("ollama", None, str(tmp_path / "cli"), "web")
    assert len(service.repository.revisions()) == 2
    monkeypatch.setattr(memory, "get_application_database_path", lambda: str(tmp_path / "operation.db"))
    client = SimpleNamespace(qdrant=store.qdrant, embeddings=store.embeddings, config={"embedding_dimensions": 3})
    config = SimpleNamespace(provider="ollama", model_id="model", module="ctf", target="https://assigned.invalid")
    operation_service = operation_techniques(client, config, Mock(), synchronize=synchronize)
    if not synchronize:
        from modules.prompts.factory import get_module_loader

        assert not operation_service.repository.revisions()
        events = []
        operation_service.events.callback = events.append
        operation_service.synchronize_module(get_module_loader(), config.module)
        assert events[-1]["type"] == "technique_sync_complete"
    assert operation_service.store.qdrant is store.qdrant
    assert len(operation_service.repository.revisions()) == 3


def test_cli_build_closes_client_on_initialization_failure(store, monkeypatch, tmp_path):
    monkeypatch.setattr("modules.storage.qdrant.open_qdrant", lambda output: store.qdrant)
    monkeypatch.setattr("modules.storage.qdrant.build_embeddings", Mock(side_effect=ValueError("bad embedding")))
    close = Mock()
    monkeypatch.setattr(store.qdrant, "close", close)
    with pytest.raises(ValueError, match="bad embedding"):
        cli.build_service("bedrock", "model", str(tmp_path), "web")
    close.assert_called_once()


@pytest.mark.parametrize("invalid", [False, True])
def test_cli_generated_document_and_generation_failure_keep_output_contract(
    scripted_service, monkeypatch, capsys, invalid
):
    state = scripted_service
    state.bad_document = invalid
    monkeypatch.setattr(cli, "build_service", lambda *args: state.service)
    result = cli.main(["generic guidance", "--provider", "ollama"])
    output = capsys.readouterr()
    if invalid:
        assert result == 1
        assert output.out == ""
        assert "3 attempts at author_json" in output.err
        assert "independent critique" not in output.err
    else:
        assert result == 0
        assert output.out.startswith("---\n")
        assert "# Guidance" in output.out
        assert output.err == ""
