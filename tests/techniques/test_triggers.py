import base64
import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from modules.operation_plugins.web.technique_signals import detect, sanitize
from modules.techniques.triggers import RecommendationContext, TechniqueObservationHook, phase_contexts, signal_adapters


def token(header=None, payload=None):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{encode(header or {'alg': 'none'})}.{encode(payload or {'sub': 'synthetic'})}."


@pytest.mark.parametrize(
    "container",
    [
        "Authorization: Bearer {}",
        "Cookie: session={}",
        "Set-Cookie: session={}",
        "https://target.invalid/path?auth={}",
        '{{"deep": {{"token": "{}"}}}}',
        "<input value='{}'>",
    ],
)
def test_jwt_detected_anywhere_in_captured_request_response(container):
    text = container.format(token())
    assert detect(text) == ["http.jwt"]
    assert token() not in sanitize(text)


def test_encoded_jwe_invalid_json_and_lookalikes():
    assert detect(quote(token(), safe="")) == ["http.jwt"]
    header = base64.urlsafe_b64encode(b'{"alg":"dir","enc":"A256GCM"}').decode().rstrip("=")
    assert detect(f"{header}..a.b.c") == ["http.jwt"]
    for text in (
        "a.b.c",
        "foo.bar.baz.more",
        "!!!.!!!.!!!",
        token({"typ": "JWT"}),
        "W10.e30.",
        "eyJhbGciOiJub25lIn0.bm90anNvbg.",
        f"{header}.a.b.c.d.e",
    ):
        assert detect(text) == []


def test_phase_matching_uses_structured_metadata_only():
    phase = SimpleNamespace(technique_contexts=("analysis",), produces_hypotheses=True)
    assert phase_contexts(phase, SimpleNamespace(kind="finding_validation")) == [
        "analysis",
        "hypothesis_generation",
        "vulnerability_verification",
    ]


