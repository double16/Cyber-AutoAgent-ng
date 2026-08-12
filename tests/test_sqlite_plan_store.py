import os
import sqlite3

import pytest

from modules.tools.memory import AcceptanceResult, CoverageResult, OperationPlan, PlanPhase, SQLiteApplicationStore, Task
from tests.helpers.acceptance import make_acceptance


def test_sqlite_plan_store_init(tmp_path):
    db_path = str(tmp_path / "test.db")
    SQLiteApplicationStore(db_path, "target")
    assert os.path.exists(db_path)

    # Check if tables were created
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='plans'")
        assert cursor.fetchone() is not None
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
        assert cursor.fetchone() is not None
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='task_acceptance_memory_publications'"
        )
        assert cursor.fetchone() is not None


def test_plan_phase_finding_candidate_dependency_round_trips():
    phase = PlanPhase.from_obj(
        {
            "id": 2,
            "title": "Candidate correlation",
            "status": "pending",
            "criteria": "Analyze persisted candidates",
            "requires_finding_candidates": True,
        }
    )

    assert phase.requires_finding_candidates is True
    assert PlanPhase.from_obj(phase.to_dict()).requires_finding_candidates is True
    assert PlanPhase.from_obj({"id": 1, "title": "Recon", "status": "pending"}).requires_finding_candidates is False
    with pytest.raises(ValueError, match="requires_finding_candidates"):
        PlanPhase.from_obj({"id": 1, "title": "Recon", "status": "pending", "requires_finding_candidates": "yes"})


def test_sqlite_plan_store_persists_immutable_preflight_results(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "test.db"), "target")
    initial = {
        "target_id": "target-1",
        "target": "service.example",
        "target_type": "network",
        "status": "pass",
        "checks": ["resolve", "tcp_connect"],
        "resolved_addresses": ["8.8.8.8"],
        "has_global_address": True,
        "has_private_or_reserved_address": False,
        "route_reachable": False,
    }
    changed = {**initial, "resolved_addresses": ["127.0.0.1"], "has_global_address": False}

    store.store_preflight_results("op-1", [initial])
    store.store_preflight_results("op-1", [changed])

    assert store.list_preflight_results("op-1")[0]["resolved_addresses"] == ["8.8.8.8"]
    assert store.list_preflight_results("other-operation") == []


def test_sqlite_plan_store_tracks_acceptance_memory_publication(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "test.db"), "target")
    store.store_task(
        "op-1",
        Task(
            task_uid="task-1",
            title="Task",
            objective="Test publication tracking",
            acceptance=make_acceptance("task-1"),
            phase=1,
            status="pending",
        ),
    )

    assert store.has_acceptance_memory_publication("op-1", "task-1", "publication-1") is False

    store.mark_acceptance_memory_published("op-1", "task-1", "publication-1")

    assert store.has_acceptance_memory_publication("op-1", "task-1", "publication-1") is True
    assert store.has_acceptance_memory_publication("op-1", "task-1", "publication-2") is False

    store.mark_acceptance_memory_published("op-1", "task-1", "publication-2")

    assert store.has_acceptance_memory_publication("op-1", "task-1", "publication-2") is True


