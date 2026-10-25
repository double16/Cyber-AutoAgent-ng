# ruff: noqa: F811
"""Review every current record once without treating evidence availability as semantic support."""

import copy
import json
from dataclasses import replace

import pytest

from modules.agents.hypothesis_review import validate_review
from tests.test_hypotheses import hypothesis, producer, publish  # noqa: F401


def semantic_checks():
    return {key: {"verdict": "adequate", "reason": "Grounded controls and identity context"}
            for key in ("grounding", "test_discrimination", "identity_requirements")}


@pytest.fixture
def empty_review(producer, tmp_path):
    binding, _ = publish(producer, tmp_path, [{"item_id": "subject", "disposition": "no_testable_hypotheses",
                                             "reason": "Inspected functionality offers no supported security test"}])
    return producer, {"status": "done", "publication_sha256": binding["sha256"], "subject_reviews": [{
        "item_id": "subject", "decision": "retain", "reason": "No supported implication in inspected content",
        "hypotheses": [],
    }]}


def test_empty_review_requires_retained_inspected_content(empty_review, tmp_path):
    from modules.agents.hypothesis_context import hypothesis_inspection_journal

    task, data = empty_review
    with pytest.raises(ValueError, match="no_hypothesis_basis"):
        validate_review(data, task, "OP_TEST")
    basis = {"kind": "inspected_evidence", "inspection_refs": []}
    data["subject_reviews"][0]["no_hypothesis_basis"] = basis
    with pytest.raises(ValueError, match="references"):
        validate_review(data, task, "OP_TEST")
    journal = hypothesis_inspection_journal(task, "OP_TEST", str(tmp_path))
    ref = journal.record_inspection("get_inventory_item", "metadata", {}, "{\"item\":{\"inputs\":[]}}", ())
    basis["inspection_refs"] = [ref]
    with pytest.raises(ValueError, match="metadata-only"):
        validate_review(data, task, "OP_TEST")
    ref = journal.record_inspection("read_artifact", "read", {}, "{\"content\":\"static page markup\"}", ())
    basis["inspection_refs"] = [ref]
    with pytest.raises(ValueError, match="behavior assessments"):
        validate_review(data, task, "OP_TEST")
    basis["no_relevant_behavior_reason"] = "Only static informational markup; no observed interaction or boundary"
    validate_review(data, task, "OP_TEST")
    basis["inspection_refs"] = ["artifact:forged.json"]
    with pytest.raises(ValueError, match="not available"):
        validate_review(data, task, "OP_TEST")


def test_metadata_only_reason_routes_to_repair_without_rewriting_semantics(empty_review):
    from modules.agents.hypothesis_review import normalize_review, semantic_review

    task, data = empty_review
    data["subject_reviews"][0]["no_hypothesis_basis"] = {"kind": "metadata_only", "inspection_refs": []}
    with pytest.raises(ValueError, match="publication repair"):
        validate_review(data, task, "OP_TEST")
    normalized = normalize_review(data)
    assert normalized["status"] == "partial_failure" and normalized["repair"]["kind"] == "acceptance"
    assert semantic_review(normalized) == semantic_review(data)
    validate_review(normalized, task, "OP_TEST")


@pytest.mark.parametrize("content", ["HTTP response body", "SSH banner", "Filesystem configuration"])
def test_empty_review_accepts_supplemental_metadata_but_assessments_need_content(empty_review, tmp_path, content):
    from modules.agents.hypothesis_context import hypothesis_inspection_journal
    from modules.agents.hypothesis_review import HypothesisEvidenceError

    task, data = empty_review
    journal = hypothesis_inspection_journal(task, "OP_TEST", str(tmp_path))
    metadata = journal.record_inspection("get_inventory_item", "metadata", {}, '{"item":{"inputs":[]}}', ())
    source = journal.record_inspection("read_artifact", "content", {}, json.dumps({"content": content}), ())
    basis = {"kind": "inspected_evidence", "inspection_refs": [metadata, source], "behavior_assessments": [{
        "observed_behavior": content, "inspection_refs": [metadata, source], "supports_conditional_test": False,
        "reasoning": "Inspected example has no supported security consequence",
    }]}
    data["subject_reviews"][0]["no_hypothesis_basis"] = basis
    validate_review(data, task, "OP_TEST")
    basis["behavior_assessments"][0]["inspection_refs"] = [metadata]
    with pytest.raises(HypothesisEvidenceError, match="content-bearing") as caught:
        validate_review(data, task, "OP_TEST")
    assert caught.value.details["available_content_refs"] == [source]
    assert caught.value.details["deficient_references"] == [metadata]
    assert caught.value.details["field_path"].endswith("behavior_assessments[0].inspection_refs")


def test_empty_review_correction_identifies_evidence_and_can_request_recovery(empty_review, tmp_path):
    from modules.agents.hypothesis_context import hypothesis_inspection_journal
    from modules.agents.hypothesis_review import (
        BoundHypothesisReview,
        HypothesisReviewContractError,
        HypothesisReviewCorrection,
    )

    task, data = empty_review
    journal = hypothesis_inspection_journal(task, "OP_TEST", str(tmp_path))
    metadata = journal.record_inspection("get_inventory_item", "metadata", {}, '{"item":{}}', ())
    row = data["subject_reviews"][0]
    row["no_hypothesis_basis"] = {"kind": "inspected_evidence", "inspection_refs": [metadata]}
    correction = HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST")
    with pytest.raises(HypothesisReviewContractError) as caught:
        correction.prepare(data, task)
    feedback = json.loads(str(caught.value).split("Review correction required: ")[1])["errors"][0]
    assert feedback["item_id"] == "subject"
    assert feedback["path"] == "subject_reviews[0].no_hypothesis_basis.inspection_refs"
    assert feedback["reference_availability"] == {metadata: "metadata_only"}
    assert feedback["available_content_refs"] == []
    assert "prerequisite_gaps" in feedback["instruction"]
    row["no_hypothesis_basis"] = {"kind": "necessary_evidence_unreviewed", "inspection_refs": [metadata]}
    data["prerequisite_gaps"] = [{"criterion_id": "hypotheses", "missing_input": "Inspect supplied source content",
                                 "item_ids": ["subject"], "source_refs": ["artifact:inventory.json"]}]
    data["reason"] = "Necessary source content has not been inspected"
    result = correction.prepare(data, task)
    assert result["status"] == "partial_failure" and result["repair"]["kind"] == "execution"
    validate_review(result, task, "OP_TEST")


