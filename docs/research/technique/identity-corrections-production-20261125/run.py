"""Isolated continuation through production agents, tool execution, acceptance and semantic review."""

import hashlib
import importlib.util
import json
import logging
import os
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from pathlib import Path

import ollama
from dotenv import load_dotenv
from strands.tools.executors import SequentialToolExecutor

from modules import prompts
from modules.agents import cyber_autoagent as agents
from modules.agents import multi_agent_workflow as workflow
from modules.agents.workflow_executor import workflow_executor_session
from modules.config import AgentConfig, get_config_manager
from modules.config.types import BudgetConfig
from modules.handlers.conversation_budget import MappingConversationManager
from modules.handlers.react import AgentEventHandler
from modules.tools import artifact, memory
from modules.tools.hypotheses import build_record_hypotheses_tool, hypothesis_binding, load_hypothesis_set
from modules.tools.memory import (
    AcceptanceBasis,
    AcceptanceContract,
    AcceptanceCriterion,
    EvidenceRequirement,
    OperationPlan,
    OperationTarget,
    PlanPhase,
    Task,
)

BASE = Path(__file__).parent
ROOT = Path("outputs/technique_review_trials/identity-corrections-production-20261125").resolve()
OPERATION = "OP_FIXTURE_INTEGRATION_20261125"
MODEL = "muse-glimmer:30b-mlx"
BUDGET = BudgetConfig(max_duration_minutes=60)
SPEC = importlib.util.spec_from_file_location(
    "recording", BASE.parent / "identity-corrections-replicated-20261125/replay.py",
)
recording = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recording)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, default=str))


class FixtureEmbeddings:
    """Deterministic vector-store fixture; no remote embeddings or semantic decisions."""

    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, _text):
        return [1.0, 0.0, 0.0, 0.0]


class FixtureEmitter:
    def __init__(self, root):
        self.path = root / "events.jsonl"

    def emit(self, event):
        with self.path.open("a") as stream:
            stream.write(json.dumps(event, default=str) + "\n")


@contextmanager
def environment(root):
    with ExitStack() as stack:
        stack.enter_context(patch_environment(root))
        client = memory.QdrantMemoryClient({
            "operation_id": OPERATION, "target_values": ["https://fixture.invalid"],
            "target_name": "fixture.integration", "logical_target": "fixture.integration",
            "output_dir": str(root), "embeddings": FixtureEmbeddings(), "embedding_dimensions": 4,
        }, silent=True)
        from unittest.mock import patch

        stack.enter_context(patch.object(memory, "_MEMORY_CLIENT", client))
        stack.enter_context(patch.object(memory, "_MEMORY_CONFIG", client.config))
        stack.enter_context(patch.object(memory, "_DATABASE_STORE", None))
        for owner in (artifact, memory, workflow):
            stack.enter_context(patch.object(owner, "_operation_output_root", lambda: str(root)))
        try:
            yield client
        finally:
            client.close()


@contextmanager
def patch_environment(root):
    from unittest.mock import patch

    with patch.dict(os.environ, {"CYBER_OPERATION_ID": OPERATION, "CYBER_AGENT_OUTPUT_DIR": str(root),
                                 "QDRANT_URL": "", "ENABLE_OBSERVABILITY": "false"}):
        yield


def runtime(root):
    manager = get_config_manager()
    config = AgentConfig(target="https://fixture.invalid", objective=(
        "Correct retained future-test proposals using only current-operation fixture artifacts. "
        "Do not contact targets, execute proposed tests, provision credentials or use live browser/network tools."
    ), provider="ollama", model_id=MODEL, op_id=OPERATION, module="", available_tools=[], budget=BUDGET)
    callback = AgentEventHandler(operation_id=OPERATION, provider_id="ollama", model_id=MODEL,
                                 emitter=FixtureEmitter(root), emit_operation_init=False, start_metrics_thread=False)
    system = prompts.get_system_prompt(config.target, config.objective, OPERATION, budget=BUDGET, provider="ollama")
    tools = [artifact.create_artifact_reader(40000), artifact.create_artifact_searcher()]
    return agents.AgentRuntimeResources(
        config=config, operation_id=OPERATION, config_manager=manager,
        server_config=manager.get_server_config("ollama", model_id=MODEL), callback_handler=callback,
        tools_list=tools, core_tools_list=tools, optional_tools_list=[], tool_executor=SequentialToolExecutor(),
        system_prompt=system, system_prompt_payload=system, hooks=[],
        conversation_manager=MappingConversationManager(), sdk_context_manager=None,
        trace_attributes={"operation.id": OPERATION}, prompt_token_limit=40000,
    )


