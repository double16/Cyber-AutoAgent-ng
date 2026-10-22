# ruff: noqa: F811
"""Review every current record once without treating evidence availability as semantic support."""

import copy

import pytest

from modules.agents.hypothesis_review import validate_review
from tests.test_hypotheses import hypothesis, producer, publish  # noqa: F401


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


def test_missing_provenance_can_be_completed_without_rewriting_judgments(reviewed):
    from modules.agents.hypothesis_review import completes_review_provenance, semantic_review

    _, data = reviewed
    before = semantic_review(data)
    after = copy.deepcopy(before)
    row = after["subject_reviews"][0]
    row["no_hypothesis_basis"] = {"kind": "inspected_evidence", "inspection_refs": []}
    assert completes_review_provenance(before, after)
    filled = copy.deepcopy(after)
    filled["subject_reviews"][0]["no_hypothesis_basis"]["inspection_refs"] = ["artifact:result.json"]
    assert completes_review_provenance(after, filled)
    assert not completes_review_provenance(filled, after)
    altered = copy.deepcopy(filled)
    altered["subject_reviews"][0]["no_hypothesis_basis"]["kind"] = "metadata_only"
    assert not completes_review_provenance(filled, altered)
    altered = copy.deepcopy(before)
    altered["subject_reviews"][0]["reason"] = "Different semantic judgment"
    assert not completes_review_provenance(before, altered)


@pytest.fixture
def reviewed(producer, tmp_path):
    binding, _ = publish(producer, tmp_path)
    return producer, {
        "status": "done", "publication_sha256": binding["sha256"],
        "subject_reviews": [{"item_id": "subject", "decision": "retain", "reason": "Conditional safe test",
                             "hypotheses": [{"hypothesis_id": binding["hypothesis_ids_by_subject"]["subject"][0],
                                             "decision": "retain", "reason": "Controls distinguish impact"}]}],
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


def test_provenance_completion_preserves_supplied_behavior_judgments(reviewed):
    from modules.agents.hypothesis_review import completes_review_provenance, semantic_review

    _, data = reviewed
    before = semantic_review(data)
    before["subject_reviews"][0]["no_hypothesis_basis"] = {
        "kind": "inspected_evidence", "inspection_refs": ["artifact:result.json"],
    }
    after = copy.deepcopy(before)
    after["subject_reviews"][0]["no_hypothesis_basis"]["no_relevant_behavior_reason"] = "Only static markup"
    assert completes_review_provenance(before, after)
    assert not completes_review_provenance(after, before)
    changed = copy.deepcopy(after)
    changed["subject_reviews"][0]["no_hypothesis_basis"]["inspection_refs"] = ["artifact:other.json"]
    assert not completes_review_provenance(after, changed)


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