def test_sqlite_plan_store_plan_operations(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = SQLiteApplicationStore(db_path, "target")
    operation_id = "test-op"

    phases = [
        PlanPhase(id=1, title="Phase 1", status="active", criteria="Criteria 1"),
        PlanPhase(id=2, title="Phase 2", status="pending", criteria="Criteria 2")
    ]
    plan = OperationPlan(
        objective="Test Objective",
        current_phase=1,
        total_phases=2,
        phases=phases,
        constraints=["Use non-destructive checks", "Store artifact evidence"],
        assessment_complete=False
    )

    # Store plan
    store.store_plan(operation_id, plan)

    # Retrieve plan
    retrieved_plan = store.get_plan(operation_id)
    assert retrieved_plan is not None
    assert retrieved_plan.objective == plan.objective
    assert retrieved_plan.current_phase == plan.current_phase
    assert retrieved_plan.constraints == plan.constraints
    assert len(retrieved_plan.phases) == 2
    assert retrieved_plan.phases[0].title == "Phase 1"
    assert retrieved_plan.created_at is not None
    assert retrieved_plan.updated_at is not None

    # Update plan
    updated_phases = [
        PlanPhase(id=1, title="Phase 1", status="done", criteria="Criteria 1"),
        PlanPhase(id=2, title="Phase 2", status="active", criteria="Criteria 2")
    ]
    updated_plan = OperationPlan(
        objective="Updated Objective",
        current_phase=2,
        total_phases=2,
        phases=updated_phases,
        assessment_complete=True,
        created_at=retrieved_plan.created_at
    )
    store.store_plan(operation_id, updated_plan)

    retrieved_updated = store.get_plan(operation_id)
    assert retrieved_updated.objective == "Updated Objective"
    assert retrieved_updated.current_phase == 2
    assert retrieved_updated.assessment_complete is True
    assert retrieved_updated.created_at == retrieved_plan.created_at
    assert retrieved_updated.updated_at > retrieved_plan.updated_at


def test_sqlite_plan_store_task_operations(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = SQLiteApplicationStore(db_path, "target")
    operation_id = "test-op"

    task = Task(
        task_uid="task-1",
        title="Task 1",
        objective="Objective 1",
        acceptance=make_acceptance("task-1"),
        phase=1,
        status="pending",
        kind="finding_validation",
        reference_id="finding-1",
    )

    # Store task
    store.store_task(operation_id, task)

    # Retrieve tasks
    tasks = store.get_tasks(operation_id)
    assert len(tasks) == 1
    assert tasks[0].task_uid == "task-1"
    assert tasks[0].title == "Task 1"
    assert tasks[0].created_at is not None
    assert tasks[0].updated_at is not None
    assert tasks[0].kind == "finding_validation"
    assert tasks[0].reference_id == "finding-1"

    # Update task
    updated_task = Task(
        task_uid="task-1",
        title="Task 1 Updated",
        objective="Objective 1",
        acceptance=task.acceptance,
        phase=1,
        status="active",
        created_at=tasks[0].created_at
    )
    store.store_task(operation_id, updated_task)

    updated_tasks = store.get_tasks(operation_id)
    assert len(updated_tasks) == 1
    assert updated_tasks[0].title == "Task 1 Updated"
    assert updated_tasks[0].status == "active"
    assert updated_tasks[0].created_at == tasks[0].created_at
    assert updated_tasks[0].updated_at > tasks[0].updated_at


def test_sqlite_finding_ledger_operations(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "test.db"), "target")
    store.store_finding_candidate("op", "finding-1", "fingerprint", {"claim": "claim"}, "task-1")
    store.link_finding_source_task("op", "finding-1", "source-task")
    store.link_finding_source_task("op", "finding-1", "source-task")

    record = store.get_finding_by_fingerprint("op", "fingerprint")
    assert record["finding_uid"] == "finding-1"
    assert record["candidate_data"] == {
        "claim": "claim",
        "source_task_uids": ["source-task"],
        "source_task_receipts": [
            {
                "task_uid": "source-task",
                "finding_uid": "finding-1",
                "status": "persisted",
                "evidence_refs": ["finding:finding-1"],
            }
        ],
    }

    store.store_finding_validation("op", "finding-1", {"outcome": "confirmed"})
    store.resolve_finding("op", "finding-1", "verified")

    resolved = store.get_finding("op", "finding-1")
    assert resolved["validation_data"] == {"outcome": "confirmed"}
    assert resolved["resolution"] == "verified"
    assert store.list_findings("op") == [resolved]
    assert store.list_findings("other-operation") == []


def test_sqlite_finding_evidence_receipts_are_operation_and_task_scoped(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "receipt.db"), "target")
    store.store_finding_evidence_receipt(
        "op", "receipt-1", "task-1", "artifact:artifacts/result.txt", "decisive marker", "a" * 64
    )

    assert store.get_finding_evidence_receipts("op", ["receipt-1"]) == [{
        "receipt_uid": "receipt-1",
        "source_task_uid": "task-1",
        "artifact_ref": "artifact:artifacts/result.txt",
        "marker": "decisive marker",
        "artifact_fingerprint": "a" * 64,
    }]
    assert store.get_finding_evidence_receipts("other-operation", ["receipt-1"]) == []


def test_sqlite_objective_validation_ledger_operations(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "objective.db"), "target")
    candidate = {"objective_type": "flag", "candidate_value": "FLAG{abc}"}
    store.store_objective_candidate("op", "candidate-1", "fingerprint", candidate, "task-1")

    assert store.get_objective_candidate_by_fingerprint("op", "fingerprint")["candidate_uid"] == "candidate-1"
    store.store_objective_validation("op", "candidate-1", {"outcome": "confirmed", "confidence": 90})
    store.resolve_objective_candidate("op", "candidate-1", "objective_verified")

    resolved = store.get_objective_candidate("op", "candidate-1")
    assert resolved["validation_data"] == {"outcome": "confirmed", "confidence": 90}
    assert resolved["resolution"] == "objective_verified"
    assert store.list_objective_candidates("op") == [resolved]
    assert store.list_objective_candidates("other-operation") == []
    assert store.get_objective_candidate("op", "missing") is None


