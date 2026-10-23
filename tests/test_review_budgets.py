"""Shared reviewer capacity preserves the semantic correction contract for each role."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, Field
from strands import Agent
from strands.types.exceptions import MaxTokensReachedException

from modules.agents.multi_agent_workflow import MultiAgentWorkflowController
from modules.agents.prompt_context import PromptInputTooLarge, ReviewBudget
from modules.agents.report_agent import ReportExecutionTelemetryHook
from modules.agents.review_contracts import REVIEW_ROLES, required_review_answer_tokens
from modules.config.models.agent_profiles import get_agent_settings_registry
from modules.config.models.invocation import (
    invocation_minimum_output_tokens,
    invocation_output_tokens,
    invocation_reasoning,
    model_output_budget,
)
from modules.config.types import BudgetConfig
from modules.handlers.report_generator import _invoke_budgeted_report_critic, _run_report_critic
from tests.test_live_prompt_fitting import LocalModel
from tests.test_multi_agent_workflow import FakeState, _checklist_response, _plan, _runtime

FEEDBACK = "The assigned requirement lacks a supported observation; capture and cite that observation."


def review_result(role):
    if role == "task_prompt_critic":
        return json.loads(_checklist_response(json.dumps({"issues": [{
            "category": "evidence", "message": FEEDBACK, "tool_names": [],
        }]})))
    if role in {"task_evaluator", "phase_evaluator"}:
        return {"status": "partial_failure", "reason": FEEDBACK, "instructions": FEEDBACK,
                "repair": {"kind": "acceptance", "evidence_gaps": [FEEDBACK]}}
    return {"approved": False, "feedback": [FEEDBACK], "repairable": True}


@pytest.mark.parametrize("role", sorted(REVIEW_ROLES - {"report_critic"}))
@pytest.mark.parametrize("structured", [False, True])
def test_reviewers_keep_semantic_feedback_and_recover_locally(monkeypatch, role, structured):
    monkeypatch.setattr("modules.config.models.capabilities.get_model_output_limit", lambda *_a, **_kw: None)
    runtime = _runtime()
    runtime.prompt_token_limit = 40000
    registry = get_agent_settings_registry()
    before = registry.get_settings(role).to_dict()
    calls = []

    def runner(active_role, prompt, *_args):
        calls.append((invocation_output_tokens(active_role), invocation_reasoning(active_role)))
        assert invocation_minimum_output_tokens(active_role) == 3024
        assert "semantic feedback" in prompt or role == "task_prompt_critic"
        if len(calls) == 1:
            raise MaxTokensReachedException("length")
        result = review_result(role)
        return result if structured else json.dumps(result)

    controller = MultiAgentWorkflowController(
        runtime=runtime, budget=BudgetConfig(max_duration_minutes=60), state_store=FakeState(_plan()),
        text_runner=runner,
    )
    controller.structured_runner = runner if structured else None
    result = controller._run_json_text_agent(role, "Review only the assigned unit.\n", [], "system")
    assert FEEDBACK in json.dumps(result)
    assert calls == [(9024, None), (9024, "none")]
    assert registry.get_settings(role).to_dict() == before
    assert invocation_minimum_output_tokens(role) is None
    exhaustion = [e for e in runtime.callback_handler.events if e["type"] == f"{role}_exhaustion"]
    assert exhaustion[0]["response_visibility"] == "unavailable"


class Row(BaseModel):
    subject_id: str
    reason: str


class RequiredRows(BaseModel):
    rows: list[Row] = Field(min_length=385, max_length=385)


def test_required_output_cardinality_not_inventory_count_controls_capacity():
    floor = required_review_answer_tokens(RequiredRows.model_json_schema(), "fixture")
    assert floor > 385 * 256
    policy = ReviewBudget.resolve(40000, 4096, role="task_evaluator", required_answer_tokens=floor)
    with pytest.raises(PromptInputTooLarge, match="output ceiling"):
        policy.fit("Review required rows", "system", [], "fixture", Mock())
    ordinary = ReviewBudget.resolve(40000, 4096, role="task_evaluator")
    _, tokens = ordinary.fit("Inventory has 385 items; assess the one assigned unit.", "", [], "fixture", Mock())
    assert tokens == 9024


def test_schema_floor_resolves_refs_unions_and_cycles():
    schema = {"$defs": {"row": {"type": "object", "required": ["value"], "properties": {
        "value": {"anyOf": [{"enum": ["retain"]}, {"const": "withdraw"}]},
    }}}, "type": "array", "minItems": 2, "items": {"$ref": "#/$defs/row"}}
    assert required_review_answer_tokens(schema, "fixture") > 512
    schema = {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"}
    assert required_review_answer_tokens(schema, "fixture") == 0
    assert required_review_answer_tokens({"type": "array"}, "fixture") == 0
    assert required_review_answer_tokens({"oneOf": [{"const": "a"}]}, "fixture") > 0


def test_minimum_generation_override_is_scoped_and_validated():
    for floor in (0, -1, 11):
        with pytest.raises(ValueError, match="Minimum output"):
            with model_output_budget("critic", 10, minimum_tokens=floor):
                pass
    with model_output_budget("critic", 9024, minimum_tokens=3024):
        assert invocation_minimum_output_tokens("critic") == 3024
        assert invocation_minimum_output_tokens("other") is None
    assert invocation_minimum_output_tokens("critic") is None


@pytest.mark.parametrize("legacy", [False, True])
def test_production_report_review_scopes_model_and_preserves_feedback(legacy):
    original = LocalModel()
    agent = Agent(model=original, tools=[], load_tools_from_directory=False, callback_handler=None,
                  hooks=[ReportExecutionTelemetryHook()])
    agent._cyber_review_budget = ReviewBudget.resolve(40000, 4096, role="report_critic")
    agent._cyber_review_model_id = "fixture/live"
    agent._prompt_token_limit = 40000
    old_messages = [{"role": "user", "content": [{"text": "Previous unrelated section"}]}]
    agent.messages = old_messages
    models = []

    def factory(tokens, reasoning):
        model = LocalModel({"approved": False, "feedback": [FEEDBACK]})
        model._output_tokens = tokens
        models.append(model)
        return model

    agent._cyber_review_model_factory = factory
    if legacy:
        result, budget = _invoke_budgeted_report_critic(agent, "Review assigned section", "section", None, None, None)
        assert budget.role == "report_critic"
        assert result.stop_reason == "end_turn"
    else:
        assert _run_report_critic(agent, "Review assigned section", 0)["feedback"] == [FEEDBACK]
    assert agent.model is original and agent.messages == old_messages
    assert models[0]._output_tokens == 9024
    sent = models[0].calls[0]
    assert "Previous unrelated section" not in json.dumps(sent)
    assert sent["estimate"] + models[0]._output_tokens + 800 <= 40000


def test_report_live_hook_invokes_fitter():
    fitter = Mock()
    agent = SimpleNamespace(_cyber_prompt_fitter=fitter)
    ReportExecutionTelemetryHook().before_model_call(SimpleNamespace(
        agent=agent, invocation_state={}, projected_input_tokens=10,
    ))
    fitter.assert_called_once_with(agent)


def test_report_factory_freezes_caps_and_creates_local_retry_models(monkeypatch):
    from modules.agents import report_agent

    cfg = SimpleNamespace(getenv=lambda _key, default: default, getenv_int=lambda _key, _default: 6000)
    monkeypatch.setattr(report_agent, "get_config_manager", lambda: cfg)
    monkeypatch.setattr(report_agent, "require_prompt_token_limit", lambda *_a: 40000)
    monkeypatch.setattr(report_agent, "get_model_output_limit", lambda *_a, **_kw: 7000)
    calls = []

    def factory(provider, model_id, role):
        calls.append((role, invocation_output_tokens(role), invocation_reasoning(role)))
        return object()

    agent = SimpleNamespace()
    monkeypatch.setattr(report_agent, "create_strands_model", factory)
    monkeypatch.setattr(report_agent, "create_agent_with_stateful_retry", lambda *_a, **_kw: agent)
    result = report_agent.ReportGenerator.create_report_agent(
        provider="ollama", model_id="fixture", system_prompt="Review", agent_role="report_critic",
    )
    assert result._cyber_review_budget.desired_generation_tokens == 6000
    result._cyber_review_model_factory(5500, "none")
    assert calls == [("report_critic", None, None), ("report_critic", 5500, "none")]
    assert invocation_output_tokens("report_critic") is None


def test_report_budget_exhaustion_restores_state_and_captures_visibility(caplog):
    class Exhausted:
        def __init__(self):
            self.model = object()
            self.messages = [{"role": "user", "content": [{"text": "prior"}]}]
            self.system_prompt = "review"
            self._cyber_review_budget = ReviewBudget.resolve(40000, 4096, role="report_critic")
            self._cyber_review_model_id = "fixture"
            self._cyber_review_model_factory = lambda *_a: object()

        def __call__(self, *_a, **_kw):
            self.messages.append({"role": "assistant", "content": [{"text": "partial decision"}]})
            raise MaxTokensReachedException("length")

    agent = Exhausted()
    old_model = agent.model
    old_messages = list(agent.messages)
    with pytest.raises(MaxTokensReachedException):
        _run_report_critic(agent, "Review", 0)
    assert agent.model is old_model and agent.messages == old_messages
    assert "visibility=visible_output" in caplog.text


def test_report_capacity_failure_does_not_retry_or_trip_provider_circuit():
    from modules.handlers.report_generator import _ReportModelCircuitBreaker

    agent = Mock()
    agent._cyber_review_budget = ReviewBudget.resolve(40000, 4096, 1000, role="report_critic")
    agent._cyber_review_model_id = "fixture"
    agent.system_prompt = "system"
    breaker = _ReportModelCircuitBreaker()
    with pytest.raises(PromptInputTooLarge):
        _run_report_critic(agent, "Review", 2, breaker)
    agent.assert_not_called()
    assert not breaker.tripped


def test_live_review_refits_growing_tool_results_before_reducing_answer_floor(monkeypatch):
    from modules.agents import prompt_context

    def estimate(_model, messages=None, system_prompt=None, tool_specs=None, extra_content=None):
        if extra_content is not None:
            return len(extra_content)
        return sum(len(block.get("text", "")) for message in messages for block in message["content"])

    monkeypatch.setattr(prompt_context, "estimate_prompt_tokens", estimate)
    model = LocalModel()
    model._output_tokens = 9024
    required = "## Evidence\n" + "x" * 34000
    agent = SimpleNamespace(model=model, messages=[{"role": "user", "content": [{"text": required}]}],
                            _prompt_token_limit=40000, system_prompt="system")
    logs = []
    prompt_context.attach_live_prompt_fitter(
        agent, required, logs.append, (), reserve_safety=True, minimum_output_tokens=3024,
    )
    agent._cyber_prompt_fitter(agent)
    assert 3024 <= model._output_tokens < 9024
    assert agent.messages[0]["content"][0]["text"] == required
    agent.messages.append({"role": "tool", "content": [{"text": "t" * 1000}]})
    agent._cyber_prompt_fitter(agent)
    assert model._output_tokens + estimate("fixture", messages=agent.messages) + 800 == 40000
    agent.messages[-1]["content"][0]["text"] += "t" * 10000
    with pytest.raises(PromptInputTooLarge):
        agent._cyber_prompt_fitter(agent)


def test_report_hook_without_live_fitter_is_valid():
    ReportExecutionTelemetryHook().before_model_call(SimpleNamespace(
        agent=SimpleNamespace(), invocation_state={}, projected_input_tokens=0,
    ))


@pytest.mark.parametrize("failure", ["structured_unavailable", "oversized"])
def test_budgeted_report_json_repair_retains_actionable_feedback(failure):
    class Critic:
        def __init__(self):
            self.model = object()
            self.messages = []
            self.system_prompt = "Review assigned section"
            self._cyber_review_budget = ReviewBudget.resolve(40000, 4096, role="report_critic")
            self._cyber_review_model_id = "fixture"
            self._cyber_review_model_factory = lambda *_a: object()
            self.calls = []

        def __call__(self, prompt, **kwargs):
            self.calls.append(prompt)
            if len(self.calls) == 1:
                if failure == "structured_unavailable":
                    raise NotImplementedError("structured output unavailable")
                return SimpleNamespace(structured_output={"approved": False, "feedback": [FEEDBACK] * 1000})
            data = {"approved": False, "feedback": [FEEDBACK]}
            if "structured_output_model" in kwargs:
                return SimpleNamespace(structured_output=data)
            return SimpleNamespace(message={"content": [{"text": json.dumps(data)}]})

    agent = Critic()
    assert _run_report_critic(agent, "Review assigned section", 1)["feedback"] == [FEEDBACK]
    assert len(agent.calls) == 2
    assert all(prompt.count("## Review output budget") == 1 for prompt in agent.calls)


def test_bounded_semantic_reasons_increase_required_answer_capacity():
    from modules.agents.structured_outputs import HypothesisSemanticChecks

    schema = HypothesisSemanticChecks.model_json_schema()
    assert required_review_answer_tokens(schema, "fixture") >= 3 * 133
