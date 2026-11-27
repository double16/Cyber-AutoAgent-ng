from conftest import plan_payload

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
            excerpt = prompt.split("Original request: ", 1)[1].split("\n", 1)[0]
            return json.dumps(plan_payload({"components": [
                {**item, "request_excerpt": excerpt} for item in components
            ]}))
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
    assert receipt["component_purpose"] == "JWT handling" and receipt["requirements"][0]["id"] == "requirement-1"


def test_delivered_revisions_are_deduplicated_across_distinct_methods(scripted_service):
    state = scripted_service
    revision = state.publish()
    state.selection = revision
    plan_runner(state, [component(), component("other", "Token validation")])
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


def test_duplicate_methods_fail_before_resolution(scripted_service):
    state = scripted_service
    plan_runner(state, [component(), component()])
    with pytest.raises(ValueError, match="Duplicate methodology"):
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
            return json.dumps(plan_payload({"components": [
                {**item, "request_excerpt": method_request} for item in items
            ]}))
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
    assert "input_references" in planners[0] and "not procedural steps" in reviews[0]
    assert "Original request: " + method_request in reviews[1]


def test_unreferenced_plan_exhausts_correction_before_research(scripted_service):
    state = scripted_service
    original = state.service.runner
    prompts = []

    def runner(role, prompt, tools):
        if role == "technique_planner":
            prompts.append(prompt)
            payload = plan_payload({"components": [component()]})
            payload["components"][0]["input_references"] = ["unknown:1"]
            return json.dumps(payload)
        return original(role, prompt, tools)

    state.service.runner = runner
    with pytest.raises(ValueError, match="Unknown input reference"):
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
            return json.dumps(plan_payload({"components": [
                {**component(str(i), method, requirements), "request_excerpt": method}
                for i, method in enumerate(names)
            ]}))
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


@pytest.mark.parametrize("source,excerpt,valid", [
    ("original_request", "HTTP controls", True),
    ("phase_guidance", "Form plausible hypotheses", True),
    ("methodology_restriction", "Read only", True),
    ("methodology_restriction", "No exploitation", True),
    ("original_request", "Cite excerpts from the ORIGINAL request", False),
    ("phase_guidance", "Unprovided phase instruction", False),
    ("methodology_restriction", "Unprovided restriction", False),
])
def test_requirement_origins_reference_supplied_inputs(source, excerpt, valid):
    from modules.techniques.bundles import requirement_origin_error
    from modules.techniques.context import MethodologyContext

    context = MethodologyContext(phase_contexts=["hypothesis_generation"],
                                methodology_constraints=["Read only"],
                                module_technique_policy=[{"module": "example", "restriction": "No exploitation"}])
    payload = component(requirements=[{"id": "r", "description": "Design controls",
                                      "origins": [{"source": source, "excerpt": excerpt}]}])
    plan = Plan(components=[payload])
    assert bool(requirement_origin_error(plan, "HTTP controls", context)) is not valid


def test_legacy_requirements_parse_but_new_plans_need_origins():
    from modules.techniques.bundles import requirement_origin_error

    plan = Plan(components=[component()])
    assert plan.components[0].requirements[0].origins == []
    assert "requires an exact" in requirement_origin_error(plan, "JWT handling", None)


@pytest.mark.parametrize("recover", [True, False])
def test_missing_requirement_origin_rejected_before_research(scripted_service, monkeypatch, recover):
    state = scripted_service
    original = state.service.runner
    attempts = []

    def run(role, prompt, tools):
        if role == "technique_planner":
            attempts.append(prompt)
            payload = {"components": [{**component(), "request_excerpt": "JWT handling"}]}
            if recover and len(attempts) == 2:
                payload = plan_payload(payload)
            return json.dumps(payload)
        return original(role, prompt, tools)

    state.service.runner = run
    get = Mock(return_value=("revision", "Validated guidance"))
    monkeypatch.setattr(state.service, "get", get)
    if recover:
        assert state.service.resolve_bundle("JWT handling").delivered
        get.assert_called_once()
    else:
        with pytest.raises(ValueError, match="planning failed"):
            state.service.resolve_bundle("JWT handling")
        get.assert_not_called()
    assert len(attempts) == 2
    assert "Validation failure" in attempts[1]


def test_planning_sources_exclude_controller_instructions():
    from modules.techniques.bundles import planning_sources

    sources = planning_sources("HTTP authentication", LookupContext(
        phase_contexts=["analysis"], methodology_constraints=["Use passive methods"],
    ))
    assert sources["original_request"] == ["HTTP authentication"]
    assert sources["methodology_restriction"] == ["Use passive methods"]
    assert "Interpret sources" in sources["phase_guidance"][0]
    assert all("Guidance purpose:" not in value for values in sources.values() for value in values)


def test_origin_validation_collects_all_invalid_references():
    from modules.techniques.bundles import requirement_origin_error

    row = component(requirements=[
        {"id": identifier, "description": "Check controls", "origins": [
            {"source": "phase_guidance", "excerpt": "Invented instruction"},
        ]} for identifier in ["first", "second"]
    ])
    errors = requirement_origin_error(Plan(components=[row]), "HTTP controls", None)
    assert "one/first" in errors and "one/second" in errors


