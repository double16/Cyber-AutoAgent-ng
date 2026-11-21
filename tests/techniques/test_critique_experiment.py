"""Experimental ratings never silently change production approval."""

import pytest
from pydantic import ValidationError

from modules.techniques.critique_experiment import RatedJudgment, TrialMetrics, Variant, select_candidate


@pytest.mark.parametrize("score", range(1, 6))
@pytest.mark.parametrize("blockers", [[], ["Missing entitlement control"]])
@pytest.mark.parametrize("valid", [True, False])
def test_rating_threshold_and_blockers(score, blockers, valid):
    result = RatedJudgment(score=score, reason="Reviewed", blocking_findings=blockers, references_valid=valid)
    assert result.accepted == (score >= 4 and not blockers and valid)


@pytest.mark.parametrize("score", [0, 6, True, "4", 4.5])
def test_rating_rejects_invalid_scores(score):
    with pytest.raises(ValidationError):
        RatedJudgment(score=score, reason="Reviewed", blocking_findings=[], references_valid=True)


def metrics(**changes):
    return TrialMetrics(**{name: changes.get(name, 0) for name in TrialMetrics.model_fields})


def test_selection_requires_complete_screen():
    with pytest.raises(ValueError):
        select_candidate({Variant.A: metrics()})


def test_selection_prioritizes_false_approvals_and_stable_ties():
    results = {arm: metrics() for arm in Variant}
    assert select_candidate(results) == Variant.A
    results[Variant.A] = metrics(false_approvals=1)
    results[Variant.B] = metrics(false_rejections=1)
    assert select_candidate(results) == Variant.C


def test_heldout_challenger_selection_does_not_compare_baseline_with_itself():
    results = {arm: metrics() for arm in Variant}
    assert select_candidate(results, challenger_only=True) == Variant.B
    results[Variant.B] = metrics(false_approvals=1)
    assert select_candidate(results, challenger_only=True) == Variant.C
    assert select_candidate(results) == Variant.A
