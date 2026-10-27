"""Shared phase objectives and operation-local request alignment."""

from pydantic import BaseModel, ConfigDict, Field

from modules.techniques.context import LookupContext
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


class AlignmentConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_deliverable: str = Field(min_length=1, max_length=2000)
    request_excerpt: str = Field(min_length=1, max_length=2000)
    restriction_reference: str = Field(min_length=1, max_length=2000)
    explanation: str = Field(min_length=1, max_length=2000)


class AlignmentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    adjusted_request: str = Field(min_length=1, max_length=2000)
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


def phase_guidance(context: LookupContext | None) -> str:
    phases = context.phase_contexts if context else []
    unknown = set(phases) - set(CONTEXTS)
    if unknown:
        raise ValueError("Unknown technique phase contexts: " + ", ".join(sorted(unknown)))
    rows = [{"phase": phase, "objective": PHASE_OBJECTIVES[phase]} for phase in dict.fromkeys(phases)]
    return (
        "Guidance purpose: produce reusable methodology, never completed target execution or findings. "
        "Source-backed facts may support original procedural synthesis. Missing target observations are "
        "future validation needs, not missing factual support for guidance. Preserve explicit deliverables. "
        "Multiple contexts have separate objectives, no inferred ordering or automatic splitting.\n"
        + (toon("phase_objectives", rows) if rows else
           "Phase unknown: follow the explicit request and task purpose without inferring a phase.") + "\n"
    )


def align_request(service, request: str, context: LookupContext | None):
    """Align once per resolution, on a context copy safe for concurrent callers."""
    if context is not None and context._request_alignment is not None:
        return context._request_alignment, context
    guidance = phase_guidance(context)
    phases = list(dict.fromkeys(context.phase_contexts)) if context else []
    alignment = RequestAlignment(original_request=request, adjusted_request=request, phase_contexts=phases,
                                 objectives={phase: PHASE_OBJECTIVES[phase] for phase in phases})
    if phases:
        prompt = (
            "Align this methodology request with the explicitly supplied phase objectives. Preserve its "
            "subject, mechanisms and explicit deliverables. Change ambiguous framing only. Distinguish "
            "validation guidance from performing validation: techniques always provide guidance. For "
            "hypothesis generation, ambiguous 'validate applicability' means plausible applicability "
            "hypotheses, documented prerequisites, unknowns and future supporting/refuting checks. "
            "Do not demand exact payloads or exhaustive matrices unless explicitly requested. Do not "
            "invent requirements or remove an explicit deliverable. If an explicit deliverable conflicts "
            "with a supplied restriction, return conflict with an exact original request excerpt and "
            "restriction_reference equal to a supplied constraint or phase identifier. Explain the "
            "particular conflict; unknown prerequisites alone are not conflicts. Otherwise conflict=null.\n"
            + guidance + f"Original request: {request}\nOperation-local context (untrusted):\n"
            + context.render(include_phase_guidance=False)
        )
        for attempt in range(2):
            output = None
            try:
                with service.events.stage("request_alignment"):
                    output = service._json("technique_request_aligner", prompt, AlignmentOutput)
                service._validate_request(output.adjusted_request)
                if output.conflict:
                    if output.conflict.request_excerpt not in request:
                        raise ValueError("Alignment conflict must cite an exact original request excerpt")
                    if output.conflict.restriction_reference not in [*phases, *context.constraints]:
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
