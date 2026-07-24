import inspect
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from strands.types.exceptions import MaxTokensReachedException

from modules.agents import multi_agent_workflow as workflow_mod
from modules.agents.cyber_autoagent import build_role_tools
from modules.agents.multi_agent_workflow import (
    MultiAgentWorkflowController,
    WorkflowInvariantError,
    WorkflowStateStore,
    extract_json_object,
    extract_result_text,
)
from modules.config.types import BudgetConfig
from modules.handlers.base import BudgetLimitReached
from modules.handlers.max_token_recovery import MaxTokenClassification
from modules.handlers.tool_recovery import ToolOutcome
from modules.tools.memory import (
    AcceptanceBasis,
    AcceptanceContract,
    AcceptanceCriterion,
    EvidenceRequirement,
    AcceptanceResult,
    OperationPlan,
    OperationTarget,
    PlanPhase,
    Task as TaskModel,
)


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

    def set_operation_health_provider(self, provider):
        self.operation_health_provider = provider


class FakeState:
    def __init__(self, plan, tasks=None, acceptance_complete=True, finding_records=None):
        self.plan = plan
        self.tasks = list(tasks or [])
        self.acceptance_complete = acceptance_complete
        self.acceptance_results = {}
        self.finding_records = list(finding_records or [])
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

    def list_finding_records(self):
        return list(self.finding_records)

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
        task_capture_prompt="task capture prompt",
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
            _tool("mem0_retrieve"),
            _tool("create_tasks"),
        ],
        optional_tools_list=[_tool("mcp_scan"), _tool("module_probe")],
    )


def test_plan_phase_accepts_partial_failure_and_blocked_statuses():
    assert PlanPhase(id=1, title="Phase", status="partial_failure").status == "partial_failure"
    assert PlanPhase(id=2, title="Phase", status="blocked").status == "blocked"
    assert PlanPhase(id=3, title="Phase", status="not_applicable").status == "not_applicable"


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


def test_json_agent_stops_at_configured_limit_after_max_tokens():
    def text_runner(role, prompt, tools, system_prompt):
        raise MaxTokensReachedException("max_tokens")

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=text_runner,
    )

    with pytest.raises(WorkflowInvariantError, match="returned invalid JSON after 2 attempt"):
        controller._run_json_text_agent("phase_evaluator", "original", [], "system")
    assert extract_result_text(SimpleNamespace(content=[{"text": "c"}])) == "c"


def test_role_tools_exclude_plan_task_mutation_and_gate_create_tasks():
    runtime = _runtime()

    default_names = {tool.__name__ for tool in build_role_tools(runtime)}
    assert {"shell", "mem0_retrieve"}.issubset(default_names)
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
    assert runtime.callback_handler.events[:2] == [
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


def test_task_executor_keeps_task_capture_and_uses_tool_completion_policy(monkeypatch):
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")],
    )
    captured = {}
    bound_task_uids = []
    original_factory = workflow_mod.build_record_task_acceptance_tool

    def build_bound_tool(task_uid, task):
        bound_task_uids.append(task_uid)
        return original_factory(task_uid, task)

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
    assert "pending tasks with their own acceptance contracts using `create_tasks`" in captured["prompt"]
    assert "call `record_task_acceptance` with one terminal status" in captured["prompt"]
    assert "Python owns task, phase, and operation state transitions" in captured["prompt"]
    assert "## Tool Selection Policy (Controller-owned)" in captured["prompt"]
    assert "Multiple methods with\noverlapping capabilities may be used" in captured["prompt"]
    assert "neither mandates use nor makes another selected method exclusive" in captured["prompt"]
    assert "module_probe" in captured["tools"]
    assert "create_tasks" in captured["tools"]
    assert "record_task_acceptance" in captured["tools"]
    assert captured["system_prompt"] == "base prompt\n\ntask capture prompt"
    assert captured["policy"].min_tool_calls == 1
    assert captured["policy"].terminal_after_required_tools is True
    assert captured["policy"].allow_text_final_after_tools is False
    assert captured["policy"].ignored_terminal_tool_names == frozenset({"create_tasks"})
    assert captured["policy"].terminal_reason == "task_executor_done"


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


