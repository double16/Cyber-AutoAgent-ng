"""Experimental review bookkeeping; semantic judgments remain model-owned."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

GUIDANCE = """Classify what each draft check actually claims using check_purpose.
Use prerequisite, legitimate_behavior or boundary_violation. Do not silently downgrade a
misleading violation label into legitimate behavior; report that authored defect. Split mixed
purposes into separate interpretations. Non-boundary checks require null counterexample
booleans and an empty discriminator: successful prerequisite establishment is not a violation.
Only boundary claims require a scenario matching every actual prerequisite and the exact
suspicious observation. If no permitted scenario matches, explain why; do not invent one.
Both roles may faithfully summarize actual controls with current body references; exact wording
is not required. Reference availability does not prove relevance. Technical facts support original
procedure synthesis; sources need not contain an identical payload or complete methodology. Unknown
target conditions remain future checks with explicit establishment steps. No target execution
is required. Independently inspect interpretation, deliverables, source support and every issue
before returning complete feedback. Reference availability does not establish semantic support.
"""

INVESTIGATION_GUIDANCE = (
    # Shared rules are appended once by the experimental adapter.
    """
Inspect the complete draft independently before seeing the critic. Record all concrete defects
with exact draft references and actionable corrections, checking interpretation, requested
deliverables and source support together. Research-readiness metadata is not a procedure.
For boundary claims, hold every prerequisite and the exact suspicious observation constant.
For prerequisite checks, assess how policy is established; for legitimate observations, assess
accurate interpretation and useful next steps. Do not require violation proof from either.
"""
)

COMPARISON_GUIDANCE = (
    # Shared rules are appended once by the experimental adapter.
    """
Compare the complete independent findings with the critic only at finalization. Assess the
critic decision and every issue, including approvals and justified rejections. Cover every
interpretation, issue, requirement and previous-issue disposition exactly once using the
structured contract. Inspect requested-deliverable completeness and source support as well
as interpretation before returning feedback. Agreement requires correctness and completeness
of all issues and justified retention, revision or withdrawal; otherwise return referenced
findings. Use current draft/requirement references for withdrawals, not obsolete issue IDs.
Ground actual_discriminator in cited body steps; a faithful paraphrase is allowed. Source absence
is limited to inspected material; full-source absence requires complete inspection. Do not demand an
exact source procedure for an original synthesis supported by relevant technical facts.
"""
)


class CheckPurpose(StrEnum):
    PREREQUISITE = "prerequisite"
    LEGITIMATE_BEHAVIOR = "legitimate_behavior"
    BOUNDARY_VIOLATION = "boundary_violation"


class PurposeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_purpose: CheckPurpose
    counterexample_matches_check: StrictBool | None
    control_excludes_counterexample: StrictBool | None
    actual_discriminator: str

    @model_validator(mode="after")
    def applicable_fields(self):
        if self.check_purpose != CheckPurpose.BOUNDARY_VIOLATION:
            if (
                self.counterexample_matches_check is not None
                or self.control_excludes_counterexample is not None
                or self.actual_discriminator
            ):
                raise ValueError("Non-violation checks require null counterexample fields and empty discriminator")
        else:
            if self.counterexample_matches_check is None or self.control_excludes_counterexample is None:
                raise ValueError("Boundary claims require boolean counterexample judgments")
            if self.control_excludes_counterexample and not self.actual_discriminator.strip():
                raise ValueError("An excluding control requires an evidence-grounded explanation")
        return self


class CorrectionDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    previous_issue_reference: str = Field(min_length=1)
    action: Literal["retained", "revised", "withdrawn"]
    reason: str = Field(min_length=1)
    replacement_issue_ids: list[str]


def validate_dispositions(previous_references, current_issue_ids, dispositions):
    """Check complete accounting only; never decide whether withdrawal is justified."""
    references = [row.previous_issue_reference for row in dispositions]
    if len(references) != len(set(references)) or set(references) != set(previous_references):
        raise ValueError("Every previous issue requires exactly one disposition")
    for row in dispositions:
        if not set(row.replacement_issue_ids) <= set(current_issue_ids):
            raise ValueError("Replacement issues must exist in the corrected critique")
        if row.action == "withdrawn":
            if row.replacement_issue_ids:
                raise ValueError("Withdrawn issues cannot have replacements")
        elif not row.replacement_issue_ids:
            raise ValueError("Retained or revised issues require replacement references")
