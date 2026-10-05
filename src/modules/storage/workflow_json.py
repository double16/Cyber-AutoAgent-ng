"""Strict decoding of persisted workflow JSON, with payload-free diagnostics."""

import json
from collections.abc import Callable
from typing import Any


class StoredWorkflowDataError(ValueError):
    """A persisted workflow record is present but cannot be trusted."""

    def __init__(self, table: str, operation_id: str, record_id: str, field: str):
        self.table = table
        self.operation_id = operation_id
        self.record_id = record_id
        self.field = field
        super().__init__(
            f"Invalid stored workflow data: table={table} operation={operation_id} "
            f"record={record_id} field={field}. Run support/repair-workflow-state.py "
            "with the database, logical target, and operation ID to diagnose and explicitly repair it."
        )


def decode_workflow_json(
    raw: Any,
    expected_type: type,
    *,
    table: str,
    operation_id: str,
    record_id: str,
    field: str,
    optional: bool = False,
) -> Any:
    """Default absent optional fields; reject malformed or incorrectly typed values."""

    if optional and (raw is None or (isinstance(raw, str) and not raw.strip())):
        return expected_type()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise StoredWorkflowDataError(table, operation_id, record_id, field) from None
    if not isinstance(value, expected_type) or (
        expected_type is list and any(not isinstance(item, str) for item in value)
    ):
        raise StoredWorkflowDataError(table, operation_id, record_id, field) from None
    return value


def validate_workflow_model(factory: Callable[[Any], Any], value: Any, **identity: str) -> Any:
    """Translate model validation errors without exposing sensitive stored content."""

    try:
        return factory(value)
    except (TypeError, ValueError, KeyError, OverflowError):
        raise StoredWorkflowDataError(**identity) from None