def test_empty_review_corrects_citations_from_retained_content(empty_review, tmp_path):
    from modules.agents.hypothesis_context import hypothesis_inspection_journal
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection

    task, data = empty_review
    journal = hypothesis_inspection_journal(task, "OP_TEST", str(tmp_path))
    metadata = journal.record_inspection("get_inventory_item", "metadata", {}, '{"item":{}}', ())
    source = journal.record_inspection("search_artifact", "content", {}, '{"matches":[{"content":"static"}]}', ())
    basis = {"kind": "inspected_evidence", "inspection_refs": [metadata],
             "no_relevant_behavior_reason": "Only inert documentation in inspected content"}
    data["subject_reviews"][0]["no_hypothesis_basis"] = basis
    correction = HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST")
    with pytest.raises(ValueError) as caught:
        correction.prepare(data, task)
    assert source in str(caught.value)
    basis["inspection_refs"].append(source)
    assert correction.prepare(data, task)["status"] == "done"
    assert len(journal._entries()) == 2


def test_necessary_unexamined_evidence_requires_frozen_prerequisite_gap(empty_review):
    from modules.agents.hypothesis_review import normalize_review

    task, data = empty_review
    data["subject_reviews"][0]["no_hypothesis_basis"] = {"kind": "necessary_evidence_unreviewed", "inspection_refs": []}
    with pytest.raises(ValueError, match="prerequisite"):
        normalize_review(data)
    data["prerequisite_gaps"] = [{"criterion_id": "hypotheses", "missing_input": "Inspect the supplied function",
                                 "item_ids": ["subject"], "source_refs": ["artifact:inventory.json"]}]
    with pytest.raises(ValueError, match="prerequisite"):
        validate_review(data, task, "OP_TEST")
    normalized = normalize_review(data)
    assert normalized["repair"]["kind"] == "execution"
    validate_review(normalized, task, "OP_TEST")


@pytest.fixture
def reviewed(producer, tmp_path):
    binding, _ = publish(producer, tmp_path)
    return producer, {
        "status": "done", "publication_sha256": binding["sha256"],
        "subject_reviews": [{"item_id": "subject", "decision": "retain", "reason": "Conditional safe test",
                             "hypotheses": [{"hypothesis_id": binding["hypothesis_ids_by_subject"]["subject"][0],
                                             "decision": "retain", "reason": "Controls distinguish impact",
                                             "semantic_checks": semantic_checks()}]}],
    }


def test_complete_review_and_noop_prose(reviewed):
    task, data = reviewed
    data.update(instructions="No further task work required", repair={"kind": "none"})
    validate_review(data, task, "OP_TEST")


@pytest.mark.parametrize("fault", ["digest", "subject", "duplicate_subject", "hypothesis", "duplicate_hypothesis",
                                  "done_changes", "missing_repair", "criterion", "gap_subject", "source", "done_gap"])
def test_rejects_invalid_review_coverage_or_provenance(reviewed, fault):
    task, data = reviewed
    row = data["subject_reviews"][0]
    gap = {"criterion_id": "hypotheses", "missing_input": "Required input", "item_ids": ["subject"],
           "source_refs": ["artifact:inventory.json"]}
    if fault == "digest":
        data["publication_sha256"] = "old"
    elif fault == "subject":
        row["item_id"] = "foreign"
    elif fault == "duplicate_subject":
        data["subject_reviews"].append(copy.deepcopy(row))
    elif fault == "hypothesis":
        row["hypotheses"] = []
    elif fault == "duplicate_hypothesis":
        row["hypotheses"].append(copy.deepcopy(row["hypotheses"][0]))
    elif fault in {"done_changes", "missing_repair"}:
        row["hypotheses"][0]["decision"] = "withdraw"
        if fault == "missing_repair":
            data["status"] = "partial_failure"
    else:
        data["status"] = "partial_failure"
        data["prerequisite_gaps"] = [gap]
        if fault == "criterion":
            gap["criterion_id"] = "invented"
        elif fault == "gap_subject":
            gap["item_ids"] = ["foreign"]
        elif fault == "source":
            gap["source_refs"] = ["artifact:other.json"]
        else:
            data["status"] = "done"
    with pytest.raises(ValueError):
        validate_review(data, task, "OP_TEST")


def test_explicit_repair_and_required_input_gap(reviewed):
    task, data = reviewed
    data["status"] = "partial_failure"
    data["subject_reviews"][0]["decision"] = "revise"
    data["repair"] = {"kind": "acceptance"}
    data["prerequisite_gaps"] = [{"criterion_id": "hypotheses", "missing_input": "Required input",
                                   "item_ids": ["subject"], "source_refs": ["artifact:inventory.json"]}]
    validate_review(data, task, "OP_TEST")


@pytest.mark.parametrize("fault", [None, "status", "criterion", "subjects", "source", "inspection"])
def test_checkpoint_only_permits_explicit_required_inputs(producer, fault):
    from modules.agents.hypothesis_review import validate_checkpoint

    data = {"status": "partial_failure", "repair": {"kind": "execution"}, "prerequisite_gaps": [{
        "criterion_id": "hypotheses", "missing_input": "Explicitly required source",
        "item_ids": ["subject"], "source_refs": ["artifact:inventory.json"],
    }]}
    if fault == "status":
        data["status"] = "done"
    elif fault == "criterion":
        data["prerequisite_gaps"][0]["criterion_id"] = "new"
    elif fault == "subjects":
        data["prerequisite_gaps"][0]["item_ids"] = ["foreign"]
    elif fault == "source":
        data["prerequisite_gaps"][0]["source_refs"] = ["artifact:foreign"]
    elif fault == "inspection":
        data["prerequisite_gaps"] = []
    if fault:
        with pytest.raises(ValueError):
            validate_checkpoint(data, producer)
    else:
        validate_checkpoint(data, producer)
        data.update(status="blocked", repair={"kind": "none"})
        validate_checkpoint(data, producer)
        data.update(repair=None, prerequisite_gaps=[])
        validate_checkpoint(data, producer)


@pytest.mark.parametrize("model_name", ["HypothesisTaskDecisionOutput", "HypothesisCheckpointDecisionOutput"])
def test_hypothesis_schemas_exclude_unrelated_decisions(model_name):
    from modules.agents import structured_outputs

    model = getattr(structured_outputs, model_name)
    properties = model.model_json_schema()["properties"]
    assert "comparison_assessment" not in properties
    assert "finding_recommendation" not in properties
    data = {"status": "partial_failure", "prerequisite_gaps": [],
            "publication_sha256": "digest", "subject_reviews": []}
    if model_name == "HypothesisCheckpointDecisionOutput":
        data.pop("publication_sha256")
        data.pop("subject_reviews")
    model.model_validate(data)
    for field in ["comparison_assessment", "finding_recommendation"]:
        with pytest.raises(ValueError):
            model.model_validate({**data, field: {}})