def test_sqlite_finding_ledger_persists_one_taxonomy_annotation(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "test.db"), "target")
    store.store_finding_candidate("op", "finding-1", "fingerprint", {"claim": "claim"}, "task-1")
    annotation = {
        "status": "completed",
        "annotated_at": "2026-07-28T00:00:00+00:00",
        "taxonomy": {"cwe": [{"id": "CWE-79"}], "mitre_attack": [], "provenance": {"version": "test"}},
    }

    assert store.update_finding_taxonomy_annotation("op", "finding-1", annotation) is True
    assert store.update_finding_taxonomy_annotation("op", "finding-1", annotation) is False

    candidate = store.get_finding("op", "finding-1")["candidate_data"]
    assert candidate["taxonomy_annotation"] == annotation
    assert candidate["taxonomy"]["cwe"][0]["id"] == "CWE-79"


def test_sqlite_finding_ledger_merges_final_attack_enrichment_without_changing_cwe(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "test.db"), "target")
    store.store_finding_candidate(
        "op",
        "finding-1",
        "fingerprint",
        {
            "claim": "claim",
            "taxonomy": {
                "cwe": [{"id": "CWE-78", "confidence": 0.95}],
                "mitre_attack": [
                    {"id": "T1190", "confidence": 0.80, "evidence": ["artifact:initial.txt"]}
                ],
            },
        },
        "task-1",
    )
    enrichment = {
        "status": "completed",
        "taxonomy": {
            "cwe": [],
            "mitre_attack": [
                {"id": "T1190", "confidence": 0.92, "evidence": ["artifact:final.txt"]},
                {"id": "T1059.004", "confidence": 0.96, "evidence": ["artifact:shell.txt"]},
            ],
            "provenance": {"version": "test"},
        },
    }

    assert store.update_finding_attack_enrichment("op", "finding-1", enrichment) is True
    assert store.update_finding_attack_enrichment("op", "finding-1", enrichment) is False

    candidate = store.get_finding("op", "finding-1")["candidate_data"]
    assert candidate["taxonomy"]["cwe"] == [{"id": "CWE-78", "confidence": 0.95}]
    assert [item["id"] for item in candidate["taxonomy"]["mitre_attack"]] == ["T1059.004", "T1190"]
    assert candidate["taxonomy"]["mitre_attack"][1]["confidence"] == 0.92
    assert candidate["final_attack_enrichment"] == enrichment


def test_sqlite_finding_ledger_allows_failed_attack_enrichment_retry(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "test.db"), "target")
    store.store_finding_candidate("op", "finding-1", "fingerprint", {"claim": "claim"}, "task-1")

    assert store.update_finding_attack_enrichment("op", "finding-1", {"status": "failed"}) is True
    assert store.update_finding_attack_enrichment(
        "op",
        "finding-1",
        {"status": "completed", "taxonomy": {"cwe": [], "mitre_attack": [], "provenance": {}}},
    ) is True

    candidate = store.get_finding("op", "finding-1")["candidate_data"]
    assert candidate["final_attack_enrichment"]["status"] == "completed"


def test_sqlite_plan_store_multiple_tasks(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = SQLiteApplicationStore(db_path, "target")
    operation_id = "test-op"

    for i in range(3):
        task = Task(
            task_uid=f"task-{i}",
            title=f"Task {i}",
            objective=f"Objective {i}",
            acceptance=make_acceptance(f"task-{i}"),
            phase=1,
            status="pending"
        )
        store.store_task(operation_id, task)

    tasks = store.get_tasks(operation_id)
    assert len(tasks) == 3
    uids = {task.task_uid for task in tasks}
    assert uids == {"task-0", "task-1", "task-2"}


def test_sqlite_plan_store_persists_acceptance_results_and_freezes_active_contract(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "acceptance.db"), "target")
    task = Task(
        task_uid="task-acceptance",
        title="Map endpoints",
        objective="Map endpoint manifest",
        acceptance=make_acceptance("endpoint:/login.php"),
        phase=1,
        status="active",
    )
    store.store_task("operation", task)
    store.store_acceptance_results(
        "operation",
        task.task_uid,
        [
            AcceptanceResult(
                criterion_id="endpoint:/login.php",
                status="assessed_negative",
                disposition="no_vulnerability",
                summary="No additional parameters were accepted",
                evidence_refs=["artifact:login-negative.txt"],
                coverage=[
                    CoverageResult(
                        item_id="login-id",
                        status="assessed_negative",
                        evidence_refs=["artifact:login-negative.txt"],
                    )
                ],
            )
        ],
    )

    persisted = store.get_acceptance_results("operation", task.task_uid)[0]
    assert persisted.status == "assessed_negative"
    assert persisted.coverage[0].item_id == "login-id"

    changed = Task(
        task_uid=task.task_uid,
        title=task.title,
        objective=task.objective,
        acceptance=make_acceptance("endpoint:/other.php"),
        phase=1,
        status="active",
    )
    with pytest.raises(ValueError, match="immutable"):
        store.store_task("operation", changed)

    pending = Task(
        task_uid="pending-contract",
        title="Pending task",
        objective="Keep its original contract",
        acceptance=make_acceptance("original"),
        phase=1,
        status="pending",
    )
    store.store_task("operation", pending)
    changed_pending = Task(
        task_uid=pending.task_uid,
        title=pending.title,
        objective=pending.objective,
        acceptance=make_acceptance("replacement"),
        phase=1,
        status="pending",
    )
    with pytest.raises(ValueError, match="immutable"):
        store.store_task("operation", changed_pending)


