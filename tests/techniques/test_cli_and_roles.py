import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from modules.techniques import cli, roles


def test_cli_only_writes_markdown_to_stdout_and_creates_no_use_receipt(scripted_service, monkeypatch, capsys, tmp_path):
    state = scripted_service
    revision = state.publish()
    state.selection = revision

    def build(*args, **kwargs):
        print("Diagnostic message")
        return state.service

    monkeypatch.setattr(cli, "build_service", build)
    assert cli.main(["JWT handling", "--provider", "ollama", "--output-dir", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert output.out.startswith("# Technique bundle")
    assert "Diagnostic message" not in output.out
    assert "Diagnostic message" not in output.err
    assert "Diagnostic message" in next((tmp_path / "techniques" / "logs").glob("*.jsonl")).read_text()
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
        from strands.agent.conversation_manager import NullConversationManager

        assert isinstance(calls[0]["conversation_manager"], NullConversationManager)
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
        get_model_id=lambda provider, model=None: model or "test",
    )
    monkeypatch.setattr(manager_module, "get_config_manager", lambda: manager)
    monkeypatch.setattr("modules.config.get_config_manager", lambda: manager)
    monkeypatch.setattr("modules.storage.qdrant.open_qdrant", lambda output: store.qdrant)
    from modules.techniques.diagnostics import DiagnosticSession

    diagnostics = DiagnosticSession(quiet=True)
    service = cli.build_service("ollama", None, str(tmp_path / "cli"), "web", diagnostics=diagnostics)
    assert "https://portswigger.net/web-security" in service.research.preferred_sites
    assert service.events.callback == diagnostics.lifecycle
    assert service.runner.diagnostics is diagnostics
    assert len(service.repository.revisions()) == len(list(
        Path("src/modules/operation_plugins/web/techniques").glob("*.md")))
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
    assert operation_service.research.preferred_sites == service.research.preferred_sites
    assert operation_service.store.qdrant is store.qdrant
    assert len(operation_service.repository.revisions()) == sum(
        len(list((Path("src/modules/operation_plugins") / module / "techniques").glob("*.md")))
        for module in ("web", "ctf")
    )


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
    monkeypatch.setattr(cli, "build_service", lambda *args, **kwargs: state.service)
    result = cli.main(["generic guidance", "--provider", "ollama"])
    output = capsys.readouterr()
    if invalid:
        assert result == 1
        assert output.out == ""
        assert "3 attempts at author_json" in output.err
        assert "independent critique" not in output.err
    else:
        assert result == 0
        assert output.out.startswith("# Technique bundle")
        assert "# Guidance" in output.out
        assert "Initializing technique service" in output.err


def test_role_runner_rejects_oversized_input_before_model_call(monkeypatch):
    monkeypatch.setattr(roles, "require_prompt_token_limit", lambda *args: 100)
    create = Mock()
    monkeypatch.setattr(roles, "create_strands_model", create)
    with pytest.raises(ValueError, match="configured prompt limit"):
        roles.TechniqueRoleRunner("ollama", "test")("technique_critic", "exact document " * 2000, [])
    create.assert_not_called()


def test_partial_cli_bundle_succeeds_with_incompleteness_notice(scripted_service, monkeypatch, capsys):
    from modules.techniques.bundles import Component, Delivered, TechniqueBundle, Unresolved

    state = scripted_service
    revision = state.publish()
    component = Component(id="one", request="Source review", purpose="Trace data flow", requirements=[
        {"id": "flow", "description": "Trace data flow"},
    ])
    bundle = TechniqueBundle(delivered=[Delivered(component=component, revision_id=revision,
                                                 markdown=state.service.store.render(revision))],
                             unresolved=[Unresolved(component=component, reason="Version research exhausted")])
    monkeypatch.setattr(state.service, "resolve_bundle", lambda request, context=None: bundle)
    monkeypatch.setattr(cli, "build_service", lambda *args, **kwargs: state.service)
    assert cli.main(["Source review", "--provider", "ollama"]) == 0
    output = capsys.readouterr()
    assert "Incomplete guidance" in output.out and "Version research exhausted" in output.out


@pytest.mark.parametrize("role,profile", [
    ("technique_request_aligner", "technique_judge"),
    ("technique_request_reviewer", "technique_critic"),
    ("technique_coverage_reviewer", "technique_critic"),
    ("technique_planner", "technique_judge"),
    ("technique_plan_reviewer", "technique_critic"),
    ("technique_applicability_confirmation", "technique_judge"),
])
def test_planning_and_confirmation_use_registered_profiles(monkeypatch, role, profile):
    model = Mock()
    monkeypatch.setattr(roles, "create_strands_model", model)
    monkeypatch.setattr(roles, "require_prompt_token_limit", lambda *args: 48_000)
    agent = Mock(return_value=SimpleNamespace(message={"content": [{"text": "{}"}]}))
    monkeypatch.setattr(roles, "create_agent_with_stateful_retry", lambda *args: agent)
    roles.TechniqueRoleRunner("ollama", "test")(role, "Assess assigned component", [])
    model.assert_called_once_with("ollama", "test", profile)


@pytest.mark.parametrize("options,phases,constraints", [
    ([], None, None),
    (["--phase", "analysis"], ["analysis"], []),
    (["--constraint", "Read only"], [], ["Read only"]),
    (["--phase", "analysis", "--phase", "hypothesis_generation", "--phase", "analysis",
      "--constraint", " Read only ", "--constraint", "No live execution", "--constraint", "Read only"],
     ["analysis", "hypothesis_generation"], ["Read only", "No live execution"]),
])
def test_cli_passes_optional_phase_and_constraints(monkeypatch, capsys, options, phases, constraints):
    resolve = Mock(return_value=SimpleNamespace(render=lambda: "# Guidance\n", delivered=[object()], unresolved=[]))
    service = SimpleNamespace(resolve_bundle=resolve, store=SimpleNamespace(qdrant=Mock()))
    monkeypatch.setattr(cli, "build_service", lambda *args, **kwargs: service)
    assert cli.main(["Trace input flow", "--provider", "ollama", *options]) == 0
    assert capsys.readouterr().out == "# Guidance\n"
    context = resolve.call_args.kwargs["context"]
    if phases is None:
        assert context is None
    else:
        assert context.phase_contexts == phases
        assert context.methodology_constraints == constraints
        assert not hasattr(context, "constraints")
        assert not hasattr(context, "purpose")
        assert not hasattr(context, "subjects")
    service.store.qdrant.close.assert_called_once()


@pytest.mark.parametrize("options", [
    ["--phase", "invented"], ["--phase"], ["--constraint"], ["--constraint", "  "],
])
def test_cli_rejects_invalid_context_options_before_storage(monkeypatch, capsys, options):
    build = Mock()
    monkeypatch.setattr(cli, "build_service", build)
    with pytest.raises(SystemExit) as error:
        cli.main(["Source review", *options])
    assert error.value.code == 2
    build.assert_not_called()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("phases", [[], ["hypothesis_generation"]])
def test_cli_context_reaches_applicability_author_and_critic(scripted_service, monkeypatch, capsys, phases):
    state = scripted_service
    monkeypatch.setattr(cli, "build_service", lambda *args, **kwargs: state.service)
    assert cli.main([
        "JWT handling", "--provider", "ollama", *[value for phase in phases for value in ("--phase", phase)],
        "--constraint", "No live execution",
    ]) == 0
    rendered = capsys.readouterr().out
    assert "Assessment: conditional" in rendered
    from modules.techniques.roles import SYSTEM

    applicability = next(prompt for role, prompt, _ in state.calls
                         if role == "technique_judge" and "generic_request" in prompt)
    assert "No live execution" in applicability
    if not phases:
        assert "Phase contexts: unknown" in applicability
    assert (
        "Task objectives, subjects, observations, tool capabilities and operation restrictions are not inputs."
        in SYSTEM + applicability
    )
    for role in ("technique_author", "technique_critic"):
        assembled = SYSTEM + next(prompt for selected, prompt, _ in state.calls if selected == role)
        assert f"Required phase contexts: {json.dumps(phases)}" in assembled
    assert not state.service.repository.pending_uses("", "")


def test_cli_phase_filters_reuse_and_rejects_generated_wrong_phase(scripted_service, monkeypatch, capsys):
    state = scripted_service
    state.selection = state.publish()  # Advertises hypothesis_generation only.
    monkeypatch.setattr(cli, "build_service", lambda *args, **kwargs: state.service)
    assert cli.main(["JWT handling", "--provider", "ollama", "--phase", "analysis"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "must support a requested phase context" in output.err
    assert state.research_calls


def test_cli_explicit_model_does_not_probe_environment_model(scripted_service, monkeypatch, capsys, tmp_path):
    from modules.config.manager import ConfigManager
    from modules.config.models import factory
    from modules.config.models import ollama as ollama_module

    monkeypatch.setenv("CYBER_AGENT_LLM_MODEL", "unused-model")
    monkeypatch.setenv("CYBER_AGENT_SWARM_MODEL", "unused-swarm")
    monkeypatch.setenv("CYBER_AGENT_EMBEDDING_MODEL", "mxbai-embed-large:latest")
    manager = ConfigManager()
    monkeypatch.setattr("modules.config.get_config_manager", lambda: manager)
    monkeypatch.setattr(factory, "_get_config_manager", lambda: manager)
    monkeypatch.setattr(manager, "get_server_config", Mock(side_effect=AssertionError("full config")))
    probes = []

    def get_limits(provider, model):
        probes.append(model)
        assert model == "selected-model"
        return 8192

    monkeypatch.setattr(manager, "get_max_tokens", get_limits)
    monkeypatch.setattr("modules.config.models.get_capabilities", lambda *_args: SimpleNamespace(
        pass_reasoning_effort=False, supports_reasoning=False, supports_temperature=True,
    ))
    monkeypatch.setattr(ollama_module, "OllamaModel", lambda **kwargs: SimpleNamespace(kwargs=kwargs))
    monkeypatch.setattr(factory, "_get_parameters_by_role", lambda *args: SimpleNamespace(
        llm_temp=0.2, llm_max=1000, top_k=None, top_p=None, reasoning_level=SimpleNamespace(value="none"),
        role=SimpleNamespace(value="technique_author"), profile_max_tokens=1000, output_ceiling=None,
    ))
    state = scripted_service
    state.selection = state.publish()

    def build(provider, model, output_dir, module, **kwargs):
        assert model == "selected-model"
        assert manager.get_qdrant_memory_config(provider)["embedding_model"] == "mxbai-embed-large:latest"
        created = factory.create_ollama_model(model, provider, "technique_author")
        assert created.kwargs["model_id"] == model
        return state.service

    monkeypatch.setattr(cli, "build_service", build)
    assert cli.main(["JWT handling", "--provider", "ollama", "--model", "selected-model",
                     "--output-dir", str(tmp_path)]) == 0
    assert probes == ["selected-model"]
    output = capsys.readouterr()
    assert output.out.startswith("# Technique bundle")
    assert "unused-model" not in output.err
    records = [
        json.loads(line)
        for line in next((tmp_path / "techniques/logs").glob("*.jsonl")).read_text().splitlines()
    ]
    assert next(record for record in records if record["type"] == "startup")["model_id"] == "selected-model"


@pytest.mark.parametrize("mode", ["success", "unknown", "export_failure"])
def test_explicit_module_exports_and_reports_failures(
    scripted_service, monkeypatch, capsys, tmp_path, mode,
):
    from modules.prompts import factory
    from modules.techniques import module_export

    revision = scripted_service.publish()
    expected = scripted_service.service.store.document(revision).markdown
    scripted_service.selection = revision
    monkeypatch.setattr(cli, "build_service", lambda *args, **kwargs: scripted_service.service)
    loader = SimpleNamespace(resolve_module_directory=lambda _: None if mode == "unknown" else tmp_path)
    monkeypatch.setattr(factory, "get_module_loader", lambda: loader)
    if mode == "export_failure":
        monkeypatch.setattr(module_export, "export_bundle", Mock(side_effect=OSError("Destination read-only")))
    status = cli.main(["JWT handling", "--provider", "ollama", "--module", "fixture",
                       "--phase", "hypothesis_generation", "--output-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert status == (0 if mode == "success" else 1)
    if mode == "unknown":
        assert "Unknown module" in captured.err
        assert captured.out == ""
    else:
        assert captured.out.startswith("# Technique bundle")
    if mode == "success":
        files = list((tmp_path / "techniques").glob("*.md"))
        assert len(files) == 1
        assert files[0].name.startswith("hypothesis-generation--")
        assert files[0].read_text() == expected
    elif mode == "export_failure":
        assert "Destination read-only" in captured.err


@pytest.mark.parametrize("phases", [[], ["hypothesis_generation"]])
def test_cli_and_agent_assemble_equivalent_generation_inputs(
    scripted_service, monkeypatch, capsys, tmp_path, phases,
):
    from modules.techniques.context import LookupContext

    state = scripted_service
    monkeypatch.setattr(state.service.store, "search", lambda *args, **kwargs: [])
    context = LookupContext(
        phase_contexts=phases, methodology_constraints=["Describe negative controls"],
        purpose="PRIVATE_TASK_OBJECTIVE", subjects=[{"value": "PRIVATE_SUBJECT"}],
        constraints=["PRIVATE_OPERATION_CONSTRAINT"], capabilities=["PRIVATE_TOOL"],
    )
    context.observe("PRIVATE_OBSERVATION", "artifact:PRIVATE_REFERENCE", "source", "subject")
    state.service.tool("PRIVATE_OPERATION", "PRIVATE_TASK", context)("JWT handling")
    expected = [(role, prompt) for role, prompt, _ in state.calls]
    state.calls.clear()
    monkeypatch.setattr(cli, "build_service", lambda *args, **kwargs: state.service)
    assert cli.main([
        "JWT handling", "--provider", "ollama", "--output-dir", str(tmp_path),
        *[value for phase in phases for value in ("--phase", phase)],
        "--constraint", "Describe negative controls",
    ]) == 0
    actual = [(role, prompt) for role, prompt, _ in state.calls]
    assert expected == actual and "PRIVATE_" not in str(actual)
    assert "Request compatibility" in capsys.readouterr().out


@pytest.mark.parametrize("tokens,valid", [(5, True), (11, False), (None, False)])
def test_prompt_budget_preserves_required_json_on_continuation(monkeypatch, tokens, valid):
    from modules.techniques.documents import json_payload

    payload = {"nested": [True, None, {"code": "a,b; λ\n  run()"}]}
    agent = SimpleNamespace(model=SimpleNamespace(), _prompt_token_limit=10, messages=[
        {"role": "user", "content": [{"text": json_payload("inputs", payload)}]},
    ])
    frozen = json.dumps(agent.messages, ensure_ascii=False)
    monkeypatch.setattr(roles, "safe_estimate_tokens", lambda _: tokens)
    if valid:
        roles.preserve_prompt_budget(agent)
    else:
        with pytest.raises(ValueError, match="cannot fit required inputs"):
            roles.preserve_prompt_budget(agent)
    assert json.dumps(agent.messages, ensure_ascii=False) == frozen
    assert json.loads(agent.messages[0]["content"][0]["text"])["inputs"] == payload



def test_prompt_budget_allocates_generation_without_rewriting_inputs(monkeypatch):
    from modules.config.models import generation_budget

    agent = SimpleNamespace(model=SimpleNamespace(_cyber_preferred_output_tokens=20), _prompt_token_limit=100)
    monkeypatch.setattr(roles, "safe_estimate_tokens", lambda _: 40)
    request = Mock(return_value="request")
    allocate = Mock(return_value=SimpleNamespace(generation_tokens=20))
    set_tokens = Mock()
    monkeypatch.setattr(generation_budget, "model_generation_request", request)
    monkeypatch.setattr(generation_budget, "allocate_generation", allocate)
    monkeypatch.setattr(generation_budget, "set_generation_tokens", set_tokens)
    roles.preserve_prompt_budget(agent)
    request.assert_called_once_with(agent.model, 40)
    allocate.assert_called_once_with("request")
    set_tokens.assert_called_once_with(agent.model, 20)
