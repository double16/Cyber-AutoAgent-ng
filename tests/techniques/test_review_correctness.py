"""Purpose applicability and correction accounting do not infer security semantics."""

import pytest
from pydantic import ValidationError

from modules.techniques.review_correctness import (
    CheckPurpose,
    CorrectionDisposition,
    PurposeReview,
    validate_dispositions,
)


@pytest.mark.parametrize("purpose", list(CheckPurpose))
def test_purpose_fields(purpose):
    boundary = purpose == CheckPurpose.BOUNDARY_VIOLATION
    result = PurposeReview(check_purpose=purpose, counterexample_matches_check=False if boundary else None,
                           control_excludes_counterexample=False if boundary else None, actual_discriminator="")
    assert result.check_purpose == purpose


@pytest.mark.parametrize("fields", [
    {"check_purpose": "prerequisite", "counterexample_matches_check": True,
     "control_excludes_counterexample": None, "actual_discriminator": ""},
    {"check_purpose": "boundary_violation", "counterexample_matches_check": None,
     "control_excludes_counterexample": False, "actual_discriminator": ""},
    {"check_purpose": "boundary_violation", "counterexample_matches_check": False,
     "control_excludes_counterexample": True, "actual_discriminator": " "},
])
def test_invalid_applicability(fields):
    with pytest.raises(ValidationError):
        PurposeReview(**fields)


def disposition(action="retained", replacements=None):
    return CorrectionDisposition(previous_issue_reference="previous:1", action=action, reason="Reviewed evidence",
                                 replacement_issue_ids=["new"] if replacements is None else replacements)


@pytest.mark.parametrize("action", ["retained", "revised", "withdrawn"])
def test_valid_dispositions(action):
    validate_dispositions(["previous:1"], ["new"], [disposition(action, [] if action == "withdrawn" else ["new"])])


@pytest.mark.parametrize("previous,current,rows", [
    (["previous:1"], ["new"], []),
    (["previous:1"], ["new"], [disposition(), disposition()]),
    (["previous:1"], [], [disposition()]),
    (["previous:1"], ["new"], [disposition("withdrawn")]),
    (["previous:1"], ["new"], [disposition("revised", [])]),
])
def test_invalid_dispositions(previous, current, rows):
    with pytest.raises(ValueError):
        validate_dispositions(previous, current, rows)