def controller_for(root, audit_root=None):
    audit_root = audit_root or root
    active_runtime = runtime(root)
    controller = workflow.MultiAgentWorkflowController(runtime=active_runtime, budget=BUDGET)

    @contextmanager
    def session(role, tools, system):
        import cyberautoagent as cli

        def invoke(agent, prompt, policy):
            callback = agent._cyber_callback_handler
            journal = callback.tool_outcome_journal
            message_start = len(agent.messages)
            try:
                result = cli.run_workflow_agent_with_max_token_recovery(
                    agent=agent, prompt=prompt, run_policy=policy, callback_handler=active_runtime.callback_handler,
                    initial_prompt=prompt, budget_cfg=BUDGET, operation_start=time.time(), max_duration=None,
                    logger=logging.getLogger("fixture.production"),
                )
                text = cli.extract_last_assistant_text(list(agent.messages)[message_start:])
                if not text and not (policy and result.reason == policy.terminal_reason):
                    text = result.message or result.reason
                return result, text
            finally:
                save(audit_root / f"executor-{len(list(audit_root.glob('executor-*.json'))):03}.json", {
                    "role": role, "prompt": prompt, "system": system,
                    "policy": asdict(policy) if policy else None,
                    "tools": [workflow.get_tool_spec(item) for item in tools], "messages": agent.messages,
                    "outcomes": [asdict(item) for item in journal.entries()],
                })

        with workflow_executor_session(
            runtime=active_runtime, role=role, tools=tools, system_prompt=system,
            create_agent=agents.create_agent, invoke=invoke,
            recovery_guidance=lambda hook: cli._recovery_guidance_with_failed_command_help(hook, []),
            logger=logging.getLogger("fixture.production"),
        ) as runner:
            yield runner

    def work(role, prompt, tools, system, policy=None):
        with session(role, tools, system) as runner:
            return runner(prompt, policy)

    controller.work_runner = work
    controller.executor_session_factory = session

    return controller


def seed(root):
    cases = json.loads((BASE / "fixtures.json").read_text())
    items = [{"id": case["id"], "kind": "endpoint", "target_id": "target-1", "value": case["url"],
              "attributes": {"source_artifact_refs": [case["source_ref"]], "interaction": case["interaction"]}}
             for case in cases]
    save(root / "inventory.json", {"schema_version": 1, "items": items, "unassessed_gaps": []})
    _, digest = memory._load_inventory_manifest("artifact:inventory.json", persist_normalization=False)
    state = workflow.WorkflowStateStore(OPERATION)
    plan = OperationPlan(objective="Repair fixture proposals without target execution", current_phase=1, total_phases=1,
                         phases=[PlanPhase(id=1, title="Future-test correction", status="active", produces_hypotheses=True)],
                         targets=[OperationTarget("target-1", "https://fixture.invalid", "network")])
    state.store_plan(plan)
    for case in cases:
        (root / case["source_ref"].removeprefix("artifact:")).write_text(case["observation"])
        task = Task(case["id"], case["title"], case["objective"], phase=1, status="pending", acceptance=(
            AcceptanceContract(mode="outcome", basis=AcceptanceBasis(
                kind="snapshot", description="Current-operation assigned fixture evidence",
                source_refs=["artifact:inventory.json"], snapshot_hash=digest, item_ids=[case["id"]],
            ), criteria=[AcceptanceCriterion(id="future-tests", description=case["criterion"],
                                            evidence_requirements=[EvidenceRequirement(kind="hypothesis_set")])])
        ))
        state.store_task(task)

        def persist(binding, uid=task.task_uid):
            current = next(item for item in state.list_tasks() if item.task_uid == uid)
            state.patch_task(uid, evidence_additions=[binding["artifact_ref"]], recovery_context_updates={
                "hypothesis_publications": [*current.recovery_context.get("hypothesis_publications", []), binding],
            })

        writer = build_record_hypotheses_tool(task, OPERATION, str(root), persist)
        writer(subjects=[{"item_id": case["id"], "disposition": "hypotheses_supplied",
                          "hypotheses": case["hypotheses"]}])
    return [case["id"] for case in cases]


