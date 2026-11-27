from types import SimpleNamespace

import pytest

from modules.techniques.coverage import Requirement
from modules.techniques.publication_review import PublicationReview, ReviewUnavailable, assess


@pytest.mark.parametrize("invalid", [False, True])
def test_output_retry_and_no_assessor(scripted_service, invalid):
    calls = []

    def runner(role, prompt, schema, tools):
        calls.append(role)
        assert schema is PublicationReview
        if invalid or len(calls) == 1:
            raise ValueError("Invalid structured response")
        return PublicationReview(summary="Useful method", issues=[], suggestions=["Optional example"])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    args = (scripted_service.service, "Read comparison", "Read a file under established policy.",
            [Requirement(id="method", description="Read comparison")], [], [])
    if invalid:
        with pytest.raises(ReviewUnavailable):
            assess(*args)
    else:
        result = assess(*args)
        assert result.approved and result.suggestions
    assert calls == ["technique_critic"] * 2


@pytest.mark.parametrize("subject", [
    "HTTP object selector", "browser storage access", "reusable token",
    "network connectivity", "filesystem read access",
])
@pytest.mark.parametrize("defective", [False, True])
def test_material_issues_and_optional_suggestions(scripted_service, subject, defective):
    from modules.techniques.critique import ReviewSnapshot

    text = f"Establish policy before testing {subject}."
    reference = ReviewSnapshot(text).body_references[0]

    def runner(role, prompt, schema, tools):
        assert role == "technique_critic"
        assert text in str(prompt)
        return PublicationReview(summary="Concrete interpretation assessment", issues=[{
            "criterion_id": "core:interpretation",
            "problem": "Observation alone is labeled a violation", "material_impact": "Legitimate access is misclassified",
            "requested_correction": "Compare against established policy", "draft_references": [reference],
        }] if defective else [], suggestions=["Optional worked example"])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    result = assess(scripted_service.service, subject, text, [], [], [])
    assert result.approved is not defective
    if defective:
        assert result.issues[0].id == "issue:1"
        assert result.issues[0].draft_excerpt == text


@pytest.mark.parametrize("defect", ["criterion", "passage", "draft"])
def test_invalid_references_retry_without_approval(scripted_service, defect):
    count = 0

    def runner(role, prompt, schema, tools):
        nonlocal count
        count += 1
        return PublicationReview(summary="Invalid reference", issues=[{
                        "criterion_id": "unknown" if defect == "criterion" else "core:interpretation",
            "problem": "Invalid reference", "material_impact": "Cannot ground defect",
            "requested_correction": "Establish policy",
            "passage_ids": ["unknown"] if defect == "passage" else [],
            "draft_references": ["unknown"] if defect == "draft" else [],
        }])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    with pytest.raises(ReviewUnavailable):
        assess(scripted_service.service, "Read", "Read comparison", [], [], [])
    assert count == 2


def test_source_passages_and_requirement_ids_are_controller_owned(scripted_service):
    def runner(role, prompt, schema, tools):
        passage = tools[0](url="https://vendor.example/source", start=0, length=100)
        return PublicationReview(summary="Missing requested mechanism", issues=[{
            "criterion_id": "source:method",
            "problem": "Retained material does not support the asserted mechanism",
            "material_impact": "Cannot ground the advertised conclusion",
            "requested_correction": "Obtain factual support",
            "passage_ids": [passage["passage_id"]],
        }])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    result = assess(scripted_service.service, "Method", "Method body",
                    [Requirement(id="method", description="Requested mechanism", grounding="source_required")],
                    [{"url": "https://vendor.example/source", "text": "Bounded facts."}],
                    ["https://vendor.example/source"])
    assert not result.approved
    assert result.issues[0].requirement_ids == ["method"]
    assert result.issues[0].source_urls == ["https://vendor.example/source"]
    assert result.issues[0].passages[0].start == 0


def test_legacy_wire_response_is_not_accepted_in_production(scripted_service):
    class Runner:
        def structured(self, role, prompt, schema, tools):
            return schema.model_validate({"approved": True, "reason": "Legacy", "issues": []})

    scripted_service.service.runner = Runner()
    with pytest.raises(ReviewUnavailable):
        assess(scripted_service.service, "Method", "Body", [], [], [])
