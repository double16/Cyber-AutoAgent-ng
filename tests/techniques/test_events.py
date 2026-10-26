"""Technique milestones describe committed lifecycle state without replacing tool events."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from modules.techniques.debug import TechniqueExports
from modules.techniques.events import TechniqueEvents
from modules.techniques.triggers import RecommendationContext


def test_event_scope_is_thread_local_redacted_bounded_and_best_effort():
    events = TechniqueEvents()
    events.emit("ignored")
    received = []
    events.callback = received.append
    events.private_values = ("private.example",)
    barrier = Barrier(2)

    def send(task):
        with events.scope(task_uid=task):
            barrier.wait()
            with events.scope(activity_id=events.activity_id()):
                events.emit("technique_warning", diagnostic="private.example " + "x" * 2000)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(send, ["http-task", "filesystem-task"]))
    assert {row["task_uid"] for row in received} == {"http-task", "filesystem-task"}
    assert len({row["activity_id"] for row in received}) == 2
    assert all("private.example" not in row["diagnostic"] and len(row["diagnostic"]) == 1000 for row in received)
    events.emit("outside")
    assert "task_uid" not in received[-1]
    events.callback = lambda event: (_ for _ in ()).throw(RuntimeError("disconnected"))
    events.emit("technique_resolved")


def test_generation_reuse_and_committed_feedback_events(scripted_service, tmp_path):
    state = scripted_service
    service = state.service
    service.exports = TechniqueExports(str(tmp_path))
    events = []
    service.events.callback = events.append
    service.tool("operation", "http-task")("JWT handling")
    progress = [row for row in events if row["type"] == "technique_build_progress"]
    assert [row["stage"] for row in progress if row["status"] == "started"] == [
        "research",
        "source_selection",
        "authoring",
        "critique",
    ]
    coverage = next(row for row in progress if row["stage"] == "source_selection" and row["status"] == "completed")
    assert coverage["fetched_count"] == coverage["selected_count"] == 1
    resolved = events[-1]
    assert resolved["outcome"] == "generated"
    assert resolved["task_uid"] == "http-task"
    assert resolved["snapshot_path"].endswith(".produced.md")
    assert resolved["snapshot_path"].startswith("techniques/")
    assert len({row["activity_id"] for row in events}) == 1
    state.selection = resolved["revision_id"]
    service.tool("operation", "network-task")("JWT handling")
    assert events[-1]["outcome"] == "reused"
    assert events[-1]["activity_id"] != resolved["activity_id"]
    state.score = -1
    for task in ["http-task", "network-task", "filesystem-task"]:
        service.tool("operation", task)("JWT handling")
        service.evaluate("operation", task, "Task observations")
        service.evaluate("operation", task, "Continuation")
    evaluations = [row for row in events if row["type"] == "technique_evaluated"]
    assert len(evaluations) == 3
    assert all(row["score"] == -1 and row["snapshot_path"].endswith(".md") for row in evaluations)
    assert len([row for row in events if row["type"] == "technique_deactivated"]) == 1
    assert not any(row["type"] in {"tool_start", "tool_end", "memory_added", "error"} for row in events)
    assert not any("document" in row or "request" in row for row in events)


def test_revision_and_research_failure_keep_existing_error_path(scripted_service):
    state = scripted_service
    events = []
    state.service.events.callback = events.append
    state.approve = False
    with pytest.raises(ValueError, match="3 attempts at critique"):
        state.service.get("JWT handling")
    rejected = [row for row in events if row.get("status") == "revision_requested"]
    assert [row["attempt"] for row in rejected] == [1, 2, 3]
    events.clear()
    state.service.research = lambda request: (_ for _ in ()).throw(ValueError("TLS trust unavailable"))
    with pytest.raises(ValueError, match="TLS trust"):
        state.service.get("JWT handling")
    assert [(row["stage"], row["status"]) for row in events] == [("research", "started")]


def test_snapshot_failure_does_not_hide_committed_evaluation(scripted_service, tmp_path, monkeypatch):
    state = scripted_service
    revision = state.publish()
    state.selection = revision
    state.service.exports = TechniqueExports(str(tmp_path))
    state.service.tool("op", "task")("JWT")
    events = []
    state.service.events.callback = events.append
    original = state.service.exports.write
    monkeypatch.setattr(state.service.exports, "write", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        state.service.evaluate("op", "task", "Outcome")
    assert events[-1]["type"] == "technique_evaluated"
    assert "snapshot_path" not in events[-1]
    monkeypatch.setattr(state.service.exports, "write", original)
    state.service.evaluate("op", "task", "Outcome")
    assert len(events) == 1
    assert list((tmp_path / "techniques").glob("*.evaluated-*.md"))


def test_produced_snapshot_failure_is_reported(scripted_service, tmp_path, monkeypatch):
    service = scripted_service.service
    service.exports = TechniqueExports(str(tmp_path))
    events = []
    service.events.callback = events.append
    monkeypatch.setattr(service.exports, "write", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    service.get("JWT")
    assert events[-2]["type"] == "technique_warning"
    assert events[-1]["type"] == "technique_resolved"
    assert "snapshot_path" not in events[-1]


def test_module_sync_counts_additions_updates_and_unchanged(scripted_service, markdown, tmp_path):
    service = scripted_service.service
    directory = tmp_path / "techniques"
    directory.mkdir()
    path = directory / "example.md"
    path.write_text(markdown())
    loader = SimpleNamespace(_inheritance_chain=lambda module: [module], _find_module_dir=lambda module: tmp_path)
    events = []
    service.events.callback = events.append
    service.synchronize_module(loader, "web")
    service.synchronize_module(loader, "web")
    path.write_text(markdown(title="Revised guidance"))
    service.synchronize_module(loader, "web")
    assert [row["added"] for row in events] == [1, 0, 0]
    assert [row["unchanged"] for row in events] == [0, 1, 0]
    assert [row["updated"] for row in events] == [0, 0, 1]


def test_recommendations_and_warnings_are_not_replayed(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    service = state.service
    events = []
    service.events.callback = events.append
    adapter = SimpleNamespace(detect=lambda text: ["http.jwt"], sanitize=lambda text: text)
    path = tmp_path / "recommendations.json"
    context = RecommendationContext(service, ["hypothesis_generation"], [adapter], path)
    context.event_context = {"operation_id": "op", "task_uid": "task"}
    context.observe("first", "capture")
    context.observe("second", "capture")
    assert len(events) == 1
    assert events[0]["match_kind"] == "deterministic"
    context.state["limitations"].append("Matching unavailable")
    context.observe("third", "capture")
    assert events[-1]["type"] == "technique_warning"
    restored = RecommendationContext(service, ["hypothesis_generation"], [adapter], path)
    restored.observe("fourth", "capture")
    assert len(events) == 2
    assert "first" not in json.dumps(events)


def test_warning_dedup_preserves_distinct_tasks_and_diagnostics():
    events = TechniqueEvents()
    received = []
    events.callback = received.append
    for task, diagnostic in [("one", "Pending"), ("one", "Pending"), ("two", "Pending"), ("one", "Unavailable")]:
        with events.scope(task_uid=task):
            events.emit("technique_warning", stage="evaluation", diagnostic=diagnostic)
    assert len(received) == 3


def test_failed_produced_snapshot_retries_without_exporting_unrelated_reuse(scripted_service, tmp_path, monkeypatch):
    state = scripted_service
    service = state.service
    service.exports = TechniqueExports(str(tmp_path))
    original = service.exports.write
    monkeypatch.setattr(service.exports, "write", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    revision, _ = service.get("JWT")
    assert revision in service._pending_produced_exports
    state.selection = revision
    monkeypatch.setattr(service.exports, "write", original)
    service.get("JWT")
    assert not service._pending_produced_exports
    assert list((tmp_path / "techniques").glob("*.produced.md"))
    other_root = tmp_path / "different-operation"
    service.exports = TechniqueExports(str(other_root))
    service.get("JWT")
    assert not other_root.exists()


def test_semantic_recommendation_event_omits_observations(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    service = state.service
    context = RecommendationContext(service, ["hypothesis_generation"], [], tmp_path / "task.json")
    events = []
    service.events.callback = events.append
    service.runner = lambda *args: json.dumps(
        {
            "trigger_ids": [context.triggers[0]["trigger_id"]],
            "reason": "PRIVATE OBSERVATION",
        }
    )
    context.observe("PRIVATE OBSERVATION", "capture")
    assert events[0]["match_kind"] == "semantic"
    assert "PRIVATE OBSERVATION" not in json.dumps(events)


def test_python_event_envelope_keeps_task_and_activity_metadata(capsys):
    from modules.handlers.events.emitters import StdoutEventEmitter

    events = TechniqueEvents()
    events.callback = StdoutEventEmitter(operation_id="op").emit
    with events.scope(operation_id="op", task_uid="task", activity_id="build"):
        with events.stage("source_selection") as fields:
            fields.update(fetched_count=3, selected_count=2)
    rows = [
        json.loads(line.removeprefix("__CYBER_EVENT__").removesuffix("__CYBER_EVENT_END__"))
        for line in capsys.readouterr().out.splitlines()
    ]
    assert len(rows) == 2
    assert rows[-1]["fetched_count"] == 3
    assert all(row["task_uid"] == "task" and row["activity_id"] == "build" for row in rows)
    assert len({row["id"] for row in rows}) == 2


def test_observation_failure_and_pending_evaluation_are_bounded_warnings(scripted_service, tmp_path, monkeypatch):
    from modules.techniques.integration import evaluate_task_techniques
    from modules.techniques.triggers import TechniqueObservationHook

    service = scripted_service.service
    scripted_service.publish()
    received = []
    service.events.callback = received.append
    adapter = SimpleNamespace(detect=lambda text: (_ for _ in ()).throw(RuntimeError()), sanitize=lambda text: text)
    context = RecommendationContext(service, ["hypothesis_generation"], [adapter], tmp_path / "hooks.json")
    context.event_context = {"operation_id": "op", "task_uid": "task"}
    hook = TechniqueObservationHook(context)
    event = SimpleNamespace(
        tool_use={"name": "reader", "input": {}, "toolUseId": "call"},
        result={"status": "success", "content": [{"text": "Original outcome"}]},
    )
    hook.after_tool(event)
    hook.after_tool(event)
    assert event.result["content"] == [{"text": "Original outcome"}]
    assert len(received) == 1
    monkeypatch.setattr(service, "evaluate", lambda *args: (_ for _ in ()).throw(ValueError("PRIVATE FAILURE")))
    runtime = SimpleNamespace(technique_service=service, operation_id="op")
    task = SimpleNamespace(task_uid="task", evidence=[], objective="Check controls", status="done")
    evaluate_task_techniques(runtime, task, "Finished", [])
    evaluate_task_techniques(runtime, task, "Finished", [])
    assert len(received) == 2
    assert received[-1]["retry_pending"] is True
    assert "PRIVATE FAILURE" not in json.dumps(received)


def test_atomic_feedback_reports_only_the_committing_transition(scripted_service):
    state = scripted_service
    revision = state.publish()
    repository = state.service.repository
    repository.use("operation", "task", revision)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: repository.feedback("operation", "task", revision, -1, "Unhelpful"), range(2))
        )
    assert sum(result is not None for result in results) == 1
    assert repository.get(revision)["score_count"] == 1


def test_unbound_lookup_leaves_operation_attribution_to_the_operation_handler(scripted_service):
    state = scripted_service
    state.selection = state.publish()
    received = []
    state.service.events.callback = received.append
    state.service.tool()("JWT")
    assert received[-1]["type"] == "technique_resolved"
    assert "operation_id" not in received[-1]
    assert "task_uid" not in received[-1]
