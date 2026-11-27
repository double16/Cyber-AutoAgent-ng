"""Purpose is model-owned; Python checks only applicability and field consistency."""

import pytest
from pydantic import ValidationError

from modules.techniques.critique import ClassifiedInterpretationReview, InterpretationReview


def row(purpose, **updates):
    return {"check_purpose": purpose, "interpretation_index": 0, "draft_references": ["draft:b1"],
            "permitted_counterexample": "No violation claimed by prerequisite check",
            "counterexample_matches_check": None, "control_excludes_counterexample": None,
            "actual_discriminator": "", "critic_correct": True, **updates}


@pytest.mark.parametrize("purpose", ["prerequisite", "legitimate_behavior"])
def test_nonboundary_fields_are_explicitly_inapplicable(purpose):
    result = ClassifiedInterpretationReview.model_validate(row(purpose))
    assert result.counterexample_matches_check is None
    for update in ({"counterexample_matches_check": False}, {"control_excludes_counterexample": True},
                   {"actual_discriminator": "Invented boundary control"}, {"control_references": ["draft:b1"]}):
        with pytest.raises(ValidationError, match="Non-boundary"):
            ClassifiedInterpretationReview.model_validate(row(purpose, **update))


def test_boundary_and_legacy_require_booleans():
    with pytest.raises(ValidationError, match="boolean"):
        ClassifiedInterpretationReview.model_validate(row("boundary_violation"))
    data = row("boundary_violation", counterexample_matches_check=True, control_excludes_counterexample=False)
    assert ClassifiedInterpretationReview.model_validate(data).counterexample_matches_check
    del data["check_purpose"]
    assert InterpretationReview.model_validate(data).check_purpose is None
    with pytest.raises(ValidationError):
        ClassifiedInterpretationReview.model_validate(data)
    with pytest.raises(ValidationError):
        ClassifiedInterpretationReview.model_validate(row("unknown"))


@pytest.mark.parametrize("matching", [True, False])
def test_purpose_agreement_is_required_for_approval(scripted_service, matching):
    import json

    from modules.techniques.coverage import Requirement
    from modules.techniques.critique import Critique, Interpretation, ReviewSnapshot, assess

    text = "Establish the intended read policy."
    reference = ReviewSnapshot(text).body_references[0]
    calls = []

    def runner(role, prompt, tools):
        if role == "technique_critic":
            return Critique(approved=True, reason="Policy prerequisite", interpretations=[Interpretation(
                check_purpose="prerequisite", draft_references=[reference], prerequisite_or_policy="Read policy",
                legitimate_outcome="Policy known", suspected_violation="No violation claimed",
                distinguishing_control="Not applicable",
            )]).model_dump_json()
        calls.append(prompt)
        return json.dumps({"critic_supported": True, "reason": "Scripted classification", "findings": [],
            "interpretation_reviews": [row("prerequisite" if matching else "legitimate_behavior",
                                            draft_references=[reference])]})

    scripted_service.service.runner = runner
    args = (scripted_service.service, "Describe future read checks", text,
            [Requirement(id="method", description="Policy prerequisite")], [], [])
    if matching:
        assert assess(*args).approved
        assert len(calls) == 1
    else:
        with pytest.raises(ValueError, match="Purpose disagreement"):
            assess(*args)
        assert len(calls) == 2


def test_native_schemas_require_purpose_without_inference_for_legacy_records():
    from modules.techniques.critique import AssessorFindings, CriticFindings, Interpretation

    for schema, definition in ((CriticFindings, "ClassifiedInterpretation"),
                               (AssessorFindings, "ClassifiedInterpretationReview")):
        fields = schema.model_json_schema()["$defs"][definition]
        assert "check_purpose" in fields["required"]
        assert set(fields["properties"]["check_purpose"]["enum"]) == {
            "prerequisite", "legitimate_behavior", "boundary_violation"}
    legacy = Interpretation(draft_references=["draft:b1"], prerequisite_or_policy="prerequisite",
                           legitimate_outcome="legitimate", suspected_violation="boundary_violation",
                           distinguishing_control="Not known")
    assert legacy.check_purpose is None
