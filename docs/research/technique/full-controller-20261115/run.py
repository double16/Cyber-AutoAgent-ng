"""Isolated controller/evaluator and real catalog path; synthetic endpoint tools only."""

import importlib.util
import json
import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


def parent():
    path = Path("docs/research/technique/production-executor-20261115/run.py")
    spec = importlib.util.spec_from_file_location("executor_core", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_case(folder, mode, arm, offline=False):
    def prepare(original):
        objective = (
            "Compare each session's own-account and cross-account access against the observed "
            "permission policy. Publish an evidence-backed classification for independent review."
        )
        basis = replace(original.acceptance.basis, procedure={
            "methods": ["Own-account baseline and cross-account comparison for both sessions"],
            "limits": {"max_requests": 8},
            "stop_condition": "first_limit_reached",
            "gap_policy": "record_unassessed",
            "output_kind": "artifact",
        })
        acceptance = replace(original.acceptance, basis=basis, criteria=tuple(
            replace(c, description=objective) for c in original.acceptance.criteria
        ), manifest_hash="")
        return replace(original, objective=objective, acceptance=acceptance)

    def full(scope):
        from cyberautoagent import run_agent_until_terminal_state
        from modules.agents import multi_agent_workflow as workflow
        from modules.agents.cyber_autoagent import create_agent
        from modules.storage.qdrant import build_embeddings
        from modules.techniques.cli import build_service
        from modules.tools import memory
        from modules.tools.memory import OperationTarget, QdrantMemoryClient

        stack = scope["stack"]
        root = scope["folder"]
        op = scope["op_id"]
        runtime = scope["runtime"]
        store = scope["store"]
        fixture_path = root / "fixture.json"
        fixture_path.write_text(json.dumps({
            "kind": "account comparison input",
            "protocol": "HTTP",
            "target": "https://fixture.invalid",
            "sessions": ["session-red", "session-blue"],
            "selectors": ["A", "B"],
            "operation": {"tool": "request_account", "inputs": ["principal", "selector"],
                          "method": "GET", "path": "/account", "selector_header": "X-Account"},
            "context": {"tool": "inspect_context", "kinds": ["identity", "policy"]},
            "publication": {"tool": "publish_result"},
        }))
        from strands import tool

        @tool
        def read_fixture() -> str:
            """Read the assigned fixture file containing account comparison inputs and tool interfaces."""
            return fixture_path.read_text()
        read_fixture._cyber_technique_observation_origin = "source"
        target = OperationTarget("target-1", "https://fixture.invalid", "network")
        task = replace(scope["task"], target_ids=["target-1"])
        phase = replace(scope["plan"].phases[0], technique_contexts=("vulnerability_verification",))
        plan = replace(scope["plan"], targets=[target], phases=[phase])
        runtime.config.target = target.value
        from modules.prompts.factory import get_system_prompt

        runtime.system_prompt = get_system_prompt(
            target.value,
            task.objective,
            op,
            provider="ollama",
            plan_snapshot=plan,
            tools_context="Only registered isolated fixture tools are available; no external target I/O.",
            output_config={"operation_path": str(root), "artifacts_path": str(root / "artifacts")},
        )
        runtime.system_prompt_payload = runtime.system_prompt
        stack.enter_context(patch.dict(os.environ, {"CYBER_AGENT_OUTPUT_DIR": str(root), "QDRANT_URL": ""}))
        config = runtime.config_manager.get_qdrant_memory_config("ollama")
        if offline:

            class Embeddings:
                def embed_query(self, text):
                    return [1.0, 0.0]

            config.update(embeddings=Embeddings(), embedding_dimensions=2)
        else:
            config["embeddings"] = build_embeddings(config)
        config.update(output_dir=str(root), operation_id=op, target_values=[target.value])
        client = QdrantMemoryClient(config, silent=True)
        stack.callback(client.close)
        stack.enter_context(patch.object(memory, "_MEMORY_CLIENT", client))
        stack.enter_context(patch.object(memory, "_MEMORY_CONFIG", config))
        stack.enter_context(patch.object(memory, "_ensure_memory_client", lambda: client))
        stack.enter_context(patch.object(workflow, "get_memory_client", lambda **kwargs: client))
        stack.enter_context(patch.object(workflow, "_get_database_store", lambda: store))
        stack.enter_context(patch.object(workflow, "_operation_output_root", lambda: str(root)))
        stack.enter_context(patch.object(memory, "_operation_id", lambda operation_id=None: operation_id or op))
        store.store_plan(op, plan)
        store.store_task(op, task)
        runtime.core_tools_list = [
            t for t in scope["tools"] if t.tool_spec["name"] not in {"get_technique", "record_task_acceptance"}
        ]
        from modules.tools.artifact import create_artifact_reader

        reader = create_artifact_reader(runtime.prompt_token_limit)
        reader._cyber_technique_observation_origin = "source"
        runtime.core_tools_list.extend([read_fixture, reader])
        runtime.optional_tools_list = []
        if arm == "lookup" and not offline:
            # Separate physical catalog backend avoids shared embedded-client lock conflicts.
            with patch.dict(os.environ, {"CYBER_AGENT_OUTPUT_DIR": str(root / "catalog")}):
                runtime.technique_service = build_service(
                    "ollama", runtime.config.model_id, str(root / "catalog"), "web"
                )
            stack.callback(runtime.technique_service.store.qdrant.close)

        def worker(role, prompt, tools, system, policy=None):
            agent = create_agent(
                runtime.config.target,
                task.objective,
                runtime_resources=runtime,
                system_prompt=system,
                tools=tools,
                agent_type=role,
            )
            try:
                result = run_agent_until_terminal_state(
                    agent=agent,
                    callback_handler=runtime.callback_handler,
                    current_message=prompt,
                    initial_prompt=prompt,
                    budget_cfg=scope["BudgetConfig"](60),
                    operation_start=time.time(),
                    max_duration=None,
                    logger=logging.getLogger("controller_pilot"),
                    run_policy=policy,
                )
                journal = agent._cyber_callback_handler.tool_outcome_journal
                return workflow.TaskExecutorCycleResult(
                    text=result.message or result.reason,
                    outcomes=journal.since(0),
                    reason=result.reason,
                    message=result.message,
                    details=result.details,
                )
            finally:
                agent.cleanup()

        def scripted(role, prompt, tools, system):
            if role == "task_prompt_builder":
                return json.dumps(
                    {
                        "prompt": "Use the fixture tools to inspect identity and policy, compare account access, publish a classification artifact and record acceptance for independent review.",
                        "tools": [],
                    }
                )
            if role == "task_prompt_critic":
                return json.dumps(
                    {
                        key: {"status": "pass", "issue": None}
                        for key in ["task_coverage", "scope", "execution_policy", "evidence", "capability_fit"]
                    }
                )
            if role == "task_evaluator":
                return json.dumps({"status": "done", "reason": "Scripted offline evaluator specimen"})
            raise AssertionError(role)

        controller = workflow.MultiAgentWorkflowController(
            runtime=runtime,
            budget=scope["BudgetConfig"](60),
            state_store=workflow.WorkflowStateStore(op, [target]),
            operation_targets=[target],
            text_runner=scripted if offline else None,
            work_runner=worker,
        )
        if offline:
            from ollama import ChatResponse

            steps = iter(
                [
                    ("inspect_context", {"kind": "identity"}),
                    ("inspect_context", {"kind": "policy"}),
                    ("request_account", {"principal": "session-red", "selector": "B"}),
                    (
                        "publish_result",
                        {
                            "classification": "inconclusive" if mode == "unknown" else "no_violation_observed",
                            "evidence_ids": ["obs-1", "obs-2", "obs-3"],
                            "explanation": "Scripted full-controller specimen",
                        },
                    ),
                    (
                        "record_task_acceptance",
                        {
                            "status": "satisfied",
                            "disposition": "observation",
                            "summary": "Comparison artifact produced for independent review",
                            "evidence_refs": ["artifact:artifacts/result.json"],
                        },
                    ),
                ]
            )

            async def sdk(client, **kwargs):
                name, args = next(steps)
                response = ChatResponse(
                    model="fixture",
                    message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"function": {"name": name, "arguments": args}}],
                    },
                    done=True,
                    done_reason="stop",
                    prompt_eval_count=100,
                    eval_count=20,
                    total_duration=1000000,
                )
                if not kwargs.get("stream"):
                    return response

                async def events():
                    yield response

                return events()

            with patch("ollama.AsyncClient.chat", sdk):
                controller._run_task(plan, plan.phases[0], task)
        else:
            controller._run_task(plan, plan.phases[0], task)
        current = store.get_tasks(op)[0]
        row = {
            "mode": mode,
            "arm": arm,
            "task_status": current.status,
            "task_reason": current.status_reason,
            "acceptance": [r.to_dict() for r in store.get_acceptance_results(op, task.task_uid)],
            "observations": scope["observations"],
        }
        if scope["published"]:
            decision = scope["published"][-1]
            row.update(answer=decision.model_dump(), **scope["fixture"].grade(mode, decision, scope["observations"]))
        (root / "controller-outcome.json").write_text(json.dumps(row, indent=2))
        return row

    return parent().run_case(folder, mode, arm, controller_hook=full, task_transform=prepare)


def main(argv=None):
    import argparse
    import hashlib
    from datetime import UTC, datetime

    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=(
        "outputs/technique_review_trials/full-controller-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ))
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    load_dotenv(".env")
    return parent().run_experiment(
        args.output,
        worker=run_case,
        limit=args.limit,
        protocol_extra={
            "scope": "Real controller task prompt-builder, critic, execution, evaluator and SQLite transition; isolated Qdrant catalog.",
            "excluded": ["whole-operation planning", "real endpoint I/O", "global operation stores"],
            "full_controller_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "authorization": "User-approved isolated fixture experiment; per-wave request accounting ceiling.",
        },
    )


if __name__ == "__main__":
    main()
