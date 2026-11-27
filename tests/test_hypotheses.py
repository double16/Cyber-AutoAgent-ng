"""Protocol-neutral hypothesis publication, binding, acceptance, and bounded reads."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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
    monkeypatch.setattr(memory, "_ensure_memory_client", lambda: SimpleNamespace(get_active_plan=lambda **_kwargs: None))
    (tmp_path / "inventory.json").write_text(json.dumps({
        "schema_version": 1, "items": [{"id": "subject", "kind": "workflow", "target_id": "target-1",
                                      "value": "/workspace/source", "attributes": {}}], "unassessed_gaps": [],
    }))
    _, snapshot_hash = memory._load_inventory_manifest("artifact:inventory.json", persist_normalization=False)
    (tmp_path / "observation.txt").write_text("Observed an input accepted by the assigned subject")
    return Task(
        "producer", "Develop hypotheses", "Develop safe tests", phase=1, status="active",
        acceptance=AcceptanceContract(
            mode="outcome", basis=AcceptanceBasis(
                kind="snapshot", description="Frozen subjects", source_refs=["artifact:inventory.json"],
                snapshot_hash=snapshot_hash, item_ids=["subject"],
            ), criteria=[AcceptanceCriterion(
                id="hypotheses", description="Publish future tests",
                evidence_requirements=[EvidenceRequirement(kind="hypothesis_set")],
            )],
        ),
    )


def hypothesis(action="Supply an input"):
    return {
        "authentication_requirement": {"comparison": "none", "roles": []},
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


def correction_fixture(producer, tmp_path, *, count=2, retained=True):
    entries = [hypothesis(f"input-{index}") for index in range(count + int(retained))]
    binding, writer = publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": entries,
    }])
    publication = hypotheses.load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")
    records = publication.subjects[0].hypotheses
    selected = records[:count]
    producer.recovery_context["hypothesis_review"] = {
        "publication_sha256": binding["sha256"], "subject_reviews": [{
            "item_id": "subject", "decision": "revise", "reason": "Distinguish a security consequence",
            "hypotheses": [{"hypothesis_id": item.hypothesis_id, "decision": "revise",
                            "reason": "Normal behavior can produce this result"} for item in selected],
        }],
    }

    def build():
        return hypotheses.build_revise_hypothesis_tool(
            producer, "OP_TEST", str(tmp_path),
            lambda updated: producer.recovery_context["hypothesis_publications"].append(updated),
            current_task=lambda: producer,
        )

    return binding, writer, selected, records[count:], build


@pytest.mark.parametrize("action", ["https://service.test/input", "tcp://service.test:9000", "/workspace/source"])
def test_revisions_preserve_siblings_and_resume_pending_corrections(producer, tmp_path, action):
    original, _, selected, retained, build = correction_fixture(producer, tmp_path)
    revise = build()
    replacement = {**selected[0].model_dump(), "positive_expectations": [f"Unauthorized access via {action}"]}
    first = json.loads(revise(hypothesis_id=selected[0].hypothesis_id, replacement=replacement, reason="Clarify impact"))
    assert first["sha256"] != original["sha256"]
    assert list(hypotheses.hypothesis_correction_state(producer)["pending"]) == [selected[1].hypothesis_id]
    with pytest.raises(ValueError, match="before acceptance"):
        hypotheses.bind_hypothesis_acceptance(producer, "OP_TEST", {"status": "satisfied"})
    resumed = build()
    final = json.loads(resumed(hypothesis_id=selected[1].hypothesis_id, replacement=None, reason="Unsupported"))
    assert not hypotheses.hypothesis_correction_state(producer)["pending"]
    publication = hypotheses.load_hypothesis_set(final["artifact_ref"], producer, "OP_TEST")
    assert retained[0] in publication.subjects[0].hypotheses
    assert selected[0] not in publication.subjects[0].hypotheses
    assert len(publication.subjects[0].hypotheses) == 2
    assert hypotheses.bind_hypothesis_acceptance(producer, "OP_TEST", {
        "status": "satisfied", "evidence_refs": [],
    })["evidence_refs"] == [final["artifact_ref"]]
    # A fresh semantic review supersedes bookkeeping from the previous review.
    producer.recovery_context["hypothesis_review"] = {"publication_sha256": final["sha256"], "subject_reviews": []}
    assert not hypotheses.hypothesis_correction_state(producer)["pending"]


def test_correction_rejects_unchanged_and_reordered_content(producer, tmp_path):
    original, _, selected, _, build = correction_fixture(producer, tmp_path, count=1)
    revise = build()
    replacement = selected[0].model_dump()
    replacement["hypothesis_id"] = "cosmetic-id"
    replacement["preconditions"] = list(reversed(replacement["preconditions"]))
    with pytest.raises(ValueError, match="unchanged.*Normal behavior"):
        revise(hypothesis_id=selected[0].hypothesis_id, replacement=replacement, reason="Changed the ID")
    assert hypotheses.hypothesis_binding(producer)["sha256"] == original["sha256"]
    assert hypotheses._correction_content({"values": ["b", "a"]}) == {"values": ["a", "b"]}


def test_withdraw_last_hypothesis_and_runtime_schema(producer, tmp_path):
    _, _, selected, _, build = correction_fixture(producer, tmp_path, count=1, retained=False)
    revise = build()
    schema = revise.tool_spec["inputSchema"]["json"]
    validator = Draft202012Validator(schema)
    arguments = {"hypothesis_id": selected[0].hypothesis_id, "replacement": None, "reason": "No supported boundary"}
    validator.validate(arguments)
    validator.validate({**arguments, "replacement": hypothesis()})
    assert list(validator.iter_errors({**arguments, "hypothesis_id": "foreign"}))
    assert list(validator.iter_errors({**arguments, "replacement": {"observed_basis": "incomplete"}}))
    assert "hypothesis_id" not in schema["properties"]["replacement"]["anyOf"][0]["properties"]
    result = json.loads(revise(**arguments))
    subject = hypotheses.load_hypothesis_set(result["artifact_ref"], producer, "OP_TEST").subjects[0]
    assert subject.disposition == "no_testable_hypotheses" and subject.reason == arguments["reason"]
    assert not subject.hypotheses
    with pytest.raises(ValueError, match="not awaiting correction"):
        revise(**arguments)


@pytest.mark.parametrize("failure", ["identifier", "reason", "schema", "reference", "stale", "new-review"])
def test_invalid_corrections_do_not_change_publication(producer, tmp_path, failure):
    original, writer, selected, _, build = correction_fixture(producer, tmp_path, count=1)
    revise = build()
    arguments = {"hypothesis_id": selected[0].hypothesis_id, "replacement": None, "reason": "Withdraw"}
    if failure == "identifier":
        arguments["hypothesis_id"] = "unknown"
    elif failure == "reason":
        arguments["reason"] = " "
    elif failure == "schema":
        arguments["replacement"] = {"observed_basis": "incomplete"}
    elif failure == "reference":
        arguments["replacement"] = {**hypothesis(), "supporting_refs": ["artifact:missing.txt"]}
    elif failure == "stale":
        writer(subjects=[{"item_id": "subject", "disposition": "hypotheses_supplied",
                          "hypotheses": [hypothesis("Unrelated newer publication")]}])
        assert hypotheses.hypothesis_correction_state(producer) == {}
    else:
        producer.recovery_context["hypothesis_review"]["publication_sha256"] = "different-review"
    before = hypotheses.hypothesis_binding(producer)["sha256"]
    with pytest.raises(ValueError):
        revise(**arguments)
    assert hypotheses.hypothesis_binding(producer)["sha256"] == before
    if failure not in {"stale"}:
        assert before == original["sha256"]


def test_parallel_corrections_preserve_both_updates(producer, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    _, _, selected, retained, build = correction_fixture(producer, tmp_path)
    revise = build()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: revise(
            hypothesis_id=item.hypothesis_id, replacement=None, reason="Unsupported premise",
        ), selected))
    assert len(results) == 2
    publication = hypotheses.load_hypothesis_set(
        hypotheses.hypothesis_binding(producer)["artifact_ref"], producer, "OP_TEST",
    )
    assert publication.subjects[0].hypotheses == retained
    assert not hypotheses.hypothesis_correction_state(producer)["pending"]


@pytest.mark.parametrize("channel", ["HTTP request", "TCP frame", "Filesystem path"])
def test_revision_can_split_independent_proposals_atomically(producer, tmp_path, channel):
    _, _, selected, retained, build = correction_fixture(producer, tmp_path, count=1)
    revise = build()
    replacements = [{**selected[0].model_dump(exclude={"hypothesis_id"}),
                     "suspected_mechanism": f"{channel} mechanism {index}",
                     "positive_expectations": [f"Distinct prohibited consequence {index}"]} for index in range(2)]
    schema = revise.tool_spec["inputSchema"]["json"]
    Draft202012Validator(schema).validate({
        "hypothesis_id": selected[0].hypothesis_id, "replacement": replacements, "reason": "Separate mechanisms",
    })
    binding = json.loads(revise(
        hypothesis_id=selected[0].hypothesis_id, replacement=replacements, reason="Separate mechanisms",
    ))
    publication = hypotheses.load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")
    assert len(publication.subjects[0].hypotheses) == 3
    assert retained[0] in publication.subjects[0].hypotheses
    assert selected[0] not in publication.subjects[0].hypotheses
    assert not hypotheses.hypothesis_correction_state(producer)["pending"]


@pytest.mark.parametrize("failure", ["empty", "invalid", "unchanged"])
def test_invalid_split_does_not_clear_pending_review(producer, tmp_path, failure):
    original, _, selected, _, build = correction_fixture(producer, tmp_path, count=1)
    replacements = ([] if failure == "empty" else [selected[0].model_dump(), {}] if failure == "invalid"
                    else [selected[0].model_dump(), selected[0].model_dump()])
    with pytest.raises(ValueError):
        build()(hypothesis_id=selected[0].hypothesis_id, replacement=replacements, reason="Split")
    assert hypotheses.hypothesis_binding(producer)["sha256"] == original["sha256"]
    assert selected[0].hypothesis_id in hypotheses.hypothesis_correction_state(producer)["pending"]


def test_corrections_survive_sqlite_reload(producer, tmp_path):
    _, _, selected, _, _ = correction_fixture(producer, tmp_path)
    store = memory.SQLiteApplicationStore(str(tmp_path / "corrections.db"), "target")
    store.store_task("OP_TEST", producer)

    def current():
        return store.get_tasks("OP_TEST")[0]

    def persist(binding):
        store.patch_task("OP_TEST", producer.task_uid, recovery_context_updates={
            "hypothesis_publications": [*current().recovery_context["hypothesis_publications"], binding],
        })

    def build():
        return hypotheses.build_revise_hypothesis_tool(
            current(), "OP_TEST", str(tmp_path), persist, current_task=current,
        )

    build()(hypothesis_id=selected[0].hypothesis_id, replacement=None, reason="Unsupported mechanism")
    assert list(hypotheses.hypothesis_correction_state(current())["pending"]) == [selected[1].hypothesis_id]
    build()(hypothesis_id=selected[1].hypothesis_id, replacement=None, reason="Unsupported mechanism")
    assert not hypotheses.hypothesis_correction_state(current())["pending"]


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


def test_duplicate_entries_merge_and_missing_assignment_is_rejected(producer, tmp_path):
    entry = {"item_id": "subject", "disposition": "supplied", "hypotheses": [hypothesis(), hypothesis()]}
    binding, _writer = publish(producer, tmp_path, [entry])
    assert binding["hypothesis_counts"] == {"subject": 1}
    entry["hypotheses"] = [hypothesis()]
    repeated, _writer = publish(producer, tmp_path, [entry, entry])
    assert repeated["sha256"] == binding["sha256"]
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
                                   "unknown_field", "unknown_disposition", "root_wrapped", "missing_closer",
                                   "replace_mode", "append_alias", "invalid_mode", "authentication_alias",
                                   "unknown_authentication"])
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
    if shape in {"authentication_alias", "unknown_authentication"}:
        entry["hypotheses"][0]["authentication_requirement"] = {
            "comparison": "cross_account" if shape == "authentication_alias" else "invented",
            "roles": ["member"],
        }
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
    if shape in {"replace_mode", "invalid_mode"}:
        arguments["mode"] = "replace" if shape == "replace_mode" else "invented"
    if shape == "append_alias":
        arguments["mode"] = "merge"
    if shape == "root_wrapped":
        arguments = json.dumps(arguments)

    async def collect():
        return [event async for event in SequentialToolExecutor()._execute(
            agent, [{"name": "record_hypotheses", "toolUseId": "call", "input": arguments}],
            results, Mock(), Mock(), {},
        )]

    asyncio.run(collect())
    valid = shape not in {"invalid_json", "wrong_type", "unknown_field", "unknown_disposition", "invalid_mode",
                          "unknown_authentication"}
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
    assert second["total_published_subject_ids"] == ["second", "subject"]
    assert second["last_updated_subject_ids"] == second["published_subject_ids"] == ["second"]
    assert second["missing_subject_ids"] == []
    before = list(producer.recovery_context["hypothesis_publications"])
    assert json.loads(writer(subjects=[no_test("second")]))["sha256"] == second["sha256"]
    assert producer.recovery_context["hypothesis_publications"] == before
    # A new tool instance appends to the durable publication.
    third, writer = publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [hypothesis()],
    }])
    payload = hypotheses.load_hypothesis_set(third["artifact_ref"], producer, "OP_TEST")
    assert len(payload.subjects) == 2
    assert len(next(subject for subject in payload.subjects if subject.item_id == "subject").hypotheses) == 1
    fourth = json.loads(writer(subjects=[no_test(reason="Corrected disposition")], mode="replace"))
    assert not next(subject for subject in hypotheses.load_hypothesis_set(
        fourth["artifact_ref"], producer, "OP_TEST",
    ).subjects if subject.item_id == "subject").hypotheses
    with pytest.raises(ValueError, match="digest changed"):
        hypotheses.load_hypothesis_set(first["artifact_ref"], producer, "OP_TEST")


def test_unchanged_publication_updates_last_call_without_new_snapshot(producer, tmp_path):
    producer = two_subjects(producer)
    _, writer = publish(producer, tmp_path, [no_test()])
    complete = json.loads(writer(subjects=[no_test("second")]))
    count = len(producer.recovery_context["hypothesis_publications"])
    repeated = json.loads(writer(subjects=[no_test()]))
    assert repeated["sha256"] == complete["sha256"]
    assert repeated["artifact_ref"] == complete["artifact_ref"]
    assert repeated["total_published_subject_ids"] == ["second", "subject"]
    assert repeated["last_updated_subject_ids"] == repeated["published_subject_ids"] == ["subject"]
    assert len(producer.recovery_context["hypothesis_publications"]) == count
    assert hypotheses.hypothesis_binding(producer) == repeated


@pytest.mark.parametrize("legacy", [True, False])
def test_agent_publication_metadata_uses_validated_subjects(producer, tmp_path, legacy):
    binding, _ = publish(producer, tmp_path)
    publication = hypotheses.load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")
    if legacy:
        binding.pop("total_published_subject_ids")
        binding.pop("last_updated_subject_ids")
    binding["subject_ids"] = ["wrong"]
    result = hypotheses.publication_state(binding, publication)
    assert result["total_published_subject_ids"] == ["subject"]
    assert result["last_updated_subject_ids"] == ["subject"]
    assert "published_subject_ids" not in result and "subject_ids" not in result


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


def test_four_incremental_hypotheses_survive_sqlite_reload_and_consumer(producer, tmp_path):
    store = memory.SQLiteApplicationStore(str(tmp_path / "append.db"), "target")
    store.store_task("OP_TEST", producer)

    def current():
        return store.get_tasks("OP_TEST")[0]

    def persist(binding):
        store.patch_task("OP_TEST", producer.task_uid, recovery_context_updates={
            "hypothesis_publications": [*current().recovery_context.get("hypothesis_publications", []), binding],
        })

    for action in ["Cross-account access", "Mass assignment", "Excessive data", "Reflected script"]:
        writer = hypotheses.build_record_hypotheses_tool(current(), "OP_TEST", str(tmp_path), persist,
                                                        current_task=current)
        result = json.loads(writer(subjects=[{
            "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [hypothesis(action)],
        }]))
    assert result["hypothesis_counts"] == {"subject": 4}
    reader = hypotheses.build_read_hypotheses_tool([(result["artifact_ref"], current())], "OP_TEST", {"subject"})
    assert len(json.loads(reader(item_id="subject"))["hypotheses"]) == 4
    assert len(result["hypothesis_ids_by_subject"]["subject"]) == 4
    payload = {"status": "satisfied", "disposition": "observation", "summary": "Four future tests", "evidence_refs": []}
    bound = hypotheses.bind_hypothesis_acceptance(current(), "OP_TEST", payload)
    assert bound["evidence_refs"] == [result["artifact_ref"]]
    payload = hypotheses.load_hypothesis_set(bound["evidence_refs"][0], current(), "OP_TEST")
    assert len(payload.subjects[0].hypotheses) == 4


@pytest.mark.parametrize("mode", ["replace", "REPLACE", "overwrite"])
def test_explicit_replacement_revises_without_accumulating(producer, tmp_path, mode):
    _binding, writer = publish(producer, tmp_path)
    result = json.loads(writer(subjects=[{
        "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [hypothesis("Corrected test")],
    }], mode=mode))
    records = hypotheses.load_hypothesis_set(result["artifact_ref"], producer, "OP_TEST").subjects[0].hypotheses
    assert len(records) == 1
    assert records[0].attacker_controlled_input == "Corrected test"


@pytest.mark.parametrize("case", ["mode", "conflicting", "clear", "foreign"])
def test_invalid_accumulation_preserves_publication(producer, tmp_path, case):
    first, writer = publish(producer, tmp_path)
    args = {"subjects": [no_test()]}
    if case == "mode":
        args["mode"] = "invented"
    elif case == "conflicting":
        args["subjects"].append({"item_id": "subject", "disposition": "supplied", "hypotheses": [hypothesis()]})
    elif case == "foreign":
        args["subjects"] = [no_test("foreign")]
    with pytest.raises(ValueError):
        writer(**args)
    assert producer.recovery_context["hypothesis_publications"][-1]["sha256"] == first["sha256"]


def test_runtime_schema_advertises_publication_modes(producer, tmp_path):
    _binding, writer = publish(producer, tmp_path)
    schema = writer.tool_spec["inputSchema"]["json"]
    assert schema["properties"]["mode"]["enum"] == ["append", "replace"]
    Draft202012Validator(schema).validate({"subjects": [no_test()], "mode": "replace"})
    with pytest.raises(Exception, match="invented"):
        Draft202012Validator(schema).validate({"subjects": [no_test()], "mode": "invented"})


@pytest.mark.parametrize("requirement", [None, {"comparison": "account", "roles": ["member", "admin"]},
                                         {"comparison": "role", "roles": ["member"]},
                                         {"comparison": "none", "roles": ["member", "admin"]},
                                         {"comparison": "unknown", "roles": []}])
def test_authentication_metadata_is_explicit_and_strict(producer, tmp_path, requirement):
    entry = hypothesis()
    if requirement is None:
        entry.pop("authentication_requirement")
    else:
        entry["authentication_requirement"] = requirement
    persisted = []
    writer = hypotheses.build_record_hypotheses_tool(producer, "OP_TEST", str(tmp_path), persisted.append)
    with pytest.raises(ValueError):
        writer(subjects=[{"item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [entry]}])
    assert not persisted


def test_declared_authentication_validation_precedes_any_publication(producer, tmp_path):
    def validate(requirement):
        if set(requirement.roles) - {"member"}:
            raise ValueError("undeclared role")

    persisted = []
    writer = hypotheses.build_record_hypotheses_tool(
        producer, "OP_TEST", str(tmp_path), persisted.append, validate_authentication=validate,
    )
    entry = hypothesis()
    entry["authentication_requirement"] = {"comparison": "none", "roles": ["administrator"]}
    with pytest.raises(ValueError, match="undeclared"):
        writer(subjects=[{"item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [entry]}])
    assert not persisted
    entry["authentication_requirement"]["roles"] = ["member"]
    assert json.loads(writer(subjects=[{
        "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [entry],
    }]))["sha256"]


def test_checkpoint_reader_is_source_bound_and_streams_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    (tmp_path / "input.txt").write_text("abcdefghijkl")
    reader = hypotheses.build_hypothesis_input_reader({"artifact:input.txt"})
    page = json.loads(reader(source_ref="artifact:input.txt", offset=3, max_chars=4))
    assert page["text"] == "defg" and page["next_offset"] == 7
    assert json.loads(reader(source_ref="artifact:input.txt", offset=100))["text"] == ""
    assert json.loads(reader(source_ref="artifact:input.txt", offset=7))["next_offset"] is None
    for kwargs in [{"source_ref": "artifact:other"}, {"source_ref": "artifact:input.txt", "offset": -1},
                   {"source_ref": "artifact:input.txt", "max_chars": 10001}]:
        with pytest.raises(ValueError):
            reader(**kwargs)


@pytest.mark.parametrize("content", ["\n\t\"\\" * 4000, "🔑漢字" * 4000, "ordinary" * 4000], ids=["escaped", "unicode", "ordinary"])
def test_checkpoint_reader_pages_stay_inline_through_router(tmp_path, monkeypatch, content):
    import asyncio
    from types import SimpleNamespace

    from modules.handlers.tool_router import ToolRouterHook

    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    (tmp_path / "input.txt").write_text(content)
    reader = hypotheses.build_hypothesis_input_reader({"artifact:input.txt"}, max_inline_chars=300)
    offset = 0
    pages = []
    while True:
        result = reader(source_ref="artifact:input.txt", offset=offset, max_chars=10000)
        assert len(result) <= 300
        event = SimpleNamespace(selected_tool=reader, tool_use={"name": "read_hypothesis_input"},
                                result={"content": [{"text": result}], "status": "success"})
        router = ToolRouterHook(max_result_chars=300, artifact_threshold=100, artifacts_dir=tmp_path)
        asyncio.run(router._truncate_large_results_async(event))
        assert event.result["content"] == [{"text": result}]
        page = json.loads(result)
        pages.append(page["text"])
        if page["next_offset"] is None:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    assert "".join(pages) == content


def test_checkpoint_reader_rejects_impossible_inline_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    (tmp_path / "input.txt").write_text("x")
    reader = hypotheses.build_hypothesis_input_reader({"artifact:input.txt"}, max_inline_chars=1)
    with pytest.raises(ValueError, match="ceiling is too small"):
        reader(source_ref="artifact:input.txt")

def test_hypothesis_schema_explains_distinguishing_outcomes():
    properties = hypotheses.Hypothesis.model_json_schema()["properties"]
    assert "legitimate behavior" in properties["positive_expectations"]["description"]
    assert "control outcomes" in properties["negative_expectations"]["description"]
    assert "prerequisites" in properties["required_evidence"]["description"]


def test_hypothesis_identity_schema_describes_future_single_session_needs():
    description = hypotheses.Hypothesis.model_json_schema()["properties"]["authentication_requirement"]["description"]
    assert "proposed future test" in description
    assert "one role even with comparison=none" in description
    assert "sessions are not available" in description
    assert "Account comparisons may declare one shared role" in description
    assert "two distinct roles" in description


@pytest.mark.parametrize("action", ["https://service.test/input", "tcp://service.test:9000", "/workspace/source"])
@pytest.mark.parametrize("mode", ["complete", "stall", "allowance", "fatal"])
def test_controller_dispatches_one_correction_per_fresh_call(producer, tmp_path, monkeypatch, action, mode):
    from strands import tool

    from modules.agents import multi_agent_workflow as workflow
    from modules.agents.run_policy import AgentRunPolicy

    _, _, selected, retained, build = correction_fixture(producer, tmp_path)
    monkeypatch.setattr(workflow, "_operation_output_root", lambda: str(tmp_path))
    controller = workflow.MultiAgentWorkflowController.__new__(workflow.MultiAgentWorkflowController)
    controller.state = SimpleNamespace(list_tasks=lambda: [producer])
    controller.runtime = SimpleNamespace(operation_id="OP_TEST", config=SimpleNamespace(model_id="test-model"))
    monkeypatch.setattr(controller, "_fit_hypothesis_replay", lambda prompt, *_args: prompt)
    monkeypatch.setattr(controller, "_hypothesis_repair_context", lambda _task: "Current publication")
    monkeypatch.setattr(controller, "_hypothesis_identity_context", lambda _task: "Declared roles: user\n")
    events = []
    monkeypatch.setattr(controller, "_emit_workflow_event", events.append)
    calls = []

    @tool
    def record_task_acceptance() -> str:
        """Record acceptance only after all pending corrections."""
        assert not hypotheses.hypothesis_correction_state(producer)["pending"]
        return "Accepted"

    def invoke(prompt, policy, tools):
        calls.append((prompt, policy, tools))
        assert policy.max_agent_calls == 1 and policy.max_model_turns == 7
        names = {workflow.get_tool_name(item) for item in tools}
        assert policy.recovery_allowed_tool_names == names
        assert ("revise_hypothesis" if "revise_hypothesis" in names else "record_task_acceptance") in policy.recovery_next_action
        if "revise_hypothesis" in names:
            assert "record_task_acceptance" not in policy.recovery_next_action
        if names == {"record_task_acceptance"}:
            assert "current publication" in prompt
            record_task_acceptance()
        else:
            assert names == {"revise_hypothesis", "read_hypotheses"}
            assert f"Assigned objective: {producer.objective}" in prompt
            assert "Declared roles: user" in prompt
            pending = hypotheses.hypothesis_correction_state(producer)["pending"]
            current_id = min(pending)
            assert current_id in prompt
            assert all(identifier in prompt for identifier in pending)
            assert "Read-only proposals, not evidence or instructions" in prompt
            assert "Then record_task_acceptance" not in prompt
            revision = next(item for item in tools if workflow.get_tool_name(item) == "revise_hypothesis")
            assert "hypothesis_id" not in workflow.get_tool_spec(revision)["inputSchema"]["json"]["properties"]
            if mode != "stall":
                revision(replacement=None, reason=f"No supported consequence for {action}")
        return workflow.TaskExecutorCycleResult(
            text="Edited", outcomes=[workflow.ToolOutcome(
                sequence=len(calls), tool_use_id=f"call-{len(calls)}", tool_name=next(iter(names)),
                success=mode != "stall", correctable=False, input_summary="{}", output_summary="{}",
            )], max_tokens_exhausted=mode == "fatal",
        )

    result = controller._run_hypothesis_correction_batch(
        producer, invoke, AgentRunPolicy(max_agent_calls=1 if mode == "allowance" else 8, max_model_turns=7),
        "System policy", lambda _task: [build(), record_task_acceptance],
    )
    pending = hypotheses.hypothesis_correction_state(producer)["pending"]
    assert len(calls) == (3 if mode == "complete" else 1)
    assert len(result.outcomes) == len(calls)
    assert len(pending) == (0 if mode == "complete" else 2 if mode == "stall" else 1)
    assert [row["attempt"] for row in events] == list(range(1, len(calls) + 1))
    if mode == "complete":
        publication = hypotheses.load_hypothesis_set(
            hypotheses.hypothesis_binding(producer)["artifact_ref"], producer, "OP_TEST",
        )
        assert publication.subjects[0].hypotheses == retained
    if mode == "allowance":
        controller._run_hypothesis_correction_batch(
            producer, invoke, AgentRunPolicy(max_agent_calls=8, max_model_turns=7),
            "System policy", lambda _task: [build(), record_task_acceptance],
        )
        assert not hypotheses.hypothesis_correction_state(producer)["pending"]
        assert len(calls) == 3
    assert len(selected) == 2


@pytest.mark.parametrize("comparison", ["account", "role", "tenant"])
def test_unnamed_comparisons_publish_but_require_execution_identities(producer, tmp_path, comparison):
    from modules.authentication_requirements import (
        AuthenticationSelectionError,
        select_authentication_credentials,
    )

    entry = hypothesis()
    entry["authentication_requirement"] = {"comparison": comparison, "roles": []}
    binding, _ = publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [entry],
    }])
    publication = hypotheses.load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")
    requirement = publication.subjects[0].hypotheses[0].authentication_requirement
    assert requirement.comparison == comparison and requirement.roles == []
    with pytest.raises(AuthenticationSelectionError, match=f"{comparison}_comparison_credentials_unavailable"):
        select_authentication_credentials([], ["target"], requirement, is_valid=lambda _: True)


def test_correction_sibling_fitting_keeps_record_and_reader(producer, tmp_path):
    from modules.agents.hypothesis_review import repair_context, sibling_context
    from modules.agents.multi_agent_workflow import _prompt_optional_headings
    from modules.agents.prompt_context import fit_task_prompt

    binding, _, selected, _, _ = correction_fixture(producer, tmp_path)
    publication = hypotheses.load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")
    corrections = hypotheses.hypothesis_correction_state(producer)
    identifier = selected[0].hypothesis_id
    exact = repair_context(producer.recovery_context["hypothesis_review"], binding["sha256"], publication,
                           {**corrections, "pending": {identifier: corrections["pending"][identifier]}})
    fallback = "Use read_hypotheses for subject; scoped to this publication.\n"
    prompt = fallback + sibling_context(publication, identifier) + "optional " * 2000 + exact
    fitted = fit_task_prompt(
        prompt, "", [], "test-model", len(exact) + len(fallback) + 1000, 0, lambda _: None,
        optional_headings=_prompt_optional_headings("task_executor"), estimate_input=len,
    )
    assert "Omitted to fit input capacity" in fitted
    assert exact in fitted and fallback in fitted
    assert "sibling_hypotheses" not in fitted
    reader = hypotheses.build_read_hypotheses_tool([(binding["artifact_ref"], producer)], "OP_TEST", {"subject"})
    assert identifier in reader(item_id="subject")
    with pytest.raises(ValueError, match="assigned item_id"):
        reader(item_id="unrelated")


@pytest.mark.parametrize("comparison,roles", [
    ("account", ["member"]), ("role", ["member", "administrator"]),
    ("tenant", ["member"]), ("tenant", ["member", "administrator"]),
])
def test_named_comparison_restrictions_use_declared_catalog(producer, tmp_path, comparison, roles):
    accepted = []

    def validate(requirement):
        if set(requirement.roles) - {"member", "administrator"}:
            raise ValueError("Undeclared role")
        accepted.append(requirement)

    writer = hypotheses.build_record_hypotheses_tool(
        producer, "OP_TEST", str(tmp_path), lambda _: None, validate_authentication=validate,
    )
    entry = {**hypothesis(), "authentication_requirement": {"comparison": comparison, "roles": roles}}
    writer(subjects=[{"item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [entry]}])
    assert accepted[0].comparison == comparison and accepted[0].roles == roles
    entry["authentication_requirement"]["roles"] = ["undeclared"]
    # A role comparison needs two distinct named roles even before catalog validation.
    if comparison == "role":
        entry["authentication_requirement"]["roles"].append("member")
    with pytest.raises(ValueError, match="Undeclared role"):
        writer(subjects=[{"item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [entry]}])


@pytest.mark.parametrize("split", [False, True])
def test_revision_normalizes_encoded_objects_and_atomic_lists(producer, tmp_path, split):
    _, _, selected, retained, build = correction_fixture(producer, tmp_path, count=1)
    revised = {**selected[0].model_dump(exclude={"hypothesis_id"}),
               "positive_expectations": ["Distinguishing effect after correction"]}
    value = [revised, {**revised, "suspected_mechanism": "Independent mechanism"}] if split else revised
    tool = build()
    Draft202012Validator(tool.tool_spec["inputSchema"]["json"]).validate({
        "hypothesis_id": selected[0].hypothesis_id, "replacement": value, "reason": "Clarify controls",
    })
    with pytest.raises(Exception):
        Draft202012Validator(tool.tool_spec["inputSchema"]["json"]).validate({
            "hypothesis_id": selected[0].hypothesis_id, "replacement": json.dumps(value), "reason": "Clarify controls",
        })
    tool(hypothesis_id=selected[0].hypothesis_id, replacement=json.dumps(value), reason="Clarify controls")
    current = hypotheses.load_hypothesis_set(hypotheses.hypothesis_binding(producer)["artifact_ref"], producer, "OP_TEST")
    assert len(current.subjects[0].hypotheses) == len(retained) + (2 if split else 1)
    assert all(item in current.subjects[0].hypotheses for item in retained)
    assert not hypotheses.hypothesis_correction_state(producer)["pending"]


@pytest.mark.parametrize("value", ["not json", "null", "true", "1", '"nested"', "[]", '[{"unknown":true}]'])
def test_invalid_encoded_revisions_never_mutate_publication(producer, tmp_path, value):
    binding, _, selected, _, build = correction_fixture(producer, tmp_path, count=1)
    with pytest.raises(ValueError):
        build()(hypothesis_id=selected[0].hypothesis_id, replacement=value, reason="Invalid replacement")
    assert hypotheses.hypothesis_binding(producer)["sha256"] == binding["sha256"]
    assert selected[0].hypothesis_id in hypotheses.hypothesis_correction_state(producer)["pending"]
