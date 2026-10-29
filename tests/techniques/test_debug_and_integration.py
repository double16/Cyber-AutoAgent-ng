import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from modules.techniques.debug import TechniqueExports
from modules.techniques.integration import bind_task_techniques, evaluate_task_techniques, reconcile_task_techniques


def test_produced_copy_is_immediate_and_evaluated_copy_includes_use_sentiment(scripted_service, tmp_path):
    state = scripted_service
    state.service.exports = TechniqueExports(str(tmp_path / "operation"))
    tool = state.service.tool("operation", "first-task")
    rendered = tool(request="JWT handling")
    revision = state.service.repository.revisions()[0]["revision_id"]
    directory = tmp_path / "operation" / "techniques"
    produced = directory / f"{revision}.produced.md"
    assert produced.read_text() in rendered
    assert not list(directory.glob("*.evaluated-*.md"))
    assert not (tmp_path / "operation" / "artifacts").exists()
    state.score = -1
    state.service.evaluate("operation", "first-task", "Guidance did not address the observed behavior")
    evaluated = list(directory.glob("*.evaluated-*.md"))
    assert len(evaluated) == 1
    metadata = yaml.safe_load(evaluated[0].read_text().split("---")[1])
    assert metadata["use_evaluation"]["score"] == -1
    assert metadata["use_evaluation"]["task_uid"] == "first-task"
    assert metadata["use_evaluation"]["reason"]
    assert metadata["sentiment_count"] == 1
    state.selection = revision
    state.service.tool("operation", "second-task")(request="JWT handling")
    assert len(list(directory.glob("*.evaluated-*.md"))) == 1
    state.service.evaluate("operation", "second-task", "A second use")
    assert len(list(directory.glob("*.evaluated-*.md"))) == 2
    assert produced.read_text() in rendered
    original = evaluated[0].read_text()
    state.service.evaluate("operation", "first-task", "Continuation")
    assert evaluated[0].read_text() == original


def test_reused_module_technique_only_exports_after_evaluation_and_retries_failed_export(
    scripted_service, tmp_path, monkeypatch
):
    state = scripted_service
    revision = state.publish()
    state.selection = revision
    state.service.exports = TechniqueExports(str(tmp_path))
    state.service.tool("op", "task")(request="JWT")
    assert not (tmp_path / "techniques").exists()
    write = state.service.exports.write
    monkeypatch.setattr(state.service.exports, "write", Mock(side_effect=OSError("disk unavailable")))
    with pytest.raises(OSError):
        state.service.evaluate("op", "task", "Evidence of useful guidance")
    assert not state.service.repository.pending_uses("op", "task")
    monkeypatch.setattr(state.service.exports, "write", write)
    state.service.evaluate("op", "task", "Retry export without repeating evaluation")
    assert len(list((tmp_path / "techniques").glob("*.evaluated-*.md"))) == 1
    assert [role for role, _, _ in state.calls].count("technique_evaluator") == 1


def test_debug_export_validates_filename_and_cleans_failed_temporary_write(tmp_path, monkeypatch):
    exporter = TechniqueExports(str(tmp_path))
    with pytest.raises(ValueError):
        exporter.write("../../outside", "guidance")
    original = Path.replace
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("disk")))
    with pytest.raises(OSError):
        exporter.write("00000000-0000-0000-0000-000000000001", "guidance")
    assert list(exporter.directory.iterdir()) == []
    monkeypatch.setattr(Path, "replace", original)
    assert exporter.write("00000000-0000-0000-0000-000000000001", "guidance").read_text() == "guidance"


