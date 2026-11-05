"""Controller-selected activity context; prose never selects feedback policy."""

from dataclasses import asdict, dataclass

SUPPLEMENTS = {
    "hypothesis_generation": (
        "Evaluate recorded hypotheses, prerequisites, controlled variations, exclusions, evidence "
        "requirements and future validation plans. Actually producing these is observed task behavior. "
        "Target engagement and confirmed vulnerabilities are not required. Execution proposed inside a "
        "hypothesis remains hypothetical. Unsupported mechanisms and labeling legitimate behavior as a "
        "violation can demonstrate harm. Do not require execution of the proposed tests."
    ),
    "execution": (
        "Evaluate guidance applied to performed actions, controls, comparisons and evidence collection. "
        "A confirmed vulnerability is not required for methodological contribution."
    ),
    "verification": (
        "Evaluate confirmation, refutation, narrowing and justified exclusion of suspected findings. "
        "Correct rejection of a finding can be helpful; do not reward confirmation alone."
    ),
    "analysis": (
        "Evaluate improved inventory, relationships, prioritization, source interpretation and "
        "evidence-backed exclusions. Target engagement is not universally required."
    ),
    "unknown": (
        "Activity metadata is unavailable or ambiguous. Apply the shared rubric to demonstrated "
        "task deliverables. Unknown activity does not itself imply neutrality."
    ),
}


@dataclass(frozen=True)
class FeedbackTaskContext:
    task_kind: str = "unknown"
    requirement_kinds: tuple[str, ...] = ()
    phase_activities: tuple[str, ...] = ()
    profile: str = "unknown"
    provenance: str = "missing_metadata"

    def render(self):
        from modules.techniques.documents import json_payload

        return ("\n" + json_payload("feedback_task_context", asdict(self))
                + "\nActivity-specific contribution guidance: " + SUPPLEMENTS[self.profile] + "\n")


def select_feedback_activity(task, phase=None):
    """Prefer frozen deliverables and explicit validation kinds over phase context."""
    acceptance = getattr(task, "acceptance", None)
    task_kind = getattr(task, "kind", "unknown")
    criteria = getattr(acceptance, "criteria", ())
    kinds = tuple(sorted({r.kind for c in criteria for r in c.evidence_requirements}))
    activities = tuple(phase.technique_contexts) if phase is not None else ()
    direct = set()
    if "hypothesis_set" in kinds:
        direct.add("hypothesis_generation")
    if task_kind in {"finding_validation", "objective_validation"}:
        direct.add("verification")
    if direct:
        profiles, origin = direct, "task_contract"
    else:
        mapping = {"hypothesis_generation": "hypothesis_generation", "execution": "execution",
                   "vulnerability_verification": "verification", "reconnaissance": "analysis", "analysis": "analysis"}
        profiles = {mapping[a] for a in activities if a in mapping}
        if any(a not in mapping for a in activities):
            profiles.add("unknown")
        if phase is not None and phase.produces_hypotheses:
            profiles.add("hypothesis_generation")
        origin = "phase_metadata" if profiles else "missing_metadata"
    profile = next(iter(profiles)) if len(profiles) == 1 else "unknown"
    if len(profiles) > 1:
        origin = "conflicting_metadata"
    return FeedbackTaskContext(task_kind, kinds, activities, profile, origin)
