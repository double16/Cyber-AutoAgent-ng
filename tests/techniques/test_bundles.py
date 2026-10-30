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
            return json.dumps({"components": [{**item, "request_excerpt": prompt.split("Original request: ", 1)[1].split("\n", 1)[0]} for item in components]})
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


@pytest.mark.parametrize("method_request", [
    "Validate prerequisites and applicability of CVE-2026-109618",
    "Trace file input, identify constraints, and validate reachability without executing source",
    "Assess TCP service authentication prerequisites and applicability",
])
def test_cohesive_methodology_repairs_step_split(scripted_service, method_request, monkeypatch):
    state = scripted_service
    original = state.service.runner
    planners = []
    reviews = []

    def runner(role, prompt, tools):
        if role == "technique_planner":
            planners.append(prompt)
            items = [component("extract"), component("validate")] if len(planners) == 1 else [component()]
            return json.dumps({"components": [{**item, "request_excerpt": method_request} for item in items]})
        if role == "technique_plan_reviewer":
            reviews.append(prompt)
            return json.dumps({"approved": len(reviews) > 1, "reason": "One workflow split into steps"})
        return original(role, prompt, tools)

    state.service.runner = runner
    monkeypatch.setattr(state.service, "get", lambda *_a, **_k: ("revision", "Validated procedure"))
    bundle = state.service.resolve_bundle(method_request)
    assert len(bundle.delivered) == 1
    assert len(planners) == len(reviews) == 2
    assert "One workflow split into steps" in planners[1]
    assert "request_excerpt" in planners[0] and "not procedural steps" in reviews[0]
    assert "Original request: " + method_request in reviews[1]


def test_unreferenced_plan_exhausts_correction_before_research(scripted_service):
    state = scripted_service
    original = state.service.runner
    prompts = []

    def runner(role, prompt, tools):
        if role == "technique_planner":
            prompts.append(prompt)
            return json.dumps({"components": [{**component(), "request_excerpt": "Invented method"}]})
        return original(role, prompt, tools)

    state.service.runner = runner
    with pytest.raises(ValueError, match="exact original request excerpt"):
        state.service.resolve_bundle("One method")
    assert len(prompts) == 2 and not state.research_calls


def test_planning_separates_methods_and_excludes_instruction_requirements(scripted_service):
    state = scripted_service
    plan_runner(state, [component()])
    original = state.service.runner
    captured = []

    def runner(role, prompt, tools):
        captured.append((role, prompt, tools))
        return original(role, prompt, tools)

    state.service.runner = runner
    state.service.resolve_bundle("JWT handling")
    prompts = [prompt for role, prompt, *_ in captured if role == "technique_planner"]
    assert prompts
    assert "independently produce useful guidance" in prompts[0]
    assert "must not become document requirements" in prompts[0]


@pytest.mark.parametrize("defect", ["combined methods", "omitted SSRF", "invented excerpt deliverable"])
@pytest.mark.parametrize("recover", [False, True])
def test_method_review_correction_is_bounded_and_precedes_execution(scripted_service, monkeypatch, defect, recover):
    state = scripted_service
    original = state.service.runner
    requests = []
    reviews = []
    executed = []
    events = []
    state.service.events.callback = events.append
    request = ("Prompt injection and jailbreak, tool SSRF, JSON mass assignment, CVE validation. "
               "Include preconditions, safe steps, positive results, negative results, required evidence.")
    deliverables = ["preconditions", "safe steps", "positive results", "negative results", "required evidence"]
    requirements = [{"id": f"d{i}", "description": item, "kind": "authored_deliverable"}
                    for i, item in enumerate(deliverables)]
    methods = ["Prompt injection and jailbreak", "tool SSRF", "JSON mass assignment", "CVE validation"]

    def runner(role, prompt, tools):
        if role == "technique_planner":
            requests.append(prompt)
            names = methods if recover and len(requests) == 2 else [request]
            return json.dumps({"components": [{**component(str(i), method, requirements), "request_excerpt": method}
                                               for i, method in enumerate(names)]})
        if role == "technique_plan_reviewer":
            reviews.append(prompt)
            return json.dumps({"approved": recover and len(reviews) == 2,
                               "reason": f"Correct {defect}: preserve four independent methods and user obligations"})
        return original(role, prompt, tools)

    def get(method, *args, **kwargs):
        executed.append(method)
        assert [row.description for row in kwargs["requirements"]] == deliverables
        assert all(row.kind == "authored_deliverable" for row in kwargs["requirements"])
        if method == "tool SSRF":
            raise ValueError("Unsupported outbound mechanism")
        return method, "Validated procedure"

    state.service.runner = runner
    monkeypatch.setattr(state.service, "get", get)
    if recover:
        bundle = state.service.resolve_bundle(request)
        assert len(bundle.delivered) == 3 and len(bundle.unresolved) == 1
        assert executed == methods
        assert len([e for e in events if e["type"] == "technique_plan_accepted"]) == 1
    else:
        with pytest.raises(ValueError, match="unresolved request coverage"):
            state.service.resolve_bundle(request)
        assert not executed and not state.research_calls
        assert not any(e["type"] == "technique_plan_accepted" for e in events)
    assert len(requests) == len(reviews) == 2
    assert defect in requests[1] and "Rejected plan" in requests[1]
    assert "requirements derived from planner instructions" in reviews[1]
    rejected = [e for e in events if e["type"] == "technique_plan_rejected"]
    assert len(rejected) == (1 if recover else 2)
    assert rejected[0]["correction_attempt"] == 0 and defect in rejected[0]["reason"]


def test_split_component_context_keeps_shared_inputs_without_widening_purpose(scripted_service, monkeypatch):
    state = scripted_service
    request = "HTTP controls and TCP service mapping"
    context = LookupContext(phase_contexts=["hypothesis_generation"], purpose=request,
                            subjects=[{"id": "subject"}], capabilities=["source inspection"],
                            constraints=["read only"])
    context.observe("Shared source observation", "artifact:source", "source", "shared")
    frozen = context.model_dump_json()
    captured = []
    plan_runner(state, [component("http", "HTTP controls"), component("tcp", "TCP service mapping")])

    def get(method, supplied, **kwargs):
        captured.append(supplied)
        assert not hasattr(supplied, "purpose")
        assert supplied.phase_contexts == context.phase_contexts
        assert not any(hasattr(supplied, name) for name in ("constraints", "subjects", "observations", "capabilities"))
        assert supplied._request_alignment.original_request == request
        return method, "Validated method"

    monkeypatch.setattr(state.service, "get", get)
    bundle = state.service.resolve_bundle(request, context)
    assert len(bundle.delivered) == 2 and captured[0] is not captured[1]
    captured[0].methodology_constraints.append("component-local change")
    assert captured[1].methodology_constraints == []
    assert context.model_dump_json() == frozen and context._request_alignment is None
