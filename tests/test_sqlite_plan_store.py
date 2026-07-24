import os
import sqlite3

import pytest

from modules.tools.memory import AcceptanceResult, CoverageResult, OperationPlan, PlanPhase, PlanStore, Task
from tests.helpers.acceptance import make_acceptance


def test_sqlite_plan_store_init(tmp_path):
    db_path = str(tmp_path / "test.db")
    PlanStore(db_path)
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


def test_sqlite_plan_store_tracks_acceptance_memory_publication(tmp_path):
    store = PlanStore(str(tmp_path / "test.db"))

    assert store.has_acceptance_memory_publication("op-1", "task-1", "publication-1") is False

    store.mark_acceptance_memory_published("op-1", "task-1", "publication-1")

    assert store.has_acceptance_memory_publication("op-1", "task-1", "publication-1") is True
    assert store.has_acceptance_memory_publication("op-1", "task-1", "publication-2") is False

    store.mark_acceptance_memory_published("op-1", "task-1", "publication-2")

    assert store.has_acceptance_memory_publication("op-1", "task-1", "publication-2") is True


def test_sqlite_plan_store_plan_operations(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = PlanStore(db_path)
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
    store = PlanStore(db_path)
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
    store = PlanStore(str(tmp_path / "test.db"))
    store.store_finding_candidate("op", "finding-1", "fingerprint", {"claim": "claim"}, "task-1")
    store.link_finding_source_task("op", "finding-1", "source-task")
    store.link_finding_source_task("op", "finding-1", "source-task")

    record = store.get_finding_by_fingerprint("op", "fingerprint")
    assert record["finding_uid"] == "finding-1"
    assert record["candidate_data"] == {"claim": "claim", "source_task_uids": ["source-task"]}

    store.store_finding_validation("op", "finding-1", {"outcome": "confirmed"})
    store.resolve_finding("op", "finding-1", "verified")

    resolved = store.get_finding("op", "finding-1")
    assert resolved["validation_data"] == {"outcome": "confirmed"}
    assert resolved["resolution"] == "verified"
    assert store.list_findings("op") == [resolved]
    assert store.list_findings("other-operation") == []


def test_sqlite_plan_store_multiple_tasks(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = PlanStore(db_path)
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
    store = PlanStore(str(tmp_path / "acceptance.db"))
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
    store = PlanStore(db_path)
    assert store.get_plan("nonexistent") is None
    assert store.get_tasks("nonexistent") == []
