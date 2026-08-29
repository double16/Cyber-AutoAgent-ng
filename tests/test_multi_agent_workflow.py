import ast
import inspect
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from strands.types.exceptions import MaxTokensReachedException

from modules.agents import multi_agent_workflow as workflow_mod
from modules.agents.cyber_autoagent import build_role_tools
from modules.agents.multi_agent_workflow import (
    MultiAgentWorkflowController,
    TaskCreationBatch,
    TaskPromptBuildError,
    WorkflowInvariantError,
    WorkflowStateStore,
    extract_json_object,
    extract_result_text,
)
from modules.config.types import BudgetConfig
from modules.handlers.base import BudgetLimitReached
from modules.handlers.max_token_recovery import MaxTokenClassification
from modules.handlers.tool_recovery import ExecutionReceipt, ToolOutcome, ToolOutcomeJournal
from modules.tools import memory as memory_mod
from modules.tools.memory import (
    AcceptanceBasis,
    AcceptanceContract,
    AcceptanceCriterion,
    AcceptanceResult,
    CoverageResult,
    EvidenceRequirement,
    ExecutionRequirement,
    OperationPlan,
    OperationTarget,
    PlanPhase,
    SQLiteApplicationStore,
)
from modules.tools.memory import Task as TaskModel


def _acceptance(criterion_id="task-outcome"):
    return AcceptanceContract(
        mode="outcome",
        basis=AcceptanceBasis(
            kind="procedure",
            description="Bounded test procedure",
            source_refs=["target:target-1", "plan:phase-1"],
            procedure={
                "methods": ["test-fixture"],
                "limits": {"max_items": 1},
                "stop_condition": "first_limit_reached",
                "gap_policy": "record_unassessed",
                "output_kind": "inventory_manifest",
            },
        ),
        criteria=[
            AcceptanceCriterion(
                id=criterion_id,
                description="Complete the test task objective",
                evidence_requirements=[EvidenceRequirement(kind="inventory_manifest")],
            )
        ],
    )


def _artifact_acceptance(criterion_id="artifact-output"):
    return AcceptanceContract(
        mode="outcome",
        basis=AcceptanceBasis(
            kind="procedure",
            description="Bounded artifact procedure",
            source_refs=["target:target-1", "plan:phase-1"],
            procedure={
                "methods": ["workflow-analysis"],
                "limits": {"max_items": 1},
                "stop_condition": "first_limit_reached",
                "gap_policy": "record_unassessed",
                "output_kind": "artifact",
            },
        ),
        criteria=[
            AcceptanceCriterion(
                id=criterion_id,
                description="Store the bounded workflow map",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            )
        ],
    )


def test_execution_evidence_capabilities_are_derived_from_observed_tools():
    request = ToolOutcome(
        sequence=1,
        tool_use_id="request-1",
        tool_name="http_request",
        success=True,
        correctable=False,
        input_summary="",
        output_summary="",
    )
    crawl = ToolOutcome(
        sequence=2,
        tool_use_id="crawl-1",
        tool_name="shell",
        success=True,
        correctable=False,
        input_summary='{"command":"katana -u http://target.test"}',
        output_summary="",
    )
    source_analysis = ToolOutcome(
        sequence=3,
        tool_use_id="source-1",
        tool_name="shell",
        success=True,
        correctable=False,
        input_summary='{"command":"opengrep scan src"}',
        output_summary="",
    )

    assert MultiAgentWorkflowController._outcome_execution_capabilities(request) == {"request"}
    assert MultiAgentWorkflowController._outcome_execution_capabilities(crawl) == {"analyze", "crawl"}
    assert MultiAgentWorkflowController._outcome_execution_capabilities(source_analysis) == {"analyze"}
    assert MultiAgentWorkflowController._canonical_execution_method("web-spider") == "crawl"


def test_execution_evidence_capabilities_include_wrapped_shell_command():
    crawl = ToolOutcome(
        sequence=1,
        tool_use_id="crawl-1",
        tool_name="shell",
        success=True,
        correctable=False,
        input_summary='{"command":"cd /tmp\\ntimeout 60 katana -u http://target.test/ -o crawl.txt"}',
        output_summary="",
    )

    assert MultiAgentWorkflowController._outcome_execution_capabilities(crawl) == {"analyze", "crawl"}


def test_execution_evidence_capabilities_use_environment_command_alias():
    scan = ToolOutcome(
        sequence=1,
        tool_use_id="scan-1",
        tool_name="shell",
        success=True,
        correctable=False,
        input_summary='{"command":"testssl https://target.test"}',
        output_summary="",
    )

    assert MultiAgentWorkflowController._outcome_execution_capabilities(scan) == {"execute", "request"}


def test_shell_outcome_retains_existing_declared_output_artifact(monkeypatch, tmp_path):
    artifact = tmp_path / "artifacts" / "crawl.txt"
    artifact.parent.mkdir()
    artifact.write_text("http://target.test/\n", encoding="utf-8")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="crawl-1",
        tool_name="shell",
        success=True,
        correctable=False,
        input_summary=json.dumps({"command": f"cd {artifact.parent}\ntimeout 60 scanner -o {artifact.name}"}),
        output_summary="",
    )

    assert MultiAgentWorkflowController._artifact_refs_from_tool_outcomes([outcome]) == [
        "artifact:artifacts/crawl.txt"
    ]


def test_journaled_katana_outcome_satisfies_crawl_execution_requirement(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Crawl the assigned root route",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce crawl evidence for the assigned root route",
            "/",
        )],
    )
    task = Task(
        task_uid="katana-execution-proof",
        title="Crawl target",
        objective="Crawl the assigned target",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Bounded crawl",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["crawl"],
                    "limits": {"max_items": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    artifact = tmp_path / "artifacts" / "katana_output.txt"
    artifact.parent.mkdir()
    artifact.write_text("http://target.test/\n", encoding="utf-8")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    journal = ToolOutcomeJournal()
    outcome = journal.append(
        tool_use_id="katana-1",
        tool_name="shell",
        success=True,
        correctable=False,
        tool_input={
            "command": f"cd {artifact.parent} && katana -u http://target.test -o {artifact.name}",
        },
        output="crawl completed",
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )

    resolved = controller._resolve_controller_execution_evidence(plan, task, criterion, [outcome])

    assert outcome.structured_input == {
        "command": f"cd {artifact.parent} && katana -u http://target.test -o {artifact.name}",
    }
    assert MultiAgentWorkflowController._outcome_execution_capabilities(outcome) == {"analyze","crawl"}
    assert resolved == {"criterion-1-execution-1": ["artifact:artifacts/katana_output.txt"]}


def Task(*args, **kwargs):
    """Construct strict tasks while preserving the older positional style inside workflow tests."""

    positional_fields = ("task_uid", "title", "objective", "phase", "status", "status_reason", "evidence")
    for field_name, value in zip(positional_fields, args):
        kwargs[field_name] = value
    task_uid = str(kwargs.get("task_uid", "task"))
    kwargs.setdefault("acceptance", _acceptance(f"criterion:{task_uid}"))
    return TaskModel(**kwargs)


def _tool(name):
    def tool_func():
        return None

    tool_func.__name__ = name
    return tool_func


def test_default_text_runner_cleans_role_agent(monkeypatch):
    cleanup_calls = []

    class Agent:
        def __call__(self, prompt):
            assert prompt == "prompt"
            return SimpleNamespace(message={"content": [{"text": "done"}]})

        def cleanup(self):
            cleanup_calls.append("cleanup")

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            provider="litellm",
            target="example.com",
            objective="test",
        ),
        operation_id="OP_TEST",
    )
    monkeypatch.setattr(workflow_mod, "create_agent", lambda *args, **kwargs: Agent())

    result = workflow_mod.default_text_runner(runtime)("planner", "prompt", [], "system")

    assert result == "done"
    assert cleanup_calls == ["cleanup"]


def test_acceptance_recovery_treats_http_error_artifacts_as_available_unknown_evidence(tmp_path):
    artifact = tmp_path / "missing.html"
    artifact.write_text("<title>404 Not Found</title><h1>Not Found</h1>", encoding="utf-8")

    assert MultiAgentWorkflowController._artifact_recovery_quality(str(artifact)) == "available"
    assert MultiAgentWorkflowController._has_viable_acceptance_recovery_evidence(
        [{"reference": "artifact:artifacts/missing.html", "usable": "true", "quality": "available"}]
    ) is True
    assert MultiAgentWorkflowController._has_viable_acceptance_recovery_evidence(
        [{"reference": "memory:observation-1"}]
    ) is True


def test_artifact_recovery_does_not_classify_arbitrary_non_http_text_as_contradictory(tmp_path):
    artifact = tmp_path / "negative.txt"
    artifact.write_text("request failed: no positive evidence was observed", encoding="utf-8")

    assert MultiAgentWorkflowController._artifact_recovery_quality(str(artifact)) == "available"


def test_contradictory_finding_artifact_references_are_deterministic(tmp_path, monkeypatch):
    artifact = tmp_path / "negative.txt"
    artifact.write_text("Failed to open stream; failed opening /etc/passwd", encoding="utf-8")
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _ref: artifact)
    monkeypatch.setattr("modules.tools.memory._artifact_path_from_ref", lambda _ref: artifact)
    monkeypatch.setattr(workflow_mod, "canonical_artifact_reference", lambda reference: reference)
    outcome = ToolOutcome(
        1,
        "finding",
        "store_finding",
        False,
        False,
        json.dumps({
            "title": "Local file inclusion",
            "claim": "LFI reads local files",
            "technique": "file inclusion",
            "artifacts": ["artifact:artifacts/negative.txt"],
        }),
        "evidence assertion marker was not found",
    )

    assert MultiAgentWorkflowController._has_contradictory_finding_artifact(outcome) is True
    assert MultiAgentWorkflowController._contradictory_finding_artifact_refs(outcome) == [
        "artifact:artifacts/negative.txt"
    ]


def test_acceptance_recovery_context_marks_unreadable_artifact_as_unknown(monkeypatch):
    task = Task(task_uid="task", title="Task", objective="Assess behavior", phase=1, status="active")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
    )
    monkeypatch.setattr(workflow_mod, "canonical_artifact_reference", lambda reference: reference)
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _ref: "/missing/artifact.txt")

    context = controller._acceptance_recovery_context(
        task,
        [],
        [ToolOutcome(1, "tool", "shell", True, False, "", "artifact:artifacts/missing.txt")],
        None,
    )

    assert context[0]["quality"] == "unavailable"
    assert context[0]["evidence_status"] == "unknown"


def test_successful_artifact_outcomes_are_retained_as_task_evidence(monkeypatch):
    task = Task(task_uid="task", title="Task", objective="Assess behavior", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=state
    )
    monkeypatch.setattr(workflow_mod, "canonical_artifact_reference", lambda reference: reference)
    outcome = ToolOutcome(
        1,
        "tool",
        "browser_goto_url",
        True,
        False,
        "",
        "artifact:artifacts/browser-result.log",
    )

    updated = controller._retain_task_artifact_evidence(task, [outcome])

    assert updated.evidence == ["artifact:artifacts/browser-result.log"]
    assert state.tasks[0].evidence == updated.evidence
    assert any(event["type"] == "task_durable_evidence_retained" for event in controller.runtime.callback_handler.events)


def test_artifact_retention_preserves_execution_receipts_from_fresher_task_state(monkeypatch):
    persisted_task = Task(
        task_uid="task",
        title="Task",
        objective="Crawl target",
        phase=1,
        status="active",
        recovery_context={
            "execution_evidence_receipts": {"crawl-root": ["artifact:artifacts/recon.json"]},
            "controller_execution_evidence": {"crawl-root": {"receipt_mode": "capability_matched"}},
        },
    )
    state = FakeState(_plan(), tasks=[persisted_task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=state
    )
    stale_task = replace(persisted_task, recovery_context={})
    monkeypatch.setattr(workflow_mod, "canonical_artifact_reference", lambda reference: reference)
    outcome = ToolOutcome(
        1,
        "tool",
        "specialized_recon_orchestrator",
        True,
        False,
        "",
        "artifact:artifacts/recon.json",
    )

    updated = controller._retain_task_artifact_evidence(stale_task, [outcome])

    assert updated.evidence == ["artifact:artifacts/recon.json"]
    assert updated.recovery_context == persisted_task.recovery_context
    assert state.tasks[0].recovery_context == persisted_task.recovery_context


def test_controller_seeds_secret_candidate_only_from_target_scoped_tool_outcomes(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    task = Task(
        task_uid="task",
        title="Inspect config",
        objective="Inspect target configuration",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=FakeState(plan, tasks=[task])
    )
    stored = []
    monkeypatch.setattr(
        workflow_mod,
        "detect_secret_exposures",
        lambda _ref: [SimpleNamespace(kind="connection_string", digest="a" * 64)],
    )
    monkeypatch.setattr(
        workflow_mod,
        "store_finding",
        lambda **payload: stored.append(payload) or '{"finding_ref":"finding:seeded"}',
    )
    outcome = ToolOutcome(
        1,
        "request",
        "http_request",
        True,
        False,
        "",
        "",
        artifact_refs=("artifact:artifacts/config.txt",),
        structured_input={"url": "http://target.test/api/config"},
    )

    controller._seed_secret_exposure_candidates(plan, task, [outcome])
    controller._seed_secret_exposure_candidates(
        plan,
        task,
        [ToolOutcome(2, "read", "read_artifact", True, False, "", "", artifact_refs=outcome.artifact_refs)],
    )

    assert len(stored) == 1
    assert stored[0]["target"] == "http://target.test/api/config"
    assert stored[0]["evidence_assertions"][0]["digest"] == "a" * 64
    assert any(event["type"] == "secret_exposure_candidate_created" for event in controller.runtime.callback_handler.events)


def test_controller_skips_unavailable_artifact_during_secret_candidate_seeding(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget(target_id="target-1", value="http://target.test", type="network")],
    )
    task = Task(
        task_uid="task",
        title="Inspect config",
        objective="Inspect target configuration",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=FakeState(plan, tasks=[task])
    )
    monkeypatch.setattr(
        workflow_mod,
        "detect_secret_exposures",
        Mock(side_effect=ValueError("Artifact does not exist: artifact:task_evidence/missing.json")),
    )
    outcome = ToolOutcome(
        1,
        "request",
        "http_request",
        True,
        False,
        "",
        "",
        artifact_refs=("artifact:task_evidence/missing.json",),
        structured_input={"url": "http://target.test/api/config"},
    )

    controller._seed_secret_exposure_candidates(plan, task, [outcome])

    skipped = [
        event
        for event in controller.runtime.callback_handler.events
        if event["type"] == "secret_exposure_candidate_skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0]["artifact_ref"] == "artifact:task_evidence/missing.json"
    assert "Artifact does not exist" in skipped[0]["reason"]


def test_snapshot_verification_failure_is_contained_to_the_active_task(monkeypatch):
    task = Task(task_uid="task", title="Task", objective="Assess behavior", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=state
    )
    monkeypatch.setattr(
        controller,
        "_run_task_in_trace",
        Mock(
            side_effect=memory_mod.TaskEvidenceSnapshotVerificationError(
                "TASK_EVIDENCE_SNAPSHOT_VERIFICATION_FAILED: digest mismatch"
            )
        ),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert state.tasks[0].status == "partial_failure"
    assert "snapshot verification failed" in state.tasks[0].status_reason.lower()
    assert any(
        event["type"] == "task_evidence_snapshot_verification_failed"
        for event in controller.runtime.callback_handler.events
    )


def test_source_artifact_repair_replaces_only_unavailable_artifact_evidence(monkeypatch):
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=FakeState(_plan())
    )
    monkeypatch.setattr(
        controller,
        "_canonical_recovery_reference",
        lambda reference: reference if reference.endswith("regenerated.txt") else "",
    )
    repair_outcome = ToolOutcome(
        1,
        "repair",
        "shell",
        True,
        False,
        "",
        "",
        artifact_refs=("artifact:artifacts/regenerated.txt",),
    )

    payload = controller._source_artifact_repair_payload(
        {
            "status": "satisfied",
            "disposition": "observation",
            "summary": "Result retained",
            "evidence_refs": ["artifact:artifacts/missing.txt", "memory:retained"],
        },
        [repair_outcome],
    )

    assert payload["evidence_refs"] == ["artifact:artifacts/regenerated.txt", "memory:retained"]
    assert (
        controller._acceptance_recovery_details(
            "TASK_EVIDENCE_SNAPSHOT_SOURCE_UNAVAILABLE: source artifact is unavailable"
        )["code"]
        == "source_artifact_unavailable"
    )
    assert (
        controller._acceptance_recovery_details(
            "TASK_EVIDENCE_SNAPSHOT_DESTINATION_UNVERIFIABLE: copied snapshot digest mismatch"
        )["code"]
        == "destination_snapshot_unverifiable"
    )


def test_source_artifact_repair_runs_once_and_replays_acceptance(monkeypatch):
    plan = _plan()
    task = Task(
        task_uid="source-repair",
        title="Repair source evidence",
        objective="Produce the required artifact",
        phase=1,
        status="active",
        acceptance=_artifact_acceptance(),
    )
    state = FakeState(plan, tasks=[task], acceptance_complete=False)
    calls = 0

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"produce artifact","tools":[]}'
        raise AssertionError(f"unexpected role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        nonlocal calls
        calls += 1
        if calls == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="Acceptance source disappeared",
                outcomes=[ToolOutcome(
                    sequence=1,
                    tool_use_id="acceptance-1",
                    tool_name="record_task_acceptance",
                    success=False,
                    correctable=False,
                    input_summary="",
                    output_summary="TASK_EVIDENCE_SNAPSHOT_SOURCE_UNAVAILABLE: source artifact is unavailable",
                    raw_output_summary="TASK_EVIDENCE_SNAPSHOT_SOURCE_UNAVAILABLE: source artifact is unavailable",
                    structured_input={
                        "status": "satisfied",
                        "disposition": "observation",
                        "summary": "Artifact regenerated",
                        "evidence_refs": ["artifact:artifacts/missing.txt"],
                    },
                )],
            )
        return workflow_mod.TaskExecutorCycleResult(
            text="Regenerated the artifact",
            outcomes=[ToolOutcome(
                sequence=2,
                tool_use_id="shell-2",
                tool_name="shell",
                success=True,
                correctable=False,
                input_summary="",
                output_summary="",
                artifact_refs=("artifact:artifacts/regenerated.txt",),
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )
    monkeypatch.setattr(
        controller,
        "_canonical_recovery_reference",
        lambda reference: reference if reference.endswith("regenerated.txt") else "",
    )
    replayed = []

    def replay_acceptance(**payload):
        replayed.append(payload)
        state.acceptance_results[task.task_uid] = [AcceptanceResult(
            criterion_id=task.acceptance.criteria[0].id,
            status="satisfied",
            disposition="observation",
            summary="Artifact regenerated",
            evidence_refs=tuple(payload["evidence_refs"]),
        )]
        return '{"complete":true}'

    monkeypatch.setattr(workflow_mod, "build_record_task_acceptance_tool", lambda *_args, **_kwargs: replay_acceptance)
    monkeypatch.setattr(
        controller,
        "_evaluate_task",
        lambda *_args, **_kwargs: workflow_mod.WorkflowDecision(status="done", reason="accepted"),
    )

    controller._run_task(plan, plan.phases[0], task)

    assert calls == 2
    assert replayed[0]["evidence_refs"] == ["artifact:artifacts/regenerated.txt"]
    assert state.tasks[0].status == "done"
    assert any(
        event["type"] == "task_source_artifact_repair" and event["outcome"] == "scheduled"
        for event in controller.runtime.callback_handler.events
    )
    assert any(
        event["type"] == "task_source_artifact_repair" and event["outcome"] == "regenerated"
        for event in controller.runtime.callback_handler.events
    )


def test_destination_snapshot_failure_marks_task_partial_without_agent_repair(monkeypatch):
    plan = _plan()
    task = Task(
        task_uid="destination-failure",
        title="Destination failure",
        objective="Produce the required artifact",
        phase=1,
        status="active",
        acceptance=_artifact_acceptance(),
    )
    state = FakeState(plan, tasks=[task], acceptance_complete=False)
    calls = 0

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"produce artifact","tools":[]}'
        raise AssertionError(f"unexpected role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        nonlocal calls
        calls += 1
        return workflow_mod.TaskExecutorCycleResult(
            text="Destination could not be verified",
            outcomes=[ToolOutcome(
                sequence=1,
                tool_use_id="acceptance-1",
                tool_name="record_task_acceptance",
                success=False,
                correctable=False,
                input_summary="",
                output_summary="TASK_EVIDENCE_SNAPSHOT_DESTINATION_UNVERIFIABLE: digest mismatch",
                raw_output_summary="TASK_EVIDENCE_SNAPSHOT_DESTINATION_UNVERIFIABLE: digest mismatch",
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    controller._run_task(plan, plan.phases[0], task)

    assert calls == 1
    assert state.tasks[0].status == "partial_failure"
    assert any(
        event["type"] == "task_source_artifact_repair" and event["outcome"] == "destination_unverifiable"
        for event in controller.runtime.callback_handler.events
    )


def test_candidate_dependent_phase_closes_without_speculative_task_creation(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[
            PlanPhase(
                id=1,
                title="Correlation",
                status="active",
                requires_finding_candidates=True,
            ),
        ],
    )
    state = FakeState(plan, finding_records=[])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        max_iterations=1,
    )
    transitions = []
    def mark_phase(current_plan, phase_id, status):
        transitions.append((phase_id, status))
        updated = OperationPlan(
            objective=current_plan.objective,
            current_phase=phase_id,
            total_phases=1,
            phases=[PlanPhase(
                id=1,
                title="Correlation",
                status=status,
                requires_finding_candidates=True,
            )],
        )
        state.plan = updated
        return updated

    monkeypatch.setattr(controller, "_mark_phase", mark_phase)
    monkeypatch.setattr(controller, "_emit_workflow_completion", lambda _plan: None)

    controller.run()

    assert transitions == [(1, "not_applicable")]
    assert any(
        event["type"] == "phase_dependency_gate" and event["reason"] == "no_persisted_finding_candidates"
        for event in controller.runtime.callback_handler.events
    )


def test_phase_title_does_not_override_structured_candidate_dependency_metadata():
    phase = PlanPhase(
        id=1,
        title="Impact Demonstration",
        status="active",
        criteria="Demonstrate impact",
        requires_finding_candidates=False,
    )

    assert workflow_mod._phase_semantically_requires_finding_candidates(phase) is False


def test_default_text_runner_preserves_agent_error_when_cleanup_fails(monkeypatch):
    class Agent:
        def __call__(self, prompt):
            raise ValueError("agent failed")

        def cleanup(self):
            raise RuntimeError("cleanup failed")

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            provider="ollama",
            target="example.com",
            objective="test",
        ),
        operation_id="OP_TEST",
    )
    monkeypatch.setattr(workflow_mod, "create_agent", lambda *args, **kwargs: Agent())

    with pytest.raises(ValueError, match="agent failed"):
        workflow_mod.default_text_runner(runtime)("planner", "prompt", [], "system")


def test_default_text_runner_discards_and_classifies_max_token_output(monkeypatch):
    partial = "The model repeats the same reasoning instead of acting.\n" * 60

    class Agent:
        def __init__(self):
            self.messages = [{"role": "assistant", "content": [{"text": partial}]}]

        def __call__(self, prompt):
            raise MaxTokensReachedException("max_tokens")

        def cleanup(self):
            return None

    runtime = SimpleNamespace(
        config=SimpleNamespace(provider="litellm", target="example.com", objective="test"),
        operation_id="OP_TEST",
    )
    agent = Agent()
    monkeypatch.setattr(workflow_mod, "create_agent", lambda *args, **kwargs: agent)

    with pytest.raises(MaxTokensReachedException) as exc_info:
        workflow_mod.default_text_runner(runtime)("planner", "prompt", [], "system")

    assert agent.messages == []
    assert exc_info.value.max_token_classification.kind == "reasoning_loop"


def test_default_text_runner_disables_catalog_for_evaluators(monkeypatch):
    kwargs_seen = []

    class Agent:
        def __call__(self, prompt):
            return "{}"

        def cleanup(self):
            return None

    runtime = SimpleNamespace(
        config=SimpleNamespace(provider="litellm", target="example.com", objective="test"),
        operation_id="OP_TEST",
    )

    def create_agent(*args, **kwargs):
        kwargs_seen.append(kwargs)
        return Agent()

    monkeypatch.setattr(workflow_mod, "create_agent", create_agent)
    runner = workflow_mod.default_text_runner(runtime)
    runner("task_evaluator", "prompt", [], "system")
    runner("task_executor", "prompt", [], "system")

    assert kwargs_seen[0]["include_tool_catalog"] is False
    assert kwargs_seen[1]["include_tool_catalog"] is True


class FakeCallbackHandler:
    def __init__(self, progress=0):
        self.progress = progress
        self.termination_emitted = False
        self.termination_events = []
        self.events = []
        self.timeline = []
        self.operation_health_provider = None
        self.efficiency_events = []

    def has_reached_limit(self):
        return False

    def get_budget_progress(self):
        return self.progress

    def emit_termination(self, reason, message):
        self.termination_emitted = True
        self.termination_events.append((reason, message))
        self.timeline.append(("termination", reason))

    def emit_ui_event(self, event):
        self.events.append(event)
        self.timeline.append(("event", event))

    def record_efficiency_event(self, category, agent=None):
        self.efficiency_events.append((category, agent))

    def record_max_token_exhaustion(self, *, role, classification, exhaustion_ordinal, agent=None):
        self.efficiency_events.append(("max_token_exhaustion", role, classification, exhaustion_ordinal, agent))

    def set_operation_health_provider(self, provider):
        self.operation_health_provider = provider


class FakeState:
    def __init__(self, plan, tasks=None, acceptance_complete=True, finding_records=None, preflight_results=None):
        self.plan = plan
        self.tasks = list(tasks or [])
        self.acceptance_complete = acceptance_complete
        self.acceptance_results = {}
        self.finding_records = list(finding_records or [])
        self.preflight_results = list(preflight_results or [{
            "target_id": "target-1",
            "resolved_addresses": ["198.51.100.10"],
            "has_global_address": True,
        }])
        self.client = SimpleNamespace(list_memories=lambda **kwargs: [])

    def get_plan(self):
        return self.plan

    def store_plan(self, plan):
        self.plan = plan
        return plan

    def list_tasks(self, phase=None, status=None):
        tasks = self.tasks
        if phase is not None:
            tasks = [task for task in tasks if task.phase == phase]
        if status:
            tasks = [task for task in tasks if task.status in status]
        return sorted(tasks, key=lambda task: task.created_at or "")

    def store_task(self, task):
        self.tasks = [existing for existing in self.tasks if existing.task_uid != task.task_uid]
        self.tasks.append(task)
        return task

    def patch_task(
        self,
        task_uid,
        *,
        status=None,
        status_reason=None,
        phase=None,
        evidence_additions=(),
        recovery_context_updates=None,
        recovery_context_removals=(),
    ):
        current = next(task for task in self.tasks if task.task_uid == task_uid)
        evidence = list(current.evidence)
        for reference in evidence_additions:
            if reference and reference not in evidence:
                evidence.append(reference)
        context = dict(current.recovery_context)
        for key in recovery_context_removals:
            context.pop(key, None)
        context.update(recovery_context_updates or {})
        return self.store_task(replace(
            current,
            status=status if status is not None else current.status,
            status_reason=status_reason if status_reason is not None else current.status_reason,
            phase=phase if phase is not None else current.phase,
            evidence=evidence,
            recovery_context=context,
        ))

    def list_task_acceptance_results(self, task_uid):
        if task_uid in self.acceptance_results:
            return self.acceptance_results[task_uid]
        if not self.acceptance_complete:
            return []
        task = next(item for item in self.tasks if item.task_uid == task_uid)
        return [
            AcceptanceResult(
                criterion_id=criterion.id,
                status="satisfied",
                disposition="observation",
                summary="Test criterion completed",
                evidence_refs=["memory:test-evidence"],
            )
            for criterion in task.acceptance.criteria
        ]

    def record_finding_candidate_acceptance(self, task, finding_ref):
        self.acceptance_results[task.task_uid] = [AcceptanceResult(
            criterion_id=task.acceptance.criteria[0].id,
            status="satisfied",
            disposition="finding_candidate",
            summary=f"Artifact-backed finding candidate persisted for {task.title}.",
            evidence_refs=(finding_ref,),
        )]

    def record_task_acceptance(self, task, payload):
        self.acceptance_results[task.task_uid] = [AcceptanceResult(
            criterion_id=task.acceptance.criteria[0].id,
            status=payload["status"],
            disposition=payload["disposition"],
            summary=payload["summary"],
            evidence_refs=tuple(payload["evidence_refs"]),
        )]
        return "acceptance stored"

    def list_finding_records(self):
        return list(self.finding_records)

    def rebind_finding_verification_task(self, task, replacement):
        for record in self.finding_records:
            if (
                record.get("finding_uid") == task.reference_id
                and record.get("verification_task_uid") == task.task_uid
                and not record.get("resolution")
                and not record.get("validation_data")
            ):
                self.store_task(replacement)
                record["verification_task_uid"] = replacement.task_uid
                return True
        return False

    def list_preflight_results(self):
        return list(self.preflight_results)

    def update_finding_taxonomy_annotation(self, finding_uid, annotation):
        for record in self.finding_records:
            if record.get("finding_uid") == finding_uid:
                candidate = record.setdefault("candidate_data", {})
                candidate["taxonomy"] = annotation.get("taxonomy", {"cwe": [], "mitre_attack": []})
                candidate["taxonomy_annotation"] = annotation
                return True
        raise ValueError("unknown finding")

    def update_finding_attack_enrichment(self, finding_uid, enrichment):
        for record in self.finding_records:
            if record.get("finding_uid") != finding_uid:
                continue
            candidate = record.setdefault("candidate_data", {})
            existing = candidate.get("final_attack_enrichment")
            if isinstance(existing, dict) and existing.get("status") == "completed":
                return False
            candidate["final_attack_enrichment"] = enrichment
            if enrichment.get("status") == "completed":
                taxonomy = dict(candidate.get("taxonomy") or {"cwe": [], "mitre_attack": []})
                mappings = [
                    *list(taxonomy.get("mitre_attack") or []),
                    *list(enrichment.get("taxonomy", {}).get("mitre_attack") or []),
                ]
                taxonomy["mitre_attack"] = list({item["id"]: item for item in mappings}.values())
                candidate["taxonomy"] = taxonomy
            return True
        raise ValueError("unknown finding")

    def activate_task(self, task):
        return self.store_task(Task(
            task_uid=task.task_uid,
            title=task.title,
            objective=task.objective,
            acceptance=task.acceptance,
            phase=task.phase,
            status="active",
            status_reason="activated",
            evidence=task.evidence,
            created_at=task.created_at,
            kind=task.kind,
            reference_id=task.reference_id,
            replacement_of=task.replacement_of,
            supersedes_criteria=task.supersedes_criteria,
            recovery_context=task.recovery_context,
            target_scope=task.target_scope,
            target_ids=task.target_ids,
        ))

    def mark_task(self, task, status, reason=""):
        return self.store_task(Task(
            task_uid=task.task_uid,
            title=task.title,
            objective=task.objective,
            acceptance=task.acceptance,
            phase=task.phase,
            status=status,
            status_reason=reason,
            evidence=task.evidence,
            created_at=task.created_at,
            kind=task.kind,
            reference_id=task.reference_id,
            replacement_of=task.replacement_of,
            supersedes_criteria=task.supersedes_criteria,
            recovery_context=task.recovery_context,
        ))

    def defer_task(self, task, reason=""):
        return self.store_task(Task(
            task_uid=task.task_uid,
            title=task.title,
            objective=task.objective,
            acceptance=task.acceptance,
            phase=task.phase,
            status="pending",
            status_reason=reason,
            evidence=task.evidence,
            created_at=task.created_at,
            kind=task.kind,
            reference_id=task.reference_id,
            target_scope=task.target_scope,
            target_ids=task.target_ids,
        ))

    def reassign_task_phase(self, task, phase_id):
        return self.store_task(Task(
            task_uid=task.task_uid,
            title=task.title,
            objective=task.objective,
            acceptance=task.acceptance,
            phase=phase_id,
            status=task.status,
            status_reason=task.status_reason,
            evidence=task.evidence,
            created_at=task.created_at,
            kind=task.kind,
            reference_id=task.reference_id,
        ))

    def mark_phase(self, plan, phase_id, status):
        phases = [
            PlanPhase(id=phase.id, title=phase.title, status=status if phase.id == phase_id else phase.status)
            for phase in plan.phases
        ]
        self.plan = OperationPlan(
            objective=plan.objective,
            current_phase=phase_id,
            total_phases=len(phases),
            phases=phases,
            constraints=plan.constraints,
            assessment_complete=True,
        )
        return self.plan

    def ensure_active_phase(self, plan):
        return plan

    def reopen_plan(self, plan):
        phases = [
            PlanPhase(id=phase.id, title=phase.title, status="active" if index == 0 else "pending")
            for index, phase in enumerate(plan.phases)
        ]
        self.plan = OperationPlan(
            objective=plan.objective,
            current_phase=1,
            total_phases=len(phases),
            phases=phases,
            constraints=plan.constraints,
            assessment_complete=False,
        )
        return self.plan

    def create_plan_from_dict(self, plan_data):
        phases = [PlanPhase.from_obj(phase) for phase in plan_data["phases"]]
        if not any(phase.status == "active" for phase in phases):
            phases[0] = PlanPhase(id=phases[0].id, title=phases[0].title, status="active")
        self.plan = OperationPlan(
            objective=plan_data["objective"],
            current_phase=1,
            total_phases=len(phases),
            phases=phases,
            constraints=plan_data.get("constraints", []),
        )
        return self.plan


def retained_work_runner(work_runner):
    """Adapt a test work runner to the retained worker-session interface."""

    @contextmanager
    def session(role, tools, system_prompt):
        def run(prompt, run_policy):
            if len(inspect.signature(work_runner).parameters) >= 5:
                return work_runner(role, prompt, tools, system_prompt, run_policy)
            return work_runner(role, prompt, tools, system_prompt)

        yield run

    return session


class AdvancingFakeState(FakeState):
    def mark_phase(self, plan, phase_id, status):
        phases = [
            PlanPhase(
                id=phase.id,
                title=phase.title,
                status=status if phase.id == phase_id else phase.status,
                criteria=phase.criteria,
            )
            for phase in plan.phases
        ]
        next_phase = next((phase for phase in phases if phase.status == "pending"), None)
        if next_phase:
            phases = [
                PlanPhase(
                    id=phase.id,
                    title=phase.title,
                    status="active" if phase.id == next_phase.id else phase.status,
                    criteria=phase.criteria,
                )
                for phase in phases
            ]
        self.plan = OperationPlan(
            objective=plan.objective,
            current_phase=next_phase.id if next_phase else phase_id,
            total_phases=len(phases),
            phases=phases,
            constraints=plan.constraints,
            assessment_complete=next_phase is None,
        )
        return self.plan


def _plan():
    return OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        constraints=["Stay within the authorized target scope"],
        assessment_complete=False,
    )


def _runtime(progress=0, env_ints=None, env_floats=None):
    env_ints = {
        "CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 0,
        **(env_ints or {}),
    }
    env_floats = env_floats or {}
    return SimpleNamespace(
        config=SimpleNamespace(target="target", objective="assess", available_tools=[]),
        operation_id="OP_TEST",
        system_prompt="base prompt",
        termination_policy="",
        config_manager=SimpleNamespace(
            getenv_int=lambda name, default=0: env_ints.get(name, default),
            getenv_float=lambda name, default=0.0: env_floats.get(name, default),
        ),
        callback_handler=FakeCallbackHandler(progress=progress),
        trace_attributes={"operation.id": "OP_TEST"},
        core_tools_list=[
            _tool("shell"),
            _tool("editor"),
            _tool("memory_retrieve"),
            _tool("create_tasks"),
        ],
        optional_tools_list=[_tool("mcp_scan"), _tool("module_probe")],
    )


def test_controller_resolves_task_local_request_artifact_as_execution_evidence(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Assess the assigned endpoint",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Request the assigned endpoint",
            "/api/products/:id",
        )],
    )
    task = TaskModel(
        task_uid="execution-proof",
        title="Assess product",
        objective="Assess product",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Bounded request",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["http_request"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )
    artifact = tmp_path / "artifacts" / "response.json"
    artifact.parent.mkdir()
    artifact.write_text("{}")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="request-1",
        tool_name="http_request",
        success=True,
        correctable=False,
        input_summary='{"url":"http://target.test/api/products/42"}',
        output_summary="artifact:artifacts/response.json",
        artifact_refs=("artifact:artifacts/response.json",),
    )

    resolved = controller._resolve_controller_execution_evidence(plan, task, criterion, [outcome])

    assert resolved == {"criterion-1-execution-1": ["artifact:artifacts/response.json"]}
    persisted = next(item for item in state.tasks if item.task_uid == task.task_uid)
    assert persisted.recovery_context["controller_execution_evidence"]["criterion-1-execution-1"][
        "tool_use_ids"
    ] == ["request-1"]
    assert persisted.recovery_context["execution_receipt_producers"]["criterion-1-execution-1"] == [{
        "tool_name": "http_request",
        "tool_use_id": "request-1",
        "capabilities": ["request"],
    }]


def test_same_cycle_acceptance_resolves_live_http_request_evidence(monkeypatch, tmp_path):
    artifact = tmp_path / "artifacts" / "root-response.log"
    artifact.parent.mkdir()
    artifact.write_text("HTTP/1.1 200 OK\n", encoding="utf-8")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(memory_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))

    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Request the application root",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[
            ExecutionRequirement(
                "criterion-1-execution-1",
                "Produce execution evidence for http_request against /.",
                "/",
            )
        ],
    )
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    task = Task(
        task_uid="same-cycle-request",
        title="Request root",
        objective="Request the root endpoint",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Request root",
                source_refs=["target:target-1"],
                procedure={
                    "methods": ["http_request"],
                    "limits": {"max_items": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task], acceptance_complete=False)
    captured = {}
    request_outcome = ToolOutcome(
        sequence=1,
        tool_use_id="root-request",
        tool_name="http_request",
        success=True,
        correctable=False,
        input_summary='{"method":"GET","url":"http://target.test/"}',
        output_summary="artifact:artifacts/root-response.log",
        artifact_refs=("artifact:artifacts/root-response.log",),
    )

    def acceptance_tool_builder(_task_uid, _task, execution_evidence_resolver):
        captured["resolver"] = execution_evidence_resolver

        def record_task_acceptance(**_kwargs):
            return "accepted"

        return record_task_acceptance

    monkeypatch.setattr(workflow_mod, "build_record_task_acceptance_tool", acceptance_tool_builder)

    def text_runner(role, _prompt, _tools, _system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"request root","tools":["http_request"]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"root request accepted"}'
        raise AssertionError(role)

    @contextmanager
    def executor_session(_role, _tools, _system_prompt):
        def run(_prompt, _policy):
            captured["run_count"] = captured.get("run_count", 0) + 1
            captured["resolved"] = captured["resolver"](task, criterion)
            state.record_task_acceptance(task, {
                "status": "satisfied",
                "disposition": "observation",
                "summary": "Root request completed",
                "evidence_refs": ["artifact:artifacts/root-response.log"],
            })
            return workflow_mod.TaskExecutorCycleResult(text="accepted", outcomes=[request_outcome])

        run.live_outcomes = lambda: [request_outcome]
        yield run

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        executor_session_factory=executor_session,
    )

    controller._run_task(plan, plan.phases[0], task)

    assert captured["resolved"] == {
        "criterion-1-execution-1": ["artifact:artifacts/root-response.log"]
    }
    assert captured["run_count"] == 1
    assert not any(
        event["type"] == "evaluator_execution_repair"
        and event["outcome"] == "scheduled"
        for event in controller.runtime.callback_handler.events
    )
    assert next(item for item in state.tasks if item.task_uid == task.task_uid).status == "done"


def test_controller_does_not_match_root_requirement_to_another_route(monkeypatch, tmp_path):
    artifact = tmp_path / "artifacts" / "api-config.log"
    artifact.parent.mkdir()
    artifact.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))

    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Request the application root",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement("root", "Request /", "/")],
    )
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    task = Task(
        task_uid="wrong-route",
        title="Request root",
        objective="Request the root endpoint",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=_artifact_acceptance(),
    )
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="api-config-request",
        tool_name="http_request",
        success=True,
        correctable=False,
        input_summary='{"url":"http://target.test/api/config"}',
        output_summary="artifact:artifacts/api-config.log",
        artifact_refs=("artifact:artifacts/api-config.log",),
    )

    assert controller._resolve_controller_execution_evidence(plan, task, criterion, [outcome]) == {}


def test_task_prompt_builder_rejects_task_from_another_phase():
    plan = _plan()
    task = TaskModel(
        task_uid="wrong-phase",
        title="Impact demonstration",
        objective="Demonstrate impact",
        acceptance=_acceptance(),
        phase=2,
        status="active",
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )

    with pytest.raises(TaskPromptBuildError, match="does not match active phase"):
        controller._build_task_prompt(plan, plan.phases[0], task)


def test_controller_reconciles_curl_evidence_before_loop_replacement(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Request the root",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement("criterion-1-execution-1", "Request root", "/")],
    )
    task = TaskModel(
        task_uid="loop-proof",
        title="Request root",
        objective="Request root",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Bounded request",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["http_request"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=state
    )
    artifact = tmp_path / "artifacts" / "root.txt"
    artifact.parent.mkdir()
    artifact.write_text("HTTP/1.1 200 OK")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="curl-root",
        tool_name="shell",
        success=True,
        correctable=False,
        input_summary='{"command":"curl -sS -D - http://target.test/"}',
        output_summary="artifact:artifacts/root.txt",
        artifact_refs=("artifact:artifacts/root.txt",),
    )

    assert controller._all_execution_requirements_resolved(plan, task, [outcome]) is True


def test_controller_aggregates_request_receipts_into_enumeration_evidence(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Map the root response surface",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement("root", "Request and enumerate root", "/")],
    )
    task = TaskModel(
        task_uid="request-collection",
        title="Map routes",
        objective="Map routes",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Bounded requests",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["request", "enumerate"],
                    "limits": {"max_requests": 3},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=state
    )
    artifact = tmp_path / "artifacts" / "root.txt"
    artifact.parent.mkdir()
    artifact.write_text("HTTP/1.1 200 OK", encoding="utf-8")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    root = ToolOutcome(
        sequence=1,
        tool_use_id="root",
        tool_name="http_request",
        success=True,
        correctable=False,
        input_summary='{"url":"http://target.test/"}',
        output_summary="artifact:artifacts/root.txt",
        artifact_refs=("artifact:artifacts/root.txt",),
        execution_receipts=(ExecutionReceipt("http_request", ("http://target.test/",), 1),),
    )
    routes = ToolOutcome(
        sequence=2,
        tool_use_id="routes",
        tool_name="http_request",
        success=True,
        correctable=False,
        input_summary='{"url":"http://target.test/api"}',
        output_summary="200",
        execution_receipts=(ExecutionReceipt(
            "http_request",
            ("http://target.test/api", "http://target.test/login"),
            2,
            True,
        ),),
    )

    assert controller._resolve_controller_execution_evidence(plan, task, criterion, [root, routes]) == {
        "root": ["artifact:artifacts/root.txt"]
    }
    stored = state.list_tasks()[0].recovery_context["controller_execution_evidence"]["root"]
    assert stored["receipt_mode"] == "aggregated_request_collection"
    assert stored["tool_use_ids"] == ["root", "routes"]


def test_controller_does_not_infer_enumeration_from_duplicate_request_receipts(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Map the root response surface",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement("root", "Request and enumerate root", "/")],
    )
    task = TaskModel(
        task_uid="duplicate-requests",
        title="Map routes",
        objective="Map routes",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Bounded requests",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["request", "enumerate"],
                    "limits": {"max_requests": 2},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=FakeState(plan, tasks=[task])
    )
    artifact = tmp_path / "artifacts" / "root.txt"
    artifact.parent.mkdir()
    artifact.write_text("HTTP/1.1 200 OK", encoding="utf-8")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    root = ToolOutcome(
        sequence=1,
        tool_use_id="root",
        tool_name="http_request",
        success=True,
        correctable=False,
        input_summary='{"url":"http://target.test/"}',
        output_summary="artifact:artifacts/root.txt",
        artifact_refs=("artifact:artifacts/root.txt",),
        execution_receipts=(ExecutionReceipt("http_request", ("http://target.test/",), 2),),
    )

    assert controller._resolve_controller_execution_evidence(plan, task, criterion, [root]) == {}


@pytest.mark.parametrize("subject_ref", ["/api/products/:id", "target:target-1"])
def test_controller_does_not_use_unrelated_analysis_artifact_as_execution_evidence(
    monkeypatch,
    tmp_path,
    subject_ref,
):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Analyze the assigned subject",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Analyze the assigned subject",
            subject_ref,
        )],
    )
    task = TaskModel(
        task_uid="unrelated-analysis-proof",
        title="Analyze subject",
        objective="Analyze subject",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Bounded analysis",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["analyze"],
                    "limits": {"max_items": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )
    artifact = tmp_path / "artifacts" / "unrelated.txt"
    artifact.parent.mkdir()
    artifact.write_text("unrelated analysis")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="analysis-1",
        tool_name="editor",
        success=True,
        correctable=False,
        input_summary='{"path":"/tmp/unrelated-source.txt"}',
        output_summary="artifact:artifacts/unrelated.txt",
        artifact_refs=("artifact:artifacts/unrelated.txt",),
    )

    assert controller._resolve_controller_execution_evidence(plan, task, criterion, [outcome]) == {}


def test_controller_matches_target_execution_subject_to_concrete_target_value():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "/workspace/source", "filesystem")],
    )
    task = TaskModel(
        task_uid="source-analysis-proof",
        title="Analyze source",
        objective="Analyze source",
        acceptance=_acceptance(),
        phase=1,
        status="active",
        target_ids=["target-1"],
    )
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="analysis-1",
        tool_name="editor",
        success=True,
        correctable=False,
        input_summary='{"path":"/workspace/source/app.py"}',
        output_summary="analysis complete",
    )

    assert MultiAgentWorkflowController._outcome_matches_execution_subject(
        plan,
        task,
        "target:target-1",
        outcome,
    )


def test_controller_matches_root_route_to_target_url_without_trailing_slash():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "http://target.test:8080", "network")],
    )
    task = TaskModel(
        task_uid="root-route-proof",
        title="Crawl root",
        objective="Crawl root",
        acceptance=_acceptance(),
        phase=1,
        status="active",
        target_ids=["target-1"],
    )
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="crawl-1",
        tool_name="specialized_recon_orchestrator",
        success=True,
        correctable=False,
        input_summary='{"target":"http://target.test:8080"}',
        output_summary="crawl complete",
    )

    assert MultiAgentWorkflowController._outcome_matches_execution_subject(plan, task, "/", outcome)


def test_controller_matches_root_url_with_trailing_slash_from_structured_shell_input():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "http://192.168.253.101:3001", "network")],
    )
    task = TaskModel(
        task_uid="root-url-proof",
        title="Probe root",
        objective="Probe root",
        acceptance=_acceptance(),
        phase=1,
        status="active",
        target_ids=["target-1"],
    )
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="shell-1",
        tool_name="shell",
        success=True,
        correctable=False,
        input_summary="x" * 500,
        output_summary="request complete",
        structured_input={"command": "curl -sS http://192.168.253.101:3001/"},
    )

    assert MultiAgentWorkflowController._outcome_matches_execution_subject(plan, task, "/", outcome)
    assert MultiAgentWorkflowController._outcome_matches_execution_subject(plan, task, "target:target-1", outcome)


@pytest.mark.parametrize(
    "tool_url",
    [
        "http://192.168.253.101:3002/",
        "https://192.168.253.101:3001/",
        "http://192.168.253.102:3001/",
        "http://192.168.253.101:3001/admin",
    ],
)
def test_controller_keeps_root_execution_subject_scope_exact(tool_url):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "http://192.168.253.101:3001", "network")],
    )
    task = TaskModel(
        task_uid="root-url-scope",
        title="Probe root",
        objective="Probe root",
        acceptance=_acceptance(),
        phase=1,
        status="active",
        target_ids=["target-1"],
    )
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="shell-1",
        tool_name="shell",
        success=True,
        correctable=False,
        input_summary="x" * 500,
        output_summary="request complete",
        structured_input={"command": f"curl -sS {tool_url}"},
    )

    assert not MultiAgentWorkflowController._outcome_matches_execution_subject(plan, task, "/", outcome)


def test_controller_does_not_match_root_route_from_artifact_path_alone():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    task = TaskModel(
        task_uid="root-route-artifact-only",
        title="Crawl root",
        objective="Crawl root",
        acceptance=_acceptance(),
        phase=1,
        status="active",
        target_ids=["target-1"],
    )
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="artifact-1",
        tool_name="editor",
        success=True,
        correctable=False,
        input_summary='{"path":"/app/outputs/current/artifacts/result.json"}',
        output_summary="artifact:artifacts/result.json",
    )

    assert not MultiAgentWorkflowController._outcome_matches_execution_subject(plan, task, "/", outcome)


def test_controller_accepts_valid_inventory_manifest_from_unknown_mcp_producer(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Inventory", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Create the inventory",
        evidence_requirements=[EvidenceRequirement(kind="inventory_manifest")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce execution evidence for crawl against target:target-1.",
            "target:target-1",
        )],
    )
    task = TaskModel(
        task_uid="mcp-inventory",
        title="Create inventory",
        objective="Create inventory",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Crawl the target",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["crawl", "spider"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "inventory_manifest",
                },
            ),
            criteria=[criterion],
        ),
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )
    artifact = tmp_path / "artifacts" / "mcp-inventory.json"
    artifact.parent.mkdir()
    artifact.write_text("{}")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    def load_inventory_manifest(_plan, reference):
        if reference != "artifact:artifacts/mcp-inventory.json":
            raise ValueError("not an inventory manifest")
        return {"schema_version": 1, "items": [{}], "unassessed_gaps": []}, reference

    monkeypatch.setattr(controller, "_load_controller_inventory_manifest", load_inventory_manifest)
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="mcp-1",
        tool_name="mcp_inventory_producer",
        success=True,
        correctable=False,
        input_summary='{"target":"http://target.test"}',
        output_summary="artifact:artifacts/mcp-inventory.json",
        artifact_refs=("artifact:artifacts/mcp-inventory.json",),
    )

    resolved = controller._resolve_controller_execution_evidence(plan, task, criterion, [outcome])

    assert resolved == {"criterion-1-execution-1": ["artifact:artifacts/mcp-inventory.json"]}


def test_controller_links_inventory_converter_to_subject_bound_recon_execution(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Inventory", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Create root inventory",
        evidence_requirements=[EvidenceRequirement(kind="inventory_manifest")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce execution evidence for crawl against /.",
            "/",
        )],
    )
    task = TaskModel(
        task_uid="linked-recon-inventory",
        title="Create inventory",
        objective="Crawl the root and create an inventory",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Crawl root",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["crawl"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "inventory_manifest",
                },
            ),
            criteria=[criterion],
        ),
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    recon_path = artifacts / "recon.json"
    manifest_path = artifacts / "inventory.json"
    recon_path.write_text("{}")
    manifest_path.write_text("{}")
    paths = {
        "artifact:artifacts/recon.json": str(recon_path),
        "artifact:artifacts/inventory.json": str(manifest_path),
    }
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda reference: paths[reference])
    def load_inventory_manifest(_plan, reference):
        if reference != "artifact:artifacts/inventory.json":
            raise ValueError("not an inventory manifest")
        return {"schema_version": 1, "items": [{}], "unassessed_gaps": []}, reference

    monkeypatch.setattr(controller, "_load_controller_inventory_manifest", load_inventory_manifest)
    recon = ToolOutcome(
        sequence=1,
        tool_use_id="recon-1",
        tool_name="specialized_recon_orchestrator",
        success=True,
        correctable=False,
        input_summary='{"target":"http://target.test"}',
        output_summary="artifact:artifacts/recon.json",
        artifact_refs=("artifact:artifacts/recon.json",),
    )
    converter = ToolOutcome(
        sequence=2,
        tool_use_id="converter-1",
        tool_name="recon_output_to_inventory_manifest",
        success=True,
        correctable=False,
        input_summary='{"source_artifact":"artifact:artifacts/recon.json"}',
        output_summary="artifact:artifacts/inventory.json",
        artifact_refs=("artifact:artifacts/inventory.json",),
    )

    resolved = controller._resolve_controller_execution_evidence(plan, task, criterion, [recon, converter])

    assert resolved == {"criterion-1-execution-1": ["artifact:artifacts/inventory.json"]}


def test_controller_accepts_target_bound_artifact_from_unknown_mcp_producer(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Analysis", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Run the assigned procedure",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce execution evidence for a custom procedure.",
            "target:target-1",
        )],
    )
    task = TaskModel(
        task_uid="mcp-artifact",
        title="Run custom procedure",
        objective="Run custom procedure",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Custom procedure",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["custom_method"],
                    "limits": {"max_items": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )
    artifact = tmp_path / "artifacts" / "mcp-result.txt"
    artifact.parent.mkdir()
    artifact.write_text("result")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="mcp-1",
        tool_name="mcp_custom_producer",
        success=True,
        correctable=False,
        input_summary='{"target":"http://target.test"}',
        output_summary="artifact:artifacts/mcp-result.txt",
        artifact_refs=("artifact:artifacts/mcp-result.txt",),
    )

    resolved = controller._resolve_controller_execution_evidence(plan, task, criterion, [outcome])

    assert resolved == {"criterion-1-execution-1": ["artifact:artifacts/mcp-result.txt"]}


def test_controller_excludes_editor_from_execution_receipts(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Analysis", status="active")],
        targets=[OperationTarget("target-1", "/workspace/source", "filesystem")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Analyze source",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Analyze the assigned source",
            "target:target-1",
        )],
    )
    task = TaskModel(
        task_uid="editor-receipt",
        title="Analyze source",
        objective="Analyze source",
        acceptance=_artifact_acceptance(),
        phase=1,
        status="active",
        target_ids=["target-1"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )
    artifact = tmp_path / "artifacts" / "editor-output.txt"
    artifact.parent.mkdir()
    artifact.write_text("edited")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="editor-1",
        tool_name="editor",
        success=True,
        correctable=False,
        input_summary='{"path":"/workspace/source/app.py"}',
        output_summary="artifact:artifacts/editor-output.txt",
        artifact_refs=("artifact:artifacts/editor-output.txt",),
    )

    assert controller._resolve_controller_execution_evidence(plan, task, criterion, [outcome]) == {}


def test_controller_does_not_resolve_unknown_execution_capability(monkeypatch, tmp_path):
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Perform a custom operation",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement("execution-1", "Custom operation", "target:target-1")],
    )
    task = TaskModel(
        task_uid="unknown-proof",
        title="Custom operation",
        objective="Custom operation",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Custom procedure",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["custom_method"],
                    "limits": {"max_items": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Test", status="active")],
        targets=[OperationTarget("target-1", "target.test", "network")],
    )
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))

    assert controller._resolve_controller_execution_evidence(plan, task, criterion, []) == {}


def test_task_creator_rejects_unsupported_synthesis_execution_before_model_retry(monkeypatch):
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
    )
    phase = _plan().phases[0]
    monkeypatch.setattr(
        controller,
        "_phase_task_contract",
        lambda _phase: SimpleNamespace(
            phase_id=phase.id,
            mode="fanout_with_synthesis",
            synthesis_execution="runtime",
        ),
    )

    with pytest.raises(WorkflowInvariantError, match="not executable"):
        controller._validate_phase_task_contract_execution(phase)

    assert {
        "type": "phase_task_contract_validation",
        "phase": phase.id,
        "outcome": "rejected",
        "code": "contract_unsatisfiable",
        "reason": "synthesis contract requires unsupported execution mode",
    } in controller.runtime.callback_handler.events


def test_controller_acceptance_replay_contains_rejection_within_task():
    task = TaskModel(
        task_uid="pending-replay",
        title="Pending replay",
        objective="Complete replay",
        acceptance=_acceptance(),
        phase=1,
        status="active",
        recovery_context={"pending_controller_acceptance": {"status": "satisfied"}},
    )
    state = FakeState(_plan(), tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    def reject_replay(**_payload):
        raise ValueError("artifact is invalid")

    decision = controller._replay_controller_acceptance(
        task,
        reject_replay,
        {"status": "satisfied"},
    )

    assert decision is not None
    assert decision.status == "partial_failure"
    assert "artifact is invalid" in decision.reason
    assert state.tasks[0].recovery_context["pending_controller_acceptance"] == {"status": "satisfied"}
    assert any(
        event["type"] == "controller_acceptance_replay" and event["outcome"] == "rejected"
        for event in controller.runtime.callback_handler.events
    )


def test_controller_acceptance_replay_clears_pending_payload_after_success():
    task = TaskModel(
        task_uid="successful-replay",
        title="Successful replay",
        objective="Complete replay",
        acceptance=_acceptance(),
        phase=1,
        status="active",
        recovery_context={"pending_controller_acceptance": {"status": "satisfied"}},
    )
    state = FakeState(_plan(), tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )
    replayed = []

    decision = controller._replay_controller_acceptance(
        task,
        lambda **payload: replayed.append(payload),
        {"status": "satisfied"},
    )

    assert decision is None
    assert replayed == [{"status": "satisfied"}]
    assert "pending_controller_acceptance" not in state.tasks[0].recovery_context
    assert any(
        event["type"] == "controller_acceptance_replay" and event["outcome"] == "accepted"
        for event in controller.runtime.callback_handler.events
    )


def test_controller_reconciles_same_cycle_inventory_evidence_before_acceptance_recovery(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Inventory", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Create the inventory",
        evidence_requirements=[EvidenceRequirement(kind="inventory_manifest")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce execution evidence for crawl against target:target-1.",
            "target:target-1",
        )],
    )
    pending_payload = {
        "status": "satisfied",
        "disposition": "observation",
        "summary": "Katana crawl was converted into an inventory manifest.",
        "evidence_refs": ["artifact:artifacts/inventory_manifest.json"],
    }
    task = TaskModel(
        task_uid="deferred-inventory-acceptance",
        title="Create inventory",
        objective="Crawl the target",
        phase=1,
        status="active",
        target_ids=["target-1"],
        recovery_context={"pending_controller_acceptance": pending_payload},
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Crawl the target",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["crawl"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "inventory_manifest",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )
    artifact = tmp_path / "artifacts" / "inventory_manifest.json"
    artifact.parent.mkdir()
    artifact.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    monkeypatch.setattr(
        controller,
        "_load_controller_inventory_manifest",
        lambda _plan, reference: ({"schema_version": 1, "items": [{}], "unassessed_gaps": []}, reference),
    )
    manifest_outcome = ToolOutcome(
        sequence=1,
        tool_use_id="manifest-1",
        tool_name="recon_output_to_inventory_manifest",
        success=True,
        correctable=False,
        input_summary='{"target":"http://target.test"}',
        output_summary="artifact:artifacts/inventory_manifest.json",
        artifact_refs=("artifact:artifacts/inventory_manifest.json",),
    )
    provisional_failure = ToolOutcome(
        sequence=2,
        tool_use_id="acceptance-provisional",
        tool_name="record_task_acceptance",
        success=False,
        correctable=False,
        input_summary=json.dumps(pending_payload, sort_keys=True),
        output_summary="Execution evidence is required before acceptance.",
        structured_input=pending_payload,
    )

    decision, ignored_failures = controller._reconcile_pending_controller_acceptance(
        plan,
        task,
        lambda **payload: state.record_task_acceptance(task, payload),
        [manifest_outcome, provisional_failure],
        [manifest_outcome, provisional_failure],
    )

    assert decision is None
    assert ignored_failures == {"acceptance-provisional"}
    assert state.list_task_acceptance_results(task.task_uid)[0].summary == pending_payload["summary"]
    assert "pending_controller_acceptance" not in state.tasks[0].recovery_context
    assert any(
        event["type"] == "controller_acceptance_replay"
        and event["outcome"] == "accepted_deferred_reconciliation"
        for event in controller.runtime.callback_handler.events
    )


def test_controller_keeps_pending_acceptance_when_same_cycle_evidence_is_invalid(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Inventory", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Create the inventory",
        evidence_requirements=[EvidenceRequirement(kind="inventory_manifest")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce execution evidence for crawl against target:target-1.",
            "target:target-1",
        )],
    )
    pending_payload = {
        "status": "satisfied",
        "disposition": "observation",
        "summary": "Inventory was created.",
        "evidence_refs": ["artifact:artifacts/inventory_manifest.json"],
    }
    task = TaskModel(
        task_uid="invalid-deferred-inventory",
        title="Create inventory",
        objective="Crawl the target",
        phase=1,
        status="active",
        target_ids=["target-1"],
        recovery_context={"pending_controller_acceptance": pending_payload},
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Crawl the target",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["crawl"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "inventory_manifest",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )
    artifact = tmp_path / "artifacts" / "inventory_manifest.json"
    artifact.parent.mkdir()
    artifact.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    monkeypatch.setattr(controller, "_load_controller_inventory_manifest", Mock(side_effect=ValueError("invalid")))
    provisional_failure = ToolOutcome(
        sequence=1,
        tool_use_id="acceptance-provisional",
        tool_name="record_task_acceptance",
        success=False,
        correctable=False,
        input_summary=json.dumps(pending_payload, sort_keys=True),
        output_summary="Execution evidence is required before acceptance.",
        structured_input=pending_payload,
    )
    manifest_outcome = ToolOutcome(
        sequence=2,
        tool_use_id="manifest-1",
        tool_name="recon_output_to_inventory_manifest",
        success=True,
        correctable=False,
        input_summary='{"target":"http://target.test"}',
        output_summary="artifact:artifacts/inventory_manifest.json",
        artifact_refs=("artifact:artifacts/inventory_manifest.json",),
    )

    decision, ignored_failures = controller._reconcile_pending_controller_acceptance(
        plan,
        task,
        lambda **_payload: pytest.fail("invalid manifest must not be replayed"),
        [manifest_outcome, provisional_failure],
        [manifest_outcome, provisional_failure],
    )

    assert decision is None
    assert ignored_failures == set()
    assert state.tasks[0].recovery_context["pending_controller_acceptance"] == pending_payload
    assert state.list_task_acceptance_results(task.task_uid) == []


def test_output_prerequisite_requires_typed_current_operation_artifact(monkeypatch, tmp_path):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Inventory", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    requirement = ExecutionRequirement(
        "criterion-1-execution-1",
        "Produce execution evidence for crawl against target:target-1.",
        "target:target-1",
    )
    task = TaskModel(
        task_uid="output-prerequisite",
        title="Create inventory",
        objective="Crawl the target",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Crawl the target",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["crawl"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "inventory_manifest",
                },
            ),
            criteria=[AcceptanceCriterion(
                id="criterion-1",
                description="Create the inventory",
                evidence_requirements=[EvidenceRequirement(kind="inventory_manifest")],
                execution_requirements=[requirement],
            )],
        ),
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )
    artifact = tmp_path / "artifacts" / "recon.json"
    artifact.parent.mkdir()
    artifact.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="recon-1",
        tool_name="specialized_recon_orchestrator",
        success=True,
        correctable=False,
        input_summary='{"target":"http://target.test"}',
        output_summary="artifact:artifacts/recon.json",
        artifact_refs=("artifact:artifacts/recon.json",),
    )

    monkeypatch.setattr(controller, "_load_controller_inventory_manifest", Mock(side_effect=ValueError("invalid")))
    assert controller._has_valid_required_output(plan, task, [outcome]) is False

    monkeypatch.setattr(
        controller,
        "_load_controller_inventory_manifest",
        lambda _plan, reference: ({"schema_version": 1, "items": [{}], "unassessed_gaps": []}, reference),
    )
    assert controller._has_valid_required_output(plan, task, [outcome]) is True


def test_output_prerequisite_prompt_keeps_acceptance_replay_controller_owned():
    plan = _plan()
    task = Task(task_uid="output-repair", title="Output repair", objective="Create required output", phase=1, status="active")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )

    prompt = controller._output_prerequisite_recovery_prompt(plan, task, [], next_cycle=2)

    assert "Required output kind: inventory_manifest" in prompt
    assert "controller retained the prior acceptance submission" in prompt.lower()
    assert "Do not call or attempt to describe record_task_acceptance." in prompt


def test_plan_phase_accepts_partial_failure_and_blocked_statuses():
    assert PlanPhase(id=1, title="Phase", status="partial_failure").status == "partial_failure"
    assert PlanPhase(id=2, title="Phase", status="blocked").status == "blocked"
    assert PlanPhase(id=3, title="Phase", status="not_applicable").status == "not_applicable"


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("complete", "done"),
        ("completed", "done"),
        ("success", "done"),
        ("successful", "done"),
        ("failed", "partial_failure"),
        ("failure", "partial_failure"),
        ("error", "partial_failure"),
    ],
)
def test_workflow_decision_status_aliases_normalize_to_canonical_values(alias, canonical):
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
    )

    decision = controller._decision_from_data(
        {"status": alias, "reason": "test"},
        allowed=("done", "partial_failure", "blocked"),
    )

    assert decision.status == canonical


def test_workflow_decision_status_normalizes_phase_continuation_aliases_only_when_allowed():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
    )

    decision = controller._decision_from_data(
        {"status": "in progress", "reason": "test"},
        allowed=("continue", "done", "partial_failure", "blocked"),
    )

    assert decision.status == "continue"
    with pytest.raises(WorkflowInvariantError, match="Invalid workflow decision status: continue"):
        controller._decision_from_data(
            {"status": "ongoing", "reason": "test"},
            allowed=("done", "partial_failure", "blocked"),
        )


def test_workflow_decision_status_keeps_unknown_values_invalid():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
    )

    with pytest.raises(WorkflowInvariantError, match="Invalid workflow decision status: finished_successfully"):
        controller._decision_from_data(
            {"status": "finished successfully", "reason": "test"},
            allowed=("done", "partial_failure", "blocked"),
        )


def test_task_evaluator_completed_alias_does_not_abort_workflow():
    plan = _plan()
    task = Task("task-evaluator-alias", "Assess", "Assess the target", 1, "active")
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: '{"status":"completed","reason":"acceptance is supported"}',
    )

    decision = controller._evaluate_task(
        plan,
        plan.phases[0],
        task,
        acceptance_results=state.list_task_acceptance_results(task.task_uid),
    )

    assert decision.status == "done"


def test_task_evaluator_cannot_reopen_controller_accepted_execution_gate():
    plan = _plan()
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Crawl the assigned target",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce execution evidence for crawl against target:target-1.",
            "target:target-1",
        )],
    )
    task = TaskModel(
        task_uid="accepted-execution-gate",
        title="Crawl target",
        objective="Crawl target",
        phase=1,
        status="active",
        target_ids=["target-1"],
        recovery_context={
            "execution_evidence_receipts": {
                "criterion-1-execution-1": ["artifact:artifacts/crawl.json"],
            },
        },
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Crawl target",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["crawl"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task])
    state.acceptance_results[task.task_uid] = [AcceptanceResult(
        criterion_id="criterion-1",
        status="satisfied",
        disposition="observation",
        summary="Controller replay accepted the crawl evidence.",
        evidence_refs=("artifact:artifacts/crawl.json",),
    )]
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: (
            '{"status":"partial_failure","reason":"The prior acceptance call lacked execution evidence.",'
            '"instructions":"Gather crawl evidence.",'
            '"repair":{"kind":"execution","evidence_gaps":["crawl receipt"]}}'
        ),
    )

    decision = controller._evaluate_task(
        plan,
        plan.phases[0],
        task,
        acceptance_results=state.list_task_acceptance_results(task.task_uid),
    )

    assert decision.status == "done"
    assert "Controller acceptance is authoritative" in decision.reason
    assert any(
        event["type"] == "evaluator_execution_gate_reconciled"
        and event["outcome"] == "suppressed_stale_execution_feedback"
        for event in controller.runtime.callback_handler.events
    )


def test_task_evaluator_execution_feedback_remains_when_controller_receipt_is_missing():
    plan = _plan()
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Crawl the assigned target",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce execution evidence for crawl against target:target-1.",
            "target:target-1",
        )],
    )
    task = TaskModel(
        task_uid="missing-execution-receipt",
        title="Crawl target",
        objective="Crawl target",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Crawl target",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["crawl"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task])
    decision = workflow_mod.WorkflowDecision(
        status="partial_failure",
        reason="Execution evidence is missing.",
        repair_kind="execution",
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    assert not controller._evaluator_reopens_resolved_execution_gate(
        task,
        state.list_task_acceptance_results(task.task_uid),
        decision,
    )


def test_incomplete_execution_gate_forces_controller_owned_execution_repair():
    plan = _plan()
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Crawl the assigned target",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce execution evidence for crawl against target:target-1.",
            "target:target-1",
        )],
    )
    task = TaskModel(
        task_uid="incomplete-execution-gate",
        title="Crawl target",
        objective="Crawl target",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Crawl target",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["crawl"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task])
    state.acceptance_results[task.task_uid] = [AcceptanceResult(
        criterion_id="criterion-1",
        status="satisfied",
        disposition="finding_candidate",
        summary="Worker reported successful crawl evidence.",
        evidence_refs=("artifact:artifacts/crawl.json",),
    )]
    evaluator_calls = []

    def unexpected_evaluator_call(*args):
        evaluator_calls.append(args)
        raise AssertionError("incomplete execution gates must not invoke the LLM evaluator")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=unexpected_evaluator_call,
    )

    decision = controller._evaluate_task(
        plan,
        plan.phases[0],
        task,
        acceptance_results=state.list_task_acceptance_results(task.task_uid),
    )

    assert evaluator_calls == []
    assert decision.status == "partial_failure"
    assert decision.repair_kind == "execution"
    assert decision.unresolved_evidence_gaps == (
        "criterion-1-execution-1: Produce execution evidence for crawl against target:target-1. "
        + "(subject: target:target-1)",
    )
    assert "execution gate is incomplete" in decision.reason


def test_task_evaluator_retries_schema_valid_non_decision_response():
    plan = _plan()
    task = Task(task_uid="schema-retry", title="Assess", objective="run", phase=1, status="active")
    state = FakeState(plan, tasks=[task])
    responses = iter([
        '{"action":"record_task_acceptance","action_input":{}}',
        '{"status":"done","reason":"acceptance is supported"}',
    ])

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: next(responses),
    )

    decision = controller._evaluate_task(plan, plan.phases[0], task)

    assert decision.status == "done"
    activities = [event for event in controller.runtime.callback_handler.events if event["type"] == "workflow_activity"]
    assert [(event["status"], event["attempt"]) for event in activities] == [
        ("started", 1),
        ("failed", 1),
        ("started", 2),
        ("completed", 2),
    ]


def test_task_evaluator_schema_failure_falls_back_to_partial_failure():
    plan = _plan()
    task = Task(task_uid="schema-fallback", title="Assess", objective="run", phase=1, status="active")
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: '{"action":"record_task_acceptance","action_input":{}}',
    )

    decision = controller._evaluate_task(plan, plan.phases[0], task)

    assert decision.status == "partial_failure"
    assert "received keys: action, action_input" in decision.reason
    event = next(event for event in controller.runtime.callback_handler.events if event["type"] == "evaluator_fallback")
    assert event["role"] == "task_evaluator"
    assert event["task_uid"] == "schema-fallback"
    assert event["source"] == "schema_validation"
    assert event["received_keys"] == ["action", "action_input"]


@pytest.mark.parametrize("response", ["", "not valid JSON"])
def test_task_evaluator_parse_failure_falls_back_to_partial_failure(response):
    plan = _plan()
    task = Task(task_uid="parse-fallback", title="Assess", objective="run", phase=1, status="active")
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: response,
    )

    decision = controller._evaluate_task(plan, plan.phases[0], task)

    assert decision.status == "partial_failure"
    assert "existing evidence was preserved" in decision.reason
    event = next(event for event in controller.runtime.callback_handler.events if event["type"] == "evaluator_fallback")
    assert event["role"] == "task_evaluator"
    assert event["source"] == "json_parse_failure"
    assert event["empty_response"] is (response == "")
    assert event["task_uid"] == "parse-fallback"


def test_empty_task_evaluator_response_does_not_retry():
    plan = _plan()
    task = Task(task_uid="empty", title="Assess", objective="run", phase=1, status="active")
    calls = []
    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 3}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
        text_runner=lambda *args: calls.append(args[0]) or "",
    )

    decision = controller._evaluate_task(plan, plan.phases[0], task)

    assert decision.status == "partial_failure"
    assert calls == ["task_evaluator"]


def test_task_evaluator_artifact_read_limit_exhaustion_synthesizes_without_tools():
    plan = _plan()
    task = Task(task_uid="artifact-limit", title="Assess", objective="run", phase=1, status="active")
    calls = []

    def text_runner(*args):
        calls.append(args)
        if len(calls) == 1:
            raise workflow_mod.EvaluatorArtifactReadLimitExceeded("read allowance exhausted")
        return '{"status":"done","reason":"controller receipt supports the criterion","instructions":""}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 3}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
        text_runner=text_runner,
    )

    decision = controller._evaluate_task(plan, plan.phases[0], task)

    assert decision.status == "done"
    assert len(calls) == 2
    assert calls[0][0] == "task_evaluator"
    assert calls[1][0] == "task_evaluator"
    assert calls[1][2] == []
    assert "Final synthesis after artifact-read hard stop" in calls[1][1]
    event = next(
        event for event in controller.runtime.callback_handler.events if event["type"] == "evaluator_artifact_synthesis"
    )
    assert event["source"] == "artifact_read_limit_hard_stop"
    assert event["task_uid"] == "artifact-limit"


def test_non_evaluator_parse_failure_remains_an_invariant_error():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda *args: "not valid JSON",
    )

    with pytest.raises(WorkflowInvariantError, match="task_prompt_builder returned invalid JSON"):
        controller._run_json_text_agent("task_prompt_builder", "prompt", [], "system")


def test_pending_finding_validation_is_prioritized_and_events_include_scope():
    plan = _plan()
    standard = Task("task-1", "Standard", "Do standard work", 1, "pending", created_at="1")
    validation = Task(
        "task-2",
        "Verify finding: Admin",
        "Verify finding",
        1,
        "pending",
        created_at="2",
        kind="finding_validation",
        reference_id="finding-1",
        target_scope="subset",
        target_ids=["target-1"],
    )
    state = FakeState(plan, [standard, validation])
    runtime = _runtime()
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    assert controller._get_pending_task(1) == validation

    controller._emit_task_started(validation)
    event = runtime.callback_handler.events[-1]
    assert event["target_scope"] == "subset"
    assert event["target_ids"] == ["target-1"]

    assert PlanPhase(id=3, title="Phase", status="complete").status == "done"


def test_extract_json_object_handles_fences_embedded_json_and_invalid_values():
    assert extract_json_object('```json\n{"status":"done"}\n```') == {"status": "done"}
    assert extract_json_object('prefix {"status":"blocked"} suffix') == {"status": "blocked"}

    with pytest.raises(ValueError, match="agent response"):
        extract_json_object({"status": "done"})
    with pytest.raises(ValueError, match="JSON object"):
        extract_json_object('["not", "object"]')


def test_extract_result_text_handles_common_result_shapes():
    assert extract_result_text(None) == ""
    assert extract_result_text("text") == "text"
    assert extract_result_text(SimpleNamespace(message={"content": [{"text": "a"}, {"text": "b"}]})) == "a\nb"


def test_json_agent_retries_fresh_after_reasoning_loop():
    prompts = []
    error = MaxTokensReachedException("max_tokens")
    error.max_token_classification = MaxTokenClassification(
        kind="reasoning_loop",
        repetition_ratio=0.8,
        pattern_hash="abc123",
        discarded_tokens=6000,
    )

    def text_runner(role, prompt, tools, system_prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise error
        return '{"status":"done"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=text_runner,
    )

    result = controller._run_json_text_agent("task_evaluator", "original", [], "system")

    assert result == {"status": "done"}
    assert len(prompts) == 2
    assert "previous response was discarded" in prompts[1]
    assert "Return only the required JSON object now" in prompts[1]
    assert prompts[1].endswith("original\n")
    assert controller.runtime.callback_handler.efficiency_events == [
        ("max_token_exhaustion", "task_evaluator", "reasoning_loop", 1, None)
    ]


def test_task_evaluator_retry_recreates_its_tool_state_after_max_tokens():
    error = MaxTokensReachedException("max_tokens")
    error.max_token_classification = MaxTokenClassification(
        kind="reasoning_loop",
        repetition_ratio=0.8,
        pattern_hash="abc123",
        discarded_tokens=6000,
    )
    supplied_tools = []

    def text_runner(_role, _prompt, tools, _system_prompt):
        supplied_tools.append(tools)
        if len(supplied_tools) == 1:
            raise error
        return '{"status":"done"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=text_runner,
    )
    tool_factory_calls = []

    def tool_factory():
        tool_factory_calls.append(None)
        return [object()]

    result = controller._run_json_text_agent(
        "task_evaluator", "original", [], "system", tool_factory=tool_factory
    )

    assert result == {"status": "done"}
    assert len(tool_factory_calls) == 2
    assert supplied_tools[0] is not supplied_tools[1]
    assert supplied_tools[0][0] is not supplied_tools[1][0]


def test_taxonomy_annotator_retry_recreates_bounded_artifact_reader_after_max_tokens(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Validation", status="done")],
        assessment_complete=True,
    )
    state = FakeState(
        plan,
        finding_records=[
            {
                "finding_uid": "finding-1",
                "candidate_data": {"artifacts": ["artifact:artifacts/proof.txt"]},
                "verification_task_uid": "verify-finding-1",
                "validation_data": {"outcome": "confirmed"},
                "resolution": "verified",
            }
        ],
    )
    readers = []
    supplied_tools = []

    class Catalog:
        def candidates(self, _finding, kind, limit=12):
            return [{"id": "CWE-79" if kind == "cwe" else "T1190", "name": "Candidate"}]

    def bounded_reader(**_kwargs):
        reader = object()
        readers.append(reader)
        return reader

    def text_runner(_role, _prompt, tools, _system_prompt):
        supplied_tools.append(tools)
        if len(supplied_tools) == 1:
            raise MaxTokensReachedException("max_tokens")
        return (
            '{"cwe":[{"id":"CWE-79","confidence":0.95,"rationale":"Proof",'
            '"evidence":["artifact:artifacts/proof.txt"]}],"mitre_attack":[]}'
        )

    monkeypatch.setattr(workflow_mod, "create_bounded_artifact_reader", bounded_reader)
    monkeypatch.setattr(workflow_mod, "get_taxonomy_catalog", lambda: Catalog())
    monkeypatch.setattr(
        workflow_mod,
        "validate_taxonomy_mappings",
        lambda cwe, attack, _artifacts: {"cwe": cwe, "mitre_attack": attack, "provenance": {}},
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    controller._annotate_verified_findings(plan)

    assert state.finding_records[0]["candidate_data"]["taxonomy_annotation"]["status"] == "completed"
    assert len(readers) == 2
    assert supplied_tools == [[readers[0]], [readers[1]]]
    assert readers[0] is not readers[1]


def test_attack_enricher_retry_recreates_bounded_artifact_reader_after_max_tokens(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Validation", status="done")],
        assessment_complete=True,
    )
    state = FakeState(
        plan,
        finding_records=[
            {
                "finding_uid": "finding-1",
                "candidate_data": {"artifacts": ["artifact:artifacts/proof.txt"]},
                "resolution": "verified",
            }
        ],
    )
    readers = []
    supplied_tools = []

    class Catalog:
        def candidates(self, _finding, _kind, limit=12):
            return [{"id": "T1059.004", "name": "Unix Shell"}]

    def bounded_reader(**_kwargs):
        reader = object()
        readers.append(reader)
        return reader

    def text_runner(_role, _prompt, tools, _system_prompt):
        supplied_tools.append(tools)
        if len(supplied_tools) == 1:
            raise MaxTokensReachedException("max_tokens")
        return (
            '{"mitre_attack":[{"id":"T1059.004","confidence":0.95,"rationale":"Proof",'
            '"evidence":["artifact:artifacts/proof.txt"]}]}'
        )

    monkeypatch.setattr(workflow_mod, "create_bounded_artifact_reader", bounded_reader)
    monkeypatch.setattr(workflow_mod, "get_taxonomy_catalog", lambda: Catalog())
    monkeypatch.setattr(
        workflow_mod,
        "validate_taxonomy_mappings",
        lambda cwe, attack, _artifacts: {"cwe": cwe, "mitre_attack": attack, "provenance": {}},
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )
    monkeypatch.setattr(
        controller,
        "_finding_behavior_evidence",
        lambda _record: ([{"summary": "Observed shell execution"}], ["artifact:artifacts/proof.txt"]),
    )

    controller._enrich_final_attack_mappings(plan)

    assert state.finding_records[0]["candidate_data"]["final_attack_enrichment"]["status"] == "completed"
    assert len(readers) == 2
    assert supplied_tools == [[readers[0]], [readers[1]]]
    assert readers[0] is not readers[1]


def test_json_agent_emits_workflow_activity_lifecycle_events():
    runtime = _runtime()
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: '{"status":"done"}',
    )

    controller._run_json_text_agent(
        "task_evaluator",
        "original",
        [],
        "system",
        cycle=2,
        cycle_total=3,
    )

    activities = [event for event in runtime.callback_handler.events if event["type"] == "workflow_activity"]
    assert [(event["status"], event["action"]) for event in activities] == [
        ("started", "task_evaluator"),
        ("completed", "task_evaluator"),
    ]
    assert activities[0]["activity"] == "evaluation"
    assert activities[0]["label"] == "Task evaluation"
    assert [(event["cycle"], event["cycle_total"]) for event in activities] == [(2, 3), (2, 3)]
    assert "original" not in activities[0]["content"]


def test_taxonomy_annotator_runs_once_at_terminal_completion(monkeypatch):
    runtime = _runtime()
    seen_trace_attributes = []
    calls = []

    class Catalog:
        def candidates(self, _finding, kind, limit=12):
            return [{"id": "CWE-79" if kind == "cwe" else "T1190", "name": "Candidate"}]

    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Validation", status="done")],
        assessment_complete=True,
    )
    state = FakeState(
        plan,
        finding_records=[
            {
                "finding_uid": "finding-1",
                "resolution": "verified",
                "candidate_data": {
                    "title": "Stored XSS",
                    "claim": "Script executes",
                    "technique": "xss",
                    "artifacts": ["artifact:artifacts/proof.txt"],
                    "source_task_uids": ["task-1"],
                },
                "verification_task_uid": "verify-finding-1",
                "validation_data": {"outcome": "confirmed"},
            },
            {
                "finding_uid": "old-finding",
                "candidate_data": {"source_task_uids": ["task-1"]},
            },
        ],
    )

    def text_runner(role, prompt, tools, system_prompt):
        calls.append((role, prompt, tools, system_prompt))
        seen_trace_attributes.append(dict(runtime.trace_attributes))
        return '{"cwe": [{"id": "CWE-79", "confidence": 0.95, "rationale": "Artifact shows script execution", "evidence": ["artifact:artifacts/proof.txt"]}], "mitre_attack": []}'

    monkeypatch.setattr(workflow_mod, "get_taxonomy_catalog", lambda: Catalog())
    def validate(cwe, attack, artifacts):
        if not cwe[0].get("confidence"):
            raise ValueError("cwe confidence must be a number from 0.0 to 1.0")
        return {"cwe": cwe, "mitre_attack": attack, "provenance": {"version": "test"}}

    monkeypatch.setattr(workflow_mod, "validate_taxonomy_mappings", validate)
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    controller._annotate_verified_findings(plan)
    controller._annotate_verified_findings(plan)

    assert calls[0][0] == "taxonomy_annotator"
    assert len(calls) == 1
    assert len(calls[0][2]) == 1
    assert calls[0][3] == ""
    assert "cwe[1]{id,name}:" in calls[0][1]
    assert "mitre_attack[1]{id,name}:" in calls[0][1]
    assert '"mitre_attack":[{"id":string,"confidence":number,"rationale":string,"evidence":[string]}]' in calls[0][1]
    assert seen_trace_attributes[0]["workflow.finding.uid"] == "finding-1"
    assert seen_trace_attributes[0]["agent.role"] == "taxonomy_annotator"
    assert state.finding_records[0]["candidate_data"]["taxonomy_annotation"]["status"] == "completed"
    assert runtime.trace_attributes == {"operation.id": "OP_TEST"}


def test_taxonomy_annotator_marks_terminal_failure_without_blocking_completion(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Validation", status="done")],
        assessment_complete=True,
    )
    state = FakeState(
        plan,
        finding_records=[
            {
                "finding_uid": "finding-1",
                "resolution": "verified",
                "candidate_data": {
                    "artifacts": ["artifact:artifacts/proof.txt"],
                    "source_task_uids": ["task-1"],
                },
                "verification_task_uid": "verify-finding-1",
                "validation_data": {"outcome": "confirmed"},
            }
        ],
    )

    class Catalog:
        def candidates(self, _finding, _kind, limit=12):
            return []

    monkeypatch.setattr(workflow_mod, "get_taxonomy_catalog", lambda: Catalog())
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "not json",
    )

    controller._annotate_verified_findings(plan)

    assert state.finding_records[0]["candidate_data"]["taxonomy_annotation"]["status"] == "failed"


def test_final_attack_enrichment_runs_before_completion_and_preserves_cwe(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Validation", status="done")],
        assessment_complete=True,
    )
    task = Task("task-1", "Exploit validation", "Confirm shell execution", 1, "done")
    state = FakeState(
        plan,
        [task],
        finding_records=[
            {
                "finding_uid": "finding-1",
                "resolution": "verified",
                "verification_task_uid": "verify-1",
                "candidate_data": {
                    "title": "Command injection",
                    "claim": "Input reached a Unix shell",
                    "source_task_uids": ["task-1"],
                    "artifacts": ["artifact:artifacts/shell-proof.txt"],
                    "taxonomy": {
                        "cwe": [{"id": "CWE-78", "confidence": 0.95}],
                        "mitre_attack": [],
                    },
                },
                "validation_data": {"outcome": "confirmed"},
            }
        ],
    )
    state.acceptance_results["task-1"] = [
        AcceptanceResult(
            criterion_id=task.acceptance.criteria[0].id,
            status="satisfied",
            disposition="existing_finding",
            summary="The proof artifact records /bin/sh command execution.",
            evidence_refs=("artifact:artifacts/shell-proof.txt", "finding:finding-1"),
        )
    ]
    roles = []

    class Catalog:
        def candidates(self, _finding, kind, limit=12):
            if kind == "cwe":
                return [{"id": "CWE-78", "name": "OS Command Injection"}]
            assert kind == "attack"
            return [{"id": "T1059.004", "name": "Unix Shell"}]

    def text_runner(role, prompt, _tools, system_prompt):
        roles.append(role)
        assert system_prompt == ""
        if role == "taxonomy_annotator":
            assert "cwe[1]{id,name}:" in prompt
            return (
                '{"cwe":[{"id":"CWE-78","confidence":0.95,'
                '"rationale":"Observed command injection","evidence":["artifact:artifacts/shell-proof.txt"]}],'
                '"mitre_attack":[]}'
            )
        assert role == "attack_enricher"
        assert "The proof artifact records /bin/sh command execution." in prompt
        assert "mitre_attack[1]{id,name}:" in prompt
        return (
            '{"mitre_attack":[{"id":"T1059.004","confidence":0.96,'
            '"rationale":"Observed Unix shell execution","evidence":["artifact:artifacts/shell-proof.txt"]}]}'
        )

    def validate(cwe, attack, _evidence):
        return {"cwe": cwe or [], "mitre_attack": attack or [], "provenance": {"version": "test"}}

    monkeypatch.setattr(workflow_mod, "get_taxonomy_catalog", lambda: Catalog())
    monkeypatch.setattr(workflow_mod, "validate_taxonomy_mappings", validate)
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0})
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    controller._emit_workflow_completion(plan)

    candidate = state.finding_records[0]["candidate_data"]
    assert roles == ["taxonomy_annotator", "attack_enricher"]
    assert candidate["taxonomy"]["cwe"][0]["id"] == "CWE-78"
    assert candidate["taxonomy"]["mitre_attack"][0]["id"] == "T1059.004"
    assert candidate["final_attack_enrichment"]["status"] == "completed"
    assert runtime.callback_handler.termination_events == [("complete", "Assessment complete: 1 phase evaluated")]


def test_final_attack_enrichment_omits_unconfirmed_findings_and_waits_for_terminal_work(monkeypatch):
    active_plan = _plan()
    active_task = Task("task-1", "Active work", "Continue assessment", 1, "active")
    state = FakeState(
        active_plan,
        [active_task],
        finding_records=[
            {
                "finding_uid": "finding-1",
                "resolution": "verified",
                "candidate_data": {"artifacts": ["artifact:artifacts/proof.txt"]},
            },
            {
                "finding_uid": "finding-2",
                "resolution": "validation_failure",
                "candidate_data": {"artifacts": ["artifact:artifacts/rejected.txt"]},
            },
        ],
    )
    calls = []
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: calls.append(args) or '{"mitre_attack":[]}',
    )

    controller._enrich_final_attack_mappings(active_plan)

    assert calls == []
    assert all("final_attack_enrichment" not in record["candidate_data"] for record in state.finding_records)


def test_final_attack_enrichment_completes_without_model_when_no_durable_evidence():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Validation", status="done")],
        assessment_complete=True,
    )
    state = FakeState(
        plan,
        finding_records=[
            {
                "finding_uid": "finding-1",
                "resolution": "verified",
                "candidate_data": {"claim": "Confirmed but no behavioral evidence"},
            }
        ],
    )
    calls = []
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: calls.append(args) or '{"mitre_attack":[]}',
    )

    controller._enrich_final_attack_mappings(plan)

    enrichment = state.finding_records[0]["candidate_data"]["final_attack_enrichment"]
    assert calls == []
    assert enrichment["status"] == "completed"
    assert enrichment["taxonomy"]["mitre_attack"] == []


def test_attack_enrichment_response_normalizes_wrapper_and_rejects_cwe_output():
    wrapped = {"taxonomy": {"mitre_attack": None}}

    MultiAgentWorkflowController._validate_attack_enrichment_response(wrapped)

    assert wrapped == {"mitre_attack": []}
    with pytest.raises(ValueError, match="only mitre_attack"):
        MultiAgentWorkflowController._validate_attack_enrichment_response(
            {"cwe": [], "mitre_attack": []}
        )


def test_t1190_is_rejected_for_private_or_missing_preflight_context():
    with pytest.raises(ValueError, match="T1190 is not eligible"):
        MultiAgentWorkflowController._validate_attack_eligibility(
            [{"id": "T1190"}],
            {"T1190"},
        )

    MultiAgentWorkflowController._validate_attack_eligibility(
        [{"id": "T1059.004"}],
        {"T1190"},
    )


def test_final_attack_enrichment_retries_once_on_resume(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Validation", status="done")],
        assessment_complete=True,
    )
    state = FakeState(
        plan,
        finding_records=[
            {
                "finding_uid": "finding-1",
                "resolution": "verified",
                "candidate_data": {"artifacts": ["artifact:artifacts/proof.txt"]},
            }
        ],
    )
    calls = []

    class Catalog:
        def candidates(self, _finding, _kind, limit=12):
            return []

    def text_runner(*_args):
        calls.append("call")
        if len(calls) == 1:
            raise ValueError("temporary model failure")
        return '{"mitre_attack":[]}'

    monkeypatch.setattr(workflow_mod, "get_taxonomy_catalog", lambda: Catalog())
    monkeypatch.setattr(
        workflow_mod,
        "validate_taxonomy_mappings",
        lambda _cwe, attack, _evidence: {"cwe": [], "mitre_attack": attack, "provenance": {}},
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    controller._enrich_final_attack_mappings(plan)
    assert state.finding_records[0]["candidate_data"]["final_attack_enrichment"]["status"] == "failed"
    controller._enrich_final_attack_mappings(plan)
    controller._enrich_final_attack_mappings(plan)

    assert calls == ["call", "call"]
    enrichment = state.finding_records[0]["candidate_data"]["final_attack_enrichment"]
    assert enrichment["status"] == "completed"


def test_taxonomy_annotator_retries_invalid_response_with_previous_result(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Validation", status="done")],
        assessment_complete=True,
    )
    state = FakeState(
        plan,
        finding_records=[
            {
                "finding_uid": "finding-1",
                "candidate_data": {
                    "artifacts": ["artifact:artifacts/proof.txt"],
                    "taxonomy_annotation": {"status": "failed", "schema_version": 1},
                },
                "verification_task_uid": "verify-finding-1",
                "validation_data": {"outcome": "confirmed"},
                "resolution": "verified",
            }
        ],
    )

    class Catalog:
        def candidates(self, _finding, kind, limit=12):
            return [{"id": "CWE-79" if kind == "cwe" else "T1190", "name": "Candidate"}]

    responses = iter([
        '{"findings":[{"classification":{"cwe":"CWE-79","mitre_attack":null}}]}',
        '{"cwe":[{"id":"CWE-79","confidence":0.95,"rationale":"Proof","evidence":["artifact:artifacts/proof.txt"]}],"mitre_attack":[]}',
    ])
    monkeypatch.setattr(workflow_mod, "get_taxonomy_catalog", lambda: Catalog())
    def validate(cwe, attack, artifacts):
        if not cwe[0].get("confidence"):
            raise ValueError("cwe confidence must be a number from 0.0 to 1.0")
        return {"cwe": cwe, "mitre_attack": attack, "provenance": {"version": "test"}}

    monkeypatch.setattr(workflow_mod, "validate_taxonomy_mappings", validate)
    prompts = []

    def text_runner(_role, prompt, _tools, _system_prompt):
        prompts.append(prompt)
        return next(responses)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    controller._annotate_verified_findings(plan)

    annotation = state.finding_records[0]["candidate_data"]["taxonomy_annotation"]
    assert annotation["status"] == "completed"
    assert annotation["schema_version"] == 2
    assert responses
    assert "Previous response to correct:" in prompts[1]
    assert '"cwe":"CWE-79"' in prompts[1]


@pytest.mark.parametrize(
    "response",
    [
        {"finding_id": "finding-1", "classification": {"cwe": ["CWE-78"], "mitre_attack": ["T1059.004"]}},
        {
            "finding_id": "finding-1",
            "taxonomy_annotation": {
                "cwe": [{"id": "CWE-78", "name": "OS Command Injection"}],
                "mitre_attack": [{"id": "T1190", "name": "Exploit Public-Facing Application"}],
            },
        },
    ],
)
def test_taxonomy_annotation_normalizes_logged_top_level_wrappers(response):
    MultiAgentWorkflowController._validate_taxonomy_annotation_response(response)

    assert set(response) == {"cwe", "mitre_attack"}
    assert isinstance(response["cwe"], list)
    assert isinstance(response["mitre_attack"], list)


def test_taxonomy_annotation_logged_wrapper_still_requires_mapping_evidence(monkeypatch):
    response = {
        "finding_id": "finding-1",
        "classification": {"cwe": ["CWE-78"], "mitre_attack": []},
    }

    monkeypatch.setattr(
        workflow_mod,
        "validate_taxonomy_mappings",
        Mock(side_effect=ValueError("cwe confidence must be a number from 0.0 to 1.0")),
    )

    with pytest.raises(ValueError, match="cwe confidence"):
        MultiAgentWorkflowController._validate_taxonomy_annotation_proposal(
            response,
            ["artifact:artifacts/command-injection.txt"],
        )

    assert response == {"cwe": [{"id": "CWE-78"}], "mitre_attack": []}


def test_json_agent_stops_at_configured_limit_after_max_tokens():
    def text_runner(role, prompt, tools, system_prompt):
        raise MaxTokensReachedException("max_tokens")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=text_runner,
    )

    with pytest.raises(WorkflowInvariantError, match="reached its max_tokens limit after 2 attempt"):
        controller._run_json_text_agent("phase_evaluator", "original", [], "system")
    assert extract_result_text(SimpleNamespace(content=[{"text": "c"}])) == "c"


def test_role_tools_exclude_plan_task_mutation_and_gate_create_tasks():
    runtime = _runtime()

    default_names = {tool.__name__ for tool in build_role_tools(runtime)}
    assert {"shell", "memory_retrieve"}.issubset(default_names)
    assert "stop" not in default_names
    assert "prompt_optimizer" not in default_names
    assert "create_tasks" not in default_names
    assert "mcp_scan" not in default_names
    assert "module_probe" not in default_names

    selected_names = {
        tool.__name__
        for tool in build_role_tools(
            runtime,
            selected_optional_tool_names=["module_probe"],
            include_create_tasks=True,
        )
    }
    assert "create_tasks" in selected_names
    assert "module_probe" in selected_names
    assert "mcp_scan" not in selected_names


def test_controller_runs_existing_active_task_before_pending_task():
    calls = []
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[
            Task(task_uid="active", title="Active", objective="run active", phase=1, status="active", created_at="1"),
            Task(task_uid="pending", title="Pending", objective="run pending", phase=1, status="pending", created_at="0"),
        ],
    )

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"completed"}'
        if role == "phase_evaluator":
            return '{"status":"done","reason":"phase complete"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        return "worked"

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        max_iterations=5,
    )

    controller.run()

    assert calls[:3] == ["task_prompt_builder", "task_executor", "task_evaluator"]
    assert next(task for task in state.tasks if task.task_uid == "active").status == "done"
    task_events = [
        event for event in runtime.callback_handler.events
        if event["type"] in {"task_started", "task_done"}
    ]
    assert task_events[:2] == [
        {"type": "task_started", "task_uid": "active", "title": "Active", "status": "active"},
        {
            "type": "task_done",
            "task_uid": "active",
            "title": "Active",
            "status": "done",
            "status_reason": "completed",
        },
    ]
    assert runtime.callback_handler.termination_events == [("complete", "Assessment complete: 1 phase evaluated")]


def test_controller_resumes_prior_contracted_mapping_before_later_task_creation(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="partial_failure"),
            PlanPhase(id=2, title="Attack Hypothesis Generation", status="active"),
        ],
    )
    contract_context = {
        "module": "web",
        "phase_id": 1,
    }
    mapping = Task(
        task_uid="mapping",
        title="Map routes",
        objective="Map routes",
        phase=1,
        status="pending",
        created_at="1",
        recovery_context={
            "phase_task_contract": {
                **contract_context,
                "workstream": "bounded_crawl",
                "task_role": "mapping",
            }
        },
    )
    synthesis = Task(
        task_uid="synthesis",
        title="Synthesize inventory",
        objective="Build inventory",
        phase=1,
        status="pending",
        created_at="2",
        recovery_context={
            "phase_task_contract": {
                **contract_context,
                "workstream": "inventory_synthesis",
                "task_role": "synthesis",
            }
        },
    )
    runtime = _runtime()
    runtime.config.module = "web"
    state = FakeState(plan, tasks=[mapping, synthesis])
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        max_iterations=1,
    )
    resumed = []
    monkeypatch.setattr(
        controller,
        "_run_task",
        lambda current_plan, phase, task: resumed.append(
            (current_plan.current_phase, phase.id, task.task_uid, task.status)
        ),
    )
    monkeypatch.setattr(controller, "_create_tasks", lambda *_args: pytest.fail("task creator must not run"))

    with pytest.raises(WorkflowInvariantError, match="Workflow iteration limit reached"):
        controller.run()

    assert resumed == [(2, 1, "mapping", "active")]
    assert next(task for task in state.tasks if task.task_uid == "mapping").status == "active"
    assert next(task for task in state.tasks if task.task_uid == "synthesis").status == "pending"
    assert {
        "type": "contract_prerequisite_resume",
        "phase": 2,
        "owner_phase": 1,
        "task_uid": "mapping",
        "title": "Map routes",
        "task_role": "mapping",
        "workstream": "bounded_crawl",
        "reason": "contracted_producer_output_unavailable",
    } in runtime.callback_handler.events


def test_contract_prerequisite_resume_ignores_terminal_mapping_failures_and_ready_producers():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Challenge Surface", status="partial_failure"),
            PlanPhase(id=2, title="Exploit", status="active"),
        ],
    )
    runtime = _runtime()
    runtime.config.module = "ctf"
    failed = Task(
        task_uid="failed",
        title="Map challenge",
        objective="Map challenge",
        phase=1,
        status="partial_failure",
        recovery_context={
            "phase_task_contract": {
                "module": "ctf",
                "phase_id": 1,
                "workstream": "challenge_hints",
                "task_role": "mapping",
            }
        },
    )
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[failed]),
    )

    assert controller._contract_prerequisite_resume_candidate(plan, plan.phases[1]) is None

    ready_synthesis = Task(
        task_uid="ready",
        title="Synthesize challenge surface",
        objective="Build challenge surface",
        phase=1,
        status="done",
        recovery_context={
            "phase_task_contract": {
                "module": "ctf",
                "phase_id": 1,
                "workstream": "challenge_surface_synthesis",
                "task_role": "synthesis",
            }
        },
    )
    pending_mapping = Task(
        task_uid="pending",
        title="Map endpoint",
        objective="Map endpoint",
        phase=1,
        status="pending",
        recovery_context={
            "phase_task_contract": {
                "module": "ctf",
                "phase_id": 1,
                "workstream": "endpoint_capabilities",
                "task_role": "mapping",
            }
        },
    )
    ready_state = FakeState(plan, tasks=[ready_synthesis, pending_mapping])
    ready_controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=ready_state,
    )

    assert ready_controller._contract_prerequisite_resume_candidate(plan, plan.phases[1]) is None


def test_contract_prerequisite_resume_replaces_terminal_synthesis_in_owner_phase():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="partial_failure"),
            PlanPhase(id=2, title="Attack Hypothesis Generation", status="active"),
        ],
    )
    failed_synthesis = Task(
        task_uid="failed-synthesis",
        title="Synthesize inventory",
        objective="Build inventory",
        phase=1,
        status="partial_failure",
        created_at="1",
        recovery_context={
            "phase_task_contract": {
                "module": "web",
                "phase_id": 1,
                "workstream": "inventory_synthesis",
                "task_role": "synthesis",
            }
        },
    )
    runtime = _runtime()
    runtime.config.module = "web"
    state = FakeState(plan, tasks=[failed_synthesis], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    candidate = controller._contract_prerequisite_resume_candidate(plan, plan.phases[1])

    assert candidate is not None
    owner_phase, replacement = candidate
    assert owner_phase.id == 1
    assert replacement.phase == 1
    assert replacement.status == "pending"
    assert replacement.replacement_of == "failed-synthesis"
    assert replacement.supersedes_criteria == ["criterion:failed-synthesis"]
    assert replacement.recovery_context["phase_task_contract"] == failed_synthesis.recovery_context[
        "phase_task_contract"
    ]
    assert controller._contract_prerequisite_blocker(plan, plan.phases[1]) == ""


def test_completed_contract_synthesis_replacement_reconciles_owner_phase():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="partial_failure"),
            PlanPhase(id=2, title="Attack Hypothesis Generation", status="active"),
        ],
    )
    contract_context = {
        "module": "web",
        "phase_id": 1,
        "workstream": "inventory_synthesis",
    }
    mapping = Task(
        task_uid="mapping",
        title="Crawl routes",
        objective="Crawl routes",
        phase=1,
        status="done",
        recovery_context={
            "phase_task_contract": {
                **contract_context,
                "workstream": "bounded_crawl",
                "task_role": "mapping",
            }
        },
    )
    failed_synthesis = Task(
        task_uid="failed-synthesis",
        title="Synthesize inventory",
        objective="Build inventory",
        phase=1,
        status="partial_failure",
        acceptance=_acceptance("criterion-1"),
        recovery_context={
            "phase_task_contract": {
                **contract_context,
                "task_role": "synthesis",
            }
        },
    )
    runtime = _runtime()
    runtime.config.module = "web"
    state = FakeState(plan, tasks=[mapping, failed_synthesis], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    candidate = controller._contract_prerequisite_resume_candidate(plan, plan.phases[1])

    assert candidate is not None
    _, replacement = candidate
    completed_replacement = state.mark_task(replacement, "done", "Recovered inventory manifest")
    controller._reconcile_completed_contract_prerequisite(plan, completed_replacement)

    assert next(task for task in state.tasks if task.task_uid == "failed-synthesis").status == "superseded"
    assert [phase.status for phase in state.plan.phases] == ["done", "active"]


def test_completed_original_contract_synthesis_reconciles_owner_phase():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="partial_failure"),
            PlanPhase(id=2, title="Attack Hypothesis Generation", status="active"),
        ],
    )
    contract_context = {
        "module": "web",
        "phase_id": 1,
        "workstream": "inventory_synthesis",
    }
    mapping = Task(
        task_uid="mapping",
        title="Crawl routes",
        objective="Crawl routes",
        phase=1,
        status="done",
        recovery_context={
            "phase_task_contract": {
                **contract_context,
                "workstream": "bounded_crawl",
                "task_role": "mapping",
            }
        },
    )
    synthesis = Task(
        task_uid="synthesis",
        title="Synthesize inventory",
        objective="Build inventory",
        phase=1,
        status="pending",
        recovery_context={
            "phase_task_contract": {
                **contract_context,
                "task_role": "synthesis",
            }
        },
    )
    runtime = _runtime()
    runtime.config.module = "web"
    state = FakeState(plan, tasks=[mapping, synthesis], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    completed_synthesis = state.mark_task(synthesis, "done", "Inventory manifest created")
    controller._reconcile_completed_contract_prerequisite(plan, completed_synthesis)

    assert [phase.status for phase in state.plan.phases] == ["done", "active"]


def test_completed_original_contract_synthesis_keeps_owner_phase_partial_when_work_remains():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="partial_failure"),
            PlanPhase(id=2, title="Attack Hypothesis Generation", status="active"),
        ],
    )
    pending_mapping = Task(
        task_uid="pending-mapping",
        title="Map authentication",
        objective="Map authentication",
        phase=1,
        status="pending",
    )
    synthesis = Task(
        task_uid="synthesis",
        title="Synthesize inventory",
        objective="Build inventory",
        phase=1,
        status="done",
        recovery_context={
            "phase_task_contract": {
                "module": "web",
                "phase_id": 1,
                "workstream": "inventory_synthesis",
                "task_role": "synthesis",
            }
        },
    )
    runtime = _runtime()
    runtime.config.module = "web"
    state = FakeState(plan, tasks=[pending_mapping, synthesis], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    controller._reconcile_completed_contract_prerequisite(plan, synthesis)

    assert [phase.status for phase in state.plan.phases] == ["partial_failure", "active"]


def test_contract_prerequisite_repairs_failed_synthesis_from_retained_recon_evidence(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="partial_failure"),
            PlanPhase(id=2, title="Attack Hypothesis Generation", status="active"),
        ],
    )
    contract_context = {
        "module": "web",
        "phase_id": 1,
    }
    mapping = Task(
        task_uid="mapping",
        title="Crawl routes",
        objective="Crawl routes",
        phase=1,
        status="done",
        evidence=["artifact:artifacts/katana_output.txt"],
        recovery_context={
            "phase_task_contract": {
                **contract_context,
                "workstream": "bounded_crawl",
                "task_role": "mapping",
            }
        },
    )
    failed_synthesis = Task(
        task_uid="failed-synthesis",
        title="Synthesize inventory",
        objective="Build inventory",
        phase=1,
        status="partial_failure",
        target_ids=["target-1"],
        recovery_context={
            "phase_task_contract": {
                **contract_context,
                "workstream": "inventory_synthesis",
                "task_role": "synthesis",
            }
        },
    )
    runtime = _runtime()
    runtime.config.module = "web"
    state = FakeState(plan, tasks=[mapping, failed_synthesis], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )
    converted = []

    def consolidate(source_artifacts, output_file, **kwargs):
        assert source_artifacts == ["artifact:artifacts/katana_output.txt"]
        converted.append(kwargs)
        assert output_file == "artifacts/contract_recovery/failed-synthesis-inventory_manifest.json"
        return {
            "artifact_ref": "artifact:artifacts/contract_recovery/inventory_manifest.json",
            "item_count": 1,
            "skipped_artifacts": [],
        }

    def load_manifest(_plan, reference):
        if reference == "artifact:artifacts/contract_recovery/inventory_manifest.json":
            return {"schema_version": 1, "items": [{}], "unassessed_gaps": []}, "manifest-hash"
        raise ValueError("not an inventory manifest")

    monkeypatch.setattr(workflow_mod, "consolidate_recon_artifacts", consolidate)
    monkeypatch.setattr(controller, "_load_controller_inventory_manifest", load_manifest)

    assert controller._repair_failed_contract_prerequisites(plan, plan.phases[1]) is True
    assert converted == [{"target_id": "target-1", "target": ""}]
    repaired = next(task for task in state.tasks if task.task_uid == "failed-synthesis")
    assert repaired.status == "done"
    assert state.acceptance_results[repaired.task_uid][0].evidence_refs == (
        "artifact:artifacts/contract_recovery/inventory_manifest.json",
    )
    assert [phase.status for phase in state.plan.phases] == ["done", "active"]


def test_web_inventory_synthesis_runs_in_controller_without_prompt_or_executor(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Attack Surface Mapping", status="active")],
        targets=[OperationTarget("target-1", "https://target.test", "network")],
    )
    contract_context = {"module": "web", "phase_id": 1}
    mapping = Task(
        task_uid="crawl",
        title="Crawl routes",
        objective="Crawl routes",
        phase=1,
        status="done",
        evidence=["artifact:artifacts/katana.json"],
        recovery_context={
            "phase_task_contract": {
                **contract_context,
                "workstream": "bounded_crawl",
                "task_role": "mapping",
            }
        },
    )
    synthesis = Task(
        task_uid="synthesis",
        title="Synthesize inventory",
        objective="Consolidate mapping workstreams",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=_acceptance("criterion-1"),
        recovery_context={
            "phase_task_contract": {
                **contract_context,
                "workstream": "inventory_synthesis",
                "task_role": "synthesis",
                "depends_on_workstreams": ["bounded_crawl"],
            }
        },
    )
    runtime = _runtime()
    runtime.config.module = "web"
    state = FakeState(plan, tasks=[mapping, synthesis], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )
    calls = []

    def consolidate(source_artifacts, output_file, **kwargs):
        calls.append((source_artifacts, output_file, kwargs))
        return {
            "artifact_ref": "artifact:artifacts/inventory_synthesis/synthesis-inventory_manifest.json",
            "item_count": 2,
            "skipped_artifacts": [{"source_artifact": "artifact:artifacts/notes.txt", "reason": "unsupported"}],
        }

    monkeypatch.setattr(workflow_mod, "consolidate_recon_artifacts", consolidate)
    monkeypatch.setattr(controller, "_load_controller_inventory_manifest", lambda *_args: ({"items": [{}, {}]}, "hash"))
    monkeypatch.setattr(controller, "_build_task_prompt", lambda *_args: pytest.fail("executor prompt must not be built"))

    controller._run_task_in_trace(plan, plan.phases[0], synthesis)

    assert calls == [
        (
            ["artifact:artifacts/katana.json"],
            "artifacts/inventory_synthesis/synthesis-inventory_manifest.json",
            {"target_id": "target-1", "target": "https://target.test"},
        )
    ]
    completed = next(task for task in state.tasks if task.task_uid == "synthesis")
    assert completed.status == "done"
    assert state.acceptance_results["synthesis"][0].evidence_refs == (
        "artifact:artifacts/inventory_synthesis/synthesis-inventory_manifest.json",
    )
    assert {
        "type": "controller_inventory_synthesis",
        "task_uid": "synthesis",
        "phase": 1,
        "outcome": "completed",
        "input_reference_count": 1,
        "skipped_artifact_count": 1,
        "manifest_ref": "artifact:artifacts/inventory_synthesis/synthesis-inventory_manifest.json",
        "item_count": 2,
    } in runtime.callback_handler.events


def test_contract_prerequisite_does_not_replace_failed_synthesis_when_conversion_is_unavailable(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="partial_failure"),
            PlanPhase(id=2, title="Attack Hypothesis Generation", status="active"),
        ],
    )
    failed_synthesis = Task(
        task_uid="failed-synthesis",
        title="Synthesize inventory",
        objective="Build inventory",
        phase=1,
        status="partial_failure",
        target_ids=["target-1"],
        evidence=["artifact:artifacts/unstructured.txt"],
        recovery_context={
            "phase_task_contract": {
                "module": "web",
                "phase_id": 1,
                "workstream": "inventory_synthesis",
                "task_role": "synthesis",
            }
        },
    )
    runtime = _runtime()
    runtime.config.module = "web"
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[failed_synthesis], acceptance_complete=False),
    )
    monkeypatch.setattr(
        workflow_mod,
        "consolidate_recon_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("unsupported source")),
    )

    assert controller._repair_failed_contract_prerequisites(plan, plan.phases[1]) is False
    assert controller._contract_prerequisite_resume_candidate(plan, plan.phases[1]) is not None


def test_contract_prerequisite_blocks_dependent_task_creation_for_ambiguous_failed_synthesis(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="partial_failure"),
            PlanPhase(id=2, title="Attack Hypothesis Generation", status="active"),
        ],
    )
    base_acceptance = _acceptance("criterion-one")
    acceptance = AcceptanceContract(
        mode=base_acceptance.mode,
        basis=base_acceptance.basis,
        criteria=[
            *base_acceptance.criteria,
            AcceptanceCriterion(
                id="criterion-two",
                description="Record remaining inventory evidence",
                evidence_requirements=[EvidenceRequirement(kind="inventory_manifest")],
            ),
        ],
    )
    failed_synthesis = Task(
        task_uid="failed-synthesis",
        title="Synthesize inventory",
        objective="Build inventory",
        phase=1,
        status="partial_failure",
        acceptance=acceptance,
        recovery_context={
            "phase_task_contract": {
                "module": "web",
                "phase_id": 1,
                "workstream": "inventory_synthesis",
                "task_role": "synthesis",
            }
        },
    )
    runtime = _runtime()
    runtime.config.module = "web"
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[failed_synthesis], acceptance_complete=False),
        max_iterations=1,
    )
    monkeypatch.setattr(controller, "_create_tasks", lambda *_args: pytest.fail("dependent task creation must not run"))

    with pytest.raises(WorkflowInvariantError, match="Task creation for phase 2 is blocked"):
        controller.run()


def test_contract_prerequisite_blocks_when_its_replacement_has_already_failed():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="partial_failure"),
            PlanPhase(id=2, title="Attack Hypothesis Generation", status="active"),
        ],
    )
    contract_context = {
        "phase_task_contract": {
            "module": "web",
            "phase_id": 1,
            "workstream": "inventory_synthesis",
            "task_role": "synthesis",
        }
    }
    failed_synthesis = Task(
        task_uid="failed-synthesis",
        title="Synthesize inventory",
        objective="Build inventory",
        phase=1,
        status="partial_failure",
        recovery_context=contract_context,
    )
    failed_replacement = Task(
        task_uid="failed-replacement",
        title="Synthesize inventory (prerequisite recovery)",
        objective="Repair inventory",
        phase=1,
        status="partial_failure",
        replacement_of="failed-synthesis",
        supersedes_criteria=["criterion:failed-synthesis"],
        recovery_context=contract_context,
    )
    runtime = _runtime()
    runtime.config.module = "web"
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(
            plan,
            tasks=[failed_synthesis, failed_replacement],
            acceptance_complete=False,
        ),
    )

    assert controller._contract_prerequisite_resume_candidate(plan, plan.phases[1]) is None
    assert "cannot be resumed or replaced" in controller._contract_prerequisite_blocker(plan, plan.phases[1])


def test_contract_prerequisite_resume_requires_valid_inventory_output(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="partial_failure"),
            PlanPhase(id=2, title="Attack Hypothesis Generation", status="active"),
        ],
    )
    runtime = _runtime()
    runtime.config.module = "web"
    mapping = Task(
        task_uid="mapping",
        title="Map API",
        objective="Map API",
        phase=1,
        status="active",
        recovery_context={
            "phase_task_contract": {
                "module": "web",
                "phase_id": 1,
                "workstream": "client_side_api",
                "task_role": "mapping",
            }
        },
    )
    synthesis = Task(
        task_uid="synthesis",
        title="Synthesize inventory",
        objective="Build inventory",
        phase=1,
        status="done",
        recovery_context={
            "phase_task_contract": {
                "module": "web",
                "phase_id": 1,
                "workstream": "inventory_synthesis",
                "task_role": "synthesis",
            }
        },
    )
    state = FakeState(plan, tasks=[mapping, synthesis])
    state.acceptance_results[synthesis.task_uid] = [AcceptanceResult(
        criterion_id="criterion:synthesis",
        status="satisfied",
        disposition="observation",
        summary="manifest recorded",
        evidence_refs=("artifact:artifacts/inventory.json",),
    )]
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )
    monkeypatch.setattr(workflow_mod, "canonical_artifact_reference", lambda reference: reference)
    monkeypatch.setattr(
        controller,
        "_load_controller_inventory_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid manifest")),
    )

    candidate = controller._contract_prerequisite_resume_candidate(plan, plan.phases[1])

    assert candidate == (plan.phases[0], mapping)
    monkeypatch.setattr(controller, "_load_controller_inventory_manifest", lambda *_args, **_kwargs: ({}, "hash"))
    assert controller._contract_prerequisite_resume_candidate(plan, plan.phases[1]) is None


def test_task_executor_keeps_task_capture_and_uses_tool_completion_policy(monkeypatch):
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")],
    )
    captured = {}
    bound_task_uids = []
    original_factory = workflow_mod.build_record_task_acceptance_tool

    def build_bound_tool(task_uid, task, execution_evidence_resolver=None):
        bound_task_uids.append(task_uid)
        return original_factory(task_uid, task, execution_evidence_resolver)

    monkeypatch.setattr(workflow_mod, "build_record_task_acceptance_tool", build_bound_tool)

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":["module_probe"]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"completed"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        captured["role"] = role
        captured["prompt"] = prompt
        captured["tools"] = {tool.__name__ for tool in tools}
        captured["system_prompt"] = system_prompt
        captured["policy"] = run_policy

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert captured["role"] == "task_executor"
    assert bound_task_uids == ["active"]
    assert captured["prompt"].startswith("execute active\n\n## Frozen Task Acceptance Contract (Controller-owned)")
    assert state.tasks[0].acceptance.manifest_hash in captured["prompt"]
    assert "## Inventory Manifest Evidence Contract (Controller-owned)" in captured["prompt"]
    assert '"schema_version": 1' in captured["prompt"]
    assert "Execute only the assigned task objective" in captured["prompt"]
    assert "do not create or execute follow-up tasks" in captured["prompt"]
    assert "Raw shell commands, tool IDs, URLs, and pasted tool output are invalid" in captured["prompt"]
    assert "call `record_task_acceptance` with one terminal status" in captured["prompt"]
    assert "Python owns task, phase, and operation state transitions" in captured["prompt"]
    assert "## Tool Selection Policy (Controller-owned)" in captured["prompt"]
    assert "Multiple methods with\noverlapping capabilities may be used" in captured["prompt"]
    assert "neither mandates use nor makes another selected method exclusive" in captured["prompt"]
    assert "module_probe" in captured["tools"]
    assert "create_tasks" not in captured["tools"]
    assert "store_finding" in captured["tools"]
    assert "record_finding_validation" not in captured["tools"]
    assert "record_task_acceptance" in captured["tools"]
    assert captured["system_prompt"] == "base prompt"
    assert captured["policy"].min_tool_calls == 1
    assert captured["policy"].terminal_after_required_tools is True
    assert captured["policy"].allow_text_final_after_tools is False
    assert captured["policy"].ignored_terminal_tool_names == frozenset()
    assert captured["policy"].terminal_reason == "task_executor_done"


def test_finding_candidate_storage_deterministically_records_matching_acceptance():
    runtime = _runtime()
    acceptance = AcceptanceContract(
        mode="outcome",
        basis=AcceptanceBasis(kind="snapshot", description="Finding candidate", source_refs=["task:source-task"]),
        criteria=[
            AcceptanceCriterion(
                id="finding-candidate",
                description="Persist one finding candidate",
                evidence_requirements=[EvidenceRequirement(kind="finding_candidate")],
            )
        ],
    )
    task = Task(
        task_uid="source-task",
        title="Assess reflected XSS",
        objective="Assess reflected XSS",
        acceptance=acceptance,
        phase=1,
        status="active",
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    captured = {}

    def text_runner(role, *_args):
        if role == "task_prompt_builder":
            return '{"prompt":"store candidate","tools":[]}'
        return '{"status":"done","reason":"candidate persisted"}'

    def work_runner(_role, _prompt, tools, _system_prompt, policy):
        captured["tools"] = {tool.__name__ for tool in tools}
        captured["policy"] = policy
        return workflow_mod.TaskExecutorCycleResult(
            text="candidate stored",
            outcomes=[ToolOutcome(
                sequence=1,
                tool_use_id="finding-store",
                tool_name="store_finding",
                success=True,
                correctable=False,
                input_summary="candidate",
                output_summary='{"finding_ref":"finding:finding-1"}',
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )
    controller._run_task(_plan(), _plan().phases[0], task)

    assert captured["policy"].required_tool_names == {"store_finding"}
    assert "store_finding" in captured["tools"]
    assert "record_task_acceptance" not in captured["tools"]
    assert state.acceptance_results[task.task_uid][0].evidence_refs == ("finding:finding-1",)
    assert state.tasks[0].status == "done", state.tasks[0].status_reason


def test_task_executor_contract_disables_follow_up_task_creation():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = TaskModel(
        task_uid="endpoint",
        title="Assess endpoint http://target.test/login",
        objective="Assess login",
        acceptance=_acceptance(),
        phase=1,
        status="active",
    )

    contract = controller._task_executor_contract(task)

    assert "do not create or execute follow-up tasks" in contract
    assert "create_tasks" not in contract


@pytest.mark.parametrize(
    ("tool_names", "expects_observation", "expects_finding"),
    [
        ({"store_observation", "store_finding"}, True, True),
        ({"store_observation"}, True, False),
        ({"store_finding"}, False, True),
        (set(), False, False),
    ],
)
def test_task_executor_contract_only_names_available_persistence_tools(
    tool_names,
    expects_observation,
    expects_finding,
):
    task = TaskModel(
        task_uid="endpoint",
        title="Assess endpoint http://target.test/login",
        objective="Assess login",
        acceptance=_acceptance(),
        phase=1,
        status="active",
    )

    contract = MultiAgentWorkflowController._task_executor_contract(task, tool_names)

    assert ("store_observation" in contract) is expects_observation
    assert ("store_finding" in contract) is expects_finding


def _execution_requirement_task(methods, requirements):
    return TaskModel(
        task_uid="execution-guidance",
        title="Produce execution evidence",
        objective="Produce bounded execution evidence",
        phase=1,
        status="active",
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Bounded execution",
                source_refs=["plan:phase-1"],
                procedure={
                    "methods": methods,
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[
                AcceptanceCriterion(
                    id="criterion-1",
                    description="Produce evidence",
                    evidence_requirements=[EvidenceRequirement(kind="artifact")],
                    execution_requirements=requirements,
                )
            ],
        ),
    )


def test_task_executor_contract_lists_only_matching_execution_requirement_providers(monkeypatch):
    monkeypatch.setattr(
        workflow_mod,
        "get_shell_command_execution_capabilities",
        lambda command: {"katana": frozenset({"crawl"}), "gospider": frozenset({"crawl"})}.get(
            command, frozenset()
        ),
    )
    task = _execution_requirement_task(
        ["crawl"],
        [ExecutionRequirement("crawl-proof", "Crawl the route", "/")],
    )

    contract = MultiAgentWorkflowController._task_executor_contract(
        task,
        {"shell", "specialized_recon_orchestrator", "client_bundle_inventory"},
        [{"command": "katana"}, {"command": "gospider"}],
    )

    section = contract.split("## Execution Requirements and Available Providers (Controller-owned)", 1)[1]
    assert "Frozen subject: `/`" in section
    assert "native `specialized_recon_orchestrator`" in section
    assert "shell command `katana`" in section
    assert "shell command `gospider`" in section
    assert "client_bundle_inventory" not in section
    assert "crawl-proof" not in section
    assert "choose any one suitable supplied provider" in section
    assert "do not execute every listed provider" in section


def test_task_executor_contract_groups_providers_by_declared_capability_and_handles_restriction(monkeypatch):
    monkeypatch.setattr(
        workflow_mod,
        "get_shell_command_execution_capabilities",
        lambda command: {"katana": frozenset({"crawl"}), "curl": frozenset({"request"})}.get(
            command, frozenset()
        ),
    )
    task = _execution_requirement_task(
        ["crawl", "request"],
        [
            ExecutionRequirement("first", "Crawl root", "/"),
            ExecutionRequirement("second", "Crawl API", "/api"),
        ],
    )

    normal = MultiAgentWorkflowController._task_executor_contract(
        task,
        {"shell", "specialized_recon_orchestrator", "http_request"},
        [{"command": "katana"}, {"command": "curl"}],
    )
    restricted = MultiAgentWorkflowController._task_executor_contract(task, {"record_task_acceptance"}, [])

    normal_section = normal.split("## Execution Requirements and Available Providers (Controller-owned)", 1)[1]
    assert normal_section.count("Frozen subject:") == 2
    assert (
        "Declared procedure capability `crawl`: native `specialized_recon_orchestrator`, shell command `katana`"
        in normal_section
    )
    assert (
        "Declared procedure capability `request`: native `http_request`, native "
        "`specialized_recon_orchestrator`, shell command `curl`"
        in normal_section
    )
    crawl_provider_section = normal_section.split("Declared procedure capability `crawl`", 1)[1].split(
        "Declared procedure capability `request`", 1
    )[0]
    assert "shell command `curl`" not in crawl_provider_section
    assert restricted.count("no supplied provider available") == 4


def test_task_executor_contract_normalizes_legacy_http_enumeration_to_crawl(monkeypatch):
    monkeypatch.setattr(
        workflow_mod,
        "get_shell_command_execution_capabilities",
        lambda command: {"katana": frozenset({"crawl"}), "nmap": frozenset({"enumerate"})}.get(
            command, frozenset()
        ),
    )
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget("target-1", "https://example.test:8443", "network")],
    )
    task = _execution_requirement_task(
        ["request", "enumerate"],
        [ExecutionRequirement("execution", "Map the root", "/")],
    )
    task = replace(task, target_ids=["target-1"])

    contract = MultiAgentWorkflowController._task_executor_contract(
        task,
        {"shell"},
        [{"command": "katana"}, {"command": "nmap"}],
        plan=plan,
    )

    assert "Declared procedure capability `crawl`: shell command `katana`" in contract
    assert "Declared procedure capability `enumerate`" not in contract


def test_task_executor_contract_omits_execution_provider_section_without_requirements():
    contract = MultiAgentWorkflowController._task_executor_contract(
        TaskModel(
            task_uid="no-execution-requirements",
            title="Observe",
            objective="Observe",
            acceptance=_acceptance(),
            phase=1,
            status="active",
        ),
        {"shell"},
        [{"command": "katana"}],
    )

    assert "Execution Requirements and Available Providers" not in contract


def test_endpoint_evidence_guard_rejects_inventory_manifest(monkeypatch):
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = TaskModel(
        task_uid="endpoint",
        title="Assess endpoint http://target.test/login [target-1]",
        objective="Assess login",
        acceptance=AcceptanceContract(
            mode="coverage",
            basis=AcceptanceBasis(
                kind="snapshot",
                description="route",
                source_refs=["artifact:artifacts/discovery.json"],
                item_ids=["endpoint-1"],
            ),
            criteria=[AcceptanceCriterion(
                id="criterion",
                description="Assess route",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            )],
        ),
        phase=1,
        status="active",
    )
    result = AcceptanceResult(
        criterion_id="criterion",
        status="satisfied",
        disposition="observation",
        summary="assessed",
        evidence_refs=("artifact:artifacts/inventory_manifest.json",),
    )
    monkeypatch.setattr(
        workflow_mod,
        "_load_inventory_manifest",
        lambda _reference: ({"items": [{"id": "endpoint-1", "kind": "endpoint"}]}, "digest"),
    )

    reason = controller._endpoint_evidence_guard(task, [result], [])

    assert "inventory/manifest evidence" in reason


def test_endpoint_evidence_inventory_rejection_is_recoverable():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    reason = "Endpoint task used inventory/manifest evidence instead of route-specific evidence: artifact:inventory_manifest.json"

    assert controller._endpoint_evidence_failure_recoverable(reason) is True
    instruction = controller._endpoint_evidence_recovery_instruction(
        TaskModel(
            task_uid="endpoint",
            title="Assess endpoint http://target.test/login",
            objective="Assess",
            acceptance=AcceptanceContract(
                mode="coverage",
                basis=AcceptanceBasis(
                    kind="snapshot",
                    description="route",
                    source_refs=["artifact:artifacts/discovery.json"],
                    item_ids=["endpoint-1"],
                ),
                criteria=[AcceptanceCriterion(
                    id="criterion",
                    description="Assess route",
                    evidence_requirements=[EvidenceRequirement(kind="artifact")],
                )],
            ),
            phase=1,
            status="active",
        ),
        reason,
        ["artifact:artifacts/login_response.txt"],
        1,
        1,
    )
    assert "do not repeat the rejected record_task_acceptance call" in instruction
    assert "artifact:artifacts/login_response.txt" in instruction
    assert "endpoint-1" in instruction


def test_phase_inventory_merge_deduplicates_and_preserves_source_provenance(monkeypatch, tmp_path):
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    manifests = {
        "artifact:first.json": {
            "items": [{
                "id": "endpoint-1",
                "target_id": "target-1",
                "kind": "endpoint",
                "value": "http://target.test/login",
                "attributes": {"interaction": {
                    "operations": ["GET"],
                    "inputs": [{"name": "username", "location": "query"}],
                }},
            }],
            "unassessed_gaps": ["first gap"],
        },
        "artifact:second.json": {
            "items": [
                {
                    "id": "endpoint-1",
                    "target_id": "target-1",
                    "kind": "endpoint",
                    "value": "http://target.test/login",
                    "attributes": {"interaction": {
                        "operations": ["POST"],
                        "inputs": [{"name": "password", "location": "body"}],
                    }},
                },
                {
                    "id": "service-1",
                    "target_id": "target-1",
                    "kind": "service",
                    "value": "ssh",
                    "attributes": {},
                },
                {
                    "id": "parameter-1",
                    "target_id": "target-1",
                    "kind": "parameter",
                    "value": "endpoint-1",
                    "attributes": {"endpoint_id": "endpoint-1"},
                },
            ],
            "unassessed_gaps": ["second gap"],
        },
    }
    monkeypatch.setattr(workflow_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr("modules.tools.memory._operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        workflow_mod,
        "canonical_artifact_reference",
        lambda path: f"artifact:{Path(path).relative_to(tmp_path)}",
    )
    monkeypatch.setattr(
        controller,
        "_load_controller_inventory_manifest",
        lambda _plan, reference: (manifests[reference], reference),
    )

    reference = controller._merge_phase_inventory_manifests(
        _plan(),
        1,
        ["artifact:first.json", "artifact:second.json"],
    )

    merged_path = tmp_path / reference.removeprefix("artifact:")
    payload = json.loads(merged_path.read_text())
    assert reference.startswith("artifact:artifacts/phase-1-inventory-merged-")
    assert len(payload["items"]) == 3
    endpoint = next(item for item in payload["items"] if item["kind"] == "endpoint")
    parameter = next(item for item in payload["items"] if item["kind"] == "parameter")
    assert endpoint["attributes"]["source_manifest_refs"] == ["artifact:first.json", "artifact:second.json"]
    assert endpoint["attributes"]["interaction"]["operations"] == ["GET", "POST"]
    assert endpoint["attributes"]["interaction"]["inputs"] == [
        {"location": "query", "name": "username"},
        {"location": "body", "name": "password"},
    ]
    assert parameter["value"] == "endpoint-1"
    assert parameter["attributes"]["endpoint_id"] == endpoint["id"]
    assert payload["unassessed_gaps"] == ["first gap", "second gap"]
    assert controller._merge_phase_inventory_manifests(
        _plan(), 1, ["artifact:second.json", "artifact:first.json"]
    ) == reference


def test_endpoint_evidence_recovery_allows_changed_evidence_before_evaluation(monkeypatch):
    runtime = _runtime(
        env_ints={
            "CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 2,
            "CYBER_TASK_ACCEPTANCE_MAX_CORRECTIONS": 0,
            "CYBER_ENDPOINT_EVIDENCE_MAX_CORRECTIONS": 1,
        }
    )
    task = TaskModel(
        task_uid="endpoint",
        title="Assess endpoint http://target.test/login [target-1]",
        objective="Assess login",
        acceptance=AcceptanceContract(
            mode="coverage",
            basis=AcceptanceBasis(
                kind="snapshot",
                description="route",
                source_refs=["artifact:artifacts/discovery.json"],
                item_ids=["endpoint-1"],
            ),
            criteria=[AcceptanceCriterion(
                id="criterion",
                description="Assess route",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            )],
        ),
        phase=1,
        status="active",
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    state.acceptance_results[task.task_uid] = [AcceptanceResult(
        criterion_id="criterion",
        status="satisfied",
        disposition="observation",
        summary="assessed",
        evidence_refs=("artifact:artifacts/inventory_manifest.json",),
    )]
    def load_manifest(reference):
        if reference in {
            "artifact:artifacts/discovery.json",
            "artifact:artifacts/inventory_manifest.json",
        }:
            return {"items": [{"id": "endpoint-1", "kind": "endpoint"}]}, "digest"
        raise ValueError("not an inventory manifest")

    monkeypatch.setattr(workflow_mod, "_load_inventory_manifest", load_manifest)
    actor_prompts = []
    evaluator_calls = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute endpoint","tools":[]}'
        if role == "task_evaluator":
            evaluator_calls.append(prompt)
            return '{"status":"done","reason":"route evidence approved"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        actor_prompts.append(prompt)
        if len(actor_prompts) == 2:
            state.acceptance_results[task.task_uid] = [AcceptanceResult(
                criterion_id="criterion",
                status="satisfied",
                disposition="observation",
                summary="route response saved",
                evidence_refs=("artifact:artifacts/login_response.txt",),
            )]
        return "route evidence"

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(actor_prompts) == 2
    assert "Missing criterion:" in actor_prompts[1]
    assert "Durable evidence ledger:" in actor_prompts[1]
    assert "Required tool call: record_task_acceptance" in actor_prompts[1]
    assert evaluator_calls
    assert state.tasks[0].status == "done"


def test_inventory_manifest_prompt_is_omitted_for_generic_artifact_task():
    task = TaskModel(
        task_uid="workflow-map",
        title="Workflow map",
        objective="Store a bounded workflow map",
        acceptance=_artifact_acceptance(),
        phase=1,
        status="active",
    )

    assert MultiAgentWorkflowController._inventory_manifest_evidence_prompt(task) == ""


def test_task_roles_share_task_trace_attributes():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1})
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    captured = {}

    def capture(role):
        captured.setdefault(role, []).append(dict(runtime.trace_attributes))

    def text_runner(role, prompt, tools, system_prompt):
        capture(role)
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        if role == "task_prompt_critic":
            return '{"approved":true,"feedback":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"completed"}'
        raise AssertionError(role)

    @contextmanager
    def executor_session(role, tools, system_prompt):
        capture(role)

        def run(prompt, run_policy):
            capture(f"{role}:run")
            return "actor result"

        yield run

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        executor_session_factory=executor_session,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    shared_roles = [
        "task_prompt_builder",
        "task_prompt_critic",
        "task_executor",
        "task_executor:run",
        "task_evaluator",
    ]
    trace_names = {
        captured[role][0]["langfuse.trace.name"]
        for role in shared_roles
    }
    assert len(trace_names) == 1
    for role in shared_roles:
        attrs = captured[role][0]
        assert attrs["workflow.trace.scope"] == "task"
        assert attrs["workflow.task.uid"] == "active"
        assert attrs["workflow.phase.id"] == 1
        assert "workflow-task" in attrs["langfuse.trace.tags"]
    assert runtime.trace_attributes == {"operation.id": "OP_TEST"}


def test_different_tasks_get_distinct_task_trace_attributes():
    runtime = _runtime()
    task_one = Task(task_uid="task-one", title="First", objective="run first", phase=1, status="active")
    task_two = Task(task_uid="task-two", title="Second", objective="run second", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task_one, task_two])
    trace_names = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            trace_names.append(runtime.trace_attributes["langfuse.trace.name"])
            return '{"prompt":"execute active","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"completed"}'
        raise AssertionError(role)

    @contextmanager
    def executor_session(role, tools, system_prompt):
        def run(prompt, run_policy):
            return "actor result"

        yield run

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        executor_session_factory=executor_session,
    )

    controller._run_task(_plan(), _plan().phases[0], task_one)
    controller._run_task(_plan(), _plan().phases[0], task_two)

    assert len(trace_names) == 2
    assert trace_names[0] != trace_names[1]
    assert "task-one" in trace_names[0]
    assert "task-two" in trace_names[1]


def test_task_execution_retries_actionable_semantic_evaluator_feedback():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1})
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    evaluator_prompts = []
    actor_prompts = []
    lifecycle = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        if role == "task_evaluator":
            evaluator_prompts.append(prompt)
            if len(evaluator_prompts) == 1:
                return json.dumps(
                    {
                        "status": "partial_failure",
                        "reason": "remaining endpoint lacks evidence",
                        "instructions": "Run endpoint validation and store the artifact path.",
                    }
                )
            return '{"status":"done","reason":"corrected evidence is sufficient"}'
        raise AssertionError(role)

    @contextmanager
    def executor_session(role, tools, system_prompt):
        lifecycle.append(("created", role, system_prompt))

        def run(prompt, run_policy):
            actor_prompts.append(prompt)
            return f"actor result {len(actor_prompts)}"

        try:
            yield run
        finally:
            lifecycle.append(("cleaned", role))

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        executor_session_factory=executor_session,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(actor_prompts) == 2
    assert actor_prompts[0].startswith("execute active")
    assert "Start a fresh actor cycle 2" in actor_prompts[1]
    assert "Run endpoint validation and store the artifact path." in actor_prompts[1]
    assert "Required tool call:" not in actor_prompts[1]
    assert "Cycle 1: actor result 1" in evaluator_prompts[0]
    assert "Cycle 2: actor result 2" in evaluator_prompts[1]
    assert lifecycle == [
        ("created", "task_executor", "base prompt"),
        ("cleaned", "task_executor"),
        ("created", "task_executor", "base prompt"),
        ("cleaned", "task_executor"),
    ]
    evaluator_activities = [
        event
        for event in runtime.callback_handler.events
        if event.get("type") == "workflow_activity" and event.get("role") == "task_evaluator"
    ]
    assert [(event["status"], event["cycle"], event["cycle_total"]) for event in evaluator_activities] == [
        ("started", 1, 6),
        ("completed", 1, 6),
        ("started", 2, 6),
        ("completed", 2, 6),
    ]
    assert state.tasks[0].status == "done"
    assert state.tasks[0].status_reason == "corrected evidence is sufficient"


def test_task_execution_does_not_retry_evaluator_feedback_when_corrections_are_disabled():
    runtime = _runtime(
        env_ints={
            "CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1,
            "CYBER_TASK_EVALUATOR_MAX_CORRECTIONS": 0,
        }
    )
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    actor_prompts = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        if role == "task_evaluator":
            return json.dumps(
                {
                    "status": "partial_failure",
                    "reason": "remaining endpoint lacks evidence",
                    "instructions": "Run endpoint validation and store the artifact path.",
                }
            )
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt, run_policy: actor_prompts.append(prompt),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert controller._task_evaluator_correction_count() == 0
    assert len(actor_prompts) == 1
    assert state.tasks[0].status == "partial_failure"
    assert state.tasks[0].status_reason == "remaining endpoint lacks evidence"


def test_finding_validation_task_requires_record_tool_and_finalizes(monkeypatch):
    runtime = _runtime()
    task = Task(
        task_uid="verify-1",
        title="Verify finding",
        objective="verify",
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )
    state = FakeState(_plan(), tasks=[task])
    policies = []
    tool_names = set()
    finalize = Mock(return_value="verified")
    monkeypatch.setattr(workflow_mod, "finalize_finding_validation", finalize)
    monkeypatch.setattr(workflow_mod, "finding_validation_submitted", Mock(return_value=True))

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"verify","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"evidence approved"}'
        raise AssertionError(role)

    @contextmanager
    def executor_session(role, tools, system_prompt):
        tool_names.update(tool.__name__ for tool in tools)

        def run(prompt, policy):
            policies.append(policy)
            return "validation submitted"

        yield run

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        executor_session_factory=executor_session,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert policies[0].required_tool_names == {"record_finding_validation"}
    assert "record_finding_validation" in tool_names
    assert "store_finding" not in tool_names
    assert "record_task_acceptance" not in tool_names
    finalize.assert_called_once_with(task, "done", "evidence approved")
    assert state.tasks[0].status == "done"
    assert state.tasks[0].status_reason == "evidence approved"
    assert runtime.callback_handler.events[-1] == {
        "type": "task_done",
        "task_uid": "verify-1",
        "title": "Verify finding",
        "status": "done",
        "status_reason": "evidence approved",
        "task_kind": "finding_validation",
        "reference_id": "finding-1",
        "finding_resolution": "verified",
    }


def test_finding_validation_recovery_rebinds_the_canonical_verification_task():
    task = Task(
        task_uid="verify-1",
        title="Verify finding",
        objective="verify",
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )
    state = FakeState(
        _plan(),
        tasks=[task],
        acceptance_complete=False,
        finding_records=[{
            "finding_uid": "finding-1",
            "verification_task_uid": "verify-1",
            "candidate_data": {},
            "resolution": "",
            "validation_data": None,
        }],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    replacement = controller._create_reasoning_loop_replacement_task(task, [])

    assert replacement is not None
    assert replacement.replacement_of == task.task_uid
    assert state.finding_records[0]["verification_task_uid"] == replacement.task_uid
    assert controller._create_reasoning_loop_replacement_task(task, []) is None


def test_finding_validation_contract_requires_claim_specific_evidence():
    finding_validation = Task(
        task_uid="verify-1",
        title="Verify finding",
        objective="verify",
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )
    standard_task = Task(
        task_uid="task-1",
        title="Assess endpoint",
        objective="assess",
        phase=1,
        status="active",
    )

    finding_contract = MultiAgentWorkflowController._task_executor_contract(finding_validation)
    standard_contract = MultiAgentWorkflowController._task_executor_contract(standard_task)

    assert "## Finding Validation Methodology" in finding_contract
    assert "negative-control artifact" in finding_contract
    assert "extracted data must appear in the response artifact" in finding_contract
    assert "401 or 403 is evidence of blocking" in finding_contract
    assert "## Finding Validation Methodology" not in standard_contract

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[finding_validation]),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    evaluator_prompt = controller._task_evaluator_prompt(_plan(), _plan().phases[0], finding_validation)

    assert "## Finding Validation Review" in evaluator_prompt
    assert "401 or 403 is blocking evidence" in evaluator_prompt


def test_objective_validation_task_requires_separate_record_tool_and_finalizes(monkeypatch):
    runtime = _runtime()
    task = Task(
        task_uid="objective-verify-1",
        title="Validate flag",
        objective="validate objective candidate",
        phase=1,
        status="active",
        kind="objective_validation",
        reference_id="candidate-1",
    )
    state = FakeState(_plan(), tasks=[task])
    policies = []
    finalize = Mock(return_value="objective_rejected")
    monkeypatch.setattr(workflow_mod, "finalize_objective_validation", finalize)
    monkeypatch.setattr(workflow_mod, "objective_validation_submitted", Mock(return_value=True))
    monkeypatch.setattr(workflow_mod, "objective_validation_outcome", Mock(return_value="rejected"))

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"validate flag","tools":[]}'
        raise AssertionError(role)

    @contextmanager
    def executor_session(role, tools, system_prompt):
        def run(prompt, policy):
            policies.append(policy)
            return "objective validation submitted"

        yield run

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        executor_session_factory=executor_session,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert policies[0].required_tool_names == {"record_objective_validation"}
    finalize.assert_called_once_with(task, "done", "Independent validation did not confirm the candidate.")
    assert state.tasks[0].status == "done"
    assert runtime.callback_handler.events[-1]["finding_resolution"] == "objective_rejected"


def test_task_execution_approval_short_circuits_first_cycle():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 4})
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    actor_calls = []
    evaluator_calls = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        if role == "task_evaluator":
            evaluator_calls.append(prompt)
            return '{"status":"done","reason":"approved immediately"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        actor_calls.append(prompt)
        return "complete"

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )
    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(actor_calls) == 1
    assert len(evaluator_calls) == 1
    assert state.tasks[0].status == "done"


def test_task_execution_persists_final_non_approval_and_clamps_cycle_count():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": -1})
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    actor_calls = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"blocked","reason":"credentials are unavailable"}'
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt, run_policy: actor_calls.append(prompt),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert controller.task_execution_cycles == 1
    assert len(actor_calls) == 1
    assert state.tasks[0].status == "blocked"
    assert state.tasks[0].status_reason == "credentials are unavailable"


def test_acceptance_corrections_extend_same_executor_beyond_normal_cycle_limit():
    runtime = _runtime(
        env_ints={
            "CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1,
            "CYBER_TASK_ACCEPTANCE_MAX_CORRECTIONS": 2,
        }
    )
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    actor_prompts = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"accepted"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        actor_prompts.append(prompt)
        call_number = len(actor_prompts)
        if call_number < 3:
            return workflow_mod.TaskExecutorCycleResult(
                text="rejected acceptance",
                outcomes=[ToolOutcome(
                    sequence=call_number,
                    tool_use_id=f"acceptance-{call_number}",
                    tool_name="record_task_acceptance",
                    success=False,
                    correctable=False,
                    input_summary=f"payload-{call_number}",
                    output_summary=f"field error {call_number}",
                )],
            )
        state.acceptance_results[task.task_uid] = [AcceptanceResult(
            criterion_id=task.acceptance.criteria[0].id,
            status="satisfied",
            disposition="observation",
            summary="Accepted result",
            evidence_refs=("memory:test-evidence",),
        )]
        return workflow_mod.TaskExecutorCycleResult(text="accepted", outcomes=[])

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )
    assert controller._task_acceptance_correction_count() == 2

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(actor_prompts) == 3
    assert "Missing criterion:" in actor_prompts[1]
    assert "Required tool call: record_task_acceptance" in actor_prompts[1]
    assert "Missing criterion:" in actor_prompts[2]
    assert "Required tool call: record_task_acceptance" in actor_prompts[2]
    assert state.tasks[0].status == "done"


@pytest.mark.parametrize("replayed", [False, True])
def test_complete_acceptance_supersedes_repeated_rejection_after_recovery(replayed):
    runtime = _runtime(
        env_ints={
            "CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1,
            "CYBER_TASK_ACCEPTANCE_MAX_CORRECTIONS": 2,
        }
    )
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    executor_prompts = []
    evaluator_prompts = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        if role == "task_evaluator":
            evaluator_prompts.append(prompt)
            return '{"status":"done","reason":"durable acceptance approved"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        executor_prompts.append(prompt)
        if len(executor_prompts) <= 2:
            return workflow_mod.TaskExecutorCycleResult(
                text="acceptance rejected",
                outcomes=[ToolOutcome(
                    sequence=len(executor_prompts),
                    tool_use_id=f"acceptance-{len(executor_prompts)}",
                    tool_name="record_task_acceptance",
                    success=False,
                    correctable=False,
                    input_summary="same-rejected-payload",
                    output_summary="finding prerequisite missing",
                )],
                recovery_required=len(executor_prompts) == 2,
                recovery_guidance="create the finding prerequisite",
            )
        state.acceptance_results[task.task_uid] = [AcceptanceResult(
            criterion_id=task.acceptance.criteria[0].id,
            status="satisfied",
            disposition="finding_candidate",
            summary="Corrected acceptance",
            evidence_refs=("finding:candidate-1",),
        )]
        return workflow_mod.TaskExecutorCycleResult(
            text="acceptance stored",
            outcomes=[ToolOutcome(
                sequence=3,
                tool_use_id="acceptance-success",
                tool_name="record_task_acceptance",
                success=True,
                correctable=False,
                input_summary="corrected-payload",
                output_summary=json.dumps({"complete": True, "replayed": replayed}),
                recovery_role="correction",
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(executor_prompts) == 3
    assert executor_prompts[-1] == "create the finding prerequisite"
    assert len(evaluator_prompts) == 1
    assert state.tasks[0].status == "done"
    assert state.tasks[0].status_reason == "durable acceptance approved"


def test_evaluator_persists_high_confidence_unverified_suggestion_without_failing_task(monkeypatch):
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1})
    task = Task(task_uid="active", title="Active", objective="test injection", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    actor_prompts = []
    evaluator_calls = 0
    advisories = []

    def store_advisory(content, artifacts=None, metadata=None):
        advisories.append((content, artifacts, metadata))
        return json.dumps({"stored": True, "created": True, "memory_ref": "memory:advisory-1"})

    monkeypatch.setattr(workflow_mod, "store_observation", store_advisory)

    def text_runner(role, prompt, tools, system_prompt):
        nonlocal evaluator_calls
        if role == "task_prompt_builder":
            return '{"prompt":"test injection","tools":[]}'
        if role == "task_evaluator":
            evaluator_calls += 1
            if evaluator_calls == 1:
                return json.dumps({
                    "status": "done",
                    "reason": "The response artifact proves command injection.",
                    "instructions": "",
                    "finding_recommendation": {
                        "required": True,
                        "confidence": 0.96,
                        "reason": "Artifact shows direct command execution and data disclosure.",
                    },
                })
            assert "finding-1" in prompt
            return '{"status":"done","reason":"linked finding recorded"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        actor_prompts.append(prompt)
        if len(actor_prompts) == 1:
            state.acceptance_results[task.task_uid] = [AcceptanceResult(
                criterion_id=task.acceptance.criteria[0].id,
                status="satisfied",
                disposition="observation",
                summary="Command injection retrieved protected data",
                evidence_refs=("artifact:artifacts/command-injection.txt",),
            )]
            return workflow_mod.TaskExecutorCycleResult(
                text="acceptance stored",
                outcomes=[ToolOutcome(
                    sequence=1,
                    tool_use_id="evidence-capture",
                    tool_name="shell",
                    success=True,
                    correctable=False,
                    input_summary="capture response",
                    output_summary="artifact:artifacts/command-injection.txt",
                )],
            )
        state.finding_records.append({
            "finding_uid": "finding-1",
            "candidate_data": {"title": "Command injection", "source_task_uids": [task.task_uid]},
            "resolution": None,
        })
        return workflow_mod.TaskExecutorCycleResult(
            text="finding stored",
            outcomes=[ToolOutcome(
                sequence=2,
                tool_use_id="finding-store",
                tool_name="store_finding",
                success=True,
                correctable=False,
                input_summary="artifact-backed finding",
                output_summary="finding:finding-1",
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(actor_prompts) == 1
    assert state.acceptance_results[task.task_uid][0].disposition == "observation"
    assert state.tasks[0].status == "done"
    assert not state.finding_records
    assert advisories == [
        (
            (
                "Unverified evaluator suggestion (not a finding candidate): "
                "Artifact shows direct command execution and data disclosure."
            ),
            ["artifact:artifacts/command-injection.txt"],
            {
                "record_kind": "unverified_evaluator_suggestion",
                "source_task_uid": task.task_uid,
                "phase_id": 1,
                "confidence": 0.96,
                "finding_status": "not_a_finding",
            },
        )
    ]
    assert state.tasks[0].recovery_context["evaluator_finding_advisory"]["memory_ref"] == "memory:advisory-1"


def test_evaluator_does_not_repair_low_confidence_observation_recommendation():
    task = Task(task_uid="active", title="Active", objective="test behavior", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    acceptance_results = [AcceptanceResult(
        criterion_id=task.acceptance.criteria[0].id,
        status="satisfied",
        disposition="observation",
        summary="Potentially interesting response difference",
        evidence_refs=("artifact:artifacts/response.txt",),
    )]

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: json.dumps({
            "status": "done",
            "reason": "Observation is sufficiently recorded.",
            "finding_recommendation": {"required": True, "confidence": 0.89, "reason": "Needs more proof."},
        }),
    )

    decision = controller._evaluate_task(_plan(), _plan().phases[0], task, acceptance_results=acceptance_results)

    assert decision.status == "done"
    assert decision.finding_recommendation_required is False


def test_preflighted_task_blocks_when_runtime_capability_is_unavailable(monkeypatch):
    task = Task(
        task_uid="active",
        title="Bounded crawl",
        objective="Crawl target",
        phase=1,
        status="active",
        acceptance=_acceptance(),
        recovery_context={"task_preflight_validated": True},
    )
    state = FakeState(_plan(), tasks=[task])
    events = []
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: pytest.fail("blocked task must not build a prompt"),
    )
    monkeypatch.setattr(controller, "_emit_workflow_event", events.append)

    controller._run_task(_plan(), _plan().phases[0], task)

    assert state.tasks[0].status == "blocked"
    preflight_event = next(event for event in events if event["type"] == "task_preflight_validation")
    assert preflight_event["code"] == "runtime_drift"


@pytest.mark.parametrize(
    "recommendation",
    [
        [],
        {"required": "yes", "confidence": 0.95, "reason": "claim"},
        {"required": True, "confidence": 1.1, "reason": "claim"},
        {"required": True, "confidence": 0.95, "reason": ""},
    ],
)
def test_task_evaluator_rejects_malformed_finding_recommendations(recommendation):
    with pytest.raises(workflow_mod.WorkflowInvariantError):
        MultiAgentWorkflowController._finding_recommendation_from_evaluator({
            "finding_recommendation": recommendation,
        })


def test_task_evaluator_treats_empty_finding_recommendation_as_omitted():
    assert MultiAgentWorkflowController._finding_recommendation_from_evaluator({
        "finding_recommendation": {},
    }) is None


def test_task_evaluator_repairs_invalid_optional_recommendation_once():
    task = Task(task_uid="active", title="Active", objective="Assess behavior", phase=1, status="active")
    responses = iter((
        json.dumps({
            "status": "done",
            "reason": "Artifact-backed negative result is sufficient.",
            "finding_recommendation": {"required": "no"},
        }),
        json.dumps({
            "status": "done",
            "reason": "Artifact-backed negative result is sufficient.",
        }),
    ))
    prompts = []

    def text_runner(role, prompt, tools, system_prompt):
        assert role == "task_evaluator"
        prompts.append(prompt)
        return next(responses)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    decision = controller._evaluate_task(_plan(), _plan().phases[0], task)

    assert decision.status == "done"
    assert len(prompts) == 2
    assert "finding_recommendation.required must be a boolean" in prompts[1]
    assert "Previous response to correct:" in prompts[1]


def test_task_evaluator_contains_repeated_invalid_optional_recommendation():
    task = Task(task_uid="active", title="Active", objective="Assess behavior", phase=1, status="active")
    responses = []

    def text_runner(role, prompt, tools, system_prompt):
        assert role == "task_evaluator"
        responses.append(prompt)
        return json.dumps({
            "status": "done",
            "reason": "Artifact-backed negative result is sufficient.",
            "finding_recommendation": {"required": "no"},
        })

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    decision = controller._evaluate_task(_plan(), _plan().phases[0], task)

    assert decision.status == "partial_failure"
    assert "failed the required decision schema after bounded retries" in decision.reason
    assert len(responses) == 2


def test_repeated_acceptance_rejection_remains_terminal_while_ledger_is_incomplete():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    actor_prompts = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        pytest.fail("an incomplete repeated acceptance must not reach the evaluator")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        actor_prompts.append(prompt)
        return workflow_mod.TaskExecutorCycleResult(
            text="acceptance rejected",
            outcomes=[ToolOutcome(
                sequence=len(actor_prompts),
                tool_use_id=f"acceptance-{len(actor_prompts)}",
                tool_name="record_task_acceptance",
                success=False,
                correctable=False,
                input_summary="same-rejected-payload",
                output_summary="finding prerequisite missing",
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(actor_prompts) == 2
    assert state.tasks[0].status == "partial_failure"
    assert state.tasks[0].status_reason == "record_task_acceptance repeated an equivalent rejected submission."


def test_complete_acceptance_does_not_bypass_finding_validation_gate(monkeypatch):
    task = Task(
        task_uid="verify-1",
        title="Verify finding",
        objective="verify",
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )
    state = FakeState(_plan(), tasks=[task])
    monkeypatch.setattr(workflow_mod, "finding_validation_submitted", Mock(return_value=False))
    monkeypatch.setattr(workflow_mod, "finalize_finding_validation", Mock(return_value=None))

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"verify","tools":[]}'
        pytest.fail("finding validation must complete before semantic evaluation")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt, run_policy: "acceptance stored",
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert state.tasks[0].status == "partial_failure"
    assert state.tasks[0].status_reason == "Finding validation was not recorded by record_finding_validation."


def test_repeated_structured_failure_stops_across_retained_executor_cycles():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 3})
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    actor_prompts = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        actor_prompts.append(prompt)
        return workflow_mod.TaskExecutorCycleResult(
            text="finding rejected",
            outcomes=[ToolOutcome(
                sequence=len(actor_prompts),
                tool_use_id=f"finding-{len(actor_prompts)}",
                tool_name="store_finding",
                success=False,
                correctable=True,
                input_summary='{"artifacts": []}',
                output_summary="At least one existing artifact is required",
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(actor_prompts) == 2
    assert state.tasks[0].status == "partial_failure"
    assert "store_finding repeated an equivalent rejected submission" in state.tasks[0].status_reason


def test_task_acceptance_gate_skips_evaluator_and_lists_missing_criteria():
    task = TaskModel(
        task_uid="coverage",
        title="Map parameters",
        objective="Map the frozen endpoint inventory",
        acceptance=AcceptanceContract(
            mode="coverage",
            basis=AcceptanceBasis(
                kind="snapshot",
                description="Endpoint enumeration snapshot",
                source_refs=["memory:endpoints"],
                item_ids=["endpoint:/login.php"],
            ),
            criteria=[
                AcceptanceCriterion(
                    id="assess-the-assigned-endpoint",
                    description="Assess the assigned endpoint",
                    evidence_requirements=[EvidenceRequirement(kind="artifact")],
                )
            ],
        ),
        phase=1,
        status="active",
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    roles = []

    def text_runner(role, prompt, tools, system_prompt):
        roles.append(role)
        if role == "task_prompt_builder":
            return '{"prompt":"map manifest","memory_ids":[],"tools":[],"shell_commands":[]}'
        raise AssertionError(f"unexpected role: {role}")

    @contextmanager
    def executor_session(role, tools, system_prompt):
        yield lambda prompt, policy: "Executor stopped without recording acceptance results"

    controller = MultiAgentWorkflowController(
        runtime=_runtime(
            env_ints={
                "CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 0,
                "CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1,
            }
        ),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        executor_session_factory=executor_session,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert roles == ["task_prompt_builder"]
    assert state.tasks[0].status == "partial_failure"
    assert "assess-the-assigned-endpoint" in state.tasks[0].status_reason


def test_missing_acceptance_replays_mechanically_complete_evidence(monkeypatch, tmp_path):
    artifact = tmp_path / "endpoint.html"
    artifact.write_text("endpoint response", encoding="utf-8")
    task = TaskModel(
        task_uid="hypotheses",
        title="Generate hypotheses",
        objective="Assess the assigned endpoint",
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Hypothesis generation",
                source_refs=["target:target-1"],
                procedure={
                    "methods": ["review"],
                    "limits": {"max_items": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[
                AcceptanceCriterion(
                    id="assess-the-assigned-endpoint",
                    description="Document endpoint hypotheses",
                    evidence_requirements=[EvidenceRequirement(kind="artifact")],
                )
            ],
        ),
        phase=1,
        status="active",
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    calls = []

    def text_runner(role, *_args):
        if role == "task_prompt_builder":
            return '{"prompt":"assess endpoint","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"acceptance recorded"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        calls.append((prompt, run_policy, {workflow_mod.get_tool_name(tool) for tool in tools}))
        if len(calls) == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="endpoint response recorded",
                outcomes=[ToolOutcome(
                    1,
                    "request",
                    "http_request",
                    True,
                    False,
                    "GET /endpoint",
                    "saved artifact:artifacts/endpoint.html",
                )],
            )
        state.acceptance_results[task.task_uid] = [AcceptanceResult(
            criterion_id="assess-the-assigned-endpoint",
            status="satisfied",
            disposition="observation",
            summary="Testable endpoint hypotheses were recorded.",
            evidence_refs=("memory:task-hypotheses",),
        )]
        return workflow_mod.TaskExecutorCycleResult(
            text="observation and acceptance recorded",
            outcomes=[
                ToolOutcome(
                    2,
                    "observation",
                    "store_observation",
                    True,
                    False,
                    "hypotheses",
                    '{"memory_ref":"memory:task-hypotheses"}',
                ),
                ToolOutcome(
                    3,
                    "acceptance",
                    "record_task_acceptance",
                    True,
                    False,
                    "acceptance",
                    "acceptance stored",
                ),
            ],
        )

    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1})
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )
    monkeypatch.setattr(workflow_mod, "canonical_artifact_reference", lambda reference: reference)
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(artifact))

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(calls) == 1
    event = next(event for event in runtime.callback_handler.events if event["type"] == "controller_acceptance_replay")
    assert event["outcome"] == "recorded"
    assert state.tasks[0].status == "done"


def test_missing_acceptance_without_durable_evidence_does_not_get_terminal_recovery():
    task = Task(task_uid="no-evidence", title="No evidence", objective="Assess endpoint", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    calls = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        calls.append((prompt, run_policy))
        return workflow_mod.TaskExecutorCycleResult(text="no durable result", outcomes=[])

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, *_args: (
            '{"prompt":"assess endpoint","tools":[]}'
            if role == "task_prompt_builder"
            else '{"status":"done","reason":"unexpected"}'
        ),
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(calls) == 1
    assert state.tasks[0].status == "partial_failure"


def test_rejected_acceptance_does_not_use_missing_acceptance_recovery():
    task = Task(task_uid="rejected", title="Rejected", objective="Assess endpoint", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    calls = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        calls.append((prompt, run_policy))
        return workflow_mod.TaskExecutorCycleResult(
            text="rejected acceptance",
            outcomes=[ToolOutcome(
                1,
                "acceptance",
                "record_task_acceptance",
                False,
                False,
                '{"status":"satisfied"}',
                "Acceptance evidence reference is invalid",
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(
            env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1, "CYBER_TASK_ACCEPTANCE_MAX_CORRECTIONS": 0}
        ),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, *_args: '{"prompt":"assess endpoint","tools":[]}',
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(calls) == 1
    assert not any(
        event.get("reason") == "missing_acceptance" for event in controller.runtime.callback_handler.events
    )


def test_acceptance_correction_turn_limits_tools_to_read_then_accept():
    task = Task(task_uid="acceptance-correction", title="Correct acceptance", objective="Assess endpoint", phase=1,
                status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    calls = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        calls.append((prompt, run_policy, {workflow_mod.get_tool_name(tool) for tool in tools}))
        if len(calls) == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="invalid acceptance",
                outcomes=[ToolOutcome(
                    1,
                    "acceptance",
                    "record_task_acceptance",
                    False,
                    False,
                    '{"status":"unknown"}',
                    "unknown enum value",
                )],
            )
        state.acceptance_results[task.task_uid] = [AcceptanceResult(
            criterion_id="criterion-1",
            status="satisfied",
            disposition="observation",
            summary="Assessment is complete.",
            evidence_refs=("memory:assessment",),
        )]
        return workflow_mod.TaskExecutorCycleResult(
            text="corrected acceptance",
            outcomes=[ToolOutcome(
                2,
                "acceptance",
                "record_task_acceptance",
                True,
                False,
                '{"status":"satisfied"}',
                "acceptance stored",
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, *_args: '{"prompt":"assess endpoint","tools":[]}',
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(calls) == 2
    assert calls[1][1].required_tool_names == {"record_task_acceptance"}
    assert calls[1][2] <= {"read_artifact", "record_task_acceptance"}


def test_task_executor_appends_selected_memories_from_prompt_spec():
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")],
    )
    requested_memory_ids = []
    state.client = SimpleNamespace(
        get_memory_by_id=lambda memory_id: requested_memory_ids.append(memory_id) or {
            "id": memory_id,
            "memory": f"memory for {memory_id}",
        }
    )
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[],"memory_ids":["m2","m1","m2",7,""]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"completed"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        captured["prompt"] = prompt

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert requested_memory_ids == ["m2", "m1"]
    assert captured["prompt"].startswith("execute active\n\n## Frozen Task Acceptance Contract (Controller-owned)")
    assert "## Selected Memory Context\n" in captured["prompt"]
    assert "memories[2]{id,category,source,origin_operation,evidence_status,memory}:" in captured["prompt"]
    assert "m2,general,,unknown,prior_operation_advisory,memory for m2" in captured["prompt"]
    assert "m1,general,,unknown,prior_operation_advisory,memory for m1" in captured["prompt"]


def test_task_executor_continues_when_selected_memory_lookup_fails():
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")],
    )
    state.client = SimpleNamespace(get_memory_by_id=lambda memory_id: (_ for _ in ()).throw(RuntimeError("fail")))
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[],"memory_ids":["missing"]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"completed"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        captured["prompt"] = prompt

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert captured["prompt"].startswith("execute active\n\n## Frozen Task Acceptance Contract (Controller-owned)")


def test_task_prompt_normalization_drops_stale_memory_references_and_prefers_indices():
    state = FakeState(_plan())
    state.client = SimpleNamespace(
        list_memories=lambda **kwargs: [
            {"id": "memory-1", "memory": "first"},
            {"id": "memory-2", "memory": "second"},
        ]
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    normalized = controller._normalize_task_prompt_spec(
        {
            "prompt": "execute active",
            "memory_indices": [1, 1, 99],
            "memory_ids": ["memory-1", "memory-2-corrupted"],
            "tools": [],
            "shell_commands": [],
        },
        Task(task_uid="active", title="Active", objective="run active", phase=1, status="active"),
    )

    assert normalized["memory_ids"] == ["memory-2"]
    assert normalized["memory_indices"] == [1]


def test_malformed_memory_id_is_dropped_during_prompt_prevalidation():
    state = FakeState(_plan())
    state.client = SimpleNamespace(
        list_memories=lambda **kwargs: [
            {"id": "98d09291-78dc-443c-aba2-f2c4b46dc7fc", "memory": "validated observation"},
        ]
    )
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    normalized = controller._normalize_task_prompt_spec(
        {
            "prompt": "execute active",
            "memory_ids": ["98d09291-78dc-443c-aba2-f2c4b467fc"],
            "tools": [],
            "shell_commands": [],
        },
        task,
    )

    assert normalized["memory_ids"] == []


def test_malformed_memory_id_reaches_prompt_critic_with_valid_prompt():
    state = FakeState(_plan())
    state.client = SimpleNamespace(
        list_memories=lambda **kwargs: [
            {"id": "98d09291-78dc-443c-aba2-f2c4b46dc7fc", "memory": "validated observation"},
        ]
    )
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append((role, prompt))
        if role == "task_prompt_builder":
            return (
                '{"prompt":"execute active","memory_ids":'
                '["98d09291-78dc-443c-aba2-f2c4b467fc"],"tools":[],"shell_commands":[]}'
            )
        if role == "task_prompt_critic":
            return '{"approved":true,"feedback":[]}'
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    prompt_spec = controller._build_task_prompt(
        _plan(),
        _plan().phases[0],
        Task(task_uid="active", title="Active", objective="run active", phase=1, status="active"),
    )

    assert prompt_spec["memory_ids"] == []
    assert [role for role, _prompt in calls] == ["task_prompt_builder", "task_prompt_critic"]


def test_task_executor_uses_deterministic_template_for_unknown_selected_shell_commands(monkeypatch):
    runtime = _runtime()
    runtime.config.available_tools = ["httpx", "nmap"]
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")],
    )
    worker_prompts = []
    monkeypatch.setattr(
        workflow_mod,
        "get_shell_command_specs",
        lambda available: [
            {
                "command": "httpx",
                "description": "HTTP probe",
                "capabilities": ["web_recon"],
            },
            {
                "command": "nmap",
                "description": "Port scan",
                "capabilities": ["network_scan"],
            },
        ],
    )

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[],"shell_commands":["httpx","unknown","nmap"]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"completed"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        worker_prompts.append(prompt)

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert worker_prompts
    assert "Prompt-build fallback reason" in worker_prompts[0]
    assert "Supplemental Shell Commands" not in worker_prompts[0]
    fallback = next(
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "task_prompt_fallback"
    )
    assert fallback["reason"] == "task_prompt_builder_unavailable"


def test_task_executor_omits_shell_command_context_for_missing_or_invalid_selection():
    runtime = _runtime()
    runtime.config.available_tools = ["httpx"]
    runtime.core_tools_list = [_tool("memory_retrieve")]
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")],
    )
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[],"shell_commands":["httpx"]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"completed"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        captured["prompt"] = prompt

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert captured["prompt"].startswith("execute active\n\n## Frozen Task Acceptance Contract (Controller-owned)")


@pytest.mark.parametrize(
    ("preserve", "expected_phase"),
    [(True, 2), (False, 1)],
)
def test_executor_follow_up_future_phase_requires_affirmative_classification(preserve, expected_phase):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Discovery", status="active", criteria="Map endpoints"),
            PlanPhase(id=2, title="Validation", status="pending", criteria="Validate mapped controls"),
        ],
    )
    future_task = Task(
        task_uid="follow-up",
        title="Validate control",
        objective="Validate a mapped control with a negative control artifact",
        phase=2,
        status="pending",
    )
    state = FakeState(plan, tasks=[future_task])

    def text_runner(role, prompt, tools, system_prompt):
        assert role == "task_phase_classifier"
        assert "Validate mapped controls" in prompt
        return (
            '{"decisions":[{"task_uid":"follow-up",'
            f'"preserve_requested_phase":{str(preserve).lower()},'
            '"reason":"criterion classification"}]}'
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    controller._validate_executor_follow_up_phases(plan, plan.phases[0], set())

    assert state.tasks[0].phase == expected_phase
    assert state.tasks[0].task_uid == "follow-up"


def test_executor_follow_up_classifier_failure_reclassifies_to_active_phase():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Discovery", status="active", criteria="Map endpoints"),
            PlanPhase(id=2, title="Validation", status="pending", criteria="Validate controls"),
        ],
    )
    state = FakeState(
        plan,
        tasks=[Task(
            task_uid="follow-up",
            title="More discovery",
            objective="Map another endpoint",
            phase=2,
            status="pending",
        )],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "not-json",
    )

    controller._validate_executor_follow_up_phases(plan, plan.phases[0], set())

    assert state.tasks[0].phase == 1


def test_executor_follow_up_in_active_phase_skips_classifier():
    plan = _plan()
    state = FakeState(
        plan,
        tasks=[Task(
            task_uid="follow-up",
            title="More discovery",
            objective="Map another endpoint",
            phase=1,
            status="pending",
        )],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: pytest.fail(role),
    )

    controller._validate_executor_follow_up_phases(plan, plan.phases[0], set())

    assert state.tasks[0].phase == 1


def test_task_evaluator_receives_task_worker_final_context():
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")],
    )
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        if role == "task_evaluator":
            captured["evaluator_prompt"] = prompt
            return '{"status":"partial_failure","reason":"found blocker"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        return {"message": {"content": [{"text": "Checked target. Missing credentials blocked validation."}]}}

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert "## Task worker final context" in captured["evaluator_prompt"]
    assert "Missing credentials blocked validation." in captured["evaluator_prompt"]
    assert state.tasks[0].status == "partial_failure"


def test_task_evaluator_uses_memory_as_evidence_without_inventing_a_requirement():
    state = FakeState(
        _plan(),
        tasks=[
            Task(
                task_uid="active",
                title="Inventory routes",
                objective="Identify exposed routes and collect their response status",
                phase=1,
                status="active",
            )
        ],
    )
    state.client = SimpleNamespace(
        list_memories=lambda **kwargs: [
            {
                "id": "obs-1",
                "memory": "[OBSERVATION] /login returned 200 and /admin returned 403",
                "metadata": {"category": "observation"},
            }
        ]
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    prompt = controller._task_evaluator_prompt(
        _plan(),
        _plan().phases[0],
        state.tasks[0],
        worker_context="I checked the routes and stored the results.",
    )

    assert "Use done only" in prompt
    assert "Require memory or observation evidence only when the frozen criterion explicitly declares" in prompt
    assert "not an additional acceptance criterion" in prompt
    assert "[OBSERVATION] /login returned 200 and /admin returned 403" in prompt


def test_task_evaluator_does_not_invent_memory_requirement_for_gathered_information():
    state = FakeState(
        _plan(),
        tasks=[
            Task(
                task_uid="active",
                title="Inventory routes",
                objective="Enumerate exposed routes and document response status",
                phase=1,
                status="active",
            )
        ],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    prompt = controller._task_evaluator_prompt(
        _plan(),
        _plan().phases[0],
        state.tasks[0],
        worker_context="Found /login 200 and /admin 403 but did not store observations.",
    )

    assert "memories[0]{id,category,source,origin_operation,evidence_status,memory}:" in prompt
    assert "did not store it in memories" not in prompt
    assert "Automatically published acceptance memory supports later tasks" in prompt


def test_task_executor_recovers_in_same_session_and_evaluator_receives_authoritative_outcomes():
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="enumerate paths", phase=1, status="active")],
    )
    captured = {"executor_prompts": []}
    failed = workflow_mod.ToolOutcome(
        sequence=1,
        tool_use_id="failed",
        tool_name="shell",
        success=False,
        correctable=True,
        input_summary="feroxbuster -w /missing.txt",
        output_summary="Could not open /missing.txt",
    )
    corrected = workflow_mod.ToolOutcome(
        sequence=2,
        tool_use_id="corrected",
        tool_name="shell",
        success=True,
        correctable=False,
        input_summary="feroxbuster -w /valid.txt",
        output_summary="200 /login.php",
        recovery_role="correction",
    )

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"enumerate paths","tools":[]}'
        if role == "task_evaluator":
            captured["evaluator_prompt"] = prompt
            return '{"status":"done","reason":"corrected scan stored evidence"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        captured["executor_prompts"].append(prompt)
        if len(captured["executor_prompts"]) == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="feroxbuster found fake paths",
                outcomes=[failed],
                recovery_required=True,
                recovery_guidance="correct the missing wordlist",
            )
        return workflow_mod.TaskExecutorCycleResult(text="found /login.php", outcomes=[corrected])

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert captured["executor_prompts"][0].startswith(
        "enumerate paths\n\n## Frozen Task Acceptance Contract (Controller-owned)"
    )
    assert captured["executor_prompts"][0].endswith(
        controller._task_executor_contract(state.tasks[0], {"store_finding"})
        + "\n\n"
        + controller._tool_selection_policy()
    )
    assert captured["executor_prompts"][1] == "correct the missing wordlist"
    assert "## Controller-observed tool outcomes" in captured["evaluator_prompt"]
    assert "Could not open /missing.txt" in captured["evaluator_prompt"]
    assert "200 /login.php" in captured["evaluator_prompt"]
    assert state.tasks[0].status == "done"


def test_task_executor_uses_fresh_sessions_for_compact_continuations():
    lifecycle = []

    @contextmanager
    def session(role, tools, system_prompt):
        lifecycle.append(("open", role))
        try:
            yield lambda prompt, policy: SimpleNamespace(prompt=prompt, policy=policy)
        finally:
            lifecycle.append(("close", role))

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        executor_session_factory=session,
    )

    with controller._task_executor_session("task_executor", [], "system") as run_executor:
        run_executor("initial", None)
        run_executor("continuation", None)

    task = Task(
        task_uid="task",
        title="Task",
        objective="Assess one behavior",
        phase=1,
        status="active",
        acceptance=_acceptance("criterion-1"),
    )
    prompt = controller._task_executor_critic_guidance(
        _plan(),
        task,
        ["criterion-1"],
        [{"reference": "artifact:artifacts/result.txt", "source": "task_evidence"}],
        next_cycle=2,
    )

    assert lifecycle == [
        ("open", "task_executor"),
        ("close", "task_executor"),
        ("open", "task_executor"),
        ("close", "task_executor"),
    ]
    assert "Start a fresh actor cycle 2" in prompt
    assert "Assigned objective: Assess one behavior" in prompt
    assert "Frozen acceptance criteria: criterion-1:" in prompt
    assert "artifact:artifacts/result.txt (source: task_evidence)" in prompt
    assert "Do not perform network discovery" in prompt


def test_acceptance_recovery_context_merges_task_ledger_outcomes_and_rejection(monkeypatch):
    task = Task(
        task_uid="task",
        title="Task",
        objective="Assess one behavior",
        phase=1,
        status="active",
        evidence=["artifact:artifacts/task.txt", "not-a-reference"],
    )
    acceptance = AcceptanceResult(
        criterion_id=task.acceptance.criteria[0].id,
        status="satisfied",
        disposition="observation",
        summary="prior evidence",
        evidence_refs=("memory:prior-observation",),
    )
    rejected = ToolOutcome(
        1,
        "rejected",
        "record_task_acceptance",
        False,
        False,
        json.dumps({"evidence_refs": ["artifact:artifacts/rejected.txt", "https://invalid.example"]}),
        "invalid evidence",
    )
    outcome = ToolOutcome(
        2,
        "tool",
        "http_request",
        True,
        False,
        "request",
        "saved artifact:artifacts/outcome.txt",
        artifact_refs=("artifact:artifacts/outcome.txt",),
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=lambda *_args: "{}",
    )
    monkeypatch.setattr(workflow_mod, "canonical_artifact_reference", lambda reference: reference)

    context = controller._acceptance_recovery_context(task, [acceptance], [outcome], rejected)

    assert [{"reference": item["reference"], "source": item["source"]} for item in context] == [
        {"reference": "artifact:artifacts/task.txt", "source": "task_evidence"},
        {"reference": "artifact:artifacts/outcome.txt", "source": "tool_outcome"},
        {"reference": "artifact:artifacts/rejected.txt", "source": "rejected_acceptance"},
        {"reference": "memory:prior-observation", "source": "prior_acceptance"},
    ]
    assert all(item["usable"] == "false" for item in context if item["reference"].startswith("artifact:"))


def test_durable_artifact_references_exclude_consumer_tool_mentions():
    outcomes = [
        ToolOutcome(
            1,
            "recon",
            "specialized_recon_orchestrator",
            True,
            False,
            "target",
            "created inventory",
            artifact_refs=("artifact:artifacts/inventory_manifest.json",),
        ),
        ToolOutcome(
            2,
            "memory-list",
            "memory_list",
            True,
            False,
            "",
            "old evidence artifact:artifacts/previous_operation.txt",
            artifact_refs=("artifact:artifacts/previous_operation.txt",),
        ),
        ToolOutcome(
            3,
            "read",
            "read_artifact",
            True,
            False,
            "artifact:artifacts/inventory_manifest.json",
            "content",
            artifact_refs=("artifact:artifacts/inventory_manifest.json",),
        ),
    ]

    assert MultiAgentWorkflowController._artifact_refs_from_tool_outcomes(outcomes) == [
        "artifact:artifacts/inventory_manifest.json"
    ]


def test_durable_artifact_references_capture_operation_local_absolute_paths(monkeypatch, tmp_path):
    operation_root = tmp_path / "operation"
    artifacts = operation_root / "artifacts"
    artifacts.mkdir(parents=True)
    katana = artifacts / "katana_crawl.txt"
    browser = artifacts / "browser_page.html"
    katana.write_text("http://target.test/", encoding="utf-8")
    browser.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr("modules.tools.memory._operation_output_root", lambda: str(operation_root))
    outcome = ToolOutcome(
        1,
        "crawl",
        "shell",
        True,
        False,
        f'{{"command":"katana -o {katana}"}}',
        f"HTML content saved to artifact: {browser}",
    )

    assert MultiAgentWorkflowController._artifact_refs_from_tool_outcomes([outcome]) == [
        "artifact:artifacts/browser_page.html",
        "artifact:artifacts/katana_crawl.txt",
    ]


def test_absolute_artifact_discovery_rejects_outside_missing_and_directory_paths(monkeypatch, tmp_path):
    operation_root = tmp_path / "operation"
    operation_root.mkdir()
    directory = operation_root / "artifacts"
    directory.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    missing = operation_root / "missing.txt"
    monkeypatch.setattr("modules.tools.memory._operation_output_root", lambda: str(operation_root))
    text = f"{outside} {missing} {directory}"

    assert MultiAgentWorkflowController._operation_local_artifact_refs_in_text(text) == []


def test_acceptance_recovery_context_retains_absolute_tool_artifacts(monkeypatch, tmp_path):
    operation_root = tmp_path / "operation"
    artifacts = operation_root / "artifacts"
    artifacts.mkdir(parents=True)
    katana = artifacts / "katana_crawl.txt"
    browser = artifacts / "browser_page.html"
    katana.write_text("http://target.test/", encoding="utf-8")
    browser.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr("modules.tools.memory._operation_output_root", lambda: str(operation_root))
    task = Task(task_uid="task", title="Task", objective="Assess behavior", phase=1, status="active")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
    )
    outcome = ToolOutcome(
        1,
        "crawl",
        "shell",
        True,
        False,
        f"katana -o {katana}",
        f"Browser output saved to {browser}",
    )

    context = controller._acceptance_recovery_context(task, [], [outcome], None)

    assert [item["reference"] for item in context] == [
        "artifact:artifacts/browser_page.html",
        "artifact:artifacts/katana_crawl.txt",
    ]
    assert all(item["usable"] == "true" for item in context)


def test_manifest_recovery_accepts_current_operation_artifact_id(monkeypatch):
    plan = _plan()
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan),
    )
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="mcp-manifest",
        tool_name="mcp_inventory_producer",
        success=True,
        correctable=False,
        input_summary="target",
        output_summary="artifact_id:mcp-inventory.json",
        artifact_refs=("artifact_id:mcp-inventory.json",),
    )
    monkeypatch.setattr(controller, "_execution_artifact_is_current_operation", lambda _reference: True)
    monkeypatch.setattr(
        controller,
        "_load_controller_inventory_manifest",
        lambda _plan, reference: ({"items": [{}]}, reference),
    )

    assert controller._valid_inventory_artifact_refs(plan, [outcome]) == ["artifact_id:mcp-inventory.json"]


def test_acceptance_recovery_context_includes_only_successful_task_created_memory_refs(monkeypatch):
    task = Task(task_uid="task", title="Task", objective="Assess one behavior", phase=1, status="active")
    outcomes = [
        ToolOutcome(
            1,
            "stored-observation",
            "store_observation",
            True,
            False,
            "observation",
            '{"memory_ref":"memory:task-observation"}',
        ),
        ToolOutcome(
            2,
            "failed-knowledge",
            "store_knowledge",
            False,
            True,
            "knowledge",
            '{"memory_ref":"memory:failed"}',
        ),
        ToolOutcome(
            3,
            "retrieval",
            "memory_retrieve",
            True,
            False,
            "query",
            '{"memory_ref":"memory:not-created-by-task"}',
        ),
    ]
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=lambda *_args: "{}",
    )
    monkeypatch.setattr(workflow_mod, "canonical_artifact_reference", lambda reference: reference)

    context = controller._acceptance_recovery_context(task, [], outcomes, None)

    assert context == [{"reference": "memory:task-observation", "source": "task_memory:store_observation"}]


def test_final_memory_acceptance_recovery_replaces_only_rejected_memory_refs():
    payload = {
        "status": "satisfied",
        "disposition": "observation",
        "summary": "Endpoint hypotheses were recorded.",
        "evidence_refs": ["artifact:artifacts/endpoint.txt", "memory:hallucinated"],
    }

    corrected, replacements = MultiAgentWorkflowController._replace_invalid_memory_references(
        payload,
        ["memory:hallucinated"],
        [
            {"reference": "memory:task-observation", "source": "task_memory:store_observation"},
        ],
    )

    assert corrected == {
        **payload,
        "evidence_refs": [
            "artifact:artifacts/endpoint.txt",
            "memory:task-observation",
        ],
    }
    assert replacements == [
        {"rejected": "memory:hallucinated", "replacement": "memory:task-observation"},
    ]


def test_final_memory_acceptance_does_not_guess_between_multiple_task_local_references():
    payload = {
        "status": "satisfied",
        "disposition": "observation",
        "summary": "Endpoint hypotheses were recorded.",
        "evidence_refs": ["memory:hallucinated"],
    }

    corrected, replacements = MultiAgentWorkflowController._replace_invalid_memory_references(
        payload,
        ["memory:hallucinated"],
        [
            {"reference": "memory:task-observation", "source": "task_memory:store_observation"},
            {"reference": "memory:task-knowledge", "source": "task_memory:store_knowledge"},
        ],
    )

    assert corrected == {}
    assert replacements == []


def test_final_memory_acceptance_recovery_requires_the_specific_missing_memory_error():
    payload = {
        "status": "satisfied",
        "disposition": "observation",
        "summary": "Endpoint hypotheses were recorded.",
        "evidence_refs": ["memory:hallucinated"],
    }

    references = MultiAgentWorkflowController._invalid_memory_references(
        "Acceptance evidence reference is invalid: memory:hallucinated",
        payload,
    )

    assert references == []


def test_task_executor_recovers_final_hallucinated_memory_reference_deterministically():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1, "CYBER_TASK_ACCEPTANCE_MAX_CORRECTIONS": 1})
    task = Task(
        task_uid="active",
        title="Generate hypotheses",
        objective="Assess one endpoint",
        acceptance=AcceptanceContract(
            mode="coverage",
            basis=AcceptanceBasis(
                kind="snapshot",
                description="Generate hypotheses",
                source_refs=["memory:seed"],
                item_ids=["endpoint-1"],
            ),
            criteria=[AcceptanceCriterion(
                id="hypotheses",
                description="Store endpoint hypotheses",
                evidence_requirements=[EvidenceRequirement(kind="observation")],
            )],
        ),
        phase=1,
        status="active",
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    attempted_payloads = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        attempted_payloads.append((prompt, run_policy.required_tool_names))
        return workflow_mod.TaskExecutorCycleResult(
            text="hypotheses persisted",
            outcomes=[
                ToolOutcome(
                    len(attempted_payloads) * 2 - 1,
                    f"observation-{len(attempted_payloads)}",
                    "store_observation",
                    True,
                    False,
                    "observation",
                    '{"memory_ref":"memory:task-hypotheses"}',
                )
                if len(attempted_payloads) == 1
                else ToolOutcome(
                    3,
                    "noop",
                    "read_artifact",
                    True,
                    False,
                    "artifact",
                    "read",
                ),
                ToolOutcome(
                    len(attempted_payloads) * 2,
                    f"acceptance-{len(attempted_payloads)}",
                    "record_task_acceptance",
                    False,
                    False,
                    json.dumps(
                        {
                            "status": "satisfied",
                            "disposition": "observation",
                            "summary": "Endpoint hypotheses were recorded.",
                            "evidence_refs": ["memory:hallucinated"],
                        }
                    ),
                    "Acceptance evidence memory does not exist in this operation: memory:hallucinated",
                ),
            ],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, *_args: (
            '{"prompt":"assess endpoint","tools":[]}'
            if role == "task_prompt_builder"
            else '{"status":"done","reason":"acceptance recorded"}'
        ),
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(attempted_payloads) == 2
    assert state.acceptance_results[task.task_uid][0].evidence_refs == ("memory:task-hypotheses",)
    event = next(
        event
        for event in runtime.callback_handler.events
        if event["type"] == "acceptance_memory_reference_recovery"
    )
    assert event["outcome"] == "accepted"
    assert event["rejected_refs"] == ["memory:hallucinated"]
    assert state.tasks[0].status == "done"


def test_invalid_memory_acceptance_reference_requires_storage_before_closure():
    runtime = _runtime(
        env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1, "CYBER_TASK_ACCEPTANCE_MAX_CORRECTIONS": 1}
    )
    task = Task(
        task_uid="active",
        title="Generate hypotheses",
        objective="Assess one endpoint",
        acceptance=AcceptanceContract(
            mode="coverage",
            basis=AcceptanceBasis(
                kind="snapshot",
                description="Generate hypotheses",
                source_refs=["memory:seed"],
                item_ids=["endpoint-1"],
            ),
            criteria=[
                AcceptanceCriterion(
                    id="hypotheses",
                    description="Store endpoint hypotheses",
                    evidence_requirements=[EvidenceRequirement(kind="observation")],
                )
            ],
        ),
        phase=1,
        status="active",
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    calls = []
    rejected_payload = {
        "status": "satisfied",
        "disposition": "observation",
        "summary": "Endpoint hypotheses were recorded.",
        "evidence_refs": ["memory:hallucinated"],
    }

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        tool_names = {workflow_mod.get_tool_name(tool) for tool in tools}
        calls.append((run_policy.required_tool_names, tool_names, prompt))
        if len(calls) == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="acceptance rejected",
                outcomes=[
                    ToolOutcome(
                        1,
                        "acceptance-rejected",
                        "record_task_acceptance",
                        False,
                        False,
                        json.dumps(rejected_payload),
                        "Acceptance evidence memory does not exist in this operation: memory:hallucinated",
                    )
                ],
            )
        if len(calls) == 2:
            return workflow_mod.TaskExecutorCycleResult(
                text="observation stored",
                outcomes=[
                    ToolOutcome(
                        2,
                        "observation-stored",
                        "store_observation",
                        True,
                        False,
                        "Endpoint behavior was assessed.",
                        '{"memory_ref":"memory:task-hypotheses"}',
                    )
                ],
            )

        state.acceptance_results[task.task_uid] = [
            AcceptanceResult(
                criterion_id="hypotheses",
                status="satisfied",
                disposition="observation",
                summary="Endpoint hypotheses were recorded.",
                evidence_refs=("memory:task-hypotheses",),
            )
        ]
        return workflow_mod.TaskExecutorCycleResult(
            text="acceptance stored",
            outcomes=[
                ToolOutcome(
                    3,
                    "acceptance-stored",
                    "record_task_acceptance",
                    True,
                    False,
                    json.dumps({**rejected_payload, "evidence_refs": ["memory:task-hypotheses"]}),
                    "acceptance stored",
                )
            ],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, *_args: (
            '{"prompt":"assess endpoint","tools":[]}'
            if role == "task_prompt_builder"
            else '{"status":"done","reason":"acceptance recorded"}'
        ),
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert [required for required, _tools, _prompt in calls] == [
        {"record_task_acceptance"},
        set(),
        {"record_task_acceptance"},
    ]
    assert calls[1][1] <= {"read_artifact", "store_observation", "store_knowledge"}
    assert "record_task_acceptance" not in calls[1][1]
    assert state.tasks[0].status == "done"


def test_task_executor_unresolved_recovery_is_partial_without_evaluator_approval():
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="enumerate paths", phase=1, status="active")],
    )
    executor_calls = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"enumerate paths","tools":[]}'
        if role == "task_evaluator":
            pytest.fail("an unresolved correctable failure must not be approved")
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        executor_calls.append(prompt)
        return workflow_mod.TaskExecutorCycleResult(
            text="claimed success",
            outcomes=[],
            recovery_required=True,
            recovery_exhausted=len(executor_calls) > 1,
            recovery_guidance="retry once",
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert executor_calls[0].startswith("enumerate paths\n\n## Frozen Task Acceptance Contract (Controller-owned)")
    assert executor_calls[0].endswith(
        controller._task_executor_contract(state.tasks[0], {"store_finding"})
        + "\n\n"
        + controller._tool_selection_policy()
    )
    assert executor_calls[1] == "retry once"
    assert state.tasks[0].status == "partial_failure"
    assert "remained unresolved" in state.tasks[0].status_reason


def test_task_executor_does_not_offer_another_turn_after_correction_was_exhausted():
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="enumerate paths", phase=1, status="active")],
    )
    executor_calls = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"enumerate paths","tools":[]}'
        pytest.fail(f"unexpected evaluator role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        executor_calls.append(prompt)
        return workflow_mod.TaskExecutorCycleResult(
            text="correction failed",
            outcomes=[],
            recovery_required=True,
            recovery_exhausted=True,
            recovery_guidance="must not run",
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert len(executor_calls) == 1
    assert state.tasks[0].status == "partial_failure"


def test_finding_validation_guard_exhaustion_stops_executor_without_replacement(monkeypatch):
    runtime = _runtime()
    task = Task(
        task_uid="verify-1",
        title="Verify finding",
        objective="verify",
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )
    state = FakeState(_plan(), tasks=[task])
    executor_calls = []
    finalize = Mock(return_value="validation_failure")
    monkeypatch.setattr(workflow_mod, "finalize_finding_validation", finalize)

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"verify","tools":[]}'
        pytest.fail(f"unexpected evaluator role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        executor_calls.append(prompt)
        return workflow_mod.TaskExecutorCycleResult(
            text="validation guard blocked further calls",
            outcomes=[ToolOutcome(
                sequence=1,
                tool_use_id="validation-blocked",
                tool_name="record_finding_validation",
                success=False,
                correctable=False,
                input_summary="same-validation-payload",
                output_summary="The configured correction allowance has been exhausted.",
                recovery_role="blocked",
            )],
            recovery_required=True,
            recovery_exhausted=True,
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(executor_calls) == 1
    assert len(state.tasks) == 1
    assert state.tasks[0].status == "partial_failure"
    assert "permanently blocked by the controller guard" in state.tasks[0].status_reason
    finalize.assert_called_once_with(task, "partial_failure", state.tasks[0].status_reason)


def test_finding_validation_guard_exhaustion_during_recovery_stops_executor(monkeypatch):
    runtime = _runtime()
    task = Task(
        task_uid="verify-1",
        title="Verify finding",
        objective="verify",
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )
    state = FakeState(_plan(), tasks=[task])
    executor_calls = []
    finalize = Mock(return_value="validation_failure")
    monkeypatch.setattr(workflow_mod, "finalize_finding_validation", finalize)

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"verify","tools":[]}'
        pytest.fail(f"unexpected evaluator role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        executor_calls.append(prompt)
        if len(executor_calls) == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="retry validation once",
                outcomes=[],
                recovery_required=True,
                recovery_guidance="retry validation with the corrected payload",
            )
        return workflow_mod.TaskExecutorCycleResult(
            text="validation guard blocked further calls",
            outcomes=[ToolOutcome(
                sequence=2,
                tool_use_id="validation-blocked",
                tool_name="record_finding_validation",
                success=False,
                correctable=False,
                input_summary="changed-validation-payload",
                output_summary="The configured correction allowance has been exhausted.",
                recovery_role="blocked",
            )],
            recovery_required=True,
            recovery_exhausted=True,
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert executor_calls[1] == "retry validation with the corrected payload"
    assert len(executor_calls) == 2
    assert len(state.tasks) == 1
    assert state.tasks[0].status == "partial_failure"
    assert "permanently blocked by the controller guard" in state.tasks[0].status_reason
    finalize.assert_called_once_with(task, "partial_failure", state.tasks[0].status_reason)


def test_task_executor_max_token_recovery_exhaustion_is_partial_failure():
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="enumerate paths", phase=1, status="active")],
    )
    executor_calls = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"enumerate paths","tools":[]}'
        pytest.fail(f"unexpected evaluator role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        executor_calls.append(prompt)
        return workflow_mod.TaskExecutorCycleResult(
            text="",
            outcomes=[],
            max_tokens_exhausted=True,
            max_tokens_reason="Task executor repeated the same reasoning loop after its bounded recovery.",
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert len(executor_calls) == 2
    assert state.tasks[0].status == "partial_failure"
    assert "reasoning loop" in state.tasks[0].status_reason
    assert runtime.callback_handler.efficiency_events == [
        ("max_token_exhaustion", "task_executor", "unknown", 1, None),
        ("max_token_exhaustion", "task_executor", "unknown", 1, None),
    ]


def test_reasoning_loop_recovery_keeps_original_incomplete_and_queues_one_replacement():
    task = Task(
        task_uid="active",
        title="Active",
        objective="capture bounded evidence",
        phase=1,
        status="active",
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"capture bounded evidence","tools":[]}'
        pytest.fail(f"unexpected role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        return workflow_mod.TaskExecutorCycleResult(
            text="",
            outcomes=[],
            max_tokens_exhausted=True,
            max_tokens_reason="Task executor repeated the same reasoning loop after its bounded recovery.",
            max_tokens_classification="reasoning_loop",
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    original = next(candidate for candidate in state.tasks if candidate.task_uid == "active")
    replacement = next(candidate for candidate in state.tasks if candidate.replacement_of == "active")
    assert original.status == "partial_failure"
    assert replacement.status == "pending"
    assert replacement.phase == task.phase
    assert replacement.acceptance.criteria == task.acceptance.criteria
    assert "criterion" in replacement.objective
    assert replacement.recovery_context["parent_task_uid"] == "active"
    assert replacement.recovery_context["original_objective"] == task.objective
    assert replacement.recovery_context["unresolved_criteria"]
    assert controller._assessment_is_complete(_plan()) is False


def test_reasoning_loop_replacement_inherits_matching_parent_execution_receipt():
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Request the assigned route",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce request evidence for the assigned route",
            "/api/health",
        )],
    )
    acceptance = AcceptanceContract(
        mode="outcome",
        basis=AcceptanceBasis(
            kind="procedure",
            description="Bounded request",
            source_refs=["target:target-1"],
            procedure={
                "methods": ["request"],
                "limits": {"max_requests": 1},
                "stop_condition": "first_limit_reached",
                "gap_policy": "record_unassessed",
                "output_kind": "artifact",
            },
        ),
        criteria=[criterion],
    )
    task = Task(
        task_uid="active",
        title="Request route",
        objective="Request the assigned route",
        acceptance=acceptance,
        phase=1,
        status="partial_failure",
        recovery_context={
            "execution_evidence_receipts": {
                "criterion-1-execution-1": ["artifact:artifacts/health-response.txt"],
            },
            "controller_execution_evidence": {
                "criterion-1-execution-1": {
                    "artifact_refs": ["artifact:artifacts/health-response.txt"],
                    "subject_ref": "/api/health",
                    "receipt_mode": "capability_matched",
                    "tool_use_ids": ["request-1"],
                    "producers": [{"tool_name": "http_request", "tool_use_id": "request-1"}],
                },
            },
            "execution_receipt_producers": {
                "criterion-1-execution-1": [{"tool_name": "http_request", "tool_use_id": "request-1"}],
            },
        },
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    replacement = controller._create_reasoning_loop_replacement_task(task, [])

    assert replacement is not None
    assert replacement.recovery_context["execution_evidence_receipts"] == {
        "criterion-1-execution-1": ["artifact:artifacts/health-response.txt"],
    }
    assert replacement.recovery_context["controller_execution_evidence"]["criterion-1-execution-1"][
        "receipt_mode"
    ] == "inherited_parent_receipt"
    assert replacement.recovery_context["inherited_execution_receipts"]["criterion-1-execution-1"] == {
        "parent_task_uid": "active",
        "source_task_uid": "active",
        "subject_ref": "/api/health",
    }


def test_reasoning_loop_replacement_rejects_parent_receipt_for_different_subject():
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Request the assigned route",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Produce request evidence for the assigned route",
            "/api/health",
        )],
    )
    acceptance = AcceptanceContract(
        mode="outcome",
        basis=_artifact_acceptance().basis,
        criteria=[criterion],
    )
    task = Task(
        task_uid="active",
        title="Request route",
        objective="Request the assigned route",
        acceptance=acceptance,
        phase=1,
        status="partial_failure",
        recovery_context={
            "execution_evidence_receipts": {
                "criterion-1-execution-1": ["artifact:artifacts/other-response.txt"],
            },
            "controller_execution_evidence": {
                "criterion-1-execution-1": {
                    "artifact_refs": ["artifact:artifacts/other-response.txt"],
                    "subject_ref": "/api/other",
                },
            },
        },
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    replacement = controller._create_reasoning_loop_replacement_task(task, [])

    assert replacement is not None
    assert "execution_evidence_receipts" not in replacement.recovery_context
    assert "inherited_execution_receipts" not in replacement.recovery_context


def test_reasoning_loop_replacement_requires_exactly_one_unresolved_criterion():
    acceptance = AcceptanceContract(
        mode="outcome",
        basis=_artifact_acceptance().basis,
        criteria=(
            AcceptanceCriterion(
                id="criterion-1",
                description="First bounded result",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            ),
            AcceptanceCriterion(
                id="criterion-2",
                description="Second bounded result",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            ),
        ),
    )
    task = Task(
        task_uid="active",
        title="Active",
        objective="capture bounded evidence",
        acceptance=acceptance,
        phase=1,
        status="active",
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    assert controller._create_reasoning_loop_replacement_task(task, []) is None


def test_contradicted_finding_records_assessed_negative_when_contract_allows_it():
    task = Task(
        task_uid="active",
        title="Check route",
        objective="Test the route",
        acceptance=_artifact_acceptance(),
        phase=1,
        status="active",
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    result = controller._record_contradicted_finding_as_negative(
        task,
        ["artifact:artifacts/route-404.txt"],
    )

    assert result["succeeded"] is True
    acceptance = state.acceptance_results[task.task_uid][0]
    assert acceptance.status == "assessed_negative"
    assert acceptance.disposition == "no_vulnerability"


def test_partial_failure_is_superseded_when_split_replacements_resolve_all_criteria():
    parent = Task(
        task_uid="parent",
        title="Combined authentication bypass test",
        objective="Test the security cookie and user token",
        phase=1,
        status="partial_failure",
        status_reason="Initial combined task exhausted its bounded recovery.",
        acceptance=_acceptance("criterion-1"),
    )
    replacements = [
        Task(
            task_uid="cookie-replacement",
            title="Security cookie test",
            objective="Test the security cookie",
            phase=1,
            status="done",
            replacement_of="parent",
            supersedes_criteria=["criterion-1"],
        ),
        Task(
            task_uid="token-replacement",
            title="User token test",
            objective="Test the user token",
            phase=1,
            status="done",
            replacement_of="parent",
            supersedes_criteria=["criterion-1"],
        ),
    ]
    state = FakeState(_plan(), tasks=[parent, *replacements])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    updated_plan = controller._mark_phase(_plan(), 1, "partial_failure")

    updated_parent = next(task for task in state.tasks if task.task_uid == "parent")
    assert updated_parent.status == "superseded"
    assert "cookie-replacement" in updated_parent.status_reason
    assert "token-replacement" in updated_parent.status_reason
    assert "Original reason: Initial combined task exhausted its bounded recovery." in updated_parent.status_reason
    assert updated_plan.phases[0].status == "done"

    supersession_events = [
        event for event in controller.runtime.callback_handler.events if event["type"] == "task_superseded"
    ]
    assert supersession_events == [{
        "type": "task_superseded",
        "task_uid": "parent",
        "replacement_task_uids": ["cookie-replacement", "token-replacement"],
        "phase": 1,
        "reason": updated_parent.status_reason,
        "original_status": "partial_failure",
        "original_status_reason": "Initial combined task exhausted its bounded recovery.",
        "covered_criteria": ["criterion-1"],
    }]


def test_phase_marking_promotes_recovered_task_even_if_evaluator_is_stale():
    plan = _plan()
    parent = Task(
        task_uid="parent",
        title="Combined test",
        objective="Resolve both assigned checks",
        phase=1,
        status="partial_failure",
        status_reason="The combined task failed before completing its contract.",
        acceptance=_acceptance("criterion-1"),
    )
    replacement = Task(
        task_uid="replacement",
        title="Replacement test",
        objective="Resolve the remaining assigned check",
        phase=1,
        status="done",
        replacement_of="parent",
        supersedes_criteria=["criterion-1"],
    )
    state = FakeState(plan, tasks=[parent, replacement])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: '{"status":"partial_failure","reason":"Stale evaluator decision."}',
    )

    decision = controller._evaluate_phase_with_policy(plan, plan.phases[0])
    updated_plan = controller._mark_phase(plan, 1, decision.status)

    assert decision.status == "partial_failure"
    assert next(task for task in state.tasks if task.task_uid == "parent").status == "superseded"
    assert updated_plan.phases[0].status == "done"


def test_successful_replacement_reconciles_parent_before_phase_evaluation():
    parent = Task(
        task_uid="parent",
        title="Original task",
        objective="Complete the assigned check",
        phase=1,
        status="partial_failure",
        status_reason="The original task timed out.",
        acceptance=_acceptance("criterion-1"),
    )
    replacement = Task(
        task_uid="replacement",
        title="Replacement task",
        objective="Complete the assigned check with a focused method",
        phase=1,
        status="active",
        replacement_of="parent",
        supersedes_criteria=["criterion-1"],
    )
    state = FakeState(_plan(), tasks=[parent, replacement])

    def text_runner(role, *_args):
        if role == "task_prompt_builder":
            return '{"prompt":"complete the focused check","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"Focused replacement completed its criterion."}'
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda *_args: "replacement evidence",
    )

    controller._run_task(_plan(), _plan().phases[0], replacement)

    assert next(task for task in state.tasks if task.task_uid == "replacement").status == "done"
    assert next(task for task in state.tasks if task.task_uid == "parent").status == "superseded"
    assert any(event["type"] == "task_superseded" for event in controller.runtime.callback_handler.events)


def test_persisted_replacement_lineage_reconciles_after_sqlite_reload(tmp_path):
    store = SQLiteApplicationStore(str(tmp_path / "workflow.db"), "target")
    parent = Task(
        task_uid="parent",
        title="Original task",
        objective="Complete the assigned check",
        phase=1,
        status="partial_failure",
        acceptance=_acceptance("criterion-1"),
    )
    replacement = Task(
        task_uid="replacement",
        title="Replacement task",
        objective="Complete the unresolved check",
        phase=1,
        status="done",
        acceptance=_acceptance("criterion-1"),
        replacement_of="parent",
        supersedes_criteria=["criterion-1"],
    )
    store.store_task("operation", parent)
    store.store_task("operation", replacement)

    reloaded_tasks = SQLiteApplicationStore(str(tmp_path / "workflow.db"), "target").get_tasks("operation")
    state = FakeState(_plan(), tasks=reloaded_tasks)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    controller._reconcile_superseded_tasks(1)

    assert next(task for task in state.tasks if task.task_uid == "parent").status == "superseded"
    assert any(event["type"] == "task_superseded" for event in controller.runtime.callback_handler.events)


@pytest.mark.parametrize(
    "replacement_status",
    ["active", "pending", "partial_failure", "blocked"],
)
def test_partial_failure_remains_blocking_until_all_replacements_succeed(replacement_status):
    parent = Task(
        task_uid="parent",
        title="Combined test",
        objective="Resolve the test intent",
        phase=1,
        status="partial_failure",
        acceptance=_acceptance("criterion-1"),
    )
    replacement = Task(
        task_uid="replacement",
        title="Replacement test",
        objective="Resolve the remaining criterion",
        phase=1,
        status=replacement_status,
        replacement_of="parent",
        supersedes_criteria=["criterion-1"],
    )
    state = FakeState(_plan(), tasks=[parent, replacement], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    controller._reconcile_superseded_tasks(1)

    assert next(task for task in state.tasks if task.task_uid == "parent").status == "partial_failure"


def test_partial_failure_remains_blocking_when_replacement_omits_parent_criterion():
    parent = Task(
        task_uid="parent",
        title="Combined test",
        objective="Resolve the test intent",
        phase=1,
        status="partial_failure",
        acceptance=_acceptance("criterion-1"),
    )
    replacement = Task(
        task_uid="replacement",
        title="Unrelated replacement",
        objective="Resolve another criterion",
        phase=1,
        status="done",
        replacement_of="parent",
        supersedes_criteria=["other-criterion"],
    )
    state = FakeState(_plan(), tasks=[parent, replacement], acceptance_complete=False)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    controller._reconcile_superseded_tasks(1)

    assert next(task for task in state.tasks if task.task_uid == "parent").status == "partial_failure"


def test_non_loop_max_token_exhaustion_remains_partial_failure():
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="enumerate paths", phase=1, status="active")],
    )

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"enumerate paths","tools":[]}'
        pytest.fail(f"unexpected role: {role}")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda *args: workflow_mod.TaskExecutorCycleResult(
            text="",
            outcomes=[],
            max_tokens_exhausted=True,
            max_tokens_reason="Task executor reached its output-token limit after bounded recovery.",
            max_tokens_classification="output_truncation",
        ),
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert state.tasks[0].status == "partial_failure"
    assert not any(candidate.replacement_of == "active" for candidate in state.tasks)


def test_max_token_recovery_uses_synthesis_when_durable_evidence_is_ready():
    state = FakeState(
        _plan(),
        tasks=[Task(
            task_uid="active",
            title="Summarize response",
            objective="Assess response",
            phase=1,
            status="active",
            acceptance=AcceptanceContract(
                mode="coverage",
                basis=AcceptanceBasis(
                    kind="snapshot",
                    description="Assess response",
                    source_refs=["plan:phase-1"],
                    snapshot_hash="response-snapshot",
                    item_ids=["response"],
                ),
                criteria=[AcceptanceCriterion(
                    id="criterion:active",
                    description="Record the response assessment",
                    evidence_requirements=[EvidenceRequirement(kind="memory")],
                )],
            ),
        )],
    )
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"assess response","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"The retained evidence supports the terminal assessment."}'
        pytest.fail(f"unexpected role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        calls.append((prompt, [workflow_mod.get_tool_name(tool) for tool in tools], run_policy))
        if len(calls) == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="",
                outcomes=[ToolOutcome(
                    sequence=1,
                    tool_use_id="response-observation",
                    tool_name="store_observation",
                    success=True,
                    correctable=False,
                    input_summary="store response assessment",
                    output_summary='{"memory_ref":"memory:response"}',
                )],
                max_tokens_exhausted=True,
                max_tokens_classification="output_truncation",
            )
        state.acceptance_results["active"] = [AcceptanceResult(
            criterion_id=state.tasks[0].acceptance.criteria[0].id,
            status="satisfied",
            disposition="observation",
            summary="Terminal response assessment",
            evidence_refs=("memory:response",),
        )]
        return workflow_mod.TaskExecutorCycleResult(
            text="The retained response evidence supports the terminal assessment.",
            outcomes=[ToolOutcome(
                sequence=2,
                tool_use_id="acceptance",
                tool_name="record_task_acceptance",
                success=True,
                correctable=False,
                input_summary="accept response",
                output_summary='{"complete":true}',
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert len(calls) == 2
    assert "## Max-Token Terminal Synthesis Recovery" in calls[1][0]
    assert calls[1][2].required_tool_names == {"record_task_acceptance"}
    recovery_events = [
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "task_max_token_recovery"
        and event.get("retry_mode") == "synthesis"
    ]
    assert [event["decision"] for event in recovery_events] == [
        "bounded_synthesis_retry",
        "completed_synthesis_turn",
    ]
    assert state.tasks[0].status == "done"


def test_output_truncation_recovery_rejects_closure_without_required_terminal_state():
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="enumerate paths", phase=1, status="active")],
    )
    calls = []
    prior_outcome = ToolOutcome(
        sequence=1,
        tool_use_id="first-observation",
        tool_name="store_observation",
        success=True,
        correctable=False,
        input_summary="observed route response",
        output_summary='{"memory_ref":"memory:current-observation"}',
    )
    repeated_outcome = ToolOutcome(
        sequence=2,
        tool_use_id="second-observation",
        tool_name="store_observation",
        success=True,
        correctable=False,
        input_summary="observed route response",
        output_summary='{"memory_ref":"memory:current-observation"}',
    )

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"enumerate paths","tools":[]}'
        pytest.fail(f"unexpected role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        calls.append((prompt, [workflow_mod.get_tool_name(tool) for tool in tools], run_policy))
        if len(calls) == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="",
                outcomes=[prior_outcome],
                max_tokens_exhausted=True,
                max_tokens_classification="output_truncation",
            )
        return workflow_mod.TaskExecutorCycleResult(text="", outcomes=[repeated_outcome])

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert len(calls) == 2
    recovery_prompt, recovery_tools, recovery_policy = calls[1]
    assert "## Compact Output-Truncation Recovery" in recovery_prompt
    assert "memory:current-observation" in recovery_prompt
    assert "observed route response" not in recovery_prompt
    assert recovery_policy.max_tool_calls == 1
    assert "record_task_acceptance" not in recovery_tools
    assert state.tasks[0].status == "partial_failure"
    recovery_event = next(
        event
        for event in controller.runtime.callback_handler.events
        if event["type"] == "task_max_token_recovery"
        and event["decision"] == "no_new_durable_state"
    )
    assert recovery_event["retry_mode"] == "evidence"


def test_output_truncation_recovery_prompt_limits_closure_to_required_tool():
    task = Task(task_uid="active", title="Active", objective="close task", phase=1, status="active")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
    )

    prompt = controller._output_truncation_recovery_prompt(
        _plan(),
        task,
        [{"reference": "memory:observation_current", "source": "tool_outcome"}],
        [],
        "closure",
        ["record_task_acceptance"],
    )

    assert "Allowed tools: record_task_acceptance" in prompt
    assert "Call exactly one allowed closure tool" in prompt
    assert "memory:observation_current" in prompt


def test_output_truncation_recovery_uses_synthesis_after_acceptance_correction():
    task = Task(
        task_uid="active",
        title="Active",
        objective="close task",
        acceptance=AcceptanceContract(
            mode="coverage",
            basis=AcceptanceBasis(
                kind="snapshot",
                description="Close task",
                source_refs=["memory:seed"],
                item_ids=["item-1"],
            ),
            criteria=[AcceptanceCriterion(
                id="close",
                description="Store closure observation",
                evidence_requirements=[EvidenceRequirement(kind="observation")],
            )],
        ),
        phase=1,
        status="active",
    )
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    calls = []
    rejected_acceptance = ToolOutcome(
        sequence=1,
        tool_use_id="rejected-acceptance",
        tool_name="record_task_acceptance",
        success=False,
        correctable=True,
        input_summary=(
            '{"status":"satisfied","disposition":"observation",'
            '"evidence_refs":["memory:existing-observation"]}'
        ),
        output_summary="Acceptance evidence reference is invalid",
    )

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"close task","tools":[]}'
        pytest.fail(f"unexpected role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        calls.append((prompt, [workflow_mod.get_tool_name(tool) for tool in tools], run_policy))
        if len(calls) == 1:
            return workflow_mod.TaskExecutorCycleResult(text="", outcomes=[
                ToolOutcome(
                    sequence=0,
                    tool_use_id="stored-observation",
                    tool_name="store_observation",
                    success=True,
                    correctable=False,
                    input_summary="observation",
                    output_summary='{"memory_ref":"memory:existing-observation"}',
                ),
                rejected_acceptance,
            ])
        return workflow_mod.TaskExecutorCycleResult(
            text="",
            outcomes=[],
            max_tokens_exhausted=True,
            max_tokens_classification="output_truncation",
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(
            env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1, "CYBER_TASK_ACCEPTANCE_MAX_CORRECTIONS": 1}
        ),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(calls) == 3
    recovery_prompt, recovery_tools, recovery_policy = calls[2]
    assert "## Max-Token Terminal Synthesis Recovery" in recovery_prompt
    assert "Do not perform discovery, scanning, new testing" in recovery_prompt
    assert "record_task_acceptance" in recovery_tools
    assert recovery_policy.required_tool_names == {"record_task_acceptance"}
    assert recovery_policy.max_tool_calls == 2
    recovery_events = [
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "task_max_token_recovery"
        and event.get("retry_mode") == "synthesis"
    ]
    assert recovery_events[0]["decision"] == "bounded_synthesis_retry"
    assert all(event["decision"] == "bounded_synthesis_retry" for event in recovery_events)


def test_task_evaluator_prompt_omits_empty_worker_context_and_truncates_long_context():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assert "Task worker final context" not in controller._task_evaluator_prompt(_plan(), _plan().phases[0], task)

    long_result = "prefix " + ("x" * (workflow_mod.WORKER_CONTEXT_LIMIT + 20)) + " suffix"
    summary = controller._worker_context_summary(long_result)
    prompt = controller._task_evaluator_prompt(_plan(), _plan().phases[0], task, summary)

    assert len(summary) == workflow_mod.WORKER_CONTEXT_LIMIT
    assert "prefix" not in summary
    assert summary.endswith(" suffix")
    assert "## Task worker final context" in prompt
    assert summary in prompt


def test_evaluator_prompts_treat_artifact_backed_negative_results_as_assessed():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(
        task_uid="task",
        title="Map route",
        objective="Determine whether /missing exists",
        phase=1,
        status="pending",
    )

    task_prompt = controller._task_evaluator_prompt(_plan(), _plan().phases[0], task)
    phase_prompt = controller._phase_evaluator_prompt(_plan(), _plan().phases[0])

    for prompt in (task_prompt, phase_prompt):
        assert "artifact-backed" in prompt
        assert "captured" in prompt
        assert "404" in prompt
        assert "Bare `curl -s`" in prompt
        assert "confirmed absent or inaccessible" in prompt
        assert "not assessed" in prompt


def test_controller_closes_phase_but_preserves_pending_task_when_hard_budget_cap_reached():
    calls = []
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="pending", title="Pending", objective="run pending", phase=1, status="pending")],
    )

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        assert role == "phase_evaluator"
        assert "mandatory budget cap" in prompt
        assert "continue|done" not in prompt
        return '{"status":"partial_failure","reason":"hard budget cap reached"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(progress=100),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt: None,
        max_iterations=1,
    )

    controller.run()

    assert calls == []
    assert state.plan.assessment_complete is True
    assert state.plan.phases[0].status == "partial_failure"
    assert state.tasks[0].status == "pending"
    assert not any(event["type"] in ("task_done", "task_deferred") for event in controller.runtime.callback_handler.events)
    assert controller.runtime.callback_handler.termination_events[0][0] == "partial_failure"
    coverage = next(
        event
        for event in controller.runtime.callback_handler.events
        if event["type"] == "workflow_coverage_summary"
    )
    assert coverage["assessment_complete"] is False
    assert coverage["actionable_task_count"] == 1
    assert coverage["incomplete_phase_ids"] == [1]


def test_phase_hard_cap_defers_active_task_without_running_worker_and_advances():
    calls = []
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=3,
        phases=[
            PlanPhase(id=1, title="Recon", status="active"),
            PlanPhase(id=2, title="Validate", status="pending"),
            PlanPhase(id=3, title="Exploit", status="pending"),
        ],
    )
    active_task = Task(task_uid="active", title="Active", objective="run", phase=1, status="active")
    pending_task = Task(task_uid="pending", title="Pending", objective="wait", phase=1, status="pending")
    state = AdvancingFakeState(plan, tasks=[active_task, pending_task])

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        assert role == "phase_evaluator"
        assert '"done|partial_failure|blocked"' in prompt
        return '{"status":"done","reason":"enough evidence"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(progress=34),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda *args: pytest.fail("capped task work must not run"),
        max_iterations=1,
    )

    with pytest.raises(WorkflowInvariantError, match="Workflow iteration limit reached"):
        controller.run()

    assert calls == []
    assert state.plan.phases[0].status == "partial_failure"
    assert state.plan.phases[1].status == "active"
    assert next(task for task in state.tasks if task.task_uid == "active").status == "pending"
    assert next(task for task in state.tasks if task.task_uid == "pending").status == "pending"
    assert controller.runtime.callback_handler.events[0] == {
        "type": "task_deferred",
        "task_uid": "active",
        "title": "Active",
        "status": "pending",
        "status_reason": next(task for task in state.tasks if task.task_uid == "active").status_reason,
    }
    plan_event = next(
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "output" and event.get("metadata", {}).get("kind") == "plan"
    )
    assert "[partial_failure] 1. Recon" in plan_event["content"]
    assert "[active] 2. Validate" in plan_event["content"]
    assert plan_event["metadata"] == {
        "source": "workflow",
        "kind": "plan",
        "action": "updated",
        "current_phase": 2,
        "total_phases": 3,
        "assessment_complete": False,
    }


def test_phase_hard_cap_defers_finding_validation_without_resolving_it(monkeypatch):
    task = Task(
        task_uid="validation",
        title="Verify finding",
        objective="validate",
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )
    state = FakeState(_plan(), tasks=[task])
    monkeypatch.setattr(
        workflow_mod,
        "finalize_finding_validation",
        lambda *args: pytest.fail("deferred validation must not be finalized"),
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(progress=100),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: '{"status":"partial_failure","reason":"phase capped"}',
        work_runner=lambda *args: pytest.fail("capped task work must not run"),
        max_iterations=1,
    )

    controller.run()

    assert state.tasks[0].status == "pending"
    assert controller.runtime.callback_handler.events[0]["type"] == "task_deferred"
    assert controller.runtime.callback_handler.events[0]["task_kind"] == "finding_validation"
    assert controller.runtime.callback_handler.events[0]["reference_id"] == "finding-1"


def test_phase_hard_cap_uses_durable_state_without_evaluator():
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="pending", title="Pending", objective="run", phase=1, status="pending")],
    )
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        return '{"status":"done","reason":"unexpected evaluator call"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(progress=100, env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda *args: pytest.fail("capped task work must not run"),
        max_iterations=1,
    )

    controller.run()

    assert calls == []
    assert state.plan.phases[0].status == "partial_failure"
    assert state.tasks[0].status == "pending"
    classification = next(
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "phase_durable_state_classification"
    )
    assert classification["status"] == "partial_failure"


@pytest.mark.parametrize("response", ["not json", '{"status":"invented","reason":"bad status"}'])
def test_phase_evaluator_malformed_output_emits_deterministic_partial_failure_fallback(response):
    plan = _plan()
    state = FakeState(plan)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: response,
    )

    decision = controller._evaluate_phase_with_policy(plan, plan.phases[0])

    assert decision.status == "partial_failure"
    event = next(event for event in controller.runtime.callback_handler.events if event["type"] == "evaluator_fallback")
    assert "deterministic durable-state" in decision.reason
    assert event["source"] == "deterministic_phase_state"
    assert event["error_type"] == "WorkflowInvariantError"
    assert event["phase_id"] == 1
    assert event["status"] == "partial_failure"
    assert len(event["error_fingerprint"]) == 64


def test_phase_evaluator_recovers_explicit_blocked_prose_after_json_retries():
    plan = _plan()
    state = FakeState(plan)
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        return "Status Assessment: BLOCKED. No viable path remains within the assigned scope."

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    decision = controller._evaluate_phase_with_policy(plan, plan.phases[0])

    assert decision.status == "blocked"
    assert len(calls) == 2
    event = next(event for event in controller.runtime.callback_handler.events if event["type"] == "evaluator_fallback")
    assert event["role"] == "phase_evaluator"
    assert event["phase_id"] == 1
    assert event["status"] == "blocked"
    assert event["source"] == "prose_conclusion"


def test_task_evaluator_recovers_explicit_failed_prose_after_json_retries():
    plan = _plan()
    task = Task(task_uid="task", title="Task", objective="run", phase=1, status="active")
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: "Status: FAILED. The required evidence could not be produced.",
    )

    decision = controller._evaluate_task(plan, plan.phases[0], task)

    assert decision.status == "partial_failure"
    event = next(event for event in controller.runtime.callback_handler.events if event["type"] == "evaluator_fallback")
    assert event["role"] == "task_evaluator"
    assert event["task_uid"] == "task"
    assert event["status"] == "partial_failure"


def test_evaluator_prose_fallback_rejects_ambiguous_and_success_prose():
    assert MultiAgentWorkflowController._evaluator_prose_fallback(
        "The result is not blocked and the work completed successfully."
    ) is None
    assert MultiAgentWorkflowController._evaluator_prose_fallback(
        "The evidence suggests there is no viable path, but no status was assigned."
    ) is None


def test_repaired_evaluator_json_does_not_use_prose_fallback():
    plan = _plan()
    state = FakeState(plan)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: '{"status":"blocked","reason":"closed",}',
    )

    decision = controller._evaluate_phase_with_policy(plan, plan.phases[0])

    assert decision.status == "blocked"
    assert not any(event["type"] == "evaluator_fallback" for event in controller.runtime.callback_handler.events)


def test_task_creator_prior_phase_context_exposes_terminal_results_without_full_contracts():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Attack Surface Mapping", status="done"),
            PlanPhase(id=2, title="Exploit Chain Analysis", status="active"),
        ],
    )
    prior = Task(
        task_uid="prior",
        title="Map login route",
        objective="Enumerate the route",
        phase=1,
        status="partial_failure",
        status_reason="POST /login requires a CSRF token",
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[prior]),
    )

    context = controller._task_creator_compact_existing_task_context(plan.phases[1])

    assert "prior_phase_terminal_tasks[1]" in context
    assert "Map login route" in context
    assert "POST /login requires a CSRF token" in context
    assert "acceptance" not in context


def test_acceptance_recovery_details_classify_evidence_and_artifact_repair():
    digest = "a" * 64

    details = MultiAgentWorkflowController._acceptance_recovery_details(
        f"inventory manifest requires 2 artifact evidence artifact_sha256={digest}"
    )

    assert details == {
        "code": "invalid_inventory_manifest",
        "changed_submission_required": True,
        "required_evidence": [{"kind": "artifact", "min_count": 2}],
        "artifact_sha256": digest,
    }


def test_acceptance_recovery_details_default_to_schema_correction():
    details = MultiAgentWorkflowController._acceptance_recovery_details("unknown enum value")

    assert details["code"] == "invalid_payload"
    assert details["required_evidence"] == []
    assert details["artifact_sha256"] is None


def test_acceptance_recovery_details_classifies_missing_artifact_as_prerequisite():
    details = MultiAgentWorkflowController._acceptance_recovery_details(
        "Artifact does not exist: artifact:artifacts/validation.txt"
    )

    assert details["code"] == "missing_artifact_prerequisite"


def test_acceptance_recovery_details_classifies_missing_execution_as_prerequisite():
    details = MultiAgentWorkflowController._acceptance_recovery_details(
        "Execution evidence is required before acceptance. Missing execution requirements: execution-1"
    )

    assert details["code"] == "missing_execution_prerequisite"


def test_advisory_checkpoint_can_continue_below_phase_hard_cap():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Recon", status="active"),
            PlanPhase(id=2, title="Validate", status="pending"),
        ],
    )
    state = FakeState(
        plan,
        tasks=[Task(task_uid="pending", title="Pending", objective="run", phase=1, status="pending")],
    )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(progress=20),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: '{"status":"continue","reason":"useful work remains"}',
        work_runner=lambda *args: pytest.fail("task activation does not run a worker"),
        max_iterations=1,
    )

    with pytest.raises(WorkflowInvariantError, match="Workflow iteration limit reached"):
        controller.run()

    assert state.tasks[0].status == "active"
    assert 20 in controller._crossed_checkpoints


def test_global_budget_limit_still_preempts_phase_hard_cap_evaluation():
    runtime = _runtime(progress=100)
    runtime.callback_handler.has_reached_limit = lambda: True
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda *args: pytest.fail("evaluator must not run after global budget exhaustion"),
        work_runner=lambda *args: pytest.fail("worker must not run after global budget exhaustion"),
    )

    with pytest.raises(BudgetLimitReached, match="Budget limit reached"):
        controller.run()


def test_controller_defers_phase_evaluation_at_python_checkpoint_when_tasks_are_pending():
    calls = []
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=3,
        phases=[
            PlanPhase(id=1, title="Recon", status="active"),
            PlanPhase(id=2, title="Validate", status="pending"),
            PlanPhase(id=3, title="Report Prep", status="pending"),
        ],
    )
    state = FakeState(
        plan,
        tasks=[Task(task_uid="pending", title="Pending", objective="run pending", phase=1, status="pending")],
    )

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        assert role == "phase_evaluator"
        return '{"status":"done","reason":"checkpoint reached"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(progress=20),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt: None,
        max_iterations=1,
    )

    should_evaluate = controller._should_evaluate_phase(plan.phases[0])

    assert should_evaluate is False
    assert calls == []
    assert state.tasks[0].status == "pending"
    assert 20 in controller._crossed_checkpoints


def test_checkpoint_bands_are_consumed_once():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(progress=45),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assert controller._consume_crossed_checkpoint() == 40
    assert controller._consume_crossed_checkpoint() is None


def test_plan_creator_prompt_requests_inferred_operation_constraints():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    prompt = controller._plan_creator_prompt()

    assert "Infer a concise list of unique, operation-wide constraints" in prompt
    assert '"constraints": [string]' in prompt
    assert "scope, safety, operational-boundary, evidence, and validation constraints" in prompt
    assert "Do not treat phase goals, tool preferences, or generic advice as constraints" in prompt
    assert "findings-consolidation" in prompt
    assert "coverage-closure" in prompt
    assert "Documenting unassessed work is an output" in prompt
    assert "Use bounded criteria" in prompt
    assert "semantically distinct objective" in prompt
    assert "one dominant outcome" in prompt
    assert "industry terminology" in prompt
    assert "separates hypothesis generation" in prompt
    assert "models exploit chains" in prompt
    assert "Concise means avoiding" in prompt
    assert '"task_creation_mode": "standard|snapshot_dependent|finding_dependent|finding_validation"' in prompt
    assert "not minimizing the number of phases" in prompt
    assert "recommended minimum phase contract" in prompt
    assert "advisory, not a" in prompt
    assert "required phase count" in prompt
    assert "Merge adjacent recommendations only" in prompt
    assert "Omit a recommendation only when it is" in prompt
    assert "demonstrably inapplicable" in prompt


def test_plan_critic_rejects_post_processing_phases_regardless_of_title():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    prompt = controller._plan_critic_prompt(
        {
            "objective": "assess",
            "constraints": [],
            "current_phase": 1,
            "phases": [
                {"id": 1, "title": "Wrap up evidence", "status": "pending", "criteria": "Summarize findings"}
            ],
        }
    )

    assert "findings-consolidation" in prompt
    assert "equivalent post-processing phase, regardless of its title" in prompt
    assert "evidence-reconciliation" in prompt
    assert "never treats a later reconciliation phase" in prompt
    assert "superficial rewording" in prompt
    assert "recommended minimum phase contract" in prompt
    assert "mandatory" in prompt
    assert "merged criteria preserve every included capability" in prompt
    assert "operational coverage phase is valid" in prompt


def test_plan_critic_calibrates_semantic_equivalence_and_task_boundaries():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    prompt = controller._plan_critic_prompt(
        {"objective": "assess", "constraints": [], "current_phase": 1, "phases": []},
        ["Phase 1 must freeze the inventory"],
    )

    assert "Treat semantically equivalent criteria as satisfied" in prompt
    assert '"produces and freezes the canonical inventory manifest"' in prompt
    assert "Do not reject it for missing task-level fan-out details" in prompt
    assert "Prior critic feedback" in prompt
    assert "Phase 1 must freeze the inventory" in prompt
    assert "do not repeat it merely because the wording changed" in prompt


def test_module_termination_policy_directs_plan_creation_and_review():
    runtime = _runtime()
    runtime.termination_policy = "Require an evidenced flag and verified cleanup."
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    plan_data = {
        "objective": "assess",
        "constraints": [],
        "current_phase": 1,
        "phases": [{"id": 1, "title": "Assess", "status": "pending", "criteria": "evidence"}],
    }

    prompts = [
        controller._plan_creator_prompt(),
        controller._plan_critic_prompt(plan_data),
        controller._plan_revision_prompt(plan_data, ["Add completion evidence"]),
    ]

    for prompt in prompts:
        assert "## Module Completion Policy" in prompt
        assert "Require an evidenced flag and verified cleanup." in prompt
        assert "measurable" in prompt


def test_planning_prompts_omit_empty_module_termination_policy_section():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    plan_data = {
        "objective": "assess",
        "constraints": [],
        "current_phase": 1,
        "phases": [{"id": 1, "title": "Assess", "status": "pending", "criteria": "evidence"}],
    }

    assert "Module Completion Policy" not in controller._plan_creator_prompt()
    assert "Module Completion Policy" not in controller._plan_critic_prompt(plan_data)
    assert "Module Completion Policy" not in controller._plan_revision_prompt(plan_data, ["revise"])


def test_plan_revision_prompt_describes_advisory_phase_contract():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    plan_data = {
        "objective": "assess",
        "constraints": [],
        "current_phase": 1,
        "phases": [{"id": 1, "title": "Assess", "status": "pending", "criteria": "evidence"}],
    }

    prompt = controller._plan_revision_prompt(plan_data, ["Preserve testing coverage"])

    assert "Address each item in the critic feedback directly" in prompt
    assert "ensuring all cited ambiguities, circularity, missing manifest synthesis responsibilities" in prompt
    assert "recommended minimum phase contract" in prompt
    assert "advisory rather than a required phase count" in prompt
    assert "merge only adjacent capabilities" in prompt
    assert "document any omitted inapplicable capability" in prompt
    assert "one dominant outcome" in prompt


def test_plan_revision_prompt_keeps_complete_creator_instructions_ahead_of_feedback():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    plan_data = {
        "objective": "assess",
        "constraints": [],
        "current_phase": 1,
        "phases": [{"id": 1, "title": "Assess", "status": "pending", "criteria": "evidence"}],
    }

    prompt = controller._plan_revision_prompt(plan_data, ["Add a report phase"])

    assert controller._plan_creator_prompt() in prompt
    assert "critic feedback is additive corrective input" in prompt
    assert "If feedback conflicts with those instructions, follow the canonical instructions." in prompt
    assert "Preserve criteria that already address prior feedback" in prompt
    assert "Do not include a report" in prompt


def test_plan_refinement_defaults_to_two_and_negative_values_disable_it():
    default_controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    disabled_controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_PLAN_REFINEMENT_ITERATIONS": -2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assert default_controller.plan_refinement_iterations == 7
    assert disabled_controller.plan_refinement_iterations == 0


def test_task_prompt_refinement_defaults_to_one_and_negative_values_disable_it():
    default_runtime = _runtime()
    default_runtime.config_manager = None
    default_controller = MultiAgentWorkflowController(
        runtime=default_runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    disabled_controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": -2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assert default_controller.task_prompt_refinement_iterations == 1
    assert disabled_controller.task_prompt_refinement_iterations == 0


def test_zero_plan_refinement_iterations_runs_only_initial_actor():
    calls = []
    state = FakeState(None)

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        return '{"objective":"single pass","constraints":[],"current_phase":1,"phases":[{"id":1,"title":"Draft","status":"pending"}]}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_PLAN_REFINEMENT_ITERATIONS": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    assert controller._ensure_plan().objective == "single pass"
    assert calls == ["plan_creator"]


def test_plan_critic_approval_skips_revision_and_persists_draft_once():
    calls = []
    state = FakeState(None)

    def text_runner(role, prompt, tools, system_prompt):
        calls.append((role, prompt, tools, system_prompt))
        if role == "plan_creator":
            return '{"objective":"draft","constraints":["Stay in scope"],"current_phase":1,"phases":[{"id":1,"title":"Recon","status":"pending","criteria":"Map scope"}]}'
        if role == "plan_critic":
            return '{"approved":true,"feedback":[]}'
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    plan = controller._ensure_plan()

    assert [call[0] for call in calls] == ["plan_creator", "plan_critic"]
    assert all(call[2] == [] for call in calls)
    assert all(call[3] == "base prompt" for call in calls)
    assert plan.objective == "draft"
    plan_events = [
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "output" and event.get("metadata", {}).get("kind") == "plan"
    ]
    assert len(plan_events) == 1
    assert "Proposed plan draft" in calls[1][1]
    assert '"title": "Recon"' in calls[1][1]
    validation_events = [
        event for event in controller.runtime.callback_handler.events if event["type"] == "plan_validation"
    ]
    assert [(event["stage"], event["outcome"]) for event in validation_events] == [
        ("draft", "created"),
        ("critic", "approved"),
    ]
    assert "approved" not in validation_events[0]
    assert validation_events[1]["approved"] is True
    assert validation_events[1]["feedback_summaries"] == []


def test_plan_critic_rejection_runs_revision_and_persists_only_revision():
    calls = []
    state = FakeState(None)
    actor_responses = iter(
        [
            '{"objective":"draft","constraints":[],"current_phase":1,"phases":[{"id":1,"title":"Recon","status":"pending","criteria":"Map"}]}',
            '{"objective":"revised","constraints":["Store evidence"],"current_phase":1,"phases":[{"id":1,"title":"Evidence-backed recon","status":"pending","criteria":"Artifact exists"}]}',
        ]
    )
    critic_responses = iter(
        [
            '{"approved":false,"feedback":["Add durable evidence criteria"]}',
            '{"approved":true,"feedback":[]}',
        ]
    )

    def text_runner(role, prompt, tools, system_prompt):
        calls.append((role, prompt))
        if role == "plan_creator":
            return next(actor_responses)
        if role == "plan_critic":
            return next(critic_responses)
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_PLAN_REFINEMENT_ITERATIONS": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    plan = controller._ensure_plan()

    assert [role for role, _prompt in calls] == ["plan_creator", "plan_critic", "plan_creator", "plan_critic"]
    assert plan.objective == "revised"
    assert plan.phases[0].title == "Evidence-backed recon"
    plan_events = [
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "output" and event.get("metadata", {}).get("kind") == "plan"
    ]
    assert len(plan_events) == 1
    assert "Add durable evidence criteria" in calls[2][1]
    assert "Address each item in the critic feedback directly" in calls[2][1]
    assert "Prior critic feedback" in calls[3][1]
    assert "Add durable evidence criteria" in calls[3][1]
    activities = [
        event for event in controller.runtime.callback_handler.events
        if event.get("type") == "workflow_activity"
    ]
    assert [(event["role"], event["cycle"], event["cycle_total"]) for event in activities] == [
        ("plan_creator", 1, 2),
        ("plan_creator", 1, 2),
        ("plan_critic", 1, 2),
        ("plan_critic", 1, 2),
        ("plan_creator", 2, 2),
        ("plan_creator", 2, 2),
        ("plan_critic", 2, 2),
        ("plan_critic", 2, 2),
    ]
    validation_events = [
        event for event in controller.runtime.callback_handler.events if event["type"] == "plan_validation"
    ]
    assert [(event["cycle"], event["stage"], event["outcome"]) for event in validation_events] == [
        (1, "draft", "created"),
        (1, "critic", "revision_requested"),
        (2, "draft", "created"),
        (2, "critic", "approved"),
    ]
    assert validation_events[1]["approved"] is False
    assert validation_events[2]["feedback_summaries"] == ["Add durable evidence criteria"]


def test_plan_validation_span_and_event_exclude_prompt_and_draft_content(monkeypatch):
    span = Mock()
    tracer = Mock()

    @contextmanager
    def span_context(*_args, **_kwargs):
        yield span

    tracer.start_as_current_span.side_effect = span_context
    monkeypatch.setattr(workflow_mod.otel_trace, "get_tracer", lambda _name: tracer)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=lambda *args: "{}",
    )

    controller._emit_plan_validation(
        cycle=1,
        cycle_total=2,
        stage="critic",
        outcome="revision_requested",
        approved=False,
        feedback=["Keep this concise\nwithout exposing a full prompt." * 20],
    )

    event = controller.runtime.callback_handler.events[-1]
    attributes = tracer.start_as_current_span.call_args.kwargs["attributes"]
    assert tracer.start_as_current_span.call_args.args[0] == "plan_validation"
    assert event["type"] == "plan_validation"
    assert event["approved"] is False
    assert len(event["feedback_summaries"]) == 1
    assert len(event["feedback_summaries"][0]) <= workflow_mod.PLAN_VALIDATION_MAX_FEEDBACK_CHARS + 3
    assert "Proposed plan draft" not in json.dumps(event)
    assert "Proposed plan draft" not in json.dumps(attributes)


def test_plan_refinement_stops_after_later_approval():
    calls = []
    actor_responses = iter(
        [
            '{"objective":"draft","constraints":[],"current_phase":1,"phases":[{"id":1,"title":"Draft","status":"pending"}]}',
            '{"objective":"revised","constraints":[],"current_phase":1,"phases":[{"id":1,"title":"Revised","status":"pending"}]}',
        ]
    )
    critic_responses = iter(
        [
            '{"approved":false,"feedback":["Improve phase"]}',
            '{"approved":true,"feedback":[]}',
        ]
    )

    def text_runner(role, prompt, tools, system_prompt):
        calls.append((role, prompt))
        return next(actor_responses) if role == "plan_creator" else next(critic_responses)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_PLAN_REFINEMENT_ITERATIONS": 3}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(None),
        text_runner=text_runner,
    )

    plan = controller._ensure_plan()

    assert [role for role, _prompt in calls] == ["plan_creator", "plan_critic", "plan_creator", "plan_critic"]
    assert plan.objective == "revised"
    assert '"title": "Revised"' in calls[-1][1]


def test_plan_refinement_fails_when_final_critic_rejects():
    calls = []
    actor_responses = iter(
        [
            '{"objective":"draft","constraints":[],"current_phase":1,"phases":[{"id":1,"title":"Draft","status":"pending"}]}',
            '{"objective":"revision one","constraints":[],"current_phase":1,"phases":[{"id":1,"title":"Revision one","status":"pending"}]}',
        ]
    )
    state = FakeState(None)

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        if role == "plan_creator":
            return next(actor_responses)
        return '{"approved":false,"feedback":["Revise again"]}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_PLAN_REFINEMENT_ITERATIONS": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    with pytest.raises(WorkflowInvariantError, match="Plan critic rejected the plan after 2 review"):
        controller._ensure_plan()

    assert calls == ["plan_creator", "plan_critic", "plan_creator", "plan_critic"]
    assert state.plan is None
    assert not any(
        event["type"] == "output" and event.get("metadata", {}).get("kind") == "plan"
        for event in controller.runtime.callback_handler.events
    )


@pytest.mark.parametrize(
    "critique",
    [
        '{"approved":"yes","feedback":[]}',
        '{"approved":false,"feedback":[]}',
        '{"approved":true,"feedback":"none"}',
    ],
)
def test_invalid_plan_critic_contract_retries_without_persisting(critique):
    calls = []
    state = FakeState(None)

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        if role == "plan_creator":
            return '{"objective":"draft","constraints":[],"current_phase":1,"phases":[{"id":1,"title":"Draft","status":"pending"}]}'
        return critique

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    with pytest.raises(WorkflowInvariantError, match="plan_critic returned invalid JSON"):
        controller._ensure_plan()

    assert calls == ["plan_creator", "plan_critic", "plan_critic"]
    assert state.plan is None
    assert not any(
        event["type"] == "output" and event.get("metadata", {}).get("kind") == "plan"
        for event in controller.runtime.callback_handler.events
    )
    validation_events = [
        event for event in controller.runtime.callback_handler.events if event["type"] == "plan_validation"
    ]
    assert [(event["stage"], event["outcome"]) for event in validation_events] == [
        ("draft", "created"),
        ("critic", "failed"),
    ]
    assert "approved" not in validation_events[-1]
    assert validation_events[-1]["error_type"] == "WorkflowInvariantError"


def test_controller_creates_plan_when_missing():
    calls = []
    state = FakeState(None)

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        if role == "plan_creator":
            return '{"objective":"assess","constraints":["Stay in scope"],"current_phase":1,"phases":[{"id":1,"title":"Recon","status":"pending"}]}'
        if role == "plan_critic":
            return '{"approved":true,"feedback":[]}'
        if role == "task_prompt_builder":
            return '{"prompt":"execute recon task","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"task complete"}'
        if role == "phase_evaluator":
            return '{"status":"done","reason":"phase complete"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        if role == "task_creator":
            state.store_task(Task(task_uid="created", title="Created", objective="run recon", phase=1, status="pending"))
        return "worked"

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
        max_iterations=4,
    )

    controller.run()

    assert calls == [
        "plan_creator",
        "plan_critic",
        "task_creator",
        "task_prompt_builder",
        "task_executor",
        "task_evaluator",
        "phase_evaluator",
    ]
    assert state.tasks[0].status == "done"
    assert state.plan.phases[0].status == "done"
    assert controller.runtime.callback_handler.termination_events[0][0] == "complete"
    plan_events = [
        event
        for event in controller.runtime.callback_handler.events
        if event.get("metadata", {}).get("kind") == "plan"
    ]
    assert [event["metadata"]["action"] for event in plan_events] == ["created", "updated"]
    assert plan_events[0]["content"] == (
        "Plan created\n"
        "Objective: assess\n"
        "Current phase: 1/1\n\n"
        "Constraints:\n"
        "- Stay in scope\n\n"
        "[active] 1. Recon"
    )
    assert "[done] 1. Recon" in plan_events[-1]["content"]
    assert plan_events[-1]["metadata"]["assessment_complete"] is True
    plan_created_index = next(
        index
        for index, item in enumerate(controller.runtime.callback_handler.timeline)
        if item[0] == "event" and item[1].get("metadata", {}).get("action") == "created"
    )
    task_started_index = next(
        index
        for index, item in enumerate(controller.runtime.callback_handler.timeline)
        if item[0] == "event" and item[1].get("type") == "task_started"
    )
    final_plan_index = max(
        index
        for index, item in enumerate(controller.runtime.callback_handler.timeline)
        if item[0] == "event" and item[1].get("metadata", {}).get("kind") == "plan"
    )
    termination_index = next(
        index
        for index, item in enumerate(controller.runtime.callback_handler.timeline)
        if item[0] == "termination"
    )
    assert plan_created_index < task_started_index
    assert final_plan_index < termination_index


def test_existing_unchanged_plan_does_not_emit_output():
    runtime = _runtime()
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assert controller._ensure_plan() == _plan()
    assert runtime.callback_handler.events == []


def test_plan_output_emitter_failure_does_not_interrupt_workflow():
    runtime = _runtime()

    def fail_emit(_event):
        raise RuntimeError("disconnected")

    runtime.callback_handler.emit_ui_event = fail_emit
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    controller._emit_plan_output("created", _plan())


def test_controller_reopens_completed_plan_only_at_start():
    completed_plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Recon", status="done"),
            PlanPhase(id=2, title="Validate", status="done"),
        ],
        assessment_complete=True,
    )
    state = FakeState(completed_plan)

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute reopened task","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"task complete"}'
        if role == "phase_evaluator":
            return '{"status":"done","reason":"reclosed"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt):
        if role == "task_creator":
            state.store_task(Task(task_uid="reopened", title="Reopened", objective="run reopened", phase=1, status="pending"))
        return "worked"

    controller = MultiAgentWorkflowController(
        runtime=_runtime(progress=0),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
        max_iterations=4,
    )

    controller.run()

    assert state.plan.phases[0].status == "done"
    assert state.plan.phases[1].status == "pending"
    assert state.plan.assessment_complete is True
    assert controller.runtime.callback_handler.termination_events[0][0] == "partial_failure"
    first_plan_event = next(
        event
        for event in controller.runtime.callback_handler.events
        if event.get("metadata", {}).get("kind") == "plan"
    )
    assert first_plan_event["metadata"]["action"] == "updated"
    assert "[active] 1. Recon" in first_plan_event["content"]
    assert "[pending] 2. Validate" in first_plan_event["content"]


def test_controller_marks_empty_phase_partial_when_task_creator_creates_no_tasks():
    state = FakeState(_plan())

    def text_runner(role, prompt, tools, system_prompt):
        if role == "phase_evaluator":
            return '{"status":"continue","reason":"needs tasks"}'
        raise AssertionError(role)

    work_calls = []

    def work_runner(role, prompt, tools, system_prompt):
        work_calls.append((role, {tool.__name__ for tool in tools}))

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
        max_iterations=1,
    )

    with pytest.raises(WorkflowInvariantError, match="Task creation failed for phase 1"):
        controller.run()

    assert work_calls == [
        ("task_creator", {"create_tasks"}),
        ("task_creator", {"create_tasks"}),
        ("task_creator", {"create_tasks"}),
        ("task_creator", {"create_tasks"}),
        ("task_creator", {"create_tasks"}),
        ("task_creator", {"create_tasks"}),
        ("task_creator", {"create_tasks"}),
    ]


def test_controller_completes_empty_final_validation_phase_without_task_creator():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(
            id=1,
            title="Impact Validation and Proof Generation",
            status="active",
            criteria="Validate findings with proof",
        )],
    )
    state = FakeState(plan, finding_records=[{"resolution": "verified"}])

    def work_runner(*_args):
        raise AssertionError("task creator must not run when no finding requires validation")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
        work_runner=work_runner,
    )

    controller.run()

    assert state.plan.phases[0].status == "done"
    assert state.plan.assessment_complete is True


def test_empty_final_validation_phase_with_unresolved_finding_requires_tasks():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(
            id=1,
            title="Impact Validation and Proof Generation",
            status="active",
            criteria="Validate findings with proof",
        )],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, finding_records=[{"resolution": None}]),
        text_runner=lambda *_args: "{}",
    )

    assert controller._can_complete_empty_validation_phase(plan, plan.phases[0]) is False


def test_empty_final_validation_phase_does_not_hide_incomplete_predecessor_work():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Assess routes", status="partial_failure"),
            PlanPhase(
                id=2,
                title="Impact Validation and Proof Generation",
                status="active",
                criteria="Validate findings with proof",
            ),
        ],
    )
    pending = Task(task_uid="pending", title="Pending", objective="assess route", phase=1, status="pending")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=AdvancingFakeState(plan, tasks=[pending]),
        text_runner=lambda *_args: "{}",
    )

    assert controller._can_complete_empty_validation_phase(plan, plan.phases[1]) is True
    summary = controller._workflow_coverage_summary(plan)
    assert summary[0]["status_reason"] == "Phase incomplete with 1 actionable task(s) remaining."


def test_validation_phase_claims_existing_pending_verification_task_without_task_creator():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Assessment", status="partial_failure"),
            PlanPhase(
                id=2,
                title="Impact Validation and Proof Generation",
                status="active",
                criteria="Validate findings with proof",
            ),
        ],
    )
    validation = Task(
        task_uid="verify-1",
        title="Verify finding: SQL injection",
        objective="Verify finding",
        phase=1,
        status="pending",
        kind="finding_validation",
        reference_id="finding-1",
    )
    state = AdvancingFakeState(
        plan,
        tasks=[validation],
        finding_records=[{
            "finding_uid": "finding-1",
            "verification_task_uid": "verify-1",
            "resolution": None,
        }],
    )

    def work_runner(*_args):
        raise AssertionError("task creator must not run when a validation task already exists")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
        work_runner=work_runner,
        max_iterations=1,
    )

    with pytest.raises(WorkflowInvariantError, match="iteration limit"):
        controller.run()

    claimed = state.list_tasks()[0]
    assert claimed.phase == 2
    assert claimed.status == "active"
    reassigned = next(event for event in controller.runtime.callback_handler.events if event["type"] == "task_reassigned")
    assert reassigned["source_phase"] == 1
    assert reassigned["phase"] == 2


def test_validation_phase_marks_missing_verification_task_partial_without_task_creator():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(
            id=1,
            title="Impact Validation and Proof Generation",
            status="active",
            criteria="Validate findings with proof",
        )],
    )
    state = FakeState(plan, finding_records=[{
        "finding_uid": "finding-1",
        "verification_task_uid": "missing-task",
        "resolution": None,
    }])

    def work_runner(*_args):
        raise AssertionError("generic task creator must not replace a missing verification task")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
        work_runner=work_runner,
    )

    controller.run()

    assert state.plan.phases[0].status == "partial_failure"
    assert state.plan.assessment_complete is True


def test_validation_phase_recreates_missing_verification_task_from_persisted_packet():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(
            id=1,
            title="Impact Validation and Proof Generation",
            status="active",
            criteria="Validate findings with proof",
        )],
    )
    state = FakeState(plan, finding_records=[{
        "finding_uid": "finding-1",
        "verification_task_uid": "missing-task",
        "resolution": None,
        "candidate_data": {"title": "Config exposure", "verification_packet": {
            "target": "http://target.test/config",
            "artifacts": ["artifact:evidence.json"],
            "target_scope": "subset",
            "target_ids": ["target-1"],
        }},
    }])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=state
    )

    controller._recover_missing_finding_validation_tasks(plan.phases[0])

    assert len(state.tasks) == 1
    assert state.tasks[0].kind == "finding_validation"
    assert state.tasks[0].reference_id == "finding-1"
    assert state.finding_records[0]["verification_task_uid"] == state.tasks[0].task_uid


def test_validation_phase_does_not_recover_contaminated_finding_packet():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(
            id=1,
            title="Impact Validation and Proof Generation",
            status="active",
            criteria="Validate findings with proof",
        )],
    )
    state = FakeState(plan, finding_records=[{
        "finding_uid": "open-redirect",
        "verification_task_uid": "missing-task",
        "resolution": None,
        "candidate_data": {
            "finding_uid": "secret-exposure",
            "title": "Open redirect",
            "verification_packet": {
                "finding_uid": "secret-exposure",
                "target": "http://target.test/redirect",
            },
        },
    }])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(), budget=BudgetConfig(max_duration_minutes=60), state_store=state
    )

    controller._recover_missing_finding_validation_tasks(plan.phases[0])

    assert state.tasks == []
    assert state.finding_records[0]["verification_task_uid"] == "missing-task"


def test_validation_phase_with_terminal_history_marks_missing_verification_task_partial():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(
            id=1,
            title="Impact Validation and Proof Generation",
            status="active",
            criteria="Validate findings with proof",
        )],
    )
    terminal_history = Task(
        task_uid="old-validation",
        title="Prior validation attempt",
        objective="Validate an earlier candidate",
        phase=1,
        status="partial_failure",
        kind="finding_validation",
        reference_id="finding-old",
    )
    state = FakeState(
        plan,
        tasks=[terminal_history],
        finding_records=[{
            "finding_uid": "finding-1",
            "verification_task_uid": "missing-task",
            "resolution": None,
        }],
    )

    def work_runner(*_args):
        raise AssertionError("terminal validation history must not trigger generic task creation")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
        work_runner=work_runner,
    )

    controller.run()

    assert state.plan.phases[0].status == "partial_failure"
    assert state.plan.assessment_complete is True


def test_completed_closure_phase_reports_unresolved_prior_work_and_partial_termination():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Assess routes", status="partial_failure"),
            PlanPhase(id=2, title="Coverage closure", status="done"),
        ],
        assessment_complete=False,
    )
    pending = Task(task_uid="pending", title="Pending", objective="assess route", phase=1, status="pending")
    state = AdvancingFakeState(plan, tasks=[pending])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
    )

    controller._emit_workflow_completion(plan)

    coverage = next(
        event
        for event in controller.runtime.callback_handler.events
        if event["type"] == "workflow_coverage_summary"
    )
    assert coverage["terminal_phase_completed_with_unresolved_prior_work"] is True
    assert coverage["phases"][1]["terminal_phase_completed_with_unresolved_prior_work"] is True
    assert coverage["phases"][1]["unresolved_prior_task_count"] == 1
    assert controller.runtime.callback_handler.termination_events[0][0] == "partial_failure"


def test_incomplete_message_separates_actionable_and_historical_failure_phases():
    plan = OperationPlan(
        objective="assess",
        current_phase=3,
        total_phases=3,
        phases=[
            PlanPhase(id=1, title="Hypotheses", status="partial_failure"),
            PlanPhase(id=2, title="Testing", status="partial_failure"),
            PlanPhase(id=3, title="Closure", status="partial_failure"),
        ],
        assessment_complete=False,
    )
    pending = Task(task_uid="pending", title="Pending", objective="resume", phase=1, status="pending")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[pending]),
        text_runner=lambda *_args: "{}",
    )

    controller._emit_workflow_completion(plan)

    message = controller.runtime.callback_handler.termination_events[0][1]
    assert message == (
        "Assessment incomplete: 1 actionable task(s) remain in phase(s) 1; "
        "unresolved task or phase failures remain in phase(s) 2, 3"
    )


def test_phase_terminal_guard_rejects_done_when_prior_phase_has_actionable_tasks():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Assess routes", status="partial_failure"),
            PlanPhase(id=2, title="Coverage closure", status="active"),
        ],
    )
    pending = Task(task_uid="pending", title="Pending", objective="assess route", phase=1, status="pending")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[pending]),
        text_runner=lambda *_args: "{}",
    )

    decision = controller._guard_phase_terminal_decision(
        plan.phases[1],
        workflow_mod.WorkflowDecision(status="done", reason="closure complete"),
        context="phase evaluation",
    )

    assert decision.status == "partial_failure"
    assert "earlier phase" in decision.reason
    assert "pending=1" in decision.reason


def test_phase_terminal_guard_allows_done_when_prior_work_is_terminal():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Assess routes", status="done"),
            PlanPhase(id=2, title="Coverage closure", status="active"),
        ],
    )
    completed = Task(task_uid="done", title="Done", objective="assess route", phase=1, status="done")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[completed]),
        text_runner=lambda *_args: "{}",
    )
    requested = workflow_mod.WorkflowDecision(status="done", reason="closure complete")

    assert controller._guard_phase_terminal_decision(
        plan.phases[1], requested, context="phase evaluation"
    ) == requested


def test_phase_terminal_guard_rejects_done_when_phase_has_terminal_task_failure():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Assess routes", status="active")],
    )
    failed = Task(
        task_uid="failed",
        title="Failed",
        objective="assess route",
        phase=1,
        status="partial_failure",
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[failed]),
        text_runner=lambda *_args: "{}",
    )

    decision = controller._guard_phase_terminal_decision(
        plan.phases[0],
        workflow_mod.WorkflowDecision(status="done", reason="closure complete"),
        context="phase evaluation",
    )

    assert decision.status == "partial_failure"
    assert "terminal task failures" in decision.reason


def test_phase_terminal_guard_converts_not_applicable_to_done_when_phase_has_completed_work():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Assess routes", status="active")],
    )
    completed = Task(task_uid="done", title="Done", objective="assess route", phase=1, status="done")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[completed]),
        text_runner=lambda *_args: "{}",
    )

    decision = controller._guard_phase_terminal_decision(
        plan.phases[0],
        workflow_mod.WorkflowDecision(status="not_applicable", reason="no work"),
        context="phase evaluation",
    )

    assert decision.status == "done"


def test_phase_terminal_guard_preserves_empty_finding_dependent_phase_status():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Hypotheses", status="partial_failure"),
            PlanPhase(id=2, title="Finding Validation", status="active", requires_finding_candidates=True),
        ],
    )
    pending = Task(task_uid="pending", title="Pending", objective="assess route", phase=1, status="pending")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[pending]),
        text_runner=lambda *_args: "{}",
    )

    decision = controller._guard_phase_terminal_decision(
        plan.phases[1],
        workflow_mod.WorkflowDecision(status="not_applicable", reason="no candidates"),
        context="phase evaluation",
    )

    assert decision.status == "not_applicable"


def test_task_cycle_progress_signature_changes_only_with_controller_observed_progress():
    acceptance = AcceptanceResult(
        criterion_id="criterion",
        status="satisfied",
        disposition="observation",
        summary="done",
        evidence_refs=("artifact:artifacts/first.txt",),
    )
    first = ToolOutcome(1, "tool-1", "shell", True, False, "curl /first", "HTTP 200")
    changed = ToolOutcome(2, "tool-2", "shell", True, False, "curl /second", "HTTP 404")

    signature = MultiAgentWorkflowController._task_cycle_progress_signature([first], [acceptance])

    assert signature == MultiAgentWorkflowController._task_cycle_progress_signature([first], [acceptance])
    assert signature != MultiAgentWorkflowController._task_cycle_progress_signature([changed], [acceptance])


def test_task_cycle_progress_actions_stop_only_for_identical_successful_action_and_result():
    first = ToolOutcome(1, "tool-1", "read_artifact", True, False, "artifact:artifacts/page.html", "body-v1")
    same = ToolOutcome(2, "tool-2", "read_artifact", True, False, "artifact:artifacts/page.html", "body-v1")
    changed_content = ToolOutcome(
        3, "tool-3", "read_artifact", True, False, "artifact:artifacts/page.html", "body-v2"
    )
    changed_action = ToolOutcome(4, "tool-4", "shell", True, False, "curl /next", "HTTP 200")

    first_actions = MultiAgentWorkflowController._task_cycle_progress_actions([first])

    assert MultiAgentWorkflowController._task_cycle_progress_actions([same]).issubset(first_actions)
    assert not MultiAgentWorkflowController._task_cycle_progress_actions([changed_content]).issubset(first_actions)
    assert not MultiAgentWorkflowController._task_cycle_progress_actions(
        [same, changed_action]
    ).issubset(first_actions)


def test_task_cycle_progress_actions_detects_changed_large_artifact_content():
    first = ToolOutcome(
        1,
        "tool-1",
        "read_artifact",
        True,
        False,
        "artifact:artifacts/page.html",
        "x" * 600 + "first",
    )
    changed = ToolOutcome(
        2,
        "tool-2",
        "read_artifact",
        True,
        False,
        "artifact:artifacts/page.html",
        "x" * 600 + "second",
    )

    first_actions = MultiAgentWorkflowController._task_cycle_progress_actions([first])

    assert not MultiAgentWorkflowController._task_cycle_progress_actions([changed]).issubset(first_actions)


def test_stagnation_actions_ignore_generated_artifact_names_but_preserve_new_evidence():
    first = ToolOutcome(
        1,
        "tool-1",
        "shell",
        True,
        False,
        "cat /app/outputs/dvwa/OP_1/artifacts/shell_20260811_214240_675d95.artifact.log | tail -n 50",
        "artifact_id:shell_20260811_214240_675d95.artifact.log\nno positive evidence",
    )
    same = ToolOutcome(
        2,
        "tool-2",
        "shell",
        True,
        False,
        "cat /app/outputs/dvwa/OP_1/artifacts/shell_20260811_214250_dea05a.artifact.log | tail -n 50",
        "artifact_id:shell_20260811_214250_dea05a.artifact.log\nno positive evidence",
    )
    changed = ToolOutcome(
        3,
        "tool-3",
        "shell",
        True,
        False,
        "cat /app/outputs/dvwa/OP_1/artifacts/shell_20260811_214302_53720c.artifact.log | tail -n 50",
        "artifact_id:shell_20260811_214302_53720c.artifact.log\nverbatim positive proof",
    )

    first_actions = MultiAgentWorkflowController._task_cycle_stagnation_actions([first])

    assert MultiAgentWorkflowController._task_cycle_stagnation_actions([same]).issubset(first_actions)
    assert not MultiAgentWorkflowController._task_cycle_stagnation_actions([changed]).issubset(first_actions)


def test_finding_repair_prompt_excludes_logical_target_ids_as_evidence():
    task = Task(task_uid="active", title="Candidate", objective="Verify the assigned finding", phase=1, status="active")

    prompt = MultiAgentWorkflowController._finding_persistence_repair_prompt(
        task,
        {},
        ["artifact:artifacts/evidence.txt"],
        "evidence assertion marker was not found",
    )

    assert "target ID" in prompt
    assert "unsupported result" in prompt
    assert "record_task_acceptance" in prompt


def test_repeat_loop_recovery_is_bounded_and_requires_changed_action():
    cycle_result = workflow_mod.TaskExecutorCycleResult(
        text="Stopped after repeated browser_evaluate_js calls",
        outcomes=[],
        repeat_loop_detected=True,
        repeat_loop_signature="loop-1",
        repeat_loop_reason="Stopped after repeated browser_evaluate_js calls",
    )

    assert not MultiAgentWorkflowController._repeat_loop_is_repeated(False, set(), "loop-1")
    assert MultiAgentWorkflowController._repeat_loop_is_repeated(True, {"loop-1"}, "loop-2")
    assert MultiAgentWorkflowController._repeat_loop_is_repeated(False, {"loop-1"}, "loop-1")

    guidance = MultiAgentWorkflowController._repeat_loop_recovery_guidance(cycle_result)
    assert "same normalized input" in guidance
    assert "same browser expression" in guidance
    assert "equivalent replacement task" in guidance


def test_task_correction_stops_after_repeated_no_progress_cycle():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 2})
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    actor_prompts = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute active","tools":[]}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        actor_prompts.append(prompt)
        return workflow_mod.TaskExecutorCycleResult(text="same reasoning", outcomes=[])

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(actor_prompts) == 2
    assert state.tasks[0].status == "partial_failure"
    assert "no durable or tool-state progress" in state.tasks[0].status_reason


def test_continuing_phase_with_task_history_rechecks_then_advances_on_creator_failure():
    plan = _plan()
    completed = Task(task_uid="done", title="Prior work", objective="Inspect target", phase=1, status="done")
    state = FakeState(plan, tasks=[completed])
    evaluator_calls = []
    creator_calls = []

    def text_runner(role, prompt, tools, system_prompt):
        assert role == "phase_evaluator"
        evaluator_calls.append(prompt)
        return '{"status":"continue","reason":"more work remains"}'

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        creator_calls.append(prompt)
        return SimpleNamespace(reason="required_tool_rejected", message="schema rejected")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_TASK_CREATOR_MAX_CORRECTIONS": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    with pytest.raises(WorkflowInvariantError, match="Task creation failed for phase 1"):
        controller.run()

    assert len(evaluator_calls) == 1
    assert len(creator_calls) == 3


def test_task_creator_requires_create_tasks_tool():
    runtime = _runtime()
    runtime.core_tools_list = [_tool("shell")]
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    with pytest.raises(WorkflowInvariantError, match="create_tasks tool is required"):
        controller._task_creator_tools()


def test_finding_dependent_task_creator_allows_only_verified_canonical_finding_refs(monkeypatch):
    phase = PlanPhase(
        id=1,
        title="Exploit Chain Analysis",
        status="active",
        requires_finding_candidates=True,
        task_creation_mode="finding_dependent",
    )
    plan = OperationPlan(objective="assess", current_phase=1, total_phases=1, phases=[phase])
    state = FakeState(plan, finding_records=[
        {"finding_uid": "verified-id", "resolution": "verified", "verification_task_uid": "verify-1"},
        {"finding_uid": "pending-id", "resolution": "", "verification_task_uid": "verify-2"},
        {"finding_uid": "rejected-id", "resolution": "validation_failure", "verification_task_uid": "verify-3"},
    ])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
    )
    captured = {}

    def build_tool(**kwargs):
        captured.update(kwargs)
        return _tool("create_tasks")

    monkeypatch.setattr(workflow_mod, "build_create_tasks_tool", build_tool)

    controller._task_creator_tools(phase)

    assert captured["required_finding_refs"] == {"finding:verified-id"}
    assert captured["finding_ref_aliases"]["verified-id"] == "finding:verified-id"
    assert "pending-id" not in captured["finding_ref_aliases"]
    assert "rejected-id" not in captured["finding_ref_aliases"]
    context = controller._task_creator_finding_context(phase)
    assert "verified-id" in context
    assert "pending-id" not in context
    assert "rejected-id" not in context


def test_task_creator_requires_retained_session_factory():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda *_args: "{}",
    )

    with pytest.raises(WorkflowInvariantError, match="requires a retained worker session factory"):
        controller._create_tasks(_plan(), _plan().phases[0])


def test_task_creator_passes_required_tool_run_policy():
    state = FakeState(_plan())
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        if role == "phase_evaluator":
            return '{"status":"continue","reason":"needs tasks"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        captured["role"] = role
        captured["tools"] = {tool.__name__ for tool in tools}
        captured["policy"] = run_policy
        state.store_task(Task(task_uid="created", title="Created", objective="run", phase=1, status="pending"))

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
        max_iterations=1,
    )

    with pytest.raises(WorkflowInvariantError, match="Workflow iteration limit reached"):
        controller.run()

    assert captured["role"] == "task_creator"
    assert captured["tools"] == {"create_tasks"}
    assert captured["policy"].required_tool_names == frozenset({"create_tasks"})
    assert captured["policy"].terminal_after_required_tools is True
    assert captured["policy"].allow_text_final_after_tools is False


def test_task_creator_uses_controller_prompt_with_complete_plan_and_contract():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Recon", status="active", criteria="Map exposure"),
            PlanPhase(id=2, title="Validate", status="pending", criteria="Confirm findings"),
        ],
    )
    state = FakeState(
        plan,
        tasks=[Task(task_uid="future", title="Existing", objective="Validate later", phase=2, status="pending")],
    )
    captured = {}

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        captured["call_count"] = captured.get("call_count", 0) + 1
        captured["prompt"] = prompt
        state.store_task(Task(task_uid="created", title="Created", objective="run", phase=1, status="pending"))

    runtime = _runtime()
    runtime.config.available_tools = ["curl"]
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    controller._create_tasks(plan, plan.phases[0])

    assert "## Complete Plan" in captured["prompt"]
    assert "## Active Phase" in captured["prompt"]
    assert "## Existing Tasks Across All Phases" in captured["prompt"]
    assert "Existing" in captured["prompt"]
    assert "Python assigns active phase 1 and pending status" in captured["prompt"]
    assert "valid plan phase IDs: 1, 2" not in captured["prompt"]
    assert "## create_tasks Payload Contract (Non-negotiable)" in captured["prompt"]
    assert '"title":"Cohesive actionable title"' in captured["prompt"]
    assert '"basis_kind"' not in captured["prompt"]
    assert '"output_kind":"inventory_manifest"' in captured["prompt"]
    assert '"snapshot_refs":[]' in captured["prompt"]
    assert "Canonical inventory manifest contract" in captured["prompt"]
    assert "Workflow maps, reports, and arbitrary JSON outputs are artifact evidence" in captured["prompt"]
    assert "unsupported top-level `description` fields" in captured["prompt"]
    assert 'Executor procedure capabilities (controller-derived): ["analyze", "request"]' in captured["prompt"]
    example = captured["prompt"].split("```json\n", 1)[1].split("\n```", 1)[0]
    example_task = json.loads(example)["tasks"][0]
    assert "basis_kind" not in example_task
    assert example_task["output_kind"] == "inventory_manifest"
    assert set(example_task["criteria"][0]) == {"description"}
    assert captured["call_count"] == 1


def test_task_creation_batches_use_resolved_context_and_preserve_atomic_groups(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Inventory", status="done"),
            PlanPhase(id=2, title="Assess", status="active"),
        ],
    )
    producer = Task(
        task_uid="inventory",
        title="Inventory",
        objective="Produce inventory",
        acceptance=_acceptance("inventory"),
        evidence=["artifact:artifacts/inventory.json"],
        phase=1,
        status="done",
    )
    runtime = _runtime()
    runtime.prompt_token_limit = 1_000
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, [producer]),
        text_runner=lambda *_args: "{}",
    )
    groups = [
        ("target-1", "endpoint", f"http://target.test/{index}-{'x' * 700}", [f"endpoint-{index}"])
        for index in range(3)
    ]
    def canonical_artifact(reference):
        if not reference.startswith("artifact:"):
            raise ValueError("not an artifact")
        return reference

    monkeypatch.setattr(workflow_mod, "canonical_artifact_reference", canonical_artifact)
    monkeypatch.setattr(workflow_mod, "_load_inventory_manifest", lambda _reference: ({"items": []}, "hash"))
    monkeypatch.setattr(workflow_mod, "_coverage_route_groups", lambda *_args, **_kwargs: groups)

    batches = controller._task_creation_batches(plan, plan.phases[1], "system")

    assert len(batches) == 3
    assert [batch.index for batch in batches] == [1, 2, 3]
    assert all(batch.total == 3 for batch in batches)
    assert all(len(batch.groups) == 1 for batch in batches)
    assert set().union(*(batch.item_ids for batch in batches)) == {
        "endpoint-0",
        "endpoint-1",
        "endpoint-2",
    }


def test_controller_inventory_filter_retains_unusual_in_scope_routes_and_removes_boundary_mismatch(
    tmp_path,
    monkeypatch,
):
    manifest_path = tmp_path / "inventory_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "id": "in-scope",
                        "target_id": "target-1",
                        "kind": "endpoint",
                        "value": "http://target.test:4280/etc/passwd",
                    },
                    {
                        "id": "wrong-host",
                        "target_id": "target-1",
                        "kind": "endpoint",
                        "value": "http://other.test:4280/admin",
                    },
                    {
                        "id": "wrong-host-parameter",
                        "target_id": "target-1",
                        "kind": "parameter",
                        "value": "redirect",
                        "attributes": {"endpoint_id": "wrong-host"},
                    },
                    {
                        "id": "unlinked-parameter",
                        "target_id": "target-1",
                        "kind": "parameter",
                        "value": "unusual_path",
                    },
                ],
                "unassessed_gaps": [],
            }
        ),
        encoding="utf-8",
    )
    plan = OperationPlan(
        objective="assess http://target.test:4280",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Inventory", status="active")],
        targets=[OperationTarget(target_id="target-1", type="network", value="http://target.test:4280")],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan),
        text_runner=lambda *_args: "{}",
    )
    events = []
    monkeypatch.setattr(workflow_mod, "_artifact_path_from_ref", lambda _reference: str(manifest_path))
    monkeypatch.setattr(
        workflow_mod,
        "_load_inventory_manifest",
        lambda _reference: (json.loads(manifest_path.read_text(encoding="utf-8")), "filtered-hash"),
    )
    monkeypatch.setattr(controller, "_emit_workflow_event", events.append)

    manifest, _snapshot_hash = controller._load_controller_inventory_manifest(
        plan, "artifact:artifacts/inventory_manifest.json"
    )

    assert [item["id"] for item in manifest["items"]] == ["in-scope", "unlinked-parameter"]
    assert "invalid or out-of-scope" in manifest["unassessed_gaps"][-1]
    assert events[-1]["type"] == "inventory_manifest_scope_filter"
    assert events[-1]["rejection_reasons"] == {
        "host_mismatch": 1,
        "source_endpoint_outside_target": 1,
    }


def test_controller_inventory_filter_retains_parameter_for_its_in_scope_target():
    target = OperationTarget(target_id="target-1", type="network", value="http://target.test:4280")

    assert MultiAgentWorkflowController._inventory_item_target_rejection_reason(
        {
            "id": "parameter-1",
            "target_id": "target-1",
            "kind": "parameter",
            "value": "redirect",
            "endpoint_id": "endpoint-1",
        },
        {target.target_id: target},
    ) == ""
    assert MultiAgentWorkflowController._inventory_item_target_rejection_reason(
        {
            "id": "endpoint-1",
            "target_id": "target-1",
            "kind": "endpoint",
            "value": "https://other.test:4280/",
        },
        {target.target_id: target},
    ) == "scheme_mismatch"


def test_model_claim_conflict_emits_telemetry_without_changing_task_state(monkeypatch):
    task = Task(task_uid="active", title="Active", objective="test", phase=1, status="active")
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), [task]),
        text_runner=lambda *_args: "{}",
    )
    events = []
    monkeypatch.setattr(controller, "_emit_workflow_event", events.append)

    controller._emit_model_claim_conflicts(
        task,
        "The store_finding call was successful.",
        [ToolOutcome(1, "store-1", "store_finding", False, True, "candidate", "rejected")],
    )

    assert events == [{
        "type": "model_claim_conflicts_with_tool_outcome",
        "task_uid": "active",
        "phase": 1,
        "claimed_action": "store_finding",
        "actual_outcome": "failed_or_absent",
        "tool_ids": ["store-1"],
    }]


def test_default_task_creation_batch_estimates_compact_fallback_prompt():
    plan = _plan()
    irrelevant = Task(
        task_uid="old-terminal",
        title="Large irrelevant prior task",
        objective="old work",
        phase=2,
        status="done",
    )
    state = FakeState(plan, [irrelevant])
    runtime = _runtime()
    runtime.prompt_token_limit = 48_000
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
    )

    batch = controller._task_creation_batches(plan, plan.phases[0], "system")[0]
    prompt = controller._task_creator_prompt(plan, plan.phases[0], batch)

    assert batch.estimated_input_tokens > 0
    assert "task_phase_status_counts" in prompt
    assert "Large irrelevant prior task" not in prompt
    assert "Never emit `work_type`" in prompt


def test_task_creator_uses_fresh_session_for_each_batch_and_retains_batch_corrections(monkeypatch):
    state = FakeState(_plan())
    runtime = _runtime(env_ints={"CYBER_TASK_CREATOR_MAX_CORRECTIONS": 1})
    lifecycle = []
    prompts = []
    session_number = 0

    @contextmanager
    def session(role, tools, system_prompt):
        nonlocal session_number
        session_number += 1
        current_session = session_number
        lifecycle.append(("open", current_session))
        calls = 0

        def run(prompt, run_policy):
            nonlocal calls
            calls += 1
            prompts.append((current_session, prompt))
            if current_session == 1 and calls == 1:
                return SimpleNamespace(reason="required_tool_rejected", message="schema rejected")
            state.store_task(Task(
                task_uid=f"created-{current_session}",
                title=f"Created {current_session}",
                objective="run",
                phase=1,
                status="pending",
            ))
            return SimpleNamespace(reason="task_creator_done")

        try:
            yield run
        finally:
            lifecycle.append(("close", current_session))

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
        executor_session_factory=session,
    )
    batches = [
        TaskCreationBatch(1, 2, "artifact:artifacts/inventory.json", ((
            "target-1", "endpoint", "http://target.test/one", ("endpoint-1",)
        ),), 500),
        TaskCreationBatch(2, 2, "artifact:artifacts/inventory.json", ((
            "target-1", "endpoint", "http://target.test/two", ("endpoint-2",)
        ),), 500),
    ]
    monkeypatch.setattr(controller, "_task_creation_batches", lambda *_args: batches)

    outcome = controller._create_tasks(_plan(), _plan().phases[0])

    assert lifecycle == [("open", 1), ("close", 1), ("open", 2), ("close", 2)]
    assert [session_id for session_id, _prompt in prompts] == [1, 1, 2]
    assert "Batch 1 of 2" in prompts[0][1]
    assert "Submit exactly one snapshot proposal" in prompts[0][1]
    assert "Validation result" in prompts[1][1]
    assert "Batch 2 of 2" in prompts[2][1]
    assert outcome.created_count == 2
    assert outcome.attempts == 3
    assert outcome.batch_count == 2
    assert outcome.failed_batch_count == 0


def test_task_creator_continues_after_one_batch_exhausts_corrections(monkeypatch):
    state = FakeState(_plan())
    runtime = _runtime(env_ints={"CYBER_TASK_CREATOR_MAX_CORRECTIONS": 0})
    sessions = 0

    @contextmanager
    def session(role, tools, system_prompt):
        nonlocal sessions
        sessions += 1
        current_session = sessions

        def run(prompt, run_policy):
            if current_session == 2:
                state.store_task(Task(
                    task_uid="second-batch",
                    title="Second batch",
                    objective="run",
                    phase=1,
                    status="pending",
                ))
            return SimpleNamespace(reason="task_creator_done")

        yield run

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
        executor_session_factory=session,
    )
    batches = [
        TaskCreationBatch(1, 2, "artifact:artifacts/inventory.json", ((
            "target-1", "endpoint", "http://target.test/one", ("endpoint-1",)
        ),), 500),
        TaskCreationBatch(2, 2, "artifact:artifacts/inventory.json", ((
            "target-1", "endpoint", "http://target.test/two", ("endpoint-2",)
        ),), 500),
    ]
    monkeypatch.setattr(controller, "_task_creation_batches", lambda *_args: batches)

    outcome = controller._create_tasks(_plan(), _plan().phases[0])

    assert sessions == 2
    assert outcome.created_count == 1
    assert outcome.batch_count == 2
    assert outcome.failed_batch_count == 1
    assert "batch 1/2" in outcome.failure_reason
    assert "endpoint-1" in outcome.failure_reason


def test_task_creator_failure_reason_names_only_registered_tool():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    result = workflow_mod.TaskExecutorCycleResult(
        text="I need to inspect the artifact",
        outcomes=[ToolOutcome(
            sequence=1,
            tool_use_id="shell-1",
            tool_name="shell",
            success=False,
            correctable=False,
            input_summary='{"command": "ls"}',
            output_summary="Unknown tool: shell",
        )],
    )

    reason = controller._task_creator_failure_reason(result)

    assert reason == (
        "Only create_tasks is registered for this role; unavailable tool call(s): shell. "
        "Call create_tasks using its registered schema."
    )


def test_task_creator_repair_summary_preserves_rejected_proposal_intents():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    result = workflow_mod.TaskExecutorCycleResult(
        text="rejected",
        outcomes=[
            ToolOutcome(
                sequence=1,
                tool_use_id="create-1",
                tool_name="create_tasks",
                success=False,
                correctable=True,
                input_summary=json.dumps(
                    {
                        "tasks": [
                            {
                                "title": "Compile inventory",
                                "objective": "Compile the frozen endpoint inventory",
                                "basis_description": "Phase 1 evidence",
                                "methods": ["compile"],
                                "limits": {"max_items": 20},
                                "snapshot_refs": [],
                                "output_kind": "inventory_manifest",
                                "criteria": [{"description": "Store the finite manifest"}],
                                "target_ids": ["target-1"],
                            },
                            {
                                "title": "Assess routes",
                                "objective": "Assess each frozen route",
                                "basis_description": "Compiled manifest",
                                "methods": [],
                                "limits": {},
                                "snapshot_refs": ["artifact:artifacts/inventory.json"],
                                "criteria": [{"description": "Assess each route"}],
                                "target_ids": ["target-1"],
                            },
                        ]
                    }
                ),
                output_summary="proposal must not mix procedure and snapshot fields",
            )
        ],
    )

    summary = controller._task_creator_rejected_proposals(result)
    repair = controller._task_creator_repair_prompt("proposal must not mix procedure and snapshot fields", summary)

    assert "Compile the frozen endpoint inventory" in repair
    assert "Assess each frozen route" in repair
    assert '"max_items": 20' in repair
    assert '"output_kind": "inventory_manifest"' in repair
    assert '"target_ids": ["target-1"]' in repair
    assert "split them into separate valid proposal objects" in repair
    assert "do not silently" in repair


def test_task_creator_repair_prompt_explains_missing_procedure_limits():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
    )

    repair = controller._task_creator_repair_prompt(
        "procedure proposal requires at least one discovery procedure limit",
        "",
    )

    assert "omit `limits` to use the bounded defaults" in repair
    assert '"limits": {"max_requests": 50}' in repair


def test_task_creator_repair_prompt_explains_unavailable_execution_capability():
    runtime = _runtime()
    runtime.config.available_tools = ["katana"]
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
    )

    repair = controller._task_creator_repair_prompt(
        "task_preflight:execution_capability: no available runtime capability for shell (shell)",
        "",
    )

    assert '["analyze", "crawl"]' in repair
    assert "Tool names such as `shell` are not procedure capabilities" in repair


def test_task_creator_max_token_recovery_is_not_executor_evidence_prompt():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
    )

    prompt = controller._task_creator_max_token_recovery_prompt(_plan().phases[0])

    assert "create_tasks" in prompt
    assert "Do not explain" in prompt
    assert "Successful tools already observed" not in prompt


def test_task_creator_skips_controller_owned_finding_validation_phase():
    phase = PlanPhase(
        id=1,
        title="Finding validation",
        status="active",
        task_creation_mode="finding_validation",
    )
    plan = OperationPlan(objective="Validate candidates", phases=[phase], current_phase=1, total_phases=1)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan),
    )

    outcome = controller._create_tasks(plan, phase)

    assert outcome.created_count == 0
    assert outcome.attempts == 0
    assert outcome.failure_reason == "finding-validation tasks are controller-owned"


def test_task_creation_fails_before_model_retry_without_execution_path(monkeypatch):
    runtime = _runtime()
    runtime.config.available_tools = []
    calls = []
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        work_runner=lambda *_args: calls.append("task_creator"),
        executor_session_factory=retained_work_runner(lambda *_args: calls.append("task_creator")),
    )
    monkeypatch.setattr(controller, "_available_task_execution_capabilities", lambda: set())

    with pytest.raises(WorkflowInvariantError, match="no executable basis"):
        controller._create_tasks(_plan(), _plan().phases[0])

    assert calls == []
    assert {
        "type": "task_creation_feasibility",
        "phase": 1,
        "outcome": "blocked",
        "code": "no_execution_path",
        "available_capabilities": [],
        "blocked_requirements": ["executor_procedure_capability", "eligible_snapshot"],
        "snapshot_batch_available": False,
    } in runtime.callback_handler.events


def test_controller_owned_synthesis_skips_executor_capability_drift_check():
    acceptance = AcceptanceContract(
        mode="outcome",
        basis=AcceptanceBasis(
            kind="procedure",
            description="Controller-owned synthesis",
            source_refs=["plan:phase-1"],
            procedure={
                "methods": ["controller_synthesis"],
                "limits": {"max_items": 1},
                "stop_condition": "first_limit_reached",
                "gap_policy": "record_unassessed",
                "output_kind": "artifact",
            },
        ),
        criteria=[
            AcceptanceCriterion(
                id="synthesis-outcome",
                description="Store the synthesized artifact",
                evidence_requirements=[EvidenceRequirement(kind="artifact")],
            )
        ],
    )
    task = Task(
        task_uid="synthesis",
        title="Synthesize",
        objective="Merge outputs",
        phase=1,
        status="active",
        acceptance=acceptance,
        recovery_context={
            "task_preflight_validated": True,
            "phase_task_contract": {"task_role": "synthesis"},
        },
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
    )

    assert controller._task_pre_execution_feedback(task) == []


def test_task_creator_retries_once_when_first_run_creates_no_durable_tasks():
    state = FakeState(_plan())
    prompts = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        prompts.append(prompt)
        if len(prompts) == 2:
            state.store_task(Task(task_uid="repaired", title="Repaired", objective="run", phase=1, status="pending"))

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    controller._create_tasks(_plan(), _plan().phases[0])

    assert len(prompts) == 2
    assert "preceding `create_tasks` call was rejected or produced no new actionable task" in prompts[1]
    assert "exactly one corrected `create_tasks` call" in prompts[1]
    assert "Preserve every correction already made" in prompts[1]
    assert "## Active Phase" not in prompts[1]
    assert len(state.tasks) == 1


def test_task_creator_replays_complete_text_json_submission_without_prompt_changes(monkeypatch):
    state = FakeState(_plan())
    tool_calls = []

    def task_creator_tools(phase=None, batch=None, invocation_observer=None):
        del phase, batch

        def create_tasks(tasks):
            tool_calls.append(tasks)
            result = '{"complete": true, "created_count": 1}'
            invocation_observer({"tasks": tasks}, result, None)
            state.store_task(Task(task_uid="replayed", title="Replayed", objective="run", phase=1, status="pending"))
            return result

        create_tasks.__name__ = "create_tasks"
        return [create_tasks]

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
        work_runner=lambda *_args: json.dumps({"tasks": [{"title": "Replay this proposal"}]}),
        executor_session_factory=retained_work_runner(
            lambda *_args: json.dumps({"tasks": [{"title": "Replay this proposal"}]})
        ),
    )
    monkeypatch.setattr(controller, "_task_creator_tools", task_creator_tools)

    outcome = controller._create_tasks(_plan(), _plan().phases[0])

    assert tool_calls == [[{"title": "Replay this proposal"}]]
    assert outcome.created_count == 1
    assert state.tasks[0].task_uid == "replayed"
    assert controller.runtime.callback_handler.efficiency_events == [
        ("task_creator_json_submission_replay", None)
    ]
    assert any(
        event.get("type") == "task_creator_json_submission_replay" and event.get("status") == "accepted"
        for event in controller.runtime.callback_handler.events
    )


def test_task_creator_does_not_replay_text_after_a_real_create_tasks_call(monkeypatch):
    state = FakeState(_plan())
    tool_calls = []

    def task_creator_tools(phase=None, batch=None, invocation_observer=None):
        del phase, batch

        def create_tasks(tasks):
            tool_calls.append(tasks)
            result = '{"complete": true, "created_count": 1}'
            invocation_observer({"tasks": tasks}, result, None)
            state.store_task(Task(task_uid="called", title="Called", objective="run", phase=1, status="pending"))
            return result

        create_tasks.__name__ = "create_tasks"
        return [create_tasks]

    def work_runner(_role, _prompt, tools, _system_prompt, _run_policy):
        tools[0](tasks=[{"title": "Called proposal"}])
        return json.dumps({"tasks": [{"title": "Would duplicate"}]})

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )
    monkeypatch.setattr(controller, "_task_creator_tools", task_creator_tools)

    outcome = controller._create_tasks(_plan(), _plan().phases[0])

    assert tool_calls == [[{"title": "Called proposal"}]]
    assert outcome.created_count == 1
    assert controller.runtime.callback_handler.efficiency_events == []


def test_task_creator_does_not_replay_incomplete_text_json(monkeypatch):
    state = FakeState(_plan())
    tool_calls = []

    def task_creator_tools(phase=None, batch=None, invocation_observer=None):
        del phase, batch, invocation_observer

        def create_tasks(tasks):
            tool_calls.append(tasks)
            return '{"complete": true, "created_count": 1}'

        create_tasks.__name__ = "create_tasks"
        return [create_tasks]

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_TASK_CREATOR_MAX_CORRECTIONS": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
        work_runner=lambda *_args: '{"tasks": [{"title": "truncated"}',
        executor_session_factory=retained_work_runner(
            lambda *_args: '{"tasks": [{"title": "truncated"}'
        ),
    )
    monkeypatch.setattr(controller, "_task_creator_tools", task_creator_tools)

    outcome = controller._create_tasks(_plan(), _plan().phases[0])

    assert tool_calls == []
    assert outcome.created_count == 0
    assert any(
        event.get("type") == "task_creator_json_submission_replay" and event.get("status") == "ineligible"
        for event in controller.runtime.callback_handler.events
    )


def test_task_creator_rejected_text_json_replay_uses_existing_correction_path(monkeypatch):
    state = FakeState(_plan())
    tool_calls = []

    def task_creator_tools(phase=None, batch=None, invocation_observer=None):
        del phase, batch

        def create_tasks(tasks):
            tool_calls.append(tasks)
            error = ValueError("proposal validation failed")
            invocation_observer({"tasks": tasks}, None, error)
            raise error

        create_tasks.__name__ = "create_tasks"
        return [create_tasks]

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_TASK_CREATOR_MAX_CORRECTIONS": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *_args: "{}",
        work_runner=lambda *_args: json.dumps({"tasks": [{"title": "Rejected proposal"}]}),
        executor_session_factory=retained_work_runner(
            lambda *_args: json.dumps({"tasks": [{"title": "Rejected proposal"}]})
        ),
    )
    monkeypatch.setattr(controller, "_task_creator_tools", task_creator_tools)

    outcome = controller._create_tasks(_plan(), _plan().phases[0])

    assert tool_calls == [[{"title": "Rejected proposal"}]]
    assert "proposal validation failed" in outcome.failure_reason
    assert any(
        event.get("type") == "task_creator_json_submission_replay" and event.get("status") == "rejected"
        for event in controller.runtime.callback_handler.events
    )


def test_task_creator_opens_and_cleans_one_session_for_all_default_attempts():
    prompts = []
    lifecycle = []

    @contextmanager
    def session(role, tools, system_prompt):
        lifecycle.append(("open", role))

        def run(prompt, run_policy):
            prompts.append(prompt)
            return SimpleNamespace(reason="required_tool_rejected", message="schema rejected")

        try:
            yield run
        finally:
            lifecycle.append(("close", role))

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda *_args: "{}",
        executor_session_factory=session,
    )

    outcome = controller._create_tasks(_plan(), _plan().phases[0])

    assert outcome.attempts == 7
    assert len(prompts) == 7
    assert lifecycle == [("open", "task_creator"), ("close", "task_creator")]
    assert all("## Complete Plan" not in prompt for prompt in prompts[1:])


def test_task_creator_uses_retained_correction_after_max_tokens():
    state = FakeState(_plan())
    prompts = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        prompts.append(prompt)
        if len(prompts) == 1:
            raise MaxTokensReachedException("max_tokens")
        state.store_task(Task(task_uid="repaired", title="Repaired", objective="run", phase=1, status="pending"))

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    outcome = controller._create_tasks(_plan(), _plan().phases[0])

    assert len(prompts) == 2
    assert "task creator reached its model token limit" in prompts[1]
    assert outcome.created_count == 1
    assert controller.runtime.callback_handler.efficiency_events == [
        ("max_token_exhaustion", "task_creator", "output_truncation", 1, None)
    ]


def test_task_creator_uses_configured_retained_correction_attempts():
    prompts = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        prompts.append(prompt)
        return SimpleNamespace(reason="required_tool_rejected")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_TASK_CREATOR_MAX_CORRECTIONS": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    controller._create_tasks(_plan(), _plan().phases[0])

    assert len(prompts) == 3
    assert "up to 2 correction(s)" in prompts[0]
    assert "Validation result" in prompts[1]


def test_task_creator_duplicate_only_success_uses_bounded_retained_corrections():
    prompts = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        prompts.append(prompt)
        return SimpleNamespace(reason="task_creator_done", message="Task creator completed after create_tasks")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_TASK_CREATOR_MAX_CORRECTIONS": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    outcome = controller._create_tasks(_plan(), _plan().phases[0])

    assert len(prompts) == 3
    assert outcome.created_count == 0
    assert "no new actionable tasks" in outcome.failure_reason


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("requires a finding created by this task", "call store_finding"),
        ("existing_finding requires a finding:<id>", "actual existing finding"),
        ("finding candidate is ambiguous", "exactly one canonical current-task finding"),
        ("inventory manifest schema_version must be 1", "schema version 1"),
        ("invalid evidence reference memory:missing", "durable evidence first"),
        ("No acceptance result was recorded.", "remaining assigned work"),
        ("invalid disposition", "canonical enum values"),
    ],
)
def test_task_acceptance_repair_guidance_is_prerequisite_aware(error, expected):
    guidance = MultiAgentWorkflowController._task_acceptance_repair_instruction(error)

    assert expected in guidance


def test_acceptance_failure_signature_tracks_inventory_artifact_state():
    first = ToolOutcome(
        sequence=1,
        tool_use_id="first",
        tool_name="record_task_acceptance",
        success=False,
        correctable=True,
        input_summary="same acceptance input",
        output_summary=f"inventory manifest validation failed artifact_sha256={'a' * 64}: invalid route",
    )
    same_artifact = ToolOutcome(
        sequence=2,
        tool_use_id="same",
        tool_name="record_task_acceptance",
        success=False,
        correctable=True,
        input_summary="changed acceptance summary",
        output_summary=f"inventory manifest validation failed artifact_sha256={'a' * 64}: invalid route",
    )
    repaired_artifact = ToolOutcome(
        sequence=3,
        tool_use_id="repaired",
        tool_name="record_task_acceptance",
        success=False,
        correctable=True,
        input_summary="same acceptance input",
        output_summary=f"inventory manifest validation failed artifact_sha256={'b' * 64}: another route",
    )

    signature = MultiAgentWorkflowController._acceptance_failure_signature

    assert signature(first) == signature(same_artifact)
    assert signature(first) != signature(repaired_artifact)


def test_task_creator_reassigns_new_future_phase_tasks_to_active_phase():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Recon", status="active"),
            PlanPhase(id=2, title="Validate", status="pending"),
        ],
    )
    existing_future = Task(
        task_uid="existing",
        title="Existing",
        objective="Validate later",
        phase=2,
        status="pending",
    )
    state = FakeState(plan, tasks=[existing_future])
    prompts = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        prompts.append(prompt)
        state.store_task(
            Task(task_uid="future", title="Validate", objective="Validate later", phase=2, status="pending")
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    controller._create_tasks(plan, plan.phases[0])

    assert len(prompts) == 1
    phases_by_uid = {task.task_uid: task.phase for task in state.tasks}
    assert phases_by_uid == {"existing": 2, "future": 1}


def test_task_creator_prompt_sets_execution_boundary_without_tool_selection():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    prompt = controller._task_creator_prompt(_plan(), _plan().phases[0])

    assert "Candidate optional tools" not in prompt
    assert "Your only action is one successful" in prompt
    assert "Every proposal MUST follow this exact structure:" in prompt
    assert "unsupported top-level `description` fields" in prompt
    assert "Stop immediately" in prompt
    assert "Python assigns active phase 1" in prompt
    assert "Never emit `acceptance`, `phase`, `status`, `target_scope`" in prompt
    assert "without violating any plan constraint" in prompt
    assert "plan_constraints[1]{constraint}:" in prompt
    assert "Stay within the authorized target scope" in prompt
    assert "ordinary HTTP procedure proposal must name exactly one endpoint route" in prompt
    assert "ordered multi-step workflow" in prompt
    assert "Split an ordinary multi-route HTTP procedure into one route-scoped proposal" in prompt


def test_task_creator_prompt_uses_bounded_active_phase_view_without_mutating_plan():
    plan = _plan()
    phase = replace(
        plan.phases[0],
        criteria="Generate testable hypotheses across key workflows from the frozen inventory.",
    )
    plan.phases[0] = phase
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    prompt = controller._task_creator_prompt(plan, phase)

    assert phase.criteria == "Generate testable hypotheses across key workflows from the frozen inventory."
    assert "Generate testable hypotheses across key workflows from the frozen inventory" not in prompt
    assert "Generate testable hypotheses from the frozen inventory" in prompt


def test_task_creator_prompt_exposes_replacement_parent_criteria():
    parent = Task(
        task_uid="failed-parent",
        title="Failed bounded task",
        objective="Complete one bounded check",
        phase=1,
        status="partial_failure",
        acceptance=_acceptance("criterion-parent"),
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[parent]),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    prompt = controller._task_creator_prompt(_plan(), _plan().phases[0])

    assert "replacement_parent_criteria[1]{parent_task_uid,criterion_id,description}:" in prompt
    assert "failed-parent,criterion-parent" in prompt
    assert "Do not guess criterion IDs" in prompt
    assert '"replacement_of":"parent-task-uid"' in prompt


def test_executor_and_phase_prompts_define_canonical_decision_tables():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    executor_prompt = controller._task_executor_contract(Task(
        task_uid="task",
        title="Task",
        objective="Assess one behavior",
        phase=1,
        status="active",
    ))
    phase_prompt = controller._phase_evaluator_prompt(_plan(), _plan().phases[0])

    assert "Acceptance Disposition Decision Table" in executor_prompt
    assert "only after this task successfully calls `store_finding`" in executor_prompt
    assert "Never use `finding_candidate` merely because this task confirmed a pre-existing finding" in executor_prompt
    assert "Apply this precedence table exactly" in phase_prompt
    assert "`continue`: only when runnable pending work remains" in phase_prompt


def test_workflow_prompts_serialize_single_tasks_and_phases_as_json():
    plan = _plan()
    phase = plan.phases[0]
    task = Task(
        task_uid="active",
        title="Map target",
        objective="Map the authorized target",
        phase=phase.id,
        status="active",
    )
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    prompt_spec = {
        "prompt": "Map the authorized target",
        "memory_ids": [],
        "tools": [],
        "shell_commands": [],
    }
    phase_json = json.dumps(phase.to_dict(), indent=2, sort_keys=True)
    task_json = json.dumps(task.to_dict(), indent=2, sort_keys=True)

    prompts_and_objects = [
        (
            controller._task_phase_classifier_prompt(phase, [(task, phase)]),
            [("## Active phase", phase_json)],
        ),
        (
            controller._task_prompt_builder_prompt(plan, phase, task),
            [("## Phase", phase_json), ("## Task", task_json)],
        ),
        (
            controller._task_prompt_critic_prompt(plan, phase, task, prompt_spec),
            [("## Active phase", phase_json), ("## Assigned task", task_json)],
        ),
        (
            controller._task_prompt_revision_prompt(plan, phase, task, prompt_spec, ["Add evidence"]),
            [("## Active phase", phase_json), ("## Assigned task", task_json)],
        ),
        (
            controller._task_creator_prompt(plan, phase),
            [("## Active Phase", phase_json)],
        ),
        (
            controller._task_evaluator_prompt(plan, phase, task),
            [
                ("## Evaluation target: active task", task_json),
                ("## Context only: active phase", phase_json),
            ],
        ),
        (
            controller._phase_evaluator_prompt(plan, phase),
            [("## Evaluation target: active phase", phase_json)],
        ),
    ]

    for prompt, expected_objects in prompts_and_objects:
        for heading, serialized_object in expected_objects:
            assert f"{heading}\n{serialized_object}" in prompt

    builder_prompt = prompts_and_objects[1][0]
    creator_prompt = prompts_and_objects[4][0]
    phase_evaluator_prompt = prompts_and_objects[6][0]
    assert "## Plan\nplan_overview[1]" in builder_prompt
    assert "plan_phases[1]{id,title,status,criteria}:" in builder_prompt
    assert "## Existing Tasks Across All Phases\ntask_phase_status_counts[1]" in creator_prompt
    assert "task_creation_relevant_tasks[1]" in creator_prompt
    assert "## Canonical task acceptance ledger\n[" in phase_evaluator_prompt
    assert '"manifest_hash"' in phase_evaluator_prompt


def test_phase_evaluator_uses_accepted_artifact_instead_of_predicted_task_evidence(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CYBER_OPERATION_ROOT", str(tmp_path))
    accepted_artifact = tmp_path / "artifacts" / "accepted.json"
    accepted_artifact.parent.mkdir()
    accepted_artifact.write_text('{"result":"accepted"}', encoding="utf-8")
    monkeypatch.setattr(
        workflow_mod,
        "_artifact_path_from_ref",
        lambda reference: str(accepted_artifact) if reference.endswith("accepted.json") else "missing",
    )
    task = Task(
        task_uid="mapped",
        title="Map surface",
        objective="Map the bounded surface",
        acceptance=_artifact_acceptance("mapped-artifact"),
        phase=1,
        status="done",
        evidence=["artifact:artifacts/predicted.json"],
    )
    state = FakeState(_plan(), tasks=[task])
    state.acceptance_results[task.task_uid] = [
        AcceptanceResult(
            criterion_id="mapped-artifact",
            status="satisfied",
            disposition="observation",
            summary="Stored the accepted map",
            evidence_refs=["artifact:artifacts/accepted.json"],
        )
    ]
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        captured["prompt"] = prompt
        return '{"status":"done","reason":"Accepted evidence is complete"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    decision = controller._evaluate_phase(_plan(), _plan().phases[0])

    assert decision.status == "done"
    assert "artifact:artifacts/accepted.json" in captured["prompt"]
    assert str(accepted_artifact) in captured["prompt"]
    assert "artifact:artifacts/predicted.json" not in captured["prompt"]


def test_optional_tool_catalog_uses_tool_spec_name_and_description():
    tool = _tool("python_name")
    tool.tool_spec = {"name": "spec_name", "description": "Spec description."}
    runtime = _runtime()
    runtime.optional_tools_list = [tool]
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    catalog = controller._optional_tool_catalog()

    assert catalog.startswith("optional_tools[1]{name,description}:")
    assert "spec_name,Spec description." in catalog
    assert "python_name" not in catalog


def test_task_prompt_builder_lists_core_and_optional_tool_capabilities_separately():
    runtime = _runtime()
    runtime.core_tools_list[0].tool_spec = {"name": "spec_shell", "description": "Execute shell commands."}
    runtime.optional_tools_list[0].tool_spec = {"name": "spec_scan", "description": "Run a targeted scan."}
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    task = Task(
        task_uid="active",
        title="Active",
        objective="run",
        phase=1,
        status="active",
    )

    prompt = controller._task_prompt_builder_prompt(_plan(), _plan().phases[0], task)

    assert "core_tools[4]{name,description}:" in prompt
    assert "spec_shell,Execute shell commands." in prompt
    assert "optional_tools[2]{name,description}:" in prompt
    assert "spec_scan,Run a targeted scan." in prompt
    assert "`tools` JSON field contains optional-tool names only" in prompt
    assert "Never return core-tool names in `tools`" in prompt
    assert "Overlapping capabilities are allowed" in prompt
    assert "capability required by the task that supplied native tools do not provide" not in prompt
    assert "mandatory execution guardrail" in prompt
    assert "plan_constraints[1]{constraint}:" in prompt
    assert 'curl -sS -o /dev/null -w "%{http_code} %{url_effective}\\n" <url>' in prompt
    assert "curl -sS -D - -o /dev/null <url>" in prompt
    assert "Do not rely on bare `curl -s <url>` as evidence" in prompt


def test_finding_validation_prompt_separates_payload_markers_from_target_scope():
    task = TaskModel(
        task_uid="verify-1",
        title="Verify configuration exposure",
        objective="Independently verify finding candidate finding-1 against http://192.168.253.101:3001/api/config.",
        acceptance=_acceptance(),
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )
    state = FakeState(
        _plan(),
        tasks=[task],
        finding_records=[
            {
                "finding_uid": "finding-1",
                "candidate_data": {
                    "verification_packet": {
                        "target": "http://192.168.253.101:3001/api/config",
                        "technique": "credential_exposure",
                        "expected_result": "Unauthenticated access is denied",
                        "observed_result": "postgres://bc:bc@db:5432/bc",
                        "evidence_assertions": [
                            {"artifact": "artifact:config.json", "marker": "https://bucket.s3.amazonaws.com"}
                        ],
                    }
                },
            }
        ],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    prompt = controller._task_prompt_builder_prompt(_plan(), _plan().phases[0], task)
    context = controller._finding_validation_context(task)

    assert "payload markers" in prompt
    assert "never as network endpoints to probe or connect to" in prompt
    assert "postgres://bc:bc@db:5432/bc" in context
    assert "https://bucket.s3.amazonaws.com" in context
    assert "## Finding Validation Context" in prompt


def test_finding_validation_context_documents_secret_exposure_manifest_schema():
    task = TaskModel(
        task_uid="verify-1",
        title="Verify exposed API key",
        objective="Independently verify finding candidate finding-1.",
        acceptance=_acceptance(),
        phase=1,
        status="active",
        kind="finding_validation",
        reference_id="finding-1",
    )
    state = FakeState(
        _plan(),
        tasks=[task],
        finding_records=[
            {
                "finding_uid": "finding-1",
                "candidate_data": {
                    "title": "Exposed API key",
                    "claim": "A secret exposure was reported.",
                    "technique": "secret exposure",
                },
            }
        ],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    context = json.loads(controller._finding_validation_context(task))

    assert context["confirmation_manifest_schema"] == {
        "checks": {
            "secret_exposure": {
                "reexposure_artifact": "artifact:<fresh operation-local exposure artifact>",
            }
        }
    }
    assert "Only this controller-bound finding_uid" in context["binding_rule"]
    assert "version" not in context["confirmation_manifest_rule"]


def test_task_prompt_builder_requires_reusable_acceptance_summaries():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(
        task_uid="active",
        title="Map endpoints",
        objective="Enumerate login and admin endpoints and document whether each is accessible",
        phase=1,
        status="active",
    )

    prompt = controller._task_prompt_builder_prompt(_plan(), _plan().phases[0], task)

    assert "Require every acceptance summary to state the concrete result or negative result" in prompt
    assert "publishes" in prompt
    assert "store_observation" not in prompt
    assert "store_finding" in prompt


def test_task_prompt_builder_adds_bounded_task_scoped_swarm_contract():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(
        task_uid="active",
        title="Test independent hypotheses",
        objective="Test independent command-injection hypotheses against the assigned service",
        phase=1,
        status="active",
    )

    prompt = controller._task_prompt_builder_prompt(_plan(), _plan().phases[0], task)

    assert "core `swarm` tool" in prompt
    assert "independent capability branches" in prompt
    assert "no more than three agents" in prompt
    assert "explicit handoff triggers" in prompt
    assert "parent executor consolidates results" in prompt
    assert "must not create or execute workflow tasks" in prompt


def test_task_prompts_delegate_loop_recovery_to_controller_without_retry_ceiling():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(
        task_uid="active",
        title="Test recovery",
        objective="Test the assigned endpoint",
        phase=1,
        status="active",
    )

    builder_prompt = controller._task_prompt_builder_prompt(_plan(), _plan().phases[0], task)
    executor_contract = controller._task_executor_contract(task, {"store_observation"})

    combined = f"{builder_prompt}\n{executor_contract}"
    assert "actual registered tool calls" in combined
    assert "controller-owned executor contract supplies the terminal acceptance protocol" in combined
    assert "controller supplies a loop or recovery instruction" in executor_contract
    assert "one correction" not in combined
    assert "correction fails" not in combined
    assert "retry" not in combined.lower()


def test_task_prompt_critic_checks_pseudo_calls_and_tool_selection_limits():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(
        task_uid="active",
        title="Test prompt",
        objective="Test the assigned endpoint",
        phase=1,
        status="active",
    )

    prompt = controller._task_prompt_critic_prompt(
        _plan(),
        _plan().phases[0],
        task,
        {"prompt": "Use the target.", "memory_ids": [], "tools": [], "shell_commands": []},
    )

    assert "actual registered tool calls" in prompt
    assert "pseudo-syntax" in prompt
    assert "Controller-Appended Terminal Protocol" in prompt
    assert "record_task_acceptance` exactly once" in prompt
    assert "Do not reject a draft for omitting" in prompt


def test_task_prompt_builder_can_omit_controller_owned_acceptance_requirements():
    task = Task(
        task_uid="active",
        title="Map entry points",
        objective="Map the assigned web entry points",
        phase=1,
        status="active",
        acceptance=_artifact_acceptance("entry-points"),
    )
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            assert "Do not restate those controller-owned requirements here" in prompt
            return '{"prompt":"Inspect the assigned entry points and capture the results.","tools":[],"shell_commands":[]}'
        if role == "task_prompt_critic":
            captured["critic"] = prompt
            return '{"approved":true,"feedback":[]}'
        if role == "task_evaluator":
            return '{"status":"partial_failure","reason":"no acceptance was recorded"}'
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt: captured.setdefault("executor", prompt),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert "Do not reject a draft for omitting" in captured["critic"]
    assert "## Frozen Task Acceptance Contract (Controller-owned)" in captured["executor"]
    assert '"kind": "artifact"' in captured["executor"]
    assert "## Task Executor Contract (Controller-owned)" in captured["executor"]


def test_task_prompt_critic_defines_swarm_acceptance_rules():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(
        task_uid="active",
        title="Test hypotheses",
        objective="Investigate independent command-injection hypotheses",
        phase=1,
        status="active",
    )

    prompt = controller._task_prompt_critic_prompt(
        _plan(),
        _plan().phases[0],
        task,
        {"prompt": "Use swarm if needed.", "memory_ids": [], "tools": [], "shell_commands": []},
    )

    assert "uses `swarm` only when" in prompt
    assert "at most three distinct child approaches" in prompt
    assert "explicit handoff triggers" in prompt
    assert "does not delegate task creation" in prompt


def test_task_prompt_revision_preserves_swarm_contract_rules():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="active", title="Test", objective="Test hypotheses", phase=1, status="active")

    prompt = controller._task_prompt_revision_prompt(
        _plan(),
        _plan().phases[0],
        task,
        {"prompt": "Use swarm if needed.", "memory_ids": [], "tools": [], "shell_commands": []},
        ["Define bounded swarm use"],
    )

    assert "Swarm is a core tool supplied automatically" in prompt
    assert "limit the team to three" in prompt
    assert "parent-owned acceptance" in prompt


def test_task_prompt_builder_excludes_published_acceptance_memory():
    acceptance_memory = {
        "id": "acceptance-memory-1",
        "memory": "Task acceptance for route mapping. Criterion routes [satisfied]: /login returned 200.",
        "metadata": {"category": "observation", "source": "task_acceptance"},
    }
    state = FakeState(_plan())
    state.client = SimpleNamespace(
        list_memories=lambda **kwargs: [acceptance_memory],
        get_memory_by_id=lambda memory_id: acceptance_memory if memory_id == "acceptance-memory-1" else None,
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(
        task_uid="active",
        title="Test login",
        objective="Use the mapped login route",
        phase=1,
        status="active",
    )

    prompt = controller._task_prompt_builder_prompt(_plan(), _plan().phases[0], task)

    assert "acceptance-memory-1" not in prompt
    assert "/login returned 200" not in prompt
    assert controller._selected_memory_context(["acceptance-memory-1"]) == ""


def test_task_prompt_critic_delegates_acceptance_summary_requirements_to_controller():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(
        task_uid="active",
        title="Inventory routes",
        objective="Identify exposed routes and collect their response status",
        phase=1,
        status="active",
    )

    prompt = controller._task_prompt_critic_prompt(
        _plan(),
        _plan().phases[0],
        task,
        {"prompt": "Identify exposed routes and summarize what you found.", "memory_ids": [], "tools": []},
    )

    assert "Do not reject a draft for omitting" in prompt
    assert "requires concrete, reusable acceptance summaries" not in prompt
    assert "reject" in prompt
    assert "controller-appended acceptance contract" in prompt


def test_task_target_scope_text_preserves_explicit_url_service_boundaries():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        constraints=["Stay within scope"],
        targets=[
            OperationTarget(target_id="target-1", type="network", value="custom-scheme://service.example:4280"),
            OperationTarget(target_id="target-2", type="network", value="service.example"),
        ],
    )
    task = Task(
        task_uid="active",
        title="Map service",
        objective="Map the assigned service",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )

    scope_text = MultiAgentWorkflowController._task_target_scope_text(plan, task)

    assert "target-1 [network]: custom-scheme://service.example:4280" in scope_text
    assert "target-2" not in scope_text
    assert "explicit URL service target" in scope_text
    assert "scheme=custom-scheme" in scope_text
    assert "host=service.example" in scope_text
    assert "port=4280" in scope_text
    assert "Do not convert it into a host-only target" in scope_text
    assert "broad host or port enumeration violates scope" in scope_text


def test_task_shell_scope_guard_rejects_logical_target_id_and_exposes_concrete_endpoint():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget(target_id="target-1", type="network", value="http://192.0.2.10:3001")],
    )
    task = Task(
        task_uid="active",
        title="Assess service",
        objective="Test the assigned service",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )

    error = controller._validate_task_shell_commands(plan, task, ["curl -sS -i https://target-1:8443/api/spawn"])

    assert error is not None
    assert "not executed" in error
    assert "http://192.0.2.10:3001" in error
    assert "logical target ID as a hostname" in error
    event = next(
        event for event in controller.runtime.callback_handler.events if event["type"] == "task_command_scope_validation"
    )
    assert event["decision"] == "blocked"
    assert event["violations"][0]["literal"] == "https://target-1:8443/api/spawn"


def test_task_shell_scope_guard_allows_assigned_endpoint_and_recovery_prompts_keep_it():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget(target_id="target-1", type="network", value="http://192.0.2.10:3001")],
    )
    task = Task(
        task_uid="active",
        title="Assess service",
        objective="Test the assigned service",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )

    assert controller._validate_task_shell_commands(
        plan,
        task,
        ["curl -sS -i http://192.0.2.10:3001/api/spawn"],
    ) is None
    prompts = [
        controller._output_truncation_recovery_prompt(plan, task, [], [], "evidence", ["shell"]),
        controller._max_token_recovery_prompt(
            plan,
            task,
            workflow_mod.TaskExecutorCycleResult(
                text="",
                outcomes=[],
                max_tokens_classification="exploration",
            ),
            0,
        ),
        controller._task_executor_critic_guidance(plan, task, ["criterion-1"], [], next_cycle=2),
        controller._with_executable_target_scope(plan, task, "Correct the failed command."),
    ]

    for prompt in prompts:
        assert "http://192.0.2.10:3001" in prompt
        assert "never use them as hostnames" in prompt
        assert "target-1 [network]" not in prompt


def test_task_shell_scope_guard_ignores_urls_in_echoed_response_payload():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget(target_id="target-1", type="network", value="http://192.0.2.10:3001")],
    )
    task = Task(
        task_uid="active",
        title="Capture response",
        objective="Capture the assigned response",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )

    command = (
        "echo '{\"external_url\":\"https://example-bucket.invalid/config\"}' "
        "> artifacts/api_config_response.json"
    )

    assert controller._validate_task_shell_commands(plan, task, [command]) is None


def test_task_shell_scope_guard_allows_external_outbound_url_after_echoed_payload():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget(target_id="target-1", type="network", value="http://192.0.2.10:3001")],
    )
    task = Task(
        task_uid="active",
        title="Capture response",
        objective="Capture the assigned response",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
    )

    command = (
        "printf '%s' '{\"external_url\":\"https://example-bucket.invalid/config\"}' "
        "; curl -sS https://outside.example.invalid/ https://target-1.example.invalid/"
    )

    assert controller._validate_task_shell_commands(plan, task, [command]) is None


def test_task_prompt_scope_mismatch_is_revised_before_the_executor_can_run():
    plan = OperationPlan(
        objective="assess custom-scheme://service.example:4280",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget(target_id="target-1", type="network", value="custom-scheme://service.example:4280")],
    )
    task = Task(
        task_uid="active",
        title="Map service",
        objective="Map the assigned service",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    responses = iter(
        [
            '{"prompt":"Probe custom-scheme://service.example:4200/health","memory_ids":[],"tools":[],"shell_commands":[]}',
            '{"prompt":"Probe custom-scheme://service.example:4280/health","memory_ids":[],"tools":[],"shell_commands":[]}',
            '{"approved":true,"feedback":[]}',
        ]
    )
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        return next(responses)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
        text_runner=text_runner,
    )

    prompt_spec = controller._build_task_prompt(plan, plan.phases[0], task)

    assert prompt_spec["prompt"] == "Probe custom-scheme://service.example:4280/health"
    assert calls == ["task_prompt_builder", "task_prompt_builder", "task_prompt_critic"]
    event = next(event for event in controller.runtime.callback_handler.events if event["type"] == "task_scope_validation")
    assert event["stage"] == "prompt_draft"
    assert event["decision"] == "revision_requested"


def test_immutable_task_scope_mismatch_fails_without_prompt_generation():
    plan = OperationPlan(
        objective="assess custom-scheme://service.example:4280",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget(target_id="target-1", type="network", value="custom-scheme://service.example:4280")],
    )
    task = Task(
        task_uid="active",
        title="Map service",
        objective="Probe custom-scheme://service.example:4200/health",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    calls = []
    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
        text_runner=lambda *args: calls.append(args) or "{}",
    )

    with pytest.raises(workflow_mod.TaskPromptBuildError, match="Task content exceeds") as error:
        controller._build_task_prompt(plan, plan.phases[0], task)

    assert error.value.repairable is False
    assert calls == []
    event = next(event for event in controller.runtime.callback_handler.events if event["type"] == "task_scope_validation")
    assert event["stage"] == "immutable_task"
    assert event["decision"] == "blocked"


def test_task_executor_blocks_an_out_of_scope_prompt_that_bypasses_prompt_building(monkeypatch):
    plan = OperationPlan(
        objective="assess custom-scheme://service.example:4280",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget(target_id="target-1", type="network", value="custom-scheme://service.example:4280")],
    )
    task = Task(
        task_uid="active",
        title="Map service",
        objective="Map the assigned service",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    state = FakeState(plan, tasks=[task])
    worker_prompts = []
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda *args: "{}",
        work_runner=lambda *args: worker_prompts.append(args),
    )
    monkeypatch.setattr(
        controller,
        "_build_task_prompt",
        lambda *_args: {"prompt": "Probe custom-scheme://service.example:4200", "tools": []},
    )

    controller._run_task(plan, plan.phases[0], task)

    assert worker_prompts == []
    assert state.tasks[0].status == "partial_failure"
    assert "exceeded the assigned service boundary" in state.tasks[0].status_reason
    event = next(event for event in controller.runtime.callback_handler.events if event["type"] == "task_scope_validation")
    assert event["stage"] == "pre_executor"
    assert event["decision"] == "blocked"
    assert event["violations"][0]["literal"] == "custom-scheme://service.example:4200"


def test_task_scope_validation_adds_safe_event_to_active_task_trace(monkeypatch):
    plan = OperationPlan(
        objective="assess custom-scheme://service.example:4280",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget(target_id="target-1", type="network", value="custom-scheme://service.example:4280")],
    )
    task = Task(
        task_uid="active",
        title="Map service",
        objective="Map the assigned service",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    span = Mock()
    tracer = Mock()

    @contextmanager
    def span_context(*_args, **_kwargs):
        yield span

    tracer.start_as_current_span.side_effect = span_context
    monkeypatch.setattr(workflow_mod.otel_trace, "get_tracer", lambda _name: tracer)
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan, tasks=[task]),
        text_runner=lambda *args: "{}",
    )

    controller._emit_task_scope_validation(
        plan,
        task,
        "Probe custom-scheme://service.example:4200/health",
        "prompt_draft",
        "revision_requested",
    )

    tracer.start_as_current_span.assert_called_once()
    name = tracer.start_as_current_span.call_args.args[0]
    attributes = tracer.start_as_current_span.call_args.kwargs["attributes"]
    assert name == "task_scope_validation"
    assert attributes["workflow.scope_validation.stage"] == "prompt_draft"
    assert attributes["workflow.scope_validation.result"] == "blocked"
    assert attributes["workflow.scope_validation.violation_count"] == 1
    assert "Probe custom" not in attributes["workflow.scope_validation.violations"]


def test_missing_finding_acceptance_recovers_with_required_persistence_then_acceptance():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1, "CYBER_TASK_ACCEPTANCE_MAX_CORRECTIONS": 1})
    task = Task(task_uid="active", title="Reflected XSS", objective="Verify reflected XSS", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    calls = []
    acceptance_payload = {
        "status": "satisfied",
        "disposition": "finding_candidate",
        "summary": "The script payload was reflected without encoding.",
        "evidence_refs": ["artifact:artifacts/xss.html"],
    }

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"verify xss","tools":[]}'
        if role == "task_evaluator":
            return '{"status":"done","reason":"acceptance recorded"}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        calls.append((prompt, run_policy.required_tool_names))
        if len(calls) == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="candidate acceptance rejected",
                outcomes=[ToolOutcome(
                    sequence=1,
                    tool_use_id="acceptance-rejected",
                    tool_name="record_task_acceptance",
                    success=False,
                    correctable=False,
                    input_summary=json.dumps(acceptance_payload),
                    output_summary="Acceptance disposition finding_candidate requires a finding created by this task.",
                )],
            )
        if len(calls) == 2:
            assert run_policy.required_tool_names == {"store_finding"}
            assert "Do not repeat discovery" in prompt
            return workflow_mod.TaskExecutorCycleResult(
                text="finding stored",
                outcomes=[ToolOutcome(
                    sequence=2,
                    tool_use_id="finding-store",
                    tool_name="store_finding",
                    success=True,
                    correctable=False,
                    input_summary="artifact-backed finding",
                    output_summary='{"finding_ref":"finding:finding-1"}',
                )],
            )
        assert run_policy.required_tool_names == {"record_task_acceptance"}
        assert "finding:finding-1" in prompt
        state.acceptance_results[task.task_uid] = [AcceptanceResult(
            criterion_id=task.acceptance.criteria[0].id,
            status="satisfied",
            disposition="finding_candidate",
            summary=acceptance_payload["summary"],
            evidence_refs=("finding:finding-1",),
        )]
        return workflow_mod.TaskExecutorCycleResult(
            text="acceptance stored",
            outcomes=[ToolOutcome(
                sequence=3,
                tool_use_id="acceptance-stored",
                tool_name="record_task_acceptance",
                success=True,
                correctable=False,
                input_summary=json.dumps(acceptance_payload),
                output_summary="acceptance stored",
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert [required for _prompt, required in calls] == [
        {"record_task_acceptance"},
        {"store_finding"},
        {"record_task_acceptance"},
    ]
    assert state.tasks[0].status == "done"


def test_missing_finding_acceptance_ends_when_required_persistence_fails():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1, "CYBER_TASK_ACCEPTANCE_MAX_CORRECTIONS": 1})
    task = Task(task_uid="active", title="Reflected XSS", objective="Verify reflected XSS", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    calls = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        calls.append(run_policy.required_tool_names)
        if len(calls) == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="candidate acceptance rejected",
                outcomes=[ToolOutcome(
                    sequence=1,
                    tool_use_id="acceptance-rejected",
                    tool_name="record_task_acceptance",
                    success=False,
                    correctable=False,
                    input_summary=json.dumps({"disposition": "finding_candidate"}),
                    output_summary="Acceptance disposition finding_candidate requires a finding created by this task.",
                )],
            )
        return workflow_mod.TaskExecutorCycleResult(
            text="finding rejected",
            outcomes=[ToolOutcome(
                sequence=2,
                tool_use_id="finding-rejected",
                tool_name="store_finding",
                success=False,
                correctable=False,
                input_summary="invalid finding",
                output_summary="At least one existing artifact is required",
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, *_args: (
            '{"prompt":"verify xss","tools":[]}'
            if role == "task_prompt_builder"
            else '{"status":"done","reason":"acceptance recorded"}'
        ),
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert calls == [{"record_task_acceptance"}, {"store_finding"}]
    assert state.tasks[0].status == "partial_failure"
    assert "finding persistence" in state.tasks[0].status_reason


def test_missing_finding_acceptance_repairs_missing_evidence_assertion_once():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1, "CYBER_TASK_ACCEPTANCE_MAX_CORRECTIONS": 1})
    task = Task(task_uid="active", title="Reflected XSS", objective="Verify reflected XSS", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    calls = []

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        calls.append((prompt, run_policy.required_tool_names, run_policy.max_tool_calls))
        if len(calls) == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="candidate acceptance rejected",
                outcomes=[ToolOutcome(
                    sequence=1,
                    tool_use_id="acceptance-rejected",
                    tool_name="record_task_acceptance",
                    success=False,
                    correctable=False,
                    input_summary=json.dumps(
                        {
                            "disposition": "finding_candidate",
                            "evidence_refs": ["artifact:artifacts/evidence.txt"],
                        }
                    ),
                    output_summary=(
                        "RECORD_TASK_ACCEPTANCE_REPAIR_FINDING_PREREQUISITE: The preceding finding submission "
                        "did not persist. Do not retry acceptance. Repair store_finding first and use its returned "
                        "finding:<id> reference only after it succeeds."
                    ),
                    raw_output_summary=(
                        "Acceptance disposition finding_candidate requires a finding created by this task."
                    ),
                )],
            )
        if len(calls) == 2:
            assert "## Required Finding Prerequisite" in prompt
            assert "## Compact Task Continuation" not in prompt
            return workflow_mod.TaskExecutorCycleResult(
                text="finding rejected",
                outcomes=[ToolOutcome(
                    sequence=2,
                    tool_use_id="finding-rejected",
                    tool_name="store_finding",
                    success=False,
                    correctable=False,
                    input_summary="missing assertions",
                    output_summary="At least one evidence assertion is required",
                )],
            )
        if len(calls) == 3:
            assert run_policy.max_tool_calls == 2
            assert "evidence_assertions" in prompt
            assert "read_artifact for at most one" in prompt
            return workflow_mod.TaskExecutorCycleResult(
                text="finding stored after repair",
                outcomes=[ToolOutcome(
                    sequence=3,
                    tool_use_id="finding-stored",
                    tool_name="store_finding",
                    success=True,
                    correctable=False,
                    input_summary="corrected assertions",
                    output_summary='{"finding_ref":"finding:finding-1"}',
                )],
            )
        state.acceptance_results[task.task_uid] = [AcceptanceResult(
            criterion_id=task.acceptance.criteria[0].id,
            status="satisfied",
            disposition="finding_candidate",
            summary="Finding persisted",
            evidence_refs=("finding:finding-1",),
        )]
        return workflow_mod.TaskExecutorCycleResult(
            text="acceptance stored",
            outcomes=[ToolOutcome(
                sequence=4,
                tool_use_id="acceptance-stored",
                tool_name="record_task_acceptance",
                success=True,
                correctable=False,
                input_summary="acceptance",
                output_summary="acceptance stored",
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, *_args: (
            '{"prompt":"verify xss","tools":[]}'
            if role == "task_prompt_builder"
            else '{"status":"done","reason":"acceptance recorded"}'
        ),
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert [required for _prompt, required, _limit in calls] == [
        {"record_task_acceptance"},
        {"store_finding"},
        {"store_finding"},
        {"record_task_acceptance"},
    ]
    recovery_event = next(
        event
        for event in runtime.callback_handler.events
        if event["type"] == "acceptance_recovery_context" and event["reason"] == "finding_prerequisite"
    )
    assert recovery_event["required_tool"] == "store_finding"
    assert recovery_event["mode"] == "finding_prerequisite_store_with_durable_evidence"
    assert state.tasks[0].status == "done", state.tasks[0].status_reason


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("At least one evidence assertion is required", True),
        ("evidence_assertions marker was not found", True),
        ("Unknown severity", False),
    ],
)
def test_finding_submission_repairability_is_narrow(error, expected):
    assert MultiAgentWorkflowController._finding_submission_error_is_repairable(error) is expected


@pytest.mark.parametrize(
    ("error", "raw_error", "expected"),
    [
        ("Acceptance disposition finding_candidate requires a finding created by this task.", "", True),
        (
            "RECORD_TASK_ACCEPTANCE_REPAIR_FINDING_PREREQUISITE: The preceding finding submission did not persist.",
            "",
            True,
        ),
        (
            "RECORD_TASK_ACCEPTANCE_REPAIR_FINDING_PREREQUISITE: The preceding finding submission did not persist.",
            "Acceptance disposition finding_candidate requires a finding created by this task.",
            True,
        ),
        ("Acceptance evidence memory does not exist in this operation.", "", False),
    ],
)
def test_acceptance_current_task_finding_classifier_uses_raw_and_normalized_errors(error, raw_error, expected):
    assert MultiAgentWorkflowController._acceptance_requires_current_task_finding(error, raw_error) is expected


def test_task_prompts_reject_host_wide_scans_for_explicit_url_service_targets():
    plan = OperationPlan(
        objective="assess custom-scheme://service.example:4280",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        constraints=["Stay within the explicit service target"],
        targets=[OperationTarget(target_id="target-1", type="network", value="custom-scheme://service.example:4280")],
    )
    phase = plan.phases[0]
    task = Task(
        task_uid="active",
        title="Map service",
        objective="Map the assigned service",
        phase=1,
        status="active",
        target_scope="subset",
        target_ids=["target-1"],
    )
    prompt_spec = {
        "prompt": "Map the service but do not broaden target scope.",
        "memory_ids": [],
        "tools": [],
        "shell_commands": ["nmap"],
    }
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    builder_prompt = controller._task_prompt_builder_prompt(plan, phase, task)
    critic_prompt = controller._task_prompt_critic_prompt(plan, phase, task, prompt_spec)
    revision_prompt = controller._task_prompt_revision_prompt(plan, phase, task, prompt_spec, ["Remove broad scan"])
    creator_contract = controller._task_creator_contract(plan, phase)
    executor_contract = controller._task_executor_contract()
    tool_policy = controller._tool_selection_policy()
    normalized = " ".join(
        "\n".join(
            [
                builder_prompt,
                critic_prompt,
                revision_prompt,
                creator_contract,
                executor_contract,
                tool_policy,
            ]
        ).split()
    )

    assert "explicit `scheme://host:port` URL" in normalized
    assert "preserve that exact scheme, host, and port boundary" in normalized
    assert "host-only" in normalized
    assert "all open ports" in normalized
    assert "omitted-port" in normalized
    assert "`-p-`" in normalized
    assert "`1-65535`" in normalized
    assert "scheme-appropriate service tooling" in normalized
    assert "separate executable host or network target authorizes that scope" in normalized


def test_task_prompt_critic_permits_http_request_and_curl_overlap(monkeypatch):
    runtime = _runtime()
    runtime.core_tools_list.append(_tool("http_request"))
    runtime.config.available_tools = ["curl"]
    monkeypatch.setattr(
        workflow_mod,
        "get_shell_command_specs",
        lambda available: [
            {
                "command": "curl",
                "description": "HTTP client for URL requests",
                "capabilities": ["http_client"],
            }
        ],
    )
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="active", title="Fetch", objective="GET the target", phase=1, status="active")
    prompt_spec = {
        "prompt": "Fetch the target and save evidence.",
        "memory_ids": [],
        "tools": [],
        "shell_commands": ["curl"],
    }

    prompt = controller._task_prompt_critic_prompt(_plan(), _plan().phases[0], task, prompt_spec)

    assert "http_request" in prompt
    assert "curl" in prompt
    assert "Tool overlap is permitted" in prompt
    assert "Core tools are supplied automatically and must not appear in `tools`" in prompt
    assert "relevant and available" not in prompt


def test_task_prompt_spec_accepts_tools_and_commands_in_either_selection_list(monkeypatch):
    runtime = _runtime()
    runtime.optional_tools_list.append(_tool("dual_probe"))
    runtime.config.available_tools = ["curl", "whatweb", "katana", "feroxbuster", "dual_probe"]
    monkeypatch.setattr(
        workflow_mod,
        "get_shell_command_specs",
        lambda available: [
            {"command": command, "description": "Security command", "capabilities": []}
            for command in available
        ],
    )
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="task", title="Fetch", objective="Fetch target", phase=1, status="pending")

    normalized = controller._normalize_task_prompt_spec(
        {
            "prompt": "Fetch with any applicable clients",
            "memory_ids": [],
            "tools": ["mcp_scan", "whatweb", "katana", "feroxbuster", "dual_probe", "whatweb"],
            "shell_commands": ["curl", "module_probe", "dual_probe", "curl"],
        },
        task,
    )

    assert normalized["tools"] == ["mcp_scan", "dual_probe", "module_probe"]
    assert normalized["shell_commands"] == ["curl", "dual_probe", "whatweb", "katana", "feroxbuster"]


def test_task_prompt_spec_filters_common_shell_commands_before_validation(monkeypatch):
    runtime = _runtime()
    runtime.config.available_tools = ["httpx"]
    monkeypatch.setattr(
        workflow_mod,
        "get_shell_command_specs",
        lambda available: [
            {"command": "httpx", "description": "HTTP probe", "capabilities": []}
        ],
    )
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="task", title="Inspect", objective="Inspect target", phase=1, status="pending")

    normalized = controller._normalize_task_prompt_spec(
        {
            "prompt": "Inspect the target",
            "tools": ["grep", "python3", "module_probe", "httpx"],
            "shell_commands": ["awk", "python3", "module_probe", "httpx"],
        },
        task,
    )

    assert normalized["tools"] == ["module_probe"]
    assert normalized["shell_commands"] == ["httpx"]


def test_task_prompt_spec_allows_over_limit_selections_without_rationale():
    runtime = _runtime()
    for name in ("probe_a", "probe_b", "probe_c", "probe_d"):
        runtime.optional_tools_list.append(_tool(name))
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="task", title="Inspect", objective="Inspect target", phase=1, status="pending")
    prompt_spec = {
        "prompt": "Inspect the target",
        "tools": ["probe_a", "probe_b", "probe_c", "probe_d"],
        "shell_commands": [],
    }

    normalized = controller._normalize_task_prompt_spec(prompt_spec, task)

    assert normalized["tools"] == ["probe_a", "probe_b", "probe_c", "probe_d"]


def test_task_terminal_protocol_summary_covers_normal_validation_and_candidate_tasks():
    normal_task = Task(task_uid="normal", title="Normal", objective="Inspect", phase=1, status="pending")
    validation_task = Task(
        task_uid="validation",
        title="Validate",
        objective="Validate",
        phase=1,
        kind="finding_validation",
        status="pending",
    )
    candidate_task = Task(
        task_uid="candidate",
        title="Candidate",
        objective="Assess",
        phase=1,
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(kind="snapshot", description="Finding", source_refs=["task:source"]),
            criteria=[
                AcceptanceCriterion(
                    id="candidate",
                    description="Store finding",
                    evidence_requirements=[EvidenceRequirement(kind="finding_candidate")],
                )
            ],
        ),
        status="pending",
    )

    assert "record_task_acceptance` exactly once" in MultiAgentWorkflowController._task_terminal_protocol_summary(normal_task)
    assert "record_finding_validation" in MultiAgentWorkflowController._task_terminal_protocol_summary(validation_task)
    assert "store_finding" in MultiAgentWorkflowController._task_terminal_protocol_summary(candidate_task)


def test_task_prompt_spec_still_rejects_unknown_names_after_common_command_filtering():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="task", title="Inspect", objective="Inspect target", phase=1, status="pending")

    with pytest.raises(workflow_mod.TaskPromptBuildError, match="unavailable selection.*mystery-tool"):
        controller._normalize_task_prompt_spec(
            {
                "prompt": "Inspect the target",
                "tools": ["grep", "python3", "mystery-tool"],
                "shell_commands": [],
            },
            task,
        )


def test_task_prompt_spec_reclassifies_optional_tools_when_shell_is_unavailable():
    runtime = _runtime()
    runtime.core_tools_list = [_tool("editor")]
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="task", title="Scan", objective="Scan target", phase=1, status="pending")

    normalized = controller._normalize_task_prompt_spec(
        {
            "prompt": "Scan with available capabilities",
            "tools": [],
            "shell_commands": ["mcp_scan", "unavailable-command"],
        },
        task,
    )

    assert normalized["tools"] == ["mcp_scan"]
    assert normalized["shell_commands"] == []


def test_task_prompt_spec_filters_runtime_supplied_core_tools_from_both_selection_fields():
    runtime = _runtime()
    runtime.core_tools_list.extend([_tool("read_artifact"), _tool("store_observation")])
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="task", title="Store", objective="Store evidence", phase=1, status="pending")

    normalized = controller._normalize_task_prompt_spec(
        {
            "prompt": "Store evidence",
            "tools": ["read_artifact", "shell", "store_observation", "record_task_acceptance", "mcp_scan"],
            "shell_commands": ["shell", "store_observation", "record_task_acceptance", "module_probe"],
        },
        task,
    )

    assert normalized["tools"] == ["mcp_scan", "module_probe"]
    assert normalized["shell_commands"] == []


def test_task_prompt_spec_filters_controller_supplied_tools_with_runtime_fallback():
    runtime = _runtime()
    runtime.tools_list = [_tool("shell"), _tool("record_finding_validation")]
    runtime.core_tools_list = []
    runtime.optional_tools_list = [_tool("mcp_scan")]
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="task", title="Accept", objective="Assess target", phase=1, status="pending")

    normalized = controller._normalize_task_prompt_spec(
        {
            "prompt": "Assess target",
            "tools": ["shell", "record_finding_validation", "record_task_acceptance", "mcp_scan"],
            "shell_commands": ["record_task_acceptance"],
        },
        task,
    )

    assert normalized["tools"] == ["mcp_scan"]
    assert normalized["shell_commands"] == []


def test_task_prompt_spec_still_rejects_unknown_tools_after_filtering_core_tools():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="task", title="Store", objective="Store evidence", phase=1, status="pending")

    with pytest.raises(workflow_mod.TaskPromptBuildError, match="unknown or unavailable.*mystery-tool"):
        controller._normalize_task_prompt_spec(
            {
                "prompt": "Store evidence",
                "tools": ["store_observation", "mystery-tool"],
                "shell_commands": [],
            },
            task,
        )


def test_task_prompt_builder_lists_compact_shell_command_catalog(monkeypatch):
    runtime = _runtime()
    runtime.config.available_tools = ["longscan"]
    monkeypatch.setattr(
        workflow_mod,
        "get_shell_command_specs",
        lambda available: [
            {
                "command": "longscan",
                "description": "line one,\n" + ("x" * 600),
                "capabilities": ["scan", "validate"],
            }
        ],
    )
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    prompt = controller._task_prompt_builder_prompt(
        _plan(),
        _plan().phases[0],
        Task(task_uid="active", title="Active", objective="run", phase=1, status="active"),
    )
    catalog = prompt.split("## Candidate shell commands\n", maxsplit=1)[1]
    row = catalog.splitlines()[1]

    assert catalog.startswith("shell_commands[1]{command,description,capabilities}:")
    assert row.startswith("  longscan,line one;")
    assert row.endswith(",scan;validate")
    description = row.split(",", maxsplit=2)[1]
    assert len(description) == 250
    assert "keys prompt, memory_indices, memory_ids, tools,\nshell_commands." in prompt


def test_shell_command_catalog_is_empty_without_shell_tool(monkeypatch):
    runtime = _runtime()
    runtime.config.available_tools = ["httpx"]
    runtime.core_tools_list = [_tool("memory_retrieve")]
    command_specs = pytest.fail
    monkeypatch.setattr(workflow_mod, "get_shell_command_specs", command_specs)
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assert controller._shell_command_catalog() == (
        "shell_commands[0]{command,description,capabilities}:\n"
    )


def test_failed_shell_command_help_context_uses_runtime_available_tools(monkeypatch):
    runtime = _runtime()
    runtime.config.available_tools = ["feroxbuster"]
    calls = []
    monkeypatch.setattr(
        workflow_mod,
        "get_shell_command_help_context",
        lambda command, available: calls.append((command, available)) or "FULL HELP",
    )
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assert controller._failed_shell_command_help_context("feroxbuster") == "FULL HELP"
    assert calls == [("feroxbuster", ["feroxbuster"])]


def test_tool_catalog_renders_empty_lists_and_rejects_unknown_structure_names():
    runtime = _runtime()
    runtime.core_tools_list = []
    runtime.optional_tools_list = []
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assert controller._core_tool_catalog() == "core_tools[0]{name,description}:\n"
    assert controller._optional_tool_catalog() == "optional_tools[0]{name,description}:\n"
    with pytest.raises(ValueError, match="Unsupported tool catalog structure"):
        controller._tool_catalog("tools", [])


def test_phase_evaluator_receives_module_termination_policy():
    runtime = _runtime()
    runtime.termination_policy = "Require verified exploitability or documented non-exploitability."
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        captured["role"] = role
        captured["prompt"] = prompt
        captured["tools"] = tools
        captured["system_prompt"] = system_prompt
        return '{"status":"done","reason":"policy satisfied"}'

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=text_runner,
    )

    decision = controller._evaluate_phase(_plan(), _plan().phases[0])

    assert decision.status == "done"
    assert captured["role"] == "phase_evaluator"
    assert captured["tools"] == []
    assert "## Module Termination Policy" in captured["system_prompt"]
    assert "Require verified exploitability" in captured["system_prompt"]
    assert "Apply the module termination policy" in captured["prompt"]


def test_phase_evaluator_omits_empty_termination_policy_section():
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        captured["system_prompt"] = system_prompt
        return '{"status":"continue","reason":"needs more evidence"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=text_runner,
    )

    decision = controller._evaluate_phase(_plan(), _plan().phases[0])

    assert decision.status == "continue"
    assert captured["system_prompt"].startswith("## Evaluator Role Boundary")
    assert "base prompt" not in captured["system_prompt"]
    assert "Module Termination Policy" not in captured["system_prompt"]


def test_task_evaluator_does_not_receive_module_termination_policy():
    runtime = _runtime()
    runtime.termination_policy = "Only phase evaluation should receive this."
    captured = {}
    task = Task(task_uid="active", title="Active", objective="run", phase=1, status="active")

    def text_runner(role, prompt, tools, system_prompt):
        captured["role"] = role
        captured["prompt"] = prompt
        captured["tools"] = tools
        captured["system_prompt"] = system_prompt
        return '{"status":"partial_failure","reason":"not enough access"}'

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    decision = controller._evaluate_task(_plan(), _plan().phases[0], task)

    assert decision.status == "partial_failure"
    assert decision.reason == "not enough access"
    assert decision.instructions == ""
    assert captured["role"] == "task_evaluator"
    assert captured["tools"] == []
    assert "Evaluator Role Boundary" in captured["system_prompt"]
    assert "Module Termination Policy" not in captured["system_prompt"]
    assert "sole evaluation target" in captured["prompt"]
    assert "## Evaluation target: active task" in captured["prompt"]
    assert "## Context only: operation objective" in captured["prompt"]
    assert "## Acceptance guardrails: plan constraints" in captured["prompt"]
    assert '"instructions": string' in captured["prompt"]
    assert "concrete prescriptive next actions" in captured["prompt"]
    assert "an evidenced violation prevents done" in captured["prompt"].lower()
    assert "Stay within the authorized target scope" in captured["prompt"]
    assert "## Plan" not in captured["prompt"]


def test_task_evaluator_decision_includes_prescriptive_instructions():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        captured["role"] = role
        captured["prompt"] = prompt
        return json.dumps(
            {
                "status": "partial_failure",
                "reason": "control endpoint was not assessed",
                "instructions": "Request the control endpoint and store a status-coded artifact.",
            }
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    decision = controller._evaluate_task(_plan(), _plan().phases[0], task)

    assert captured["role"] == "task_evaluator"
    assert decision.status == "partial_failure"
    assert decision.reason == "control endpoint was not assessed"
    assert decision.instructions == "Request the control endpoint and store a status-coded artifact."
    assert "Satisfying the phase or operation objective does not make this task done" in captured["prompt"]


def test_task_evaluator_artifact_reader_allows_one_read_per_distinct_recorded_artifact(monkeypatch, tmp_path):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    artifact_refs = [f"artifact:artifacts/evidence-{index}.txt" for index in range(1, 9)]
    for index, reference in enumerate(artifact_refs, 1):
        (artifact_root / f"evidence-{index}.txt").write_text(reference, encoding="utf-8")
    task = Task(
        task_uid="evaluator-artifact-limit",
        title="Review recorded artifacts",
        objective="Review task evidence",
        phase=1,
        status="active",
        evidence=[artifact_refs[0], artifact_refs[1], "memory:task-note"],
    )
    acceptance = AcceptanceResult(
        criterion_id="task-outcome",
        status="satisfied",
        disposition="observation",
        summary="Recorded artifact evidence",
        evidence_refs=[artifact_refs[2], artifact_refs[3], artifact_refs[2], "finding:ignored", "https://ignored"],
        coverage=(
            CoverageResult("route-1", "satisfied", (artifact_refs[4], artifact_refs[5])),
            CoverageResult("route-2", "satisfied", (artifact_refs[6], artifact_refs[7], "memory:ignored")),
        ),
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
    )
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr("modules.tools.artifact._operation_output_root", lambda: str(tmp_path))

    assert controller._task_evaluator_artifact_refs(task, [acceptance]) == artifact_refs
    tools = controller._task_evaluator_tools(task, [acceptance])

    assert {tool.__name__ for tool in tools} == {"read_artifact"}
    for start_line in (1, 2, 3, 4):
        assert artifact_refs[0] in tools[0](artifact_refs[0], start_line=start_line)
    page_limit = ast.literal_eval(tools[0](artifact_refs[0], start_line=5))
    assert page_limit["status"] == "not_directly_readable"
    assert page_limit["reason"] == "artifact_page_budget_exhausted"


def test_task_evaluator_prompt_scales_artifact_budget_from_authorized_evidence(monkeypatch, tmp_path):
    artifact_refs = [f"artifact:artifacts/evidence-{index}.txt" for index in range(1, 13)]
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    for index in range(1, 13):
        (artifact_root / f"evidence-{index}.txt").write_text("evidence", encoding="utf-8")
    task = Task(
        task_uid="evaluator-artifact-budget",
        title="Review recorded artifacts",
        objective="Review task evidence",
        phase=1,
        status="active",
        evidence=artifact_refs,
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
    )
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr("modules.tools.artifact._operation_output_root", lambda: str(tmp_path))

    prompt = controller._task_evaluator_prompt(_plan(), _plan().phases[0], task)

    assert "- Smaller artifacts available for line review: 12" in prompt
    assert "- Larger artifacts available only by explicit byte page: 0" in prompt
    assert "- Total successful reads: 48" in prompt
    assert "- Successful pages per artifact: 4" in prompt
    assert "- Maximum lines per page: 200" in prompt
    assert "- Maximum UTF-8 bytes per page: 19200" in prompt
    assert "Acceptance summaries, review digest, and controller-observed outcomes are the default evidence" in prompt
    assert "Controller execution receipts are authoritative" in prompt
    assert "never reread artifacts to prove a receipt or tool invocation" in prompt
    assert artifact_refs[0] in prompt
    assert artifact_refs[-1] in prompt


def test_task_evaluator_allows_large_artifact_byte_pages_but_prioritizes_digest(monkeypatch, tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "mapping.json").write_text('{"mapped": true}', encoding="utf-8")
    (artifacts / "bundle.js").write_text("x" * 19_201, encoding="utf-8")
    task = Task(
        task_uid="large-artifact-review",
        title="Review mapping evidence",
        objective="Review task evidence",
        phase=1,
        status="active",
        evidence=["artifact:artifacts/mapping.json", "artifact:artifacts/bundle.js"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
    )
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr("modules.tools.artifact._operation_output_root", lambda: str(tmp_path))

    tools = controller._task_evaluator_tools(task, [])
    prompt = controller._task_evaluator_prompt(_plan(), _plan().phases[0], task)

    assert "mapped" in tools[0]("artifact:artifacts/mapping.json")
    with pytest.raises(RuntimeError, match="requires explicit byte paging"):
        tools[0]("artifact:artifacts/bundle.js")
    first_page = tools[0]("artifact:artifacts/bundle.js", start_byte=0, max_bytes=19_200)
    assert "'start_byte': 0" in first_page
    assert "- Smaller artifacts available for line review: 1" in prompt
    assert "- Larger artifacts available only by explicit byte page: 1" in prompt
    assert "- Total successful reads: 8" in prompt
    assert "artifact:artifacts/mapping.json (16 bytes)" in prompt
    assert "artifact:artifacts/bundle.js (19201 bytes; exceeds one page)" in prompt
    assert "Prefer smaller artifacts" in prompt
    assert "not routine review" in prompt


def test_task_evaluator_hides_raw_bundle_when_webcrack_derivative_is_available(monkeypatch, tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "bundle.js").write_text("x" * 19_201, encoding="utf-8")
    (artifacts / "bundle.webcrack.js").write_text(
        'const api = "/api/products";\n', encoding="utf-8"
    )
    task = Task(
        task_uid="formatted-bundle-review",
        title="Review client bundle evidence",
        objective="Review task evidence",
        phase=1,
        status="active",
        evidence=[
            "artifact:artifacts/bundle.js",
            "artifact:artifacts/bundle.webcrack.js",
        ],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
    )
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr("modules.tools.artifact._operation_output_root", lambda: str(tmp_path))

    tools = controller._task_evaluator_tools(task, [])
    prompt = controller._task_evaluator_prompt(_plan(), _plan().phases[0], task)

    assert controller._task_evaluator_artifact_refs(task, []) == [
        "artifact:artifacts/bundle.webcrack.js"
    ]
    with pytest.raises(RuntimeError, match="Artifact is not available to this evaluator"):
        tools[0]("artifact:artifacts/bundle.js", start_byte=0, max_bytes=19_200)
    assert "/api/products" in tools[0]("artifact:artifacts/bundle.webcrack.js", max_lines=1)
    assert "artifact:artifacts/bundle.webcrack.js" in prompt
    assert "artifact:artifacts/bundle.js" not in prompt


def test_task_evaluator_substitutes_webcrack_sibling_when_only_raw_bundle_is_cited(monkeypatch, tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "bundle.js").write_text("minified source", encoding="utf-8")
    (artifacts / "bundle.webcrack.js").write_text(
        'const api = "/api/products";\n', encoding="utf-8"
    )
    task_a = Task(
        task_uid="bundle-formatting",
        title="Format client bundle",
        objective="Create a readable client bundle derivative",
        phase=1,
        status="done",
        evidence=["artifact:artifacts/bundle.webcrack.js"],
    )
    task_b = Task(
        task_uid="bundle-review",
        title="Review raw client bundle evidence",
        objective="Review task evidence",
        phase=1,
        status="active",
        evidence=["artifact:artifacts/bundle.js"],
    )
    acceptance = AcceptanceResult(
        criterion_id="task-outcome",
        status="satisfied",
        disposition="observation",
        summary="Raw source bundle was retained as evidence.",
        evidence_refs=["artifact:artifacts/bundle.js"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task_a, task_b]),
    )
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr("modules.tools.artifact._operation_output_root", lambda: str(tmp_path))

    tools = controller._task_evaluator_tools(task_b, [acceptance])
    prompt = controller._task_evaluator_prompt(_plan(), _plan().phases[0], task_b, acceptance_results=[acceptance])

    assert controller._task_evaluator_artifact_refs(task_b, [acceptance]) == [
        "artifact:artifacts/bundle.webcrack.js"
    ]
    with pytest.raises(RuntimeError, match="Artifact is not available to this evaluator"):
        tools[0]("artifact:artifacts/bundle.js")
    assert "/api/products" in tools[0]("artifact:artifacts/bundle.webcrack.js", max_lines=1)
    assert "artifact:artifacts/bundle.webcrack.js" in prompt
    assert "artifact:artifacts/bundle.js" not in prompt
    assert task_b.evidence == ["artifact:artifacts/bundle.js"]
    assert acceptance.evidence_refs == ("artifact:artifacts/bundle.js",)


@pytest.mark.parametrize("derivative_exists, derivative_readable", [(False, True), (True, False)])
def test_task_evaluator_keeps_raw_bundle_when_webcrack_sibling_is_unavailable(
    monkeypatch,
    tmp_path,
    derivative_exists,
    derivative_readable,
):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "bundle.js").write_text("raw source", encoding="utf-8")
    if derivative_exists:
        (artifacts / "bundle.webcrack.js").write_text("formatted source", encoding="utf-8")
    task = Task(
        task_uid="raw-bundle-fallback",
        title="Review client bundle evidence",
        objective="Review task evidence",
        phase=1,
        status="active",
        evidence=["artifact:artifacts/bundle.js"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
    )
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr("modules.tools.artifact._operation_output_root", lambda: str(tmp_path))
    if not derivative_readable:
        original_open = Path.open

        def reject_webcrack_derivative(path, *args, **kwargs):
            if path.name == "bundle.webcrack.js":
                raise PermissionError("webcrack derivative is unreadable")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(
            Path,
            "open",
            reject_webcrack_derivative,
        )

    tools = controller._task_evaluator_tools(task, [])

    assert controller._task_evaluator_artifact_refs(task, []) == ["artifact:artifacts/bundle.js"]
    assert "raw source" in tools[0]("artifact:artifacts/bundle.js", max_lines=1)


def test_task_evaluator_does_not_substitute_non_sibling_or_fresh_bundle_artifacts(monkeypatch, tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "bundle.js.fresh").write_text("fresh raw source", encoding="utf-8")
    (artifacts / "other.webcrack.js").write_text("unrelated formatted source", encoding="utf-8")
    task = Task(
        task_uid="fresh-bundle",
        title="Review fresh bundle evidence",
        objective="Review task evidence",
        phase=1,
        status="active",
        evidence=["artifact:artifacts/bundle.js.fresh"],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
    )
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr("modules.tools.artifact._operation_output_root", lambda: str(tmp_path))

    tools = controller._task_evaluator_tools(task, [])

    assert controller._task_evaluator_artifact_refs(task, []) == ["artifact:artifacts/bundle.js.fresh"]
    assert "fresh raw source" in tools[0]("artifact:artifacts/bundle.js.fresh", max_lines=1)


def test_durable_artifact_evidence_keeps_raw_bundle_for_controller_provenance():
    outcome = ToolOutcome(
        sequence=1,
        tool_use_id="bundle-inventory",
        tool_name="client_bundle_inventory",
        success=True,
        correctable=False,
        input_summary='{"source_artifact":"artifact:artifacts/bundle.js"}',
        output_summary=(
            '{"formatted_artifact":"artifact:artifacts/bundle.webcrack.js",'
            '"extraction_artifact":"artifact:artifacts/bundle-inventory.json"}'
        ),
    )

    assert MultiAgentWorkflowController._artifact_refs_from_tool_outcomes([outcome]) == [
        "artifact:artifacts/bundle-inventory.json",
        "artifact:artifacts/bundle.js",
        "artifact:artifacts/bundle.webcrack.js",
    ]


def test_task_evaluator_artifact_reader_uses_persisted_acceptance_results_when_omitted(monkeypatch, tmp_path):
    artifact = tmp_path / "artifacts" / "recorded.txt"
    artifact.parent.mkdir()
    artifact.write_text("recorded evidence", encoding="utf-8")
    task = Task(task_uid="persisted-evidence", title="Review", objective="Review", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    state.acceptance_results[task.task_uid] = [AcceptanceResult(
        criterion_id="task-outcome",
        status="satisfied",
        disposition="observation",
        summary="Recorded artifact evidence",
        evidence_refs=["artifact:artifacts/recorded.txt"],
    )]
    captured = {}

    def text_runner(role, prompt, tools, system_prompt):
        captured["tools"] = tools
        return '{"status":"done","reason":"Recorded evidence is complete."}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )
    monkeypatch.setattr(memory_mod, "_operation_output_root", lambda: str(tmp_path))
    monkeypatch.setattr("modules.tools.artifact._operation_output_root", lambda: str(tmp_path))

    controller._evaluate_task(_plan(), _plan().phases[0], task)

    assert {tool.__name__ for tool in captured["tools"]} == {"read_artifact"}


def test_evaluator_tools_exclude_shell_and_optional_execution_tools():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    task = Task(task_uid="no-artifacts", title="Assess", objective="Assess", phase=1, status="active")

    assert controller._task_evaluator_tools(task, []) == []
    assert controller._phase_evaluator_tools() == []


def test_phase_evaluator_prompt_is_review_only():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    prompt = controller._phase_evaluator_prompt(_plan(), _plan().phases[0])

    assert "Review existing evidence and classify the active phase" in prompt
    assert "Evaluate the active phase" not in prompt
    assert "do not perform phase work" in prompt
    assert "Python alone decides whether the operation is complete" in prompt
    assert "Acceptance guardrails: plan constraints" in prompt
    assert "an evidenced violation prevents done" in prompt
    assert "Stay within the authorized target scope" in prompt


def test_prompt_builder_context_includes_task_history():
    state = FakeState(
        _plan(),
        tasks=[
            Task(task_uid="done", title="Worked", objective="use working path", phase=1, status="done",
                 status_reason="evidence stored", evidence=["memory:finding-1"]),
            Task(task_uid="blocked", title="Blocked", objective="avoid blocked path", phase=1, status="blocked",
                 status_reason="requires credentials"),
            Task(task_uid="active", title="Active", objective="run active", phase=1, status="active"),
        ],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    prompt = controller._task_prompt_builder_prompt(_plan(), _plan().phases[0], state.tasks[-1])

    assert "## Task history" in prompt
    assert "Execute only the assigned task objective below" in prompt
    assert "Do not continue into later phase objectives" in prompt
    assert "preserve it as task-local durable context" in prompt
    assert "do not investigate or execute that follow-up work" in prompt
    assert "Python workflow will decide whether to create another task" not in prompt
    assert "Worked" in prompt
    assert "Blocked" in prompt
    assert "requires credentials" in prompt


def test_task_prompt_critic_approves_initial_draft():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append((role, prompt, tools, system_prompt))
        if role == "task_prompt_builder":
            return '{"prompt":"approved execution","memory_ids":[],"tools":[],"shell_commands":[]}'
        if role == "task_prompt_critic":
            return '{"approved":true,"feedback":[]}'
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    prompt_spec = controller._build_task_prompt(_plan(), _plan().phases[0], task)

    assert prompt_spec["prompt"] == "approved execution"
    assert [call[0] for call in calls] == ["task_prompt_builder", "task_prompt_critic"]
    assert all(call[2] == [] for call in calls)
    assert all(call[3] == "base prompt" for call in calls)
    assert "Proposed task prompt draft" in calls[1][1]
    assert "approved execution" in calls[1][1]
    activities = [
        event for event in controller.runtime.callback_handler.events
        if event.get("type") == "workflow_activity"
    ]
    assert [(event["role"], event["cycle"], event["cycle_total"]) for event in activities] == [
        ("task_prompt_builder", 1, 1),
        ("task_prompt_builder", 1, 1),
        ("task_prompt_critic", 1, 1),
        ("task_prompt_critic", 1, 1),
    ]


def test_task_prompt_critic_rejection_runs_revision_until_approved():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    calls = []
    builder_responses = iter(
        [
            '{"prompt":"draft","memory_ids":[],"tools":[],"shell_commands":[]}',
            '{"prompt":"revised","memory_ids":[],"tools":["module_probe"],"shell_commands":[]}',
        ]
    )
    critic_responses = iter(
        [
            '{"approved":false,"feedback":["Add the relevant validation tool"]}',
            '{"approved":true,"feedback":[]}',
        ]
    )

    def text_runner(role, prompt, tools, system_prompt):
        calls.append((role, prompt))
        if role == "task_prompt_builder":
            return next(builder_responses)
        if role == "task_prompt_critic":
            return next(critic_responses)
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 3}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    prompt_spec = controller._build_task_prompt(_plan(), _plan().phases[0], task)

    assert prompt_spec["prompt"] == "revised"
    assert [role for role, _prompt in calls] == [
        "task_prompt_builder",
        "task_prompt_critic",
        "task_prompt_builder",
        "task_prompt_critic",
    ]
    assert "Add the relevant validation tool" in calls[2][1]
    assert "Apply feedback only when it is consistent" in calls[2][1]
    activities = [
        event for event in controller.runtime.callback_handler.events
        if event.get("type") == "workflow_activity"
    ]
    assert [(event["role"], event["cycle"], event["cycle_total"]) for event in activities] == [
        ("task_prompt_builder", 1, 3),
        ("task_prompt_builder", 1, 3),
        ("task_prompt_critic", 1, 3),
        ("task_prompt_critic", 1, 3),
        ("task_prompt_builder", 2, 3),
        ("task_prompt_builder", 2, 3),
        ("task_prompt_critic", 2, 3),
        ("task_prompt_critic", 2, 3),
    ]


def test_task_prompt_final_rejection_marks_task_partial_failure_without_execution():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        if role == "task_prompt_builder":
            return '{"prompt":"unsafe draft","tools":[]}'
        if role == "task_prompt_critic":
            return '{"approved":false,"feedback":["Prompt broadens scope outside target boundary"]}'
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda *args: pytest.fail("task executor must not run"),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert calls == ["task_prompt_builder", "task_prompt_critic"]
    assert state.tasks[0].status == "partial_failure"
    assert "Prompt broadens scope outside target boundary" in state.tasks[0].status_reason
    assert controller.runtime.callback_handler.events[-1] == {
        "type": "task_done",
        "task_uid": "active",
        "title": "Active",
        "status": "partial_failure",
        "status_reason": state.tasks[0].status_reason,
    }


def test_task_prompt_repairable_rejection_runs_one_bounded_repair():
    task = Task(task_uid="active", title="Active", objective="capture response evidence", phase=1, status="active")
    calls = []
    builders = iter([
        '{"prompt":"discard response body","tools":[],"shell_commands":["id"]}',
        '{"prompt":"capture response body with an explicit http URL","tools":[],"shell_commands":[]}',
    ])
    critics = iter([
        '{"approved":false,"repairable":true,"feedback":["The response body is required for artifact evidence"]}',
        '{"approved":true,"repairable":false,"feedback":[]}',
    ])

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        if role == "task_prompt_builder":
            return next(builders)
        if role == "task_prompt_critic":
            return next(critics)
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    prompt_spec = controller._build_task_prompt(_plan(), _plan().phases[0], task)

    assert prompt_spec["prompt"] == "capture response body with an explicit http URL"
    assert calls == ["task_prompt_builder", "task_prompt_critic", "task_prompt_builder", "task_prompt_critic"]


def test_task_prompt_repairable_rejection_uses_deterministic_template():
    task = Task(task_uid="active", title="Active", objective="capture response evidence", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        if role == "task_prompt_builder":
            return '{"prompt":"unsafe","tools":[],"shell_commands":[]}'
        if role == "task_prompt_critic":
            return '{"approved":false,"repairable":true,"feedback":["Add an explicit URL scheme"]}'
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
    )

    with pytest.raises(workflow_mod.TaskPromptBuildError) as error:
        controller._build_task_prompt(_plan(), _plan().phases[0], task)
    fallback = controller._deterministic_task_prompt_spec(_plan(), _plan().phases[0], task, error.value)

    assert fallback["memory_ids"] == []
    assert fallback["memory_indices"] == []
    assert fallback["tools"] == []
    assert fallback["shell_commands"] == []
    assert "Assigned task" in fallback["prompt"]
    assert len(state.tasks) == 1
    assert calls == ["task_prompt_builder", "task_prompt_critic", "task_prompt_builder", "task_prompt_critic"]


def test_invalid_task_prompt_critic_json_retries_then_uses_deterministic_template():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        if role == "task_prompt_builder":
            return '{"prompt":"draft","tools":[]}'
        return '{"approved":"yes","feedback":[]}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(
            env_ints={
                "CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1,
                "CYBER_WORKFLOW_JSON_RETRIES": 1,
            }
        ),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt: calls.append(("executor", prompt)),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert calls[:3] == ["task_prompt_builder", "task_prompt_critic", "task_prompt_critic"]
    assert calls[3][0] == "executor"
    assert "Prompt-build fallback reason" in calls[3][1]
    assert "Supplemental Shell Commands" not in calls[3][1]
    assert state.tasks[0].status == "partial_failure"
    fallback = next(
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "task_prompt_fallback"
    )
    assert fallback["reason"] == "task_prompt_critic_invalid_json"
    assert fallback["fallback"] == "deterministic_controller_template"


def test_invalid_task_prompt_builder_json_uses_deterministic_template():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    executed_prompts = []

    controller = MultiAgentWorkflowController(
        runtime=_runtime(
            env_ints={
                "CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1,
                "CYBER_WORKFLOW_JSON_RETRIES": 0,
            }
        ),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: (
            '{"status":"partial_failure","reason":"no acceptance recorded"}'
            if role == "task_evaluator" else "not json"
        ),
        work_runner=lambda role, prompt, tools, system_prompt: executed_prompts.append(prompt),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert executed_prompts
    assert "Assigned task" in executed_prompts[0]
    assert state.tasks[0].status == "partial_failure"
    fallback = next(
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "task_prompt_fallback"
    )
    assert fallback["reason"] == "task_prompt_builder_invalid_json"


def test_task_prompt_critic_max_tokens_uses_deterministic_template():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    executed_prompts = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"draft","tools":[]}'
        if role == "task_prompt_critic":
            raise MaxTokensReachedException("max_tokens")
        return '{"status":"partial_failure","reason":"no acceptance recorded"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(
            env_ints={
                "CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1,
                "CYBER_WORKFLOW_JSON_RETRIES": 0,
            }
        ),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt: executed_prompts.append(prompt),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert executed_prompts
    fallback = next(
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "task_prompt_fallback"
    )
    assert fallback["reason"] == "task_prompt_critic_max_tokens"


def test_task_prompt_critic_transport_failure_uses_deterministic_template():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    executed_prompts = []

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"draft","tools":[]}'
        if role == "task_prompt_critic":
            raise RuntimeError("event loop unavailable")
        return '{"status":"partial_failure","reason":"no acceptance recorded"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(
            env_ints={
                "CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1,
                "CYBER_WORKFLOW_JSON_RETRIES": 0,
            }
        ),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt: executed_prompts.append(prompt),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert executed_prompts
    fallback = next(
        event for event in controller.runtime.callback_handler.events
        if event["type"] == "task_prompt_fallback"
    )
    assert fallback["reason"] == "task_prompt_critic_unavailable"


def test_controller_converts_invalid_evaluator_status_to_partial_failure():
    state = FakeState(_plan(), tasks=[Task(task_uid="active", title="Active", objective="run", phase=1, status="active")])

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute"}'
        if role == "task_evaluator":
            return '{"status":"finished_successfully","reason":"bad status"}'
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt: None,
        max_iterations=1,
    )

    decision = controller._evaluate_task(state.plan, state.plan.phases[0], state.tasks[0])

    assert decision.status == "partial_failure"
    event = next(event for event in controller.runtime.callback_handler.events if event["type"] == "evaluator_fallback")
    assert event["source"] == "schema_validation"


def test_json_text_agent_retries_invalid_json_by_default():
    calls = []
    task = Task(task_uid="active", title="Active", objective="run", phase=1, status="active")

    def text_runner(role, prompt, tools, system_prompt):
        calls.append((role, prompt))
        if len(calls) == 1:
            return "not json"
        return '{"prompt":"fixed prompt","tools":[]}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    prompt_spec = controller._build_task_prompt(_plan(), _plan().phases[0], task)

    assert prompt_spec["prompt"] == "fixed prompt"
    assert [role for role, _prompt in calls] == ["task_prompt_builder", "task_prompt_builder"]
    assert "previous response could not be parsed" in calls[1][1]
    assert "not json" not in calls[1][1]
    assert "Original prompt:" in calls[1][1]


def test_json_text_agent_retry_can_be_disabled():
    runtime = _runtime()
    runtime.config_manager = SimpleNamespace(getenv_int=lambda _name, default=0: 0)
    task = Task(task_uid="active", title="Active", objective="run", phase=1, status="active")
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        return "not json"

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    with pytest.raises(WorkflowInvariantError, match="task_prompt_builder returned invalid JSON after 1 attempt"):
        controller._build_task_prompt(_plan(), _plan().phases[0], task)

    assert calls == ["task_prompt_builder"]


def test_json_text_agent_raises_after_configured_retries():
    runtime = _runtime()
    runtime.config_manager = SimpleNamespace(getenv_int=lambda _name, default=0: 2)
    task = Task(task_uid="active", title="Active", objective="run", phase=1, status="active")
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        return "not json"

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    with pytest.raises(WorkflowInvariantError, match="task_prompt_builder returned invalid JSON after 3 attempt"):
        controller._build_task_prompt(_plan(), _plan().phases[0], task)

    assert calls == ["task_prompt_builder", "task_prompt_builder", "task_prompt_builder"]


def test_json_text_agent_retries_valid_json_with_invalid_status_and_falls_back():
    task = Task(task_uid="active", title="Active", objective="run", phase=1, status="active")
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        return '{"status":"finished_successfully","reason":"bad status"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    decision = controller._evaluate_task(_plan(), _plan().phases[0], task)

    assert decision.status == "partial_failure"
    assert calls == ["task_evaluator", "task_evaluator"]


def test_state_store_mutates_plan_and_tasks_with_fake_client(monkeypatch):
    stored = {}
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Recon", status="done"),
            PlanPhase(id=2, title="Validate", status="pending"),
        ],
        constraints=["Use read-only validation"],
        assessment_complete=True,
    )
    task = Task(task_uid="t1", title="Task", objective="Do it", phase=2, status="pending", created_at="1")

    class Client:
        def get_active_plan(self, operation_id=None):
            return stored.get("plan")

        def store_plan(self, plan, operation_id=None):
            stored["plan"] = plan

        def list_tasks(self, phase=None, status=None):
            tasks = [stored["task"]] if "task" in stored else []
            if phase is not None:
                tasks = [item for item in tasks if item.phase == phase]
            if status:
                tasks = [item for item in tasks if item.status in status]
            return tasks

        def store_task(self, task):
            stored["task"] = task

    monkeypatch.setattr(workflow_mod, "get_memory_client", lambda silent=True: Client())
    store = WorkflowStateStore("OP_TEST")
    stored["plan"] = plan
    stored["task"] = task

    reopened = store.reopen_plan(plan)
    assert reopened.assessment_complete is False
    assert reopened.constraints == ["Use read-only validation"]
    assert reopened.current_phase == 2
    assert [phase.status for phase in reopened.phases] == ["done", "active"]

    activated_phase = store.activate_phase(reopened, 2)
    assert activated_phase.current_phase == 2
    assert activated_phase.constraints == ["Use read-only validation"]
    assert [phase.status for phase in activated_phase.phases] == ["done", "active"]

    phase_one_done = store.mark_phase(activated_phase, 1, "done")
    finished_phase = store.mark_phase(phase_one_done, 2, "blocked")
    assert finished_phase.assessment_complete is False
    assert finished_phase.constraints == ["Use read-only validation"]

    generated_plan = store.create_plan_from_dict(
        {
            "objective": "generated",
            "constraints": "  Keep generated work in scope  ",
            "current_phase": 1,
            "phases": [{"id": 1, "title": "Generated", "status": "pending", "criteria": "Evidence exists"}],
        }
    )
    assert generated_plan.constraints == ["Keep generated work in scope"]
    assert generated_plan.phases[0].status == "active"

    assert store.list_tasks(phase=2, status=["pending"]) == [task]
    active_task = store.activate_task(task)
    assert active_task.status == "active"
    deferred_task = store.defer_task(active_task, "hard cap")
    assert deferred_task.status == "pending"
    assert deferred_task.status_reason == "hard cap"
    done_task = store.mark_task(store.activate_task(deferred_task), "partial_failure", "soft cap")
    assert done_task.status == "partial_failure"
    assert done_task.status_reason == "soft cap"
    finished_phase = store.mark_phase(finished_phase, 2, "done")
    assert finished_phase.phases[1].status == "partial_failure"
    assert finished_phase.assessment_complete is False

    with pytest.raises(ValueError, match="phase status"):
        store.mark_phase(activated_phase, 2, "active")
    with pytest.raises(ValueError, match="task status"):
        store.mark_task(task, "pending")


def test_state_store_reopens_all_actionable_phases_in_plan_order(monkeypatch):
    stored = {
        "plan": OperationPlan(
            objective="assess",
            current_phase=3,
            total_phases=3,
            phases=[
                PlanPhase(id=1, title="Recon", status="done"),
                PlanPhase(id=2, title="Validate", status="partial_failure"),
                PlanPhase(id=3, title="Exploit", status="blocked"),
            ],
            assessment_complete=True,
        ),
        "tasks": [
            Task(task_uid="terminal", title="Done", objective="done", phase=1, status="done", created_at="1"),
            Task(task_uid="first", title="Resume validation", objective="resume", phase=2, status="pending", created_at="2"),
            Task(task_uid="second", title="Resume exploit", objective="resume", phase=3, status="pending", created_at="3"),
        ],
    }

    class Client:
        def get_active_plan(self, operation_id=None):
            return stored["plan"]

        def store_plan(self, plan, operation_id=None):
            stored["plan"] = plan

        def list_tasks(self, phase=None, status=None):
            tasks = stored["tasks"]
            if phase is not None:
                tasks = [task for task in tasks if task.phase == phase]
            if status:
                tasks = [task for task in tasks if task.status in status]
            return tasks

    monkeypatch.setattr(workflow_mod, "get_memory_client", lambda silent=True: Client())
    store = WorkflowStateStore("OP_TEST")

    reopened = store.reopen_plan(stored["plan"])

    assert reopened.current_phase == 2
    assert reopened.assessment_complete is False
    assert [phase.status for phase in reopened.phases] == ["done", "active", "pending"]

    advanced = store.mark_phase(reopened, 2, "done")

    assert advanced.current_phase == 3
    assert advanced.assessment_complete is False
    assert [phase.status for phase in advanced.phases] == ["done", "partial_failure", "active"]


def test_state_store_ensures_active_phase_for_actionable_terminal_plan(monkeypatch):
    stored = {
        "plan": OperationPlan(
            objective="assess",
            current_phase=3,
            total_phases=3,
            phases=[
                PlanPhase(id=1, title="Recon", status="done"),
                PlanPhase(id=2, title="Validate", status="partial_failure"),
                PlanPhase(id=3, title="Closure", status="partial_failure"),
            ],
            assessment_complete=False,
        ),
        "tasks": [
            Task(
                task_uid="pending",
                title="Resume validation",
                objective="resume",
                phase=2,
                status="pending",
            )
        ],
    }

    class Client:
        def get_active_plan(self, operation_id=None):
            return stored["plan"]

        def store_plan(self, plan, operation_id=None):
            stored["plan"] = plan

        def list_tasks(self, phase=None, status=None):
            tasks = stored["tasks"]
            if phase is not None:
                tasks = [task for task in tasks if task.phase == phase]
            if status:
                tasks = [task for task in tasks if task.status in status]
            return tasks

    monkeypatch.setattr(workflow_mod, "get_memory_client", lambda silent=True: Client())
    store = WorkflowStateStore("OP_TEST")

    resumed = store.ensure_active_phase(stored["plan"])

    assert resumed.current_phase == 2
    assert resumed.assessment_complete is False
    assert [phase.status for phase in resumed.phases] == ["done", "active", "partial_failure"]


def test_state_store_does_not_reopen_completed_plan_without_actionable_work(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Recon", status="done"),
            PlanPhase(id=2, title="Validate", status="partial_failure"),
        ],
        assessment_complete=True,
    )

    class Client:
        def get_active_plan(self, operation_id=None):
            return plan

        def store_plan(self, plan, operation_id=None):
            pytest.fail("completed plan should not be rewritten")

        def list_tasks(self, phase=None, status=None):
            return []

    monkeypatch.setattr(workflow_mod, "get_memory_client", lambda silent=True: Client())

    reopened = WorkflowStateStore("OP_TEST").reopen_plan(plan)

    assert reopened is plan
    assert reopened.assessment_complete is True
    assert [phase.status for phase in reopened.phases] == ["done", "partial_failure"]


def test_memory_summary_returns_compact_memories_and_handles_errors():
    state = FakeState(_plan())
    state.client = SimpleNamespace(
        list_memories=lambda **kwargs: [
            {"id": "m1", "memory": "x" * 1200, "metadata": {"category": "finding"}},
            {"memory_id": "m2", "content": "short", "metadata": {}},
        ]
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    memories = controller._memory_summary()
    assert memories.startswith("memories[2]{id,category,source,origin_operation,evidence_status,memory}:\n")
    assert "  m1,finding,," in memories
    assert "\n  m2,general,,unknown,prior_operation_advisory,short\n" in memories

    first_line = memories.splitlines()[1]
    first_parts = first_line.strip().split(",", maxsplit=5)
    assert first_parts[0] == "m1"
    assert len(first_parts[5]) == 1000

    state.client = SimpleNamespace(list_memories=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fail")))
    assert controller._memory_summary() == (
        "memories[0]{id,category,source,origin_operation,evidence_status,memory}:"
    )


def test_prompt_memory_filter_excludes_bookkeeping_and_retains_evidence():
    task_acceptance = {
        "id": "acceptance",
        "memory": "Task acceptance says route mapping was complete.",
        "metadata": {"category": "observation", "source": "task_acceptance"},
    }
    publication_marker = {
        "id": "publication",
        "memory": "Copied acceptance ledger.",
        "metadata": {"category": "observation", "publication_key": "task_acceptance:task-1:digest"},
    }
    records = [
        task_acceptance,
        publication_marker,
        {"id": "plan", "memory": "Plan record", "metadata": {"category": "plan"}},
        {"id": "task", "memory": "Task record", "metadata": {"source": "task"}},
        {"id": "observation", "memory": "The login form accepts POST.", "metadata": {"category": "observation"}},
        {"id": "finding", "memory": "Stored XSS candidate.", "metadata": {"category": "finding"}},
        {"id": "knowledge", "memory": "Use explicit response status capture.", "metadata": {"category": "knowledge"}},
        {"id": "validation", "memory": "Candidate needs independent reproduction.", "metadata": {"category": "validation_failure"}},
    ]
    state = FakeState(_plan())
    state.client = SimpleNamespace(
        list_memories=lambda **kwargs: records,
        get_memory_by_id=lambda memory_id: next((item for item in records if item["id"] == memory_id), None),
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assert [record["id"] for record in controller._prompt_memory_records()] == [
        "observation",
        "finding",
        "knowledge",
        "validation",
    ]
    prompt = controller._task_creator_prompt(_plan(), _plan().phases[0])
    assert "Task acceptance says route mapping was complete" not in prompt
    assert "Copied acceptance ledger" not in prompt
    assert "Plan record" not in prompt
    assert "Task record" not in prompt
    assert "The login form accepts POST" in prompt
    assert "Stored XSS candidate" in prompt
    assert "Use explicit response status capture" in prompt
    assert "Candidate needs independent reproduction" in prompt
    assert "Controller-owned task history, acceptance ledgers" in prompt
    selected = controller._selected_memory_context(["acceptance", "finding"])
    assert "Task acceptance says route mapping was complete" not in selected
    assert "Stored XSS candidate" in selected


def test_operation_health_provider_predicts_current_phase_from_inventory_fanout(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Inventory", status="done"),
            PlanPhase(id=2, title="Assessment", status="active"),
        ],
    )
    inventory_task = Task(
        task_uid="inventory",
        title="Build inventory",
        objective="Build a bounded inventory",
        acceptance=_acceptance("inventory"),
        phase=1,
        status="done",
        evidence=["artifact:artifacts/inventory.json"],
    )
    route_task = Task(
        task_uid="route-1",
        title="Assess route one",
        objective="Assess one route",
        acceptance=_artifact_acceptance("route-one"),
        phase=2,
        status="done",
    )
    state = FakeState(plan, [inventory_task, route_task])
    state.acceptance_results[inventory_task.task_uid] = []
    runtime = _runtime(env_floats={"CYBER_INCOMPLETE_HEALTH_CAP": 0.75})
    runtime.prompt_token_limit = 48_000
    monkeypatch.setattr(workflow_mod, "canonical_artifact_reference", lambda reference: reference)
    monkeypatch.setattr(
        workflow_mod,
        "_load_inventory_manifest",
        lambda reference: ({"items": [{"id": "one"}, {"id": "two"}]}, "hash"),
    )
    monkeypatch.setattr(
        workflow_mod,
        "_coverage_route_groups",
        lambda manifest, prompt_token_limit: [
            ("target-1", "endpoint", "https://example.test/one", ["one"]),
            ("target-1", "endpoint", "https://example.test/two", ["two"]),
        ],
    )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )
    health = runtime.callback_handler.operation_health_provider()

    assert controller._current_phase_task_prediction(plan) == {
        "source_phase": 1,
        "target_phase": 2,
        "expected_tasks": 2,
        "confidence": "high",
        "basis": "inventory_manifest_fanout",
    }
    assert health["prediction"]["expected_tasks"] == 2
    assert health["prediction"]["actual_tasks"] == 1
    assert health["prediction"]["coverage"] == 0.5
    assert health["health_cap"] == 0.75


def test_operation_health_provider_freezes_last_assessment_health_during_reporting():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Inventory", status="done"),
            PlanPhase(id=2, title="Assessment", status="active"),
        ],
    )
    state = FakeState(
        plan,
        [
            Task("done", "Done", "done", 1, "done"),
            Task("pending", "Pending", "pending", 2, "pending"),
        ],
    )
    runtime = _runtime()
    diagnostics = {"progress_percent": 75, "assessment_active": True}
    runtime.callback_handler.operation_health_budget_diagnostics = lambda: dict(diagnostics)
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assessment_health = runtime.callback_handler.operation_health_provider()
    diagnostics.update({"progress_percent": 99, "assessment_active": False})
    reporting_health = runtime.callback_handler.operation_health_provider()

    assert assessment_health["coverage_feasibility"]["penalty_applied"] is True
    assert reporting_health == assessment_health
    assert controller._last_assessment_health == assessment_health


def test_shared_prompt_memory_marks_prior_operation_as_advisory():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
    )

    rendered = controller._render_memories([
        {
            "id": "current",
            "memory": "current proof",
            "operation_id": "OP_TEST",
            "metadata": {"category": "observation", "operation_id": "OP_TEST"},
        },
        {
            "id": "prior",
            "memory": "prior claim",
            "operation_id": "OP_PRIOR",
            "metadata": {"category": "observation", "operation_id": "OP_PRIOR"},
        },
    ])

    assert "origin_operation,evidence_status" in rendered
    assert "OP_TEST,current_operation_evidence" in rendered
    assert "OP_PRIOR,prior_operation_advisory" in rendered
    assert "cannot satisfy current-task acceptance" in controller._memory_prompt_guidance()


@pytest.mark.parametrize(
    ("repair", "error"),
    [
        ({"kind": "execution", "evidence_gaps": []}, "requires evidence_gaps"),
        ({"kind": "invented", "evidence_gaps": ["gap"]}, "none, acceptance, or execution"),
        ({"kind": "execution", "evidence_gaps": [""]}, "non-empty strings"),
        ({"kind": "execution", "evidence_gaps": ["one", "two", "three", "four"]}, "at most three"),
    ],
)
def test_task_evaluator_rejects_invalid_repair_contract(repair, error):
    with pytest.raises(workflow_mod.WorkflowInvariantError, match=error):
        MultiAgentWorkflowController._evaluator_repair_from_data({"repair": repair})


@pytest.mark.parametrize(
    ("repair_resolves", "expected_status", "expected_outcomes"),
    [
        (True, "done", ["scheduled", "completed"]),
        (False, "partial_failure", ["scheduled", "exhausted"]),
    ],
)
def test_evaluator_execution_defect_gets_one_evidence_producing_actor_cycle(
    repair_resolves,
    expected_status,
    expected_outcomes,
):
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1})
    task = Task(task_uid="active", title="Protocol test", objective="Test all required operations", phase=1,
                status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    state.acceptance_results[task.task_uid] = [AcceptanceResult(
        criterion_id=task.acceptance.criteria[0].id,
        status="satisfied",
        disposition="assessed_negative",
        summary="One operation tested",
        evidence_refs=("artifact:artifacts/first-operation.txt",),
    )]
    actor_prompts = []
    evaluator_calls = 0

    def text_runner(role, prompt, tools, system_prompt):
        nonlocal evaluator_calls
        if role == "task_prompt_builder":
            return '{"prompt":"run protocol test","tools":[]}'
        if role == "task_evaluator":
            evaluator_calls += 1
            if evaluator_calls == 1:
                return json.dumps({
                    "status": "partial_failure",
                    "reason": "A required operation was not executed.",
                    "instructions": "Execute the missing operation and capture durable evidence.",
                    "repair": {"kind": "execution", "evidence_gaps": ["required operation B"]},
                })
            if repair_resolves:
                return json.dumps({"status": "done", "reason": "Both operations now have durable evidence."})
            return json.dumps({
                "status": "partial_failure",
                "reason": "Operation B evidence is still invalid.",
                "instructions": "Capture a valid operation B result.",
                "repair": {"kind": "execution", "evidence_gaps": ["required operation B"]},
            })
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        actor_prompts.append(prompt)
        if len(actor_prompts) == 1:
            return workflow_mod.TaskExecutorCycleResult(text="first operation already accepted", outcomes=[])
        return workflow_mod.TaskExecutorCycleResult(
            text="captured operation B",
            outcomes=[ToolOutcome(
                sequence=1,
                tool_use_id="operation-b",
                tool_name="shell",
                success=True,
                correctable=False,
                input_summary="run operation B",
                output_summary="artifact:artifacts/operation-b.txt",
                artifact_refs=("artifact:artifacts/operation-b.txt",),
            )],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(actor_prompts) == 2
    assert "required operation B" in actor_prompts[1]
    assert "Required tool call: record_task_acceptance" not in actor_prompts[1]
    repair_events = [event for event in runtime.callback_handler.events
                     if event["type"] == "evaluator_execution_repair"]
    assert [event["outcome"] for event in repair_events] == expected_outcomes
    assert state.tasks[0].status == expected_status


@pytest.mark.parametrize("repair_resolves", [True, False])
def test_evaluator_synthesis_repair_is_closure_only_and_bounded(repair_resolves):
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 1})
    task = Task(task_uid="active", title="Protocol summary", objective="Summarize the assessed protocol", phase=1,
                status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    state.acceptance_results[task.task_uid] = [AcceptanceResult(
        criterion_id=task.acceptance.criteria[0].id,
        status="satisfied",
        disposition="observation",
        summary="The protocol was assessed.",
        evidence_refs=("artifact:artifacts/protocol.txt",),
    )]
    evaluator_calls = 0
    actor_prompts = []
    policies = []

    def text_runner(role, prompt, tools, system_prompt):
        nonlocal evaluator_calls
        if role == "task_prompt_builder":
            return '{"prompt":"assess protocol","tools":[]}'
        if role == "task_evaluator":
            evaluator_calls += 1
            if evaluator_calls == 1:
                return json.dumps({
                    "status": "partial_failure",
                    "reason": "The terminal summary omitted the required protocol hypothesis.",
                    "instructions": "State the supported hypothesis from the retained protocol artifact.",
                    "repair": {"kind": "acceptance", "evidence_gaps": []},
                })
            if repair_resolves:
                return '{"status":"done","reason":"The terminal hypothesis is now explicit."}'
            return json.dumps({
                "status": "partial_failure",
                "reason": "The terminal hypothesis remains unsupported.",
                "instructions": "State the supported hypothesis from the retained protocol artifact.",
                "repair": {"kind": "acceptance", "evidence_gaps": []},
            })
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        actor_prompts.append(prompt)
        policies.append(run_policy)
        return workflow_mod.TaskExecutorCycleResult(text="The retained evidence supports the required hypothesis.", outcomes=[])

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert len(actor_prompts) == 2
    assert "Evaluator Terminal Synthesis Repair" in actor_prompts[1]
    assert "Do not perform discovery, scanning, new testing" in actor_prompts[1]
    assert policies[1].min_tool_calls == 0
    assert policies[1].allow_text_final_after_tools is True
    repair_events = [
        event for event in runtime.callback_handler.events
        if event["type"] == "evaluator_synthesis_repair"
    ]
    assert [event["outcome"] for event in repair_events] == (
        ["scheduled"] if repair_resolves else ["scheduled", "exhausted"]
    )
    assert state.tasks[0].status == ("done" if repair_resolves else "partial_failure")


def test_phase_cap_extends_immediate_producer_for_candidate_dependent_phase():
    plan = OperationPlan(
        objective="assess",
        current_phase=2,
        total_phases=4,
        phases=[
            PlanPhase(id=1, title="Inventory", status="done"),
            PlanPhase(id=2, title="Discovery", status="active"),
            PlanPhase(id=3, title="Validation", status="pending", requires_finding_candidates=True),
            PlanPhase(id=4, title="Report", status="pending"),
        ],
    )
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(plan),
    )

    context = controller._phase_budget_cap_context(plan, plan.phases[1])

    assert context == {
        "base_cap": 50.0,
        "effective_cap": 75.0,
        "extended": True,
        "dependent_phase_id": 3,
        "extension_reason": "future_phase_requires_finding_candidates",
    }


def test_phase_cap_does_not_extend_when_candidate_already_exists():
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=2,
        phases=[
            PlanPhase(id=1, title="Discovery", status="active"),
            PlanPhase(id=2, title="Validation", status="pending", requires_finding_candidates=True),
        ],
    )
    state = FakeState(plan)
    state.finding_records.append({"finding_uid": "finding-1", "resolution": None})
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    assert controller._phase_budget_cap(plan, plan.phases[0]) == 50.0


def test_controller_inventory_filter_rejects_dependents_of_out_of_scope_endpoint():
    endpoint = {"id": "outside", "target_id": "target-1", "kind": "endpoint",
                "value": "https://outside.test/path"}
    top_level = {"id": "parameter-1", "target_id": "target-1", "kind": "parameter",
                 "value": "q", "endpoint_id": "outside"}
    nested = {"id": "parameter-2", "target_id": "target-1", "kind": "parameter",
              "value": "page", "attributes": {"endpoint_id": "outside"}}
    unlinked = {"id": "parameter-3", "target_id": "target-1", "kind": "parameter", "value": "safe"}
    rejected_endpoint_ids = {endpoint["id"]}

    assert MultiAgentWorkflowController._inventory_source_endpoint_id(top_level) in rejected_endpoint_ids
    assert MultiAgentWorkflowController._inventory_source_endpoint_id(nested) in rejected_endpoint_ids
    assert MultiAgentWorkflowController._inventory_source_endpoint_id(unlinked) == ""


def test_manifest_prerequisite_recovery_failure_when_manifest_missing_or_invalid():
    plan = _plan()
    task = Task(
        task_uid="manifest-task",
        title="Inventory task",
        objective="Generate manifest",
        phase=1,
        status="active",
    )
    state = FakeState(plan, tasks=[task], acceptance_complete=False)
    cycle_calls = 0

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"build manifest","tools":[]}'
        raise AssertionError(f"unexpected role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        nonlocal cycle_calls
        cycle_calls += 1
        if cycle_calls == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="Attempted acceptance",
                outcomes=[
                    ToolOutcome(
                        sequence=1,
                        tool_use_id="acceptance-1",
                        tool_name="record_task_acceptance",
                        success=False,
                        correctable=True,
                        input_summary="record acceptance",
                        output_summary="Validation failed: submitted manifest file does not exist at path",
                    )
                ],
            )
        return workflow_mod.TaskExecutorCycleResult(
            text="Manifest recovery attempt with no valid artifact",
            outcomes=[],
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    controller._run_task(plan, plan.phases[0], task)

    assert cycle_calls == 2
    assert state.tasks[0].status == "partial_failure"
    assert (
        "Inventory manifest prerequisite recovery did not produce a valid manifest artifact."
        in state.tasks[0].status_reason
    )


def test_repeat_tool_loop_recurrence_partial_failure_when_no_replacement_created():
    plan = _plan()
    task = Task(
        task_uid="looping-task",
        title="Loop task",
        objective="Run looping task",
        phase=1,
        status="active",
        acceptance=_acceptance("crit-1"),
    )
    # Put a prior replacement task into state so _create_reasoning_loop_replacement_task returns None
    prior_replacement = Task(
        task_uid="prior-repl",
        title="Prior",
        objective="Obj",
        phase=1,
        status="pending",
        replacement_of="looping-task",
        acceptance=_acceptance("crit-prior"),
    )
    state = FakeState(plan, tasks=[task, prior_replacement], acceptance_complete=False)
    cycle_calls = 0

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"run loop task","tools":[]}'
        raise AssertionError(f"unexpected role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        nonlocal cycle_calls
        cycle_calls += 1
        return workflow_mod.TaskExecutorCycleResult(
            text="Reasoning loop detected",
            outcomes=[],
            repeat_loop_detected=True,
            repeat_loop_signature="loop-signature-1",
            repeat_loop_reason="Repeated identical tool call pattern",
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 3}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )

    controller._run_task(plan, plan.phases[0], task)

    assert cycle_calls == 2
    updated_task = next(t for t in state.tasks if t.task_uid == "looping-task")
    assert updated_task.status == "partial_failure"
    assert (
        "A tool-call loop recurred after the one bounded changed-action recovery; "
        "no equivalent replacement task could be created."
    ) in updated_task.status_reason


def test_execution_prerequisite_recovery_failure_when_execution_ids_unresolved(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Request endpoint",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Request the assigned endpoint",
            "/api/test",
        )],
    )
    task = Task(
        task_uid="execution-repair-task",
        title="Execution repair",
        objective="Request endpoint",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Bounded request",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["http_request"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task], acceptance_complete=False)
    cycle_calls = 0

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"request endpoint","tools":[]}'
        raise AssertionError(f"unexpected role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        nonlocal cycle_calls
        cycle_calls += 1
        if cycle_calls == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="Attempted acceptance",
                outcomes=[
                    ToolOutcome(
                        sequence=1,
                        tool_use_id="acceptance-1",
                        tool_name="record_task_acceptance",
                        success=False,
                        correctable=True,
                        input_summary="record acceptance",
                        output_summary="Execution evidence is required before acceptance.",
                    )
                ],
            )
        return workflow_mod.TaskExecutorCycleResult(
            text="Execution turn without producing valid receipts",
            outcomes=[],
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )
    monkeypatch.setattr(controller, "_has_valid_required_output", lambda *args: True)

    controller._run_task(plan, plan.phases[0], task)

    assert cycle_calls == 2
    assert state.tasks[0].status == "partial_failure"
    assert (
        "Execution prerequisite recovery did not produce task-local proof for: criterion-1-execution-1"
        in state.tasks[0].status_reason
    )


def test_output_prerequisite_recovery_failure_when_output_ids_unresolved(monkeypatch):
    plan = OperationPlan(
        objective="assess",
        current_phase=1,
        total_phases=1,
        phases=[PlanPhase(id=1, title="Recon", status="active")],
        targets=[OperationTarget("target-1", "http://target.test", "network")],
    )
    criterion = AcceptanceCriterion(
        id="criterion-1",
        description="Request endpoint",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement(
            "criterion-1-execution-1",
            "Request the assigned endpoint",
            "/api/test",
        )],
    )
    task = Task(
        task_uid="output-repair-task",
        title="Output repair",
        objective="Request endpoint",
        phase=1,
        status="active",
        target_ids=["target-1"],
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="Bounded request",
                source_refs=["target:target-1", "plan:phase-1"],
                procedure={
                    "methods": ["http_request"],
                    "limits": {"max_requests": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[criterion],
        ),
    )
    state = FakeState(plan, tasks=[task], acceptance_complete=False)
    cycle_calls = 0

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"request endpoint","tools":[]}'
        raise AssertionError(f"unexpected role: {role}")

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        nonlocal cycle_calls
        cycle_calls += 1
        if cycle_calls == 1:
            return workflow_mod.TaskExecutorCycleResult(
                text="Attempted acceptance",
                outcomes=[
                    ToolOutcome(
                        sequence=1,
                        tool_use_id="acceptance-1",
                        tool_name="record_task_acceptance",
                        success=False,
                        correctable=True,
                        input_summary="record acceptance",
                        output_summary="Execution evidence is required before acceptance.",
                    )
                ],
            )
        return workflow_mod.TaskExecutorCycleResult(
            text="Output recovery turn without output",
            outcomes=[],
        )

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 2}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
        executor_session_factory=retained_work_runner(work_runner),
    )
    monkeypatch.setattr(controller, "_has_valid_required_output", lambda *args: False)

    controller._run_task(plan, plan.phases[0], task)

    assert cycle_calls == 2
    assert state.tasks[0].status == "partial_failure"
    assert (
        "Output prerequisite recovery did not produce the required task-local output for: criterion-1-execution-1"
        in state.tasks[0].status_reason
    )


def test_task_prompt_build_error_negative_cases():
    with pytest.raises(TaskPromptBuildError, match="task prompt tools must be a list of strings"):
        MultiAgentWorkflowController._validated_selection_list("invalid", "tools")

    with pytest.raises(TaskPromptBuildError, match="task prompt tools must contain only non-empty strings"):
        MultiAgentWorkflowController._validated_selection_list(["   "], "tools")

    with pytest.raises(TaskPromptBuildError, match="task prompt tools must contain only non-empty strings"):
        MultiAgentWorkflowController._validated_selection_list([123], "tools")

    runtime = _runtime()
    runtime.config.available_tools = ["katana"]
    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
    )
    task = Task(task_uid="t1", title="Task 1", objective="Objective", phase=1, status="active")
    with pytest.raises(TaskPromptBuildError, match="task prompt tools contains unknown or unavailable selection"):
        controller._normalize_task_prompt_spec(
            {"prompt": "do something", "tools": ["completely_unknown_tool_xyz"]},
            task,
        )

    with pytest.raises(TaskPromptBuildError, match="task prompt shell_commands contains unavailable tool or command"):
        controller._normalize_task_prompt_spec(
            {"prompt": "do something", "shell_commands": ["completely_unknown_shell_command_xyz"]},
            task,
        )

    with pytest.raises(TaskPromptBuildError, match="task prompt must be a non-empty string"):
        controller._normalize_task_prompt_spec(
            {"prompt": "   ", "tools": []},
            task,
        )


def test_extract_json_object_and_extract_result_text_edge_cases():
    assert extract_json_object('{"key": "value", "count": 42}') == {"key": "value", "count": 42}
    assert extract_json_object('```json\n{"key": "value"}\n```') == {"key": "value"}

    with pytest.raises(ValueError):
        extract_json_object("not valid json")

    with pytest.raises(ValueError):
        extract_json_object("[1, 2, 3]")

    with pytest.raises(ValueError):
        extract_json_object('"string value"')

    assert extract_result_text(None) == ""
    assert extract_result_text("simple string") == "simple string"

    msg_obj = SimpleNamespace(message={"content": [{"text": "line1"}, {"text": "line2"}]})
    assert extract_result_text(msg_obj) == "line1\nline2"

    msg_empty = SimpleNamespace(message={"content": []})
    assert extract_result_text(msg_empty) == str(msg_empty)

    content_obj = SimpleNamespace(content=[{"text": "block1"}, {"text": "block2"}])
    assert extract_result_text(content_obj) == "block1\nblock2"

    assert extract_result_text(999) == "999"


def test_reconcile_pending_controller_acceptance_branches():
    plan = _plan()
    task = Task(task_uid="reconcile-task", title="Reconcile", objective="Test reconcile", phase=1, status="active")
    state = FakeState(plan, tasks=[task])
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
    )

    decision, ignored = controller._reconcile_pending_controller_acceptance(plan, task, lambda **kwargs: None, [], [])
    assert decision is None
    assert ignored == set()

    task_empty_pending = replace(task, recovery_context={"pending_controller_acceptance": {}})
    state.tasks = [task_empty_pending]
    decision, ignored = controller._reconcile_pending_controller_acceptance(
        plan, task_empty_pending, lambda **kwargs: None, [], []
    )
    assert decision is None
    assert ignored == set()

    crit = AcceptanceCriterion(
        id="crit-1",
        description="Criterion with exec requirement",
        evidence_requirements=[EvidenceRequirement(kind="artifact")],
        execution_requirements=[ExecutionRequirement("req-1", "Execute", "target:t1")],
    )
    task_with_req = replace(
        task,
        acceptance=AcceptanceContract(
            mode="outcome",
            basis=AcceptanceBasis(
                kind="procedure",
                description="p",
                source_refs=["target:t1"],
                procedure={
                    "methods": ["test"],
                    "limits": {"max_items": 1},
                    "stop_condition": "first_limit_reached",
                    "gap_policy": "record_unassessed",
                    "output_kind": "artifact",
                },
            ),
            criteria=[crit],
        ),
        recovery_context={
            "pending_controller_acceptance": {
                "status": "satisfied",
                "disposition": "observation",
                "summary": "done",
                "evidence_refs": [],
            }
        },
    )
    state.tasks = [task_with_req]
    decision, ignored = controller._reconcile_pending_controller_acceptance(
        plan, task_with_req, lambda **kwargs: None, [], []
    )
    assert decision is None
    assert ignored == set()

    def failing_tool(**kwargs):
        raise ValueError("Replay validation failed")

    task_satisfied_req = replace(
        task_with_req,
        recovery_context={
            "pending_controller_acceptance": {
                "status": "satisfied",
                "disposition": "observation",
                "summary": "done",
                "evidence_refs": [],
            },
            "execution_evidence_receipts": {"req-1": ["artifact:1"]},
        },
    )
    state.tasks = [task_satisfied_req]
    decision, ignored = controller._reconcile_pending_controller_acceptance(
        plan, task_satisfied_req, failing_tool, [], []
    )
    assert decision is not None
    assert decision.status == "partial_failure"
    assert "Controller acceptance replay was rejected" in decision.reason

    def succeeding_tool(**kwargs):
        return {"status": "ok"}

    matching_outcome = ToolOutcome(
        sequence=1,
        tool_use_id="failed-acc-1",
        tool_name="record_task_acceptance",
        success=False,
        correctable=True,
        input_summary=json.dumps({
            "status": "satisfied",
            "disposition": "observation",
            "summary": "done",
            "evidence_refs": [],
        }),
        output_summary="Failed initially",
    )
    state.tasks = [task_satisfied_req]
    decision, ignored = controller._reconcile_pending_controller_acceptance(
        plan, task_satisfied_req, succeeding_tool, [], [matching_outcome]
    )
    assert decision is None
    assert ignored == {"failed-acc-1"}
    refreshed = next(t for t in state.tasks if t.task_uid == task.task_uid)
    assert "pending_controller_acceptance" not in refreshed.recovery_context


def test_synthesis_repair_cycle_resets_flags():
    runtime = _runtime(env_ints={"CYBER_WORKFLOW_TASK_EXECUTION_CYCLES": 2})
    task = Task(task_uid="synthesis-reset", title="Synthesis reset", objective="Assess", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task], acceptance_complete=False)
    state.acceptance_results[task.task_uid] = [AcceptanceResult(
        criterion_id=task.acceptance.criteria[0].id,
        status="satisfied",
        disposition="observation",
        summary="Assessed protocol",
        evidence_refs=("artifact:artifacts/protocol.txt",),
    )]
    evaluator_calls = 0
    cycle_count = 0

    def text_runner(role, prompt, tools, system_prompt):
        nonlocal evaluator_calls
        if role == "task_prompt_builder":
            return '{"prompt":"assess","tools":[]}'
        if role == "task_evaluator":
            evaluator_calls += 1
            if evaluator_calls == 1:
                return json.dumps({
                    "status": "partial_failure",
                    "reason": "Missing synthesis summary.",
                    "instructions": "Synthesize findings.",
                    "repair": {"kind": "acceptance", "evidence_gaps": []},
                })
            return '{"status":"done","reason":"Synthesis accepted."}'
        raise AssertionError(role)

    def work_runner(role, prompt, tools, system_prompt, run_policy):
        nonlocal cycle_count
        cycle_count += 1
        return workflow_mod.TaskExecutorCycleResult(
            text="Synthesized summary evidence",
            outcomes=[],
        )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert cycle_count == 2
    assert evaluator_calls == 2
    assert state.tasks[0].status == "done"
