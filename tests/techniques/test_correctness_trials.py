"""Experimental role contracts preserve purpose and complete correction evidence."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from modules.techniques.correctness_trials import (
    CorrectnessAssessment,
    CorrectnessCritique,
    CorrectnessInterpretationReview,
    CorrectnessRunner,
)
from modules.techniques.critique import Critique, IssueAssessment
from modules.techniques.review_correctness import CheckPurpose
from modules.techniques.roles import DeferredReviewPrompt


def interpretation():
    return dict(
        check_purpose="prerequisite",
        draft_references=["draft:b1"],
        prerequisite_or_policy="Read configuration",
        legitimate_outcome="Policy established",
        suspected_violation="Not applicable",
        distinguishing_control="Inspect policy",
    )


def critique():
    return CorrectnessCritique(
        approved=True, reason="Prerequisite only", interpretations=[interpretation()], correction_dispositions=[]
    )


def assessment():
    return CorrectnessAssessment(
        critic_supported=True,
        reason="Accurate",
        findings=[],
        interpretation_reviews=[
            CorrectnessInterpretationReview(
                interpretation_index=0,
                draft_references=["draft:b1"],
                check_purpose="prerequisite",
                permitted_counterexample="Not applicable",
                counterexample_matches_check=None,
                control_excludes_counterexample=None,
                actual_discriminator="",
                critic_correct=True,
            )
        ],
        issue_reviews=[],
        requirement_reviews=[],
        disposition_reviews=[],
    )


def test_prerequisite_review_normalizes_without_changing_raw_evidence():
    calls = []
    outputs = iter([critique(), assessment()])

    def structured(role, prompt, schema, tools):
        calls.append(prompt)
        return next(outputs)

    adapter = CorrectnessRunner(SimpleNamespace(structured=structured), [])
    assert adapter.structured("critic", "draft", Critique, []).approved
    result = adapter.structured("assessor", DeferredReviewPrompt("independent", "compare"), IssueAssessment, [])
    assert result.critic_supported
    assert result.interpretation_reviews[0].counterexample_matches_check is False
    assert adapter.records[-1]["output"]["interpretation_reviews"][0]["counterexample_matches_check"] is None
    assert "complete_critic" not in calls[1].investigation
    assert "complete_critic" in calls[1].comparison


def test_coverage_and_purpose_disagreements_rejected():
    adapter = CorrectnessRunner(None, [SimpleNamespace(id="requirement")])
    adapter.candidate = critique().model_dump()
    with pytest.raises(ValueError, match="every issue and requirement"):
        adapter._validate_assessment(assessment())
    adapter.requirements = []
    output = assessment()
    output.interpretation_reviews[0].interpretation_index = 5
    with pytest.raises(ValueError, match="Unknown interpretation"):
        adapter._validate_assessment(output)
    output = assessment()
    output.interpretation_reviews[0].check_purpose = CheckPurpose.LEGITIMATE_BEHAVIOR
    with pytest.raises(ValueError, match="Purpose disagreement"):
        adapter._validate_assessment(output)
    output.critic_supported = False
    adapter._validate_assessment(output)


def test_purpose_validator_cannot_be_bypassed_by_new_schema():
    data = assessment().interpretation_reviews[0].model_dump()
    data["counterexample_matches_check"] = True
    with pytest.raises(ValidationError):
        CorrectnessInterpretationReview(**data)


def test_passthrough_and_boundary_review():
    calls = []
    runner = SimpleNamespace(structured=lambda *args: calls.append(args) or "pass")
    adapter = CorrectnessRunner(runner, [])
    assert adapter.structured("other", "subject", str, []) == "pass"
    output = assessment()
    row = output.interpretation_reviews[0]
    row.check_purpose = CheckPurpose.BOUNDARY_VIOLATION
    row.counterexample_matches_check = False
    row.control_excludes_counterexample = True
    row.actual_discriminator = "Compare known denied access"
    candidate = critique().model_dump()
    candidate["interpretations"][0]["check_purpose"] = "boundary_violation"
    adapter.candidate = candidate
    runner.structured = lambda *args: output
    assert adapter.structured("assessor", "subject", IssueAssessment, []).critic_supported
    assert len(calls) == 1


def test_unknown_coverage_reference_is_rejected_with_retained_response():
    from modules.techniques.correctness_trials import CoverageReview

    output = assessment()
    output.requirement_reviews = [
        CoverageReview(reference="method", assessment="Covered", supports_critic=True, draft_references=["nonexistent"])
    ]
    adapter = CorrectnessRunner(
        SimpleNamespace(structured=lambda *args: output), [SimpleNamespace(id="method")], "Procedure"
    )
    adapter.candidate = critique().model_dump()
    with pytest.raises(ValueError, match="unknown draft references") as failure:
        adapter.structured("assessor", "subject", IssueAssessment, [])
    assert "nonexistent" in failure.value.rejected_response


def test_corrected_critic_cannot_drop_previous_issue():
    from modules.techniques.critique import Issue

    original = critique()
    original.approved = False
    original.issues = [
        Issue(
            id="storage",
            category="authored_content",
            problem="Missing boundary",
            requested_correction="Require boundary",
            requirement_ids=[],
            source_urls=[],
            draft_references=["draft:b1"],
        )
    ]
    responses = iter([original, critique()])
    adapter = CorrectnessRunner(SimpleNamespace(structured=lambda *args: next(responses)), [])
    assert not adapter.structured("critic", "subject", Critique, []).approved
    with pytest.raises(ValueError, match="exactly one disposition") as failure:
        adapter.structured("critic", "correction", Critique, [])
    assert failure.value.rejected_response
    assert adapter.previous[0]["issue"]["id"] == "storage"


def test_valid_coverage_reference():
    from modules.techniques.correctness_trials import CoverageReview

    adapter = CorrectnessRunner(None, [SimpleNamespace(id="method")], "Procedure")
    adapter.candidate = critique().model_dump()
    reference = next(iter(adapter.snapshot.values))
    output = assessment()
    output.requirement_reviews = [
        CoverageReview(reference="method", assessment="Covered", supports_critic=True, draft_references=[reference])
    ]
    adapter._validate_assessment(output)


def test_coverage_disagreement_cannot_support_critic():
    from modules.techniques.correctness_trials import CoverageReview

    adapter = CorrectnessRunner(None, [SimpleNamespace(id="method")])
    adapter.candidate = critique().model_dump()
    output = assessment()
    output.requirement_reviews = [
        CoverageReview(
            reference="method",
            supports_critic=False,
            assessment="Missing deliverable not reported",
            draft_references=[],
        )
    ]
    with pytest.raises(ValueError, match="Unsupported coverage"):
        adapter._validate_assessment(output)


def test_purpose_contract_advertises_required_fields():
    schema = CorrectnessCritique.model_json_schema()
    assert "correction_dispositions" in schema["required"]
    fields = CorrectnessAssessment.model_json_schema()["required"]
    assert {"issue_reviews", "requirement_reviews", "interpretation_reviews"} <= set(fields)


def test_withdrawal_is_preserved_for_independent_semantic_review():
    from modules.techniques.correctness_trials import CoverageReview
    from modules.techniques.critique import Issue
    from modules.techniques.review_correctness import CorrectionDisposition

    original = critique()
    original.approved = False
    original.issues = [
        Issue(
            id="old",
            category="authored_content",
            problem="Disputed",
            requested_correction="Clarify",
            requirement_ids=[],
            source_urls=[],
            draft_references=["draft:b1"],
        )
    ]
    corrected = critique()
    corrected.correction_dispositions = [
        CorrectionDisposition(
            previous_issue_reference="previous:0",
            action="withdrawn",
            reason="The actual draft already contains the control",
            replacement_issue_ids=[],
        )
    ]
    independent = assessment()
    independent.disposition_reviews = [
        CoverageReview(
            reference="previous:0",
            supports_critic=False,
            assessment="Withdrawal unjustified: the boundary remains missing",
            draft_references=["draft:b1"],
        )
    ]
    responses = iter([original, corrected, independent])
    adapter = CorrectnessRunner(SimpleNamespace(structured=lambda *args: next(responses)), [])
    adapter.structured("critic", "subject", Critique, [])
    adapter.structured("critic", "correction", Critique, [])
    with pytest.raises(ValueError, match="Unsupported coverage"):
        adapter.structured("assessor", DeferredReviewPrompt("independent", "compare"), IssueAssessment, [])
    assert "The actual draft already contains" in adapter.records[-2]["prompt"]
    assert "Disputed" in adapter.records[-2]["prompt"]


def test_assessor_correction_retains_raw_applicability_only_in_comparison():
    prompts = []
    output = assessment()

    def structured(role, prompt, schema, tools):
        prompts.append(prompt)
        return output

    adapter = CorrectnessRunner(SimpleNamespace(structured=structured), [])
    adapter.candidate = critique().model_dump()
    for _ in range(2):
        adapter.structured("assessor", DeferredReviewPrompt("independent", "compare"), IssueAssessment, [])
    assert '"counterexample_matches_check": null' in prompts[1].comparison
    assert "raw_previous_assessments" not in prompts[1].investigation
    assert "prerequisite" in prompts[1].comparison
