"""Controller integration and bounded synthetic pilot verification."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("bound_controller_pilot", Path(__file__).with_name("controller.py"))
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


@pytest.mark.parametrize("case_index", [3, 4])
def test_real_controller_retains_provenance_valid_review(tmp_path, case_index):
    case = pilot.probe.CASES[case_index]

    def runner(role, prompt, tools, system, schema):
        assert role == "task_evaluator"
        assert "Frozen acceptance results:" not in prompt
        data = {
            "status": "partial_failure" if case[1] else "done",
            "reason": "Retained observations assessed",
            "repair": {"kind": "acceptance" if case[1] else "none"},
            "subject_reviews": [{
                "item_id": "subject", "decision": "revise" if case[1] else "retain",
                "reason": "Observed record boundary supports a conditional test" if case[1] else "Only a route name",
                "hypotheses": [], "no_hypothesis_basis": {
                    "kind": "inspected_evidence", "inspection_refs": ["e1"],
                    "no_relevant_behavior_reason": "Source behavior assessed for conditional testing",
                },
            }], "prerequisite_gaps": [], "unresolved_criterion_ids": [],
        }
        return schema.model_validate(data).model_dump()

    result = pilot.evaluate(tmp_path, case, runner)
    assert result["status"] == result["expected"]
    assert result["review"]["subject_reviews"][0]["no_hypothesis_basis"]["inspection_refs"][0].startswith(
        "artifact:artifacts/hypothesis_context/returned_results/",
    )


@pytest.mark.parametrize("limit", [False, True])
def test_pilot_records_real_dispatch_count_resume_and_failures(tmp_path, monkeypatch, limit):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pilot, "load_dotenv", lambda *args: None)
    monkeypatch.setattr(pilot, "create_strands_model", lambda *args: None)
    monkeypatch.setattr(pilot, "require_prompt_token_limit", lambda *args: 48000)
    monkeypatch.setattr(pilot, "Agent", lambda **kwargs: SimpleNamespace(messages=[]))

    async def chat(*args, **kwargs):
        return None

    monkeypatch.setattr(pilot.ollama.AsyncClient, "chat", chat)

    def invoke(*args, **kwargs):
        asyncio.run(pilot.ollama.AsyncClient.chat(None))
        return SimpleNamespace(structured_output=SimpleNamespace(model_dump=dict))

    monkeypatch.setattr(pilot, "invoke_structured", invoke)

    def evaluate(root, case, runner):
        for _ in range(31 if limit else 1):
            runner("task_evaluator", "prompt", [], "system", None)
        return {"status": "done"}

    monkeypatch.setattr(pilot, "evaluate", evaluate)
    pilot.main()
    pilot.main()
    root = Path("outputs/technique_review_trials/evidence-id-controller-20261118")
    assert json.loads((root / "budget.json").read_text())["requests"] == (30 if limit else 2)
    result = json.loads((root / "network-access.json").read_text())
    assert ("error" in result) is limit
