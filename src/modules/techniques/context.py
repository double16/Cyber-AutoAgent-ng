"""Operation-local applicability inputs, kept separate from global guidance."""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from modules.techniques.documents import toon

ObservationOrigin = Literal["source", "analysis", "guidance", "unknown"]
Applicability = Literal["supported", "conditional", "incompatible"]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    sha256: str
    origin: ObservationOrigin = "unknown"
    scope: Literal["subject", "shared"] = "subject"
    excerpt: str = Field(max_length=2000)


class LookupContext(BaseModel):
    """No protocol, subject type, inventory, or live execution capability is required."""

    model_config = ConfigDict(extra="forbid")

    phase_contexts: list[str] = Field(default_factory=list)
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

    def render(self) -> str:
        return (
            "Bounded excerpts and subject descriptors may be incomplete; missing details remain unknown.\n"
            f"Phase contexts: {self.phase_contexts or 'unknown'}\nPurpose: {self.purpose[:4000]}\n"
            f"Capabilities: {', '.join(self.capabilities)[:2000]}\n"
            f"Task constraints: {'; '.join(self.constraints)[:4000]}\n"
            + toon("subjects", self.subjects[:20])[:6000]
            + "\n" + toon("observations", [row.model_dump() for row in self.observations])
        )


class ApplicabilityConflict(BaseModel):
    """A task restriction or subject-source contradiction, never a catalog miss."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["task_constraint", "contradicted_prerequisite"]
    requirement: str = Field(min_length=1, max_length=2000)
    context_reference: str = Field(min_length=1, max_length=2000)


class LookupDecision(BaseModel):
    """Candidate-independent assessment of the requested methodology."""

    model_config = ConfigDict(extra="forbid")

    generic_request: str = Field(min_length=1, max_length=2000)
    conflict: ApplicabilityConflict | None = None
    applicability: Applicability
    unmet_prerequisites: list[str] = Field(max_length=12)
    reason: str = Field(min_length=1, max_length=2000)

    def receipt(self, context: LookupContext) -> dict:
        return {
            "generic_request": self.generic_request,
            "phase_contexts": context.phase_contexts,
            "applicability": self.applicability,
            "unmet_prerequisites": self.unmet_prerequisites,
            "observations": [row.model_dump(exclude={"excerpt"}) for row in context.observations],
        }

    def render(self) -> str:
        missing = "\n".join(f"- {item}" for item in self.unmet_prerequisites) or "- None identified."
        return (
            "\n## Current-task applicability (operation-local guidance)\n"
            f"Assessment: {self.applicability}. {self.reason}\n"
            f"Unmet prerequisites:\n{missing}\n"
            "Establish unmet prerequisites within the assigned scope before mechanism-specific testing. "
            "During hypothesis generation, describe future checks; do not execute them. "
            "Guidance and unknown observations do not establish current-operation facts.\n"
        )