@pytest.mark.parametrize("status,kind,gaps,valid", [
    ("partial_failure", "acceptance", False, True),
    ("partial_failure", "acceptance", True, False),
    ("partial_failure", "execution", True, True),
    ("partial_failure", "execution", False, False),
    ("partial_failure", "none", False, False),
    ("blocked", "none", True, True),
    ("blocked", "execution", True, False),
])
def test_checkpoint_repair_state_controls_inspection(producer, status, kind, gaps, valid):
    from modules.agents.hypothesis_review import validate_checkpoint

    data = {"status": status, "repair": {"kind": kind}, "prerequisite_gaps": [{
        "criterion_id": "hypotheses", "missing_input": "Required source observations",
        "item_ids": ["subject"], "source_refs": ["artifact:inventory.json"],
    }] if gaps else []}
    if valid:
        validate_checkpoint(data, producer)
    else:
        with pytest.raises(ValueError):
            validate_checkpoint(data, producer)


@pytest.mark.parametrize("status", ["done", "partial_failure", "blocked"])
@pytest.mark.parametrize("decision", ["revise", "withdraw"])
def test_normalization_routes_explicit_changes_without_rewriting_judgments(reviewed, status, decision):
    from modules.agents.hypothesis_review import normalize_review, semantic_review

    _task, data = reviewed
    data["status"] = status
    data["subject_reviews"][0]["hypotheses"][0]["decision"] = decision
    before = semantic_review(data)
    result = normalize_review(data)
    assert result["status"] == "partial_failure"
    assert result["repair"]["kind"] == "acceptance"
    assert result["reason"]
    assert semantic_review(result) == before


@pytest.mark.parametrize("status", ["done", "partial_failure", "blocked"])
@pytest.mark.parametrize("changes", [False, True])
def test_normalization_prerequisite_routing_and_priority(reviewed, status, changes):
    from modules.agents.hypothesis_review import normalize_review

    task, data = reviewed
    data.update(status=status, reason="Required input is unavailable", prerequisite_gaps=[{
        "criterion_id": "hypotheses", "missing_input": "Required source", "item_ids": ["subject"],
        "source_refs": ["artifact:inventory.json"],
    }])
    if changes:
        data["subject_reviews"][0]["decision"] = "revise"
    result = normalize_review(data)
    assert result["status"] == ("blocked" if status == "blocked" else "partial_failure")
    assert (result["repair"] or {}).get("kind", "none") == (
        "none" if status == "blocked" else "execution"
    )
    validate_review(result, task, "OP_TEST")


@pytest.mark.parametrize("fault", ["unknown", "empty_reason", "unexplained", "blocked", "digest", "rows"])
def test_normalization_rejects_unsupported_terminal_decisions(reviewed, fault):
    from modules.agents.hypothesis_review import normalize_review, semantic_review

    _task, data = reviewed
    if fault == "unknown":
        data["status"] = "invented"
    elif fault in {"empty_reason", "unexplained"}:
        data.update(status="partial_failure", reason="" if fault == "empty_reason" else "All retained")
    elif fault == "blocked":
        data.update(status="blocked", reason="Missing unspecified input")
    elif fault == "digest":
        data["publication_sha256"] = None
    else:
        data["subject_reviews"] = "invalid"
    with pytest.raises(ValueError):
        semantic_review(data)
        normalize_review(data)


def test_current_review_feedback_is_complete_and_digest_bound(reviewed):
    from modules.agents.hypothesis_review import normalize_review, repair_context

    _task, data = reviewed
    data["subject_reviews"][0]["decision"] = "revise"
    data["subject_reviews"][0]["reason"] = "Control must distinguish unauthorized behavior"
    result = normalize_review(data)
    text = repair_context(result, data["publication_sha256"])
    assert data["publication_sha256"] in text
    assert "Control must distinguish unauthorized behavior" in text
    assert data["subject_reviews"][0]["hypotheses"][0]["hypothesis_id"] in text
    assert repair_context(result, "other") == ""
    assert normalize_review(reviewed[1])["repair"]["kind"] == "acceptance"


@pytest.mark.parametrize("action", ["https://service.test/input", "tcp://service.test:9000", "/workspace/source"])
@pytest.mark.parametrize("decision", ["revise", "withdraw"])
def test_repair_supplies_exact_records_and_retained_siblings(producer, tmp_path, action, decision):
    from modules.agents.hypothesis_review import repair_context
    from modules.tools.hypotheses import load_hypothesis_set
    from tests.test_hypotheses import no_test, two_subjects

    task = two_subjects(producer)
    binding, _ = publish(task, tmp_path, [{
        "item_id": "subject", "disposition": "hypotheses_supplied",
        "hypotheses": [hypothesis(action), hypothesis("Retained sibling")],
    }, no_test("second")])
    publication = load_hypothesis_set(binding["artifact_ref"], task, "OP_TEST")
    subject = next(item for item in publication.subjects if item.item_id == "subject")
    changed = next(item for item in subject.hypotheses if item.attacker_controlled_input == action)
    data = {"publication_sha256": binding["sha256"], "subject_reviews": [{
        "item_id": "subject", "decision": "revise", "reason": "Correct unsupported mechanism",
        "hypotheses": [{"hypothesis_id": item.hypothesis_id,
                        "decision": decision if item == changed else "retain", "reason": "Specific review reason"}
                       for item in subject.hypotheses],
    }, {"item_id": "second", "decision": "retain", "hypotheses": []}]}
    text = repair_context(data, binding["sha256"], publication)
    payload = json.loads(text.split("## Exact hypothesis repair records\n")[1].split("\nThese are")[0])
    assert payload["publication_sha256"] == binding["sha256"]
    assert len(payload["corrections"]) == 1
    row = payload["corrections"][0]
    assert row["current_hypothesis_to_revise"] == changed.model_dump(mode="json")
    assert "Retained sibling" not in text
    assert row["review_concern"]["reason"] == "Specific review reason"
    assert f'"decision": "{decision}"' in text and "Specific review reason" in text
    assert "Python preserves all other hypotheses" in text
    assert repair_context(data, "stale", publication) == ""


