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
        producer.recovery_context["hypothesis_output"] = binding

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
        producer.recovery_context["hypothesis_output"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        hypotheses.load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")


def test_typed_acceptance_rejects_inventory_and_accepts_immutable_copy(producer, tmp_path):
    criterion = producer.acceptance.criteria[0]
    result = AcceptanceResult("hypotheses", "satisfied", "observation", "Future tests", ("artifact:inventory.json",))
    with pytest.raises(ValueError, match="record_hypotheses"):
        memory._validate_acceptance_result_evidence(producer, criterion, result)
    binding, _tool = publish(producer, tmp_path)
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
