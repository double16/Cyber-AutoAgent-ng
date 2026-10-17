"""Protocol-neutral hypothesis publication, binding, acceptance, and bounded reads."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from modules.tools import artifact, hypotheses, memory
from modules.tools.memory import (
    AcceptanceBasis,
    AcceptanceContract,
    AcceptanceCriterion,
    AcceptanceResult,
    EvidenceRequirement,
    Task,
)


@pytest.fixture
def producer(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_id", lambda: "OP_TEST")
    (tmp_path / "inventory.json").write_text("{}")
    (tmp_path / "observation.txt").write_text("Observed an input accepted by the assigned subject")
    return Task(
        "producer", "Develop hypotheses", "Develop safe tests", phase=1, status="active",
        acceptance=AcceptanceContract(
            mode="outcome", basis=AcceptanceBasis(
                kind="snapshot", description="Frozen subjects", source_refs=["artifact:inventory.json"],
                snapshot_hash="frozen-digest", item_ids=["subject"],
            ), criteria=[AcceptanceCriterion(
                id="hypotheses", description="Publish future tests",
                evidence_requirements=[EvidenceRequirement(kind="hypothesis_set")],
            )],
        ),
    )


def hypothesis(action="Supply an input"):
    return {
        "observed_basis": "Assigned subject accepts user input", "preconditions": ["Authorized isolated target"],
        "suspected_mechanism": "Input may reach an interpreter", "attacker_controlled_input": action,
        "safe_test_steps": ["Compare a harmless probe with a control"],
        "positive_expectations": ["Probe produces the specified behavioral difference"],
        "negative_expectations": ["Control remains unchanged"], "required_evidence": ["Saved probe and control"],
        "supporting_refs": ["artifact:observation.txt"], "research_refs": ["https://example.test/advisory"],
    }


def publish(producer, tmp_path, entries=None):
    def persist(binding):
        producer.recovery_context.setdefault("hypothesis_publications", []).append(binding)

    tool = hypotheses.build_record_hypotheses_tool(producer, "OP_TEST", str(tmp_path), persist)
    result = json.loads(tool(subjects=entries or [{
        "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [hypothesis()],
    }]))
    return result, tool


@pytest.mark.parametrize("action", [
    "Supply an HTTP query parameter", "Send a protocol frame to tcp://service:9000",
    "Analyze an input read from /workspace/source/config.py",
])
def test_publish_and_read_protocol_neutral(producer, tmp_path, action):
    binding, tool = publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "supplied", "hypotheses": [hypothesis(action)],
    }])
    payload = hypotheses.load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")
    assert payload.subjects[0].hypotheses[0].attacker_controlled_input == action
    assert payload.producer_task_uid == producer.task_uid
    assert payload.subjects[0].hypotheses[0].hypothesis_id
    repeated = json.loads(tool(subjects=[payload.subjects[0].model_dump()]))
    assert repeated["sha256"] == binding["sha256"]
    read = hypotheses.build_read_hypotheses_tool([(binding["artifact_ref"], producer)], "OP_TEST", {"subject"})
    result = json.loads(read(item_id="subject"))
    assert len(result["hypotheses"]) == 1
    assert result["next_offset"] is None
    assert result["dispositions"][0]["producer_task_uid"] == "producer"


def test_no_test_disposition_and_runtime_schema(producer, tmp_path):
    entry = {"item_id": "subject", "disposition": "no_testable_hypotheses", "reason": "No safe probe"}
    binding, tool = publish(producer, tmp_path, [entry])
    schema = tool.tool_spec["inputSchema"]["json"]
    Draft202012Validator(schema).validate({"subjects": [entry]})
    assert schema["properties"]["subjects"]["items"]["properties"]["disposition"]["enum"] == [
        "hypotheses_supplied", "no_testable_hypotheses",
    ]
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate({"subjects": [{**entry, "disposition": "unknown"}]})
    read = hypotheses.build_read_hypotheses_tool([(binding["artifact_ref"], producer)], "OP_TEST", {"subject"})
    result = json.loads(read(item_id="subject"))
    assert result["hypotheses"] == []
    assert result["dispositions"][0]["reason"] == "No safe probe"


@pytest.mark.parametrize("entry", [
    {"item_id": "subject", "disposition": "hypotheses_supplied"},
    {"item_id": "subject", "disposition": "no_testable_hypotheses", "reason": " "},
    {"item_id": "subject", "disposition": "unknown", "reason": "No safe probe"},
    {"item_id": "unassigned", "disposition": "none", "reason": "No safe probe"},
    {"item_id": "subject", "disposition": "none", "reason": "No safe probe", "hypotheses": [hypothesis()]},
])
def test_invalid_disposition_rejected(producer, tmp_path, entry):
    with pytest.raises(ValueError):
        publish(producer, tmp_path, [entry])
    assert not producer.recovery_context


def test_duplicate_and_missing_assignment_rejected(producer, tmp_path):
    entry = {"item_id": "subject", "disposition": "supplied", "hypotheses": [hypothesis(), hypothesis()]}
    with pytest.raises(ValueError, match="duplicate hypothesis"):
        publish(producer, tmp_path, [entry])
    entry["hypotheses"] = [hypothesis()]
    with pytest.raises(ValueError, match="exactly one"):
        publish(producer, tmp_path, [entry, entry])
    with pytest.raises(ValueError, match="frozen inventory"):
        publish(replace(producer, acceptance=replace(producer.acceptance, basis=replace(
            producer.acceptance.basis, snapshot_hash="",
        ), manifest_hash="")), tmp_path)


@pytest.mark.parametrize("change", ["unbound", "digest", "operation", "producer", "snapshot", "ids", "id_missing"])
def test_corrupt_or_foreign_binding_rejected(producer, tmp_path, change):
    binding, _tool = publish(producer, tmp_path)
    path = Path(artifact.resolve_operation_artifact_path(binding["artifact_ref"]))
    payload = json.loads(path.read_text())
    if change == "unbound":
        producer.recovery_context.clear()
    elif change == "digest":
        path.write_text("changed")
    else:
        if change == "operation":
            payload["operation_id"] = "OP_PRIOR"
        elif change == "producer":
            payload["producer_task_uid"] = "foreign"
        elif change == "snapshot":
            payload["snapshot_hash"] = "different"
        elif change == "ids":
            payload["subjects"][0]["item_id"] = "foreign"
        else:
            payload["subjects"][0]["hypotheses"][0]["hypothesis_id"] = ""
        path.write_text(json.dumps(payload))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        producer.recovery_context["hypothesis_publications"][-1]["sha256"] = digest
    with pytest.raises(ValueError):
        hypotheses.load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")


def test_typed_acceptance_rejects_inventory_and_accepts_immutable_copy(producer, tmp_path):
    criterion = producer.acceptance.criteria[0]
    result = AcceptanceResult("hypotheses", "satisfied", "observation", "Future tests", ("artifact:inventory.json",))
    with pytest.raises(ValueError, match="record_hypotheses"):
        memory._validate_acceptance_result_evidence(producer, criterion, result)
    binding, _tool = publish(producer, tmp_path)
    with pytest.raises(ValueError, match="Include the published hypothesis_set artifact reference") as error:
        memory._validate_acceptance_result_evidence(producer, criterion, result)
    assert "in evidence_refs" in str(error.value)
    assert "If already published, reuse its returned artifact reference" in str(error.value)
    result = replace(result, evidence_refs=(binding["artifact_ref"],))
    memory._validate_acceptance_result_evidence(producer, criterion, result)
    copied = memory._snapshot_task_acceptance_artifacts(producer, [result])[0]
    memory._validate_acceptance_result_evidence(producer, criterion, copied)
    hypotheses.load_hypothesis_set(copied.evidence_refs[0], producer, "OP_TEST")
    (tmp_path / "copy.json").write_text("{}")
    with pytest.raises(ValueError, match="digest changed"):
        hypotheses.load_hypothesis_set("artifact:copy.json", producer, "OP_TEST")


@pytest.mark.parametrize("item_id,offset,count", [("foreign", 0, 5), ("subject", -1, 5), ("subject", 0, 0)])
def test_reader_limits(producer, tmp_path, item_id, offset, count):
    binding, _tool = publish(producer, tmp_path)
    read = hypotheses.build_read_hypotheses_tool([(binding["artifact_ref"], producer)], "OP_TEST", {"subject"})
    with pytest.raises(ValueError, match="assigned item_id"):
        read(item_id=item_id, offset=offset, max_records=count)


def test_reader_pagination_missing_subject_and_size_limit(producer, tmp_path):
    binding, _tool = publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "supplied", "hypotheses": [hypothesis("first"), hypothesis("second")],
    }])
    read = hypotheses.build_read_hypotheses_tool([(binding["artifact_ref"], producer)], "OP_TEST", {"subject", "missing"})
    assert json.loads(read(item_id="subject", max_records=1))["next_offset"] == 1
    assert json.loads(read(item_id="subject", offset=1))["next_offset"] is None
    with pytest.raises(ValueError, match="no validated"):
        read(item_id="missing")
    binding, _tool = publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "supplied", "hypotheses": [hypothesis("x" * 20_000)],
    }])
    read = hypotheses.build_read_hypotheses_tool([(binding["artifact_ref"], producer)], "OP_TEST", {"subject"})
    with pytest.raises(ValueError, match="20000"):
        read(item_id="subject")


def test_contract_idempotence(producer):
    first = hypotheses.hypothesis_contract(producer)
    assert hypotheses.hypothesis_contract(first).acceptance == first.acceptance
    legacy = replace(producer, acceptance=replace(producer.acceptance, criteria=(replace(
        producer.acceptance.criteria[0], evidence_requirements=(EvidenceRequirement(kind="artifact"),),
    ),), manifest_hash=""))
    updated = hypotheses.hypothesis_contract(legacy)
    assert updated.acceptance.manifest_hash != legacy.acceptance.manifest_hash
    assert [item.kind for item in updated.acceptance.criteria[0].evidence_requirements] == ["artifact", "hypothesis_set"]


@pytest.mark.parametrize("shape", ["native", "wrapped", "double_wrapped", "nested", "invalid_json", "wrong_type",
                                   "unknown_field", "unknown_disposition", "root_wrapped", "missing_closer"])
def test_hypothesis_arguments_normalize_before_sdk_validation(producer, tmp_path, shape):
    import asyncio
    import importlib
    from copy import deepcopy
    from unittest.mock import Mock

    from strands import Agent
    from strands.hooks import BeforeToolCallEvent, HookProvider
    from strands.tools.executors import SequentialToolExecutor

    importlib.import_module("cyberautoagent")
    from modules.handlers.agent_repair_hook import AgentRepairHook

    recorded = []
    received = []
    entry = {"item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [hypothesis()]}
    if shape == "unknown_field":
        entry["unknown"] = True
    if shape == "unknown_disposition":
        entry["disposition"] = "invented"
    if shape == "nested":
        entry["hypotheses"][0]["preconditions"] = json.dumps(entry["hypotheses"][0]["preconditions"])
        entry["hypotheses"] = json.dumps(entry["hypotheses"])
    subjects = [entry]
    if shape not in {"native", "root_wrapped"}:
        subjects = json.dumps(subjects)
    if shape == "double_wrapped":
        subjects = json.dumps(subjects)
    if shape == "missing_closer":
        subjects = subjects[:-1]
    if shape == "invalid_json":
        subjects = "[{invalid]"
    if shape == "wrong_type":
        subjects = "{}"
    tool = hypotheses.build_record_hypotheses_tool(producer, "OP_TEST", str(tmp_path), recorded.append)
    class Observer(HookProvider):
        def register_hooks(self, registry, **kwargs):
            registry.add_callback(BeforeToolCallEvent, self.before)

        def before(self, event):
            received.append(deepcopy(event.tool_use["input"]))

    agent = Agent(model=Mock(), tools=[], hooks=[AgentRepairHook(), Observer()], callback_handler=None)
    # Resolve the selected schema at call time, including tools added after agent initialization.
    agent.tool_registry.register_tool(tool)
    # Snapshot the SDK-advertised schema after its normal description enrichment.
    agent.tool_registry.get_all_tool_specs()
    original_schema = deepcopy(tool.tool_spec)
    results = []

    arguments = {"subjects": subjects}
    if shape == "root_wrapped":
        arguments = json.dumps(arguments)

    async def collect():
        return [event async for event in SequentialToolExecutor()._execute(
            agent, [{"name": "record_hypotheses", "toolUseId": "call", "input": arguments}],
            results, Mock(), Mock(), {},
        )]

    asyncio.run(collect())
    valid = shape not in {"invalid_json", "wrong_type", "unknown_field", "unknown_disposition"}
    assert results[0]["status"] == ("success" if valid else "error")
    assert len(recorded) == int(valid)
    if shape == "invalid_json":
        feedback = str(results)
        assert "$.subjects" in feedback
        assert "line 1, column" in feedback
        assert "native JSON" in feedback
    assert tool.tool_spec == original_schema
    if valid:
        assert isinstance(received[0]["subjects"], list)
        assert isinstance(received[0]["subjects"][0]["hypotheses"], list)
        assert isinstance(received[0]["subjects"][0]["hypotheses"][0]["preconditions"], list)


def two_subjects(task):
    return replace(task, acceptance=replace(task.acceptance, basis=replace(
        task.acceptance.basis, item_ids=["subject", "second"],
    ), manifest_hash=""))


def no_test(item_id="subject", reason="No safe probe"):
    return {"item_id": item_id, "disposition": "no_testable_hypotheses", "reason": reason}


def test_incremental_publication_replacement_reload_and_idempotence(producer, tmp_path):
    producer = two_subjects(producer)
    first, writer = publish(producer, tmp_path, [no_test()])
    assert first["missing_subject_ids"] == ["second"]
    with pytest.raises(ValueError, match="missing assigned subjects.*second"):
        hypotheses.load_hypothesis_set(first["artifact_ref"], producer, "OP_TEST")
    partial = hypotheses.load_hypothesis_set(first["artifact_ref"], producer, "OP_TEST", require_complete=False)
    assert [subject.item_id for subject in partial.subjects] == ["subject"]
    second = json.loads(writer(subjects=[no_test("second")]))
    assert second["subject_ids"] == ["second", "subject"]
    assert second["missing_subject_ids"] == []
    before = list(producer.recovery_context["hypothesis_publications"])
    assert json.loads(writer(subjects=[no_test("second")]))["sha256"] == second["sha256"]
    assert producer.recovery_context["hypothesis_publications"] == before
    # A new tool instance uses the durable publication, replacing rather than appending a subject's hypotheses.
    third, writer = publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [hypothesis()],
    }])
    payload = hypotheses.load_hypothesis_set(third["artifact_ref"], producer, "OP_TEST")
    assert len(payload.subjects) == 2
    assert len(next(subject for subject in payload.subjects if subject.item_id == "subject").hypotheses) == 1
    fourth = json.loads(writer(subjects=[no_test(reason="Corrected disposition")]))
    assert not next(subject for subject in hypotheses.load_hypothesis_set(
        fourth["artifact_ref"], producer, "OP_TEST",
    ).subjects if subject.item_id == "subject").hypotheses
    with pytest.raises(ValueError, match="digest changed"):
        hypotheses.load_hypothesis_set(first["artifact_ref"], producer, "OP_TEST")


@pytest.mark.parametrize("bad_ref", ["memory:observed", "https://target.test", "artifact:missing.txt"])
def test_publication_reference_error_preserves_existing_state(producer, tmp_path, bad_ref):
    first, writer = publish(producer, tmp_path)
    history = list(producer.recovery_context["hypothesis_publications"])
    broken = {**hypothesis(), "supporting_refs": [bad_ref]}
    with pytest.raises(ValueError, match=r"Subject subject hypotheses\[0\].supporting_refs") as error:
        writer(subjects=[{"item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [broken]}])
    assert bad_ref in str(error.value)
    assert "underlying artifact" in str(error.value)
    assert producer.recovery_context["hypothesis_publications"] == history
    hypotheses.load_hypothesis_set(first["artifact_ref"], producer, "OP_TEST")
    assert len(list((tmp_path / "artifacts" / "hypotheses").glob("*.json"))) == 1


@pytest.mark.parametrize("history", ["invalid", [{}], ["invalid"]])
def test_invalid_publication_history_is_not_silently_replaced(producer, history):
    producer.recovery_context["hypothesis_publications"] = history
    with pytest.raises(ValueError, match="Invalid"):
        hypotheses.hypothesis_binding(producer)


def test_partial_and_corrupted_authoritative_output_cannot_bind_acceptance(producer, tmp_path):
    producer = two_subjects(producer)
    first, writer = publish(producer, tmp_path, [no_test()])
    payload = {"status": "satisfied", "disposition": "observation", "summary": "Semantic result", "evidence_refs": []}
    with pytest.raises(ValueError, match="second"):
        hypotheses.bind_hypothesis_acceptance(producer, "OP_TEST", payload)
    second = json.loads(writer(subjects=[no_test("second")]))
    bound = hypotheses.bind_hypothesis_acceptance(
        producer, "OP_TEST", {**payload, "evidence_refs": [first["artifact_ref"]]},
    )
    assert bound == {**payload, "evidence_refs": [second["artifact_ref"]]}
    assert hypotheses.bind_hypothesis_acceptance(producer, "OP_TEST", {"status": "inaccessible"}) == {
        "status": "inaccessible",
    }
    Path(artifact.resolve_operation_artifact_path(second["artifact_ref"])).unlink()
    with pytest.raises(ValueError, match="does not exist"):
        hypotheses.bind_hypothesis_acceptance(producer, "OP_TEST", payload)
    assert Path(artifact.resolve_operation_artifact_path(first["artifact_ref"])).exists()


def test_bound_acceptance_collects_multiple_publications_from_sqlite(producer, tmp_path, monkeypatch):
    from types import SimpleNamespace

    store = memory.SQLiteApplicationStore(str(tmp_path / "workflow.db"), "target")
    producer = two_subjects(producer)
    store.store_task("OP_TEST", producer)
    monkeypatch.setattr(memory, "_get_database_store", lambda: store)
    monkeypatch.setattr(memory, "_store_memory_entry", lambda *_args: SimpleNamespace(created=True))
    monkeypatch.setattr(memory, "_ensure_memory_client", lambda: SimpleNamespace(
        patch_task=lambda task_uid, user_id=None, **kwargs: store.patch_task("OP_TEST", task_uid, **kwargs),
    ))
    acceptance = memory.build_record_task_acceptance_tool(producer.task_uid, producer)
    with pytest.raises(ValueError, match="missing subjects: second, subject"):
        acceptance(status="satisfied", disposition="observation",
                   summary="Retained semantic decision", evidence_refs=[])

    def current():
        return store.get_tasks("OP_TEST")[0]

    def persist(binding):
        history = [*current().recovery_context.get("hypothesis_publications", []), binding]
        store.patch_task("OP_TEST", producer.task_uid, recovery_context_updates={"hypothesis_publications": history})

    writer = hypotheses.build_record_hypotheses_tool(producer, "OP_TEST", str(tmp_path), persist, current_task=current)
    writer(subjects=[no_test()])
    with pytest.raises(ValueError, match="missing assigned subjects.*second"):
        acceptance(status="satisfied", disposition="observation",
                   summary="Retained semantic decision", evidence_refs=[])
    result = json.loads(writer(subjects=[no_test("second")]))
    # Reloading and a stale acceptance closure must still use the latest persisted cumulative artifact.
    result = json.loads(acceptance(status="satisfied", disposition="observation",
                                   summary="Retained semantic decision", evidence_refs=[]))
    assert result["complete"]
    recorded = store.get_acceptance_results("OP_TEST", producer.task_uid)[0]
    assert recorded.summary == "Retained semantic decision"
    hypotheses.load_hypothesis_set(recorded.evidence_refs[0], current(), "OP_TEST")
    assert len(current().recovery_context["hypothesis_publications"]) == 2


def test_changed_assignment_rejects_stale_publication_tool(producer, tmp_path):
    changed = two_subjects(producer)
    writer = hypotheses.build_record_hypotheses_tool(
        producer, "OP_TEST", str(tmp_path), lambda _binding: pytest.fail("Must not persist"),
        current_task=lambda: changed,
    )
    with pytest.raises(ValueError, match="assignment changed"):
        writer(subjects=[no_test()])
