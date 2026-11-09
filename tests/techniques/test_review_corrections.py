"""Incremental merges preserve evidence without approving disputed semantics."""

import pytest

from modules.techniques.critique import Critique, Interpretation, IssueAssessment
from modules.techniques.review_corrections import (
    AssessmentCorrection,
    CriticCorrection,
    merge_assessment,
    merge_critic,
)


def interpretation():
    return dict(check_purpose="legitimate_behavior", draft_references=["draft:b1"],
                prerequisite_or_policy="Established policy", legitimate_outcome="Allowed read",
                suspected_violation="No violation claimed", distinguishing_control="Not applicable")


def critic():
    return Critique(approved=True, decision_origin="controller_findings", reason="Initial analysis",
                    interpretations=[Interpretation(**interpretation()), Interpretation(**interpretation())])


def correction(**updates):
    return CriticCorrection.model_validate({"reason": "Explicit correction", "issues": [],
        "issue_dispositions": [], "interpretation_changes": [{"interpretation_index": 0,
            "action": "retained", "reason": "Current policy is explicit"}],
        "finding_responses": [{"finding_reference": "finding:1", "action": "rejected",
                               "reason": "The concern changes the prerequisite"}], **updates})


def test_critic_preserves_full_table_and_reassesses_explicit_retention():
    base = critic()
    result = merge_critic(base, correction(), {"finding:1": {}}, [0])
    assert len(result.interpretations) == 2
    assert result.interpretations == base.interpretations
    assert result.interpretations[1] is not base.interpretations[1]
    assert result.approved  # Still requires independent assessment in the production path.
    replacement = interpretation() | {"legitimate_outcome": "Recorded allowed read"}
    patch = correction(interpretation_changes=[{"interpretation_index": 0, "action": "revised",
                       "reason": "Clarified", "replacement": replacement}],
                       finding_responses=[{"finding_reference": "finding:1", "action": "accepted",
                                           "reason": "Clarification needed", "interpretation_indices": [0]}],
                       added_interpretations=[replacement], suggestions=[])
    revised = merge_critic(base, patch, {"finding:1": {}}, [0])
    assert len(revised.interpretations) == 3
    assert revised.interpretations[0].legitimate_outcome == "Recorded allowed read"
    assert base.interpretations[0].legitimate_outcome == "Allowed read"


@pytest.mark.parametrize("changes", [[], [{"interpretation_index": 2, "action": "retained", "reason": "Bad"}],
    [{"interpretation_index": 0, "action": "retained", "reason": "One"}]*2,
    [{"interpretation_index": 0, "action": "revised", "reason": "Missing replacement"}],
    [{"interpretation_index": 0, "action": "retained", "reason": "Bad replacement", "replacement": interpretation()}]])
def test_disputed_interpretations_cannot_silently_carry_forward(changes):
    with pytest.raises(ValueError):
        merge_critic(critic(), correction(interpretation_changes=changes), {"finding:1": {}}, [0])


@pytest.mark.parametrize("responses", [[], [{"finding_reference": "unknown", "action": "rejected", "reason": "Bad"}],
    [{"finding_reference": "finding:1", "action": "accepted", "reason": "No change"}],
    [{"finding_reference": "finding:1", "action": "accepted", "reason": "Unknown", "issue_ids": ["unknown"]}],
    [{"finding_reference": "finding:1", "action": "accepted", "reason": "Untouched", "interpretation_indices": [1]}]])
def test_every_finding_requires_a_valid_explicit_response(responses):
    with pytest.raises(ValueError):
        merge_critic(critic(), correction(finding_responses=responses), {"finding:1": {}}, [0])


def review(index):
    return dict(check_purpose="legitimate_behavior", interpretation_index=index, draft_references=["draft:b1"],
                permitted_counterexample="Not applicable", counterexample_matches_check=None,
                control_excludes_counterexample=None, actual_discriminator="", critic_correct=True)


def test_assessor_updates_only_changed_rows_and_explicit_aggregate():
    base = IssueAssessment(critic_supported=True, reason="Initial", interpretation_reviews=[review(0), review(1)])
    patch = AssessmentCorrection(critic_supported=True, reason="Corrected", findings=[], review_changes=[])
    assert len(merge_assessment(base, patch).interpretation_reviews) == 2
    patch = AssessmentCorrection.model_validate({**patch.model_dump(),
        "review_changes": [{"interpretation_index": 0, "replacement": review(0)}]})
    assert merge_assessment(base, patch).interpretation_reviews[0].interpretation_index == 0


@pytest.mark.parametrize("changes", [
    [{"interpretation_index": 2, "replacement": review(2)}],
    [{"interpretation_index": 0, "replacement": review(1)}],
    [{"interpretation_index": 0, "replacement": review(0)}]*2])
