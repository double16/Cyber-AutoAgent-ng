"""Continue the same isolated fixture stores after the observed runtime-boundary repair."""

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import ollama

BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("production_resume", BASE / "run.py")
integration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(integration)


def continue_operation(root=integration.ROOT):
    prior = json.loads((root / "requests.json").read_text())["requests"]
    audit = root / "continuation"
    audit.mkdir(exist_ok=False)
    sources = [Path(__file__), BASE / "run.py", BASE / "fixtures.json",
               Path(integration.memory.__file__).with_name("hypotheses.py"),
               Path(integration.agents.__file__).with_name("workflow_executor.py"),
               Path(integration.workflow.__file__)]
    (audit / "sources").mkdir()
    for index, source in enumerate(sources):
        (audit / "sources" / f"{index:02}-{source.name}").write_bytes(source.read_bytes())
    integration.save(audit / "frozen.json", {"sha256": {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources
    }, "prior_requests": prior, "overall_limit": 96, "remaining_limit": 96 - prior,
       "same_operation_id": integration.OPERATION, "reseeded": False})
    integration.save(audit / "requests.json", {"requests": 0, "limit": 96 - prior})
    with sqlite3.connect(str(root / "cyber_autoagent.db")) as source:
        with sqlite3.connect(str(audit / "state-before.db")) as destination:
            source.backup(destination)
    for name in ("results.json", "initial.json", "frozen.json", "stopped-for-runtime-contract.json"):
        if (root / name).exists():
            (audit / f"prior-{name}").write_bytes((root / name).read_bytes())
    results = []

    async def counted(client, *args, **kwargs):
        return await integration.recording.counted(client, *args, output_root=audit, **kwargs)

    with integration.environment(root):
        state = integration.workflow.WorkflowStateStore(integration.OPERATION)
        for task in state.list_tasks():
            if task.status == "active":
                state.defer_task(task, "Interrupted isolated fixture before runtime contract repair")
        task_ids = [task.task_uid for task in state.list_tasks() if task.status != "done"]
        controller = integration.controller_for(root, audit_root=audit)
        integration.save(audit / "before.json", {uid: integration.snapshot(controller, uid) for uid in task_ids})
    with patch.object(ollama.AsyncClient, "chat", counted):
        for uid in task_ids:
            with integration.environment(root):
                try:
                    result = integration.perform(root, uid, audit_root=audit)
                except Exception as error:
                    state = integration.workflow.WorkflowStateStore(integration.OPERATION)
                    task = next(item for item in state.list_tasks() if item.task_uid == uid)
                    state.mark_task(task, "partial_failure", f"Isolated continuation failed: {type(error).__name__}")
                    result = {"task_uid": uid, "error": repr(error)}
                results.append(result)
                integration.save(audit / "results.json", results)
                print(uid, result.get("error") or result["final"]["task"]["status"], flush=True)
    with integration.environment(root):
        controller = integration.controller_for(root, audit_root=audit)
        integration.save(audit / "after.json", {task.task_uid: integration.snapshot(controller, task.task_uid)
                                                for task in controller.state.list_tasks()})
    return results


if __name__ == "__main__":
    integration.recording.preflight()
    continue_operation()
