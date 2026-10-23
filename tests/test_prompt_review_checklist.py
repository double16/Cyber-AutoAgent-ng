"""Bounded prompt review contracts and task-local provenance checks."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from modules.agents.multi_agent_workflow import MultiAgentWorkflowController
from modules.agents.prompt_review import CHECK_CATEGORIES, normalize_task_prompt_checklist
from modules.agents.structured_outputs import TaskPromptChecklistOutput, structured_output_dict
from modules.config.types import BudgetConfig
from tests.test_multi_agent_workflow import FakeState, Task, _plan, _runtime


def passing():
    return {name: {"status": "pass", "issue": None} for name in CHECK_CATEGORIES} | {"repairable": True}


def failing(check="task_coverage", category="objective", ref="criterion:required", excerpt=""):
    data = passing()
    data[check] = {"status": "fail", "issue": {
        "category": category, "message": "Required action is omitted.", "requirement_ref": ref,
        "draft_excerpt": excerpt, "tool_names": ["inspect"] if category == "tool_availability" else [],
    }}
    return data


def test_all_checks_are_required_and_legacy_output_is_not_a_new_review():
    assert normalize_task_prompt_checklist(passing()) == {"issues": [], "repairable": True}
    for name in CHECK_CATEGORIES:
        data = passing()
        del data[name]
        with pytest.raises(ValidationError):
            TaskPromptChecklistOutput.model_validate(data)
    with pytest.raises(ValidationError):
        TaskPromptChecklistOutput.model_validate({"issues": []})
    with pytest.raises(ValidationError):
        TaskPromptChecklistOutput.model_validate(passing() | {"approved": True})


@pytest.mark.parametrize("check,categories", CHECK_CATEGORIES.items())
def test_failed_check_converts_to_legacy_issue_without_losing_category(check, categories):
    for category in categories:
        result = normalize_task_prompt_checklist(
            failing(check, category, excerpt="discard evidence"),
            requirement_refs={"criterion:required"}, draft="Inspect, then discard evidence.",
        )
        assert result["issues"][0]["category"] == category
        assert set(result["issues"][0]) == {"category", "message", "tool_names"}


@pytest.mark.parametrize("status,issue", [("pass", True), ("fail", False), ("maybe", False)])
def test_status_and_issue_must_agree(status, issue):
    data = failing()
    data["task_coverage"]["status"] = status
    if not issue:
        data["task_coverage"]["issue"] = None
    with pytest.raises(ValidationError):
        TaskPromptChecklistOutput.model_validate(data)


@pytest.mark.parametrize("change,error", [
    ({"requirement_ref": "operation_background"}, "non-authoritative"),
    ({"requirement_ref": "invented"}, "non-authoritative"),
    ({"draft_excerpt": "never in the draft"}, "verbatim"),
    ({"message": "   "}, "concrete defect"),
    ({"category": "scope"}, "matching issue category"),
    ({"message": "x" * 501}, "500"),
    ({"draft_excerpt": "x" * 301}, "300"),
])
def test_invalid_provenance_or_defect_is_rejected(change, error):
    data = failing()
    data["task_coverage"]["issue"].update(change)
    with pytest.raises(ValueError, match=error):
        normalize_task_prompt_checklist(data, requirement_refs={"criterion:required"}, draft="Inspect.")


def test_capability_availability_requires_named_tools():
    data = failing("capability_fit", "tool_availability")
    data["capability_fit"]["issue"]["tool_names"] = []
    with pytest.raises(ValueError, match="require tool_names"):
        normalize_task_prompt_checklist(data)


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("defect", ["reference", "excerpt", "missing_check", "status"])
def test_invalid_checklist_gets_complete_repair_in_both_transports(monkeypatch, structured, defect):
    task = Task(task_uid="bounded", title="Map", objective="Map assigned subject", phase=1, status="active")
    draft = "Inspect assigned subject and retain evidence."
    refs = MultiAgentWorkflowController._task_prompt_review_refs(task)
    invalid = failing(ref="task")
    if defect == "reference":
        invalid["task_coverage"]["issue"]["requirement_ref"] = "operation_background"
    elif defect == "excerpt":
        invalid["task_coverage"]["issue"]["draft_excerpt"] = "invented quotation"
    elif defect == "missing_check":
        del invalid["scope"]
    else:
        invalid["task_coverage"]["status"] = "pass"
    outputs = iter([invalid, passing()])
    calls = []

    def runner(role, prompt, tools, system, *extra):
        calls.append(prompt)
        if structured:
            assert extra == (TaskPromptChecklistOutput,)
        output = next(outputs)
        return output if structured else json.dumps(output)

    monkeypatch.setattr("modules.config.models.capabilities.get_model_output_limit", lambda *_a, **_kw: None)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=FakeState(_plan(), [task]),
        text_runner=runner,
    )
    controller.structured_runner = runner if structured else None
    result = controller._run_json_text_agent(
        "task_prompt_critic", "Review assigned task.\n## Proposed task prompt draft\n" + draft, [], "system",
        data_preprocessor=controller._task_prompt_checklist_validator(task, {"prompt": draft}),
    )
    assert result == {"issues": [], "repairable": True}
    assert len(calls) == 2
    assert "complete object" in calls[1]
    assert "Review assigned task" in calls[1]
    assert "operation_background" not in refs


def test_normalization_does_not_mutate_wire_response():
    data = failing(ref="task")
    before = deepcopy(data)
    normalize_task_prompt_checklist(data, requirement_refs={"task"}, draft="Inspect.")
    assert data == before


def test_structured_transport_preserves_required_null_pass_issues():
    wire = structured_output_dict(TaskPromptChecklistOutput.model_validate(passing()))
    assert wire == passing()
    assert normalize_task_prompt_checklist(wire)["issues"] == []


def test_builder_schema_repair_precedes_answer_budget_validation():
    responses = iter(['{"tools":[]}', '{"prompt":"Inspect assigned subject","tools":[]}'])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=FakeState(_plan()),
        text_runner=lambda *_args: next(responses),
    )
    assert controller._run_json_text_agent("task_prompt_builder", "Build", [], "system")["prompt"] == (
        "Inspect assigned subject"
    )
