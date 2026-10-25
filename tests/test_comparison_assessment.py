"""Comparison semantics remain distinct from execution proof and response availability."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from modules.agents import multi_agent_workflow as workflow
from modules.agents.structured_outputs import WorkflowDecisionOutput
from modules.tools.memory import AcceptanceResult, OperationTarget
from tests.helpers.comparison import operation_support
from tests.test_worker_flows import _use_sqlite_workflow_state
from tests.test_worker_flows import worker_case as worker_case


@pytest.mark.parametrize("interface,outcome,body", [
    ("http", "inconclusive", "Both requests returned 400: Invalid parameter path"),
    ("network", "inconclusive", "Both identities used an unsupported service command"),
    ("filesystem", "inconclusive", "Both identities supplied invalid arguments to the file operation"),
    ("http", "supported", "Known valid resource: owner allowed; other account denied"),
    ("network", "supported", "Valid command: allowed for one identity, denied for the other"),
    ("filesystem", "supported", "Known existing file: owner can read, other identity is denied"),
    ("http", "supported", "Known existing resource: explicit policy denies both accounts"),
    ("http", "supported", "Known existing resource: policy conceals it with 404 for the second account"),
])
def test_comparison_assessment_controls_completion_with_production_state(
    worker_case, monkeypatch, tmp_path, interface, outcome, body,
):
    controller, task, store, target, _observed = worker_case
    value = target if interface == "http" else "192.0.2.1:22" if interface == "network" else str(tmp_path / "project")
    store.store_plan("OP_AUTH", replace(store.get_plan("OP_AUTH"), targets=[OperationTarget(
        "target", value, "filesystem" if interface == "filesystem" else "network",
    )]))
    evidence = tmp_path / "comparison.txt"
    evidence.write_text(body)
    reference = f"artifact:{evidence}"
    store.patch_task("OP_AUTH", task.task_uid, recovery_context_updates={
        "authentication_requirement": {"comparison": "account"},
    })
    result = AcceptanceResult(
        criterion_id=task.acceptance.criteria[0].id, status="satisfied", disposition="observation",
        summary="Both identities exercised the assigned operation", evidence_refs=[reference],
    )
    store.store_acceptance_results("OP_AUTH", task.task_uid, [result])
    _use_sqlite_workflow_state(controller, store, monkeypatch)
    task = controller.state.list_tasks()[0]
    reason = "Suitable operation and compared identities are evidenced" if outcome == "supported" else body
    assessment = {"operation_support": operation_support(outcome, [reference] if outcome == "supported" else []),
        "outcome": outcome, "operation_evidence_refs": [reference] if outcome == "supported" else [],
        "comparison_evidence_refs": [reference], "reason": reason,
    }
    prompts = []

    def evaluator(_role, prompt, _tools, _system):
        prompts.append(prompt)
        return json.dumps({"status": "done", "reason": "Recorded acceptance is satisfied",
                           "comparison_assessment": assessment})

    controller.text_runner = evaluator
    decision = controller._evaluate_task(store.get_plan("OP_AUTH"), store.get_plan("OP_AUTH").phases[0], task)
    assert decision.status == ("done" if outcome == "supported" else "partial_failure")
    assert "Required comparison assessment" in prompts[0]
    assert "Matching invalid-input or generic error responses" in prompts[0]
    if outcome == "inconclusive":
        assert decision.repair_kind == "execution" and reason in decision.unresolved_evidence_gaps
        assert "within the frozen scope" in decision.instructions
    saved = store.get_tasks("OP_AUTH")[0].recovery_context["comparison_assessment"]
    assert saved["outcome"] == outcome and saved["operation_id"] == "OP_AUTH"
    assert store.get_acceptance_results("OP_AUTH", task.task_uid)[0].status == "satisfied"


@pytest.mark.parametrize("change", [
    "missing", "invalid_outcome", "empty_reason", "unknown_ref", "missing_operation", "missing_comparison",
])
def test_comparison_schema_and_scope_reject_unsupported_payload(change):
    reference = "artifact:artifacts/current-response.txt"
    assessment = {
        "operation_support": operation_support("supported", [reference]), "outcome": "supported", "operation_evidence_refs": [reference],
                  "comparison_evidence_refs": [reference], "reason": "Valid operation and access comparison"}
    if change == "invalid_outcome":
        assessment["outcome"] = "maybe"
    elif change == "empty_reason":
        assessment["reason"] = " "
    elif change == "unknown_ref":
        assessment["comparison_evidence_refs"] = ["artifact:artifacts/another-task.txt"]
    elif change == "missing_operation":
        assessment["operation_evidence_refs"] = []
    elif change == "missing_comparison":
        assessment["comparison_evidence_refs"] = []
    data = {"status": "done"}
    if change != "missing":
        data["comparison_assessment"] = assessment
    with pytest.raises(ValueError):
        workflow.MultiAgentWorkflowController._validate_task_evaluator_decision_payload(
            data, allowed=("done", "partial_failure", "blocked"), comparison_required=True,
            comparison_evidence_refs=[reference],
        )


@pytest.mark.parametrize("status", ["done", "blocked"])
def test_missing_comparison_assessment_cannot_complete_or_crash_evaluation(worker_case, monkeypatch, status):
    controller, task, store, _target, _observed = worker_case
    store.patch_task("OP_AUTH", task.task_uid, recovery_context_updates={"authentication_matrix": {"version": 1}})
    _use_sqlite_workflow_state(controller, store, monkeypatch)
    task = controller.state.list_tasks()[0]
    # Exercise handling of a bounded evaluator fallback, which carries no semantic judgment.
    monkeypatch.setattr(controller, "_run_json_text_agent", lambda *_args, **_kwargs: {"status": status})
    decision = controller._evaluate_task(store.get_plan("OP_AUTH"), store.get_plan("OP_AUTH").phases[0], task)
    assert decision.status == "partial_failure"
    assert "evaluator output invalid" in decision.reason
    assert decision.repair_kind == "none"
    assert "comparison_assessment" not in store.get_tasks("OP_AUTH")[0].recovery_context


def test_comparison_detection_uses_contract_and_metadata_not_titles(worker_case, monkeypatch):
    controller, task, store, _target, _observed = worker_case
    phase = store.get_plan("OP_AUTH").phases[0]
    task = replace(task, title="Compare access between identities")
    assert not controller._requires_comparison_assessment(phase, task)
    task = replace(task, recovery_context={"authentication_requirement": {"comparison": "none"}})
    assert not controller._requires_comparison_assessment(phase, task)
    task.recovery_context["authentication_matrix"] = {"version": 1}
    assert controller._requires_comparison_assessment(phase, task)
    task = replace(task, recovery_context={"phase_task_contract": {"workstream": "identity_access"}})
    monkeypatch.setattr(controller, "_phase_task_contract", lambda _phase: SimpleNamespace(
        authentication_comparison_workstreams={"identity_access"},
    ))
    assert controller._requires_comparison_assessment(phase, task)


def test_assessment_is_optional_for_non_comparison_decisions():
    assert WorkflowDecisionOutput.model_validate({"status": "done"}).comparison_assessment is None
    workflow.MultiAgentWorkflowController._validate_task_evaluator_decision_payload(
        {"status": "done"}, allowed=("done",),
    )


def test_inconclusive_comparison_is_not_overridden_by_satisfied_receipts(worker_case, monkeypatch):
    controller, task, store, _target, _observed = worker_case
    task = replace(task, recovery_context={"authentication_requirement": {"comparison": "account"}})
    monkeypatch.setattr(controller, "_run_json_text_agent", lambda *_args, **_kwargs: {
        "status": "partial_failure", "repair": {"kind": "execution", "execution_receipt_gaps": ["request"]},
        "comparison_assessment": {"outcome": "inconclusive", "reason": "No appropriate subject was established",
                                  "operation_support": operation_support("inconclusive", [])},
    })
    reconciliations = []
    monkeypatch.setattr(controller, "_evaluator_reopens_resolved_execution_gate",
                        lambda *_args: reconciliations.append(True) or True)
    decision = controller._evaluate_task(store.get_plan("OP_AUTH"), store.get_plan("OP_AUTH").phases[0], task,
                                         acceptance_results=[])
    assert decision.status == "partial_failure" and decision.repair_kind == "execution"
    assert reconciliations == []


def test_comparison_schema_repair_accepts_corrected_assessment(worker_case, monkeypatch, tmp_path):
    controller, task, store, _target, _observed = worker_case
    evidence = tmp_path / "resource-proof.txt"
    evidence.write_text("Valid resource input; one identity allowed and the other denied")
    reference = f"artifact:{evidence}"
    store.patch_task("OP_AUTH", task.task_uid, evidence_additions=[reference], recovery_context_updates={
        "authentication_requirement": {"comparison": "role"},
    })
    _use_sqlite_workflow_state(controller, store, monkeypatch)
    task = controller.state.list_tasks()[0]
    calls = []

    def model(_role, _prompt, _tools, _system):
        calls.append(True)
        if len(calls) == 1:
            return '{"status":"done"}'
        return json.dumps({"status": "done", "comparison_assessment": {
            "operation_support": operation_support("supported", [reference]),
            "outcome": "supported", "reason": "Operation and comparison demonstrated",
            "operation_evidence_refs": [reference], "comparison_evidence_refs": [reference, reference],
        }})

    controller.text_runner = model
    decision = controller._evaluate_task(store.get_plan("OP_AUTH"), store.get_plan("OP_AUTH").phases[0], task)
    assert decision.status == "done" and len(calls) == 2
    saved = store.get_tasks("OP_AUTH")[0].recovery_context["comparison_assessment"]
    assert len(saved["comparison_evidence_refs"]) == 1


def test_exhausted_comparison_schema_repair_preserves_gap(worker_case, monkeypatch):
    controller, task, store, _target, _observed = worker_case
    store.patch_task("OP_AUTH", task.task_uid, recovery_context_updates={
        "authentication_requirement": {"comparison": "tenant"},
    })
    _use_sqlite_workflow_state(controller, store, monkeypatch)
    task = controller.state.list_tasks()[0]
    controller.text_runner = lambda *_args: '{"status":"done"}'
    decision = controller._evaluate_task(store.get_plan("OP_AUTH"), store.get_plan("OP_AUTH").phases[0], task)
    assert decision.status == "partial_failure" and "evaluator output invalid" in decision.reason


@pytest.mark.parametrize("repair_resolves", [True, False])
def test_inconclusive_comparison_runs_one_evidence_producing_repair(tmp_path, monkeypatch, repair_resolves):
    from modules.config.types import BudgetConfig
    from modules.handlers.tool_recovery import ToolOutcome
    from modules.tools import artifact, memory
    from tests.test_multi_agent_workflow import FakeState, Task, _plan, _runtime

    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1})
    task = Task(task_uid="comparison", title="Compare access", objective="Compare the assigned resource", phase=1,
                status="active", recovery_context={"authentication_matrix": {"version": 1}})
    plan = _plan()
    state = FakeState(plan, [task], acceptance_complete=True)
    evidence = tmp_path / "comparison.txt"
    evidence.write_text("The initial requests used invalid resource inputs")
    reference = f"artifact:{evidence}"
    state.acceptance_results[task.task_uid] = [AcceptanceResult(
        criterion_id=task.acceptance.criteria[0].id, status="satisfied", disposition="observation",
        summary="Both requests returned the same error", evidence_refs=[reference],
    )]
    monkeypatch.setattr(workflow, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(artifact, "_operation_output_root", lambda: str(tmp_path))
    from modules.tools.memory import SQLiteApplicationStore

    store = SQLiteApplicationStore(str(tmp_path / "workflow.db"), "target")
    monkeypatch.setattr(workflow, "_get_database_store", lambda: store)
    evaluations = []
    actors = []

    def model(role, _prompt, _tools, _system):
        if role == "task_prompt_builder":
            return '{"prompt":"Compare the assigned resource"}'
        assert role == "task_evaluator"
        evaluations.append(True)
        supported = repair_resolves and len(evaluations) == 2
        return json.dumps({"status": "done", "comparison_assessment": {
            "operation_support": operation_support("supported" if supported else "inconclusive", [reference] if supported else []),
            "outcome": "supported" if supported else "inconclusive",
            "operation_evidence_refs": [reference] if supported else [],
            "comparison_evidence_refs": [reference],
            "reason": "Valid operation and identity comparison" if supported else "Valid resource inputs are missing",
        }})

    def actor(_role, prompt, tools, _system, _policy):
        actors.append(prompt)
        if len(actors) == 1:
            return workflow.TaskExecutorCycleResult(text="Existing observation accepted", outcomes=[])
        assert "shell" in {workflow.get_tool_name(tool) for tool in tools}
        evidence.write_text("A valid resource was compared for both identities" if repair_resolves else "Still invalid")
        return workflow.TaskExecutorCycleResult(text="New response evidence", outcomes=[ToolOutcome(
            sequence=1, tool_use_id="corrected-operation", tool_name="shell", success=True, correctable=False,
            input_summary="Exercise assigned operation with suitable inputs", output_summary=reference,
            artifact_refs=(reference,),
        )])

    controller = workflow.MultiAgentWorkflowController(
        runtime=runtime, budget=BudgetConfig(max_duration_minutes=60), state_store=state,
        text_runner=model, work_runner=actor,
    )
    controller._run_task(plan, plan.phases[0], task)
    assert len(actors) == 2 and len(evaluations) == 2
    events = [event for event in runtime.callback_handler.events if event["type"] == "evaluator_execution_repair"]
    assert [event["outcome"] for event in events] == ["scheduled", "completed" if repair_resolves else "exhausted"]
    assert state.list_tasks()[0].status == ("done" if repair_resolves else "partial_failure")


def test_comparison_schema_is_required_in_structured_transport_and_repair(worker_case, monkeypatch, tmp_path):
    from modules.agents.structured_outputs import ComparisonTaskDecisionOutput

    controller, task, store, _target, _observed = worker_case
    evidence = tmp_path / "comparison-proof.txt"
    evidence.write_text("Suitable resource exercised by both required identities")
    reference = f"artifact:{evidence}"
    store.patch_task("OP_AUTH", task.task_uid, evidence_additions=[reference], recovery_context_updates={
        "authentication_matrix": {"version": 1},
    })
    _use_sqlite_workflow_state(controller, store, monkeypatch)
    task = controller.state.list_tasks()[0]
    models = []

    def structured(_role, prompt, _tools, _system, model):
        models.append(model)
        assert model is ComparisonTaskDecisionOutput
        assert "comparison_assessment" in model.model_json_schema()["required"]
        assert '"comparison_assessment":' in prompt
        if len(models) == 1:
            return {"status": "done"}
        return {"status": "done", "comparison_assessment": {
            "operation_support": operation_support("supported", [reference]),
            "outcome": "supported", "reason": "Suitable request and comparison evidenced",
            "operation_evidence_refs": [reference], "comparison_evidence_refs": [reference],
        }}

    controller.structured_runner = structured
    controller.text_runner = lambda *_args: pytest.fail("structured output is available")
    controller._evaluate_task(store.get_plan("OP_AUTH"), store.get_plan("OP_AUTH").phases[0], task)
    assert len(models) == 2


@pytest.mark.parametrize("interface", ["http", "network", "filesystem"])
@pytest.mark.parametrize("basis", ["availability_only", "unestablished"])
def test_availability_cannot_satisfy_operation_support(worker_case, monkeypatch, tmp_path, interface, basis):
    controller, task, store, _target, _observed = worker_case
    path = tmp_path / f"{interface}-responses.txt"
    path.write_text("Both requests completed but intended behavior is unestablished")
    reference = f"artifact:{path}"
    store.patch_task("OP_AUTH", task.task_uid, evidence_additions=[reference], recovery_context_updates={
        "authentication_requirement": {"comparison": "account"},
    })
    _use_sqlite_workflow_state(controller, store, monkeypatch)
    task = controller.state.list_tasks()[0]
    controller.text_runner = lambda *_args: json.dumps({"status": "done", "comparison_assessment": {
        "outcome": "supported", "reason": "Responses are identical",
        "operation_support": {"status": "established", "basis": basis, "evidence_refs": [reference]},
        "operation_evidence_refs": [reference], "comparison_evidence_refs": [reference],
    }})
    decision = controller._evaluate_task(store.get_plan("OP_AUTH"), store.get_plan("OP_AUTH").phases[0], task)
    assert decision.status == "partial_failure" and decision.repair_kind == "execution"
    saved = store.get_tasks("OP_AUTH")[0].recovery_context["comparison_assessment"]
    assert saved["outcome"] == "inconclusive" and saved["operation_support"]["status"] == "unestablished"


def test_operation_support_refs_are_required_and_scoped():
    reference = "artifact:valid.txt"
    data = {"comparison_assessment": {
        "outcome": "supported", "reason": "Resource policy evidenced",
        "operation_support": {"status": "established", "basis": "established_access_policy", "evidence_refs": []},
        "operation_evidence_refs": [reference], "comparison_evidence_refs": [reference],
    }}
    # Use a normalization boundary stub to isolate semantic schema validation from file availability.
    from unittest.mock import patch

    with patch.object(workflow, "canonical_artifact_reference", side_effect=lambda ref: ref):
        with pytest.raises(ValueError, match="requires artifact evidence"):
            workflow.MultiAgentWorkflowController._comparison_assessment_from_data(data, required=True,
                                                                                   allowed_refs=[reference])
        data["comparison_assessment"]["operation_support"]["evidence_refs"] = ["artifact:other.txt"]
        with pytest.raises(ValueError, match="unauthorized artifact"):
            workflow.MultiAgentWorkflowController._comparison_assessment_from_data(data, required=True,
                                                                                   allowed_refs=[reference])
        data["comparison_assessment"]["operation_support"]["evidence_refs"] = [reference]
        assert workflow.MultiAgentWorkflowController._comparison_assessment_from_data(
            data, required=True, allowed_refs=[reference],
        )["outcome"] == "supported"


def test_comparison_artifact_schema_normalizes_supported_presentation_and_explains_kinds():
    from modules.agents.structured_outputs import ComparisonAssessmentOutput

    assessment = {
        "outcome": "supported", "reason": "Observed policy",
        "operation_support": {"status": "established", "basis": "established_access_policy",
                              "evidence_refs": ["Artifact: proof.txt"]},
        "operation_evidence_refs": ["`artifact: proof.txt`"], "comparison_evidence_refs": ["artifact_id: response"],
    }
    parsed = ComparisonAssessmentOutput.model_validate(assessment)
    assert parsed.operation_evidence_refs == ["artifact:proof.txt"]
    assert parsed.operation_support.evidence_refs == ["artifact:proof.txt"]
    assert parsed.comparison_evidence_refs == ["artifact_id:response"]
    assessment["comparison_evidence_refs"] = ["memory:observation"]
    with pytest.raises(ValueError, match="memory and other kinds are unsupported"):
        ComparisonAssessmentOutput.model_validate(assessment)