def test_deterministic_recommendation_persists_and_never_exposes_token(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    from modules.operation_plugins.web import technique_signals

    path = tmp_path / "recommendations.json"
    context = RecommendationContext(state.service, ["hypothesis_generation"], [technique_signals], path)
    text = context.observe("Response cookie: " + token(), "artifact:response.txt")
    assert "JWT handling" in text
    assert "Lookup is your decision" in text
    assert token() not in text
    count = len(state.calls)
    context.observe("Response cookie: " + token(), "artifact:response.txt")
    assert len(state.calls) == count
    resumed = RecommendationContext(state.service, ["hypothesis_generation"], [technique_signals], path)
    assert resumed.render() == text
    assert not state.research_calls
    assert not state.service.repository.pending_uses("op", "task")


def test_semantic_matcher_can_match_network_and_source_observations(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    context = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "semantic.json")
    trigger = context.triggers[0]["trigger_id"]
    state.service.runner = lambda *args: json.dumps({"trigger_ids": [trigger], "reason": "Observed matching behavior"})
    for content in (
        "TCP service presents application authentication metadata",
        "Source passes token into signature verifier",
    ):
        assert "JWT handling" in context.observe(content, "artifact:observation.txt")
    state.service.runner = lambda *args: json.dumps({"trigger_ids": ["invented"], "reason": "Invalid"})
    assert "matcher unavailable or invalid" in context.observe("Different observation", "artifact:different.txt")


def test_phase_filter_and_semantic_allowance(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    unrelated = RecommendationContext(state.service, ["reconnaissance"], [], tmp_path / "other.json")
    assert unrelated.observe(token(), "source") == ""
    context = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "bounded.json")
    for number in range(8):
        context.observe(f"Observation {number}", "source")
    assert context.state["semantic_calls"] == 6
    assert "allowance exhausted" in context.render()


def test_capture_hook_appends_new_recommendations_and_skips_guidance(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    from modules.operation_plugins.web import technique_signals

    context = RecommendationContext(
        state.service, ["hypothesis_generation"], [technique_signals], tmp_path / "hooks.json"
    )
    hook = TechniqueObservationHook(context)
    registry = SimpleNamespace(add_callback=lambda *args, **kwargs: None)
    hook.register_hooks(registry)
    result = {"status": "success", "content": [{"text": "Set-Cookie: " + token()}]}
    event = SimpleNamespace(tool_use={"name": "http_request", "toolUseId": "call", "input": {}}, result=result)
    hook.after_tool(event)
    assert len(result["content"]) == 2
    assert "recommended_lookups" in result["content"][1]["text"]
    hook.after_tool(event)
    event.tool_use["name"] = "get_technique"
    before = len(context.state["seen"])
    hook.after_tool(event)
    assert len(context.state["seen"]) == before


def test_adapter_discovery_and_unavailable_capture(scripted_service, tmp_path, monkeypatch):
    from modules.prompts.factory import get_module_loader

    adapters = signal_adapters(get_module_loader(), "ctf")
    assert adapters[0].detect(token()) == ["http.jwt"]
    assert (
        signal_adapters(
            SimpleNamespace(_inheritance_chain=lambda name: [name], _find_module_dir=lambda name: None), "missing"
        )
        == []
    )
    scripted_service.publish()
    context = RecommendationContext(
        scripted_service.service, ["hypothesis_generation"], adapters, tmp_path / "missing.json"
    )
    monkeypatch.setattr("modules.tools.artifact.resolve_operation_artifact_path", lambda ref: str(tmp_path / "missing"))
    context.inspect("artifact:missing")
    assert "Capture unavailable" in context.render()


def test_seed_is_scoped_to_assigned_and_same_target_linked_inventory(scripted_service, tmp_path, monkeypatch):
    from modules.operation_plugins.web import technique_signals

    scripted_service.publish()
    context = RecommendationContext(
        scripted_service.service,
        ["hypothesis_generation"],
        [technique_signals],
        tmp_path / "seed.json",
    )
    manifest = {
        "items": [
            {"id": "assigned", "kind": "endpoint", "target_id": "target-1", "attributes": {}},
            {
                "id": "linked",
                "kind": "parameter",
                "target_id": "target-1",
                "value": token(),
                "attributes": {"endpoint_id": "assigned"},
            },
            {
                "id": "other",
                "kind": "parameter",
                "target_id": "target-2",
                "value": "not assigned",
                "attributes": {"endpoint_id": "assigned"},
            },
        ]
    }
    monkeypatch.setattr("modules.tools.memory._load_inventory_manifest", lambda *args, **kwargs: (manifest, "digest"))
    task = SimpleNamespace(
        evidence=[],
        acceptance=SimpleNamespace(
            basis=SimpleNamespace(
                source_refs=["artifact:inventory.json"],
                item_ids=["assigned"],
                snapshot_hash="digest",
            )
        ),
    )
    context.seed(task)
    assert "JWT handling" in context.render()
    task.acceptance.basis.snapshot_hash = "changed"
    context.seed(task)
    assert "Assigned inventory unavailable" in context.render()


def test_referenced_capture_can_trigger_after_large_inline_result_is_offloaded(scripted_service, tmp_path, monkeypatch):
    from modules.operation_plugins.web import technique_signals

    scripted_service.publish()
    capture = tmp_path / "response.txt"
    capture.write_text("Response: " + token())
    monkeypatch.setattr("modules.tools.artifact.resolve_operation_artifact_path", lambda ref: str(capture))
    context = RecommendationContext(
        scripted_service.service,
        ["hypothesis_generation"],
        [technique_signals],
        tmp_path / "capture.json",
    )
    event = SimpleNamespace(
        tool_use={"name": "new_runtime_capture_tool", "toolUseId": "capture", "input": {}},
        result={"content": [{"text": "Full response: artifact:response.txt"}]},
    )
    TechniqueObservationHook(context).after_tool(event)
    assert "JWT handling" in context.render()
    assert token() not in json.dumps(context.state)
    capture.write_bytes(b"\xff\xfe")
    context.inspect("artifact:response.txt")
    assert "Capture unavailable" in context.render()


def test_passive_hook_failure_never_changes_successful_tool_result(scripted_service, tmp_path, monkeypatch):
    scripted_service.publish()
    context = RecommendationContext(scripted_service.service, ["hypothesis_generation"], [], tmp_path / "passive.json")
    monkeypatch.setattr(context, "observe", lambda *args: (_ for _ in ()).throw(OSError("disk")))
    result = {"status": "success", "content": [{"text": "Successful target response"}]}
    hook = TechniqueObservationHook(context)
    hook.after_tool(SimpleNamespace(tool_use={"name": "capture", "input": {}}, result=result))
    assert result == {"status": "success", "content": [{"text": "Successful target response"}]}


def test_production_sdk_hooks_detect_jwt_and_preserve_original_outcome(scripted_service, tmp_path, monkeypatch):
    from modules.operation_plugins.web import technique_signals
    from tests.fixtures.outcome_agent import complete_tool_call, create_outcome_agent

    scripted_service.publish()
    context = RecommendationContext(
        scripted_service.service,
        ["hypothesis_generation"],
        [technique_signals],
        tmp_path / "sdk.json",
    )
    selected = scripted_service.service.tool("op", "task")
    selected._cyber_technique_context = context
    agent = create_outcome_agent(monkeypatch, "task_executor", [selected])
    event = complete_tool_call(agent, "future_capture_tool", {}, "Set-Cookie: " + token())
    assert "recommended_lookups" in event.result["content"][-1]["text"]
    original = agent._cyber_callback_handler.tool_outcome_journal.entries()[0]
    assert original.success
    assert "Set-Cookie:" in original.output_summary


def test_compact_tokens_are_blocked_before_research_or_embedding(scripted_service):
    from modules.tools.web_search import contains_sensitive_web_search_data

    assert contains_sensitive_web_search_data(token())
    with pytest.raises(ValueError, match="without secrets"):
        scripted_service.service.get("Review validation of " + token())
    assert not scripted_service.calls


@pytest.mark.parametrize("positive", [True, False])
def test_semantic_cache_shared_across_tasks_and_continuation(scripted_service, tmp_path, positive):
    state = scripted_service
    state.publish()
    first = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "first.json")
    selected = [first.triggers[0]["trigger_id"]] if positive else []
    calls = []

    def runner(*args):
        calls.append(args)
        return json.dumps({"trigger_ids": selected, "reason": "PRIVATE observation reason"})

    state.service.runner = runner
    first.observe("Equivalent evidence", "artifact:first")
    second = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "second.json")
    second.observe("Equivalent evidence", "artifact:second")
    assert len(calls) == 1
    assert second.state["semantic_calls"] == 0
    assert bool(second.state["recommendations"]) is positive
    if positive:
        assert second.state["recommendations"][0]["source"] == "artifact:second"
    resumed = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "second.json")
    resumed.observe("Equivalent evidence", "artifact:third")
    assert len(calls) == 1
    cached = next((tmp_path / "semantic_cache").glob("*.json")).read_text()
    assert "Equivalent evidence" not in cached
    assert "PRIVATE" not in cached


