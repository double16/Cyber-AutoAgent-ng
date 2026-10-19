# ruff: noqa: F811
"""Controller construction uses frozen subjects, publications and explicit identities."""

import json

import pytest

from modules.agents.hypothesis_tasks import build_assignments
from modules.agents.multi_agent_workflow import TaskCreationBatch
from modules.tools.hypotheses import load_hypothesis_set
from tests.test_hypotheses import hypothesis, producer, publish  # noqa: F401


@pytest.mark.parametrize("label", ["https://target.test/input", "tcp://target:9000", "/workspace/source.py"])
def test_atomic_groups_partition_authentication_and_keep_all_hypotheses(producer, tmp_path, label):
    entries = [hypothesis("one"), hypothesis("two"), hypothesis("three"), hypothesis("four")]
    entries[1]["authentication_requirement"] = {"roles": ["reader"], "comparison": "none"}
    entries[2]["authentication_requirement"] = {"roles": ["reader"], "comparison": "account"}
    entries[3]["authentication_requirement"] = {"roles": ["reader"], "comparison": "none"}
    binding, _ = publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": entries,
    }])
    payload = load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")
    batch = TaskCreationBatch(1, 1, "artifact:inventory.json", (("target", "service", label, ("subject",)),), 0)
    args = (batch, [(producer, binding, payload)])
    result = build_assignments(*args)
    assert len(result) == 3
    assert sum(len(row.hypothesis_ids) for row in result) == 4
    assert result == build_assignments(*args)
    assert all(row.proposal.snapshot_refs == [batch.snapshot_ref] and not row.proposal.methods for row in result)
    assert len({row.proposal.workstream for row in result}) == 3
    assert all(row.item_ids == ("subject",) for row in result)


def test_no_hypothesis_and_foreign_subjects_do_not_create_tasks(producer, tmp_path):
    binding, _ = publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "no_testable_hypotheses", "reason": "No supported impact",
    }])
    payload = load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")
    batch = TaskCreationBatch(1, 1, "artifact:inventory.json", (("target", "service", "service", ("other",)),), 0)
    assert build_assignments(batch, [(producer, binding, payload)]) == []
    batch = TaskCreationBatch(1, 1, "artifact:inventory.json", (("target", "service", "service", ("subject",)),), 0)
    assert build_assignments(batch, [(producer, binding, payload)]) == []


def test_assignment_rejects_missing_stable_id_before_persistence(producer, tmp_path):
    binding, _ = publish(producer, tmp_path)
    payload = load_hypothesis_set(binding["artifact_ref"], producer, "OP_TEST")
    payload.subjects[0].hypotheses[0].hypothesis_id = ""
    batch = TaskCreationBatch(1, 1, "artifact:inventory.json", (("target", "service", "service", ("subject",)),), 0)
    with pytest.raises(ValueError, match="stable"):
        build_assignments(batch, [(producer, binding, payload)])


def test_reader_filters_before_pagination(producer, tmp_path):
    from modules.tools.hypotheses import build_read_hypotheses_tool

    binding, _ = publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "hypotheses_supplied",
        "hypotheses": [hypothesis("one"), hypothesis("two")],
    }])
    selected = binding["hypothesis_ids_by_subject"]["subject"][1]
    reader = build_read_hypotheses_tool([(binding["artifact_ref"], producer)], "OP_TEST", {"subject"}, {selected})
    result = json.loads(reader(item_id="subject", max_records=1))
    assert [row["hypothesis_id"] for row in result["hypotheses"]] == [selected]
    assert result["next_offset"] is None


@pytest.mark.parametrize("phase_id", [3, 8])
@pytest.mark.parametrize("value,kind", [("https://target.test/input", "endpoint"),
                                      ("tcp://target.test:9000", "service"), ("/workspace/source.py", "workflow")])
