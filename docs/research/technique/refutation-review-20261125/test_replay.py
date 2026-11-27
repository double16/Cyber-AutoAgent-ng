"""Verify real persistence, assembled review inputs and the frozen replay lifecycle offline."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

BASE = Path(__file__).parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


replay = load("refutation_replay", BASE / "replay.py")
transport = load("refutation_transport", BASE.parent / "identity-corrections-production-20261125/test_run.py")
cases = json.loads(replay.FIXTURES.read_text())


def test_exact_incident_and_minimal_pairs():
    recorded = json.loads(replay.FIXTURES.with_name("recorded-http-miss.json").read_text())
    assert cases[0]["hypotheses"] == recorded["hypotheses"]
    for missing, complete in zip(cases[::2], cases[1::2], strict=True):
        left, right = deepcopy(missing["hypotheses"][0]), deepcopy(complete["hypotheses"][0])
        assert right["negative_expectations"][:-1] == left["negative_expectations"]
        left.pop("negative_expectations")
        right.pop("negative_expectations")
        assert left == right


@pytest.mark.parametrize("case", cases, ids=lambda case: case["id"])
def test_structured_review_persists_semantic_verdict_without_keyword_gate(tmp_path, monkeypatch, case):
    production = replay.production
    with production.environment(tmp_path):
        replay.seed(tmp_path, [case])
        controller = production.controller_for(tmp_path)
        task = controller.state.patch_task(case["id"], status="active")
        before = production.snapshot(controller, task.task_uid)
        identifier = before["publication"]["subjects"][0]["hypotheses"][0]["hypothesis_id"]
        checks = {name: {"verdict": "adequate", "reason": "Scripted transport verdict"}
                  for name in ("grounding", "test_discrimination", "identity_requirements")}
        checks["test_discrimination"]["verdict"] = "revise" if case["expected"] == "revise" else "adequate"
        verdict = case["expected"]
        payload = {"status": "partial_failure" if verdict == "revise" else "done",
                   "repair": {"kind": "acceptance" if verdict == "revise" else "none"},
                   "subject_reviews": [{"item_id": task.task_uid, "decision": verdict, "reason": "Scripted",
                       "hypotheses": [{"hypothesis_id": identifier, "decision": verdict, "reason": "Scripted",
                                       "semantic_checks": checks}]}],
                   "prerequisite_gaps": [], "unresolved_criterion_ids": []}
        monkeypatch.setattr(production.agents, "create_strands_model", lambda *_: transport.LocalModel(payload))
        monkeypatch.setattr(production.agents, "get_capabilities", lambda *_: None)
        monkeypatch.setattr(controller, "_task_evaluator_tools", lambda *_: [])
        plan = controller.state.get_plan()
        prompt = controller._task_evaluator_system_prompt() + controller._task_evaluator_prompt(
            plan, plan.phases[0], task,
        )
        assert "observation of the same probe that would refute it" in prompt
        assert "unrelated invalid-input control cannot substitute" in prompt
        assert "mark test_discrimination\nrevise" in prompt
        assert "expected" not in json.dumps(task.to_dict())
        assert "no particular wording or field placement" in prompt
        decision = controller._evaluate_task(plan, plan.phases[0], task)
        assert decision.status == payload["status"]
        after = production.snapshot(controller, task.task_uid)
        assert after["binding"]["sha256"] == before["binding"]["sha256"]
        review = after["task"]["recovery_context"]["hypothesis_review"]
        assert review["publication_sha256"] == before["binding"]["sha256"]
        assert review["subject_reviews"][0]["hypotheses"][0]["decision"] == verdict


def test_matching_refutation_may_be_in_steps_without_a_new_required_field(tmp_path):
    case = deepcopy(cases[-1])
    proposal = case["hypotheses"][0]
    proposal["safe_test_steps"].append(proposal["negative_expectations"].pop())
    with replay.production.environment(tmp_path):
        replay.seed(tmp_path, [case])
        controller = replay.production.controller_for(tmp_path)
        after = replay.production.snapshot(controller, case["id"])
        retained = after["publication"]["subjects"][0]["hypotheses"][0]
        assert retained["safe_test_steps"] == proposal["safe_test_steps"]
        assert retained["negative_expectations"] == proposal["negative_expectations"]


@pytest.mark.parametrize("failing", [False, True])
def test_frozen_lifecycle_counts_failures_and_repairs(tmp_path, monkeypatch, failing):
    root = tmp_path / "replay"
    seen = []

    def perform(work, uid, cycles):
        assert cycles == 0
        seen.append((work.name, uid))
        if failing and uid == "fixture-03":
            raise ValueError("Retained fixture failure")
        return {"task_uid": uid, "final": {"task": {"status": "done"}}}

    monkeypatch.setattr(replay.production, "perform", perform)
    monkeypatch.setattr(replay, "repair", lambda _: (_ for _ in ()).throw(ValueError("Repair failure"))
                        if failing else {"after": {"task": {"status": "done"}}})
    result = replay.run(root)
    assert len(seen) == len(result) == 12
    assert seen[:6] == [("repetition-1", case["id"]) for case in cases]
    assert seen[6:] == [("repetition-2", case["id"]) for case in reversed(cases)]
    assert sum("error" in row for row in result) == (2 if failing else 0)
    assert ("error" in json.loads((root / "repair.json").read_text())) is failing
    assert json.loads((root / "requests.json").read_text())["limit"] == 96
    with pytest.raises(FileExistsError):
        replay.freeze(root)


@pytest.mark.parametrize("completed", [False, True])
def test_only_preregistered_pending_task_is_repaired(tmp_path, monkeypatch, completed):
    with replay.production.environment(tmp_path):
        replay.seed(tmp_path, cases)
        controller = replay.production.controller_for(tmp_path)
        controller.state.patch_task("fixture-01", status="done" if completed else "partial_failure")
        monkeypatch.setattr(replay.production, "controller_for", lambda *_, **__: controller)
        calls = []
        monkeypatch.setattr(controller, "_run_task", lambda *_: calls.append(True))
        result = replay.repair(tmp_path)
        assert bool(calls) is not completed
        assert ("skipped" in result) is completed


def test_repair_activates_task_before_real_acceptance(tmp_path, monkeypatch):
    production = replay.production
    with production.environment(tmp_path):
        replay.seed(tmp_path, cases)
        controller = production.controller_for(tmp_path)
        task = controller.state.patch_task("fixture-01", status="partial_failure")
        record = production.memory.build_record_task_acceptance_tool(task.task_uid, task)
        payload = {"status": "satisfied", "disposition": "observation",
                   "summary": "Fixture publication is available; semantics are tested separately", "evidence_refs": []}
        with pytest.raises(ValueError, match="only for the active task"):
            record(**payload)
        monkeypatch.setattr(production, "controller_for", lambda *_, **__: controller)
        calls = []

        def run(_plan, _phase, current):
            assert current.status == "active"
            active = next(t for t in controller.state.list_tasks() if t.task_uid == current.task_uid)
            assert active.status == "active"
            calls.append(json.loads(record(**payload)))

        monkeypatch.setattr(controller, "_run_task", run)
        replay.repair(tmp_path)
        assert len(calls) == 1 and calls[0]["complete"]
