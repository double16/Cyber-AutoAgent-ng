"""Exact pilot subjects traverse the actual SDK without provider or semantic calls."""

import json
from pathlib import Path

import pytest

from modules.techniques.calibration_budget import CalibrationBudget
from modules.techniques.generation_pilot_offline import GenerationContractModel, run_offline_generation

CASES = json.loads(Path("docs/research/technique-generation-yield-pilot/cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
@pytest.mark.parametrize("mode", ["native", "tool"])
def test_exact_generation_case_through_sdk(case, mode, tmp_path):
    from modules.techniques import roles

    original = roles.create_strands_model
    result = run_offline_generation(case, tmp_path / "case", mode, CalibrationBudget(tmp_path / "budget.json"))
    assert result["status"] == "offline_contract_passed", result
    assert result["scripted_model_dispatches"] > 0
    assert result["counts"]["requests"] == 0
    assert result["semantic_judgment"] == "not_performed"
    assert json.loads((tmp_path / "case/outcome.json").read_text())["status"] == "offline_contract_passed"
    assert roles.create_strands_model is original


def test_scripted_worker_rejects_unexpected_roles_and_requirement_shape():
    model = GenerationContractModel(CASES[0], "native")
    with pytest.raises(ValueError, match="Unexpected"):
        model.payload({})
    model.role = "technique_requirement_assessor"
    model.case = {**CASES[0], "requirements": []}
    with pytest.raises(ValueError, match="one declared requirement"):
        model.payload({})


def test_scripted_alignment_payloads_preserve_declared_request():
    model = GenerationContractModel(CASES[0], "native")
    model.role = "technique_request_aligner"
    assert model.payload({})["adjusted_request"] == CASES[0]["request"]
    model.role = "technique_request_reviewer"
    assert model.payload({})["subject_preserved"]
    model.role = "technique_judge"
    assert model.payload({})["selected_id"] is None
