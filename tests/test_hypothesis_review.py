# ruff: noqa: F811
"""Review every current record once without treating evidence availability as semantic support."""

import copy

import pytest

from modules.agents.hypothesis_review import validate_review
from tests.test_hypotheses import hypothesis, producer, publish  # noqa: F401


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
    assert result["status"] == ("blocked" if status == "blocked" and not changes else "partial_failure")
    assert (result["repair"] or {}).get("kind", "none") == (
        "acceptance" if changes else "none" if status == "blocked" else "execution"
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
