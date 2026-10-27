"""Flat planning, partial delivery, and per-revision receipts."""

import json
from unittest.mock import Mock

import pytest

from modules.techniques.bundles import Component, Plan
from modules.techniques.context import LookupContext


def component(identifier="one", request="JWT handling", requirements=None):
    return {"id": identifier, "request": request, "purpose": f"Purpose {identifier}",
            "requirements": requirements or [{"id": "method", "description": request}]}


def plan_runner(state, components):
    original = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_planner":
            return json.dumps({"components": components})
        return original(role, prompt, tools)

    state.service.runner = runner


def test_mixed_bundle_delivers_complete_document_applicability_and_explicit_gaps(scripted_service, monkeypatch):
    state = scripted_service
    plan_runner(state, [component(), component("two", "Network mapping")])
    original = state.service.get

    def get(request, *args, **kwargs):
        if request == "Network mapping":
            raise ValueError("Missing authoritative version coverage; research exhausted")
        return original(request, *args, **kwargs)

    monkeypatch.setattr(state.service, "get", get)
    bundle = state.service.resolve_bundle("JWT and network mapping", LookupContext(), operation="op", task="task")
    assert len(bundle.delivered) == len(bundle.unresolved) == 1
    rendered = bundle.render()
    assert "Incomplete guidance" in rendered and "Missing authoritative version" in rendered
    assert "Assessment: conditional" in rendered and "Check assumptions and controls" in rendered
    uses = state.service.repository.pending_uses("op", "task")
    assert len(uses) == 1
    receipt = json.loads(uses[0]["use_context"])
    assert receipt["component_purpose"] == "Purpose one" and receipt["requirements"][0]["id"] == "method"


def test_equivalent_components_and_delivered_revisions_are_deduplicated(scripted_service):
    state = scripted_service
    revision = state.publish()
    state.selection = revision
    plan_runner(state, [component(), component("duplicate"), component("other", "Token validation")])
    bundle = state.service.resolve_bundle("Token methods", operation="op", task="task")
    assert len(bundle.delivered) == 2
    assert bundle.render().count("Check assumptions and controls") == 1
    assert len(json.loads(state.service.repository.pending_uses("op", "task")[0]["use_context"])["components"]) == 2
    assert {row.revision_id for row in bundle.delivered} == {revision}
    assert len(state.service.repository.pending_uses("op", "task")) == 1
    state.service.evaluate("op", "task", "Guidance used in source analysis")
    assert state.service.repository.get(revision)["score_count"] == 1


def test_total_failure_is_actionable_and_creates_no_receipts(scripted_service, monkeypatch):
    state = scripted_service
    monkeypatch.setattr(state.service, "get", Mock(side_effect=ValueError("Fetch primary version documentation")))
    with pytest.raises(ValueError, match="No technique components resolved.*Fetch primary"):
        state.service.resolve_bundle("Methods", operation="op", task="task")
    assert not state.service.repository.pending_uses("op", "task")


@pytest.mark.parametrize("components,message", [
    ([component(), component()], "Component identifiers"),
    ([component(requirements=[{"id": "same", "description": "a"}, {"id": "same", "description": "b"}])],
     "Requirement identifiers"),
])
def test_duplicate_identifiers_fail_before_resolution(scripted_service, components, message):
    state = scripted_service
    plan_runner(state, components)
    with pytest.raises(ValueError, match=message):
        state.service.resolve_bundle("Methods")
    assert not state.research_calls


def test_plan_review_rejects_lost_or_invented_requirements(scripted_service):
    state = scripted_service
    original = state.service.runner

    def runner(role, prompt, tools):
        if role == "technique_plan_reviewer":
            assert "adds no unrelated requirements" in prompt
            return json.dumps({"approved": False, "reason": "Missing requested CVE validation component"})
        return original(role, prompt, tools)

    state.service.runner = runner
    with pytest.raises(ValueError, match="planning.*Missing requested CVE"):
        state.service.resolve_bundle("Token handling and CVE validation")
    assert not state.research_calls


def test_distinct_delivered_revisions_are_each_evaluated_once(scripted_service, monkeypatch, markdown):
    state = scripted_service
    from modules.techniques.documents import TechniqueDocument

    first = state.publish()
    second = state.service.store.publish(TechniqueDocument(markdown(title="Network")), "module:network", "module:test")
    plan_runner(state, [component(), component("two", "Network mapping")])
    monkeypatch.setattr(state.service, "get", lambda request, *args, **kwargs: (
        (first, state.service.store.render(first)) if request == "JWT handling"
        else (second, state.service.store.render(second))
    ))
    bundle = state.service.resolve_bundle("Two methods", operation="op", task="task")
    assert len(bundle.delivered) == 2
    for _ in range(2):
        state.service.evaluate("op", "task", "Demonstrated contribution from both methods")
    assert all(state.service.repository.get(revision)["score_count"] == 1 for revision in (first, second))
    assert all(json.loads(row["use_context"])["component_purpose"] for row in
               state.service.repository.scored_uses("op", "task"))


def test_bundle_types_reject_recursive_plan():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Plan.model_validate({"components": [{**component(), "components": [component()]}]})
    assert Component.model_validate(component()).id == "one"
