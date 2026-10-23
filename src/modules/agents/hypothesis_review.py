"""Validate review coverage and provenance without deciding semantic merit."""

import json
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union

from pydantic import Field, create_model

from modules.agents.hypothesis_context import hypothesis_inspection_journal, review_input_candidates
from modules.agents.structured_outputs import (
    HypothesisBehaviorAssessment,
    HypothesisCheckpointDecisionOutput,
    HypothesisPrerequisiteGap,
    HypothesisRecordReview,
    HypothesisSemanticChecks,
    HypothesisSubjectReview,
    HypothesisTaskDecisionOutput,
    NoHypothesisBasis,
    TaskReviewDecisionOutput,
)
from modules.tools import memory
from modules.tools.artifact_journal import inspection_has_content
from modules.tools.hypotheses import HypothesisSet, hypothesis_binding, load_hypothesis_set
from modules.tools.inventory_inputs import inspection_covers_input


def semantic_review(data: dict[str, Any]) -> dict[str, Any]:
    """Extract structured judgments independently of workflow routing fields."""

    digest = data.get("publication_sha256")
    if not isinstance(digest, str) or not digest:
        raise ValueError("Hypothesis review requires a publication digest")
    rows = data.get("subject_reviews")
    if not isinstance(rows, list):
        raise ValueError("Hypothesis review requires subject reviews")
    gaps = data.get("prerequisite_gaps", [])
    criteria = data.get("unresolved_criterion_ids", [])
    if not isinstance(gaps, list) or not isinstance(criteria, list):
        raise ValueError("Hypothesis review gaps must be lists")
    if any(not isinstance(item, str) or not item.strip() for item in criteria):
        raise ValueError("Unresolved criteria must be non-empty IDs")
    return {"publication_sha256": digest, "subject_reviews": [
        HypothesisSubjectReview.model_validate(row).model_dump(exclude_none=True) for row in rows
    ], "prerequisite_gaps": [HypothesisPrerequisiteGap.model_validate(gap).model_dump() for gap in gaps],
        "unresolved_criterion_ids": criteria}


def review_requires_changes(data: dict[str, Any]) -> bool:
    return any(
        row["decision"] != "retain" or any(item["decision"] != "retain" for item in row["hypotheses"])
        or (row.get("no_hypothesis_basis") or {}).get("kind") == "metadata_only"
        or any(item.get("supports_conditional_test") for item in
               ((row.get("no_hypothesis_basis") or {}).get("behavior_assessments") or []))
        for row in data.get("subject_reviews", [])
    )


def normalize_review(data: dict[str, Any]) -> dict[str, Any]:
    """Derive repair routing from explicit judgments, never infer semantic acceptance."""

    review = HypothesisTaskDecisionOutput.model_validate(data)
    if review.status not in {"done", "partial_failure", "blocked"}:
        raise ValueError("Unknown hypothesis review status")
    result = review.model_dump()
    result["subject_reviews"] = semantic_review(data)["subject_reviews"]
    changes = review_requires_changes(result)
    gaps = review.prerequisite_gaps
    if any(row.no_hypothesis_basis and row.no_hypothesis_basis.kind == "necessary_evidence_unreviewed"
           for row in review.subject_reviews) and not gaps:
        raise ValueError("Unreviewed necessary evidence requires explicit prerequisite_gaps")
    acceptance_gaps = review.unresolved_criterion_ids
    if review.status == "blocked" and gaps:
        result["repair"] = {"kind": "none", "evidence_gaps": [], "execution_receipt_gaps": []}
    elif changes or gaps or acceptance_gaps:
        result["status"] = "partial_failure"
        result["repair"] = {"kind": "execution" if gaps else "acceptance",
                            "evidence_gaps": [gap.missing_input for gap in gaps][:3],
                            "execution_receipt_gaps": []}
        if not review.reason.strip():
            result["reason"] = (
                "Publication revisions or frozen acceptance corrections required"
                if changes or acceptance_gaps else "Required frozen input unavailable"
            )
    elif review.status in {"done", "blocked"}:
        result["repair"] = {"kind": "none", "evidence_gaps": [], "execution_receipt_gaps": []}
    if result["status"] != "done" and not result["reason"].strip():
        raise ValueError("Non-success hypothesis reviews require a concrete reason")
    if result["status"] == "partial_failure" and not changes and not gaps and not acceptance_gaps:
        raise ValueError("Incomplete hypothesis review must identify changes, unresolved criteria, or prerequisite gaps")
    if result["status"] == "blocked" and not gaps:
        raise ValueError("Blocked hypothesis review requires an explicit unavailable prerequisite")
    return result


