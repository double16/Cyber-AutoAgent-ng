"""Preserve methodology subjects and research only missing technical facts."""

import json

import pytest
from conftest import assessment_output

from modules.techniques.context import LookupContext, LookupDecision
from modules.techniques.coverage import Requirement, SourceSelection


def test_single_component_request_is_controller_owned(scripted_service):
    state = scripted_service
    original = state.service.runner

    def run(role, prompt, tools):
        if role == "technique_planner":
            return json.dumps({"components": [{"id": "method", "request": "vulnerability_verification",
                "purpose": "Verification methodology", "request_excerpt": "CVE-2099-1234 methodology",
                "requirements": [{"id": "method", "description": "CVE-2099-1234 methodology"}]}]})
        return original(role, prompt, tools)

    state.service.runner = run
    bundle = state.service.resolve_bundle("CVE-2099-1234 methodology")
    assert bundle.delivered[0].component.request == "CVE-2099-1234 methodology"
    assert any("CVE-2099-1234 methodology" in query for query in state.research_calls)


@pytest.mark.parametrize("subject", ["HTTP CVE-2099-1234", "TCP parser", "source/filesystem parser"])
@pytest.mark.parametrize("repair", [False, True])
def test_applicability_rewrite_is_independently_checked(scripted_service, subject, repair):
    service = scripted_service.service
    prompts = []
    decision = LookupDecision(generic_request="Static-only generic checklist", applicability="conditional",
                              unmet_prerequisites=["Identify version"], reason="Method can help")

    def run(role, prompt, tools):
        prompts.append((role, prompt))
        if role == "technique_request_reviewer":
            return json.dumps({"approved": repair and "Proposed request: " + subject in prompt,
                               "reason": "Preserve subject and remove invented static-only restriction",
                               "references": ["original_request"],
                               "subject_preserved": repair and "Proposed request: " + subject in prompt,
                               "mechanisms_preserved": True, "deliverables_preserved": True,
                               "additions": [], "omissions": []})
        return json.dumps({**decision.model_dump(), "generic_request": subject})

    service.runner = run
    if repair:
        result = service._preserve_request(subject, decision, LookupContext(),
                                          [Requirement(id="method", description=subject)])
        assert result.generic_request == subject
        assert result.applicability == decision.applicability
    else:
        with pytest.raises(ValueError, match="request preservation failed"):
            service._preserve_request(subject, decision, None, [])
    assert len(prompts) == 3
    assert "invented obligations" in prompts[0][1]


def assessment():
    return {"selected_urls": ["https://vendor.example/advisory"],
            "attributions": [{"url": "https://vendor.example/advisory", "reason": "Mechanism research"}],
            "coverage": [{"requirement_id": "method", "source_urls": ["https://vendor.example/advisory"],
                          "explanation": "Affected versions and mechanism", "gaps": []}],
            "reason": "Technical basis established", "authoritative_sufficient": True}


def test_authored_deliverable_explanation_is_canonical():
    requirements = [Requirement(id="method", description="Verification checklist", kind="authored_deliverable")]
    result = SourceSelection.model_validate(assessment())
    documents = [{"url": result.selected_urls[0]}]
    result.coverage[0].explanation = (
        "Documented versions and mechanism support deriving version checks and prerequisite controls"
    )
    result.validate_references(documents, requirements)
    assert result.sufficient
    result.coverage[0].synthesis = "Derive version checks and controls from documented mechanism and prerequisites"
    result.validate_references(documents, requirements)
    assert result.sufficient
    assert Requirement(id="legacy", description="Facts").kind == "factual_support"


def test_canonical_gap_requires_actionable_factual_question():
    from pydantic import ValidationError

    from modules.techniques.coverage import CoverageGap

    with pytest.raises(ValidationError):
        CoverageGap(kind="missing_factual_support", description="No checklist")
    gap = CoverageGap(kind="unresolved_contradiction", description="Versions disagree", claim="Affected versions",
                      blocking_reason="Cannot recommend version comparison",
                      research_question="Which release fixed it?")
    assert gap.research_question == "Which release fixed it?"


