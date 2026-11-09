"""Activity selection uses contracts, never task prose."""
from types import SimpleNamespace as NS

import pytest

from modules.techniques.feedback_activity import FeedbackTaskContext, select_feedback_activity


@pytest.mark.parametrize("kind,requirements,activities,produces,profile", [
    ("standard", ["hypothesis_set"], ["execution"], False, "hypothesis_generation"),
    ("hypothesis_repair", ["hypothesis_set"], [], False, "hypothesis_generation"),
    ("finding_validation", [], ["execution"], False, "verification"),
    ("objective_validation", [], [], False, "verification"),
    ("standard", [], ["execution"], False, "execution"),
    ("standard", [], ["reconnaissance", "analysis"], False, "analysis"),
    ("standard", [], [], True, "hypothesis_generation"),
    ("standard", [], ["vulnerability_verification"], False, "verification"),
    ("standard", [], ["execution", "analysis"], False, "unknown"),
    ("standard", [], ["execution", "future_activity"], False, "unknown"),
    ("finding_validation", ["hypothesis_set"], [], False, "unknown"),
    ("legacy", [], [], False, "unknown"),
])
def test_selection(kind, requirements, activities, produces, profile):
    task = NS(kind=kind, title="Generate hypotheses", objective="Execute verification",
              acceptance=NS(criteria=[NS(evidence_requirements=[NS(kind=k) for k in requirements])]))
    phase = NS(technique_contexts=activities, produces_hypotheses=produces)
    selected = select_feedback_activity(task, phase)
    assert selected.profile == profile
    assert selected.task_kind == kind
    assert profile in selected.render()


def test_missing_phase_and_default_context_are_unknown():
    task = NS(kind="standard", acceptance=NS(criteria=[]))
    assert select_feedback_activity(task).profile == "unknown"
    assert "does not itself imply neutrality" in FeedbackTaskContext().render()


@pytest.mark.parametrize("profile", ["hypothesis_generation", "execution", "verification", "analysis", "unknown"])
def test_evaluator_and_reviewer_receive_identical_activity_guidance(scripted_service, profile):
    state = scripted_service
    state.service.tool("op", "task")(request="Generic methodology")
    context = FeedbackTaskContext(profile=profile, provenance="test_contract")
    state.service.evaluate("op", "task", "Recorded control and next step; no target request", task_context=context)
    prompts = [prompt for role, prompt, _ in state.calls
               if role in {"technique_evaluator", "technique_feedback_reviewer"}]
    assert len(prompts) == 2
    assert all(context.render() in prompt for prompt in prompts)
    assert not state.service.repository.pending_uses("op", "task")


def test_activity_survives_evaluator_correction(scripted_service):
    state = scripted_service
    state.service.tool("op", "task")(request="Generic methodology")
    original = state.service.runner
    context = FeedbackTaskContext(profile="hypothesis_generation", provenance="task_contract")
    calls = []

    def runner(role, prompt, tools):
        if role in {"technique_evaluator", "technique_feedback_reviewer"}:
            calls.append(role)
            assert context.render() in prompt
            if len(calls) == 1:
                return "{}"
        return original(role, prompt, tools)

    state.service.runner = runner
    state.service.evaluate("op", "task", "Recorded next step", task_context=context)
    assert calls == ["technique_evaluator", "technique_evaluator", "technique_feedback_reviewer"]


def test_continuation_uses_each_tasks_own_phase():
    from modules.techniques.integration import reconcile_task_techniques

    calls = []
    service = NS(evaluate=lambda *args, **kwargs: calls.append(kwargs["task_context"]))
    runtime = NS(technique_service=service, operation_id="op")
    task = NS(task_uid="task", phase=1, kind="standard", acceptance=NS(criteria=[]),
              evidence=[], objective="Title cannot select activity", status="done", status_reason="Recorded plan")
    plan = NS(current_phase=2, phases=[
        NS(id=1, technique_contexts=["hypothesis_generation"], produces_hypotheses=True),
        NS(id=2, technique_contexts=["execution"], produces_hypotheses=False),
    ])
    reconcile_task_techniques(runtime, [task], plan=plan)
    assert len(calls) == 1 and calls[0].profile == "hypothesis_generation"
    assert calls[0].phase_activities == ("hypothesis_generation",)