def test_acceptance_only_repair_does_not_request_republication(reviewed):
    from modules.agents.hypothesis_review import repair_context
    from modules.tools.hypotheses import hypothesis_binding, load_hypothesis_set

    task, data = reviewed
    publication = load_hypothesis_set(hypothesis_binding(task)["artifact_ref"], task, "OP_TEST")
    text = repair_context(data, data["publication_sha256"], publication)
    assert "Reuse the current publication for acceptance" in text
    assert "## Exact hypothesis repair records" not in text


def test_prerequisite_only_repair_does_not_claim_acceptance_ready(reviewed):
    from modules.agents.hypothesis_review import repair_context
    from modules.tools.hypotheses import hypothesis_binding, load_hypothesis_set

    task, data = reviewed
    publication = load_hypothesis_set(hypothesis_binding(task)["artifact_ref"], task, "OP_TEST")
    data["prerequisite_gaps"] = [{"missing_input": "Required source", "item_ids": ["subject"]}]
    text = repair_context(data, data["publication_sha256"], publication)
    assert "Resolve the supplied prerequisite gaps before acceptance" in text
    assert "No hypothesis revisions requested" not in text


@pytest.mark.parametrize("unknown", ["subject", "hypothesis"])
def test_repair_rejects_unknown_publication_records(reviewed, unknown):
    from modules.agents.hypothesis_review import repair_context
    from modules.tools.hypotheses import hypothesis_binding, load_hypothesis_set

    task, data = reviewed
    publication = load_hypothesis_set(hypothesis_binding(task)["artifact_ref"], task, "OP_TEST")
    row = data["subject_reviews"][0]
    row["decision"] = "revise"
    row["hypotheses"][0]["decision"] = "revise"
    if unknown == "subject":
        row["item_id"] = "unknown"
    else:
        row["hypotheses"][0]["hypothesis_id"] = "unknown"
    with pytest.raises(ValueError, match=f"unknown publication {unknown}"):
        repair_context(data, data["publication_sha256"], publication)


def test_normalization_does_not_infer_acceptance_and_supports_frozen_criterion_gaps(reviewed):
    from modules.agents.hypothesis_review import normalize_review, repair_context

    task, data = reviewed
    data["repair"] = {"kind": "acceptance"}
    result = normalize_review(data)
    assert result["status"] == "done" and result["repair"]["kind"] == "none"
    data.update(status="partial_failure", reason="Required artifact not bound", unresolved_criterion_ids=["hypotheses"])
    result = normalize_review(data)
    validate_review(result, task, "OP_TEST")
    assert result["repair"]["kind"] == "acceptance"
    assert "Required artifact not bound" in repair_context(result, result["publication_sha256"])
    result["unresolved_criterion_ids"] = ["invented"]
    with pytest.raises(ValueError, match="frozen criterion"):
        validate_review(result, task, "OP_TEST")
    data["prerequisite_gaps"] = [{"criterion_id": "hypotheses", "missing_input": "Explicit source",
                                  "item_ids": ["subject"], "source_refs": ["artifact:inventory.json"]}]
    data.update(status="blocked", reason="Explicit source unavailable")
    result = normalize_review(data)
    assert result["status"] == "blocked" and result["repair"]["kind"] == "none"


@pytest.mark.parametrize("field,value", [
    ("prerequisite_gaps", "invalid"), ("unresolved_criterion_ids", "invalid"),
    ("unresolved_criterion_ids", [""]), ("unresolved_criterion_ids", [1]),
])
def test_semantic_freeze_rejects_malformed_gaps(reviewed, field, value):
    from modules.agents.hypothesis_review import semantic_review

    _task, data = reviewed
    with pytest.raises(ValueError):
        semantic_review({**data, field: value})


def test_normalization_derives_a_reason_for_explicit_required_input(reviewed):
    from modules.agents.hypothesis_review import normalize_review

    _task, data = reviewed
    data["prerequisite_gaps"] = [{"criterion_id": "hypotheses", "missing_input": "Explicit source",
                                  "item_ids": ["subject"], "source_refs": ["artifact:inventory.json"]}]
    result = normalize_review(data)
    assert result["status"] == "partial_failure"
    assert result["reason"] == "Required frozen input unavailable"
    assert result["repair"]["kind"] == "execution"


@pytest.mark.parametrize("behavior", [
    "A client sends a storage-controlled identity to a permission service",
    "A network command selects a resource using a caller-controlled identifier",
    "A source function opens a caller-selected filesystem path",
])
def test_behavior_assessment_requires_consistent_repair(empty_review, tmp_path, behavior):
    from modules.agents.hypothesis_context import hypothesis_inspection_journal
    from modules.agents.hypothesis_review import normalize_review

    task, data = empty_review
    journal = hypothesis_inspection_journal(task, "OP_TEST", str(tmp_path))
    reference = journal.record_inspection("read_artifact", "behavior", {}, repr({"content": behavior}), ())
    basis = {"kind": "inspected_evidence", "inspection_refs": [reference], "behavior_assessments": [{
        "observed_behavior": behavior, "inspection_refs": [reference], "supports_conditional_test": True,
        "reasoning": "Compare permitted and unauthorized resource access; server behavior remains conditional",
    }]}
    data["subject_reviews"][0]["no_hypothesis_basis"] = basis
    with pytest.raises(ValueError, match="conditional test"):
        validate_review(data, task, "OP_TEST")
    repaired = normalize_review(data)
    assert repaired["status"] == "partial_failure" and repaired["repair"]["kind"] == "acceptance"
    validate_review(repaired, task, "OP_TEST")
    basis["behavior_assessments"][0]["supports_conditional_test"] = False
    basis["behavior_assessments"][0]["reasoning"] = "The inspected example is a non-executable documentation sample"
    validate_review(data, task, "OP_TEST")
    basis["no_relevant_behavior_reason"] = "No behavior"
    with pytest.raises(ValueError, match="conflict"):
        validate_review(data, task, "OP_TEST")
    del basis["no_relevant_behavior_reason"]
    basis["behavior_assessments"][0]["inspection_refs"] = ["artifact:uncited.json"]
    with pytest.raises(ValueError, match="cite"):
        validate_review(data, task, "OP_TEST")


