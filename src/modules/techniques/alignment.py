"""Shared phase objectives and methodology request alignment."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from modules.techniques.context import MethodologyContext, methodology_context
from modules.techniques.documents import CONTEXTS, toon

PHASE_OBJECTIVES = {
    "reconnaissance": "Discover and characterize relevant surfaces, components, versions and trust boundaries; "
                      "preserve unknowns rather than require confirmed vulnerabilities.",
    "hypothesis_generation": "Form plausible source-backed hypotheses with known prerequisites, explicit "
                             "assumptions and future supporting or refuting checks. Target evidence, exact "
                             "payloads, exhaustive product matrices and ready-made checklists are not "
                             "automatically required. Unknown target conditions become future checks.",
    "vulnerability_verification": "Describe validation methods, controls, evidence requirements and criteria "
                                  "to confirm or reject a hypothesis; do not require completed target validation.",
    "execution": "Describe actionable procedures with prerequisites, observable outcomes, scope constraints "
                 "and cleanup where applicable; do not require proof that execution has already occurred.",
    "analysis": "Interpret sources, code, configurations or observations, distinguishing supported conclusions "
                "from uncertainty; do not require live execution.",
}


class PreservationReview(BaseModel):
    """Semantic preservation findings; references identify controller-supplied fields."""

    model_config = ConfigDict(extra="forbid")
    approved: StrictBool
    reason: str = Field(min_length=1, max_length=2000)
    subject_preserved: StrictBool
    mechanisms_preserved: StrictBool
    deliverables_preserved: StrictBool
    additions: list[str] = Field(max_length=16)
    omissions: list[str] = Field(max_length=16)
    references: list[Literal[
        "original_request", "component_request", "requirements", "phase_contexts", "constraints",
    ]] = Field(min_length=1, max_length=8)

    def validate_decision(self) -> None:
        if len(self.references) != len(set(self.references)):
            raise ValueError("Preservation references must identify unique supplied input fields")
        preserved = (self.subject_preserved and self.mechanisms_preserved
                     and self.deliverables_preserved and not self.additions and not self.omissions)
        if self.approved != preserved:
            raise ValueError("Preservation approval must agree with all findings")


PRESERVATION_RULES = (
    "Review semantic request preservation. The original tool request is authoritative. "
    "Phase and methodology restrictions do not invent additional deliverables. Reject transformation "
    "instructions copied as the methodology, phase-only requests, omitted mechanisms, and invented "
    "obligations. Operation terminal dispositions and completed target execution are not reusable "
    "methodology deliverables. Preserve explicitly requested CVEs and legitimate restrictions. "
    "Report subject, mechanism and deliverable preservation, additions and omissions. "
    "Approve only when all are preserved and there are no additions or omissions. "
    "Clarifying phase framing and abstracting incidental target details are allowed when requested "
    "methods and deliverables remain intact. "
    "Cite only original_request, component_request, requirements, phase_contexts or constraints "
    "in references; never reproduce formatted text as proof or invent indexed reference paths. "
    "A component preserves only its assigned method; sibling methods remain bundle context. "
)


def emit_preservation_review(service, review):
    """Expose semantic findings without leaking generated secrets in nested lifecycle fields."""
    findings = review.model_dump()
    for key in ("additions", "omissions"):
        findings[key] = [service._safe_feedback(value) for value in findings[key]]
    findings["reason"] = service._safe_feedback(review.reason)
    service.events.emit("technique_request_review", findings=findings)


class AlignmentConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_deliverable: str = Field(min_length=1, max_length=2000)
    request_excerpt: str = Field(min_length=1, max_length=2000)
    restriction_reference: str = Field(min_length=1, max_length=2000)
    explanation: str = Field(min_length=1, max_length=2000)


class AlignmentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    adjusted_request: str = Field(
        min_length=1, max_length=2000,
        description="The requested methodology itself, never instructions for rewriting the request",
    )
    reason: str = Field(min_length=1, max_length=2000)
    conflict: AlignmentConflict | None = None


class RequestAlignment(BaseModel):
    """Controller-owned provenance; never global technique metadata."""

    original_request: str
    adjusted_request: str
    phase_contexts: list[str] = Field(default_factory=list)
    objectives: dict[str, str] = Field(default_factory=dict)
    reason: str = "Phase unknown; preserve the explicit request without inferring a phase."

    def render(self) -> str:
        if self.original_request == self.adjusted_request:
            return ""
        return ("\n## Request alignment (operation-local)\n"
                f"Original request: {self.original_request}\n"
                f"Phase-aligned request: {self.adjusted_request}\nReason: {self.reason}\n")


def phase_guidance(context: MethodologyContext | None) -> str:
    phases = context.phase_contexts if context else []
    unknown = set(phases) - set(CONTEXTS)
    if unknown:
        raise ValueError("Unknown technique phase contexts: " + ", ".join(sorted(unknown)))
    rows = [{"phase": phase, "objective": PHASE_OBJECTIVES[phase]} for phase in dict.fromkeys(phases)]
    policy = ""
    if context:
        policy = (toon("module_technique_policy", context.module_technique_policy)
                  + "\n" + toon("user_methodology_restrictions", [
                      {"restriction": value, "origin": "user"} for value in context.methodology_constraints
                  ]))
    return (
        policy + "\n" +
        "Apply supplied module technique policy and user methodology restrictions to reusable guidance. "
        "Guidance purpose: produce reusable methodology, never completed target execution or findings. "
        "Source-backed facts may support original procedural synthesis. Missing target observations are "
        "future validation needs, not missing factual support for guidance. Preserve explicit deliverables. "
        "Multiple contexts have separate objectives, no inferred ordering or automatic splitting.\n"
        + (toon("phase_objectives", rows) if rows else
           "Phase unknown: follow the explicit request without inferring a phase.") + "\n"
    )


def align_request(service, request: str, context: MethodologyContext | None):
    """Align once per resolution, on a context copy safe for concurrent callers."""
    context = methodology_context(context)
    policy = service.module_technique_policy
    if policy and (context is None or context.module_technique_policy != policy):
        context = (context or MethodologyContext()).model_copy(deep=True)
        context.module_technique_policy = list(policy)
    if context is not None and context._request_alignment is not None:
        return context._request_alignment, context
    guidance = phase_guidance(context)
    phases = list(dict.fromkeys(context.phase_contexts)) if context else []
    alignment = RequestAlignment(original_request=request, adjusted_request=request, phase_contexts=phases,
                                 objectives={phase: PHASE_OBJECTIVES[phase] for phase in phases})
    if phases or (context and (context.methodology_constraints or context.module_technique_policy)):
        prompt = (
            "Align this methodology request with the explicitly supplied phase objectives. Preserve its "
            "subject, mechanisms and explicit deliverables. Change ambiguous framing only. Distinguish "
            "validation guidance from performing validation: techniques always provide guidance. For "
            "hypothesis generation, ambiguous 'validate applicability' means plausible applicability "
            "hypotheses, documented prerequisites, unknowns and future supporting/refuting checks. "
            "Do not demand exact payloads or exhaustive matrices unless explicitly requested. Do not "
            "invent requirements or remove an explicit deliverable. Only Original request defines the "
            "requested methodology; task objectives and execution context are not supplied to this workflow. Return the methodology itself, never these instructions. "
            "If a methodology restriction is ambiguous or requires incidental target details, report an "
            "explicit conflict rather than ignoring it or converting it into an operation restriction. "
            "If an explicit deliverable conflicts "
            "with a supplied restriction, return conflict with an exact original request excerpt and "
            "restriction_reference equal to a supplied constraint or phase identifier. Explain the "
            "particular conflict; unknown prerequisites alone are not conflicts. Otherwise conflict=null.\n"
            + guidance + f"Original request: {request}\nPhase and restriction data (not deliverables):\n"
            + toon("alignment_context", [{"phase_contexts": phases, "constraints": context.methodology_constraints}])
        )
        for attempt in range(2):
            output = None
            try:
                with service.events.scope(correction_attempt=attempt), service.events.stage("request_alignment"):
                    output = service._json("technique_request_aligner", prompt, AlignmentOutput)
                service._validate_request(output.adjusted_request)
                if output.conflict is None and output.adjusted_request != request:
                    with service.events.scope(transformation="alignment", correction_attempt=attempt):
                        with service.events.stage("request_preservation"):
                            review = service._json(
                                "technique_request_reviewer",
                                PRESERVATION_RULES + guidance + f"\noriginal_request (authoritative): {request}"
                                + f"\ncomponent_request: {request}\nrequirements: []"
                                + f"\nphase_contexts: {phases}\nconstraints: {context.methodology_constraints}"
                                + f"\nProposed methodology: {output.adjusted_request}",
                                PreservationReview,
                            )
                            review.validate_decision()
                        emit_preservation_review(service, review)
                    if not review.approved:
                        raise ValueError("Alignment preservation rejected: " + review.model_dump_json())
                if output.conflict:
                    if output.conflict.request_excerpt not in request:
                        raise ValueError("Alignment conflict must cite an exact original request excerpt")
                    restrictions = [*phases, *context.methodology_constraints,
                                    *(row["restriction"] for row in context.module_technique_policy)]
                    if output.conflict.restriction_reference not in restrictions:
                        raise ValueError("Alignment conflict must reference a supplied restriction")
                break
            except ValueError as error:
                if attempt:
                    raise ValueError("Technique request alignment failed after one correction: "
                                     + service._safe_feedback(str(error))) from None
                if output is not None:
                    error.rejected_response = output.model_dump_json()
                prompt = service._repair_prompt(prompt, error)
        if output.conflict:
            service.events.emit("technique_warning", stage="request_alignment",
                                diagnostic=service._safe_feedback(output.conflict.explanation))
            raise ValueError("Technique request alignment conflict: "
                             + service._safe_feedback(output.conflict.explanation))
        alignment.adjusted_request = output.adjusted_request
        alignment.reason = output.reason
    service.events.emit("technique_build_progress", stage="request_alignment", status="completed",
                        **alignment.model_dump())
    if context is not None:
        context = context.model_copy(deep=True)
        context._request_alignment = alignment
    return alignment, context
