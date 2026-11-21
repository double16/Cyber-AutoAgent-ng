"""Task creation repairs native errors without losing the assigned phase or batch."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from strands.types.exceptions import MaxTokensReachedException

from modules.agents.multi_agent_workflow import MultiAgentWorkflowController, TaskCreationBatch
from modules.config.models.structured_output import StructuredOutputValidationError, validate_structured_json
from modules.config.types import BudgetConfig
from tests.test_multi_agent_workflow import FakeState, Task, _plan, _runtime, _structured_task_payload


def controller_for(monkeypatch, responses, *, corrections=3, subject="https://target.test/route"):
    plan = _plan()
    phase = replace(plan.phases[0], id=4, title="Generate evidence-grounded hypotheses",
                    technique_contexts=("hypothesis_generation",))
    plan = replace(plan, current_phase=4, phases=[phase])
    state = FakeState(plan)
    prompts, submissions, events = [], [], []
    responses = iter(responses)

    def run(_role, prompt, _tools, _system, schema):
        prompts.append(prompt)
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return validate_structured_json(schema, response).model_dump()

    controller = MultiAgentWorkflowController(
        runtime=_runtime(env_ints={"CYBER_TASK_CREATOR_MAX_CORRECTIONS": corrections}),
        budget=BudgetConfig(max_duration_minutes=60), state_store=state,
        structured_runner=run, text_runner=lambda *_: pytest.fail("native errors must not disable native output"),
    )
    batch = TaskCreationBatch(
        1, 1, "artifact:artifacts/inventory.json", (("target-1", "endpoint", subject, ("item-1",)),), 500,
    )
    monkeypatch.setattr(controller, "_task_creation_batches", lambda *_: [batch])
    monkeypatch.setattr(controller, "_emit_workflow_event", events.append)

    def bind(_phase, _batch, repair_guard=None):
        def submit(tasks):
            submissions.extend(tasks)
            state.store_task(Task(task_uid="new", title=tasks[0].title, objective=tasks[0].objective,
                                  phase=phase.id, status="pending"))
            return '{"created_count":1}'
        return submit

    monkeypatch.setattr(controller, "_task_creator_submitter", bind)
    return controller, plan, prompts, submissions, events


@pytest.mark.parametrize("subject", ["https://target.test/route", "tcp://target.test:9000", "/workspace/source.py"])
def test_invalid_role_repair_preserves_phase_batch_and_proposal(monkeypatch, subject):
    payload = _structured_task_payload("Generate controlled hypotheses")
    proposal = payload["tasks"][0]
    proposal.update(task_role="hypothesis", methods=[], limits={},
                    snapshot_refs=["artifact:artifacts/inventory.json"], workstream="hypothesis_generation")
    corrected = deepcopy(payload)
    corrected["tasks"][0]["task_role"] = "mapping"
    controller, plan, prompts, submissions, events = controller_for(
        monkeypatch, [json.dumps(payload), json.dumps(corrected)], subject=subject,
    )

    outcome = controller._create_tasks(plan, plan.phases[0])

    assert outcome.created_count == 1
    assert outcome.attempts == 2
    assert submissions[0].task_role == "mapping"
    assert submissions[0].snapshot_refs == proposal["snapshot_refs"]
    assert prompts[1].startswith(prompts[0])
    assert subject in prompts[1]
    assert "tasks.0.task_role" in prompts[1]
    assert "Generate controlled hypotheses" in prompts[1]
    assert "hypothesis_generation" in prompts[1]
    assert "untrusted data" in prompts[1]
    assert any("task_role" in event.get("failure_summary", "") for event in events)


def test_different_invalid_native_payloads_remain_repairable(monkeypatch):
    first = _structured_task_payload()
    first["tasks"][0]["task_role"] = "hypothesis"
    second = _structured_task_payload("Incorrect restart of mapping")
    second["tasks"][0].pop("methods")
    controller, plan, prompts, submissions, _events = controller_for(
        monkeypatch, [json.dumps(first), json.dumps(second), json.dumps(_structured_task_payload())],
    )
    outcome = controller._create_tasks(plan, plan.phases[0])
    assert outcome.created_count == 1
    assert outcome.attempts == 3
    assert len(submissions) == 1
    assert prompts[0] in prompts[2]
    assert "task_role" in prompts[2]
    assert "procedure methods or snapshot_refs" in prompts[2]


@pytest.mark.parametrize("same_error", [True, False])
def test_duplicate_detection_requires_payload_and_failure_match(monkeypatch, same_error):
    content = '{"tasks":[]}'
    errors = [StructuredOutputValidationError(ValueError("first failure"), content),
              StructuredOutputValidationError(ValueError("first failure" if same_error else "second failure"), content)]
    responses = errors if same_error else [*errors, json.dumps(_structured_task_payload())]
    controller, plan, prompts, _submissions, events = controller_for(monkeypatch, responses)
    outcome = controller._create_tasks(plan, plan.phases[0])
    assert outcome.attempts == (2 if same_error else 3)
    assert outcome.created_count == (0 if same_error else 1)
    assert any(event.get("stop_reason") == "duplicate_submission" for event in events) is same_error
    assert len(prompts) == outcome.attempts


def test_missing_response_does_not_reuse_previous_fingerprint(monkeypatch):
    error = StructuredOutputValidationError(ValueError("same failure"), "bad payload")
    controller, plan, prompts, _submissions, events = controller_for(
        monkeypatch, [error, ValueError("same failure"), ValueError("same failure"),
                      json.dumps(_structured_task_payload())],
    )
    outcome = controller._create_tasks(plan, plan.phases[0])
    assert outcome.created_count == 1
    assert len(prompts) == 4
    assert not any(event.get("stop_reason") for event in events)


def test_malformed_json_feedback_and_attempt_exhaustion_are_visible(monkeypatch):
    controller, plan, prompts, _submissions, events = controller_for(
        monkeypatch, ["not json", "different malformed json"], corrections=1,
    )
    outcome = controller._create_tasks(plan, plan.phases[0])
    assert outcome.created_count == 0
    assert outcome.attempts == 2
    assert "json_invalid" in prompts[1]
    assert "not json" in prompts[1]
    assert events[-1]["stop_reason"] == "attempts_exhausted"
    assert "json_invalid" in events[-1]["failure_summary"]


def test_rejected_response_is_internal_and_event_failure_is_redacted(monkeypatch):
    error = StructuredOutputValidationError(ValueError("password=private-value invalid"), "raw rejected content")
    controller, plan, prompts, _submissions, events = controller_for(monkeypatch, [error], corrections=0)
    controller._create_tasks(plan, plan.phases[0])
    serialized = json.dumps(events)
    assert "private-value" not in serialized
    assert "raw rejected content" not in serialized
    assert "[REDACTED]" in serialized
    assert len(prompts) == 1


def test_token_limit_recovery_retains_prior_corrections_and_discards_partial_output(monkeypatch):
    first = _structured_task_payload()
    first["tasks"][0]["task_role"] = "hypothesis"
    controller, plan, prompts, submissions, _events = controller_for(
        monkeypatch, [json.dumps(first), MaxTokensReachedException("output limit"),
                      json.dumps(_structured_task_payload())],
    )
    outcome = controller._create_tasks(plan, plan.phases[0])
    assert outcome.created_count == 1
    assert outcome.attempts == 3
    assert len(submissions) == 1
    assert prompts[2].startswith(prompts[0])
    assert "task_role" in prompts[2]
    assert "prior response was discarded" in prompts[2]
    assert "task creator reached its model token limit" in prompts[2]


def test_repair_keeps_all_validation_errors_beyond_the_ui_summary_limit(monkeypatch):
    bad = _structured_task_payload()
    bad["tasks"] *= 15
    for proposal in bad["tasks"]:
        proposal["task_role"] = "hypothesis"
    controller, plan, prompts, _submissions, events = controller_for(
        monkeypatch, [json.dumps(bad), json.dumps(_structured_task_payload())],
    )
    assert controller._create_tasks(plan, plan.phases[0]).created_count == 1
    assert "validation_errors[15]" in prompts[1]
    assert "tasks.14.task_role" in prompts[1]
    failure = next(event for event in events if event.get("failure_summary"))
    assert len(failure["failure_summary"]) <= 1015
    assert "tasks.14.task_role" not in failure["failure_summary"]


def test_snapshot_examples_and_batch_repair_match_controller_contract(monkeypatch):
    from jsonschema import Draft202012Validator

    from modules.agents.structured_outputs import TaskProposalBatchOutput
    from modules.tools.memory import _TASK_PROPOSAL_INPUT_SCHEMA, TaskProposal

    controller, plan, _prompts, _submissions, _events = controller_for(monkeypatch, [])
    phase = plan.phases[0]
    batch = TaskCreationBatch(
        1, 1, "artifact:artifacts/inventory.json",
        (("target-1", "endpoint", "https://target.test/input", ("subject",)),), 0,
    )
    prompt = controller._task_creator_prompt(plan, phase, batch)
    example = json.loads(prompt.split("Snapshot shape (use exactly this basis mode):\n```json\n")[1]
                         .split("```", 1)[0])
    proposals = TaskProposalBatchOutput.model_validate(example).tasks
    assert all(proposal.workstream for proposal in proposals)
    schema = _TASK_PROPOSAL_INPUT_SCHEMA["json"]
    for example in schema["$defs"]["TaskProposal"]["examples"]:
        Draft202012Validator(schema).validate({"tasks": [example]})
        proposal = TaskProposal.model_validate(example)
        if proposal.snapshot_refs:
            assert proposal.workstream
    repair = prompt + controller._task_creator_repair_prompt(
        "controller-bound inventory batch requires distinct snapshot proposal workstreams",
        json.dumps({"tasks": [proposals[0].model_dump()] * 2}), phase=phase,
    )
    assert "Remove identical duplicate proposals" in repair
    assert "different intents sharing a workstream still require correction" in repair
    assert "repair the complete batch, including its count when necessary" in repair
