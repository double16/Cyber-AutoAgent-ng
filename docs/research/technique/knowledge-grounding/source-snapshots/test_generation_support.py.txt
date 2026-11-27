"""Requirement-specific authority and preserved synthesis obligations."""

import json

import pytest
from conftest import assessment_output
from pydantic import ValidationError

from modules.techniques.coverage import AssessedSupport, Requirement, select_sources

URL = "https://standards.example/mechanism"
DOCS = [{"url": URL, "text": "A selector chooses an object; entitlements restrict access."}]


@pytest.mark.parametrize("kind", ["factual_support", "authored_deliverable"])
@pytest.mark.parametrize("authority", [True, False])
def test_requirement_authority_overrides_unrelated_source_wide_flag(scripted_service, kind, authority):
    prompts = []
    requirement = Requirement(grounding="source_required", id="method", description="Design an entitlement comparison", kind=kind)
    payload = {
        "selected_urls": [URL],
        "attributions": [{"url": URL, "reason": "Object selection facts"}],
        "authoritative_sufficient": not authority,
        "reason": "Individual contribution only",
        "coverage": [
            {
                "requirement_id": "method",
                "source_urls": [URL],
                "explanation": "Selection and entitlement facts support an original comparison",
                "gaps": [],
                "authority_sufficient": authority,
                "claim_support": [
                    {
                        "claim": "Object selection differs from authorization",
                        "source_urls": [URL],
                        "authority_basis": "Protocol specification",
                    }
                ],
                "future_validation": ["Establish the target's entitlement policy"],
                "authoring_obligations": ["Write the requested comparison procedure"],
            }
        ],
    }

    def runner(role, prompt, tools):
        prompts.append((role, prompt))
        return assessment_output(role, prompt, payload)

    scripted_service.service.runner = runner
    result = select_sources(scripted_service.service, "Object selection testing", [requirement], DOCS)
    assert result.research_ready is authority
    assert result.sufficient is authority
    assert f"Required-source obligations ready: {authority};" in result.summary()
    row = result.coverage[0]
    assert row.claim_support[0].source_urls == [URL]
    assert row.future_validation and row.authoring_obligations
    assigned = json.loads(prompts[-1][1].split("Assigned requirement: ")[1].splitlines()[0])
    assert assigned["kind"] == kind
    assert assigned["description"] == requirement.description
    assert "Prior requirement assessment" not in prompts[0][1]


@pytest.mark.parametrize("defect", ["unknown_claim_url", "empty_claims", "missing_authority", "missing_claims"])
def test_new_support_requires_complete_referenced_claim_judgments(scripted_service, defect):
    payload = {
        "requirement_id": "method",
        "source_urls": [URL],
        "explanation": "Foundation",
        "gaps": [],
        "authority_sufficient": True,
        "claim_support": [
            {"claim": "Entitlements restrict access", "source_urls": [URL], "authority_basis": "Standard"}
        ],
    }
    if defect == "unknown_claim_url":
        payload["claim_support"][0]["source_urls"] = ["https://unknown.example"]
    elif defect == "empty_claims":
        payload["claim_support"] = []
    else:
        del payload["authority_sufficient" if defect == "missing_authority" else "claim_support"]

    def runner(role, prompt, tools):
        if role == "technique_source_assessor":
            return json.dumps(
                {
                    "selected_urls": [URL],
                    "attributions": [{"url": URL, "reason": "Relevant"}],
                    "reason": "Contribution",
                    "authoritative_sufficient": True,
                }
            )
        return json.dumps(payload)

    scripted_service.service.runner = runner
    with pytest.raises(ValueError, match="after one correction"):
        select_sources(scripted_service.service, "Review", [Requirement(grounding="source_required", id="method", description="Review")], DOCS)


def test_legacy_support_can_be_loaded_but_new_invocation_requires_authority():
    with pytest.raises(ValidationError):
        AssessedSupport.model_validate(
            {"requirement_id": "method", "source_urls": [], "explanation": "Unknown foundation", "gaps": []}
        )