def test_semantic_cache_isolates_phase_catalog_excerpt_and_operation(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    contexts = ["hypothesis_generation"]
    first = RecommendationContext(state.service, contexts, [], tmp_path / "op1" / "first.json")
    first.observe("Evidence", "source")
    other_phase = RecommendationContext(
        state.service, [*contexts, "analysis"], [], tmp_path / "op1" / "phase.json"
    )
    other_phase.observe("Evidence", "source")
    other_catalog = RecommendationContext(state.service, contexts, [], tmp_path / "op1" / "catalog.json")
    other_catalog.triggers = [{**first.triggers[0], "when": "Changed condition"}]
    other_catalog.observe("Evidence", "source")
    other_operation = RecommendationContext(state.service, contexts, [], tmp_path / "op2" / "first.json")
    other_operation.observe("Evidence", "source")
    first.observe("Different evidence", "source")
    assert len([role for role, _, _ in state.calls if role == "technique_trigger_matcher"]) == 5


def test_failed_semantic_match_can_retry_same_observation(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    context = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "retry.json")
    runner = state.service.runner
    state.service.runner = lambda *args: "invalid"
    context.observe("Same evidence", "source")
    assert context.state["semantic_calls"] == 1
    assert not list((tmp_path / "semantic_cache").glob("*.json"))
    state.service.runner = runner
    context.observe("Same evidence", "source")
    assert context.state["semantic_calls"] == 2
    assert list((tmp_path / "semantic_cache").glob("*.json"))


def test_inputs_empty_outputs_and_exhaustion_preserve_deterministic_detection(scripted_service, tmp_path):
    from modules.operation_plugins.web import technique_signals

    state = scripted_service
    state.publish()
    context = RecommendationContext(
        state.service, ["hypothesis_generation"], [technique_signals], tmp_path / "inputs.json"
    )
    context.observe("Some arguments", "tool_input:1")
    context.observe("  ", "tool_output:1")
    assert context.state["semantic_calls"] == 0
    for number in range(7):
        context.observe(f"Evidence {number}", "tool_output:2")
    assert context.state["semantic_calls"] == 6
    assert "JWT handling" in context.observe(token(), "tool_input:jwt")


def test_bad_cache_is_replaced_and_cached_decisions_work_after_exhaustion(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    first = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "first.json")
    first.observe("Shared evidence", "source")
    path = next((tmp_path / "semantic_cache").glob("*.json"))
    path.write_text(json.dumps({"trigger_ids": ["invented"], "reason": "bad cache"}))
    second = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "second.json")
    second.observe("Shared evidence", "source")
    assert second.state["semantic_calls"] == 1
    third = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "third.json")
    third.state["semantic_calls"] = 6
    third.observe("Shared evidence", "source")
    assert not third.state["limitations"]
    assert third.state["semantic_calls"] == 6


