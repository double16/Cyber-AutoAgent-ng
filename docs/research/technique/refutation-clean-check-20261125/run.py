"""Bounded clean acceptance check using the remaining original generation allowance."""

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import ollama
from dotenv import load_dotenv

BASE = Path(__file__).parent
PARENT = Path("outputs/technique_review_trials/refutation-review-20261125").resolve()
SPEC = importlib.util.spec_from_file_location("clean_seed", BASE.parent / "refutation-review-20261125/replay.py")
seed_replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed_replay)
production = seed_replay.production
save = production.save
OPERATION = "OP_REFUTATION_CLEAN_20261125"


def freeze(parent):
    prior = json.loads((parent / "requests.json").read_text())
    if prior["requests"] >= prior["limit"]:
        raise ValueError("Original request allowance exhausted")
    root = parent / "clean-check"
    root.mkdir(exist_ok=False)
    sources = [Path(__file__), BASE / "fixtures.json", Path(seed_replay.__file__), Path(production.__file__),
               Path(production.recording.__file__), Path("src/modules/prompts/security_interpretation.py"),
               Path("src/modules/agents/hypothesis_review.py"), Path("src/modules/agents/multi_agent_workflow.py"),
               Path("src/modules/agents/workflow_executor.py"), Path("src/modules/tools/hypotheses.py")]
    (root / "sources").mkdir()
    for index, source in enumerate(sources):
        (root / "sources" / f"{index:02}-{source.name}").write_bytes(source.read_bytes())
    save(root / "frozen.json", {"sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "prior_requests": prior["requests"], "overall_limit": prior["limit"],
        "operation_id": OPERATION, "order": ["check-02", "check-01"], "repair_cycles": 2,
        "prior_record_hashes": {name: hashlib.sha256((parent / name).read_bytes()).hexdigest()
                                for name in ("requests.json", "results.json", "repair.json", "frozen.json")
                                if (parent / name).exists()}, "live_target_tools": []})
    save(root / "requests.json", {"requests": 0, "limit": prior["limit"] - prior["requests"]})
    return root


def run(parent=PARENT):
    root = freeze(parent)
    production.OPERATION = OPERATION
    cases = json.loads((BASE / "fixtures.json").read_text())
    with production.environment(root):
        seed_replay.seed(root, cases)
    results = []

    async def counted(client, *args, **kwargs):
        return await production.recording.counted(client, *args, output_root=root, **kwargs)

    with patch.object(ollama.AsyncClient, "chat", counted):
        for uid in ("check-02", "check-01"):
            before = seed_replay.consumed(root)
            with production.environment(root):
                try:
                    # perform activates before evaluation/repair, matching the ordinary dispatcher.
                    result = production.perform(root, uid, cycles=2)
                except Exception as error:
                    state = production.workflow.WorkflowStateStore(OPERATION)
                    task = next(t for t in state.list_tasks() if t.task_uid == uid)
                    state.mark_task(task, "partial_failure", f"Clean fixture failed: {type(error).__name__}")
                    result = {"task_uid": uid, "error": repr(error)}
            result["requests"] = seed_replay.consumed(root) - before
            results.append(result)
            save(root / "results.json", results)
            print(uid, result.get("error") or result["final"]["task"]["status"], flush=True)
    with production.environment(root):
        controller = production.controller_for(root)
        save(root / "reopened.json", {t.task_uid: production.snapshot(controller, t.task_uid)
                                      for t in controller.state.list_tasks()})
    return results


if __name__ == "__main__":
    load_dotenv(Path.cwd() / ".env")
    production.recording.preflight()
    run()
