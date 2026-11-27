"""Knowledge-based requirements and explicit source obligations are separate axes."""

from modules.techniques.coverage import Requirement, SourceSelection, Support
from modules.techniques.review_contract import ReviewContract


def selection(requirement, urls):
    value = SourceSelection(selected_urls=urls,
                            attributions=[{"url": url, "reason": "Useful background"} for url in urls],
                            coverage=[Support(requirement_id=requirement.id, source_urls=[], gaps=[],
                                              explanation="Knowledge-based design")],
                            authoritative_sufficient=False, reason="Reference contribution")
    value.validate_references([{"url": url} for url in urls], [requirement])
    return value


def test_general_factual_requirement_does_not_require_retrieved_claim_support():
    requirement = Requirement(id="method", description="Design a comparison", kind="factual_support")
    value = selection(requirement, ["https://example.org/reference"])
    assert requirement.grounding == "knowledge_allowed"
    assert value.research_ready and value.sufficient
    contract = ReviewContract.freeze([requirement])
    assert "core:correctness" in contract.criterion_ids
    assert "core:technical_support" not in contract.criterion_ids
    assert "source:method" not in contract.criterion_ids


def test_no_reference_still_prevents_readiness():
    requirement = Requirement(id="method", description="Design a comparison")
    value = selection(requirement, [])
    assert not value.research_ready and not value.sufficient


def test_explicit_source_required_obligation_adds_scoped_criterion():
    requirement = Requirement(id="versions", description="Verified affected versions", grounding="source_required")
    contract = ReviewContract.freeze([requirement])
    assert "source:versions" in contract.criterion_ids


def test_general_generation_needs_one_reference_not_per_requirement_source_judgments(scripted_service):
    state = scripted_service
    state.authoritative = False  # Authority over every statement is not needed for useful background.
    revision, document = state.service.get("Design a reusable comparison")
    assert revision and document
    assert len(state.research_calls) == 1
    assert not any(role in {"technique_requirement_assessor", "technique_requirement_reviewer"}
                   for role, _, _ in state.calls)
    assert "Knowledge-allowed content is not source-verified" in document


def test_source_required_direct_lookup_remains_blocked_without_authority(scripted_service):
    import pytest

    state = scripted_service
    state.grounding = "source_required"
    state.authoritative = False
    with pytest.raises(ValueError, match="Research coverage exhausted"):
        state.service.get("Quote the verified vendor statement")
    assert not any(role == "technique_author" for role, _, _ in state.calls)
    assert not state.service.repository.revisions()


def test_required_source_without_evidence_cannot_claim_ready():
    import pytest

    requirement = Requirement(id="versions", description="Verified versions", grounding="source_required")
    with pytest.raises(ValueError, match="explicit gaps"):
        selection(requirement, ["https://example.org/reference"])


def test_knowledge_only_source_criterion_cannot_be_invented(scripted_service):
    from types import SimpleNamespace

    import pytest

    from modules.techniques.publication_review import PublicationReview, ReviewUnavailable, assess

    def runner(role, prompt, schema, tools):
        return PublicationReview(summary="Invented source duty", issues=[{
            "criterion_id": "source:method", "problem": "Procedure is absent from reference",
            "material_impact": "Claimed unsupported", "requested_correction": "Fetch a complete procedure",
        }])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    with pytest.raises(ReviewUnavailable, match="Unknown acceptance criterion"):
        assess(scripted_service.service, "Design comparison", "Future comparison.",
               [Requirement(id="method", description="Design comparison")], [], [])


def test_controller_owns_repair_category(scripted_service):
    from types import SimpleNamespace

    from modules.techniques.critique import ReviewSnapshot
    from modules.techniques.publication_review import PublicationReview, assess

    text = "Read comparison with a cited vendor statement."
    reference = ReviewSnapshot(text).body_references[0]

    for criterion, expected in [
        ("core:correctness", "authored_content"),
        ("core:attribution", "source_attribution"),
        ("requirement:versions", "request_coverage"),
        ("source:versions", "research_coverage"),
    ]:
        def runner(role, prompt, schema, tools):
            passage = tools[0](url="https://example.org/reference", start=0, length=100)
            return PublicationReview(summary="Material correction", issues=[{
                "criterion_id": criterion, "problem": "Incorrect requested statement",
                "material_impact": "Misleads the comparison", "requested_correction": "Correct the statement",
                "draft_references": [reference], "passage_ids": [passage["passage_id"]],
            }])

        scripted_service.service.runner = SimpleNamespace(structured=runner)
        result = assess(scripted_service.service, "Verified versions", text,
                        [Requirement(id="versions", description="Verified versions", grounding="source_required")],
                        [{"url": "https://example.org/reference", "text": "Supported version statement."}],
                        ["https://example.org/reference"])
        assert result.issues[0].category == expected


def test_planned_source_requirement_preserves_provenance():
    from modules.techniques.bundles import PlannedRequirement

    row = PlannedRequirement(description="Vendor affected versions", kind="factual_support",
                             grounding="source_required", input_references=["original_request"])
    assert row.grounding == "source_required"
    assert row.input_references == ["original_request"]


def test_source_required_constraint_cannot_bypass_evidence():
    import pytest

    requirement = Requirement(id="policy", description="Quote policy restriction",
                              kind="constraint", grounding="source_required")
    with pytest.raises(ValueError, match="explicit gaps"):
        selection(requirement, ["https://example.org/reference"])
