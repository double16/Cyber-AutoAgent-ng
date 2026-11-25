"""Flat component planning and operation-local bundle delivery."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

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


class ComponentEdit(BaseModel):
    """One local correction against controller-assigned proposal IDs."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["remove", "merge", "replace"]
    component_ids: list[str] = Field(min_length=1)
    replacements: list[PlannedComponent] = Field(default_factory=list)


class PlanCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edits: list[ComponentEdit] = Field(min_length=1)


def apply_correction(output, correction):
    """Apply independent edits atomically; unchanged model values retain their provenance."""
    known = {f"component-{i}": item for i, item in enumerate(output.components, 1)}
    consumed = set()
    replacements = {}
    for edit in correction.edits:
        ids = edit.component_ids
        if len(set(ids)) != len(ids) or any(key not in known or key in consumed for key in ids):
            raise ValueError("Unknown, repeated or conflicting correction component")
        if edit.action == "remove":
            if edit.replacements:
                raise ValueError("Removal cannot supply replacements")
        elif edit.action == "merge":
            if len(ids) < 2 or len(edit.replacements) != 1:
                raise ValueError("Merge requires multiple components and one replacement")
        elif len(ids) != 1 or not edit.replacements:
            raise ValueError("Replace requires one component and nonempty replacements")
        consumed.update(ids)
        first = min(ids, key=lambda key: list(known).index(key))
        replacements[first] = edit.replacements
    components = []
    for key, item in known.items():
        components.extend(replacements.get(key, [] if key in consumed else [item]))
    return PlannerOutput(components=components)


def deduplicate_plan(output):
    """Group identical method requests without discarding distinct obligations or provenance."""
    groups = {}
    for item in output.components:
        groups.setdefault(item.request, []).append(item)
    components = []
    for items in groups.values():
        retained = None
        for item in items:
            incoming = {}
            for row in item.requirements:
                key = (row.description, row.kind, row.grounding)
                if key not in incoming:
                    incoming[key] = row.model_copy(deep=True)
                else:
                    incoming[key].input_references = list(dict.fromkeys(
                        incoming[key].input_references + row.input_references,
                    ))
            existing = ({(row.description, row.kind, row.grounding): row for row in retained.requirements}
                        if retained is not None else {})
            # Preserve the existing schema bound without silently dropping obligations.
            if retained is None or len(existing.keys() | incoming.keys()) > 16:
                retained = item.model_copy(deep=True, update={"requirements": list(incoming.values())})
                components.append(retained)
                continue
            retained.input_references = list(dict.fromkeys(retained.input_references + item.input_references))
            for key, source in incoming.items():
                if key in existing:
                    target = existing[key]
                    target.input_references = list(dict.fromkeys(target.input_references + source.input_references))
                else:
                    retained.requirements.append(source.model_copy(deep=True))
    return PlannerOutput.model_validate({"components": [item.model_dump() for item in components]})


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


class PlanIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: Literal["decomposition", "overlap", "omission", "invented_requirement", "grounding"]
    component_ids: list[str] = Field(min_length=1)
    input_references: list[str] = Field(min_length=1)
    correction: str = Field(min_length=1)


class BundlePlanReview(BaseModel):
    """Independent advice; recommendations never constitute a semantic veto."""

    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    issues: list[PlanIssue] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def legacy_approval(cls, value):
        """Accept historical scripted responses without advertising approval to models."""
        if isinstance(value, dict) and "approved" in value:
            value = dict(value)
            value.pop("approved")
        return value


DECOMPOSITION_POLICY = (
    "Default to one cohesive method; separate methods when their independently useful procedures differ. "
    "Prompt injection/jailbreak, tool-mediated SSRF, mass assignment and CVE validation can be "
    "independent methods even on the same endpoint. Preserve all requested mechanisms in either grouping. "
    "Extraction, prerequisites, criteria mapping and validation are steps within one CVE method. "
    "SQL injection testing and TLS configuration assessment are independently useful methods. "
    "Components represent independently reusable requested methods. Preconditions, procedural "
    "steps, controls, payloads and evidence expectations are requirements within a method. "
    "A list of mechanisms does not automatically determine component count. Choose cohesive or "
    "independent methods according to their purpose. Never include an umbrella component whose "
    "coverage duplicates its constituent components. Requirements within one method do not form "
    "independent components. "
)


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