def test_review_removes_synthesis_gap_before_second_research_batch(scripted_service):
    state = scripted_service
    original = state.service.runner
    reviewed = []

    def run(role, prompt, tools):
        if role in {"technique_source_assessor", "technique_requirement_assessor"}:
            data = assessment()
            data["coverage"][0]["gaps"] = ["No ready-made checklist"]
            data["authoritative_sufficient"] = True
            return assessment_output(role, prompt, data)
        if role == "technique_requirement_reviewer":
            reviewed.append(prompt)
            data = assessment()
            data["coverage"][0]["synthesis"] = "Construct checklist from supported version and mechanism facts"
            return assessment_output(role, prompt, data)
        return original(role, prompt, tools)

    state.service.runner = run
    state.service.get("Verification checklist", requirements=[
        Requirement(id="method", description="Verification checklist", kind="authored_deliverable")])
    assert len(state.research_calls) == len(reviewed) == 1
    assert "missing prewritten procedure is not a factual claim" in reviewed[0]
    author = next(prompt for role, prompt, _ in state.calls if role == "technique_author")
    assert "Construct checklist" in author


@pytest.mark.parametrize("malformed", [False, True])
def test_bad_request_review_fails_actionably_after_one_correction(scripted_service, malformed):
    service = scripted_service.service
    calls = []

    def run(role, prompt, tools):
        calls.append(role)
        if role == "technique_request_reviewer":
            return "invalid JSON" if malformed else json.dumps({"approved": False, "reason": "Subject omitted",
                               "references": ["original_request"], "subject_preserved": False,
        "mechanisms_preserved": True, "deliverables_preserved": True, "additions": [], "omissions": []})
        return json.dumps({"generic_request": "Still omits subject", "applicability": "conditional",
                           "unmet_prerequisites": ["Check version"], "reason": "Can help"})

    service.runner = run
    decision = LookupDecision(generic_request="Generic phase label", applicability="conditional",
                              unmet_prerequisites=["Check version"], reason="Can help")
    with pytest.raises(ValueError, match="request preservation failed"):
        service._preserve_request("Requested network mechanism", decision, None, [])
    assert calls == ["technique_request_reviewer", "technique_judge", "technique_request_reviewer"]


def test_empty_batches_do_not_repeat_independent_review(scripted_service):
    state = scripted_service
    original = state.service.runner
    batches = []
    reviews = []

    def research(request):
        batches.append(request)
        return [{"url": "https://vendor.example/advisory", "text": "Mechanism"}] if len(batches) == 1 else []

    def run(role, prompt, tools):
        if role in {"technique_source_assessor", "technique_requirement_assessor"}:
            data = assessment()
            data["coverage"][0]["gaps"] = [{"kind": "missing_factual_support", "description": "Version unknown",
                "claim": "Affected version", "blocking_reason": "Cannot define version check",
                "research_question": "Which release fixed the issue?"}]
            return assessment_output(role, prompt, data)
        if role == "technique_requirement_reviewer":
            reviews.append(prompt)
        return original(role, prompt, tools)

    state.service.runner = run
    state.service.research = research
    with pytest.raises(ValueError, match="Which release fixed the issue"):
        state.service.get("Network version verification")
    assert len(batches) == 3 and len(reviews) == 1
    assert "Which release fixed the issue" in batches[1]


def test_invalid_preservation_correction_is_actionable(scripted_service):
    service = scripted_service.service
    service.runner = lambda role, *_: json.dumps({"approved": False, "reason": "Lost subject",
                               "references": ["original_request"], "subject_preserved": False,
        "mechanisms_preserved": True, "deliverables_preserved": True, "additions": [], "omissions": []}) \
        if role == "technique_request_reviewer" else "invalid JSON"
    decision = LookupDecision(generic_request="Phase", applicability="conditional",
                              unmet_prerequisites=["Unknown version"], reason="Can help")
    with pytest.raises(ValueError, match="request preservation failed"):
        service._preserve_request("Specific source mechanism", decision, None, [])


def test_constraints_need_compliance_explanation_without_external_fact_support():
    data = assessment()
    data["coverage"][0]["source_urls"] = []
    data["coverage"][0]["explanation"] = "Procedure describes future checks without performing live execution"
    result = SourceSelection.model_validate(data)
    result.validate_references([{"url": result.selected_urls[0]}], [
        Requirement(id="method", description="No live execution", kind="constraint")])
    assert result.sufficient
    assert "_requirement_kind" not in result.model_dump_json()
