"""Offline, operation-scoped repair of damaged workflow records from validated snapshots."""

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.storage.workflow_json import StoredWorkflowDataError
from modules.tools.memory import OperationPlan, SQLiteApplicationStore, Task


class WorkflowRepairError(ValueError):
    """An actionable repair refusal whose message contains no payload values."""


TASK_JSON_FIELDS = {
    "acceptance_contract": "acceptance",
    "evidence": "evidence",
    "supersedes_criteria": "supersedes_criteria",
    "recovery_context": "recovery_context",
    "auth_context": "auth_context",
    "target_ids": "target_ids",
}
TASK_SCALAR_FIELDS = ("title", "objective", "phase", "status", "target_scope")
TASK_STATUSES = {"active", "pending", "done", "partial_failure", "blocked", "superseded", "replanned"}


def _scalar_valid(field: str, value: Any) -> bool:
    if field in {"phase", "current_phase", "total_phases"}:
        return isinstance(value, int) and value > 0
    if field == "status":
        return value in TASK_STATUSES
    if field == "target_scope":
        return value in {"all", "subset"}
    if field == "assessment_complete":
        return value in {0, 1}
    return isinstance(value, str) and bool(value.strip())


def inspect_record(row: sqlite3.Row, table: str, operation_id: str) -> tuple[dict[str, Any], list[str]]:
    """Return decoded fields and all damaged field names without exposing payloads."""

    decoded = dict(row)
    damaged = []
    fields = TASK_JSON_FIELDS if table == "tasks" else {"plan_data": "plan_data"}
    for field in fields:
        try:
            if table == "plans":
                decoded[field] = SQLiteApplicationStore._decode_plan(row[field], operation_id).to_dict()
            else:
                value = SQLiteApplicationStore._decode_task_field(row[field], field, operation_id, row["task_uid"])
                decoded[field] = value.to_dict() if field == "acceptance_contract" else value
        except StoredWorkflowDataError:
            damaged.append(field)
    scalars = (
        TASK_SCALAR_FIELDS
        if table == "tasks"
        else ("objective", "current_phase", "total_phases", "assessment_complete")
    )
    damaged.extend(field for field in scalars if not _scalar_valid(field, row[field]))
    if table == "tasks" and row["target_scope"] == "subset" and not decoded.get("target_ids"):
        if "target_ids" not in damaged:
            damaged.append("target_ids")
    return decoded, damaged


def _snapshot_fields(table: str, snapshot: dict[str, Any], operation_id: str) -> dict[str, Any]:
    if table == "plans":
        plan = SQLiteApplicationStore._decode_plan(json.dumps(snapshot), operation_id)
        value = plan.to_dict()
        return {
            "plan_data": value,
            **{key: value[key] for key in ("objective", "current_phase", "total_phases", "assessment_complete")},
        }
    task = Task.from_obj(snapshot)
    values = task.to_dict()
    fields = {key: values[key] for key in TASK_SCALAR_FIELDS}
    for column, key in TASK_JSON_FIELDS.items():
        raw = json.dumps(snapshot.get(key, {} if column.endswith("context") else []))
        if column == "acceptance_contract":
            raw = json.dumps(snapshot.get("acceptance"))
        SQLiteApplicationStore._decode_task_field(raw, column, operation_id, task.task_uid)
        fields[column] = values[key]
    return fields