def repair_context(data: dict[str, Any], digest: str, publication: HypothesisSet | None = None) -> str:
    """Deliver the full current review to the actor without relying on optional prose."""

    if data.get("publication_sha256") != digest:
        return ""
    feedback = {key: data[key] for key in (
        "publication_sha256", "subject_reviews", "prerequisite_gaps", "unresolved_criterion_ids",
    ) if key in data}
    if data.get("unresolved_criterion_ids"):
        feedback["criterion_gap_reason"] = data.get("reason", "")
    text = "\n## Structured hypothesis review feedback\n" + json.dumps(feedback, ensure_ascii=False)
    if publication is None:
        return text
    subjects = {subject.item_id: subject for subject in publication.subjects}
    affected = []
    for row in data.get("subject_reviews", []):
        changes = [item for item in row.get("hypotheses", []) if item.get("decision") in {"revise", "withdraw"}]
        if not review_requires_changes({"subject_reviews": [row]}):
            continue
        subject = subjects.get(row["item_id"])
        if subject is None:
            raise ValueError("Hypothesis repair review names an unknown publication subject")
        records = {item.hypothesis_id: item for item in subject.hypotheses}
        if any(item["hypothesis_id"] not in records for item in changes):
            raise ValueError("Hypothesis repair review names an unknown publication hypothesis")
        affected.append({
            "current_subject": subject.model_dump(mode="json"),
            "hypotheses_requiring_changes": [
                records[item["hypothesis_id"]].model_dump(mode="json") for item in changes
            ],
        })
    if not affected:
        if feedback.get("prerequisite_gaps"):
            return text + (
                "\nResolve the supplied prerequisite gaps before acceptance. Reuse the current publication "
                "unless the review requests revisions."
            )
        return text + "\nNo hypothesis revisions requested. Reuse the current publication for acceptance."
    return (
        text + "\n## Exact hypothesis repair records\n" + json.dumps({
            "publication_sha256": digest, "affected_subjects": affected,
        }, ensure_ascii=False)
        + "\nThese complete current records match the review decisions and reasons above by hypothesis_id. "
          "They are supplied directly; do not reread the publication to reconstruct "
          "them. Submit mode=replace with each affected subject's full corrected hypothesis list, retaining "
          "siblings marked retain. Omit unaffected subjects; Python preserves them. Do not execute future tests. "
          "Then record_task_acceptance."
    )


def validate_checkpoint(data: dict[str, Any], task: Any) -> None:
    """Permit follow-up inspection only for explicitly identified frozen inputs."""

    review = HypothesisCheckpointDecisionOutput.model_validate(data)
    if review.status not in {"partial_failure", "blocked"}:
        raise ValueError("A missing publication cannot be accepted as completed")
    kind = review.repair.kind if review.repair else "none"
    if review.status == "blocked" and kind != "none":
        raise ValueError("A blocked checkpoint cannot request repair")
    if review.status == "partial_failure" and kind not in {"acceptance", "execution"}:
        raise ValueError("An incomplete checkpoint requires publication or prerequisite repair")
    if kind == "acceptance" and review.prerequisite_gaps:
        raise ValueError("Publication repair cannot request prerequisite inspection")
    for gap in review.prerequisite_gaps:
        if gap.criterion_id not in {criterion.id for criterion in task.acceptance.criteria}:
            raise ValueError("Checkpoint prerequisite must name a frozen criterion")
        if not set(gap.item_ids) <= set(task.acceptance.basis.item_ids):
            raise ValueError("Checkpoint prerequisite must name assigned subjects")
        if not set(gap.source_refs) <= set(task.acceptance.basis.source_refs):
            raise ValueError("Checkpoint prerequisite must name a frozen input source")
    if review.repair and review.repair.kind == "execution" and not review.prerequisite_gaps:
        raise ValueError("Further inspection requires a concrete structured prerequisite gap")