def test_worker_binding_retains_scoped_tool_and_recommendations(scripted_service, tmp_path, monkeypatch):
    state = scripted_service
    revision = state.publish()
    state.selection = revision
    runtime = SimpleNamespace(technique_service=state.service, config=SimpleNamespace(module="web"), operation_id="op")
    phase = SimpleNamespace(id=1, technique_contexts=(), produces_hypotheses=True)
    task = SimpleNamespace(
        task_uid="task",
        kind="standard",
        phase=1,
        evidence=[],
        objective="Review",
        status="done",
        status_reason="Completed",
        acceptance=SimpleNamespace(basis=SimpleNamespace(snapshot_hash="digest", source_refs=[], item_ids=[])),
    )
    monkeypatch.setattr("modules.tools.memory._operation_output_root", lambda: str(tmp_path))
    tools = bind_task_techniques(runtime, SimpleNamespace(phases=[phase]), task, [state.service.tool()])
    assert len(tools) == 1
    assert tools[0]._cyber_technique_context.contexts == ["hypothesis_generation"]
    tools[0](request="JWT")
    assert len(state.service.repository.pending_uses("op", "task")) == 1
    evaluate_task_techniques(runtime, task, "Useful results", [])
    assert not state.service.repository.pending_uses("op", "task")
    assert bind_task_techniques(SimpleNamespace(), None, None, tools) is tools
    evaluate_task_techniques(SimpleNamespace(), task, "No service", [])
    reconcile_task_techniques(SimpleNamespace(), [])


def test_pending_feedback_survives_failed_evaluator_and_reconciles_on_continuation(
    scripted_service, monkeypatch, caplog
):
    state = scripted_service
    revision = state.publish()
    state.service.repository.use("op", "task", revision)
    runtime = SimpleNamespace(technique_service=state.service, operation_id="op")
    task = SimpleNamespace(
        task_uid="task", status="done", status_reason="Evidence reviewed", objective="Review", evidence=[]
    )
    evaluate = state.service.evaluate
    monkeypatch.setattr(state.service, "evaluate", Mock(side_effect=RuntimeError("model unavailable")))
    evaluate_task_techniques(
        runtime,
        task,
        "Results",
        [SimpleNamespace(tool_name="reader", success=True, output_summary="Fact", artifact_refs=())],
    )
    assert "remains pending" in caplog.text
    assert len(state.service.repository.pending_uses("op", "task")) == 1
    monkeypatch.setattr(state.service, "evaluate", evaluate)
    reconcile_task_techniques(runtime, [task, SimpleNamespace(status="pending")])
    assert not state.service.repository.pending_uses("op", "task")


def test_private_identifiers_cannot_enter_requests_and_feedback_is_redacted(scripted_service):
    state = scripted_service
    state.service.private_values = ("assigned.invalid",)
    with pytest.raises(ValueError, match="operation-specific"):
        state.service.get("Inspect JWT on assigned.invalid")
    revision = state.publish()
    state.service.repository.use("op", "task", revision)
    state.service.runner = lambda *args: json.dumps({
        "methodological_purpose": "Review", "observed_use": "", "helpful_contribution": False,
        "contribution": "", "misleading_or_harmful": False, "harmful_effect": "",
        "reason": "assigned.invalid was unavailable"})
    state.service.evaluate("op", "task", "Context")
    assert "assigned.invalid" not in state.service.store.render(revision)


def test_evaluator_can_read_only_task_evidence_and_continuation_replays_recommendations(
    scripted_service, tmp_path, monkeypatch
):
    from modules.techniques.feedback import ContributionFeedback
    from modules.techniques.integration import recommendation_prompt

    state = scripted_service
    revision = state.publish()
    state.service.repository.use("op", "task", revision)
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("Controlled comparison excluded the suspected weakness")
    monkeypatch.setattr("modules.tools.artifact.resolve_operation_artifact_path", lambda ref: str(evidence))
    runtime = SimpleNamespace(technique_service=state.service, operation_id="op", prompt_token_limit=48_000)
    task = SimpleNamespace(task_uid="task", objective="Compare", status="done", evidence=["artifact:evidence.txt"])
    original = state.service._json
    seen_tools = []

    def evaluate(role, prompt, schema, tools=None):
        assert schema is ContributionFeedback
        assert "artifact:evidence.txt" in prompt
        seen_tools.extend(tools)
        return original(role, prompt, schema, tools)

    monkeypatch.setattr(state.service, "_json", evaluate)
    evaluate_task_techniques(runtime, task, "Comparison evidence available", [])
    assert len(seen_tools) == 1
    selected = SimpleNamespace(
        _cyber_technique_context=SimpleNamespace(
            render=lambda: "\n## Technique lookup recommendations\nConsider JWT guidance",
        )
    )
    prompt = recommendation_prompt("Continue", [selected])
    assert "JWT guidance" in prompt
    assert recommendation_prompt(prompt, [selected]) == prompt
    assert recommendation_prompt("Publish only", []) == "Publish only"


