"""Validate review coverage and provenance without deciding semantic merit."""

import json
from typing import Any

from modules.agents.structured_outputs import (
    HypothesisCheckpointDecisionOutput,
    HypothesisPrerequisiteGap,
    HypothesisSubjectReview,
    HypothesisTaskDecisionOutput,
)
from modules.tools.hypotheses import hypothesis_binding, load_hypothesis_set


def semantic_review(data: dict[str, Any]) -> dict[str, Any]:
    """Freeze semantic judgments independently of malformed workflow bookkeeping."""

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
        HypothesisSubjectReview.model_validate(row).model_dump() for row in rows
    ], "prerequisite_gaps": [HypothesisPrerequisiteGap.model_validate(gap).model_dump() for gap in gaps],
        "unresolved_criterion_ids": criteria}


def review_requires_changes(data: dict[str, Any]) -> bool:
    return any(
        row["decision"] != "retain" or any(item["decision"] != "retain" for item in row["hypotheses"])
        for row in data.get("subject_reviews", [])
    )


def normalize_review(data: dict[str, Any]) -> dict[str, Any]:
    """Derive repair routing from explicit judgments, never infer semantic acceptance."""

    review = HypothesisTaskDecisionOutput.model_validate(data)
    if review.status not in {"done", "partial_failure", "blocked"}:
        raise ValueError("Unknown hypothesis review status")
    result = review.model_dump()
    changes = review_requires_changes(result)
    gaps = review.prerequisite_gaps
    acceptance_gaps = review.unresolved_criterion_ids
    if changes or ((gaps or acceptance_gaps) and review.status != "blocked"):
        result["status"] = "partial_failure"
        result["repair"] = {"kind": "acceptance" if changes or acceptance_gaps else "execution",
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


def repair_context(data: dict[str, Any], digest: str) -> str:
    """Deliver the full current review to the actor without relying on optional prose."""

    if data.get("publication_sha256") != digest:
        return ""
    feedback = {key: data[key] for key in (
        "publication_sha256", "subject_reviews", "prerequisite_gaps", "unresolved_criterion_ids",
    ) if key in data}
    if data.get("unresolved_criterion_ids"):
        feedback["criterion_gap_reason"] = data.get("reason", "")
    return "\n## Structured hypothesis review feedback\n" + json.dumps(feedback, ensure_ascii=False)


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
    changes = False
    for row in review.subject_reviews:
        if (len(row.hypotheses) != len(expected[row.item_id])
                or {item.hypothesis_id for item in row.hypotheses} != expected[row.item_id]):
            raise ValueError("Review every current hypothesis exactly once for its subject")
        changes |= row.decision != "retain" or any(item.decision != "retain" for item in row.hypotheses)
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
    if changes and (review.repair is None or review.repair.kind != "acceptance"):
        raise ValueError("Revised or withdrawn hypotheses require explicit publication repair")