def test_bound_review_schema_owns_digest_and_constrains_populated_subject(reviewed):
    from modules.agents.hypothesis_review import BoundHypothesisReview

    task, data = reviewed
    bound = BoundHypothesisReview.capture(task, "OP_TEST")
    schema = bound.output_model.model_json_schema()
    assert "publication_sha256" not in schema["properties"]
    assert "status" in schema["required"]
    assert schema["properties"]["status"]["enum"] == ["done", "partial_failure", "blocked"]
    wire = {key: value for key, value in data.items() if key != "publication_sha256"}
    assert bound.canonicalize(wire, task)["publication_sha256"] == data["publication_sha256"]
    assert bound.canonicalize(data, task) == bound.canonicalize(wire, task)
    assert any(definition.get("properties", {}).get("item_id", {}).get("const") == "subject"
               for definition in schema["$defs"].values())
    for change in ("status", "subject", "hypothesis", "basis", "misnested", "criterion", "duplicate"):
        candidate = copy.deepcopy(wire)
        row = candidate["subject_reviews"][0]
        if change == "status":
            candidate.pop("status")
        elif change == "subject":
            row["item_id"] = "foreign"
        elif change == "hypothesis":
            row["hypotheses"][0]["hypothesis_id"] = "foreign"
        elif change == "basis":
            row["no_hypothesis_basis"] = {"kind": "metadata_only"}
        elif change == "misnested":
            row["behavior_assessments"] = []
        elif change == "criterion":
            candidate["unresolved_criterion_ids"] = ["foreign"]
        else:
            candidate["subject_reviews"].append(copy.deepcopy(row))
        with pytest.raises(ValueError):
            bound.canonicalize(candidate, task)
    with pytest.raises(ValueError, match="latest publication"):
        bound.canonicalize({**wire, "publication_sha256": "stale"}, task)
    from dataclasses import replace

    with pytest.raises(ValueError, match="Publication changed"):
        bound.canonicalize(wire, replace(task, task_uid="foreign-task"))
    assert bound.output_model.model_validate_json(bound.example).status == "done"


def test_bound_empty_review_requires_nested_basis(empty_review):
    from modules.agents.hypothesis_review import BoundHypothesisReview

    task, data = empty_review
    bound = BoundHypothesisReview.capture(task, "OP_TEST")
    with pytest.raises(ValueError, match="no_hypothesis_basis"):
        bound.canonicalize(data, task)
    row = data["subject_reviews"][0]
    row["no_hypothesis_basis"] = {"kind": "inspected_evidence", "inspection_refs": ["artifact:inspection.json"],
                                  "no_relevant_behavior_reason": "Only inert documentation"}
    assert bound.canonicalize(data, task)["subject_reviews"][0] == row
    assert bound.output_model.model_validate_json(bound.example).repair.kind == "acceptance"


@pytest.mark.parametrize("case", ["missing", "null", "empty", "assessment", "metadata", "unreviewed", "valid"])
def test_native_empty_review_without_candidates_requires_explanation(empty_review, case):
    from jsonschema import Draft202012Validator
    from strands.tools.structured_output.structured_output_tool import StructuredOutputTool

    from modules.agents.hypothesis_review import BoundHypothesisReview

    task, data = empty_review
    bound = BoundHypothesisReview.capture(task, "OP_TEST")
    data.pop("publication_sha256")
    basis = {"kind": "inspected_evidence", "inspection_refs": ["artifact:inspection.json"]}
    if case == "null":
        basis["no_relevant_behavior_reason"] = None
    elif case == "empty":
        basis["no_relevant_behavior_reason"] = ""
    elif case == "assessment":
        basis["behavior_assessments"] = []
    elif case in {"metadata", "unreviewed"}:
        basis["kind"] = "metadata_only" if case == "metadata" else "necessary_evidence_unreviewed"
    elif case == "valid":
        basis["no_relevant_behavior_reason"] = "Inspected source has only inert documentation"
    data["subject_reviews"][0]["no_hypothesis_basis"] = basis
    invalid = case in {"missing", "null", "empty", "assessment"}
    for schema in (bound.output_model.model_json_schema(),
                   StructuredOutputTool(bound.output_model).tool_spec["inputSchema"]["json"]):
        assert bool(list(Draft202012Validator(schema).iter_errors(data))) is invalid
    if invalid:
        with pytest.raises(ValueError):
            bound.output_model.model_validate_json(json.dumps(data))
    else:
        assert bound.output_model.model_validate_json(json.dumps(data)).subject_reviews[0].no_hypothesis_basis
    schema = bound.output_model.model_json_schema()["$defs"]["BoundNoHypothesisBasis0"]
    assert "no_relevant_behavior_reason" in schema["oneOf"][0]["required"]
    assert schema["properties"]["behavior_assessments"]["type"] == "null"


@pytest.mark.parametrize("case", ["missing", "null", "empty", "explanation", "foreign", "valid"])
def test_native_empty_review_with_candidates_requires_assessments(candidate_review, case):
    from jsonschema import Draft202012Validator
    from strands.tools.structured_output.structured_output_tool import StructuredOutputTool

    from modules.agents.hypothesis_review import BoundHypothesisReview

    task, data, basis, _journal = candidate_review
    bound = BoundHypothesisReview.capture(task, "OP_TEST")
    data.pop("publication_sha256")
    if case == "missing":
        del basis["behavior_assessments"]
    elif case == "null":
        basis["behavior_assessments"] = None
    elif case == "empty":
        basis["behavior_assessments"] = []
    elif case == "explanation":
        basis["no_relevant_behavior_reason"] = "No inputs recorded"
    elif case == "foreign":
        basis["behavior_assessments"][0]["input_candidate_ids"] = ["unknown"]
    for schema in (bound.output_model.model_json_schema(),
                   StructuredOutputTool(bound.output_model).tool_spec["inputSchema"]["json"]):
        assert bool(list(Draft202012Validator(schema).iter_errors(data))) is (case != "valid")
    if case != "valid":
        with pytest.raises(ValueError):
            bound.output_model.model_validate_json(json.dumps(data))
    else:
        bound.output_model.model_validate_json(json.dumps(data))
        validate_review({**data, "publication_sha256": bound.digest}, task, "OP_TEST")
    schema = bound.output_model.model_json_schema()["$defs"]["BoundNoHypothesisBasis0"]
    assert "behavior_assessments" in schema["oneOf"][0]["required"]
    assert schema["properties"]["no_relevant_behavior_reason"]["type"] == "null"