def test_researched_technique_lifecycle_exports_and_regeneration(scripted_service, tmp_path):
    import asyncio

    from modules.techniques.research import WebResearch

    state = scripted_service

    async def search(**kwargs):
        return [{"url": "https://vendor.example/advisory", "title": "Advisory"}]

    original_runner = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_researcher":
            asyncio.run(tools[0](query="Generic JWT guidance"))
            tools[1](url="https://vendor.example/advisory")
            return "Research complete"
        return original_runner(role, prompt, tools)

    state.service.runner = runner
    state.service.research = WebResearch(
        runner, search, fetch=lambda url: {"url": url, "text": "Primary source", "retrieved_at": "2026-10-26"}
    )
    state.service.exports = TechniqueExports(str(tmp_path / "operation"))
    state.service.tool("operation", "first")(request="JWT handling")
    revision = state.service.repository.revisions()[0]["revision_id"]
    directory = tmp_path / "operation" / "techniques"
    assert (directory / f"{revision}.produced.md").exists()
    state.selection = revision
    state.score = -1
    for task in ["first", "second", "third"]:
        if task != "first":
            state.service.tool("operation", task)(request="JWT handling")
        state.service.evaluate("operation", task, "Guidance was not applicable")
    assert len(list(directory.glob("*.evaluated-*.md"))) == 3
    assert not state.service.repository.revisions()[0]["active"]
    state.selection = None

    def rebuilding_runner(role, prompt, tools):
        if role == "technique_judge" and "inactive technique address" in prompt:
            return json.dumps({"selected_id": revision, "reason": "Same method"})
        output = runner(role, prompt, tools)
        if role == "technique_author":
            payload = json.loads(output)
            payload["body"] += "\nRecheck applicability before interpreting token validation controls.\n"
            output = json.dumps(payload)
        return output

    state.service.runner = rebuilding_runner
    state.service.tool("operation", "regenerated")(request="JWT handling")
    assert any("negative" in prompt.lower() for role, prompt, _ in state.calls if role == "technique_author")
    assert any(row["active"] for row in state.service.repository.revisions())


def test_rejected_diagnostics_are_isolated_atomic_and_cleanup_on_failure(tmp_path, monkeypatch):
    exporter = TechniqueExports(str(tmp_path))
    first = "00000000-0000-0000-0000-000000000001"
    second = "00000000-0000-0000-0000-000000000002"
    one = exporter.write_rejected(first, "one", attempt=1, stage="author_json", reason="Invalid JSON")
    two = exporter.write_rejected(second, "two", attempt=1, stage="critique", reason="Missing controls")
    assert one != two
    assert json.loads(one.read_text())["response"] == "one"
    assert json.loads(two.read_text())["response"] == "two"
    with pytest.raises(ValueError):
        exporter.write_rejected("../../escape", "", attempt=1, stage="schema", reason="bad")
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("disk unavailable")))
    with pytest.raises(OSError):
        exporter.write_rejected(first, "failed", attempt=2, stage="schema", reason="bad")
    assert list(one.parent.iterdir()) == [one]


def test_diagnostic_export_failure_does_not_block_repair(scripted_service, tmp_path, monkeypatch):
    state = scripted_service
    state.service.exports = TechniqueExports(str(tmp_path))
    monkeypatch.setattr(state.service.exports, "write_rejected", Mock(side_effect=OSError("disk unavailable")))
    runner = state.service.runner
    attempts = []
    events = []
    state.service.events.callback = events.append

    def run(role, prompt, tools):
        if role == "technique_author":
            attempts.append(prompt)
            if len(attempts) < 3:
                return "invalid"
        return runner(role, prompt, tools)

    state.service.runner = run
    state.service.get("JWT")
    assert len(state.service.repository.revisions()) == 1
    assert len([event for event in events if event["type"] == "technique_warning"]) == 1
    assert events[-1]["type"] == "technique_resolved"
    assert not any("disk unavailable" in str(event) for event in events)