def test_author_enrichment_does_not_become_a_research_or_critique_obligation(scripted_service):
    from modules.techniques.alignment import AUTHORING_ADDITIONS, phase_guidance
    from modules.techniques.critique import assess

    state = scripted_service
    scope = phase_guidance(None)
    assert AUTHORING_ADDITIONS not in scope
    assert "supplied request, requirements" in scope
    assess(state.service, "Review only this prerequisite procedure", "Establish effective access policy.",
           [Requirement(grounding="source_required", id="method", description="Establish the prerequisite policy", kind="authored_deliverable")],
           [], [])
    critic = next(prompt for role, prompt, _ in state.calls if role == "technique_critic")
    assert AUTHORING_ADDITIONS not in critic
    assert "Both roles may faithfully summarize actual steps with current body references" in critic
    state.calls.clear()
    original = state.service.runner
    critiques = 0

    def runner(role, prompt, tools):
        nonlocal critiques
        if role == "technique_critic":
            critiques += 1
            state.approve = critiques > 1
        return original(role, prompt, tools)

    state.service.runner = runner
    state.service.get("JWT handling")
    authors = [prompt for role, prompt, _ in state.calls if role == "technique_author"]
    assert len(authors) == 2
    assert all(AUTHORING_ADDITIONS in prompt for prompt in authors)
    for role, prompt, _ in state.calls:
        if role in {"technique_source_assessor", "technique_requirement_assessor", "technique_critic"}:
            assert AUTHORING_ADDITIONS not in prompt


@pytest.mark.parametrize("supported", [True, False])
def test_shared_scope_reaches_requirement_assessment_and_review(scripted_service, supported):
    from modules.techniques.coverage import review_coverage
    from modules.techniques.support_scope import SUPPORT_SCOPE_GUIDANCE

    original = scripted_service.service.runner
    calls = []

    def runner(role, prompt, tools):
        calls.append((role, str(prompt)))
        response = original(role, prompt, tools)
        if role in {"technique_requirement_assessor", "technique_requirement_reviewer"} and not supported:
            payload = json.loads(response)
            payload["gaps"] = [{"kind": "missing_factual_support", "description": "Unverified asserted mechanism",
                                "claim": "An asserted mechanism lacks factual support",
                                "blocking_reason": "The asserted fact is not established",
                                "research_question": "What evidence establishes this mechanism?"}]
            return json.dumps(payload)
        return response

    scripted_service.service.runner = runner
    requirement = Requirement(grounding="source_required", id="method", description="Design a controlled comparison", kind="authored_deliverable")
    documents = [{"url": "https://vendor.example/advisory", "text": "A protected boundary is required."}]
    selection = select_sources(scripted_service.service, requirement.description, [requirement], documents)
    reviewed = review_coverage(scripted_service.service, requirement.description, [requirement], selection)
    assert reviewed.research_ready is supported
    for role in ("technique_requirement_assessor", "technique_requirement_reviewer"):
        prompts = [prompt for actual_role, prompt in calls if actual_role == role]
        assert prompts and all(SUPPORT_SCOPE_GUIDANCE in prompt for prompt in prompts)
        assert all("numbered procedural steps" not in prompt for prompt in prompts)


def test_methodology_review_instructions_cover_reusable_checks(scripted_service):
    state = scripted_service
    state.service.get("JWT handling")
    critic = next(prompt for role, prompt, _ in state.calls if role == "technique_critic")
    assert not any(role == "technique_critique_assessor" for role, _, _ in state.calls)
    assert "Future verification instructions" in critic
    assert "material issues and optional suggestions" in critic
    assert "No audit tables or approval vote" in critic


def test_minimal_repair_and_execution_scope_preserve_explicit_obligations():
    from modules.techniques.review_contract import ReviewContract
    from modules.techniques.support_scope import SUPPORT_SCOPE_GUIDANCE

    requirements = [Requirement(grounding="source_required", id="restriction", description="Do not describe execution steps", kind="constraint"),
                    Requirement(grounding="source_required", id="date", description="Include retrieval timestamps", kind="authored_deliverable")]
    rendered = ReviewContract.freeze(requirements).render()
    assert all(row.description in rendered for row in requirements)
    assert "smallest necessary correction" in rendered
    assert "unless an explicit requirement" in SUPPORT_SCOPE_GUIDANCE
    assert "unless it explicitly forbids describing execution steps" in rendered
    assert "presentation elaboration belong in suggestions" in rendered


def test_author_repair_keeps_specific_subject_instructions(scripted_service):
    from modules.techniques.alignment import AUTHORING_ADDITIONS

    state = scripted_service
    original = state.service.runner
    critics = []

    def runner(role, prompt, tools):
        if role == "technique_critic":
            critics.append(prompt)
            state.approve = len(critics) > 1
        return original(role, prompt, tools)

    state.service.runner = runner
    state.service.get("JWT handling")
    authors = [prompt for role, prompt, _ in state.calls if role == "technique_author"]
    assert len(authors) == 2
    assert all(AUTHORING_ADDITIONS in prompt for prompt in authors)
    assert all("keep the method's explicit subject, objects and operations" in prompt for prompt in authors)
