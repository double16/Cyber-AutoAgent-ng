"""Reusable retrieval, research, actor/critic publication, and independent feedback."""

import hashlib
import json
import re
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictInt, ValidationError, model_validator
from strands import tool

from modules.techniques.alignment import align_request, phase_guidance
from modules.techniques.context import LookupContext, LookupDecision
from modules.techniques.coverage import Requirement, review_coverage, select_sources
from modules.techniques.critique import Critique as Critique
from modules.techniques.critique import assess, repair_attribution
from modules.techniques.documents import Metadata, Source, TechniqueDocument, Trigger, toon
from modules.techniques.storage import TechniqueStore

APPLICABILITY_RULES = (
    "Assess whether advisory guidance helps a component of the task. Generic guidance need not "
    "produce target-specific hypotheses, evidence, findings or terminal disposition. Interpret "
    "constraints for the active phase: future validation guidance does not authorize present execution. "
    "Missing observations indicate unknown prerequisites, never a contradiction. "
    "Use conditional for unknown prerequisites and incompatible only for a referenced restriction "
    "or subject-source contradiction connected to a particular requested action. "
)

if TYPE_CHECKING:
    from modules.techniques.bundles import TechniqueBundle


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_id: str | None
    reason: str = Field(min_length=1)


class ProcedureMetadata(Metadata):
    """Accept legacy author source fields, but the controller always replaces them."""

    sources: list[Source] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def controller_fields(cls, value):
        if isinstance(value, dict):
            return {**value, "sources": [], "source_coverage": "Controller-owned coverage"}
        return value


class AuthoredTechnique(BaseModel):
    """LLM-authored content; Python owns document framing."""

    model_config = ConfigDict(extra="forbid")
    _raw_response: str | None = PrivateAttr(default=None)
    metadata: ProcedureMetadata
    body: str = Field(min_length=1)