def test_controller_compiles_and_deduplicates_with_sqlite_without_creator(
    producer, tmp_path, monkeypatch, phase_id, value, kind,
):
    from dataclasses import replace
    from types import SimpleNamespace
    from unittest.mock import Mock

    from modules.agents import multi_agent_workflow as workflow
    from modules.config.types import BudgetConfig
    from modules.tools import memory
    from tests.test_multi_agent_workflow import _runtime

    plan = memory.OperationPlan(objective="Assess", current_phase=phase_id, total_phases=phase_id, phases=[
        memory.PlanPhase(id=1, title="Candidates", status="done", produces_hypotheses=True,
                         source_manifest_refs=("artifact:inventory.json",)),
        *[memory.PlanPhase(id=index, title="Other work", status="done") for index in range(2, phase_id)],
        memory.PlanPhase(id=phase_id, title="Investigate", status="active", task_creation_mode="hypothesis_dependent"),
    ], targets=[memory.OperationTarget(target_id="target-1", value=value, type="network")])
    store = memory.SQLiteApplicationStore(str(tmp_path / "workflow.db"), "target")
    client = SimpleNamespace(
        get_active_plan=lambda **_kwargs: plan,
        list_tasks=lambda phase=None, status=None: [task for task in store.get_tasks("OP_TEST")
            if (phase is None or task.phase == phase) and (status is None or task.status in status)],
        list_task_acceptance_results=lambda *_args, **_kwargs: [],
        store_task=lambda *, task, **_kwargs: store.store_task("OP_TEST", task),
        patch_task=lambda *, task_uid, **kwargs: store.patch_task("OP_TEST", task_uid, **kwargs),
    )
    monkeypatch.setattr(workflow.WorkflowStateStore, "client", property(lambda _self: client))
    monkeypatch.setattr(memory, "_ensure_memory_client", lambda: client)
    monkeypatch.setattr(memory, "_get_database_store", lambda: store)
    monkeypatch.setattr(memory, "_user_id", lambda: "tester")
    monkeypatch.setattr(workflow, "_operation_output_root", lambda: str(tmp_path))
    manifest = {"schema_version": memory.INVENTORY_MANIFEST_SCHEMA_VERSION, "items": [
        {"id": "subject", "target_id": "target-1", "kind": kind, "value": value, "attributes": {}},
    ], "unassessed_gaps": []}
    (tmp_path / "inventory.json").write_text(json.dumps(manifest))
    _, digest = memory._load_inventory_manifest("artifact:inventory.json")
    producer = replace(producer, status="done", acceptance=replace(producer.acceptance, manifest_hash="",
        basis=replace(producer.acceptance.basis, snapshot_hash=digest)))
    publish(producer, tmp_path)
    store.store_task("OP_TEST", producer)
    creator = Mock(side_effect=AssertionError("Hypothesis task construction must not invoke an agent"))
    controller = workflow.MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60),
        state_store=workflow.WorkflowStateStore("OP_TEST"), text_runner=creator, structured_runner=creator,
    )
    result = controller._create_tasks(plan, plan.phases[-1])
    assert result.created_count == 1
    assert result.attempts == 0
    task = next(task for task in store.get_tasks("OP_TEST") if task.phase == phase_id)
    assert len(task.recovery_context["hypothesis_ids"]) == 1
    assert task.recovery_context["authentication_requirement"] == {"comparison": "none", "roles": []}
    assert workflow.authentication_agent_optional_tool_names(task) == []
    assert task.acceptance.basis.snapshot_hash == digest
    assert task.acceptance.basis.item_ids == ("subject",)
    assert task.recovery_context["hypothesis_assignments"][0]["sha256"]
    assert controller._create_tasks(plan, plan.phases[-1]).created_count == 0
    missing_producer = replace(task, recovery_context={
        **task.recovery_context, "hypothesis_source_task_uids": ["unavailable"],
    })
    with pytest.raises(ValueError, match="producer unavailable"):
        controller._hypothesis_consumer_bindings(missing_producer)
    prepared = controller._prepare_hypothesis_consumer(plan, task)
    assert prepared.recovery_context["hypothesis_index"][0]["hypothesis_ids"] == task.recovery_context["hypothesis_ids"]
    publish(producer, tmp_path, [{
        "item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [hypothesis("Changed test")],
    }])
    store.store_task("OP_TEST", producer)
    with pytest.raises(ValueError, match="publication changed"):
        controller._hypothesis_consumer_bindings(task)
    assert controller._prepare_hypothesis_consumer(plan, task) is None
    creator.assert_not_called()