def test_concurrent_task_bindings_share_one_semantic_decision(scripted_service, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    state = scripted_service
    state.publish()
    contexts = [
        RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / f"task-{number}.json")
        for number in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda context: context.observe("Shared observation", "source"), contexts))
    assert len(results) == 2
    assert sum(context.state["semantic_calls"] for context in contexts) == 1


def test_cache_identity_uses_only_the_sanitized_submitted_excerpt(scripted_service, tmp_path):
    state = scripted_service
    state.publish()
    adapter = SimpleNamespace(detect=lambda text: [], sanitize=lambda text: text.replace("private detail", "MASK"))
    first = RecommendationContext(state.service, ["hypothesis_generation"], [adapter], tmp_path / "first.json")
    second = RecommendationContext(state.service, ["hypothesis_generation"], [adapter], tmp_path / "second.json")
    first.observe("private detail" + "a" * 12_000 + "tail one", "source")
    second.observe("MASK" + "a" * 12_000 + "tail two", "source")
    assert first.state["semantic_calls"] == 1
    assert second.state["semantic_calls"] == 0


def test_added_trigger_reuses_old_decisions_and_preserves_task_allowance(scripted_service, markdown, tmp_path):
    from modules.techniques.documents import TechniqueDocument

    state = scripted_service
    state.publish()
    path = tmp_path / "first.json"
    first = RecommendationContext(state.service, ["hypothesis_generation"], [], path)
    old = first.triggers[0]["trigger_id"]
    calls = []

    def runner(_role, prompt, _tools):
        calls.append(prompt)
        trigger = old if len(calls) == 1 else new
        return json.dumps({"trigger_ids": [trigger], "reason": "Observed conditions"})

    state.service.runner = runner
    first.observe("Shared evidence", "source:first")
    # A newly published technique changes the catalog without invalidating the old trigger.
    state.service.store.publish(TechniqueDocument(markdown(title="New method")), "module:new", "module:source")
    second = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "second.json")
    new = next(row["trigger_id"] for row in second.triggers if row["trigger_id"] != old)
    second.observe("Shared evidence", "source:second")
    assert len(calls) == 2
    assert old not in calls[1] and new in calls[1]
    assert {row["trigger_id"] for row in second.state["recommendations"]} == {old, new}
    assert all(row["source"] == "source:second" for row in second.state["recommendations"])
    # Resuming the first task retains its allowance and recommendations across the catalog change.
    first.state["semantic_calls"] = 6
    first._save()
    resumed = RecommendationContext(state.service, ["hypothesis_generation"], [], path)
    assert resumed.state["semantic_calls"] == 6
    assert resumed.state["recommendations"][0]["trigger_id"] == old
    resumed.observe("Shared evidence", "source:resumed")
    assert len(calls) == 2  # cached new-trigger decisions also remain usable after exhaustion
    assert RecommendationContext._fingerprint(first.triggers[0]) == RecommendationContext._fingerprint({
        **first.triggers[0], "active": False,
    })


