import pytest
from pydantic import ValidationError

from modules.agents.structured_outputs import (
    ReportNextStepsOutput,
    TaskProposalBatchOutput,
    WorkflowDecisionOutput,
    is_structured_output_unavailable,
)


def test_task_proposal_batch_schema_accepts_canonical_procedure():
    output = TaskProposalBatchOutput.model_validate(
        {
            "tasks": [
                {
                    "title": "Inspect route",
                    "objective": "Inspect the assigned route",
                    "methods": ["request"],
                    "limits": {"max_requests": 5},
                    "snapshot_refs": [],
                    "criteria": [{"description": "Store the bounded response evidence"}],
                }
            ]
        }
    )

    assert output.tasks[0].limits.max_requests == 5
    assert output.tasks[0].output_kind == "artifact"


def test_task_proposal_batch_schema_accepts_case_only_key_variants_recursively():
    output = TaskProposalBatchOutput.model_validate(
        {
            "Tasks": [
                {
                    "Title": "Inspect route",
                    "Objective": "Inspect the assigned route",
                    "Methods": ["request"],
                    "Limits": {"MAX_REQUESTS": 5},
                    "Snapshot_Refs": [],
                    "Criteria": [{"Description": "Store the bounded response evidence"}],
                }
            ]
        }
    )

    assert output.tasks[0].title == "Inspect route"
    assert output.tasks[0].limits.max_requests == 5


def test_task_proposal_batch_schema_rejects_case_collisions_with_different_values():
    with pytest.raises(ValidationError, match="conflicting JSON keys after lowercasing"):
        TaskProposalBatchOutput.model_validate(
            {
                "tasks": [],
                "Tasks": [
                    {
                        "title": "Inspect route",
                        "objective": "Inspect the assigned route",
                        "criteria": [{"description": "Store evidence"}],
                    }
                ],
            }
        )


def test_task_proposal_batch_schema_rejects_unknown_fields_and_mixed_basis():
    with pytest.raises(ValidationError):
        TaskProposalBatchOutput.model_validate(
            {
                "tasks": [
                    {
                        "title": "Invalid",
                        "objective": "Mix basis modes",
                        "methods": ["request"],
                        "limits": {"max_requests": 5},
                        "snapshot_refs": ["artifact:artifacts/inventory.json"],
                        "criteria": [{"description": "Invalid mixed basis"}],
                        "unexpected": True,
                    }
                ]
            }
        )


def test_workflow_decision_schema_enforces_nested_types():
    output = WorkflowDecisionOutput.model_validate(
        {
            "status": "partial_failure",
            "reason": "Evidence gap",
            "repair": {"kind": "execution", "evidence_gaps": ["Missing response"]},
            "finding_recommendation": {"required": False, "confidence": 0.2, "reason": "Expected behavior"},
        }
    )

    assert output.repair is not None
    assert output.repair.kind == "execution"
    with pytest.raises(ValidationError):
        WorkflowDecisionOutput.model_validate(
            {
                "status": "done",
                "reason": "Complete",
                "finding_recommendation": {"required": True, "confidence": 2, "reason": "invalid"},
            }
        )


def test_report_next_steps_schema_rejects_non_positive_recommendation():
    with pytest.raises(ValidationError):
        ReportNextStepsOutput.model_validate(
            {
                "budget_recommendations": [
                    {
                        "dimension": "duration",
                        "current": 60,
                        "recommended": 0,
                        "rationale": "Invalid",
                    }
                ]
            }
        )


def test_structured_output_unavailable_classifier_recognizes_strands_and_preserves_operational_errors():
    strands_error = type("StructuredOutputException", (RuntimeError,), {})("forced tool was ignored")

    assert is_structured_output_unavailable(strands_error)
    assert is_structured_output_unavailable(RuntimeError("ToolChoice is not supported by this provider"))
    assert not is_structured_output_unavailable(ConnectionError("connection refused"))
    assert not is_structured_output_unavailable(ValidationError.from_exception_data("Output", []))
