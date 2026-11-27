"""Frozen structured production reviews, followed by one preregistered regression repair."""

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import ollama
from dotenv import load_dotenv

BASE = Path(__file__).parent
FIXTURES = Path("tests/fixtures/hypothesis_refutation/cases.json")
ROOT = Path("outputs/technique_review_trials/refutation-review-20261125").resolve()
SPEC = importlib.util.spec_from_file_location(
    "refutation_production", BASE.parent / "identity-corrections-production-20261125/run.py",
)
production = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(production)
save = production.save


def seed(root, cases):
    """Publish generic HTTP, network and filesystem cases into isolated real storage."""
    memory = production.memory
    save(root / "inventory.json", {"schema_version": 1, "unassessed_gaps": [], "items": [
        {"id": case["id"], "kind": case["kind"], "value": case["url"], "target_id": "target-1",
         "attributes": {"source_artifact_refs": [case["source_ref"]], "interaction": case["interaction"]}}
        for case in cases
    ]})
    _, digest = memory._load_inventory_manifest("artifact:inventory.json", persist_normalization=False)
    state = production.workflow.WorkflowStateStore(production.OPERATION)
    plan = production.OperationPlan(
        objective="Review future test discrimination without executing probes", current_phase=1, total_phases=1,
        phases=[production.PlanPhase(id=1, title="Future test review", status="active", produces_hypotheses=True)],
        targets=[production.OperationTarget("target-1", "https://fixture.invalid", "network")],
    )
    state.store_plan(plan)
    for case in cases:
        (root / case["source_ref"].removeprefix("artifact:")).write_text(case["observation"])
        task = production.Task(case["id"], case["title"], case["objective"], phase=1, status="pending", acceptance=(
            production.AcceptanceContract(mode="outcome", basis=production.AcceptanceBasis(
                kind="snapshot", description="Current-operation isolated fixture evidence",
                source_refs=["artifact:inventory.json"], snapshot_hash=digest, item_ids=[case["id"]],
            ), criteria=[production.AcceptanceCriterion(
                id="future-tests", description=case["criterion"],
                evidence_requirements=[production.EvidenceRequirement(kind="hypothesis_set")],
            )])
        ))
        state.store_task(task)

        def persist(binding, uid=task.task_uid):
            state.patch_task(uid, evidence_additions=[binding["artifact_ref"]], recovery_context_updates={
                "hypothesis_publications": [binding],
            })

        writer = production.build_record_hypotheses_tool(task, production.OPERATION, str(root), persist)
        writer(subjects=[{"item_id": case["id"], "disposition": "hypotheses_supplied",
                          "hypotheses": case["hypotheses"]}])


def freeze(root):
    root.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), FIXTURES, FIXTURES.with_name("recorded-http-miss.json"),
               Path(production.__file__), Path(production.recording.__file__),
               Path("src/modules/prompts/security_interpretation.py"),
               Path("src/modules/agents/hypothesis_review.py"),
               Path("src/modules/agents/multi_agent_workflow.py"),
               Path("src/modules/agents/native_structured.py"),
               Path("src/modules/agents/workflow_executor.py"),
               Path("src/modules/tools/hypotheses.py"), Path("src/cyberautoagent.py")]
    (root / "sources").mkdir()
    for index, source in enumerate(sources):
        (root / "sources" / f"{index:02}-{source.name}").write_bytes(source.read_bytes())
    save(root / "frozen.json", {"sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "repetitions": 2, "cases_per_repetition": 6, "request_limit": 96,
        "repair": {"repetition": 1, "task_uid": "fixture-01", "cycles": 2},
        "live_target_tools": [], "expected_labels_visible_to_model": False})
    save(root / "requests.json", {"requests": 0, "limit": 96})


def consumed(root):
    return json.loads((root / "requests.json").read_text())["requests"]


def repair(root):
    """Use existing pending correction state; do not repeat a completed review trial."""
    controller = production.controller_for(root, audit_root=root / "repair")
    task = next(t for t in controller.state.list_tasks() if t.task_uid == "fixture-01")
    before = production.snapshot(controller, task.task_uid)
    if task.status == "done":
        return {"skipped": "Evaluator missed the regression; no selective rerun", "before": before}
    task = controller.state.patch_task(task.task_uid, status="active")
    controller.task_execution_cycles = 2
    plan = controller.state.get_plan()
    controller._run_task(plan, plan.phases[0], task)
    return {"before": before, "after": production.snapshot(controller, task.task_uid)}


def run(root=ROOT):
    cases = json.loads(FIXTURES.read_text())
    freeze(root)
    results = []

    async def counted(client, *args, **kwargs):
        return await production.recording.counted(client, *args, output_root=root, **kwargs)

    with patch.object(ollama.AsyncClient, "chat", counted):
        for repetition in (1, 2):
            work = root / f"repetition-{repetition}"
            work.mkdir()
            production.OPERATION = f"OP_REFUTATION_REVIEW_20261125_R{repetition}"
            with production.environment(work):
                seed(work, cases)
            # Reverse order in the second independent store; no trial is selectively repeated.
            for case in cases if repetition == 1 else reversed(cases):
                start = consumed(root)
                with production.environment(work):
                    try:
                        result = production.perform(work, case["id"], cycles=0)
                    except Exception as error:
                        result = {"task_uid": case["id"], "error": repr(error)}
                result.update(repetition=repetition, expected=case["expected"], requests=consumed(root) - start)
                results.append(result)
                save(root / "results.json", results)
                print(repetition, case["id"], result.get("error") or result["final"]["task"]["status"], flush=True)
        production.OPERATION = "OP_REFUTATION_REVIEW_20261125_R1"
        work = root / "repetition-1"
        (work / "repair").mkdir()
        start = consumed(root)
        with production.environment(work):
            try:
                result = repair(work)
            except Exception as error:
                result = {"error": repr(error)}
        result["requests"] = consumed(root) - start
        save(root / "repair.json", result)
    return results


if __name__ == "__main__":
    load_dotenv(Path.cwd() / ".env")
    production.recording.preflight()
    run()