@pytest.mark.parametrize("supported", [True, False])
def test_legacy_behavior_assessments_preserve_semantic_judgments(empty_review, supported):
    from modules.agents.hypothesis_review import BoundHypothesisReview, normalize_review

    task, data = empty_review
    basis = {"kind": "inspected_evidence", "inspection_refs": ["artifact:inspection.json"],
             "behavior_assessments": [{"observed_behavior": "Caller selects a resource",
                                       "inspection_refs": ["artifact:inspection.json"],
                                       "supports_conditional_test": supported,
                                       "reasoning": "Compare the observed behavior to the intended boundary"}]}
    data["subject_reviews"][0]["no_hypothesis_basis"] = basis
    result = BoundHypothesisReview.capture(task, "OP_TEST").canonicalize(data, task)
    assert result["subject_reviews"][0]["no_hypothesis_basis"]["behavior_assessments"][0][
        "supports_conditional_test"] is supported
    assert "no_relevant_behavior_reason" not in result["subject_reviews"][0]["no_hypothesis_basis"]
    assert normalize_review(result)["status"] == ("partial_failure" if supported else "done")
    assert "input_candidate_ids" not in basis["behavior_assessments"][0]
    basis["behavior_assessments"][0]["input_candidate_ids"] = ["foreign"]
    with pytest.raises(ValueError, match="unknown input_candidate_ids"):
        BoundHypothesisReview.capture(task, "OP_TEST").canonicalize(data, task)


def test_bound_review_mixed_subjects(reviewed, monkeypatch):
    from types import SimpleNamespace

    import modules.agents.hypothesis_review as review_module

    task, _data = reviewed
    publication = review_module.load_hypothesis_set(
        review_module.hypothesis_binding(task)["artifact_ref"], task, "OP_TEST",
    )
    subjects = [*publication.subjects, SimpleNamespace(item_id="empty-subject", hypotheses=[])]
    monkeypatch.setattr(review_module, "load_hypothesis_set", lambda *_args: SimpleNamespace(subjects=subjects))
    bound = review_module.BoundHypothesisReview.capture(task, "OP_TEST")
    wire = bound.output_model.model_validate_json(bound.example).model_dump(exclude_none=True)
    assert len(bound.canonicalize(wire, task)["subject_reviews"]) == 2
    wire["subject_reviews"][1]["item_id"] = "foreign"
    with pytest.raises(ValueError):
        bound.canonicalize(wire, task)
    wire = bound.output_model.model_validate_json(bound.example).model_dump(exclude_none=True)
    wire["unresolved_criterion_ids"] = ["unknown"]
    with pytest.raises(ValueError):
        bound.canonicalize(wire, task)
    subjects.clear()
    with pytest.raises(ValueError, match="without subjects"):
        review_module.BoundHypothesisReview.capture(task, "OP_TEST")


@pytest.mark.parametrize("mixed", [False, True])
def test_bound_review_strands_runtime_schema_resolves_every_reference(reviewed, monkeypatch, mixed):
    import json
    from types import SimpleNamespace

    from strands.tools.structured_output.structured_output_tool import StructuredOutputTool

    import modules.agents.hypothesis_review as review_module

    task, _data = reviewed
    if mixed:
        original = review_module.load_hypothesis_set(
            review_module.hypothesis_binding(task)["artifact_ref"], task, "OP_TEST",
        )
        monkeypatch.setattr(review_module, "load_hypothesis_set", lambda *_args: SimpleNamespace(subjects=[
            *original.subjects, SimpleNamespace(item_id="empty-subject", hypotheses=[]),
        ]))
    bound = review_module.BoundHypothesisReview.capture(task, "OP_TEST")
    schema = StructuredOutputTool(bound.output_model).tool_spec["inputSchema"]["json"]
    assert "status" in schema["required"]
    assert "publication_sha256" not in schema["properties"]
    assert '"$ref"' not in json.dumps(schema)
    assert '"subject"' in json.dumps(schema)
    if mixed:
        assert '"empty-subject"' in json.dumps(schema)
        assert "no_hypothesis_basis" in json.dumps(schema)


@pytest.mark.parametrize("blocked", [False, True])
def test_supported_behavior_can_wait_for_explicit_prerequisite(empty_review, tmp_path, blocked):
    from modules.agents.hypothesis_context import hypothesis_inspection_journal
    from modules.agents.hypothesis_review import normalize_review

    task, data = empty_review
    journal = hypothesis_inspection_journal(task, "OP_TEST", str(tmp_path))
    reference = journal.record_inspection("read_artifact", "behavior", {},
                                          repr({"content": "Function accepts caller-selected resource identity"}), ())
    data["subject_reviews"][0]["no_hypothesis_basis"] = {
        "kind": "inspected_evidence", "inspection_refs": [reference], "behavior_assessments": [{
            "observed_behavior": "Caller selects a resource", "inspection_refs": [reference],
            "supports_conditional_test": True, "reasoning": "Compare authorization across owners",
        }],
    }
    data["prerequisite_gaps"] = [{"criterion_id": "hypotheses", "missing_input": "Required ownership policy",
                                 "item_ids": ["subject"], "source_refs": ["artifact:inventory.json"]}]
    data["status"] = "blocked" if blocked else "partial_failure"
    data["reason"] = "Required policy is unavailable" if blocked else "Inspect required policy first"
    result = normalize_review(data)
    assert result["repair"]["kind"] == ("none" if blocked else "execution")
    validate_review(result, task, "OP_TEST")


def test_invalid_review_can_be_reassessed_after_reference_correction(empty_review, tmp_path):
    from modules.agents.hypothesis_context import hypothesis_inspection_journal
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection

    task, data = empty_review
    correction = HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST")
    basis = {"kind": "inspected_evidence", "inspection_refs": ["artifact:unavailable.json"],
             "no_relevant_behavior_reason": "Only static markup"}
    data["subject_reviews"][0]["no_hypothesis_basis"] = basis
    with pytest.raises(ValueError, match="subject_reviews\\[0\\]"):
        correction.prepare(data, task)
    assert correction.accepted == {}
    journal = hypothesis_inspection_journal(task, "OP_TEST", str(tmp_path))
    ref = journal.record_inspection("read_artifact", "source", {}, '{"content":"static markup"}', ())
    basis["inspection_refs"] = [ref]
    data["subject_reviews"][0]["reason"] = "Corrected assessment after inspection"
    result = correction.prepare(data, task)
    validate_review(result, task, "OP_TEST")
    assert result["status"] == "done"


def test_incomplete_behavior_assessment_does_not_freeze_bad_judgment(empty_review, tmp_path):
    from modules.agents.hypothesis_context import hypothesis_inspection_journal
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection

    task, data = empty_review
    journal = hypothesis_inspection_journal(task, "OP_TEST", str(tmp_path))
    ref = journal.record_inspection("read_artifact", "source", {}, '{"content":"resource access"}', ())
    data["subject_reviews"][0]["no_hypothesis_basis"] = {"kind": "inspected_evidence", "inspection_refs": [ref]}
    correction = HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST")
    with pytest.raises(ValueError, match="behavior assessments"):
        correction.prepare(data, task)
    row = data["subject_reviews"][0]
    row.update(decision="revise", reason="Observed resource boundary supports a future comparison")
    row["no_hypothesis_basis"]["behavior_assessments"] = [{
        "observed_behavior": "Resource selection", "inspection_refs": [ref],
        "supports_conditional_test": True, "reasoning": "Compare authorized and unauthorized resource access",
    }]
    result = correction.prepare(data, task)
    assert result["status"] == "partial_failure" and result["repair"]["kind"] == "acceptance"
    validate_review(result, task, "OP_TEST")