def repair_workflow(
    database: Path,
    logical_target: str,
    operation_id: str,
    *,
    plan_snapshot: dict[str, Any] | None = None,
    task_snapshots: list[dict[str, Any]] | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or transactionally repair damaged fields; never initialize or recover the database."""

    snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    if plan_snapshot is not None:
        snapshots[("plans", operation_id)] = _snapshot_fields("plans", plan_snapshot, operation_id)
    for snapshot in task_snapshots or []:
        task_uid = snapshot.get("task_uid")
        key = ("tasks", task_uid)
        if key in snapshots:
            raise WorkflowRepairError("Duplicate replacement task ID")
        snapshots[key] = _snapshot_fields("tasks", snapshot, operation_id)
    mode = "rw" if apply else "ro"
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode={mode}", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise WorkflowRepairError("SQLite integrity check failed; repair requires a structurally valid database")
        if not connection.execute(
            "SELECT 1 FROM operations WHERE logical_target=? AND operation_id=?", (logical_target, operation_id)
        ).fetchone():
            raise WorkflowRepairError("Unknown logical target or operation ID")
        backup = None
        if apply:
            connection.execute("BEGIN IMMEDIATE")
        try:
            changes = []
            diagnostics = []
            found_keys = set()
            repaired_plan = None
            decoded_tasks = []
            for table in ("plans", "tasks"):
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE logical_target=? AND operation_id=?",
                    (logical_target, operation_id),
                ).fetchall()
                for row in rows:
                    record_id = operation_id if table == "plans" else row["task_uid"]
                    key = (table, record_id)
                    found_keys.add(key)
                    decoded, damaged = inspect_record(row, table, operation_id)
                    if damaged:
                        diagnostics.append({"table": table, "record_id": record_id, "fields": damaged})
                    replacement = snapshots.get(key)
                    if replacement is not None:
                        if not damaged:
                            raise WorkflowRepairError("Replacement targets a valid record")
                        for field, value in replacement.items():
                            if field not in damaged and value != decoded[field]:
                                raise WorkflowRepairError(f"Replacement changes a valid field: {table}.{field}")
                        for field in damaged:
                            value = replacement[field]
                            decoded[field] = value
                            stored_value = json.dumps(value) if field in {"plan_data", *TASK_JSON_FIELDS} else value
                            changes.append((table, record_id, field, stored_value))
                    if table == "plans" and (not damaged or replacement is not None):
                        repaired_plan = OperationPlan.from_obj(decoded["plan_data"])
                    if table == "tasks" and (not damaged or replacement is not None):
                        task_data = dict(decoded)
                        task_data["acceptance"] = task_data.pop("acceptance_contract")
                        decoded_tasks.append(Task.from_obj(task_data))
            if set(snapshots) - found_keys:
                raise WorkflowRepairError("Replacement targets an unknown record")
            if snapshots and repaired_plan is None:
                raise WorkflowRepairError("A valid plan is required before repairing tasks")
            if repaired_plan is not None and snapshots:
                phases = {phase.id for phase in repaired_plan.phases}
                targets = {target.target_id for target in repaired_plan.targets}
                for task in decoded_tasks:
                    result_ids = {
                        row[0]
                        for row in connection.execute(
                            "SELECT criterion_id FROM task_acceptance_results "
                            "WHERE logical_target=? AND operation_id=? AND task_uid=?",
                            (logical_target, operation_id, task.task_uid),
                        )
                    }
                    if not result_ids.issubset({criterion.id for criterion in task.acceptance.criteria}):
                        raise WorkflowRepairError("Replacement changes persisted acceptance bindings")
                    if task.phase not in phases or not set(task.target_ids).issubset(targets):
                        raise WorkflowRepairError("Replacement violates existing phase or target bindings")
                preflight = connection.execute(
                    "SELECT target_id, target, target_type FROM operation_preflight_results "
                    "WHERE logical_target=? AND operation_id=?",
                    (logical_target, operation_id),
                ).fetchall()
                registered = {row["target_id"]: (row["target"], row["target_type"]) for row in preflight}
                replacement_targets = {
                    target.target_id: (target.value, target.type) for target in repaired_plan.targets
                }
                if registered and registered != replacement_targets:
                    raise WorkflowRepairError("Replacement plan changes registered target scope")
            if apply:
                if not changes:
                    raise WorkflowRepairError("No damaged fields have validated replacements")
                backup = database.with_name(f"{database.stem}.workflow-backup-{datetime.now():%Y%m%dT%H%M%S%f}.db")
                with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as source:
                    with closing(sqlite3.connect(backup)) as destination:
                        source.backup(destination)
                backup.chmod(0o600)
                for suffix in ("-wal", "-shm", "-journal"):
                    sidecar = Path(f"{database}{suffix}")
                    if sidecar.exists():
                        copied = Path(f"{backup}.source{suffix}")
                        shutil.copy2(sidecar, copied)
                        copied.chmod(0o600)
                for table, record_id, field, value in changes:
                    key_column = "operation_id" if table == "plans" else "task_uid"
                    connection.execute(
                        f"UPDATE {table} SET {field}=? WHERE logical_target=? AND operation_id=? AND {key_column}=?",
                        (value, logical_target, operation_id, record_id),
                    )
                for table, record_id in snapshots:
                    key_column = "operation_id" if table == "plans" else "task_uid"
                    row = connection.execute(
                        f"SELECT * FROM {table} WHERE logical_target=? AND operation_id=? AND {key_column}=?",
                        (logical_target, operation_id, record_id),
                    ).fetchone()
                    if inspect_record(row, table, operation_id)[1]:
                        raise WorkflowRepairError("Repair validation failed")
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise WorkflowRepairError("Repair failed the SQLite integrity check")
                connection.commit()
            return {
                "operation_id": operation_id,
                "logical_target": logical_target,
                "applied": apply,
                "backup": str(backup) if backup else None,
                "damaged_records": diagnostics,
                "changes": [{"table": t, "record_id": r, "field": f} for t, r, f, _ in changes],
            }
        except Exception:
            connection.rollback()
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--logical-target", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--plan-json", type=Path)
    parser.add_argument("--tasks-json", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = json.loads(args.plan_json.read_text()) if args.plan_json else None
        tasks = json.loads(args.tasks_json.read_text()) if args.tasks_json else None
        if tasks is not None and (not isinstance(tasks, list) or any(not isinstance(t, dict) for t in tasks)):
            raise WorkflowRepairError("Replacement tasks must be an array of task objects")
        result = repair_workflow(
            args.database,
            args.logical_target,
            args.operation_id,
            plan_snapshot=plan,
            task_snapshots=tasks,
            apply=args.apply,
        )
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.DatabaseError) as error:
        # Replacement files and database values are sensitive; emit identifiers only.
        message = (
            str(error)
            if isinstance(error, (WorkflowRepairError, StoredWorkflowDataError))
            else "Workflow repair failed"
        )
        print(json.dumps({"error": message, "error_type": type(error).__name__}))
        return 1