def test_sqlite_plan_store_get_nonexistent(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = SQLiteApplicationStore(db_path, "target")
    assert store.get_plan("nonexistent") is None
    assert store.get_tasks("nonexistent") == []


def test_sqlite_application_store_scopes_same_operation_to_exact_logical_target(tmp_path):
    db_path = str(tmp_path / "cyber_autoagent.db")
    first = SQLiteApplicationStore(db_path, "https://example.com/first")
    second = SQLiteApplicationStore(db_path, "https://example.com/second")
    operation_id = "OP_SAME"
    first.store_plan(
        operation_id,
        OperationPlan(
            objective="First target",
            current_phase=1,
            total_phases=1,
            phases=[PlanPhase(id=1, title="First", status="pending")],
        ),
    )
    second.store_plan(
        operation_id,
        OperationPlan(
            objective="Second target",
            current_phase=1,
            total_phases=1,
            phases=[PlanPhase(id=1, title="Second", status="pending")],
        ),
    )

    assert first.get_plan(operation_id).objective == "First target"
    assert second.get_plan(operation_id).objective == "Second target"
    assert first.has_operation(operation_id) is True
    assert SQLiteApplicationStore(db_path, "https://example.com/missing").has_operation(operation_id) is False


def _model_metric_row(**overrides):
    row = {
        "provider": "litellm",
        "model": "test-model",
        "context_window_tokens": 48_000,
        "input_tokens": 120,
        "output_tokens": 30,
        "cache_read_tokens": 20,
        "cache_write_tokens": 5,
        "total_tokens": 150,
        "cost": 0.123,
        "inference_time_ms": 456.0,
        "model_calls": 3,
        "correction_loops": 1,
        "efficiency": 75.0,
    }
    return {**row, **overrides}


def test_sqlite_application_store_appends_timestamped_model_metric_captures(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "cyber_autoagent.db"), "https://example.com")
    operation_id = "OP_CONTINUED"
    store.append_operation_model_metrics(
        operation_id,
        "2026-08-06T12:00:00.000001+00:00",
        [_model_metric_row()],
    )
    store.append_operation_model_metrics(
        operation_id,
        "2026-08-06T12:10:00.000001+00:00",
        [_model_metric_row(input_tokens=200, output_tokens=50, total_tokens=250)],
    )

    rows = store.list_operation_model_metrics(operation_id)
    assert [row["captured_at"] for row in rows] == [
        "2026-08-06T12:00:00.000001+00:00",
        "2026-08-06T12:10:00.000001+00:00",
    ]
    assert [row["total_tokens"] for row in rows] == [150, 250]


def test_sqlite_application_store_model_metrics_are_target_scoped_and_validated(tmp_path):
    db_path = str(tmp_path / "cyber_autoagent.db")
    first = SQLiteApplicationStore(db_path, "https://example.com/one")
    second = SQLiteApplicationStore(db_path, "https://example.com/two")
    first.append_operation_model_metrics("OP_1", "2026-08-06T12:00:00.000001+00:00", [_model_metric_row()])

    assert second.list_operation_model_metrics("OP_1") == []
    with pytest.raises(ValueError, match="total_tokens"):
        second.append_operation_model_metrics(
            "OP_1",
            "2026-08-06T12:00:00.000001+00:00",
            [_model_metric_row(total_tokens=1)],
        )
