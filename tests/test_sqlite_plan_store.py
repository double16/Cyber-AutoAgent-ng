import os
import sqlite3
from modules.tools.memory import PlanStore, OperationPlan, PlanPhase, Task


def test_sqlite_plan_store_init(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = PlanStore(db_path)
    assert os.path.exists(db_path)

    # Check if tables were created
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='plans'")
        assert cursor.fetchone() is not None
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
        assert cursor.fetchone() is not None


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
        assessment_complete=False
    )

    # Store plan
    store.store_plan(operation_id, plan)

    # Retrieve plan
    retrieved_plan = store.get_plan(operation_id)
    assert retrieved_plan is not None
    assert retrieved_plan.objective == plan.objective
    assert retrieved_plan.current_phase == plan.current_phase
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
        phase=1,
        status="pending"
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

    # Update task
    updated_task = Task(
        task_uid="task-1",
        title="Task 1 Updated",
        objective="Objective 1",
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


def test_sqlite_plan_store_multiple_tasks(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = PlanStore(db_path)
    operation_id = "test-op"

    for i in range(3):
        task = Task(
            task_uid=f"task-{i}",
            title=f"Task {i}",
            objective=f"Objective {i}",
            phase=1,
            status="pending"
        )
        store.store_task(operation_id, task)

    tasks = store.get_tasks(operation_id)
    assert len(tasks) == 3
    uids = {t.task_uid for t in tasks}
    assert uids == {"task-0", "task-1", "task-2"}


def test_sqlite_plan_store_get_nonexistent(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = PlanStore(db_path)
    assert store.get_plan("nonexistent") is None
    assert store.get_tasks("nonexistent") == []