def validate_subject_review(
    row: HypothesisSubjectReview, expected: set[str], task: Any, operation_id: str,
    candidates: list[dict],
) -> None:
    """Validate one subject before preserving it across correction attempts."""
    if (len(row.hypotheses) != len(expected)
            or {item.hypothesis_id for item in row.hypotheses} != expected):
        raise ValueError("Review every current hypothesis exactly once for its subject")
    for item in row.hypotheses:
        if item.semantic_checks and item.decision == "retain" and any(
            check.verdict == "revise" for check in (
                item.semantic_checks.grounding, item.semantic_checks.test_discrimination,
                item.semantic_checks.identity_requirements,
            )
        ):
            raise ValueError("A retained hypothesis cannot have a revise semantic check")
    if expected and row.no_hypothesis_basis is not None:
        raise ValueError("Populated subjects must omit no_hypothesis_basis")
    if not expected:
        basis = row.no_hypothesis_basis
        if basis is None:
            raise ValueError("Empty hypothesis review requires no_hypothesis_basis")
        if basis.kind == "inspected_evidence":
            if not basis.inspection_refs:
                raise ValueError("Inspected-evidence basis requires retained inspection references")
            journal = hypothesis_inspection_journal(task, operation_id, memory._operation_output_root())
            if not all(inspection_has_content(journal.load_inspection(reference)) for reference in basis.inspection_refs):
                raise ValueError("Empty disposition requires inspected content, not metadata-only results")
            if not basis.behavior_assessments and not basis.no_relevant_behavior_reason:
                raise ValueError("Empty review requires behavior assessments or a no-relevant-behavior explanation")
            if basis.behavior_assessments and basis.no_relevant_behavior_reason:
                raise ValueError("Behavior assessments conflict with a no-relevant-behavior explanation")
            for assessment in basis.behavior_assessments or []:
                if not set(assessment.inspection_refs) <= set(basis.inspection_refs):
                    raise ValueError("Behavior assessment must cite its inspected-evidence basis")
            known = {candidate["candidate_id"]: candidate for candidate in candidates}
            assessed = set()
            for assessment in basis.behavior_assessments or []:
                if not set(assessment.input_candidate_ids) <= known.keys():
                    raise ValueError("Behavior assessment names unknown input_candidate_ids")
                for candidate_id in assessment.input_candidate_ids:
                    candidate = known[candidate_id]
                    if not any(inspection_covers_input(candidate, journal.load_inspection(ref))
                               for ref in assessment.inspection_refs):
                        raise ValueError(f"Source input {candidate_id} requires corresponding retained inspection")
                    assessed.add(candidate_id)
            if assessed != known.keys():
                raise ValueError(f"Empty review must assess input_candidate_ids: {sorted(known.keys() - assessed)}")