def test_assessor_rejects_stale_duplicate_or_remapped_indices(changes):
    base = IssueAssessment(critic_supported=True, reason="Initial", interpretation_reviews=[review(0)])
    patch = AssessmentCorrection(critic_supported=True, reason="Correction", findings=[], review_changes=changes)
    with pytest.raises(ValueError):
        merge_assessment(base, patch)


def test_withdrawn_interpretation_is_explicit_and_not_silently_reused():
    patch = correction(interpretation_changes=[{"interpretation_index": 0, "action": "withdrawn",
                       "reason": "This row described a check absent from the subject"}],
                       finding_responses=[{"finding_reference": "finding:1", "action": "accepted",
                                           "reason": "Removed invented check", "interpretation_indices": [0]}])
    assert len(merge_critic(critic(), patch, {"finding:1": {}}, [0]).interpretations) == 1
    with pytest.raises(ValueError, match="actual interpretation"):
        merge_critic(critic(), correction(finding_responses=[{"finding_reference": "finding:1",
                     "action": "accepted", "reason": "No change", "interpretation_indices": [0]}]),
                     {"finding:1": {}}, [0])


def test_unordered_baseline_reviews_are_canonicalized_and_duplicates_rejected():
    patch = AssessmentCorrection(critic_supported=True, reason="Correction", findings=[], review_changes=[])
    base = IssueAssessment(critic_supported=True, reason="Initial", interpretation_reviews=[review(1), review(0)])
    assert [row.interpretation_index for row in merge_assessment(base, patch).interpretation_reviews] == [0, 1]
    base.interpretation_reviews = [base.interpretation_reviews[0]] * 2
    with pytest.raises(ValueError, match="contiguous unique"):
        merge_assessment(base, patch)


@pytest.mark.parametrize("persistent", [False, True])
def test_native_critic_correction_retains_table_and_rechecks_disputed_rows(scripted_service, persistent):
    from modules.techniques.coverage import Requirement
    from modules.techniques.critique import IssueAssessment, ReviewSnapshot, assess

    text = "Establish policy, then record allowed read outcomes."
    reference = ReviewSnapshot(text).body_references[0]
    base = critic()
    for item in base.interpretations:
        item.draft_references = [reference]
    calls, events = [], []
    scripted_service.service.events.callback = events.append

    def runner(role, prompt, schema, tools):
        calls.append((role, schema, prompt))
        if schema is Critique:
            return base
        if schema is CriticCorrection:
            assert "finding:1" in str(prompt)
            assert "Return CriticCorrection, not a full critique" in str(prompt)
            replacement = interpretation() | {"draft_references": [reference]}
            return correction(interpretation_changes=[{"interpretation_index": 0, "action": "revised",
                "reason": "Clarify the actual prerequisite", "replacement": replacement}],
                finding_responses=[{"finding_reference": "finding:1", "action": "accepted",
                    "reason": "Revised the disputed row", "interpretation_indices": [0]}])
        reviews = [review(0) | {"draft_references": [reference]}, review(1) | {"draft_references": [reference]}]
        if sum(item[0] == "technique_critique_assessor" for item in calls) == 1:
            reviews[0]["critic_correct"] = False
            return IssueAssessment(critic_supported=False, reason="Clarify row zero", interpretation_reviews=reviews,
                findings=[{"criterion_id": "core:interpretation", "material_impact": "Ambiguous prerequisite",
                    "problem": "Clarify row", "requested_correction": "Describe actual prerequisite",
                    "draft_references": [reference]}])
        assert "critic_correction_responses" in str(prompt)
        if persistent:
            reviews[0]["critic_correct"] = False
            return IssueAssessment(critic_supported=False, reason="Dispute persists", interpretation_reviews=reviews,
                findings=[{"criterion_id": "core:interpretation", "material_impact": "Still unresolved",
                    "problem": "Correction did not resolve concern", "requested_correction": "Clarify prerequisite",
                    "draft_references": [reference]}])
        return IssueAssessment(critic_supported=True, reason="Merged correction checked", interpretation_reviews=reviews)

    from types import SimpleNamespace

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    args = (scripted_service.service, "Read comparison", text,
            [Requirement(id="method", description="Read comparison")], [], [])
    if persistent:
        with pytest.raises(ValueError, match="after one correction"):
            assess(*args)
    else:
        result = assess(*args)
        assert result.approved and len(result.interpretations) == 2
    assert len(calls) == 4
    assert any(row.get("status") == "correction_merged" for row in events)