def test_correction_preserves_valid_subjects_and_rejects_stale_binding(reviewed):
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection

    task, data = reviewed
    correction = HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST")
    correction.prepare(data, task)
    changed = copy.deepcopy(data)
    changed["subject_reviews"][0]["reason"] = "Unauthorized rewrite"
    with pytest.raises(ValueError, match="already validated"):
        correction.prepare(changed, task)
    assert correction.prepare(data, task)["status"] == "done"
    with pytest.raises(ValueError, match="digest"):
        correction.prepare({**data, "publication_sha256": "stale"}, task)
    with pytest.raises(ValueError, match="changed"):
        correction.prepare(data, replace(task, task_uid="other"))


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unknown", "not_list", "bad_row", "bad_hypothesis"])
def test_correction_rejects_invalid_subject_identity_and_coverage(reviewed, fault):
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection

    task, data = reviewed
    correction = HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST")
    if fault == "missing":
        data["subject_reviews"] = []
    elif fault == "duplicate":
        data["subject_reviews"] *= 2
    elif fault == "unknown":
        data["subject_reviews"][0]["item_id"] = "foreign"
    elif fault == "not_list":
        data["subject_reviews"] = None
    elif fault == "bad_row":
        data["subject_reviews"] = [None]
    else:
        data["subject_reviews"][0]["hypotheses"] = []
    with pytest.raises(ValueError):
        correction.prepare(data, task)
    assert correction.accepted == {}


@pytest.fixture
def candidate_review(empty_review, tmp_path, monkeypatch):
    from modules.agents import hypothesis_review
    from modules.agents.hypothesis_context import hypothesis_inspection_journal

    task, data = empty_review
    journal = hypothesis_inspection_journal(task, "OP_TEST", str(tmp_path))
    reference = journal.record_inspection("read_artifact", "source", {}, json.dumps({
        "artifact_ref": "artifact:source.js", "start_line": 1, "end_line": 2,
        "content": 'const link = {url: "/file?path=example"};',
    }), ())
    candidate = {"candidate_id": "candidate-path", "origins": [{"kind": "client_link",
                 "reference": "artifact:source.js", "source_location": {"start_line": 1, "end_line": 1}}]}
    monkeypatch.setattr(hypothesis_review, "review_input_candidates", lambda _task: {"subject": [candidate]})
    basis = {"kind": "inspected_evidence", "inspection_refs": [reference], "behavior_assessments": [{
        "observed_behavior": "Client link includes query input path", "inspection_refs": [reference],
        "input_candidate_ids": ["candidate-path"], "supports_conditional_test": False,
        "reasoning": "An informational example without evidence of a security-relevant boundary",
    }]}
    data["subject_reviews"][0]["no_hypothesis_basis"] = basis
    return task, data, basis, journal


def test_visible_input_can_be_assessed_without_requiring_a_hypothesis(candidate_review):
    task, data, _, _ = candidate_review
    validate_review(data, task, "OP_TEST")


@pytest.mark.parametrize("fault", ["omitted", "unknown", "uninspected", "no_behavior"])
def test_empty_review_cannot_ignore_or_misattribute_visible_input(candidate_review, fault):
    task, data, basis, journal = candidate_review
    assessment = basis["behavior_assessments"][0]
    if fault == "omitted":
        assessment["input_candidate_ids"] = []
    elif fault == "unknown":
        assessment["input_candidate_ids"] = ["foreign"]
    elif fault == "uninspected":
        ref = journal.record_inspection("read_artifact", "unrelated", {}, json.dumps({
            "artifact_ref": "artifact:other.js", "content": "different source", "start_line": 1, "end_line": 4,
        }), ())
        assessment["inspection_refs"] = [ref]
        basis["inspection_refs"] = [ref]
    else:
        del basis["behavior_assessments"]
        basis["no_relevant_behavior_reason"] = "No parameters recorded"
    with pytest.raises(ValueError, match="input_candidate|Source input"):
        validate_review(data, task, "OP_TEST")


def test_bound_candidate_schema_and_supported_input_route_to_publication_repair(candidate_review):
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection

    task, data, basis, _ = candidate_review
    bound = BoundHypothesisReview.capture(task, "OP_TEST")
    assert '"candidate-path"' in json.dumps(bound.output_model.model_json_schema())
    bad = copy.deepcopy(data)
    bad["subject_reviews"][0]["no_hypothesis_basis"]["behavior_assessments"][0]["input_candidate_ids"] = ["foreign"]
    with pytest.raises(ValueError):
        bound.canonicalize(bad, task)
    basis["behavior_assessments"][0]["supports_conditional_test"] = True
    result = HypothesisReviewCorrection(bound, "OP_TEST").prepare(data, task)
    assert result["status"] == "partial_failure" and result["repair"]["kind"] == "acceptance"
    validate_review(result, task, "OP_TEST")


def test_mixed_correction_preserves_only_valid_subjects(producer, tmp_path):
    from modules.agents.hypothesis_context import hypothesis_inspection_journal
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection
    from modules.tools import memory

    path = tmp_path / "inventory.json"
    manifest = json.loads(path.read_text())
    manifest["items"].append({**manifest["items"][0], "id": "second"})
    path.write_text(json.dumps(manifest))
    _, digest = memory._load_inventory_manifest("artifact:inventory.json", persist_normalization=False)
    task = replace(producer, acceptance=replace(producer.acceptance, manifest_hash="", basis=replace(
        producer.acceptance.basis, item_ids=["subject", "second"], snapshot_hash=digest,
    )))
    binding, _ = publish(task, tmp_path, [
        {"item_id": "subject", "disposition": "hypotheses_supplied", "hypotheses": [hypothesis()]},
        {"item_id": "second", "disposition": "no_testable_hypotheses", "reason": "No relevant behavior"},
    ])
    valid = {"item_id": "subject", "decision": "retain", "reason": "Adequate controlled test",
             "hypotheses": [{"hypothesis_id": binding["hypothesis_ids_by_subject"]["subject"][0],
                             "decision": "retain", "reason": "Controls distinguish the outcome",
                             "semantic_checks": semantic_checks()}]}
    invalid = {"item_id": "second", "decision": "retain", "reason": "No test", "hypotheses": [],
               "no_hypothesis_basis": {"kind": "inspected_evidence", "inspection_refs": []}}
    data = {"status": "done", "subject_reviews": [valid, invalid]}
    correction = HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST")
    with pytest.raises(ValueError, match="accepted_subject_reviews"):
        correction.prepare(data, task)
    assert correction.accepted == {"subject": valid}
    journal = hypothesis_inspection_journal(task, "OP_TEST", str(tmp_path))
    ref = journal.record_inspection("read_artifact", "source", {}, '{"content":"static text"}', ())
    invalid["reason"] = "Corrected after reading content"
    invalid["no_hypothesis_basis"].update(inspection_refs=[ref], no_relevant_behavior_reason="Static content only")
    result = correction.prepare(data, task)
    assert result["status"] == "done"
    validate_review(result, task, "OP_TEST")
    data["subject_reviews"][0]["hypotheses"][0]["decision"] = "withdraw"
    with pytest.raises(ValueError, match="already validated"):
        correction.prepare(data, task)