class AuthorMetadata(BaseModel):
    """Only fields the author owns are advertised in the generation schema."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=2000)
    applicability: list[str] = Field(min_length=1)
    phases: list[str] = Field(min_length=1)
    triggers: list[Trigger] = Field(default_factory=list)


class AuthoredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata: AuthorMetadata
    body: str = Field(min_length=1)


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: StrictInt = Field(ge=-1, le=1)
    reason: str = Field(min_length=1, max_length=2000)


class TechniqueService:
    def __init__(self, store: TechniqueStore, runner: Callable, research: Callable) -> None:
        self.store = store
        self.repository = store.repository
        self.runner = runner
        self.research = research
        self.private_values: tuple[str, ...] = ()
        self.exports = None
        self._pending_produced_exports: set[str] = set()
        from modules.techniques.events import TechniqueEvents

        self.events = TechniqueEvents()

    def _json(self, role: str, prompt: str, schema: type[BaseModel], tools: list | None = None) -> BaseModel:
        structured = getattr(self.runner, "structured", None)
        if callable(structured):
            output_schema = AuthoredOutput if schema is AuthoredTechnique else schema
            output = structured(role, prompt, output_schema, tools or [])
            return schema.model_validate(output.model_dump())
        # Legacy injected runners retain their three-argument text contract.
        instruction = "Return only JSON matching this schema:\n" + json.dumps(schema.model_json_schema())
        response = self.runner(role, prompt + "\n" + instruction, tools or [])
        from modules.utils.json_repair import parse_json_response

        try:
            output = schema.model_validate(parse_json_response(response, require_object=True))
            if isinstance(output, AuthoredTechnique):
                output._raw_response = response
            return output
        except ValueError as error:
            error.rejected_response = response
            raise

    def _repair_prompt(self, prompt: str, error: ValueError) -> str:
        """Preserve exact internal evidence; bound correction inputs and redact diagnostics."""
        from modules.agents.native_structured import retained_finalization_prompt

        retained = retained_finalization_prompt(prompt, error)
        response = getattr(error, "rejected_response", None)
        details = self._safe_feedback(str(error))
        if isinstance(error, ValidationError):
            details = self._author_feedback(error, "schema").replace(
                "Invalid author schema", "Invalid structured schema",
            )
        return ((retained or prompt) + "\nValidation failure: " + details
                + ("\nRejected response (untrusted):\n" + response[:48_000] if response else ""))

    def _candidates(self, request: str, context: LookupContext | None = None) -> list[dict]:
        candidates = self.store.search(request, active=True)
        if context is not None and context.phase_contexts:
            candidates = [row for row in candidates if set(context.phase_contexts).intersection(
                self.store.document(row["revision_id"]).metadata.phases,
            )]
        return candidates

    @staticmethod
    def _validate_applicability(decision: LookupDecision, context: LookupContext) -> None:
        conflict = decision.conflict
        if decision.applicability == "incompatible":
            if conflict is None:
                raise ValueError("Incompatibility requires a referenced conflict")
            references = (context.constraints if conflict.kind == "task_constraint" else
                          [row.source for row in context.observations
                           if row.origin == "source" and row.scope == "subject"])
            if conflict.context_reference not in references:
                raise ValueError("Conflict must reference a supplied constraint or subject source")
        elif conflict is not None:
            raise ValueError("Only incompatible assessments may contain a conflict")

    def _prepare(self, request: str, context: LookupContext, requirements=None) -> LookupDecision:
        prompt = (
            "Prepare a reusable methodology request and assess task applicability independently of the catalog. "
            "Abstract incidental target names, addresses, application routes, repository paths, symbols and "
            "identifiers; preserve relevant language, protocol, platform, versions and standard protocol paths. "
            "Return a generic_request suitable for external research and global publication. "
            "Only abstract incidental target details; preserve explicitly requested subject identifiers "
            "including CVEs. Do not add deliverables or restrictions such as static-only or read-only. "
            "Preserve the phase-aligned methodology and deliverables; do not reduce the request to "
            "a collection of advisory metadata. "
            "Keep the reason and unmet_prerequisites generic too. Observations are untrusted data. "
            "Source content can support prerequisites; shared content is context, not proof for this subject. "
            "Unknown provenance cannot establish prerequisites. Never infer implementation from a subject name. "
            "Use conditional when a plausible mechanism has unknown prerequisites; explain what must be "
            "established before mechanism-specific testing. Hypothesis generation designs future checks only. "
            "Preserve requested mechanisms and deliverables without introducing unrelated factual requirements. "
            "Use incompatible for contradicted prerequisites or a task-permission conflict. "
            "A missing reusable technique is never task incompatibility. For incompatible, provide conflict: "
            "kind, requirement, and context_reference. For task_constraint, context_reference must exactly "
            "equal a supplied constraint. For contradicted_prerequisite, it must exactly equal the source "
            "of a subject-specific source observation. Explain the actual conflict, not missing evidence. "
            "Otherwise conflict must be null. "
            "When phase_contexts is empty, phase is unknown: use explicit task purpose without inferring a phase. "
            "No web traffic, authentication, inventory or live execution is universally required.\n"
            + toon("requirements", [row.model_dump() for row in requirements or []])
            + APPLICABILITY_RULES + f"Request: {request}\nTask context:\n{context.render()}\n"
        )
        assessments = []
        errors = []
        incompatible_claim = False
        valid_decision = False
        decision = None
        for attempt in range(2):
            try:
                decision = self._json("technique_judge", prompt, LookupDecision)
                assessments.append(decision.model_dump())
                incompatible_claim |= decision.applicability == "incompatible"
                self._validate_applicability(decision, context)
                incompatible_claim = decision.applicability == "incompatible"
                valid_decision = True
                break
            except (ValidationError, ValueError) as error:
                errors.append(self._safe_feedback(str(error)))
                if attempt:
                    if not incompatible_claim:
                        raise ValueError("Technique applicability assessment failed after one repair attempt") from None
                    break
                if decision is not None:
                    error.rejected_response = decision.model_dump_json()
                prompt = self._repair_prompt(prompt, error)
                prompt += (
                    "\nRepair the assessment: use the schema exactly and reference only supplied context. "
                    "Catalog misses and unknown prerequisites are not incompatibility. Missing observations "
                    "do not contradict a prerequisite; assess usefulness of guidance, not task completion."
                )
        if incompatible_claim:
            confirmation_prompt = (
                "Independently confirm or reject this incompatibility. Explicitly check confusion between "
                "advisory guidance and final task requirements, and between future checks and present execution. "
                "Missing observations are unknown prerequisites, never contradiction. Assess whether the "
                "methodology can help. Connect a particular requested action to the cited restriction or "
                "contradiction. Select incompatible only for the same substantiated conflict; otherwise "
                "explain uncertainty and return a usable reusable methodology request.\n"
                + toon("requirements", [row.model_dump() for row in requirements or []])
                + APPLICABILITY_RULES + f"Request: {request}\nContext: {context.render()}\n"
                + toon("assessments", assessments) + f"\nValidation diagnostics: {errors}"
            )
            confirmation = None
            valid_confirmation = False
            for _attempt in range(2):
                try:
                    confirmation = self._json(
                        "technique_applicability_confirmation", confirmation_prompt, LookupDecision,
                    )
                    self._validate_applicability(confirmation, context)
                    valid_confirmation = True
                    break
                except ValueError as error:
                    errors.append(self._safe_feedback(str(error)))
                    if confirmation is not None:
                        error.rejected_response = confirmation.model_dump_json()
                    confirmation_prompt = self._repair_prompt(confirmation_prompt, error)
            if confirmation is not None:
                assessments.append(confirmation.model_dump())
            if (decision is not None and valid_confirmation and valid_decision
                    and decision.applicability == confirmation.applicability == "incompatible"
                    and decision.conflict == confirmation.conflict):
                raise ValueError("Requested methodology is incompatible with the assigned task: " + self._safe_feedback(
                    f"{decision.conflict.requirement}: {decision.reason}",
                ))
            usable = confirmation or decision
            if usable is None:
                raise ValueError("Technique applicability assessment failed: no usable methodology request")
            self._validate_request(usable.generic_request)
            decision = usable.model_copy(update={
                "applicability": "uncertain", "conflict": None,
                "reason": "Independent applicability assessments did not establish a confirmed conflict. "
                          "Applicability remains uncertain; guidance does not authorize execution.",
            })
            decision._assessment_provenance = {"assessments": assessments, "validation_errors": errors}
        self._validate_request(decision.generic_request)
        if decision.applicability == "supported" and decision.unmet_prerequisites:
            decision = decision.model_copy(update={"applicability": "conditional"})
        if decision.applicability == "supported" and not any(
            row.origin == "source" and row.scope == "subject" for row in context.observations
        ):
            decision = decision.model_copy(update={
                "applicability": "conditional",
                "unmet_prerequisites": [*decision.unmet_prerequisites, "Establish subject-specific prerequisites"],
            })
        if decision.applicability == "conditional" and not decision.unmet_prerequisites:
            raise ValueError("Conditional technique guidance requires explicit unmet prerequisites")
        decision = self._preserve_request(request, decision, context, requirements)
        return decision

    def _preserve_request(self, request, decision, context, requirements):
        from modules.techniques.bundles import PlanReview

        if decision.generic_request == request:
            return decision
        prompt = (
            "Review request preservation, not task completion. Only incidental target details may be "
            "abstracted. Preserve the requested subject (including explicit CVEs), mechanisms and "
            "deliverables. Reject phase labels, omissions, invented obligations and restrictions. "
            + (context.render() if context else phase_guidance(None))
            + f"Original component request: {request}\n"
            + toon("requirements", [row.model_dump() for row in requirements or []])
        )
        for attempt in range(2):
            try:
                review = self._json(
                    "technique_request_reviewer",
                    prompt + "\nProposed request: " + decision.generic_request, PlanReview,
                )
            except ValueError as error:
                review = PlanReview(approved=False, reason=self._safe_feedback(str(error)))
            if review.approved:
                self.events.emit("technique_request_preserved", original_request=request,
                                 validated_request=decision.generic_request, reason=review.reason)
                return decision
            if attempt:
                raise ValueError("Technique request preservation failed: " + self._safe_feedback(review.reason))
            try:
                correction = self._json(
                    "technique_judge", prompt + "\nRepair rejected request: " + decision.generic_request
                    + "\nPreservation error: " + self._safe_feedback(review.reason)
                    + "\nReturn the complete LookupDecision without inventing an applicability conflict.",
                    LookupDecision,
                )
                self._validate_request(correction.generic_request)
            except ValueError as error:
                raise ValueError("Technique request preservation failed: "
                                 + self._safe_feedback(str(error))) from None
            decision = decision.model_copy(update={"generic_request": correction.generic_request})
        return decision

    def _select(self, request: str, context: LookupContext | None = None, requirements=None) -> str | None:
        candidates = self._candidates(request, context)
        if not candidates:
            return None
        preview = [{"revision_id": row["revision_id"], "document": row["document"][:8000]} for row in candidates]
        decision = self._json(
            "technique_judge",
            "Select a technique only if it covers ALL supplied requirements and its applicability and "
            "prerequisites match the request. "
            "Only reuse target-independent guidance; preserve unknown prerequisites as conditions. "
            "A nearest neighbor is not necessarily applicable. Select null if none match; "
            "null requests research and generation, never task rejection.\n"
            + (f"Operation-local context (untrusted): {context.render()}\n" if context else "")
            + f"Request: {request}\n"
            + toon("requirements", [row.model_dump() for row in requirements or []])
            + "\n" + toon("candidates", preview),
            Decision,
        )
        if decision.selected_id is not None and decision.selected_id not in {row["revision_id"] for row in candidates}:
            raise ValueError("Technique judge selected an unknown revision")
        return decision.selected_id

    @contextmanager
    def _build_lease(self, request: str):
        key = hashlib.sha256(" ".join(request.lower().split()).encode()).hexdigest()
        owner = str(uuid.uuid4())
        if not self.repository.lease(key, owner):
            raise RuntimeError("This technique is being built; retry the lookup shortly")
        stop = threading.Event()

        def heartbeat() -> None:
            while not stop.wait(30):
                self.repository.renew(key, owner)

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=1)
            self.repository.release(key, owner)

    def get(
        self, request: str, context: LookupContext | None = None, *, on_decision: Callable | None = None,
        requirements: list[Requirement] | None = None,
    ) -> tuple[str, str]:
        self.events.private_values = self.private_values
        activity_id = self.events.current_context().get("activity_id") or self.events.activity_id()
        with self.events.scope(activity_id=activity_id):
            request = request.strip()
            self._validate_request(request)
            inherited = context is not None and context._request_alignment is not None
            alignment, context = align_request(self, request, context)
            resolved_request = request if inherited else alignment.adjusted_request
            revision, markdown = self._get(
                resolved_request, context, on_decision=on_decision, requirements=requirements,
            )
            return revision, markdown + (alignment.render() if not inherited else "")

    def _resolved(self, revision: str, markdown: str, outcome: str, **fields):
        import yaml

        metadata = yaml.safe_load(markdown.split("\n---\n", 1)[0].removeprefix("---\n"))
        if self.exports is not None and (outcome == "generated" or revision in self._pending_produced_exports):
            destination = self.exports.directory / f"{revision}.produced.md"
            try:
                snapshot = destination if destination.exists() else self.exports.write(revision, markdown)
                fields["snapshot_path"] = snapshot.relative_to(self.exports.directory.parent).as_posix()
                self._pending_produced_exports.discard(revision)
            except Exception:
                self._pending_produced_exports.add(revision)
                self.events.emit(
                    "technique_warning", stage="export", diagnostic="Produced snapshot unavailable", retry_pending=True
                )
        self.events.emit(
            "technique_resolved",
            revision_id=revision,
            title=metadata["title"],
            origin=metadata["origin"],
            outcome=outcome,
            **fields,
        )
        return revision, markdown

    def _validate_request(self, request: str) -> None:
        if not request or len(request) > 2000:
            raise ValueError("Technique request must contain 1–2,000 characters")
        from modules.tools.web_search import contains_sensitive_web_search_data

        if contains_sensitive_web_search_data(request):
            raise ValueError("Technique requests must describe a generic method without secrets or personal data")
        if any(value.lower() in request.lower() for value in self.private_values):
            raise ValueError("Technique requests must not include operation-specific identifiers")

    def _get(
        self, request: str, context: LookupContext | None = None, *, on_decision: Callable | None = None,
        requirements: list[Requirement] | None = None,
    ) -> tuple[str, str]:
        request = request.strip()
        self._validate_request(request)
        decision = self._prepare(request, context, requirements) if context is not None else None
        if decision is not None:
            request = decision.generic_request
        requirements = requirements or [Requirement(id="method", description=request)]
        for requirement in requirements:
            self._validate_request(requirement.description)
        if len({row.id for row in requirements}) != len(requirements):
            raise ValueError("Requirement identifiers must be unique within a component")
        selected = self._select(request, context, requirements)
        if decision is not None and on_decision is not None:
            on_decision(decision)
        resolution_fields = ({"applicability": decision.applicability,
                              "unmet_prerequisite_count": len(decision.unmet_prerequisites)} if decision else {})
        if selected:
            return self._resolved(selected, self.store.render(selected), "reused", **resolution_fields)
        with self._build_lease(request):
            selected = self._select(request, context, requirements)
            if selected:
                return self._resolved(selected, self.store.render(selected), "reused", **resolution_fields)
            research_queries = []
            documents, selection, research_round = self._research_coverage(
                request, requirements, context=context, research_queries=research_queries,
            )
            selected_urls = set(selection.selected_urls)
            sources = [doc for doc in documents if doc["url"] in selected_urls]
            active_examples = self.store.search(request, active=True, limit=5)
            positive = [row for row in active_examples if self.repository.get(row["revision_id"])["score_total"] >= 0][
                :2
            ]
            negative = self.store.search(request, active=False, limit=2)
            negative_examples = (
                negative
                + [row for row in active_examples if self.repository.get(row["revision_id"])["score_total"] < 0][:2]
            )
            examples = [
                {"sentiment": label, "document": row["document"][:6000]}
                for label, rows in (("positive_or_neutral", positive), ("negative_or_inactive", negative_examples))
                for row in rows
            ]
            evidence_context = (
                f"Requested technique: {request}\nSource selection: {selection.summary()}\n"
                + toon("requirements", [row.model_dump() for row in requirements]) + "\n"
                + phase_guidance(context)
                + (f"Required phase contexts: {json.dumps(context.phase_contexts)}\n" if context else "")
                + toon("sources", [{key: value for key, value in source.items() if key != "text"}
                                    for source in sources])
                + "\n"
                + toon("existing_examples", examples)
            )
            prompt = (
                evidence_context
                + "\nWrite ONE self-contained technique with metadata and a Markdown body."
                + "\nUse prerequisites, original steps, controls, interpretation, limitations, optional tool choices, "
                "and phase-aware triggers. Use Python 3.13+ or browser JavaScript for original examples. "
                "Never copy source code verbatim. Never require a specific tool. Do not include operation or target "
                "identifiers, credentials, or evidence claims. Inactive examples are cautionary context; distinguish "
                "negative feedback from supersession. Explain source coverage limitations. "
                "Python owns sources, retrieval metadata, attribution and coverage; do not author these fields. "
                "The body is Markdown without YAML frontmatter. "
                "Return only JSON, without introductory prose or an outer code fence."
            )
            feedback = ""
            previous_draft = ""
            failure_stage = "authoring"
            attempt = 0
            attribution_repairs = 0
            source_selection_round = 1
            retain_draft = False
            while attempt < 3 or retain_draft:
                failure_stage = "author_json"
                try:
                    if not retain_draft:
                        attempt += 1
                        with self.events.stage("authoring", attempt):
                            authored = self._json(
                                "technique_author",
                                prompt + "\nPrevious response:\n" + previous_draft + "\nRepair feedback: " + feedback,
                                AuthoredTechnique,
                            )
                        response = authored._raw_response or authored.model_dump_json()
                    retain_draft = False
                    previous_draft = response[:48_000]
                    failure_stage = "document_validation"
                    authored.metadata.sources = [Source(
                        url=source["url"], title=source["title"], retrieved_at=source.get("retrieved_at", ""),
                        selection_reason=next(row.reason for row in selection.attributions if row.url == source["url"]),
                    ) for source in sources]
                    authored.metadata.source_coverage = (
                        f"Considered {len(documents)} fetched documents; selected {len(sources)}; target 3. "
                        + toon("requirements", [row.model_dump() for row in requirements]) + "\n"
                        + selection.summary()
                    )
                    markdown = (
                        "---\n"
                        + yaml.safe_dump(authored.metadata.model_dump(), sort_keys=False)
                        + "---\n\n"
                        + authored.body.strip()
                        + "\n"
                    )
                    document = TechniqueDocument(markdown)
                    if not document.metadata.active:
                        raise ValueError("Generated guidance must be active")
                    if context and context.phase_contexts and not set(context.phase_contexts).intersection(
                        document.metadata.phases,
                    ):
                        raise ValueError("Generated guidance must support a requested phase context")
                    # The critic reviews the exact controller-assembled provenance before publication.
                    if any(value.lower() in markdown.lower() for value in self.private_values):
                        raise ValueError("Generated technique contains operation-specific identifiers")
                except ValueError as error:
                    retain_draft = False
                    if failure_stage == "author_json":
                        response = getattr(error, "rejected_response", None) or ""
                        previous_draft = response[:48_000]
                        if isinstance(error, ValidationError):
                            failure_stage = "author_schema"
                    feedback = self._safe_feedback(self._author_feedback(error, failure_stage))
                    diagnostic = self._rejected_attempt(response, attempt, failure_stage, feedback)
                    self.events.emit(
                        "technique_build_progress",
                        stage=failure_stage,
                        status="revision_requested",
                        attempt=attempt,
                        reason=feedback,
                        **diagnostic,
                    )
                    continue
                failure_stage = "critique"
                try:
                    with self.events.stage("critique", attempt):
                        critique = assess(
                            self,
                            "Independently review grounding, completeness, phase triggers, original code, "
                            "tool independence, target independence, and requested version prerequisites. "
                            "Do not add CVE validation unless requested. Categorize defects by repair ownership: "
                            "authored_content for procedure defects, source_attribution for inaccurate or unsupported "
                            "URL attribution, research_coverage for unsupported requirements, request_coverage for "
                            "omitted requested mechanisms or deliverables. Similar attribution wording alone is "
                            "not a defect. Assess the exact assembled document below. "
                            "A technique provides guidance, never proof. Approve only if all requirements hold.\n"
                            "Unknown prerequisites must remain explicit conditions, not inferred facts. "
                            "Judge phase suitability for source review, network services, system mapping, challenges "
                            "and emulation as well as web assessments. Public protocol metadata alone is not "
                            "information disclosure without an unintended protected information boundary.\n"
                            + evidence_context
                            + "\nRequired document metadata schema: "
                            + json.dumps(Metadata.model_json_schema())
                            + "\nDraft:\n"
                            + markdown,
                            markdown, requirements, documents, selection.selected_urls,
                        )
                except ValueError as error:
                    diagnostic = self._rejected_attempt(
                        response, attempt, "critique_assessment", self._safe_feedback(str(error)),
                    )
                    self.events.emit(
                        "technique_build_progress", stage="critique_assessment", status="failed",
                        attempt=attempt, author_attempts=attempt, research_batches=research_round,
                        attribution_repairs=attribution_repairs, source_selection_round=source_selection_round,
                        reason=self._safe_feedback(str(error)), **diagnostic,
                    )
                    raise
                if critique.approved:
                    # Recheck the final bytes immediately before publication.
                    document = TechniqueDocument(markdown)
                    if any(value.lower() in markdown.lower() for value in self.private_values):
                        raise ValueError("Generated technique contains operation-specific identifiers")
                    # Repair an inactive logical technique; do not reset its old revision's feedback.
                    lineage = (
                        self.repository.get(negative[0]["revision_id"])["lineage"]
                        if negative and self._negative_matches(request, negative[0])
                        else f"generated:{uuid.uuid4()}"
                    )
                    from modules.techniques.documents import revision_id

                    prior = self.repository.get(revision_id(lineage, document.sha256))
                    if prior is not None and not prior["active"]:
                        feedback = (
                            "Revise the guidance to address negative feedback; this draft repeats an inactive revision."
                        )
                        diagnostic = self._rejected_attempt(response, attempt, "critique", feedback)
                        self.events.emit(
                            "technique_build_progress",
                            **diagnostic,
                            stage="critique",
                            status="revision_requested",
                            attempt=attempt,
                            reason=feedback,
                        )
                        continue
                    revision = self.store.publish(document, lineage, "generated")
                    rendered = self.store.render(revision)
                    return self._resolved(revision, rendered, "generated", **resolution_fields)
                owner = next(category for category in (
                    "research_coverage", "source_attribution", "authored_content", "request_coverage"
                ) if any(issue.category == category for issue in critique.issues))
                issues = [issue for issue in critique.issues if issue.category == owner]
                feedback = toon("issues", [issue.model_dump() for issue in issues])
                diagnostic = (
                    self._rejected_attempt(response, attempt, "critique", self._safe_feedback(feedback))
                    if owner in {"authored_content", "request_coverage"} else {}
                )
                self.events.emit(
                    "technique_build_progress", stage="critique", status="revision_requested",
                    **diagnostic, reason=self._safe_feedback(feedback),
                    attempt=attempt, owning_stage=owner, issue_references=[issue.id for issue in issues],
                    affected_urls=sorted({url for issue in issues for url in issue.source_urls}),
                    source_selection_round=source_selection_round, author_attempts=attempt,
                    research_batches=research_round, attribution_repairs=attribution_repairs,
                )
                if owner in {"source_attribution", "research_coverage"}:
                    if owner == "source_attribution":
                        if attribution_repairs >= 1:
                            raise ValueError("Source attribution unresolved after one repair: " + feedback)
                        attribution_repairs += 1
                        with self.events.stage("attribution_repair", attribution_repairs):
                            selection = repair_attribution(
                                self, selection, documents, issues, requirements,
                            )
                    else:
                        if research_round >= 3:
                            raise ValueError("Research coverage exhausted: " + feedback)
                        previous_selection = selection.model_dump_json(exclude={"attributions"})
                        documents, selection, research_round = self._research_coverage(
                            request, requirements, documents, selection, research_round, feedback, context=context,
                            research_queries=research_queries,
                        )
                        if selection.model_dump_json(exclude={"attributions"}) != previous_selection:
                            source_selection_round += 1
                            attribution_repairs = 0
                    selected_urls = set(selection.selected_urls)
                    sources = [doc for doc in documents if doc["url"] in selected_urls]
                    old_evidence = evidence_context
                    evidence_context = (
                        f"Requested technique: {request}\nSource selection: {selection.summary()}\n"
                        + toon("requirements", [row.model_dump() for row in requirements]) + "\n"
                        + phase_guidance(context)
                        + (f"Required phase contexts: {json.dumps(context.phase_contexts)}\n" if context else "")
                        + toon("existing_examples", examples) + "\n"
                        + toon("sources", [{key: value for key, value in source.items() if key != "text"}
                                           for source in sources])
                    )
                    prompt = prompt.replace(old_evidence, evidence_context, 1)
                    retain_draft = True
                    continue
                feedback = self._safe_feedback(feedback, limit=None)
            message = f"Technique generation failed after 3 attempts at {failure_stage}: {feedback}"
            self.events.emit(
                "technique_build_progress",
                stage=failure_stage,
                status="failed",
                attempt=3,
                reason=feedback,
                **diagnostic,
            )
            raise ValueError(message) from None

    def resolve_bundle(
        self, request: str, context: LookupContext | None = None, *, operation: str = "", task: str = "",
    ) -> "TechniqueBundle":
        from modules.techniques.bundles import resolve_bundle

        self.events.private_values = self.private_values
        with self.events.scope(operation_id=operation, task_uid=task, activity_id=self.events.activity_id()):
            return resolve_bundle(self, request, context, operation, task)

    def _research_coverage(self, request, requirements, documents=None, selection=None, research_round=0, gap="",
                           *, context=None, research_queries=None):
        documents = list(documents or [])
        prior_queries = research_queries if research_queries is not None else []
        reviewed_assessments = {}
        while research_round < 3:
            research_round += 1
            with self.events.scope(research_round=research_round):
                with self.events.stage("research"):
                    research_request = request + "\n" + phase_guidance(context)
                    if len(requirements) > 1 or requirements[0].description != request:
                        research_request += "\n" + toon("requirements", [row.model_dump() for row in requirements])
                    query = research_request if research_round == 1 else (
                        research_request + "\nTarget unresolved coverage: " + gap
                        + (selection.summary() if selection else "")
                        + "\nSearch specifically for the listed missing factual support or contradictions. "
                          "Do not repeat broad advisory searches. Prior source limitations and target "
                          "validation needs are not factual research gaps."
                        + "\nPrior searches (avoid repeating): " + json.dumps(prior_queries)
                        + "\nDo not refetch: " + ", ".join(doc["url"] for doc in documents)
                    )
                    if hasattr(self.research, "batch"):
                        fetched = self.research.batch(query, {doc["url"] for doc in documents})
                    else:
                        fetched = self.research(query)
                queries = list(getattr(fetched, "queries", ()))
                normalized = {" ".join(query.casefold().split()) for query in prior_queries}
                repeats = [query for query in queries if " ".join(query.casefold().split()) in normalized]
                if repeats:
                    self.events.emit("technique_warning", stage="research",
                                     diagnostic=f"Research batch {research_round} repeated prior search queries",
                                     repeated_queries=repeats)
                prior_queries.extend(queries)
                known = {doc["url"] for doc in documents}
                fresh = []
                for document in fetched:
                    if document["url"] not in known:
                        fresh.append(document)
                        known.add(document["url"])
                documents.extend(fresh)
                if not fresh:
                    self.events.emit("technique_build_progress", stage="research", status="no_new_documents",
                                     fetched_count=len(documents), retained_selection=selection is not None)
                    continue
                with self.events.stage("source_selection") as fields:
                    selection = select_sources(self, request, requirements, fresh, selection, gap, context=context)
                    fields.update(fetched_count=len(documents), selected_count=len(selection.selected_urls))
                if not selection.sufficient:
                    assessment_key = selection.model_dump_json()
                    if assessment_key not in reviewed_assessments:
                        reviewed_assessments[assessment_key] = review_coverage(
                            self, request, requirements, selection, context,
                        )
                    selection = reviewed_assessments[assessment_key]
                if selection.sufficient:
                    return documents, selection, research_round
        if selection is None:
            raise ValueError("Research did not fetch any usable source documents after three batches")
        if selection.sufficient and not gap:
            return documents, selection, research_round
        raise ValueError("Research coverage exhausted after three batches: " + selection.summary()
                         + ("\nRequested repair: " + gap if gap else ""))

    def _safe_feedback(self, text: str, *, limit: int | None = 2000) -> str:
        from modules.utils.redaction import redact_text

        text = redact_text(text)
        for value in self.private_values:
            text = re.sub(re.escape(value), "[operation-specific]", text, flags=re.IGNORECASE)
        return text[:limit] if limit is not None else text

    @staticmethod
    def _author_feedback(error: ValueError, stage: str) -> str:
        from modules.config.models.structured_output import StructuredOutputValidationError

        if isinstance(error, StructuredOutputValidationError):
            return str(error)
        if stage == "author_json":
            return (f"Invalid JSON ({error}): return one JSON object with metadata and body, "
                    "without prose or code fences.")
        if isinstance(error, ValidationError):
            details = "; ".join(
                f"{'.'.join(map(str, item['loc']))}: {item['type']}"
                for item in error.errors(include_input=False, include_url=False)
            )
            return f"Invalid author schema: {details}"[:2000]
        return str(error)

    def _rejected_attempt(self, response: str, attempt: int, stage: str, reason: str) -> dict:
        if self.exports is None:
            return {}
        try:
            snapshot = self.exports.write_rejected(
                self.events.current_activity_id(), response, attempt=attempt, stage=stage, reason=reason
            )
            return {"snapshot_path": snapshot.relative_to(self.exports.directory.parent).as_posix()}
        except Exception:
            self.events.emit(
                "technique_warning", stage="export", diagnostic="Rejected technique diagnostic unavailable"
            )
            return {}

    def _negative_matches(self, request: str, candidate: dict) -> bool:
        decision = self._json(
            "technique_judge",
            f"Does this inactive technique address the requested method? Request: {request}\n"
            + toon("candidates", [candidate])
            + "\nSelect its revision ID if yes, otherwise null.",
            Decision,
        )
        if decision.selected_id not in {None, candidate["revision_id"]}:
            raise ValueError("Unknown inactive technique selected")
        return decision.selected_id is not None

    def synchronize_module(self, loader: Any, module: str) -> None:
        counts = {"added": 0, "updated": 0, "unchanged": 0}
        for name in loader._inheritance_chain(module):
            root = loader._find_module_dir(name)
            if root is None:
                continue
            technique_root = root / "techniques"
            for path in sorted(technique_root.rglob("*.md")):
                if not path.resolve().is_relative_to(technique_root.resolve()):
                    raise ValueError("Technique document escapes its module directory")
                document = TechniqueDocument(path.read_bytes().decode("utf-8"))
                lineage = f"module:{name}:{path.relative_to(root).as_posix()}"
                from modules.techniques.documents import revision_id

                prior = self.repository.get(revision_id(lineage, document.sha256))
                category = (
                    "unchanged"
                    if prior
                    else (
                        "updated" if any(row["lineage"] == lineage for row in self.repository.revisions()) else "added"
                    )
                )
                self.store.publish(document, lineage, f"module:{name}")
                counts[category] += 1
        self.events.emit("technique_sync_complete", module=module, **counts)

    def tool(self, operation: str = "", task: str = "", context: LookupContext | None = None):
        lookup_lock = threading.Lock()

        @tool
        def get_technique(request: str) -> str:
            """Retrieve or build reusable methodology, hypothesis, or CVE-verification guidance.

            Describe the generic technique in natural language. Do not submit target URLs, tokens,
            credentials, or personal data. Guidance never constitutes current-operation evidence.
            Conditional applicability notes list prerequisites to establish within the assigned scope.
            """
            # Concurrent executors may invoke this task-bound tool from separate worker threads.
            with lookup_lock:
                return self.resolve_bundle(request, context, operation=operation, task=task).render()

        get_technique._cyber_technique_observation_origin = "guidance"
        return get_technique

    def evaluate(self, operation: str, task: str, context: str, tools: list | None = None) -> None:
        self.events.private_values = self.private_values
        with self.events.scope(operation_id=operation, task_uid=task):
            self._evaluate(operation, task, context, tools)

    def _evaluate(self, operation: str, task: str, context: str, tools: list | None = None) -> None:
        for use in self.repository.pending_uses(operation, task):
            revision = use["revision_id"]
            feedback = self._json(
                "technique_evaluator",
                "Independently rate the technique's usefulness for this task: +1 helpful, "
                "0 neutral/unused/insufficient evidence, -1 unhelpful. Correctly ruling out a vulnerability can be "
                "helpful. Task failure or unavailable tools alone do not establish negative sentiment. "
                "Use the recorded original and phase-aligned request and guidance objectives when available. "
                "Evaluate demonstrated contribution to the requested purpose, not completeness against every "
                "task requirement. Unrelated missing research or execution during a design/analysis task is "
                "not grounds for neutrality. Source analysis can exclude a reachable sink; system mapping can "
                "resolve a dependency; emulation guidance can improve telemetry and cleanup. "
                "Distinguish applicability mismatch from document quality. Reserve -1 for demonstrably "
                "misleading or harmful guidance. Legacy use context is unknown, not inferred from prose. "
                "Identify observed contribution, non-use or misleading effect in the reason. Genericity, "
                "conditional prerequisites and incomplete execution do not independently justify zero. "
                "Preserve neutral when contribution is unestablished. "
                "Give a generic reason without target identifiers or secret values.\nTechnique:\n"
                + self.store.render(revision, require_active=False)
                + "\nTask results:\n"
                + context[:24_000]
                + "\nRecorded delivery context:\n" + use["use_context"],
                Feedback,
                tools,
            )
            from modules.utils.redaction import redact_text

            reason = redact_text(feedback.reason)
            for value in self.private_values:
                reason = re.sub(re.escape(value), "[operation-specific]", reason, flags=re.IGNORECASE)
            transition = self.repository.feedback(operation, task, revision, feedback.score, reason)
            if transition:
                with self.events.scope(operation_id=operation, task_uid=task):
                    fields = {
                        "revision_id": revision,
                        "score": feedback.score,
                        "reason": reason,
                        "sentiment_count": transition["score_count"],
                        "sentiment_mean": transition["score_total"] / transition["score_count"],
                    }
                    try:
                        if self.exports is not None:
                            snapshot = self._export_evaluation(
                                operation,
                                task,
                                {"revision_id": revision, "score": feedback.score, "reason": reason},
                            )
                            fields["snapshot_path"] = snapshot.relative_to(self.exports.directory.parent).as_posix()
                    finally:
                        self.events.emit("technique_evaluated", **fields)
                        if transition["deactivated"]:
                            self.events.emit("technique_deactivated", **fields)
        self.store.reconcile()
        if self.exports is not None:
            for use in self.repository.scored_uses(operation, task):
                self._export_evaluation(operation, task, use)

    def _export_evaluation(self, operation, task, use):
        row = self.repository.get(use["revision_id"])
        rendered = self.store.document(use["revision_id"]).render(
            {
                **self.store.lifecycle(row),
                "use_evaluation": {
                    "operation_id": operation,
                    "task_uid": task,
                    "score": use["score"],
                    "reason": use["reason"],
                },
            }
        )
        return self.exports.write(use["revision_id"], rendered, task=task)