def test_generated_tool_output_and_its_artifacts_do_not_trigger(scripted_service, tmp_path, monkeypatch):
    state = scripted_service
    state.publish()
    context = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "origin.json")
    context.tool_origins["publish_analysis"] = "analysis"
    hook = TechniqueObservationHook(context)
    generated = {"status": "success", "content": [{"text": "artifact:generated.json"}]}
    hook.after_tool(SimpleNamespace(
        tool_use={"name": "publish_analysis", "toolUseId": "write", "input": {}}, result=generated,
    ))
    assert context.artifact_origins == {"artifact:generated.json": "analysis"}
    resumed = RecommendationContext(state.service, ["hypothesis_generation"], [], tmp_path / "resumed.json")
    assert resumed.artifact_origins == context.artifact_origins
    TechniqueObservationHook(resumed).after_tool(SimpleNamespace(
        tool_use={"name": "future_reader", "toolUseId": "read-input", "input": {"path": "artifact:generated.json"}},
        result={"status": "success", "content": [{"text": "Generated hypotheses without a reference header"}]},
    ))
    assert not resumed.lookup.observations
    hook.after_tool(SimpleNamespace(
        tool_use={"name": "future_reader", "toolUseId": "read", "input": {}}, result=generated,
    ))
    path = tmp_path / "source.json"
    path.write_text("Generated hypothesis text")
    monkeypatch.setattr("modules.tools.artifact.resolve_operation_artifact_path", lambda _: str(path))
    context.inspect("artifact:generated.json")
    assert not context.state["recommendations"]
    assert not context.lookup.observations
    assert not state.calls


def test_procedure_source_seed_works_without_inventory_or_adapters(scripted_service, tmp_path, monkeypatch):
    state = scripted_service
    state.publish()
    path = tmp_path / "source.txt"
    path.write_text("Configuration declares a service dependency")
    monkeypatch.setattr("modules.tools.artifact.resolve_operation_artifact_path", lambda _: str(path))
    context = RecommendationContext(state.service, ["analysis"], [], tmp_path / "procedure.json")
    task = SimpleNamespace(evidence=[], acceptance=SimpleNamespace(basis=SimpleNamespace(
        source_refs=["target:service", "artifact:source.txt"], item_ids=[], snapshot_hash="",
    )))
    context.seed(task)
    assert context.lookup.observations[0].origin == "source"
    assert context.lookup.observations[0].scope == "subject"
    assert not context.state["limitations"]


def test_runtime_tool_declarations_are_read_from_actual_sdk_registry(scripted_service, tmp_path, monkeypatch):
    from strands import tool
    from strands.tools.registry import ToolRegistry

    from tests.fixtures.outcome_agent import complete_tool_call, create_outcome_agent

    @tool
    def late_source() -> str:
        """Read an assigned source file."""
        return "Source data"

    late_source._cyber_technique_observation_origin = "source"
    state = scripted_service
    context = RecommendationContext(state.service, ["analysis"], [], tmp_path / "late.json")
    lookup = state.service.tool("op", "task", context.lookup)
    lookup._cyber_technique_context = context
    agent = create_outcome_agent(monkeypatch, "task_executor", [lookup])
    agent.tool_registry = ToolRegistry()
    agent.tool_registry.register_tool(lookup)
    agent.tool_registry.register_tool(late_source)
    complete_tool_call(agent, "late_source", {}, "Source code uses a bound parameter")
    assert context.lookup.observations[-1].origin == "source"
    assert "late_source" in context.lookup.capabilities
    late_source._cyber_technique_observation_origin = "analysis"
    before = list(context.lookup.observations)
    complete_tool_call(agent, "late_source", {}, "Generated analysis")
    assert context.lookup.observations == before
