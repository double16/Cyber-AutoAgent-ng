"""Frozen selection inputs contain complete source documents and production schemas."""

import hashlib
import json
from pathlib import Path

from selection_review_snapshot import selection_output_model

BASE = Path(__file__).parent


def test_frozen_selection_pairs_are_balanced_and_identified():
    rows = json.loads((BASE / "selection-inputs.json").read_text())
    assert len(rows) == 24
    assert len({row["case"] for row in rows}) == 12
    assert sum(row["expected"] for row in rows) == 12
    for row in rows:
        assert hashlib.sha256(row["prompt"].encode()).hexdigest() == row["sha256"]
        assert row["schema"] == selection_output_model(
            row["requirements"], task_local=row["arm"] == "candidate",
        ).model_json_schema()
        assert "COMPLETE technique" in row["prompt"]
        assert '"passages"' in row["prompt"]
        assert ("task_local_context" in row["prompt"]) is (row["arm"] == "candidate")


def test_frozen_matching_and_mismatching_baselines_share_methodology():
    rows = json.loads((BASE / "selection-inputs.json").read_text())
    baselines = [row for row in rows if row["arm"] == "baseline"]
    for row in baselines:
        opposite = next(other for other in baselines if other["case"].rsplit("-", 1)[0]
                        == row["case"].rsplit("-", 1)[0] and other["expected"] != row["expected"])
        assert row["prompt"] == opposite["prompt"]


def test_review_pairs_use_complete_production_inputs_and_bound_schemas():
    rows = json.loads((BASE / "review-inputs.json").read_text())
    assert len(rows) == 32
    assert len({row["case"] for row in rows}) == 16
    for row in rows:
        assert hashlib.sha256(row["prompt"].encode()).hexdigest() == row["sha256"]
        assert "Authoritative current hypothesis publication" in row["prompt"]
        assert "Required hypothesis review output" in row["prompt"]
        assert "Retained inspection evidence" in row["prompt"]
        assert "subject_reviews" in row["schema"]["properties"]
        partner = next(other for other in rows if other["case"] == row["case"] and other["arm"] != row["arm"])
        assert row["schema"] == partner["schema"]
        assert row["system"] == partner["system"]
        assert row["publication"] == partner["publication"]
        assert row["expected_verdict"] == partner["expected_verdict"]
        assert ("Frozen inventory lookup examples" in row["prompt"]) is (row["arm"] == "baseline")


def test_corrected_review_fixture_has_valid_frozen_inventory():
    rows = json.loads((BASE / "review-inputs-corrected.json").read_text())
    assert len(rows) == 32
    for row in rows:
        assert row["publication"]["source_refs"] == ["artifact:inventory.json"]
        assert "Frozen input context unavailable" not in row["prompt"]
        assert "recorded_observation" in row["prompt"]
        assert "artifact:form.html" in row["prompt"]
        assert "Retained inspection evidence" in row["prompt"]
        assert hashlib.sha256(row["prompt"].encode()).hexdigest() == row["sha256"]


def test_review_v2_valid_controls_have_concrete_sources_and_identity_needs():
    rows = json.loads((BASE / "review-inputs-v2.json").read_text())
    assert len(rows) == 32
    assert len({row["case"] for row in rows}) == 16
    for row in rows:
        assert row["publication"]["source_refs"] == ["artifact:inventory.json"]
        assert "Frozen input context unavailable" not in row["prompt"]
        assert "synthetic, not live evidence" not in row["prompt"]
        assert hashlib.sha256(row["prompt"].encode()).hexdigest() == row["sha256"]
        if row["case"] == "http_valid_anonymous":
            assert set(row["admissible_verdicts"]) == {"adequate", "not_applicable"}
        if row["case"] in {"csrf-valid", "property-valid", "parser-valid"}:
            hypothesis = row["publication"]["subjects"][0]["hypotheses"][0]
            assert hypothesis["authentication_requirement"] == {"comparison": "none", "roles": ["user"]}
            assert len(hypothesis["safe_test_steps"]) == 3
            assert "user" in row["prompt"]
        partner = next(other for other in rows if other["case"] == row["case"] and other["arm"] != row["arm"])
        assert row["schema"] == partner["schema"]
        assert row["system"] == partner["system"]
        assert row["publication"] == partner["publication"]


def test_review_v2_role_catalog_and_controls_are_complete():
    rows = json.loads((BASE / "review-inputs-v2.json").read_text())
    for row in rows:
        assert "observer, reader, maintainer" in row["prompt"]
        h = row["publication"]["subjects"][0]["hypotheses"][0]
        if row["case"] == "http_valid_anonymous":
            assert "X-Probe-Marker" in " ".join(h["positive_expectations"])
        if row["case"].startswith("network_"):
            assert "excluded receiver" in " ".join(h["positive_expectations"])
        if row["case"] == "filesystem_valid_single_reader":
            assert "known outside marker bytes" in " ".join(h["positive_expectations"])


def test_contract_view_inputs_isolate_presentation_changes():
    for filename, count in [("review-inputs-v3.json", 32), ("selection-inputs-v3.json", 24)]:
        rows = json.loads((BASE / filename).read_text())
        assert len(rows) == count
        for row in rows:
            partner = next(other for other in rows if other["case"] == row["case"] and other["arm"] != row["arm"])
            assert row["schema"] == partner["schema"]
            assert hashlib.sha256(row["prompt"].encode()).hexdigest() == row["sha256"]
            if filename.startswith("review"):
                assert row["publication"] == partner["publication"]
                assert row["system"] == partner["system"]
                assert ("comparison_between_authenticated_identities" in row["prompt"]) is (row["arm"] == "candidate")
            else:
                assert "task_local_context" in row["prompt"]
                assert '"task_relevance"' in json.dumps(row["schema"])
                assert ('"requested_deliverable"' in row["prompt"]) is (row["arm"] == "candidate")
