"""Offline lifecycle and accounting checks; scripted verdicts do not assess model semantics."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("clean_check", BASE / "run.py")
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def parent_ledger(tmp_path):
    parent = tmp_path / "study"
    parent.mkdir()
    check.save(parent / "requests.json", {"requests": 38, "limit": 96})
    check.save(parent / "results.json", [{"preserved": "Earlier model results"}])
    return parent


def test_freeze_preserves_original_records_and_remaining_cap(tmp_path):
    parent = parent_ledger(tmp_path)
    before = (parent / "requests.json").read_bytes()
    root = check.freeze(parent)
    assert json.loads((root / "requests.json").read_text()) == {"requests": 0, "limit": 58}
    assert (parent / "requests.json").read_bytes() == before
    frozen = json.loads((root / "frozen.json").read_text())
    assert frozen["prior_requests"] == 38 and frozen["live_target_tools"] == []
    with pytest.raises(FileExistsError):
        check.freeze(parent)
    check.save(parent / "requests.json", {"requests": 96, "limit": 96})
    with pytest.raises(ValueError, match="exhausted"):
        check.freeze(parent)


@pytest.mark.parametrize("failing", [False, True])
def test_run_preserves_failed_trials_and_reopens_same_operation(tmp_path, monkeypatch, failing):
    parent = parent_ledger(tmp_path)
    seen = []

    def perform(root, uid, cycles):
        seen.append((uid, cycles))
        if failing and uid == "check-01":
            raise ValueError("Retained fixture failure")
        return {"task_uid": uid, "final": {"task": {"status": "done"}}}

    monkeypatch.setattr(check.production, "perform", perform)
    result = check.run(parent)
    assert seen == [("check-02", 2), ("check-01", 2)]
    assert ("error" in result[-1]) is failing
    assert json.loads((parent / "results.json").read_text()) == [{"preserved": "Earlier model results"}]
    root = parent / "clean-check"
    reopened = json.loads((root / "reopened.json").read_text())
    assert set(reopened) == {"check-01", "check-02"}
    if failing:
        assert reopened["check-01"]["task"]["status"] == "partial_failure"


def test_actual_perform_activates_task_and_real_acceptance_succeeds(tmp_path, monkeypatch):
    production = check.production
    production.OPERATION = check.OPERATION
    cases = json.loads((BASE / "fixtures.json").read_text())
    with production.environment(tmp_path):
        check.seed_replay.seed(tmp_path, cases)
        controller = production.controller_for(tmp_path)
        controller.state.patch_task("check-01", status="partial_failure")
        monkeypatch.setattr(production, "controller_for", lambda *_args, **_kwargs: controller)
        monkeypatch.setattr(controller, "_evaluate_task", lambda *_: SimpleNamespace(
            status="partial_failure", repair_kind="acceptance", reason="Scripted policy-attribution correction",
        ))
        # perform serializes the real evaluation dataclass into the retained history.
        monkeypatch.setattr(production, "asdict", lambda value: vars(value))
        results = []

        def run(_plan, _phase, task):
            assert task.status == "active"
            tool = production.memory.build_record_task_acceptance_tool(task.task_uid, task)
            results.append(json.loads(tool(status="satisfied", disposition="observation",
                summary="Fixture availability only; semantic validity is assessed separately", evidence_refs=[])))
            controller.state.mark_task(task, "done", "Scripted lifecycle result")

        monkeypatch.setattr(controller, "_run_task", run)
        result = production.perform(tmp_path, "check-01", cycles=2)
        assert result["final"]["task"]["status"] == "done"
        assert len(results) == 1 and results[0]["complete"]


@pytest.mark.parametrize("domain", ["http", "tcp", "filesystem"])
def test_timeout_attribution_guidance_reaches_assembled_generic_reviews(tmp_path, monkeypatch, domain):
    production = check.production
    production.OPERATION = check.OPERATION
    cases = json.loads(Path("tests/fixtures/hypothesis_refutation/cases.json").read_text())
    case = cases[{"http": 0, "tcp": 2, "filesystem": 4}[domain]]
    case["hypotheses"][0]["negative_expectations"] = [
        "A timeout/reset/I/O error establishes that the configured policy denied the probe",
    ]
    with production.environment(tmp_path):
        check.seed_replay.seed(tmp_path, [case])
        controller = production.controller_for(tmp_path)
        task = controller.state.list_tasks()[0]
        plan = controller.state.get_plan()
        prompt = controller._task_evaluator_system_prompt() + controller._task_evaluator_prompt(
            plan, plan.phases[0], task,
        )
        assert "A timeout, reset or I/O error alone is inconclusive about policy enforcement" in prompt
        assert "do not label those failures policy-compliant denial" in prompt
        assert "not a requirement to execute future tests" in prompt
        assert "relevant enforcement boundary" in prompt