def validate_review(data: dict[str, Any], task: Any, operation_id: str) -> None:
    review = HypothesisTaskDecisionOutput.model_validate(data)
    binding = hypothesis_binding(task)
    publication = load_hypothesis_set(binding["artifact_ref"], task, operation_id)
    if review.publication_sha256 != binding["sha256"]:
        raise ValueError("Hypothesis review must bind the latest publication digest")
    expected = {subject.item_id: {item.hypothesis_id for item in subject.hypotheses}
                for subject in publication.subjects}
    if (len(review.subject_reviews) != len(expected)
            or {row.item_id for row in review.subject_reviews} != expected.keys()):
        raise ValueError("Review every current subject exactly once")
    candidates = review_input_candidates(task) if any(not value for value in expected.values()) else {}
    changes = False
    for row in review.subject_reviews:
        validate_subject_review(row, expected[row.item_id], task, operation_id, candidates.get(row.item_id, []))
        validate_subject_prerequisites(row, data, task)
        changes |= row.decision != "retain" or any(item.decision != "retain" for item in row.hypotheses)
        if not expected[row.item_id]:
            basis = row.no_hypothesis_basis
            if basis.kind == "inspected_evidence":
                if any(item.supports_conditional_test for item in basis.behavior_assessments or []):
                    waiting = review.status == "blocked" and bool(review.prerequisite_gaps)
                    required_kind = "execution" if review.prerequisite_gaps else "acceptance"
                    if review.status == "done" or (not waiting and (
                        review.repair is None or review.repair.kind != required_kind
                    )):
                        raise ValueError("Supported conditional test requires publication repair")
            elif basis.kind == "metadata_only":
                if review.status == "done" or review.repair is None or review.repair.kind not in {"acceptance", "execution"}:
                    raise ValueError("Metadata-only empty disposition requires publication repair")
            elif not review.prerequisite_gaps or review.status == "done":
                raise ValueError("Unreviewed necessary evidence requires prerequisite recovery")
    criteria = {criterion.id for criterion in task.acceptance.criteria}
    if not set(review.unresolved_criterion_ids) <= criteria:
        raise ValueError("Unresolved acceptance must identify frozen criterion IDs")
    for gap in review.prerequisite_gaps:
        if gap.criterion_id not in criteria or not set(gap.item_ids) <= expected.keys():
            raise ValueError("Prerequisite gaps must identify frozen criteria and assigned subjects")
        if not set(gap.source_refs) <= set(publication.source_refs):
            raise ValueError("Prerequisite inspection must name a frozen input source")
    if review.status == "done" and (changes or review.prerequisite_gaps or review.unresolved_criterion_ids):
        raise ValueError("A completed hypothesis review cannot require changes or prerequisites")
    permitted_repairs = {"acceptance", "execution"} if review.prerequisite_gaps else {"acceptance"}
    if changes and review.status != "blocked" and (review.repair is None or review.repair.kind not in permitted_repairs):
        raise ValueError("Revised or withdrawn hypotheses require explicit publication repair")


def validate_subject_prerequisites(row: HypothesisSubjectReview, payload: dict, task: Any) -> list[dict]:
    """Validate gaps belonging to a subject before preserving its unreviewed-evidence judgment."""
    gaps = payload.get("prerequisite_gaps", [])
    if not isinstance(gaps, list):
        raise ValueError("prerequisite_gaps must be a list")
    related = []
    for value in gaps:
        gap = HypothesisPrerequisiteGap.model_validate(value)
        if row.item_id not in gap.item_ids:
            continue
        if (gap.criterion_id not in {criterion.id for criterion in task.acceptance.criteria}
                or not set(gap.item_ids) <= set(task.acceptance.basis.item_ids)
                or not set(gap.source_refs) <= set(task.acceptance.basis.source_refs)):
            raise ValueError("Subject prerequisite must identify frozen criteria, subjects, and sources")
        related.append(gap.model_dump())
    if (row.no_hypothesis_basis and row.no_hypothesis_basis.kind == "necessary_evidence_unreviewed"
            and not related):
        raise ValueError("Unreviewed necessary evidence requires a prerequisite gap for this subject")
    return related