def snapshot(controller, task_uid):
    task = next(item for item in controller.state.list_tasks() if item.task_uid == task_uid)
    binding = hypothesis_binding(task)
    return {"task": task.to_dict(), "binding": binding,
            "publication": load_hypothesis_set(binding["artifact_ref"], task, OPERATION).model_dump(),
            "acceptance": [result.to_dict() for result in controller.state.list_task_acceptance_results(task_uid)]}


def perform(root, task_uid, cycles=2, audit_root=None):
    controller = controller_for(root, audit_root=audit_root) if audit_root else controller_for(root)
    audit_root = audit_root or root
    state = controller.state
    task = state.patch_task(task_uid, status="active")
    plan = state.get_plan()
    phase = plan.phases[0]
    history = []
    try:
        if not state.list_task_acceptance_results(task_uid):
            state.record_task_acceptance(task, {"status": "satisfied", "disposition": "observation",
                                               "summary": "Seed publication available; semantic review pending",
                                               "evidence_refs": []})
        decision = controller._evaluate_task(plan, phase, task)
        history.append({"stage": "semantic_review", "decision": asdict(decision),
                        "state": snapshot(controller, task_uid)})
        save(audit_root / f"{task_uid}-history.json", history)
        if decision.status == "done":
            state.mark_task(task, "done", decision.reason)
        elif cycles and decision.repair_kind in {"acceptance", "execution"}:
            controller.task_execution_cycles = cycles
            current = next(item for item in state.list_tasks() if item.task_uid == task_uid)
            # Use the complete production task entry: prompt building/review, execution, receipts and evaluation.
            controller._run_task(plan, phase, current)
            history.append({"stage": "production_continuation", "state": snapshot(controller, task_uid)})
            save(audit_root / f"{task_uid}-history.json", history)
        else:
            state.mark_task(task, decision.status, decision.reason)
        return {"task_uid": task_uid, "history": history, "final": snapshot(controller, task_uid)}
    finally:
        callback = controller.runtime.callback_handler
        stop = getattr(callback, "stop", None)
        if callable(stop):
            stop()


def freeze(root):
    root.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), BASE / "fixtures.json", Path(agents.__file__), Path(workflow.__file__),
               Path(memory.__file__)]
    for name in ("hypothesis_review.py", "hypothesis_context.py", "native_structured.py"):
        sources.append(Path(workflow.__file__).with_name(name))
    import cyberautoagent as cli

    sources.append(Path(cli.__file__))
    sources.append(Path(agents.__file__).with_name("workflow_executor.py"))
    sources.append(Path(recording.__file__))
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    (root / "sources").mkdir()
    for index, path in enumerate(sources):
        (root / "sources" / f"{index:02}-{path.name}").write_bytes(path.read_bytes())
    save(root / "frozen.json", {"sha256": hashes, "operation_id": OPERATION, "request_limit": 96,
                                "max_review_repair_cycles": 2, "live_target_tools": []})
    save(root / "requests.json", {"requests": 0, "limit": 96})


def main():
    load_dotenv(Path.cwd() / ".env")
    recording.preflight()
    freeze(ROOT)
    from unittest.mock import patch

    async def counted(client, *args, **kwargs):
        return await recording.counted(client, *args, output_root=ROOT, **kwargs)

    with environment(ROOT):
        task_ids = seed(ROOT)
        save(ROOT / "initial.json", {uid: snapshot(controller_for(ROOT), uid) for uid in task_ids})
    # A new client/controller opens the existing stores for every continuation task.
    results = []
    with patch.object(ollama.AsyncClient, "chat", counted):
        for uid in task_ids:
            with environment(ROOT):
                try:
                    result = perform(ROOT, uid)
                except Exception as error:
                    result = {"task_uid": uid, "error": repr(error)}
                results.append(result)
                save(ROOT / "results.json", results)
                print(uid, result.get("error") or result["final"]["task"]["status"], flush=True)


if __name__ == "__main__":
    main()