def test_controller_derives_metadata_and_collects_each_defect():
    from modules.techniques.bundles import PlannerOutput, input_records, materialize_plan
    from modules.techniques.context import MethodologyContext

    records = input_records("HTTP controls", MethodologyContext(
        phase_contexts=["analysis"], methodology_constraints=["Read only"],
    ))
    payload = {"components": [
        {"request": "HTTP controls", "input_references": ["original_request:1"], "requirements": [
            {"description": "Steps", "kind": "authored_deliverable", "input_references": ["original_request:1"]},
            {"description": "Controls", "kind": "constraint", "input_references": ["methodology_restriction:1"]},
        ]},
    ]}
    plan, findings = materialize_plan(PlannerOutput.model_validate(payload), records)
    assert not findings
    row = plan.components[0]
    assert row.id == "component-1" and row.purpose == row.request
    assert row.request_excerpt == "HTTP controls"
    assert [r.id for r in row.requirements] == ["requirement-1", "requirement-2"]
    assert row.requirements[1].origins[0].excerpt == "Read only"
    payload["components"].append({"request": "HTTP controls", "input_references": ["unknown:1"],
                                  "requirements": [{"description": "Evidence", "kind": "authored_deliverable",
                                                    "input_references": ["unknown:2"]}]})
    _, findings = materialize_plan(PlannerOutput.model_validate(payload), records)
    assert len(findings) == 4
    assert "Duplicate" in findings[0]["problem"]
    assert [r["problem"] for r in findings[1:3]] == [
        "Unknown input reference: unknown:1", "Unknown input reference: unknown:2"]


def test_planner_schema_excludes_controller_fields():
    from modules.techniques.bundles import PlannerOutput

    schema = PlannerOutput.model_json_schema()
    assert set(schema["$defs"]["PlannedComponent"]["properties"]) == {
        "request", "input_references", "requirements"}
    assert set(schema["$defs"]["PlannedRequirement"]["properties"]) == {
        "description", "kind", "grounding", "input_references"}


def test_correction_contains_complete_findings_and_actual_rejected_output(scripted_service, monkeypatch):
    state = scripted_service
    original = state.service.runner
    prompts = []
    events = []
    state.service.events.callback = events.append
    diagnostic = "Independent semantic finding " + "λ,; punctuation " * 120

    def run(role, prompt, tools):
        if role == "technique_planner":
            prompts.append(prompt)
            payload = plan_payload({"components": [component(), component()]})
            for item in payload["components"]:
                item["requirements"][0]["input_references"] = ["bad:1", "bad:2"]
            return json.dumps(payload)
        if role == "technique_plan_reviewer":
            return json.dumps({"approved": False, "reason": diagnostic})
        return original(role, prompt, tools)

    state.service.runner = run
    monkeypatch.setattr(state.service, "get", Mock())
    with pytest.raises(ValueError, match="unresolved request coverage"):
        state.service.resolve_bundle("JWT handling")
    rejected = next(e for e in events if e["type"] == "technique_plan_rejected")
    assert len(rejected["validation_findings"]) == 6
    assert diagnostic in prompts[1]
    assert '"bad:2"' in prompts[1]
    assert "original_request:1" in prompts[1]
    state.service.get.assert_not_called()


def test_schema_correction_reports_individual_defects_and_preserves_inputs(scripted_service, monkeypatch):
    state = scripted_service
    original = state.service.runner
    prompts = []
    events = []
    state.service.events.callback = events.append

    def run(role, prompt, tools):
        if role == "technique_planner":
            prompts.append(prompt)
            if len(prompts) == 1:
                return json.dumps({"components": [{"request": "HTTP controls", "requirements": [
                    {"description": "Controls", "kind": "invented"},
                ]}]})
            return json.dumps(plan_payload({"components": [component(request="HTTP controls")]}))
        return original(role, prompt, tools)

    state.service.runner = run
    monkeypatch.setattr(state.service, "get", Mock(return_value=("revision", "Validated method")))
    assert state.service.resolve_bundle("HTTP controls").delivered
    rejected = next(e for e in events if e["type"] == "technique_plan_rejected")
    assert len(rejected["validation_findings"]) == 3
    assert {r["field"] for r in rejected["validation_findings"]} == {
        "components.0.input_references", "components.0.requirements.0.kind",
        "components.0.requirements.0.input_references"}
    assert '"original_request:1"' in prompts[1]
    assert '"kind": "invented"' in prompts[1]
    assert '"field": "components.0.requirements.0.kind"' in prompts[1]


def test_controller_bounds_persisted_metadata_without_shortening_authoritative_inputs():
    from modules.techniques.bundles import PlannerOutput, input_records, materialize_plan
    from modules.techniques.context import MethodologyContext

    request = "Review HTTP controls; " + "preserve mechanisms, " * 60
    restriction = "Keep these explicit restrictions: " + "λ,;\n" * 900
    records = input_records(request, MethodologyContext(methodology_constraints=[restriction]))
    assert records["methodology_restriction:1"].excerpt == restriction
    output = PlannerOutput.model_validate({"components": [{
        "request": request, "input_references": ["original_request:1"], "requirements": [{
            "description": "Preserve restrictions", "kind": "constraint",
            "input_references": ["methodology_restriction:1"],
        }],
    }]})
    plan, findings = materialize_plan(output, records)
    assert not findings
    component = plan.components[0]
    assert component.request == component.request_excerpt == request
    assert component.purpose == request[:1000]
    assert component.requirements[0].origins[0].excerpt == restriction[:2000]