def record_deliveries(service, bundle, context, operation, task):
    """Freeze the same per-revision receipt for direct reuse and planned components."""
    if not operation or not task:
        return
    receipts = {}
    for item in bundle.delivered:
        decision, component = item.applicability, item.component
        receipt = decision.receipt(context) if decision else {}
        purpose = {"component_id": component.id, "component_purpose": component.purpose,
                   "requirements": [row.model_dump() for row in component.requirements]}
        if item.revision_id not in receipts:
            receipts[item.revision_id] = {**receipt, **purpose, "components": [], "delivered_documents": []}
        receipts[item.revision_id]["components"].append({**receipt, **purpose})
        if item.markdown not in receipts[item.revision_id]["delivered_documents"]:
            receipts[item.revision_id]["delivered_documents"].append(item.markdown)
    for revision, receipt in receipts.items():
        service.repository.use(operation, task, revision, receipt)


def resolve_bundle(service, request, context=None, operation="", task="") -> TechniqueBundle:
    from modules.techniques.roles import RolePrompt

    request = request.strip()
    service._validate_request(request)
    alignment, context = align_request(service, request, context)
    with service.events.scope(component_id="component-1"):
        selected = service._select_existing(alignment.adjusted_request, context)
        if selected is not None:
            revision, markdown, decision = selected
            component = Component(
                id="component-1", request=alignment.adjusted_request,
                purpose=alignment.adjusted_request[:1000], request_excerpt=request[:2000],
                requirements=[Requirement(
                    id="request", kind="authored_deliverable", grounding=decision.grounding,
                    description="Provide the complete component request, including its mechanisms and explicit deliverables.",
                    origins=[RequirementOrigin(source="original_request", excerpt=request[:2000])],
                )],
            )
            markdown = service._resolved(
                revision, markdown, "reused", applicability=decision.applicability,
                unmet_prerequisite_count=len(decision.unmet_prerequisites),
            )[1]
            bundle = TechniqueBundle(alignment=alignment, delivered=[Delivered(
                component=component, revision_id=revision, markdown=markdown, applicability=decision,
            )])
            record_deliveries(service, bundle, context, operation, task)
            return bundle
    planning_rules = DECOMPOSITION_POLICY + (
        "Preserve requested mechanisms and deliverables. "
        "Carry shared requested deliverables into each applicable component. Keep assessable obligations "
        "within their method. Planner instructions, including citing original request excerpts, are "
        "provenance rules and must not become document requirements unless the user requested them. "
        "The original request defines methods and explicit deliverables. Central phase-objective records "
        "and explicit restriction records are authoritative methodology inputs, not planner instructions. "
        "Relevant requirements may cite those records to frame the requested method appropriately. "
        "Keep optional phase enrichment out of component requests and mandatory requirements. "
        "Authors receive example and checklist preferences separately through authoring guidance. "
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
        "Plan the methodology preserving original mechanisms and explicit deliverables. "
        "Reference IDs are controller bookkeeping, never research or authoring requirements. "
        "When correcting a supplied proposal, apply local edits using component-N IDs in its current order. "
        "Preserve unchanged components. Replace can split a component when requested coverage is missing. "
        "Python applies edits atomically, then validates the complete resulting plan.\n"
    )
    planning_inputs = (
        json_payload("authoritative_inputs", [
            {"id": identifier, **origin.model_dump()} for identifier, origin in records.items()
        ]) + f"\nPhase-aligned request (framing only): {alignment.adjusted_request}\n"
    )
    correction = ""
    review_history = []
    previous_output = None
    for attempt in range(3):
        with service.events.stage("planning"):
            try:
                # Legacy injected text runners retain their full-plan response contract.
                use_edits = previous_output is not None and callable(getattr(service.runner, "structured", None))
                response = service._json(
                    "technique_planner",
                    RolePrompt(planning_rules, planning_inputs + f"Original request: {request}\n" + correction),
                    PlanCorrection if use_edits else PlannerOutput,
                )
                output = apply_correction(previous_output, response) if use_edits else response
                output = deduplicate_plan(output)
                if previous_output is not None:
                    service.events.emit(
                        "technique_plan_corrected", before_count=len(previous_output.components),
                        after_count=len(output.components),
                        edit_kinds=[edit.action for edit in response.edits] if use_edits else ["legacy_replacement"],
                    )
            except ValueError as error:
                findings = ([
                    {"field": ".".join(map(str, row["loc"])), "problem": row["msg"]}
                    for row in error.errors(include_input=False, include_url=False)
                ] if isinstance(error, ValidationError) else [{"field": "planner_output", "problem": str(error)}])
                service.events.emit(
                    "technique_plan_rejected", correction_attempt=attempt, rejection_kind="contract",
                    reason=service._safe_feedback(str(error)),
                    validation_findings=[{**row, "problem": service._safe_feedback(row["problem"], limit=None)}
                                         for row in findings],
                )
                if attempt:
                    raise ValueError("Technique planning failed after one correction: " + str(error)) from None
                correction = service._repair_prompt("", error) + json_payload("validation_findings", findings)
                continue
        plan, findings = materialize_plan(output, records)
        if previous_output is not None:
            if findings:
                raise ValueError("Revised technique plan violates controller validation: " + str(findings))
            break
        with service.events.stage("plan_review"):
            review = service._json(
                "technique_plan_reviewer",
                RolePrompt(
                    "Independently check the plan adds no unrelated requirements and preserves each requested "
                    "methodology. Review planner_output using its input_references. Component IDs are "
                    "component-N, assigned by Python in proposal order starting at one. "
                    "Check each component request itself preserves its subject, mechanisms and explicit "
                    "deliverables, not only its purpose and requirements. Check requirement classifications: "
                    "facts need factual_support, procedures need authored_deliverable, restrictions need constraint. "
                    "Separately check grounding: source_required must correspond to explicit requested citations "
                    "or source-dependent facts such as affected versions, quotations or vendor statements. "
                    "General knowledge and original procedures use knowledge_allowed. "
                    "Requirements within one component may separately describe preconditions, steps, expectations "
                    "and evidence; this is not method splitting. Not automatically required does not mean "
                    "prohibited. Check coverage of independent methods, omitted obligations and requirements "
                    "derived from "
                    "planner instructions. Recommend corrections to invented prohibitions or omitted useful deliverables. "
                    "Judge each requirement's relevance, not its source category alone. A central phase-objective "
                    "record is authoritative methodology input, distinct from planner instructions. Relevant "
                    "phase framing must not promote optional payloads, checklists or presentation preferences "
                    "into component requests or mandatory requirements without an explicit request. "
                    "Identify unrelated mandatory artifacts and prohibitions. "
                    "Check every requirement origin semantically "
                    "justifies the obligation, not merely that the excerpt exists. Advice must name the "
                    "affected method or requirement and the requested "
                    "correction. Identify phase-only requests and splitting one workflow into supporting steps or "
                    "invented deliverables. "
                    + DECOMPOSITION_POLICY
                    + "Provide advisory improvements only; return an empty issues list when no changes help. "
                    "Review the proposal; do not generate a replacement plan. Identify structured issues "
                    "using component-N IDs and supplied input references. ",
                    json_payload("authoritative_inputs", [
                        {"id": identifier, **origin.model_dump()} for identifier, origin in records.items()
                    ])
                    + json_payload("review_history", review_history)
                    + json_payload("validation_findings", findings)
                    + f"Original request: {request}\n"
                    + json_payload("planner_output", output.model_dump()),
                ),
                BundlePlanReview,
            )
        for issue in review.issues:
            if any(ref not in records for ref in issue.input_references):
                raise ValueError("Review issue contains unknown input reference")
            if any(identifier not in {item.id for item in plan.components} for identifier in issue.component_ids):
                raise ValueError("Review issue contains unknown component")
        review_history.append({"proposal": output.model_dump(), "review": review.model_dump()})
        if review.issues:
            findings.append({"field": "advisory_review", "problem": review.reason})
        if not findings:
            break
        reason = "; ".join(row["field"] + ": " + row["problem"] for row in findings)
        service.events.emit("technique_plan_advice", correction_attempt=attempt,
                            review_mode="advisory",
                            reason=service._safe_feedback(reason),
                            validation_findings=[{**row, "problem": service._safe_feedback(row["problem"], limit=None)}
                                                 for row in findings],
                            issues=[issue.model_dump() for issue in review.issues],
                            components=[item.model_dump() for item in plan.components])
        previous_output = output
        correction = (
            "Draft plan (untrusted): "
            + output.model_dump_json()
            + "\nApply independent advice once, preserving requested methods and obligations: "
            + json_payload("validation_findings", findings)
            + json_payload("review_issues", [issue.model_dump() for issue in review.issues])
        )
    service.events.emit("technique_plan_accepted", components=[item.model_dump() for item in plan.components])
    bundle = TechniqueBundle(alignment=alignment)
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
    record_deliveries(service, bundle, context, operation, task)
    if not bundle.delivered:
        raise ValueError(
            "No technique components resolved. "
            + "; ".join(f"{item.component.id}: {item.reason}" for item in bundle.unresolved)
        )
    return bundle
