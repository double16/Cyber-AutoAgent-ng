"""Typed contracts for model-authored structured output."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from modules.tools.memory import TaskProposal


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


class PlanPhaseOutput(StrictStructuredOutput):
    id: int
    title: str
    status: str = "pending"
    criteria: str = ""
    requires_finding_candidates: bool = False
    task_creation_mode: Literal[
        "standard",
        "snapshot_dependent",
        "finding_dependent",
        "finding_validation",
    ] = "standard"


class PlanOutput(StrictStructuredOutput):
    objective: str
    constraints: list[str] = Field(default_factory=list)
    current_phase: int = 1
    phases: list[PlanPhaseOutput] = Field(min_length=1)


class CritiqueOutput(StrictStructuredOutput):
    approved: StrictBool
    feedback: list[str] = Field(default_factory=list)
    repairable: bool | None = None


class TaskPromptOutput(StrictStructuredOutput):
    prompt: str
    memory_indices: list[int] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
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


class FindingRecommendationOutput(StrictStructuredOutput):
    required: StrictBool
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class WorkflowDecisionOutput(StrictStructuredOutput):
    status: str
    reason: str = ""
    instructions: str = ""
    repair: EvaluatorRepairOutput | None = None
    finding_recommendation: FindingRecommendationOutput | None = None


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
    "task_prompt_builder": TaskPromptOutput,
    "task_prompt_critic": CritiqueOutput,
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
