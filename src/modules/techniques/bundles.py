"""Flat component planning and operation-local bundle delivery."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from modules.techniques.alignment import RequestAlignment, align_request, phase_guidance
from modules.techniques.context import LookupDecision
from modules.techniques.coverage import Requirement
from modules.techniques.documents import toon


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    request: str = Field(min_length=1, max_length=2000)
    purpose: str = Field(min_length=1, max_length=1000)
    request_excerpt: str = Field(default="", max_length=2000)
    requirements: list[Requirement] = Field(min_length=1, max_length=16)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    components: list[Component] = Field(min_length=1, max_length=16)


class PlanReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: StrictBool
    reason: str = Field(min_length=1, max_length=2000)


class Delivered(BaseModel):
    component: Component
    revision_id: str
    markdown: str
    applicability: LookupDecision | None = None


class Unresolved(BaseModel):
    component: Component
    reason: str


class TechniqueBundle(BaseModel):
    alignment: RequestAlignment | None = None
    delivered: list[Delivered] = Field(default_factory=list)
    unresolved: list[Unresolved] = Field(default_factory=list)

    def render(self) -> str:
        lines = ["# Technique bundle", (f"Validated components: {len(self.delivered)}. "
                  f"Unresolved components: {len(self.unresolved)}.")]
        if self.alignment:
            lines.append(self.alignment.render())
        if self.unresolved:
            lines.append("Incomplete guidance: resolve the listed gaps before relying on full request coverage.")
        rendered_revisions = set()
        for item in self.delivered:
            document = (f"See validated revision {item.revision_id} above." if item.revision_id in rendered_revisions
                        else item.markdown)
            rendered_revisions.add(item.revision_id)
            lines.append(f"\n## {item.component.id}: {item.component.purpose}\n\n{document}")
            if item.applicability:
                lines.append(item.applicability.render())
        for item in self.unresolved:
            lines.append(f"\n## Unresolved: {item.component.id}\n{item.component.purpose}\n{item.reason}")
        return "\n".join(lines) + "\n"


def resolve_bundle(service, request, context=None, operation="", task="") -> TechniqueBundle:
    request = request.strip()
    service._validate_request(request)
    alignment, context = align_request(service, request, context)
    planning_rules = (
        "Identify independently reusable methodology requests, not procedural steps. "
        "Split requested methods that independently produce useful guidance, even when they share an "
        "endpoint, subject or phase. Never split merely because a method is broad, "
        "complex, has multiple steps or intermediate artifacts. Extraction, prerequisite identification, "
        "criteria mapping and validation form ONE CVE applicability methodology. SQL injection testing "
        "and TLS configuration assessment are two separately requested methods. Preserve ALL requested "
        "mechanisms and deliverables without inventing summary documents, matrices or CWE requirements. "
        "For an LLM endpoint request covering prompt injection, jailbreak, tool-mediated SSRF, JSON mass"
        " assignment and CVE validation, use four methods: prompt injection/jailbreak; SSRF; mass "
        "assignment; CVE applicability/validation. This is an example, not a fixed component count. "
        "Carry shared requested deliverables into each applicable component. Separate independently "
        "assessable obligations. Planner instructions, including citing original request excerpts, are "
        "provenance rules and must not become document requirements unless the user requested them. "
        "Task purpose is applicability context, not a source of extra deliverables. Do not import "
        "operation terminal dispositions, completed execution or findings into reusable guidance. "
        "Classify each requirement kind as factual_support, authored_deliverable or constraint. "
        "Component requests must be complete methodologies preserving subject identifiers, mechanisms "
        "and deliverables, never phase labels. "
        "Each component must include request_excerpt: an exact original request excerpt establishing its "
        "methodology. Remove incidental target details from reusable requests. Do not require HTTP targets, "
        "inventory, authentication or live execution. Keep a flat, deduplicated plan. "
    )
    planning_rules += (
        phase_guidance(context) + f"Phase-aligned request: {alignment.adjusted_request}\n"
        "Plan the aligned methodology while preserving the original mechanisms and explicit "
        "deliverables. Cite excerpts from the ORIGINAL request.\n"
    )
    correction = ""
    for attempt in range(2):
        with service.events.stage("planning"):
            plan = service._json(
                "technique_planner",
                planning_rules + f"Original request: {request}\n" + correction,
                Plan,
            )
        if len(plan.components) == 1:
            plan.components[0].request = alignment.adjusted_request
        with service.events.stage("plan_review"):
            review = service._json(
                "technique_plan_reviewer",
                "Independently check the plan adds no unrelated requirements and preserves each requested "
                "methodology. Check each component request itself preserves its subject, mechanisms and explicit "
                "deliverables, not only its purpose and requirements. Check requirement classifications: "
                "facts need factual_support, procedures need authored_deliverable, restrictions need constraint. "
                "Reject under-splitting independent methods, omitted obligations and requirements derived from "
                "planner instructions. Rejection must name the affected method or requirement and the requested "
                "correction. Reject phase-only requests. Reject splitting one workflow into supporting steps or "
                "invented deliverables. "
                + planning_rules
                + f"Original request: {request}\n"
                + toon("components", [item.model_dump() for item in plan.components]),
                PlanReview,
            )
        invalid_excerpt = any(
            not item.request_excerpt or item.request_excerpt not in request for item in plan.components
        )
        if review.approved and not invalid_excerpt:
            break
        reason = "Each component requires an exact original request excerpt" if invalid_excerpt else review.reason
        service.events.emit("technique_plan_rejected", correction_attempt=attempt,
                            reason=service._safe_feedback(reason),
                            components=[item.model_dump() for item in plan.components])
        if attempt:
            raise ValueError("Technique planning left unresolved request coverage: " + service._safe_feedback(reason))
        correction = (
            "Rejected plan (untrusted): "
            + plan.model_dump_json()
            + "\nRepair planning: "
            + service._safe_feedback(reason)
        )
    ids = [item.id for item in plan.components]
    if len(set(ids)) != len(ids):
        raise ValueError("Component identifiers must be unique")
    service.events.emit("technique_plan_accepted", components=[item.model_dump() for item in plan.components])
    bundle = TechniqueBundle(alignment=alignment)
    seen = set()
    receipts = {}
    for component in plan.components:
        service._validate_request(component.request)
        for requirement in component.requirements:
            service._validate_request(requirement.description)
        requirement_ids = [row.id for row in component.requirements]
        if len(set(requirement_ids)) != len(requirement_ids):
            raise ValueError("Requirement identifiers must be unique within a component")
        key = (
            " ".join(component.request.lower().split()),
            tuple(sorted(row.description.lower() for row in component.requirements)),
        )
        if key in seen:
            continue
        seen.add(key)
        component_context = context
        if context is not None and len(plan.components) > 1:
            component_context = context.model_copy(deep=True, update={"purpose": component.request})
        decisions = []
        with service.events.scope(operation_id=operation, task_uid=task, component_id=component.id):
            try:
                revision, markdown = service.get(
                    component.request,
                    component_context,
                    on_decision=decisions.append,
                    requirements=component.requirements,
                )
            except Exception as error:
                reason = service._safe_feedback(str(error))
                bundle.unresolved.append(Unresolved(component=component, reason=reason))
                service.events.emit("technique_build_progress", stage="component", status="unresolved", reason=reason)
                continue
        decision = decisions[0] if decisions else None
        bundle.delivered.append(
            Delivered(component=component, revision_id=revision, markdown=markdown, applicability=decision)
        )
        if operation and task:
            receipt = decision.receipt(context) if decision else {}
            purpose = {
                "component_id": component.id,
                "component_purpose": component.purpose,
                "requirements": [row.model_dump() for row in component.requirements],
            }
            if revision not in receipts:
                receipts[revision] = {**receipt, **purpose, "components": []}
            receipts[revision]["components"].append({**receipt, **purpose})
    for revision, receipt in receipts.items():
        service.repository.use(operation, task, revision, receipt)
    if not bundle.delivered:
        raise ValueError(
            "No technique components resolved. "
            + "; ".join(f"{item.component.id}: {item.reason}" for item in bundle.unresolved)
        )
    return bundle