def test_task_execution_finalizes_after_semantic_review_of_atomic_acceptance():
    runtime = _runtime()
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
            return json.dumps(
                {
                    "status": "partial_failure",
                    "reason": "remaining endpoint lacks evidence",
                    "instructions": "Run endpoint validation and store the artifact path.",
                }
            )
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

    assert len(actor_prompts) == 1
    assert actor_prompts[0].startswith("execute active")
    assert "Cycle 1: actor result 1" in evaluator_prompts[0]
    assert lifecycle == [
        ("created", "task_executor", "base prompt\n\ntask capture prompt"),
        ("cleaned", "task_executor"),
    ]
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
    assert "field error 1" in actor_prompts[1]
    assert "field error 2" in actor_prompts[2]
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
                    evidence_requirements=[EvidenceRequirement(kind="memory")],
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
    assert "memories[2]{id,memory}:" in captured["prompt"]
    assert "m2,memory for m2" in captured["prompt"]
    assert "m1,memory for m1" in captured["prompt"]


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


def test_task_executor_rejects_unknown_selected_shell_commands(monkeypatch):
    runtime = _runtime()
    runtime.config.available_tools = ["httpx", "nmap"]
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")],
    )
    worker_called = False
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
        nonlocal worker_called
        worker_called = True

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert worker_called is False
    assert state.tasks[0].status == "partial_failure"
    assert "unavailable tool or command(s): unknown" in state.tasks[0].status_reason


def test_task_executor_omits_shell_command_context_for_missing_or_invalid_selection():
    runtime = _runtime()
    runtime.config.available_tools = ["httpx"]
    runtime.core_tools_list = [_tool("mem0_retrieve")]
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

    assert "memories[0]{id,memory}:" in prompt
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
        controller._task_executor_contract() + "\n\n" + controller._tool_selection_policy()
    )
    assert captured["executor_prompts"][1] == "correct the missing wordlist"
    assert "## Controller-observed tool outcomes" in captured["evaluator_prompt"]
    assert "Could not open /missing.txt" in captured["evaluator_prompt"]
    assert "200 /login.php" in captured["evaluator_prompt"]
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
    assert executor_calls[0].endswith(controller._task_executor_contract() + "\n\n" + controller._tool_selection_policy())
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


def test_task_executor_max_token_recovery_exhaustion_is_partial_failure():
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
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
    )

    controller._run_task(_plan(), _plan().phases[0], state.tasks[0])

    assert len(executor_calls) == 1
    assert state.tasks[0].status == "partial_failure"
    assert "reasoning loop" in state.tasks[0].status_reason


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


def test_controller_emits_task_started_when_activating_pending_task():
    runtime = _runtime()
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="pending", title="Pending", objective="run pending", phase=1, status="pending")],
    )

    controller = MultiAgentWorkflowController(
        runtime=runtime,
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
        work_runner=lambda role, prompt, tools, system_prompt: None,
        max_iterations=1,
    )

    with pytest.raises(WorkflowInvariantError, match="Workflow iteration limit reached"):
        controller.run()

    assert state.tasks[0].status == "active"
    assert runtime.callback_handler.events == [
        {"type": "task_started", "task_uid": "pending", "title": "Pending", "status": "active"},
    ]


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

    assert calls == ["phase_evaluator"]
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

    assert calls == ["phase_evaluator"]
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
    plan_event = controller.runtime.callback_handler.events[1]
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


