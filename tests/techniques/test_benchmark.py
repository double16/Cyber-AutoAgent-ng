import json
from types import SimpleNamespace

import pytest
from conftest import connection_records

from modules.techniques.benchmark import RetrievalCase, compare, evaluate_workflow, main


def test_comparison_isolated_representations_and_limits(store, markdown):
    cases = [RetrievalCase(request="JWT", phases=["hypothesis_generation"], relevant_lineages=["jwt"])]
    rows = compare(
        [("jwt", markdown())], store.embeddings, {"embedding_dimensions": 3, "embedding_model": "test"}, cases,
    )
    assert [(row["representation"], row["limit"]) for row in rows] == [
        ("full", 10), ("full", 20), ("summary", 10), ("summary", 20),
    ]
    assert all(row["catalog_size"] == 1 and row["retrieval"][0]["recall"] == 1 for row in rows)
    assert store.repository.revisions() == []
    with pytest.raises(ValueError, match="outside the catalog"):
        compare([], store.embeddings, {}, cases)


@pytest.mark.parametrize("generate", [False, True])
def test_workflow_actual_reuse_generation_and_feedback(scripted_service, tmp_path, generate):
    state = scripted_service
    revision = state.publish()
    lineage = state.service.repository.get(revision)["lineage"]
    state.selection = None if generate else revision
    original = state.service.runner
    trigger = state.service.store.triggers(["hypothesis_generation"])[0]["trigger_id"]

    def runner(role, prompt, tools):
        if role == "technique_trigger_matcher":
            return json.dumps({"trigger_ids": [trigger], "reason": "Observed token handling", "support": [
                {"trigger_id": trigger, "subject_ids": ["assigned"],
                 "connections": connection_records(prompt, ["benchmark:source"]),
                 "observation_references": ["benchmark:source"]}]})
        return original(role, prompt, tools)

    state.service.runner = runner
    captured = []
    state.service.events.callback = captured.append
    rows = evaluate_workflow(state.service, [
        RetrievalCase(request="JWT handling", phases=["hypothesis_generation"],
                      relevant_lineages=[lineage], observation="A JWT is observed", subjects=[{"id": "assigned"}],
                      recommendation_lineages=[lineage]),
    ], tmp_path)
    result = rows[0]
    assert result["generated"] is generate
    assert result["unnecessary_generation"] is generate
    assert result["selection_correct"] is (None if generate else True)
    assert result["recommendation_precision"] == result["recommendation_recall"] == 1
    assert result["complete_document_reviews"] == 1
    assert result["justified_catalog_miss"] is generate
    assert result["unresolved_reviews"] == 0
    assert result["subject_relevance"]
    assert result["support_contract_valid"]
    assert result["catalog_rejection_correct"] is (False if generate else None)
    assert result["model_calls"] > 0 and result["elapsed_seconds"] >= 0
    assert captured
    assert state.service.events.callback == captured.append


def test_workflow_empty_labels_and_callback_restored_on_error(scripted_service, tmp_path):
    state = scripted_service
    revision = state.publish()
    state.selection = revision
    rows = evaluate_workflow(state.service, [
        RetrievalCase(request="JWT handling", phases=["hypothesis_generation"], relevant_lineages=[]),
    ], tmp_path)
    assert rows[0]["selection_correct"] is False
    assert rows[0]["recommendation_precision"] is None
    assert rows[0]["recommendation_recall"] is None
    with pytest.raises(ValueError, match="labeled"):
        evaluate_workflow(state.service, [], tmp_path)
    with pytest.raises(ValueError):
        evaluate_workflow(state.service, [
            RetrievalCase(request=" ", phases=["hypothesis_generation"], relevant_lineages=[]),
        ], tmp_path / "failure")
    assert state.service.events.callback is None


def test_workflow_comparison_uses_actual_pipeline(scripted_service, markdown):
    state = scripted_service
    # No existing candidate selected; the scripted author/research path must actually execute.
    cases = [RetrievalCase(request="JWT handling", phases=["hypothesis_generation"], relevant_lineages=[])]
    rows = compare(
        [("jwt", markdown())], state.service.store.embeddings,
        {"embedding_dimensions": 3, "embedding_model": "test"}, cases,
        workflow_runner=state.service.runner, research=state.service.research,
    )
    assert all(row["workflow"][0]["generated"] for row in rows)
    assert all(not row["workflow"][0]["unnecessary_generation"] for row in rows)


@pytest.mark.parametrize("workflow", [False, True])
def test_cli_creates_report_and_refuses_overwrite(
    monkeypatch, tmp_path, markdown, store, scripted_service, workflow,
):
    import dotenv

    import modules.config
    import modules.storage.qdrant
    import modules.techniques.research
    import modules.techniques.roles

    monkeypatch.setattr(dotenv, "load_dotenv", lambda: None)
    manager = SimpleNamespace(
        get_provider=lambda: "ollama",
        get_model_id=lambda provider, model: "test",
        get_qdrant_memory_config=lambda provider: {
            "embedding_dimensions": 3, "embedding_model": "test", "embedding_provider": provider,
        },
    )
    monkeypatch.setattr(modules.techniques.roles, "TechniqueRoleRunner",
                        lambda provider, model: scripted_service.service.runner)
    monkeypatch.setattr(modules.techniques.research, "default_search_tool", lambda: None)
    monkeypatch.setattr(modules.techniques.research, "WebResearch",
                        lambda runner, tool: scripted_service.service.research)
    monkeypatch.setattr(modules.config, "get_config_manager", lambda: manager)
    monkeypatch.setattr(modules.storage.qdrant, "build_embeddings", lambda config: store.embeddings)
    module = tmp_path / "catalog" / "web" / "techniques"
    module.mkdir(parents=True)
    (module / "jwt.md").write_text(markdown())
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{
        "request": "JWT", "phases": ["hypothesis_generation"],
        "relevant_lineages": ["module:web:techniques/jwt.md"],
    }]))
    output = tmp_path / "report.json"
    args = ["--cases", str(cases), "--catalog", str(tmp_path / "catalog"), "--output", str(output)]
    if workflow:
        args.append("--workflow")
    assert main(args) == 0
    report = json.loads(output.read_text())
    assert report["embedding_model"] == "test" and report["workflow"] is workflow
    assert len(report["results"]) == 4
    with pytest.raises(SystemExit):
        main(args)
    with pytest.raises(SystemExit):
        main(["--cases", str(cases), "--catalog", str(tmp_path / "empty"), "--output", str(tmp_path / "new")])


def test_workflow_unresolved_review_is_reported_without_generation(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    original = state.service.runner
    state.service.runner = lambda role, prompt, tools: (
        "invalid" if role == "technique_judge" and "COMPLETE" in prompt else original(role, prompt, tools)
    )
    result = evaluate_workflow(state.service, [RetrievalCase(
        request="JWT handling", phases=["hypothesis_generation"], relevant_lineages=[],
    )], tmp_path)[0]
    assert result["unresolved_reviews"] == 1
    assert result["complete_document_reviews"] == 0
    assert not result["generated"] and not result["justified_catalog_miss"]
