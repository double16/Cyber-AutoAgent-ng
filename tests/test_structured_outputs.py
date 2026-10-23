import pytest
from pydantic import ValidationError

from modules.agents.structured_outputs import (
    ReportNextStepsOutput,
    TaskProposalBatchOutput,
    WorkflowDecisionOutput,
    is_structured_output_unavailable,
    structured_output_dict,
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


def test_structured_output_unavailable_classifier_follows_exception_chains():
    unavailable = NotImplementedError("transport unavailable")
    wrapped = RuntimeError("outer error")
    wrapped.__cause__ = unavailable

    assert is_structured_output_unavailable(wrapped)
    assert is_structured_output_unavailable(AttributeError("missing structured API"))
    assert is_structured_output_unavailable(RuntimeError("structured output is not supported"))


def test_structured_output_dict_accepts_models_and_dicts_but_rejects_text():
    model = WorkflowDecisionOutput(status="complete", repair=None)

    assert structured_output_dict(model) == {"status": "complete"}
    assert structured_output_dict({"status": "complete"}) == {"status": "complete"}
    with pytest.raises(ValueError, match="Pydantic model or dict"):
        structured_output_dict("complete")


@pytest.mark.parametrize("role", ["authentication_flow_critic", "registration_flow_critic"])
def test_flow_discovery_critique_schema_advertises_supporting_selection(role):
    from modules.agents.structured_outputs import WORKFLOW_OUTPUT_MODELS, FlowCritiqueOutput

    model = WORKFLOW_OUTPUT_MODELS[role]
    assert model is FlowCritiqueOutput
    assert "supporting_evidence_refs" in model.model_json_schema()["properties"]
    value = model(approved=True, feedback=[], supporting_evidence_refs=["artifact:form.html"])
    assert structured_output_dict(value)["supporting_evidence_refs"] == ["artifact:form.html"]
    with pytest.raises(ValidationError):
        model(approved=True, supporting_evidence_refs=[7])
    with pytest.raises(ValidationError):
        model(approved=True, unrelated_field=True)


def test_general_critique_schema_does_not_accept_discovery_selection():
    from modules.agents.structured_outputs import WORKFLOW_OUTPUT_MODELS

    for role in ("plan_critic", "task_prompt_critic"):
        with pytest.raises(ValidationError):
            WORKFLOW_OUTPUT_MODELS[role](approved=True, supporting_evidence_refs=["artifact:form.html"])


def test_evaluator_receipt_gaps_are_typed_separately_from_semantic_gaps():
    from modules.agents.structured_outputs import EvaluatorRepairOutput

    repair = EvaluatorRepairOutput(kind="execution", execution_receipt_gaps=["requirement-1"],
                                   evidence_gaps=["Unsupported comparison"])
    assert repair.execution_receipt_gaps == ["requirement-1"]
    assert repair.evidence_gaps == ["Unsupported comparison"]
    assert "execution_receipt_gaps" in EvaluatorRepairOutput.model_json_schema()["properties"]
    with pytest.raises(ValidationError):
        EvaluatorRepairOutput(execution_receipt_gaps=[123])


@pytest.mark.parametrize("value", [None, "", "Keep meaningful feedback"])
def test_review_instructions_normalize_nullable_wire_values(value):
    from modules.agents.structured_outputs import TaskReviewDecisionOutput

    model = TaskReviewDecisionOutput.model_validate({"status": "done", "instructions": value})
    assert model.instructions == ("" if value is None else value)
    assert TaskReviewDecisionOutput.model_validate({"status": "done"}).instructions == ""


@pytest.mark.parametrize("value", [False, 12, [], {}])
def test_review_instructions_reject_non_string_values(value):
    from modules.agents.structured_outputs import TaskReviewDecisionOutput

    with pytest.raises(ValidationError):
        TaskReviewDecisionOutput.model_validate({"status": "done", "instructions": value})


@pytest.mark.parametrize("fault", ["unknown", "empty_reason", "long_reason", "missing_check"])
def test_semantic_checks_are_closed_and_bounded(fault):
    from modules.agents.structured_outputs import HypothesisSemanticChecks

    checks = {key: {"verdict": "adequate", "reason": "Grounded proposal"}
              for key in ("grounding", "test_discrimination", "identity_requirements")}
    if fault == "unknown":
        checks["grounding"]["verdict"] = "probably"
    elif fault == "empty_reason":
        checks["grounding"]["reason"] = ""
    elif fault == "long_reason":
        checks["grounding"]["reason"] = "x" * 501
    else:
        del checks["grounding"]
    with pytest.raises(ValidationError):
        HypothesisSemanticChecks.model_validate(checks)