@pytest.mark.parametrize("response", ['{"status":"continue","reason":"keep going"}', "not json"])
def test_phase_hard_cap_falls_back_to_partial_failure_for_nonterminal_evaluation(response):
    state = FakeState(
        _plan(),
        tasks=[Task(task_uid="pending", title="Pending", objective="run", phase=1, status="pending")],
    )
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        return response

    controller = MultiAgentWorkflowController(
        runtime=_runtime(progress=100, env_ints={"CYBER_WORKFLOW_JSON_RETRIES": 0}),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda *args: pytest.fail("capped task work must not run"),
        max_iterations=1,
    )

    controller.run()

    assert calls == ["phase_evaluator"]
    assert state.plan.phases[0].status == "partial_failure"
    assert state.tasks[0].status == "pending"


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

    assert default_controller.plan_refinement_iterations == 3
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
    assert len(controller.runtime.callback_handler.events) == 1
    assert "Proposed plan draft" in calls[1][1]
    assert '"title": "Recon"' in calls[1][1]


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
    assert len(controller.runtime.callback_handler.events) == 1
    assert "Add durable evidence criteria" in calls[2][1]
    assert "Apply feedback only when it is consistent" in calls[2][1]


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
    assert controller.runtime.callback_handler.events == []


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
    assert controller.runtime.callback_handler.events == []


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
        runtime=_runtime(progress=100),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=work_runner,
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

    controller.run()

    assert state.plan.phases[0].status == "partial_failure"
    assert state.plan.assessment_complete is True
    assert work_calls == [
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

    assert state.plan.phases[0].status == "not_applicable"
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


def test_empty_final_validation_phase_requires_complete_predecessor_work():
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

    assert controller._can_complete_empty_validation_phase(plan, plan.phases[1]) is False
    summary = controller._workflow_coverage_summary(plan)
    assert summary[0]["status_reason"] == "Phase incomplete with 1 actionable task(s) remaining."


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

    controller.run()

    assert len(evaluator_calls) == 2
    assert "Controller Task-Creation Outcome" in evaluator_calls[1]
    assert len(creator_calls) == 3
    assert state.plan.phases[0].status == "partial_failure"


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

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
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
    assert '"snapshot_refs":["artifact:artifacts/inventory.json"]' in captured["prompt"]
    assert "Canonical inventory manifest contract" in captured["prompt"]
    assert "Workflow maps, reports, and arbitrary JSON outputs are artifact evidence" in captured["prompt"]
    assert "unsupported top-level `description` fields" in captured["prompt"]
    example = captured["prompt"].split("```json\n", 1)[1].split("\n```", 1)[0]
    example_task = json.loads(example)["tasks"][0]
    assert "basis_kind" not in example_task
    assert example_task["output_kind"] == "inventory_manifest"
    assert set(example_task["criteria"][0]) == {"description"}
    assert captured["call_count"] == 1


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

    assert outcome.attempts == 5
    assert len(prompts) == 5
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
    assert "Every proposal MUST contain non-empty `title`, `objective`, explicit `methods`" in prompt
    assert "unsupported top-level `description` fields" in prompt
    assert "Stop immediately" in prompt
    assert "Python assigns active phase 1" in prompt
    assert "Never emit `acceptance`, `phase`, `status`, `target_scope`" in prompt
    assert "without violating any plan constraint" in prompt
    assert "plan_constraints[1]{constraint}:" in prompt
    assert "Stay within the authorized target scope" in prompt


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
    assert "## Existing Tasks Across All Phases\ntask[1]" in creator_prompt
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
    assert "Select any reasonably useful optional-tool working set" in prompt
    assert "Overlapping capabilities are allowed" in prompt
    assert "Select any reasonably useful command working set" in prompt
    assert "no single-tool, exclusivity, minimal-selection, or redundancy requirement" in prompt
    assert "Selection makes a command available; it does not require the executor to use it" in prompt
    assert "capability required by the task that supplied native tools do not provide" not in prompt
    assert "mandatory execution guardrail" in prompt
    assert "plan_constraints[1]{constraint}:" in prompt
    assert 'curl -sS -o /dev/null -w "%{http_code} %{url_effective}\\n" <url>' in prompt
    assert "curl -sS -D - -o /dev/null <url>" in prompt
    assert "Do not rely on bare `curl -s <url>` as evidence" in prompt


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
    assert "Use `store_observation` only" in prompt


def test_task_prompt_builder_can_select_published_acceptance_memory():
    state = FakeState(_plan())
    state.client = SimpleNamespace(
        list_memories=lambda **kwargs: [
            {
                "id": "acceptance-memory-1",
                "memory": "Task acceptance for route mapping. Criterion routes [satisfied]: /login returned 200.",
                "metadata": {"category": "observation", "source": "task_acceptance"},
            }
        ]
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

    assert "acceptance-memory-1" in prompt
    assert "/login returned 200" in prompt


def test_task_prompt_critic_rejects_generic_acceptance_summaries():
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

    assert "requires concrete, reusable acceptance summaries" in prompt
    assert "reject" in prompt
    assert "generic completion claims" in prompt


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
    assert "include both a native tool and a shell command for the same capability" in " ".join(prompt.split())
    assert "Core tools are supplied automatically and must not appear in `tools`" in prompt
    assert "There is no single-tool" in prompt
    assert "relevant and available" not in prompt
    assert "Reject a selection only when it has no reasonable relationship to the task" in " ".join(prompt.split())


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
            "tools": ["read_artifact", "shell", "store_observation", "mcp_scan"],
            "shell_commands": ["shell", "store_observation", "module_probe"],
        },
        task,
    )

    assert normalized["tools"] == ["mcp_scan", "module_probe"]
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
    assert "keys prompt, memory_ids, tools, shell_commands" in prompt


def test_shell_command_catalog_is_empty_without_shell_tool(monkeypatch):
    runtime = _runtime()
    runtime.config.available_tools = ["httpx"]
    runtime.core_tools_list = [_tool("mem0_retrieve")]
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
    assert {tool.__name__ for tool in captured["tools"]} == {"read_artifact", "mem0_retrieve"}
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
    assert {tool.__name__ for tool in captured["tools"]} == {"read_artifact", "mem0_retrieve"}
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


