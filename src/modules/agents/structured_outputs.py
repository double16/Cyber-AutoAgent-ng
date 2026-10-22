"""Typed contracts for model-authored structured output."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, model_validator

from modules.tools.artifact_references import require_artifact_reference
from modules.tools.memory import TaskProposal
from modules.utils.json_repair import normalize_json_key_case


class StructuredOutputUnavailableError(RuntimeError):
    """The active provider/model cannot complete the requested structured-output protocol."""


def is_structured_output_unavailable(error: BaseException) -> bool:
    """Return whether an error warrants the validated JSON compatibility path.

    This intentionally excludes malformed model data and operational failures.  Those retain their
    existing retry or propagation behavior; only an unavailable structured-output transport falls
    back to prompted JSON.
    """

    pending: list[BaseException | None] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (StructuredOutputUnavailableError, NotImplementedError, AttributeError)):
            return True
        if current.__class__.__name__ == "StructuredOutputException":
            return True
        message = str(current).lower()
        if "toolchoice" in message and "not supported" in message:
            return True
        if "structured output" in message and "not supported" in message:
            return True
        pending.extend((current.__cause__, current.__context__))
    return False


class StrictStructuredOutput(BaseModel):
    """Closed schema used at model output boundaries."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _normalize_model_authored_key_case(cls, value: Any) -> Any:
        """Accept case-only key variants while retaining the canonical closed schema."""

        return normalize_json_key_case(value).value


class PlanPhaseOutput(StrictStructuredOutput):
    id: int
    title: str
    produces_hypotheses: bool
    status: str = "pending"
    criteria: str = ""
    requires_finding_candidates: bool = False
    task_creation_mode: Literal[
        "standard",
        "snapshot_dependent",
        "hypothesis_dependent",
        "finding_dependent",
        "finding_validation",
    ] = "standard"
    controller_owned_phase_kind: str = ""
    provided_workstreams: list[str] = Field(default_factory=list)


class PlanOutput(StrictStructuredOutput):
    objective: str
    constraints: list[str] = Field(default_factory=list)
    current_phase: int = 1
    phases: list[PlanPhaseOutput] = Field(min_length=1)


class CritiqueOutput(StrictStructuredOutput):
    approved: StrictBool
    feedback: list[str] = Field(default_factory=list)
    repairable: bool | None = None


class TaskPromptIssueOutput(StrictStructuredOutput):
    category: Literal["scope", "objective", "evidence", "execution_policy", "tool_availability"]
    message: str = Field(min_length=1, max_length=500)
    tool_names: list[str] = Field(default_factory=list)


class TaskPromptCritiqueOutput(StrictStructuredOutput):
    issues: list[TaskPromptIssueOutput] = Field(max_length=5)
    repairable: bool = True


class FlowCritiqueOutput(CritiqueOutput):
    supporting_evidence_refs: list[str] = Field(default_factory=list)


class TaskPromptOutput(StrictStructuredOutput):
    prompt: str
    memory_ids: list[str] | None = None
    tools: list[str] = Field(default_factory=list)
    shell_commands: list[str] = Field(default_factory=list)


class TaskPhaseDecisionOutput(StrictStructuredOutput):
    task_uid: str
    preserve_requested_phase: bool
    reason: str


class TaskPhaseClassificationOutput(StrictStructuredOutput):
    decisions: list[TaskPhaseDecisionOutput]


class EvaluatorRepairOutput(StrictStructuredOutput):
    kind: Literal["none", "acceptance", "execution"] = "none"
    evidence_gaps: list[str] = Field(default_factory=list)
    execution_receipt_gaps: list[str] = Field(default_factory=list)


class FindingRecommendationOutput(StrictStructuredOutput):
    required: StrictBool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


ArtifactEvidenceReference = Annotated[str, BeforeValidator(require_artifact_reference), Field(
    pattern=r"^artifact(?:_id)?:\s*\S",
    description="Current-task artifact: or artifact_id: reference only; memory: and other kinds are unsupported.",
)]


class OperationSupportOutput(StrictStructuredOutput):
    status: Literal["established", "unestablished"]
    basis: Literal["observed_intended_behavior", "established_access_policy", "availability_only", "unestablished"]
    evidence_refs: list[ArtifactEvidenceReference] = Field(default_factory=list)


class ComparisonAssessmentOutput(StrictStructuredOutput):
    """Semantic support for an intended operation and its identity comparison."""

    outcome: Literal["supported", "inconclusive"]
    operation_support: OperationSupportOutput
    operation_evidence_refs: list[ArtifactEvidenceReference] = Field(default_factory=list)
    comparison_evidence_refs: list[ArtifactEvidenceReference] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class TaskReviewDecisionOutput(StrictStructuredOutput):
    status: str
    reason: str = ""
    instructions: str = ""
    repair: EvaluatorRepairOutput | None = None


class WorkflowDecisionOutput(TaskReviewDecisionOutput):
    comparison_assessment: ComparisonAssessmentOutput | None = None
    finding_recommendation: FindingRecommendationOutput | None = None


