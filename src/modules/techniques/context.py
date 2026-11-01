"""Methodology-only lookup inputs and task-local recommendation state."""

import hashlib
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from modules.techniques.documents import json_payload, optional_records_payload

if TYPE_CHECKING:
    from modules.techniques.alignment import RequestAlignment

ObservationOrigin = Literal["source", "analysis", "guidance", "unknown"]
Applicability = Literal["supported", "conditional", "incompatible", "uncertain"]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    sha256: str
    origin: ObservationOrigin = "unknown"
    scope: Literal["subject", "shared"] = "subject"
    excerpt: str = Field(max_length=2000)


class MethodologyContext(BaseModel):
    """Only methodology inputs may reach lookup and generation roles."""

    model_config = ConfigDict(extra="forbid")

    phase_contexts: list[str] = Field(default_factory=list)
    methodology_constraints: list[str] = Field(default_factory=list)
    module_technique_policy: list[dict[str, str]] = Field(default_factory=list)
    _request_alignment: "RequestAlignment | None" = PrivateAttr(default=None)

    def render(self, *, include_phase_guidance: bool = True) -> str:
        from modules.techniques.alignment import phase_guidance

        phases = (json_payload("phase_contexts", self.phase_contexts)
                  if self.phase_contexts else "Phase contexts: unknown")
        return ((phase_guidance(self) if include_phase_guidance else "") + phases + "\n"
                + json_payload("methodology_restrictions", self.methodology_constraints) + "\n")


def methodology_context(context: MethodologyContext | None) -> MethodologyContext | None:
    """Copy allowed fields; legacy task context remains local to recommendations."""
    if context is None:
        return None
    result = MethodologyContext(
        phase_contexts=list(context.phase_contexts),
        methodology_constraints=list(context.methodology_constraints),
        module_technique_policy=[dict(row) for row in context.module_technique_policy],
    )
    result._request_alignment = context._request_alignment.model_copy(deep=True) if context._request_alignment else None
    return result


class LookupContext(MethodologyContext):
    """Legacy task-local recommendation state; normalize before methodology lookup."""

    model_config = ConfigDict(extra="forbid")

    purpose: str = ""
    subjects: list[dict] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)

    def observe(self, text: str, source: str, origin: ObservationOrigin, scope: str) -> None:
        if origin in {"analysis", "guidance"} or not text.strip():
            return
        digest = hashlib.sha256(text.encode()).hexdigest()
        for row in self.observations:
            if row.source == source and row.sha256 == digest:
                if origin == "source":
                    row.origin = "source"
                if scope == "subject":
                    row.scope = "subject"
                return
        self.observations.append(Observation(
            source=source, sha256=digest, origin=origin, scope=scope, excerpt=text[:2000],
        ))
        # Prefer assigned-subject observations to shared excerpts; bound the judge input.
        self.observations.sort(key=lambda row: row.scope != "subject")
        del self.observations[6:]

    def render(self, *, include_phase_guidance: bool = True) -> str:
        from modules.techniques.alignment import phase_guidance

        return (
            (phase_guidance(self) if include_phase_guidance else "")
            + "Bounded excerpts and subject descriptors may be incomplete; missing details remain unknown.\n"
            + json_payload("recommendation_context", {
                "phase_contexts": self.phase_contexts, "purpose": self.purpose[:4000],
                "capabilities": self.capabilities, "methodology_restrictions": self.methodology_constraints,
                "task_constraints": self.constraints,
            }) + "\n"
            + optional_records_payload("subjects", self.subjects[:20], 6000)
            + "\n" + json_payload("observations", [row.model_dump() for row in self.observations])
        )


class ApplicabilityConflict(BaseModel):
    """Conflict with an explicit methodology restriction, never a catalog miss."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["methodology_constraint"]
    requirement: str = Field(min_length=1, max_length=2000)
    context_reference: str = Field(min_length=1, max_length=2000)


class LookupDecision(BaseModel):
    """Candidate-independent assessment of the requested methodology."""

    model_config = ConfigDict(extra="forbid")

    generic_request: str = Field(min_length=1, max_length=2000)
    conflict: ApplicabilityConflict | None = None
    applicability: Applicability = Field(description="Compatibility with the methodology request only")
    unmet_prerequisites: list[str] = Field(max_length=12)
    reason: str = Field(min_length=1, max_length=2000)
    _assessment_provenance: dict = PrivateAttr(default_factory=dict)

    def receipt(self, context: MethodologyContext) -> dict:
        return {
            "request_alignment": (context._request_alignment.model_dump() if context._request_alignment else {}),
            "assessment_provenance": self._assessment_provenance,
            "generic_request": self.generic_request,
            "phase_contexts": context.phase_contexts,
            "applicability": self.applicability,
            "unmet_prerequisites": self.unmet_prerequisites,
            "assessment_scope": "methodology_request",
            "observations": [],
        }

    def render(self) -> str:
        missing = "\n".join(f"- {item}" for item in self.unmet_prerequisites) or "- None identified."
        return (
            "\n## Request compatibility\n"
            f"Assessment: {self.applicability}. {self.reason}\n"
            f"Unmet prerequisites:\n{missing}\n"
            "Generic prerequisites are conditions of the methodology, not verified task conditions. "
            "During hypothesis generation, describe future checks; do not execute them. "
            "The executor determines applicability and execution scope.\n"
        )
