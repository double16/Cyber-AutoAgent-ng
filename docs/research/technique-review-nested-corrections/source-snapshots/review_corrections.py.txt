"""Controller-owned review merges; models explain every disputed retention or change."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from modules.techniques.critique import (
    BlockingIssue,
    ClassifiedInterpretation,
    ClassifiedInterpretationReview,
    Critique,
    IssueAssessment,
    IssueDisposition,
    MaterialAssessmentFinding,
    Suggestion,
)


class InterpretationChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interpretation_index: StrictInt = Field(ge=0)
    action: Literal["retained", "revised", "withdrawn"]
    reason: str = Field(min_length=1)
    replacement: ClassifiedInterpretation | None = None


class FindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_reference: str = Field(description="Copy one exact controller finding:N key from independent_findings")
    action: Literal["accepted", "rejected"]
    reason: str = Field(min_length=1)
    issues: list[BlockingIssue] = Field(
        default_factory=list, description="For an accepted finding needing document correction, put complete "
        "blocking issues HERE. Python derives their issue_ids from this nesting; do not duplicate at the root.",
    )
    issue_ids: list[str] = Field(
        default_factory=list, description="Optional links to issues already supplied elsewhere in this correction. "
        "Not needed for issues nested in this response. Unknown IDs are rejected.",
    )
    interpretation_indices: list[StrictInt] = Field(
        default_factory=list, description="Baseline interpretation indices explicitly disposed in interpretation_changes, "
        "or appended indices beginning at the baseline table length. These are not reindexed merged-table positions.",
    )


class CriticCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1)
    issues: list[BlockingIssue] = Field(
        description="Retained or new blockers unrelated to a finding response. Put blockers addressing a finding "
        "inside that response's issues instead. Python combines both lists before independent assessment.",
    )
    issue_dispositions: list[IssueDisposition]
    interpretation_changes: list[InterpretationChange]
    finding_responses: list[FindingResponse]
    added_interpretations: list[ClassifiedInterpretation] = Field(default_factory=list)
    suggestions: list[Suggestion] | None = None
    no_security_checks_reason: str | None = Field(
        default=None, description="Required when withdrawing every interpretation leaves no checks. "
        "Explain why this unchanged subject has no security-relevant checks.",
    )


class ReviewChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interpretation_index: StrictInt = Field(ge=0)
    replacement: ClassifiedInterpretationReview


class AssessmentCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    critic_supported: StrictBool
    reason: str = Field(min_length=1, max_length=2000)
    findings: list[MaterialAssessmentFinding]
    review_changes: list[ReviewChange]


def normalize_correction(patch):
    """Resolve typed containment only; never match issue prose to independent findings."""
    result = patch.model_copy(deep=True)
    issues = {}
    for issue in result.issues:
        if issue.id in issues:
            raise ValueError("Duplicate root correction issue ID")
        issues[issue.id] = issue
    for response in result.finding_responses:
        if response.action == "rejected" and response.issues:
            raise ValueError("Rejected findings cannot contain accepted blocking issues")
        for issue in response.issues:
            if issue.id in issues and issues[issue.id] != issue:
                raise ValueError("Conflicting correction issue definitions")
            issues[issue.id] = issue
        response.issue_ids = list(dict.fromkeys(response.issue_ids + [issue.id for issue in response.issues]))
    result.issues = list(issues.values())
    return result


def merge_critic(base, patch, findings, disputed_indices):
    """Keep untouched interpretations; disputed ones require explicit justified dispositions."""
    patch = normalize_correction(patch)
    indices = [row.interpretation_index for row in patch.interpretation_changes]
    if len(indices) != len(set(indices)) or not set(disputed_indices) <= set(indices):
        raise ValueError("Every disputed interpretation requires one explicit change or retention")
    rows = [row.model_copy(deep=True) for row in base.interpretations]
    removed = set()
    for change in patch.interpretation_changes:
        if change.interpretation_index >= len(rows):
            raise ValueError("Unknown interpretation change index")
        if (change.action == "revised") != (change.replacement is not None):
            raise ValueError("Revised interpretations require replacements; retained interpretations forbid them")
        if change.action == "withdrawn":
            removed.add(change.interpretation_index)
        if change.replacement is not None:
            rows[change.interpretation_index] = change.replacement
    rows.extend(patch.added_interpretations)
    references = [row.finding_reference for row in patch.finding_responses]
    if len(references) != len(set(references)) or set(references) != set(findings):
        raise ValueError("Every independent finding requires one explicit response")
    issue_ids = {row.id for row in patch.issues}
    changed = set(indices) | set(range(len(base.interpretations), len(rows)))
    revised = {row.interpretation_index for row in patch.interpretation_changes if row.action != "retained"}
    revised |= set(range(len(base.interpretations), len(rows)))
    for response in patch.finding_responses:
        if not set(response.issue_ids) <= issue_ids or not set(response.interpretation_indices) <= changed:
            raise ValueError("Finding response references unknown issues or untouched interpretations")
        if response.action == "accepted" and not (response.issue_ids or set(response.interpretation_indices) & revised):
            raise ValueError("Accepted findings require an issue or actual interpretation revision/withdrawal")
    rows = [row for index, row in enumerate(rows) if index not in removed]
    reason = base.no_security_checks_reason if patch.no_security_checks_reason is None else patch.no_security_checks_reason
    return Critique(approved=not patch.issues, decision_origin="controller_findings", reason=patch.reason,
                    category=base.category, issues=patch.issues, issue_dispositions=patch.issue_dispositions,
                    interpretations=rows, suggestions=base.suggestions if patch.suggestions is None else patch.suggestions,
                    no_security_checks_reason="" if rows else reason)


def merge_assessment(base, patch):
    rows = sorted((row.model_copy(deep=True) for row in base.interpretation_reviews),
                  key=lambda row: row.interpretation_index)
    if [row.interpretation_index for row in rows] != list(range(len(rows))):
        raise ValueError("Retained reviews require contiguous unique indices")
    indices = [row.interpretation_index for row in patch.review_changes]
    if len(indices) != len(set(indices)):
        raise ValueError("Duplicate interpretation review changes")
    for change in patch.review_changes:
        if change.interpretation_index >= len(rows):
            raise ValueError("Unknown interpretation review change index")
        if change.replacement.interpretation_index != change.interpretation_index:
            raise ValueError("Review replacement must retain its interpretation index")
        rows[change.interpretation_index] = change.replacement
    return IssueAssessment(critic_supported=patch.critic_supported, reason=patch.reason,
                           findings=patch.findings, interpretation_reviews=rows)
