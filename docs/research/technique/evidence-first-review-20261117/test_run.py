"""Offline verification of replay isolation and actual assembled inputs."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.handlers.utils import sanitize_toon_value

spec = importlib.util.spec_from_file_location("review_probe", Path(__file__).with_name("run.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("case", probe.CASES, ids=lambda row: row[0])
def test_assembled_variants_keep_evidence_and_bound_contract(tmp_path, monkeypatch, case):
    system, schema, baseline, revised = probe.prepare(tmp_path.resolve(), case, monkeypatch)
    assert system
    assert schema.model_json_schema()
    for prompt in (baseline, revised):
        assert sanitize_toon_value(case[3]) in prompt
        assert "artifact:source.txt" in prompt
        assert "Authoritative current hypothesis publication" in prompt
        assert "Required hypothesis review output" in prompt
    assert "Frozen acceptance results:" in baseline
    assert "Frozen acceptance results:" not in revised
    assert baseline.index("Authoritative current hypothesis publication") < baseline.index(sanitize_toon_value(case[3]))
    assert revised.index(sanitize_toon_value(case[3])) < revised.index("Authoritative current hypothesis publication")


@pytest.mark.parametrize("failure", [False, True])
def test_runner_preserves_results_errors_and_resumes(tmp_path, monkeypatch, failure):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(probe, "load_dotenv", lambda *args: None)
    monkeypatch.setattr(probe, "prepare", lambda *args, **kwargs: (
        "system", SimpleNamespace(model_json_schema=dict, model_validate=lambda value: value), "old", "new", lambda value: value,
    ))
    monkeypatch.setattr(probe, "Agent", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(probe, "create_strands_model", lambda *args: None)
    monkeypatch.setattr(probe, "require_prompt_token_limit", lambda *args: 48000)
    calls = []

    def invoke(*args, **kwargs):
        calls.append(args)
        if failure:
            raise RuntimeError("Unavailable")
        return SimpleNamespace(structured_output=SimpleNamespace(model_dump=lambda: {"status": "done"}))

    monkeypatch.setattr(probe, "invoke_structured", invoke)
    probe.main()
    probe.main()
    assert len(calls) == 36
    records = list(Path("outputs").rglob("*-baseline.json"))
    assert len(records) == 18
    row = json.loads(records[0].read_text())
    assert ("error" in row) is failure
    assert ("output" in row) is not failure


@pytest.mark.parametrize("case", probe.CASES, ids=lambda row: row[0])
def test_provenance_mode_records_real_content_receipt(tmp_path, monkeypatch, case):
    _system, _schema, baseline, revised, validate = probe.prepare(
        tmp_path.resolve(), case, monkeypatch, with_validator=True,
    )
    assert "returned_results/" in baseline
    assert "returned_results/" in revised
    assert callable(validate)


@pytest.mark.parametrize("forged", [False, True])
def test_provenance_validator_checks_actual_content_receipt(tmp_path, monkeypatch, forged):
    *_, validate = probe.prepare(tmp_path.resolve(), probe.CASES[3], monkeypatch, with_validator=True)
    receipt = next((tmp_path / "artifacts/hypothesis_context/returned_results").rglob("*.json"))
    reference = "artifact:" + receipt.relative_to(tmp_path).as_posix()
    data = {
        "status": "done", "reason": "Only a route name is observed", "repair": {"kind": "none"},
        "subject_reviews": [{
            "item_id": "subject", "decision": "retain", "reason": "No behavior observed", "hypotheses": [],
            "no_hypothesis_basis": {
                "kind": "inspected_evidence",
                "inspection_refs": ["artifact:invented" if forged else reference],
                "no_relevant_behavior_reason": "Only a route name; no concrete observed behavior",
            },
        }], "prerequisite_gaps": [], "unresolved_criterion_ids": [],
    }
    if forged:
        with pytest.raises(ValueError):
            validate(data)
    else:
        assert validate(data)["status"] == "done"


def test_provenance_validator_accepts_input_assessment_using_bound_evidence_id(tmp_path, monkeypatch):
    _, schema, _, _, validate = probe.prepare(
        tmp_path.resolve(), probe.CASES[0], monkeypatch, with_validator=True,
    )
    candidate = schema.model_json_schema()["$defs"]["BoundHypothesisBehavior0"]["properties"][
        "input_candidate_ids"
    ]["items"]["const"]
    data = {
        "status": "partial_failure", "reason": "Observed query-to-rendering path supports a conditional test",
        "repair": {"kind": "acceptance"},
        "subject_reviews": [{
            "item_id": "subject", "decision": "revise", "reason": "Propose a conditional test",
            "hypotheses": [], "no_hypothesis_basis": {
                "kind": "inspected_evidence", "inspection_refs": ["e1"],
                "behavior_assessments": [{
                    "input_candidate_ids": [candidate], "observed_behavior": probe.CASES[0][3],
                    "inspection_refs": ["e1"], "supports_conditional_test": True,
                    "reasoning": "A future test can check whether query data is interpreted as HTML",
                }],
            },
        }], "prerequisite_gaps": [], "unresolved_criterion_ids": [],
    }
    assert validate(data)["status"] == "partial_failure"