@pytest.mark.parametrize("fault", ["missing", "criterion", "subject", "source", "type", "malformed"])
def test_invalid_prerequisite_judgment_is_not_frozen(empty_review, fault):
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection

    task, data = empty_review
    data["subject_reviews"][0]["no_hypothesis_basis"] = {
        "kind": "necessary_evidence_unreviewed", "inspection_refs": [],
    }
    gap = {"criterion_id": "hypotheses", "missing_input": "Inspect the source", "item_ids": ["subject"],
           "source_refs": ["artifact:inventory.json"]}
    data["prerequisite_gaps"] = [gap]
    if fault == "missing":
        data["prerequisite_gaps"] = []
    elif fault == "criterion":
        gap["criterion_id"] = "foreign"
    elif fault == "subject":
        gap["item_ids"] = ["foreign"]
    elif fault == "source":
        gap["source_refs"] = ["artifact:foreign.json"]
    elif fault == "type":
        data["prerequisite_gaps"] = "invalid"
    else:
        data["prerequisite_gaps"] = [None]
    correction = HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST")
    with pytest.raises(ValueError):
        correction.prepare(data, task)
    assert correction.accepted == {}
    data["subject_reviews"][0].update(reason="Corrected to publication repair", decision="revise",
                                     no_hypothesis_basis={"kind": "metadata_only", "inspection_refs": []})
    data["prerequisite_gaps"] = []
    assert correction.prepare(data, task)["repair"]["kind"] == "acceptance"


def test_valid_subject_prerequisite_cannot_disappear_during_envelope_correction(reviewed):
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection

    task, data = reviewed
    gap = {"criterion_id": "hypotheses", "missing_input": "Inspect an explicit required source",
           "item_ids": ["subject"], "source_refs": ["artifact:inventory.json"]}
    data["prerequisite_gaps"] = [gap]
    data["repair"] = {"kind": "execution", "invalid_field": True}
    correction = HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST")
    with pytest.raises(ValueError):
        correction.prepare(data, task)
    assert correction.accepted
    data["repair"] = {"kind": "execution"}
    data["prerequisite_gaps"] = []
    with pytest.raises(ValueError, match="already validated"):
        correction.prepare(data, task)
    data["prerequisite_gaps"] = [gap]
    result = correction.prepare(data, task)
    assert result["repair"]["kind"] == "execution"
    validate_review(result, task, "OP_TEST")


@pytest.mark.parametrize("check", ["grounding", "test_discrimination", "identity_requirements"])
@pytest.mark.parametrize("target", ["https://service.test/resource", "tcp://service.test:1234", "/data/private"])
def test_semantic_check_conflict_can_be_reassessed_before_freezing(reviewed, check, target):
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection, repair_context

    task, data = reviewed
    item = data["subject_reviews"][0]["hypotheses"][0]
    item["semantic_checks"][check] = {"verdict": "revise", "reason": f"Specify the access boundary for {target}"}
    correction = HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST")
    with pytest.raises(ValueError, match="retained hypothesis"):
        correction.prepare(data, task)
    assert correction.accepted == {}
    item["decision"] = "revise"
    data["subject_reviews"][0]["decision"] = "revise"
    result = correction.prepare(data, task)
    assert result["repair"]["kind"] == "acceptance"
    assert target in repair_context(result, result["publication_sha256"])
    assert result["subject_reviews"][0]["hypotheses"][0]["semantic_checks"] == item["semantic_checks"]


def test_new_bound_review_requires_checks_but_historical_review_remains_readable(reviewed):
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection, semantic_review

    task, data = reviewed
    del data["subject_reviews"][0]["hypotheses"][0]["semantic_checks"]
    assert semantic_review(data)["subject_reviews"]
    bound = BoundHypothesisReview.capture(task, "OP_TEST")
    with pytest.raises(ValueError, match="semantic_checks"):
        bound.canonicalize(data, task)
    with pytest.raises(ValueError, match="semantic_checks"):
        HypothesisReviewCorrection(bound, "OP_TEST").prepare(data, task)


@pytest.mark.parametrize("identity", ["adequate", "not_applicable"])
def test_grounded_future_proposal_does_not_require_execution_credentials(reviewed, identity):
    from modules.agents.hypothesis_review import BoundHypothesisReview, HypothesisReviewCorrection

    task, data = reviewed
    item = data["subject_reviews"][0]["hypotheses"][0]
    item["semantic_checks"]["identity_requirements"] = {
        "verdict": identity, "reason": "Future test identities are specified; credentials need not exist yet",
    }
    assert HypothesisReviewCorrection(BoundHypothesisReview.capture(task, "OP_TEST"), "OP_TEST").prepare(
        data, task,
    )["status"] == "done"


@pytest.mark.parametrize("value", [None, "", "Revise the controls"])
def test_bound_runtime_instructions_are_nullable_and_canonical(reviewed, value):
    from strands.tools.structured_output.structured_output_tool import StructuredOutputTool

    from modules.agents.hypothesis_review import BoundHypothesisReview

    task, data = reviewed
    bound = BoundHypothesisReview.capture(task, "OP_TEST")
    schema = StructuredOutputTool(bound.output_model).tool_spec["inputSchema"]["json"]
    assert set(schema["properties"]["instructions"]["type"]) == {"string", "null"}
    result = bound.canonicalize({**data, "instructions": value}, task)
    assert result["instructions"] == ("" if value is None else value)