def test_evaluator_tools_exclude_shell_and_optional_execution_tools():
    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan()),
        text_runner=lambda role, prompt, tools, system_prompt: "{}",
    )

    assert {tool.__name__ for tool in controller._evaluator_tools()} == {"read_artifact", "mem0_retrieve"}


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
                 status_reason="evidence stored", evidence=["mem0://finding-1"]),
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
    assert "create durable pending tasks" in prompt
    assert "Do not execute newly created follow-up tasks" in prompt
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


def test_task_prompt_final_rejection_marks_task_partial_failure_without_execution():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        if role == "task_prompt_builder":
            return '{"prompt":"unsafe draft","tools":[]}'
        if role == "task_prompt_critic":
            return '{"approved":false,"feedback":["Honor the task boundary"]}'
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
    assert "Honor the task boundary" in state.tasks[0].status_reason
    assert controller.runtime.callback_handler.events[-1] == {
        "type": "task_done",
        "task_uid": "active",
        "title": "Active",
        "status": "partial_failure",
        "status_reason": state.tasks[0].status_reason,
    }


def test_invalid_task_prompt_critic_json_retries_then_marks_partial_failure():
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
        work_runner=lambda *args: pytest.fail("task executor must not run"),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert calls == ["task_prompt_builder", "task_prompt_critic", "task_prompt_critic"]
    assert state.tasks[0].status == "partial_failure"
    assert "task_prompt_critic returned invalid JSON" in state.tasks[0].status_reason


def test_invalid_task_prompt_builder_json_marks_task_partial_failure():
    task = Task(task_uid="active", title="Active", objective="run active", phase=1, status="active")
    state = FakeState(_plan(), tasks=[task])

    controller = MultiAgentWorkflowController(
        runtime=_runtime(
            env_ints={
                "CYBER_WORKFLOW_TASK_PROMPT_REFINEMENT_ITERATIONS": 1,
                "CYBER_WORKFLOW_JSON_RETRIES": 0,
            }
        ),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=lambda role, prompt, tools, system_prompt: "not json",
        work_runner=lambda *args: pytest.fail("task executor must not run"),
    )

    controller._run_task(_plan(), _plan().phases[0], task)

    assert state.tasks[0].status == "partial_failure"
    assert "task_prompt_builder returned invalid JSON" in state.tasks[0].status_reason


def test_controller_rejects_invalid_evaluator_status():
    state = FakeState(_plan(), tasks=[Task(task_uid="active", title="Active", objective="run", phase=1, status="active")])

    def text_runner(role, prompt, tools, system_prompt):
        if role == "task_prompt_builder":
            return '{"prompt":"execute"}'
        if role == "task_evaluator":
            return '{"status":"complete","reason":"bad status"}'
        raise AssertionError(role)

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=state,
        text_runner=text_runner,
        work_runner=lambda role, prompt, tools, system_prompt: None,
        max_iterations=1,
    )

    with pytest.raises(WorkflowInvariantError, match="Invalid workflow decision"):
        controller.run()


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


def test_json_text_agent_does_not_retry_valid_json_with_invalid_status():
    task = Task(task_uid="active", title="Active", objective="run", phase=1, status="active")
    calls = []

    def text_runner(role, prompt, tools, system_prompt):
        calls.append(role)
        return '{"status":"complete","reason":"bad status"}'

    controller = MultiAgentWorkflowController(
        runtime=_runtime(),
        budget=BudgetConfig(max_duration_minutes=60),
        state_store=FakeState(_plan(), tasks=[task]),
        text_runner=text_runner,
    )

    with pytest.raises(WorkflowInvariantError, match="Invalid workflow decision"):
        controller._evaluate_task(_plan(), _plan().phases[0], task)

    assert calls == ["task_evaluator"]


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
    finished_phase = store.mark_phase(finished_phase, 2, "blocked")
    assert finished_phase.assessment_complete is True

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
    assert [phase.status for phase in advanced.phases] == ["done", "done", "active"]


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
    assert memories.startswith("memories[2]{id,memory}:\n")
    assert "  m1," in memories
    assert "\n  m2,short\n" in memories

    first_line = memories.splitlines()[1]
    first_parts = first_line.strip().split(",", maxsplit=2)
    assert first_parts[0] == "m1"
    assert len(first_parts[1]) == 1000

    state.client = SimpleNamespace(list_memories=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fail")))
    assert controller._memory_summary() == "memories[0]{id,memory}:"


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