def test_assessor_correction_changes_aggregate_without_repeating_rows(scripted_service):
    from types import SimpleNamespace

    from modules.techniques.coverage import Requirement
    from modules.techniques.critique import ReviewSnapshot, assess

    text = "Establish read policy."
    reference = ReviewSnapshot(text).body_references[0]
    base = critic()
    for item in base.interpretations:
        item.draft_references = [reference]
    calls = []

    def runner(role, prompt, schema, tools):
        calls.append(schema)
        if schema is Critique:
            return base
        rows = [review(0) | {"draft_references": [reference]}, review(1) | {"draft_references": [reference]}]
        if schema is IssueAssessment:
            rows[0]["critic_correct"] = False
            return IssueAssessment(critic_supported=True, reason="Invalid aggregate", interpretation_reviews=rows)
        assert schema is AssessmentCorrection
        assert "Return AssessmentCorrection" in str(prompt)
        return AssessmentCorrection(critic_supported=True, reason="Corrected the erroneous row", findings=[],
                                    review_changes=[{"interpretation_index": 0, "replacement": rows[0]}])

    scripted_service.service.runner = SimpleNamespace(structured=runner)
    assert assess(scripted_service.service, "Read comparison", text,
                  [Requirement(id="method", description="Read comparison")], [], []).approved
    assert calls == [Critique, IssueAssessment, AssessmentCorrection]


def blocker(**updates):
    return {"id": "clarity", "category": "authored_content", "criterion_id": "core:interpretation",
            "material_impact": "Cannot identify the tested operation", "problem": "Operation is unclear",
            "requested_correction": "Identify the operation", "requirement_ids": [], "source_urls": [],
            "draft_references": ["draft:b1"], **updates}


def test_nested_issue_relation_is_controller_derived_without_mutating_submitted_patch():
    from modules.techniques.review_corrections import normalize_correction

    patch = correction(finding_responses=[{"finding_reference": "finding:1", "action": "accepted",
                       "reason": "Missing deliverable", "issues": [blocker()]}])
    normalized = normalize_correction(patch)
    assert normalized.finding_responses[0].issue_ids == ["clarity"]
    assert patch.finding_responses[0].issue_ids == [] and patch.issues == []
    result = merge_critic(critic(), patch, {"finding:1": {}}, [0])
    assert not result.approved and result.issues[0].id == "clarity"
    assert len(result.interpretations) == 2


@pytest.mark.parametrize("location", ["root", "other_response"])
def test_identical_nested_issue_can_be_shared_but_not_duplicated_in_merged_issues(location):
    from modules.techniques.review_corrections import normalize_correction

    responses = [{"finding_reference": "finding:1", "action": "accepted", "reason": "Missing operation",
                  "issues": [blocker()], "issue_ids": ["clarity"]}]
    if location == "other_response":
        responses.append({**responses[0], "finding_reference": "finding:2"})
    patch = correction(issues=[blocker()] if location == "root" else [], finding_responses=responses)
    result = normalize_correction(patch)
    assert len(result.issues) == 1
    assert all(row.issue_ids == ["clarity"] for row in result.finding_responses)


@pytest.mark.parametrize("defect", ["duplicate_root", "conflicting_issue", "rejected_with_issue", "unknown_link"])
def test_nested_issue_normalization_does_not_guess_or_accept_conflicting_relations(defect):
    patch = correction(finding_responses=[{"finding_reference": "finding:1", "action": "accepted",
                       "reason": "Missing operation", "issues": [blocker()]}])
    if defect == "duplicate_root":
        patch.issues = [patch.finding_responses[0].issues[0]] * 2
    elif defect == "conflicting_issue":
        from modules.techniques.critique import BlockingIssue

        patch.issues = [BlockingIssue(**blocker(problem="Different meaning"))]
    elif defect == "rejected_with_issue":
        patch.finding_responses[0].action = "rejected"
    else:
        patch.finding_responses[0].issue_ids = ["unknown"]
    with pytest.raises(ValueError):
        merge_critic(critic(), patch, {"finding:1": {}}, [0])


def test_captured_empty_mapping_response_is_not_semantically_matched_from_prose():
    """The historical response remains invalid; nesting is an explicit new authoring contract."""
    import json
    from pathlib import Path

    path = Path("docs/research/technique-review-incremental-corrections/live-acceptance-1/"
                "filesystem-correction-response.json")
    captured = json.loads(path.read_text())["response"]["content"][0]["toolUse"]["input"]
    patch = CriticCorrection.model_validate(captured)
    base = critic()
    base.interpretations = base.interpretations * 3
    findings = {row.finding_reference: {} for row in patch.finding_responses}
    with pytest.raises(ValueError, match="Accepted findings require"):
        merge_critic(base, patch, findings, [4])
    # Structural counterfactual only: explicitly place each emitted issue in its response.
    # This does not validate the reasoning or claim a successful live correction.
    for response, issue in zip(patch.finding_responses, patch.issues, strict=True):
        response.issues = [issue]
    patch.issues = []
    result = merge_critic(base, patch, findings, [4])
    assert not result.approved and len(result.issues) == 2
    assert len(result.interpretations) == 5