@dataclass(frozen=True)
class BoundHypothesisReview:
    """Immutable publication identity and model-facing structured review contract."""

    digest: str
    output_model: Any
    task_uid: str
    example: str

    @classmethod
    def capture(cls, task: Any, operation_id: str) -> "BoundHypothesisReview":
        binding = hypothesis_binding(task)
        publication = load_hypothesis_set(binding["artifact_ref"], task, operation_id)
        candidates = review_input_candidates(task) if any(not item.hypotheses for item in publication.subjects) else {}
        rows = []
        for index, subject in enumerate(publication.subjects):
            identifiers = tuple(item.hypothesis_id for item in subject.hypotheses)
            record = create_model(
                f"BoundHypothesisRecord{index}", __base__=HypothesisRecordReview,
                hypothesis_id=(Literal[identifiers] if identifiers else str, ...),
                semantic_checks=(HypothesisSemanticChecks, ...),
            )
            candidate_ids = tuple(item["candidate_id"] for item in candidates.get(subject.item_id, []))
            assessment = create_model(
                f"BoundHypothesisBehavior{index}", __base__=HypothesisBehaviorAssessment,
                input_candidate_ids=(list[Literal[candidate_ids]] if candidate_ids else list[str],
                                     Field(default_factory=list, **({} if candidate_ids else {"max_length": 0}))),
            )
            basis = create_model(
                f"BoundNoHypothesisBasis{index}", __base__=NoHypothesisBasis,
                behavior_assessments=(list[assessment] | None, None),
            )
            rows.append(create_model(
                f"BoundHypothesisSubject{index}", __base__=HypothesisSubjectReview,
                item_id=(Literal[subject.item_id], ...),
                hypotheses=(list[record], Field(min_length=len(identifiers), max_length=len(identifiers))),
                no_hypothesis_basis=(type(None), None) if identifiers else (basis, ...),
            ))
        if not rows:
            raise ValueError("Cannot review a publication without subjects")
        row_type = rows[0] if len(rows) == 1 else Annotated[
            Union[tuple(rows)], Field(discriminator="item_id")  # noqa: UP007 - runtime union of generated models
        ]
        criteria = tuple(item.id for item in task.acceptance.criteria)
        criterion_type = Literal[criteria]
        gap = create_model(
            "BoundHypothesisPrerequisiteGap", __base__=HypothesisPrerequisiteGap,
            criterion_id=(criterion_type, ...),
            item_ids=(list[Literal[tuple(subject.item_id for subject in publication.subjects)]], Field(min_length=1)),
        )
        model = create_model(
            "BoundHypothesisReviewOutput", __base__=TaskReviewDecisionOutput,
            status=(Literal["done", "partial_failure", "blocked"], ...),
            subject_reviews=(list[row_type], Field(min_length=len(rows), max_length=len(rows))),
            prerequisite_gaps=(list[gap], Field(default_factory=list)),
            unresolved_criterion_ids=(list[criterion_type], Field(default_factory=list)),
        )
        has_empty = any(not subject.hypotheses for subject in publication.subjects)
        example = {"status": "partial_failure" if has_empty else "done",
                   "reason": "Illustrative review only; replace reasons with your evidence-based judgments",
                   "repair": {"kind": "acceptance" if has_empty else "none"},
                   "subject_reviews": [{
                       "item_id": subject.item_id, "decision": "revise" if not subject.hypotheses else "retain",
                       "reason": "Explain your judgment", "hypotheses": [{
                           "hypothesis_id": item.hypothesis_id, "decision": "retain", "reason": "Explain test adequacy",
                           "semantic_checks": {
                               key: {"verdict": "adequate", "reason": "Explain this check against supplied evidence"}
                               for key in ("grounding", "test_discrimination", "identity_requirements")
                           },
                       } for item in subject.hypotheses],
                       **({"no_hypothesis_basis": {"kind": "metadata_only", "inspection_refs": []}}
                          if not subject.hypotheses else {}),
                   } for subject in publication.subjects],
                   "prerequisite_gaps": [], "unresolved_criterion_ids": []}
        return cls(binding["sha256"], model, task.task_uid, json.dumps(example))

    def bind(self, payload: dict[str, Any], task: Any) -> dict[str, Any]:
        """Attach controller provenance before freezing any parseable semantic judgments."""
        if task.task_uid != self.task_uid or hypothesis_binding(task).get("sha256") != self.digest:
            raise ValueError("Publication changed during hypothesis evaluation")
        wire = dict(payload)
        if wire.pop("publication_sha256", self.digest) != self.digest:
            raise ValueError("Hypothesis review must bind the latest publication digest")
        return {**wire, "publication_sha256": self.digest}

    def canonicalize(self, payload: dict[str, Any], task: Any) -> dict[str, Any]:
        """Validate model judgments and produce the existing canonical decision envelope."""
        wire = self.bind(payload, task)
        wire.pop("publication_sha256")
        result = self.output_model.model_validate(wire).model_dump(exclude_none=True)
        return {**result, "publication_sha256": self.digest}


