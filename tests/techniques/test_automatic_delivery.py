"""Automatic delivery is optional, bounded, receipted, and never execution evidence."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from strands.hooks import BeforeModelCallEvent, HookOrder

from modules.techniques import delivery
from modules.techniques.context import LookupContext, MethodologyContext
from modules.techniques.triggers import RecommendationContext


@pytest.fixture
def prepared(scripted_service, tmp_path, monkeypatch):
    service = scripted_service.service
    revision = scripted_service.publish()
    context = RecommendationContext(service, ["hypothesis_generation"], [], tmp_path / "recommendations.json")
    context.event_context = {"operation_id": "op", "task_uid": "task"}
    context.state["recommendations"] = [{"trigger_id": "trigger", "revision_id": revision,
                                         "request": "Generic methodology", "score": 0.9}]
    receipt = {"generic_request": "Generic methodology", "applicability": "conditional",
               "unmet_prerequisites": ["Establish policy"], "phase_contexts": context.contexts}
    select = Mock(return_value=(revision, service.store.render(revision, guidance=True), receipt))
    monkeypatch.setattr(service, "select_existing", select)
    monkeypatch.setattr(delivery, "get_agent_prompt_limits", lambda agent: (1000, 100))
    monkeypatch.setattr(delivery, "safe_estimate_tokens", lambda agent, extra_content=None: 300)
    agent = SimpleNamespace(messages=[{"role": "user", "content": [{"text": "Assigned task"}]}])
    events = []
    service.events.callback = events.append
    return SimpleNamespace(service=service, revision=revision, context=context, select=select,
                           agent=agent, event=SimpleNamespace(agent=agent), events=events)


@pytest.mark.parametrize("profile", ["HTTP response comparison", "TCP reachability", "Filesystem permissions"])
def test_complete_delivery_replay_and_feedback_snapshots(prepared, profile, monkeypatch):
    p = prepared
    p.context.state["recommendations"][0]["request"] = profile
    hook = delivery.AutomaticGuidanceHook(p.context)
    registry = Mock()
    hook.register_hooks(registry)
    registry.add_callback.assert_called_once_with(BeforeModelCallEvent, hook.before_model, order=HookOrder.SDK_LAST)
    hook.before_model(p.event)
    payload = p.agent.messages[0]["content"][-1]["text"]
    assert profile == p.select.call_args.args[0]
    assert isinstance(p.select.call_args.args[1], MethodologyContext)
    assert not isinstance(p.select.call_args.args[1], LookupContext)
    assert p.agent.messages[0]["content"][0]["text"] == "Assigned task"
    assert "not a new task or acceptance requirement" in payload
    assert "conditional" in payload and "Establish policy" in payload
    packet = json.loads(payload.removeprefix(delivery.PREAMBLE))["selected_technique"]
    assert packet["document"] == p.select.return_value[1]
    hook.before_model(p.event)
    assert len(p.agent.messages[0]["content"]) == 2
    p.service.repository.feedback("op", "task", p.revision, 1, "Observed application")
    p.agent.messages = [{"role": "user", "content": [{"text": "Continue"}]}]
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    assert p.agent.messages[0]["content"][-1]["text"] == payload
    assert p.select.call_count == 1
    uses = delivery.task_uses(p.service, "op", "task")
    assert len(uses) == 1 and uses[0]["score"] == 1
    monkeypatch.setattr(p.service.store, "render", Mock(side_effect=AssertionError("Never replace snapshots")))
    runtime = SimpleNamespace(technique_service=p.service, operation_id="op")
    assert delivery.delivered_guidance(runtime, SimpleNamespace(task_uid="task")) == payload
    assert delivery.delivered_guidance(runtime, SimpleNamespace(task_uid="other")) == ""


@pytest.mark.parametrize("limit,estimate", [(None, 300), (1000, None), (399, 300)])
def test_capacity_defers_without_receipts_and_cached_selection_survives_resume(prepared, monkeypatch, limit, estimate):
    p = prepared
    monkeypatch.setattr(delivery, "get_agent_prompt_limits", lambda agent: (limit, 100))
    monkeypatch.setattr(delivery, "safe_estimate_tokens", lambda *args, **kwargs: estimate)
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    assert not delivery.task_uses(p.service, "op", "task")
    assert len(p.agent.messages[0]["content"]) == 1
    monkeypatch.setattr(delivery, "get_agent_prompt_limits", lambda agent: (1000, 100))
    monkeypatch.setattr(delivery, "safe_estimate_tokens", lambda *args, **kwargs: 300)
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    assert len(delivery.task_uses(p.service, "op", "task")) == 1
    assert p.select.call_count == 1


@pytest.mark.parametrize("mode", ["no_leads", "no_match", "selection_error", "inactive", "missing",
                                  "manual", "no_task", "no_user", "receipt_error"])
def test_unavailable_guidance_preserves_task_and_optional_lookup(prepared, monkeypatch, mode):
    p = prepared
    if mode == "no_leads":
        p.context.state["recommendations"] = []
    elif mode == "no_match":
        p.select.return_value = None
    elif mode == "selection_error":
        p.select.side_effect = RuntimeError("Provider unavailable")
    elif mode in {"inactive", "missing"}:
        monkeypatch.setattr(p.service.repository, "get", lambda revision: None if mode == "missing" else {"active": 0})
    elif mode == "manual":
        p.service.repository.use("op", "task", p.revision, {"delivered_documents": ["Manual guidance"]})
    elif mode == "no_task":
        p.context.event_context = {}
    elif mode == "no_user":
        p.agent.messages = [{"role": "assistant", "content": [{"text": "Context"}]}]
    else:
        monkeypatch.setattr(p.service.repository, "use", Mock(side_effect=OSError("Disk unavailable")))
    hook = delivery.AutomaticGuidanceHook(p.context)
    original = json.dumps(p.agent.messages)
    hook.before_model(p.event)
    hook.before_model(p.event)
    assert json.dumps(p.agent.messages) == original
    assert p.select.call_count <= 1
    assert delivery.delivered_guidance(SimpleNamespace(), SimpleNamespace(task_uid="task")) == ""


@pytest.mark.parametrize("content", ["invalid", "null", "[]", '{}', '{"binding":{},"attempted":true}'])
def test_bad_cache_does_not_break_task(prepared, content):
    p = prepared
    p.context.path.with_suffix(".delivery.json").write_text(content)
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    assert len(delivery.task_uses(p.service, "op", "task")) == 1


def test_cache_binding_and_invalid_snapshots(prepared):
    p = prepared
    first = delivery.AutomaticGuidanceHook(p.context)
    first.state["attempted"] = True
    first._save()
    p.context.lookup.methodology_constraints = ["Changed restriction"]
    assert not delivery.AutomaticGuidanceHook(p.context).state["attempted"]
    p.service.repository.use("op", "task", p.revision, {"delivery_mode": delivery.MODE, "delivered_documents": [""]})
    with pytest.raises(ValueError, match="snapshot"):
        delivery.automatic_snapshots(p.service, "op", "task")
    assert delivery.delivered_guidance(SimpleNamespace(technique_service=p.service, operation_id="op"),
                                       SimpleNamespace(task_uid="task")) == ""
    assert p.events[-1]["type"] == "technique_warning"


@pytest.mark.parametrize("enabled", ["false", "true", "yes", "0"])
def test_opt_in_roles_and_default(prepared, monkeypatch, enabled):
    monkeypatch.delenv("TECHNIQUE_AUTO_DELIVERY", raising=False)
    assert not delivery.automatic_delivery_hooks(prepared.context, "task_executor")
    monkeypatch.setenv("TECHNIQUE_AUTO_DELIVERY", enabled)
    hooks = delivery.automatic_delivery_hooks(prepared.context, "task_executor")
    assert bool(hooks) is (enabled in {"true", "yes"})
    for role in ["task_evaluator", "task_prompt_builder", "task_prompt_critic"]:
        assert not delivery.automatic_delivery_hooks(prepared.context, role)
    monkeypatch.setenv("TECHNIQUE_AUTO_DELIVERY", "unknown")
    with pytest.raises(ValidationError):
        delivery.DeliverySettings.from_environment()


def test_existing_selection_never_starts_research_or_authorship(scripted_service):
    p = scripted_service
    p.selection = p.publish()
    p.service.module_technique_policy = [{"restriction": "No discovery"}]
    selected = p.service.select_existing("JWT handling", MethodologyContext(phase_contexts=["hypothesis_generation"]))
    assert selected[0] == p.selection and selected[2]["applicability"] == "conditional"
    assert any("No discovery" in prompt for _, prompt, _ in p.calls)
    assert not p.research_calls
    assert not {"technique_author", "technique_critic"}.intersection(role for role, _, _ in p.calls)


@pytest.mark.parametrize("failure", ["miss", "inactive", "missing", "phase"])
def test_existing_selection_rechecks_lifecycle_and_phase(scripted_service, monkeypatch, failure):
    p = scripted_service
    revision = p.publish()
    monkeypatch.setattr(p.service, "_select", lambda *args: None if failure == "miss" else revision)
    if failure in {"inactive", "missing"}:
        monkeypatch.setattr(p.service.repository, "get", lambda *args: None if failure == "missing" else {"active": 0})
    context = MethodologyContext(phase_contexts=["analysis"] if failure == "phase" else [])
    assert p.service.select_existing("JWT handling", context) is None
    assert not p.research_calls


def test_inactive_delivery_is_retained_for_review_but_not_new_execution(prepared, monkeypatch):
    p = prepared
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    runtime = SimpleNamespace(technique_service=p.service, operation_id="op")
    task = SimpleNamespace(task_uid="task")
    payload = delivery.delivered_guidance(runtime, task, execution=True)
    assert payload
    monkeypatch.setattr(p.service.repository, "get", lambda revision: {"active": 0})
    assert delivery.delivered_guidance(runtime, task, execution=True) == ""
    assert delivery.delivered_guidance(runtime, task) == payload
    p.agent.messages = [{"role": "user", "content": [{"text": "Continue"}]}]
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    assert p.agent.messages[0]["content"] == [{"text": "Continue"}]


def test_pending_selection_uses_current_store_rendering(prepared, monkeypatch):
    p = prepared
    monkeypatch.setattr(delivery, "get_agent_prompt_limits", lambda agent: (100, 100))
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    assert not delivery.task_uses(p.service, "op", "task")
    monkeypatch.setattr(p.service.store, "render", lambda revision, **kwargs: "Current lifecycle rendering")
    monkeypatch.setattr(delivery, "get_agent_prompt_limits", lambda agent: (1000, 100))
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    packet = json.loads(p.agent.messages[0]["content"][-1]["text"].removeprefix(delivery.PREAMBLE))
    assert packet["selected_technique"]["document"] == "Current lifecycle rendering"
    assert p.select.call_count == 1


def test_controller_continuation_packet_is_not_duplicated_by_live_hook(prepared):
    p = prepared
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    packet = delivery.delivered_guidance(SimpleNamespace(technique_service=p.service, operation_id="op"),
                                         SimpleNamespace(task_uid="task"), execution=True)
    p.agent.messages = [{"role": "user", "content": [{"text": "Continuation\n" + packet}]}]
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    assert len(p.agent.messages[0]["content"]) == 1
    assert p.agent.messages[0]["content"][0]["text"].count(delivery.PREAMBLE) == 1
    assert len(delivery.task_uses(p.service, "op", "task")) == 1


@pytest.mark.parametrize("capacity", [None, 0, 5000, 9500, 40000])
def test_execution_replay_fits_whole_packet_or_keeps_base_request(prepared, monkeypatch, capacity):
    from modules.agents import prompt_context

    p = prepared
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    runtime = SimpleNamespace(technique_service=p.service, operation_id="op", prompt_token_limit=capacity,
                              config=SimpleNamespace(model_id="fixture"))
    task = SimpleNamespace(task_uid="task")
    monkeypatch.setattr(prompt_context, "estimate_prompt_tokens", lambda *args, **kwargs: len(
        kwargs.get("messages", [{"content": [{"text": kwargs.get("extra_content", "")}]}])[0]["content"][0]["text"]
    ))
    result = delivery.execution_guidance_prompt(runtime, task, "Required task and evidence", "System", [])
    if capacity == 40000:
        assert delivery.PREAMBLE in result and "selected_technique" in result
    else:
        assert result == "Required task and evidence"


def test_live_capacity_removes_only_optional_packet_without_empty_blocks(prepared, monkeypatch):
    p = prepared
    hook = delivery.AutomaticGuidanceHook(p.context)
    hook.before_model(p.event)
    payload = p.agent.messages[0]["content"][-1]["text"]
    p.agent.messages.extend([
        {"role": "user", "content": [{"toolResult": {"toolUseId": "read", "content": [{"text": "Evidence"}]}}]},
        {"role": "user", "content": [{"text": "Required evidence\n" + payload}]},
        {"role": "user", "content": [{"text": payload}]},
    ])
    monkeypatch.setattr(delivery, "get_agent_prompt_limits", lambda agent: (100, 100))
    hook.before_model(p.event)
    assert p.agent.messages == [
        {"role": "user", "content": [{"text": "Assigned task"}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "read", "content": [{"text": "Evidence"}]}}]},
        {"role": "user", "content": [{"text": "Required evidence\n"}]},
    ]
    assert len(delivery.task_uses(p.service, "op", "task")) == 1


def test_evaluator_prompt_fitting_omits_whole_optional_packet(prepared):
    from modules.agents.multi_agent_workflow import _prompt_optional_headings
    from modules.agents.prompt_context import fit_task_prompt

    p = prepared
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    payload = p.agent.messages[0]["content"][-1]["text"]
    prompt = "Required acceptance and evidence\n" + payload
    fitted = fit_task_prompt(prompt, "", [], "fixture", 1000, 100, lambda details: None,
                             optional_headings=_prompt_optional_headings("task_evaluator"), estimate_input=len)
    assert "Required acceptance and evidence" in fitted
    assert "selected_technique" not in fitted
    assert "Omitted to fit input capacity" in fitted
    assert delivery.execution_guidance_prompt(SimpleNamespace(), SimpleNamespace(task_uid="other"),
                                               "Required task", "System", []) == "Required task"


def test_sdk_delivers_after_fitting_and_persists_before_provider_dispatch(prepared, monkeypatch):
    from strands import Agent
    from strands.hooks import HookProvider
    from strands.models.model import Model

    from modules.techniques.triggers import TechniqueObservationHook

    p = prepared
    order = []
    monkeypatch.setattr(p.context, "flush", lambda: order.append("match"))

    class Budget(HookProvider):
        def register_hooks(self, registry, **kwargs):
            registry.add_callback(BeforeModelCallEvent, self.fit)

        def fit(self, event):
            order.append("fit")

    def estimate(*args, **kwargs):
        assert order[-1] == "fit"
        order.append("supply")
        return 300

    monkeypatch.setattr(delivery, "safe_estimate_tokens", estimate)

    class Provider(Model):
        def __init__(self):
            self.config = {"model_id": "fixture"}
            self.calls = []

        def get_config(self):
            return self.config

        def update_config(self, **kwargs):
            self.config.update(kwargs)

        async def structured_output(self, *args, **kwargs):
            raise AssertionError("No final structured output is needed")

        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            receipt = delivery.task_uses(p.service, "op", "task")[0]
            payload = json.loads(receipt["use_context"])["delivered_documents"][0]
            assert any(payload in block.get("text", "") for row in messages for block in row["content"])
            self.calls.append(json.dumps(messages))
            order.append("provider")
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockDelta": {"delta": {"text": "Guidance available."}, "contentBlockIndex": 0}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "end_turn"}}

    model = Provider()
    agent = Agent(model=model, hooks=[
        Budget(), delivery.AutomaticGuidanceHook(p.context), TechniqueObservationHook(p.context),
    ], callback_handler=None)
    agent("Use the assigned scope.")
    assert order == ["match", "fit", "supply", "provider"]
    assert len(model.calls) == 1


def test_obsolete_generation_not_replayed_but_historical_delivery_retained(prepared):
    p = prepared
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    runtime = SimpleNamespace(technique_service=p.service, operation_id="op")
    task = SimpleNamespace(task_uid="task")
    original = delivery.delivered_guidance(runtime, task)
    assert original
    with p.service.repository.transaction() as connection:
        connection.execute("UPDATE technique_revisions SET origin = 'generated', generation_version = NULL "
                           "WHERE revision_id = ?", (p.revision,))
    assert delivery.delivered_guidance(runtime, task, execution=True) == ""
    assert delivery.delivered_guidance(runtime, task) == original


@pytest.mark.parametrize("purpose", ["HTTP redirect trust", "TCP service identity", "Filesystem ownership"])
def test_automatic_selection_uses_ranked_lead_and_tries_next(prepared, purpose):
    p = prepared
    p.context.lookup.purpose = purpose
    p.context.state["recommendations"] = [
        {"trigger_id": f"t{index}", "request": f"Method {index}"} for index in range(5)
    ]
    chosen = p.select.return_value
    p.select.side_effect = [None, chosen]
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    assert p.select.call_count == 2
    assert "task_context" not in p.select.call_args.kwargs
    assert p.select.call_args.args[0] == "Method 1"
    assert len(delivery.task_uses(p.service, "op", "task")) == 1


def test_automatic_selection_bounds_misses_and_invalidates_task_cache(prepared):
    p = prepared
    p.context.state["recommendations"] = [
        {"trigger_id": f"t{index}", "request": f"Method {index}"} for index in range(5)
    ]
    p.select.return_value = None
    delivery.AutomaticGuidanceHook(p.context).before_model(p.event)
    assert p.select.call_count == 3
    assert not delivery.task_uses(p.service, "op", "task")
    p.context.lookup.purpose = "Changed purpose"
    assert not delivery.AutomaticGuidanceHook(p.context).state["attempted"]




def test_pending_selection_from_previous_contract_is_recomputed(prepared):
    p = prepared
    hook = delivery.AutomaticGuidanceHook(p.context)
    hook.state["binding"]["selection_contract"] = 1
    hook.state["attempted"] = True
    hook._save()
    resumed = delivery.AutomaticGuidanceHook(p.context)
    assert not resumed.state["attempted"]
    resumed.before_model(p.event)
    assert p.select.call_count == 1
