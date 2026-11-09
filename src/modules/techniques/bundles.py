"""Flat component planning and operation-local bundle delivery."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from modules.techniques.alignment import PHASE_OBJECTIVES, RequestAlignment, align_request
from modules.techniques.context import LookupDecision
from modules.techniques.coverage import Requirement, RequirementOrigin
from modules.techniques.documents import json_payload


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


class PlannedRequirement(BaseModel):
    """Semantic obligations only; persisted identities and origins belong to Python."""

    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=1000)
    kind: Literal["factual_support", "authored_deliverable", "constraint"]
    grounding: Literal["knowledge_allowed", "source_required"] = "knowledge_allowed"
    input_references: list[str] = Field(min_length=1, max_length=8)


class PlannedComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(
        min_length=1, max_length=2000,
        description="Standalone methodology request enumerating its subject, mechanisms and deliverables; "
                    "lookup receives this request independently of the requirement list",
    )
    input_references: list[str] = Field(min_length=1, max_length=8)
    requirements: list[PlannedRequirement] = Field(min_length=1, max_length=16)


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    components: list[PlannedComponent] = Field(min_length=1, max_length=16)


class InputRecord(BaseModel):
    """Complete authoritative input; only persisted provenance excerpts are bounded."""

    source: Literal["original_request", "phase_guidance", "methodology_restriction"]
    excerpt: str


def input_records(request, context):
    """Stable invocation-local references, independent of model-authored text."""
    return {
        f"{source}:{index}": InputRecord(source=source, excerpt=value)
        for source, values in planning_sources(request, context).items()
        for index, value in enumerate(values, 1)
    }


def materialize_plan(output, records):
    """Collect every deterministic defect before attempting semantic review or correction."""
    findings = []
    components = []
    seen = set()
    for index, item in enumerate(output.components, 1):
        identifier = f"component-{index}"
        key = " ".join(item.request.casefold().split())
        if key in seen:
            findings.append({"field": identifier, "problem": "Duplicate methodology component"})
        seen.add(key)
        for field, references in [(identifier, item.input_references), *[
            (f"{identifier}/requirement-{number}", row.input_references)
            for number, row in enumerate(item.requirements, 1)
        ]]:
            for reference in references:
                if reference not in records:
                    findings.append({"field": field, "problem": f"Unknown input reference: {reference}"})
        if not any(ref in records and records[ref].source == "original_request"
                   for ref in item.input_references):
            findings.append({"field": identifier, "problem": "Component must reference original_request"})
        components.append(Component(
            id=identifier, request=item.request, purpose=item.request[:1000],
            request_excerpt="\n".join(records[ref].excerpt for ref in dict.fromkeys(item.input_references)
                                      if ref in records and records[ref].source == "original_request"),
            requirements=[Requirement(
                id=f"requirement-{number}", description=row.description, kind=row.kind, grounding=row.grounding,
                origins=[RequirementOrigin(source=records[ref].source, excerpt=records[ref].excerpt[:2000])
                         for ref in dict.fromkeys(row.input_references) if ref in records],
            ) for number, row in enumerate(item.requirements, 1)],
        ))
    return Plan(components=components), findings


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


def planning_sources(request, context):
    """Authoritative inputs, excluding controller instructions."""
    return {
        "original_request": [request],
        "phase_guidance": [PHASE_OBJECTIVES[phase] for phase in (context.phase_contexts if context else [])],
        "methodology_restriction": (list(context.methodology_constraints)
                                  + [row["restriction"] for row in context.module_technique_policy]) if context else [],
    }


def requirement_origin_error(plan, request, context):
    """Check references in Python; the reviewer decides semantic justification."""
    sources = planning_sources(request, context)
    errors = []
    for component in plan.components:
        for requirement in component.requirements:
            if not requirement.origins:
                errors.append(
                    f"Requirement {component.id}/{requirement.id} requires an exact supplied-input origin",
                )
            for origin in requirement.origins:
                if not any(origin.excerpt in value for value in sources[origin.source]):
                    errors.append(f"Requirement {component.id}/{requirement.id}: invalid {origin.source} excerpt")
    return "; ".join(errors)


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
        "The original request defines methods and explicit deliverables. Central phase-objective records "
        "and explicit restriction records are authoritative methodology inputs, not planner instructions. "
        "Relevant requirements may cite those records to frame the requested method appropriately. "
        "Applicable procedural detail, examples and checklists are normal guidance even when not "
        "explicitly requested; they do not create independent methods or unrelated deliverables. Do not import "
        "operation terminal dispositions, completed execution or findings into reusable guidance. "
        "Classify each requirement kind as factual_support, authored_deliverable or constraint. "
        "Use grounding=knowledge_allowed normally. Use source_required for explicit citation requests, "
        "affected product versions, vendor statements or quotations, preserving request provenance. "
        "Every component and requirement must include input_references: nonempty lists of supplied "
        "record IDs. Components must reference original_request. References identify provenance, "
        "not semantic justification. Python assigns IDs, purposes and origin excerpts. "
        "Component requests must be complete methodologies preserving subject identifiers, mechanisms "
        "and deliverables, never phase labels. "
        "Remove incidental target details from reusable requests. Keep a flat, deduplicated plan. "
        "Requirements within one method do not form independent components. "
    )
    records = input_records(request, context)
    planning_rules += (
        json_payload("authoritative_inputs", [
            {"id": identifier, **origin.model_dump()} for identifier, origin in records.items()
        ]) + f"\nPhase-aligned request (framing only): {alignment.adjusted_request}\n"
        "Plan the methodology preserving original mechanisms and explicit deliverables. "
        "Reference IDs are controller bookkeeping, never research or authoring requirements.\n"
    )
    correction = ""
    for attempt in range(2):
        with service.events.stage("planning"):
            try:
                output = service._json(
                    "technique_planner",
                    planning_rules + f"Original request: {request}\n" + correction,
                    PlannerOutput,
                )
            except ValueError as error:
                findings = ([
                    {"field": ".".join(map(str, row["loc"])), "problem": row["msg"]}
                    for row in error.errors(include_input=False, include_url=False)
                ] if isinstance(error, ValidationError) else [{"field": "planner_output", "problem": str(error)}])
                service.events.emit(
                    "technique_plan_rejected", correction_attempt=attempt,
                    reason=service._safe_feedback(str(error)),
                    validation_findings=[{**row, "problem": service._safe_feedback(row["problem"], limit=None)}
                                         for row in findings],
                )
                if attempt:
                    raise ValueError("Technique planning failed after one correction: " + str(error)) from None
                correction = service._repair_prompt("", error) + json_payload("validation_findings", findings)
                continue
        plan, findings = materialize_plan(output, records)
        with service.events.stage("plan_review"):
            review = service._json(
                "technique_plan_reviewer",
                "Independently check the plan adds no unrelated requirements and preserves each requested "
                "methodology. Review planner_output using its input_references. The components section is "
                "controller-derived persisted metadata: Python resolves references into origins and assigns "
                "IDs and purposes; do not require input_references in that derived section. "
                "Check each component request itself preserves its subject, mechanisms and explicit "
                "deliverables, not only its purpose and requirements. Check requirement classifications: "
                "facts need factual_support, procedures need authored_deliverable, restrictions need constraint. "
                "Separately check grounding: source_required must correspond to explicit requested citations "
                "or source-dependent facts such as affected versions, quotations or vendor statements. "
                "General knowledge and original procedures use knowledge_allowed. "
                "Requirements within one component may separately describe preconditions, steps, expectations "
                "and evidence; this is not method splitting. Not automatically required does not mean "
                "prohibited. Reject under-splitting independent methods, omitted obligations and requirements "
                "derived from "
                "planner instructions. Reject invented prohibitions or omitted useful deliverables. "
                "Judge each requirement's relevance, not its source category alone. A central phase-objective "
                "record is authoritative methodology input, distinct from planner instructions. Relevant "
                "phase requirements and applicable procedural details, payloads or checklists do not require "
                "an explicit user request. Reject unrelated mandatory artifacts and prohibitions. "
                "Check every requirement origin semantically "
                "justifies the obligation, not merely that the excerpt exists. Rejection must name the "
                "affected method or requirement and the requested "
                "correction. Reject phase-only requests. Reject splitting one workflow into supporting steps or "
                "invented deliverables. "
                + planning_rules
                + f"Original request: {request}\n"
                + json_payload("planner_output", output.model_dump()) + "\n"
                + json_payload("components", [item.model_dump() for item in plan.components]),
                PlanReview,
            )
        if not review.approved:
            findings.append({"field": "semantic_review", "problem": review.reason})
        if not findings:
            break
        reason = "; ".join(row["field"] + ": " + row["problem"] for row in findings)
        service.events.emit("technique_plan_rejected", correction_attempt=attempt,
                            reason=service._safe_feedback(reason),
                            validation_findings=[{**row, "problem": service._safe_feedback(row["problem"], limit=None)}
                                                 for row in findings],
                            components=[item.model_dump() for item in plan.components])
        if attempt:
            raise ValueError("Technique planning left unresolved request coverage: " + service._safe_feedback(reason))
        correction = (
            "Rejected plan (untrusted): "
            + output.model_dump_json()
            + "\nRepair planning: "
            + json_payload("validation_findings", findings)
        )
    service.events.emit("technique_plan_accepted", components=[item.model_dump() for item in plan.components])
    bundle = TechniqueBundle(alignment=alignment)
    receipts = {}
    for component in plan.components:
        service._validate_request(component.request)
        for requirement in component.requirements:
            service._validate_request(requirement.description)
        component_context = context
        if context is not None and len(plan.components) > 1:
            component_context = context.model_copy(deep=True)
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
                receipts[revision] = {**receipt, **purpose, "components": [], "delivered_documents": []}
            receipts[revision]["components"].append({**receipt, **purpose})
            if markdown not in receipts[revision]["delivered_documents"]:
                receipts[revision]["delivered_documents"].append(markdown)
    for revision, receipt in receipts.items():
        service.repository.use(operation, task, revision, receipt)
    if not bundle.delivered:
        raise ValueError(
            "No technique components resolved. "
            + "; ".join(f"{item.component.id}: {item.reason}" for item in bundle.unresolved)
        )
    return bundle
