"""Explicit repair must preserve valid state and isolate the selected operation."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from modules.storage import workflow_repair as repair
from modules.tools.memory import OperationPlan, PlanPhase, SQLiteApplicationStore, Task
from tests.helpers.acceptance import make_acceptance


@pytest.fixture
def workflow(tmp_path):
    database = tmp_path / "workflow.db"
    store = SQLiteApplicationStore(str(database), "target")
    plan = OperationPlan(
        objective="Check scope",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Inspect", status="active")],
    )
    task = Task(
        task_uid="task", title="Task", objective="Check scope", phase=1, status="pending", acceptance=make_acceptance()
    )
    store.store_plan("operation", plan)
    store.store_task("operation", task)
    store.store_plan("other", plan)
    return database, store, plan, task


def damage(database, table="plans", field="plan_data", value="secret-value{"):
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(f"UPDATE {table} SET {field}=? WHERE operation_id='operation'", (value,))


def call(workflow, **kwargs):
    return repair.repair_workflow(workflow[0], "target", "operation", **kwargs)


def test_preview_diagnosis_and_apply_preserve_valid_state(workflow):
    database, store, plan, _task = workflow
    damage(database)
    other_before = store.get_plan("other").to_dict()
    before = database.read_bytes()
    diagnosis = call(workflow)
    assert diagnosis["damaged_records"] == [{"table": "plans", "record_id": "operation", "fields": ["plan_data"]}]
    preview = call(workflow, plan_snapshot=plan.to_dict())
    assert preview["changes"] == [{"table": "plans", "record_id": "operation", "field": "plan_data"}]
    assert database.read_bytes() == before
    result = call(workflow, plan_snapshot=plan.to_dict(), apply=True)
    assert result["applied"] is True
    assert Path(result["backup"]).exists()
    with closing(sqlite3.connect(result["backup"])) as connection:
        assert (
            connection.execute("SELECT plan_data FROM plans WHERE operation_id='operation'").fetchone()[0]
            == "secret-value{"
        )
    assert store.get_plan("operation").to_dict() == plan.to_dict()
    assert store.get_plan("other").to_dict() == other_before


def test_task_repair_only_changes_damaged_fields(workflow):
    _database, store, _plan, task = workflow
    damage(workflow[0], "tasks", "evidence")
    repaired = replace(task, evidence=["artifact:proof.txt"])
    result = call(workflow, task_snapshots=[repaired.to_dict()], apply=True)
    assert result["changes"] == [{"table": "tasks", "record_id": "task", "field": "evidence"}]
    assert store.get_tasks("operation")[0].evidence == ["artifact:proof.txt"]


@pytest.mark.parametrize("case", ["valid", "unknown", "duplicate", "state", "plan_missing", "phase"])
def test_repair_refuses_unsafe_replacements(workflow, case):
    database, _store, _plan, task = workflow
    kwargs = {"task_snapshots": [task.to_dict()]}
    if case != "valid":
        damage(database, "tasks", "evidence")
    if case == "unknown":
        kwargs["task_snapshots"] = [replace(task, task_uid="unknown").to_dict()]
    if case == "duplicate":
        kwargs["task_snapshots"] *= 2
    if case == "state":
        kwargs["task_snapshots"] = [replace(task, status="done").to_dict()]
    if case == "plan_missing":
        damage(database)
    if case == "phase":
        damage(database, "tasks", "phase", 0)
        kwargs["task_snapshots"] = [replace(task, phase=2).to_dict()]
    with pytest.raises(ValueError):
        call(workflow, **kwargs)
    assert not list(database.parent.glob("*.workflow-backup-*.db"))


def test_atomic_rollback_and_backup_on_post_validation_failure(workflow, monkeypatch):
    database, _store, plan, _task = workflow
    damage(database)
    original = repair.inspect_record
    calls = 0

    def fail_post_validation(*args):
        nonlocal calls
        calls += 1
        if calls == 3:
            return {}, ["plan_data"]
        return original(*args)

    monkeypatch.setattr(repair, "inspect_record", fail_post_validation)
    with pytest.raises(ValueError, match="Repair validation failed"):
        call(workflow, plan_snapshot=plan.to_dict(), apply=True)
    with closing(sqlite3.connect(database)) as connection:
        assert (
            connection.execute("SELECT plan_data FROM plans WHERE operation_id='operation'").fetchone()[0]
            == "secret-value{"
        )
    assert len(list(database.parent.glob("*.workflow-backup-*.db"))) == 1


def test_apply_without_replacements_and_unknown_operation(workflow):
    with pytest.raises(ValueError, match="No damaged fields"):
        call(workflow, apply=True)
    with pytest.raises(ValueError, match="Unknown logical"):
        repair.repair_workflow(workflow[0], "target", "missing")


def test_missing_database_is_not_created(tmp_path):
    database = tmp_path / "missing.db"
    with pytest.raises(sqlite3.DatabaseError):
        repair.repair_workflow(database, "target", "operation", apply=True)
    assert not database.exists()


def test_cli_preview_and_safe_failure(workflow, tmp_path, capsys):
    database, _store, plan, _task = workflow
    args = ["--database", str(database), "--logical-target", "target", "--operation-id", "operation"]
    assert repair.main(args) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is False
    damage(database)
    snapshot = tmp_path / "plan.json"
    snapshot.write_text(json.dumps(plan.to_dict()))
    assert repair.main([*args, "--plan-json", str(snapshot)]) == 0
    assert json.loads(capsys.readouterr().out)["changes"]
    snapshot.write_text('{"secret-value":')
    assert repair.main([*args, "--plan-json", str(snapshot)]) == 1
    assert "secret-value" not in capsys.readouterr().out


@pytest.mark.parametrize("value", ["{}", "[1]"])
def test_cli_rejects_invalid_task_file(workflow, tmp_path, capsys, value):
    snapshot = tmp_path / "tasks.json"
    snapshot.write_text(value)
    assert (
        repair.main(
            [
                "--database",
                str(workflow[0]),
                "--logical-target",
                "target",
                "--operation-id",
                "operation",
                "--tasks-json",
                str(snapshot),
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["error_type"] == "WorkflowRepairError"


@pytest.mark.parametrize(
    "target_type,target_value",
    [
        ("network", "https://target.test"),
        ("network", "192.0.2.10"),
        ("filesystem", "/source/repository"),
    ],
)
def test_target_scoped_repair_preserves_protocol_neutral_bindings(workflow, target_type, target_value):
    from modules.tools.memory import OperationTarget

    database, store, plan, task = workflow
    scoped_plan = replace(plan, targets=[OperationTarget("target-1", target_value, target_type)])
    scoped_task = replace(task, target_scope="subset", target_ids=["target-1"])
    store.store_plan("operation", scoped_plan)
    store.store_task("operation", scoped_task)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO operation_preflight_results "
            "(logical_target, operation_id, target_id, target, target_type, status, checks, reason, "
            "resolved_addresses, has_global_address, has_private_or_reserved_address, route_reachable, recorded_at) "
            "VALUES ('target', 'operation', 'target-1', ?, ?, 'pass', '[]', '', '[]', 0, 1, 1, 'now')",
            (target_value, target_type),
        )
    damage(database, "tasks", "evidence")
    result = call(workflow, task_snapshots=[scoped_task.to_dict()], apply=True)
    assert len(result["changes"]) == 1
    assert store.get_tasks("operation")[0].target_ids == ["target-1"]


@pytest.mark.parametrize("target_change", ["value", "removed", "type"])
def test_repair_refuses_plan_target_drift(workflow, target_change):
    from modules.tools.memory import OperationTarget

    database, _store, plan, _task = workflow
    damage(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO operation_preflight_results "
            "(logical_target, operation_id, target_id, target, target_type, status, checks, reason, "
            "resolved_addresses, has_global_address, has_private_or_reserved_address, route_reachable, recorded_at) "
            "VALUES ('target', 'operation', 'target-1', 'https://original.test', 'network', 'pass', "
            "'[]', '', '[]', 0, 1, 1, 'now')"
        )
    target = OperationTarget("target-1", "https://changed.test", "network")
    if target_change == "type":
        target = OperationTarget("target-1", "https://original.test", "filesystem")
    replacement = replace(plan, targets=[] if target_change == "removed" else [target])
    with pytest.raises(ValueError, match="registered target scope"):
        call(workflow, plan_snapshot=replacement.to_dict())


def test_repair_preserves_acceptance_result_bindings(workflow):
    database, _store, _plan, task = workflow
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO task_acceptance_results "
            "(logical_target, operation_id, task_uid, criterion_id, status, disposition, summary, evidence_refs, "
            "coverage, updated_at) VALUES "
            "('target', 'operation', 'task', 'test-outcome', 'satisfied', 'supported', 'Checked', '[]', '{}', 'now')"
        )
    damage(database, "tasks", "acceptance_contract")
    wrong = replace(task, acceptance=make_acceptance("new-criterion"))
    with pytest.raises(ValueError, match="acceptance bindings"):
        call(workflow, task_snapshots=[wrong.to_dict()])
    assert call(workflow, task_snapshots=[task.to_dict()], apply=True)["applied"] is True


def test_diagnosis_identifies_scalar_and_scope_damage(workflow):
    database, _store, _plan, task = workflow
    damage(database, "tasks", "title", "")
    damage(database, "tasks", "phase", 0)
    damage(database, "tasks", "status", "invalid")
    damage(database, "tasks", "target_scope", "subset")
    diagnosis = call(workflow)
    assert diagnosis["damaged_records"][0]["fields"] == ["title", "phase", "status", "target_ids"]
    assert diagnosis["changes"] == []
    with pytest.raises(ValueError, match="valid field"):
        call(workflow, task_snapshots=[task.to_dict()])


def test_cli_applies_task_snapshot(workflow, tmp_path, capsys):
    database, store, _plan, task = workflow
    damage(database, "tasks", "evidence")
    snapshot = tmp_path / "tasks.json"
    snapshot.write_text(json.dumps([task.to_dict()]))
    assert (
        repair.main(
            [
                "--database",
                str(database),
                "--logical-target",
                "target",
                "--operation-id",
                "operation",
                "--tasks-json",
                str(snapshot),
                "--apply",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert store.get_tasks("operation")[0].evidence == []


def test_support_command_entrypoint(workflow, monkeypatch, capsys):
    import runpy
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "repair-workflow-state.py",
            "--database",
            str(workflow[0]),
            "--logical-target",
            "target",
            "--operation-id",
            "operation",
        ],
    )
    with pytest.raises(SystemExit) as caught:
        runpy.run_path("support/repair-workflow-state.py", run_name="__main__")
    assert caught.value.code == 0
    assert json.loads(capsys.readouterr().out)["changes"] == []


def test_backup_with_wal_preserves_original_state_and_sidecars(workflow):
    database, store, plan, _task = workflow
    with closing(sqlite3.connect(database)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        damage(database)
        result = call(workflow, plan_snapshot=plan.to_dict(), apply=True)
        backup = Path(result["backup"])
        assert Path(f"{backup}.source-wal").exists()
        assert Path(f"{backup}.source-shm").exists()
        assert backup.stat().st_mode & 0o777 == 0o600
        assert Path(f"{backup}.source-wal").stat().st_mode & 0o777 == 0o600
        with closing(sqlite3.connect(backup)) as connection:
            assert (
                connection.execute("SELECT plan_data FROM plans WHERE operation_id='operation'").fetchone()[0]
                == "secret-value{"
            )
        assert store.get_plan("operation").objective == plan.objective


def test_importing_support_command_does_not_run_repair(monkeypatch):
    import runpy

    def unexpected_invocation(*_args):
        pytest.fail("Importing the command must not invoke repair")

    monkeypatch.setattr(repair, "main", unexpected_invocation)
    namespace = runpy.run_path("support/repair-workflow-state.py", run_name="workflow_repair_command")
    assert namespace["main"] is unexpected_invocation
