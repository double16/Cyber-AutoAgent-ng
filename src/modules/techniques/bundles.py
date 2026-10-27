"""Flat component planning and operation-local bundle delivery."""

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from modules.techniques.context import LookupDecision
from modules.techniques.coverage import Requirement
from modules.techniques.documents import toon


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    request: str = Field(min_length=1, max_length=2000)
    purpose: str = Field(min_length=1, max_length=1000)
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
    delivered: list[Delivered] = Field(default_factory=list)
    unresolved: list[Unresolved] = Field(default_factory=list)

    def render(self) -> str:
        lines = ["# Technique bundle", (f"Validated components: {len(self.delivered)}. "
                  f"Unresolved components: {len(self.unresolved)}.")]
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
    with service.events.stage("planning"):
        plan = service._json(
            "technique_planner",
            "Plan a flat list of independently reusable methodology components. Narrow requests remain one "
            "component; broad requests split once, without recursion. Deduplicate equivalent components. "
            "Preserve ALL requested mechanisms and deliverables as explicit requirements. Remove incidental "
            "target details without adding requirements such as CVE validation unless requested. "
            "Do not require HTTP targets, inventory, authentication or live execution. "
            f"Return generic component requests suitable for global publication. Request: {request}",
            Plan,
        )
    with service.events.stage("plan_review"):
        review = service._json(
            "technique_plan_reviewer",
            "Independently check that the flat plan preserves every requested mechanism and deliverable, "
            "adds no unrelated requirements (including unsolicited CVE validation), and splits only independently "
            "reusable components. Abstraction may remove incidental target details. "
            f"Original request: {request}\n" + toon("components", [item.model_dump() for item in plan.components]),
            PlanReview,
        )
    if not review.approved:
        raise ValueError("Technique planning left unresolved request coverage: " + service._safe_feedback(review.reason))
    ids = [item.id for item in plan.components]
    if len(set(ids)) != len(ids):
        raise ValueError("Component identifiers must be unique")
    bundle = TechniqueBundle()
    seen = set()
    receipts = {}
    for component in plan.components:
        service._validate_request(component.request)
        for requirement in component.requirements:
            service._validate_request(requirement.description)
        requirement_ids = [row.id for row in component.requirements]
        if len(set(requirement_ids)) != len(requirement_ids):
            raise ValueError("Requirement identifiers must be unique within a component")
        key = (" ".join(component.request.lower().split()),
               tuple(sorted(row.description.lower() for row in component.requirements)))
        if key in seen:
            continue
        seen.add(key)
        decisions = []
        with service.events.scope(operation_id=operation, task_uid=task, component_id=component.id):
            try:
                revision, markdown = service.get(
                    component.request, context, on_decision=decisions.append, requirements=component.requirements,
                )
            except Exception as error:
                reason = service._safe_feedback(str(error))
                bundle.unresolved.append(Unresolved(component=component, reason=reason))
                service.events.emit("technique_build_progress", stage="component", status="unresolved", reason=reason)
                continue
        decision = decisions[0] if decisions else None
        bundle.delivered.append(Delivered(component=component, revision_id=revision, markdown=markdown,
                                          applicability=decision))
        if operation and task:
            receipt = decision.receipt(context) if decision else {}
            purpose = {"component_id": component.id, "component_purpose": component.purpose,
                       "requirements": [row.model_dump() for row in component.requirements]}
            if revision not in receipts:
                receipts[revision] = {**receipt, **purpose, "components": []}
            receipts[revision]["components"].append({**receipt, **purpose})
    for revision, receipt in receipts.items():
        service.repository.use(operation, task, revision, receipt)
    if not bundle.delivered:
        raise ValueError("No technique components resolved. " + "; ".join(
            f"{item.component.id}: {item.reason}" for item in bundle.unresolved
        ))
    return bundle
