"""Cross-field consistency of new usefulness decisions."""

import pytest
from pydantic import ValidationError

from modules.techniques.feedback import ContributionFeedback


@pytest.mark.parametrize("helpful,harmful", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize("basis", [None, "not_used", "contribution_unestablished"])
def test_non_neutral_basis(helpful, harmful, basis):
    values = dict(
        neutral_basis=basis, methodological_purpose="Compare access boundaries",
        observed_use="Applied the comparison", helpful_contribution=helpful,
        contribution="Selected a control" if helpful else "", misleading_or_harmful=harmful,
        harmful_effect="Misclassified the control" if harmful else "", reason="Observed comparison",
    )
    if basis is not None:
        with pytest.raises(ValidationError, match="neutral_basis=null"):
            ContributionFeedback(**values)
    else:
        assert ContributionFeedback(**values).score == (-1 if harmful else 1)


def test_independent_review_withholds_evaluator_decision(scripted_service, monkeypatch):
    from modules.techniques.feedback import evaluate_feedback
    from modules.techniques.roles import DeferredReviewPrompt

    service = scripted_service.service
    original = service._json
    captured = []

    def inspect(role, prompt, schema, tools=None):
        if role == "technique_feedback_reviewer":
            assert isinstance(prompt, DeferredReviewPrompt)
            assert "Evaluator decision for comparison" not in prompt.investigation
            assert "Evaluator decision for comparison" in prompt.comparison
            assert "evidence_references" in prompt.investigation
            assert "evidence_references" not in prompt.comparison
            captured.append(prompt)
        return original(role, prompt, schema, tools)

    monkeypatch.setattr(service, "_json", inspect)
    result = evaluate_feedback(service, {"revision_id": "frozen", "use_context":
        '{"delivered_documents": ["Specific comparison guidance"]}'},
        "A recorded control comparison after delivery", [])
    assert result.score == 1
    assert len(captured) == 1