class ComparisonTaskDecisionOutput(WorkflowDecisionOutput):
    comparison_assessment: ComparisonAssessmentOutput


class HypothesisRecordReview(StrictStructuredOutput):
    hypothesis_id: str
    decision: Literal["retain", "revise", "withdraw"]
    reason: str = Field(min_length=1)


class HypothesisBehaviorAssessment(StrictStructuredOutput):
    observed_behavior: str = Field(min_length=1)
    inspection_refs: list[ArtifactEvidenceReference] = Field(min_length=1)
    supports_conditional_test: bool
    reasoning: str = Field(min_length=1)


class NoHypothesisBasis(StrictStructuredOutput):
    kind: Literal["inspected_evidence", "metadata_only", "necessary_evidence_unreviewed"]
    inspection_refs: list[ArtifactEvidenceReference] = Field(default_factory=list)
    behavior_assessments: list[HypothesisBehaviorAssessment] | None = None
    no_relevant_behavior_reason: str | None = Field(default=None, min_length=1)


class HypothesisSubjectReview(StrictStructuredOutput):
    item_id: str
    decision: Literal["retain", "revise", "withdraw"]
    reason: str = Field(min_length=1)
    hypotheses: list[HypothesisRecordReview]
    no_hypothesis_basis: NoHypothesisBasis | None = None


class HypothesisPrerequisiteGap(StrictStructuredOutput):
    criterion_id: str
    missing_input: str = Field(min_length=1)
    item_ids: list[str] = Field(min_length=1)
    source_refs: list[ArtifactEvidenceReference] = Field(min_length=1)


class HypothesisTaskDecisionOutput(TaskReviewDecisionOutput):
    publication_sha256: str
    subject_reviews: list[HypothesisSubjectReview]
    prerequisite_gaps: list[HypothesisPrerequisiteGap] = Field(default_factory=list)
    unresolved_criterion_ids: list[str] = Field(default_factory=list)


class HypothesisCheckpointDecisionOutput(TaskReviewDecisionOutput):
    prerequisite_gaps: list[HypothesisPrerequisiteGap]


class TaxonomyMappingOutput(StrictStructuredOutput):
    id: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence: list[str]


class TaxonomyAnnotationOutput(StrictStructuredOutput):
    cwe: list[TaxonomyMappingOutput] = Field(default_factory=list)
    mitre_attack: list[TaxonomyMappingOutput] = Field(default_factory=list)


class AttackEnrichmentOutput(StrictStructuredOutput):
    mitre_attack: list[TaxonomyMappingOutput] = Field(default_factory=list)


class TaskProposalBatchOutput(StrictStructuredOutput):
    tasks: list[TaskProposal] = Field(min_length=1)


class ReportCritiqueOutput(StrictStructuredOutput):
    approved: bool
    feedback: list[str] = Field(default_factory=list, max_length=5)


class BudgetRecommendationOutput(StrictStructuredOutput):
    dimension: str
    current: float
    recommended: float = Field(gt=0)
    rationale: str


class ReportNextStepsOutput(StrictStructuredOutput):
    coverage_gaps: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    budget_recommendations: list[BudgetRecommendationOutput]
    agent_improvements: list[str] = Field(default_factory=list)
    tooling_improvements: list[str] = Field(default_factory=list)
    manual_investigations: list[str] = Field(default_factory=list)


class EvaluationPolicyOutput(StrictStructuredOutput):
    caps: dict[str, float] = Field(default_factory=dict)
    disable: list[str] = Field(default_factory=list)


class RubricJudgeOutput(StrictStructuredOutput):
    scores: dict[str, float] = Field(default_factory=dict)
    overall: float | None = None
    rationale: str = ""
    insufficient_evidence: bool = False


class TopicsOutput(StrictStructuredOutput):
    topics: list[str] = Field(min_length=1, max_length=12)


WORKFLOW_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "task_creator": TaskProposalBatchOutput,
    "plan_creator": PlanOutput,
    "plan_critic": CritiqueOutput,
    "authentication_flow_critic": FlowCritiqueOutput,
    "registration_flow_critic": FlowCritiqueOutput,
    "task_prompt_builder": TaskPromptOutput,
    "task_prompt_critic": TaskPromptCritiqueOutput,
    "task_phase_classifier": TaskPhaseClassificationOutput,
    "task_evaluator": WorkflowDecisionOutput,
    "phase_evaluator": WorkflowDecisionOutput,
    "taxonomy_annotator": TaxonomyAnnotationOutput,
    "attack_enricher": AttackEnrichmentOutput,
}


def structured_output_dict(value: Any) -> dict[str, Any]:
    """Normalize a structured result without reparsing model text."""

    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True, exclude_unset=True)
    if isinstance(value, dict):
        return value
    raise ValueError(f"structured output must be a Pydantic model or dict, received {type(value).__name__}")
