"""Retained native validation is useful internally and safe to display externally."""

import json

import pytest
from pydantic import BaseModel
from strands.tools.structured_output.structured_output_tool import StructuredOutputTool

from modules.agents.structured_outputs import TaskProposalBatchOutput
from modules.config.models.structured_output import StructuredOutputValidationError, validate_structured_json
from modules.tools.memory import TaskProposal, create_tasks


class Output(BaseModel):
    count: int


def test_valid_native_json():
    assert validate_structured_json(Output, '{"count": 2}').count == 2


@pytest.mark.parametrize("content", ['{"count": "password=super-secret"}', "not-json"])
def test_invalid_native_json_retains_exact_content_without_input_in_diagnostics(content):
    with pytest.raises(StructuredOutputValidationError) as caught:
        validate_structured_json(Output, content)
    error = caught.value
    assert isinstance(error, ValueError)
    assert error.rejected_response == content
    assert error.response_fingerprint
    assert error.failure_fingerprint
    assert all(set(item) == {"loc", "type", "msg"} for item in error.validation_errors)
    assert content not in str(error)
    assert "super-secret" not in str(error)
    assert error.__suppress_context__


def test_response_fingerprints_compare_json_values_and_distinguish_other_payloads():
    error = ValueError("invalid role")
    first = StructuredOutputValidationError(error, '{"role":"invalid", "tasks": []}')
    reordered = StructuredOutputValidationError(error, '{ "tasks": [], "role": "invalid" }')
    different = StructuredOutputValidationError(error, '{"role":"other"}')
    other_failure = StructuredOutputValidationError(ValueError("missing basis"), first.rejected_response)
    assert first.response_fingerprint == reordered.response_fingerprint
    assert first.response_fingerprint != different.response_fingerprint
    assert first.failure_fingerprint != other_failure.failure_fingerprint
    assert StructuredOutputValidationError(error).response_fingerprint is None
    assert StructuredOutputValidationError(error, "").response_fingerprint is not None


def test_exception_text_is_bounded_and_redacted():
    error = StructuredOutputValidationError(ValueError("password=super-secret " + "x" * 2000), "private")
    assert "super-secret" not in str(error)
    assert len(str(error)) < 1100
    assert "truncated" in str(error)


def test_task_role_schema_and_validation_expose_canonical_values():
    role = TaskProposal.model_json_schema()["properties"]["task_role"]
    assert role["enum"] == ["mapping", "synthesis", "direct_single_step"]
    payload = {
        "title": "Generate hypotheses", "objective": "Analyze frozen evidence",
        "snapshot_refs": ["artifact:artifacts/inventory.json"],
        "criteria": [{"description": "Record hypotheses with controls"}],
    }
    for value in role["enum"]:
        assert TaskProposal.model_validate({**payload, "task_role": value}).task_role == value
    assert TaskProposal.model_validate(payload).task_role == "mapping"
    with pytest.raises(StructuredOutputValidationError) as caught:
        validate_structured_json(TaskProposal, json.dumps({**payload, "task_role": "hypothesis"}))
    assert caught.value.validation_errors[0]["loc"] == ["task_role"]
    assert "mapping" in str(caught.value)


def test_sdk_and_create_tasks_wire_schemas_agree_on_planning_roles():
    schema = StructuredOutputTool(TaskProposalBatchOutput).tool_spec["inputSchema"]["json"]
    native_roles = schema["properties"]["tasks"]["items"]["properties"]["task_role"]["enum"]
    tool_schema = create_tasks.tool_spec["inputSchema"]["json"]
    tool_roles = tool_schema["$defs"]["TaskProposal"]["properties"]["task_role"]["enum"]
    assert native_roles == tool_roles == ["mapping", "synthesis", "direct_single_step"]
