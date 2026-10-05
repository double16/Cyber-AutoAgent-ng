"""Persisted workflow JSON must not confuse missing data with damaged data."""

import pytest

from modules.storage.workflow_json import (
    StoredWorkflowDataError,
    decode_workflow_json,
    validate_workflow_model,
)

IDENTITY = dict(table="tasks", operation_id="operation", record_id="task", field="evidence")


@pytest.mark.parametrize("raw", [None, "", " \n"])
@pytest.mark.parametrize("expected", [list, dict])
def test_optional_json_defaults(raw, expected):
    assert decode_workflow_json(raw, expected, optional=True, **IDENTITY) == expected()


@pytest.mark.parametrize("raw", [None, "", "secret-value{", "null", "{}", "[1]", '["ok", null]'])
def test_required_json_rejects_invalid_values_without_payload(raw):
    with pytest.raises(StoredWorkflowDataError) as caught:
        decode_workflow_json(raw, list, **IDENTITY)
    assert caught.value.table == "tasks"
    assert caught.value.operation_id == "operation"
    assert caught.value.record_id == "task"
    assert caught.value.field == "evidence"
    assert "secret-value" not in str(caught.value)


@pytest.mark.parametrize("raw,expected,value", [("[]", list, []), ('["ref"]', list, ["ref"]), ("{}", dict, {})])
def test_json_valid_values(raw, expected, value):
    assert decode_workflow_json(raw, expected, **IDENTITY) == value


@pytest.mark.parametrize("error", [ValueError, TypeError, KeyError, OverflowError])
def test_model_validation_errors_are_safe(error):
    def fail(_value):
        raise error("secret-value")

    with pytest.raises(StoredWorkflowDataError, match="field=evidence") as caught:
        validate_workflow_model(fail, {}, **IDENTITY)
    assert "secret-value" not in str(caught.value)


def test_model_validation_success():
    assert validate_workflow_model(dict, {"ok": True}, **IDENTITY) == {"ok": True}