@dataclass
class HypothesisReviewCorrection:
    """Keep validated subjects; permit invalid subjects to be reassessed on the same publication."""

    binding: BoundHypothesisReview
    operation_id: str
    accepted: dict[str, dict] = field(default_factory=dict)
    accepted_prerequisites: dict[str, list[dict]] = field(default_factory=dict)

    def prepare(self, payload: dict[str, Any], task: Any) -> dict[str, Any]:
        wire = self.binding.bind(payload, task)
        publication = load_hypothesis_set(hypothesis_binding(task)["artifact_ref"], task, self.operation_id)
        expected = {subject.item_id: {item.hypothesis_id for item in subject.hypotheses}
                    for subject in publication.subjects}
        candidates = review_input_candidates(task) if any(not value for value in expected.values()) else {}
        errors, seen, rows = [], set(), []
        raw_rows = wire.get("subject_reviews")
        if not isinstance(raw_rows, list):
            raise ValueError("subject_reviews must be a complete list")
        counts = Counter(row.get("item_id") for row in raw_rows
                         if isinstance(row, dict) and isinstance(row.get("item_id"), str))
        duplicates = {item_id for item_id, count in counts.items() if count > 1}
        for index, raw in enumerate(raw_rows):
            item_id = raw.get("item_id") if isinstance(raw, dict) else None
            try:
                row = HypothesisSubjectReview.model_validate(raw)
                if row.item_id not in expected or row.item_id in duplicates:
                    raise ValueError("Review each assigned subject exactly once; unknown or duplicate subject")
                seen.add(row.item_id)
                if any(item.semantic_checks is None for item in row.hypotheses):
                    raise ValueError("Each current hypothesis review requires semantic_checks")
                validate_subject_review(row, expected[row.item_id], task, self.operation_id,
                                        candidates.get(row.item_id, []))
                prerequisites = validate_subject_prerequisites(row, wire, task)
                canonical = row.model_dump(exclude_none=True)
                if row.item_id in self.accepted and (
                    canonical != self.accepted[row.item_id]
                    or prerequisites != self.accepted_prerequisites[row.item_id]
                ):
                    raise ValueError("Preserve this already validated subject review exactly")
                self.accepted[row.item_id] = deepcopy(canonical)
                self.accepted_prerequisites[row.item_id] = deepcopy(prerequisites)
                rows.append(canonical)
            except (ValueError, OSError) as error:
                errors.append({"item_id": item_id, "path": f"subject_reviews[{index}]", "error": str(error)})
        for item_id in sorted(expected.keys() - seen - duplicates):
            errors.append({"item_id": item_id, "path": "subject_reviews", "error": "Assigned subject omitted"})
        if errors:
            raise ValueError("Review correction required: " + json.dumps({
                "publication_sha256": self.binding.digest, "errors": errors,
                "accepted_subject_reviews": list(self.accepted.values()),
                "accepted_subject_prerequisites": self.accepted_prerequisites,
                "instruction": "Return all subjects. Preserve accepted reviews and prerequisite gaps; reassess invalid reviews.",
            }, ensure_ascii=False))
        wire["subject_reviews"] = rows
        return normalize_review(self.binding.canonicalize(wire, task))
