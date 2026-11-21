"""Isolated real controller evaluation with a real native model and synthetic retained evidence."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import ollama
import pytest
from dotenv import load_dotenv
from strands import Agent

from modules.agents.native_structured import StructuredPayload, invoke_structured
from modules.config.models.factory import create_strands_model, require_prompt_token_limit
from modules.tools.hypotheses import hypothesis_binding
from modules.tools.memory import AcceptanceResult

spec = importlib.util.spec_from_file_location("evidence_review_probe", Path(__file__).with_name("run.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def evaluate(root, case, runner):
    root.mkdir(parents=True, exist_ok=True)
    with pytest.MonkeyPatch.context() as monkeypatch:
        *_, controller, plan, phase, task = probe.prepare(
            root.resolve(), case, monkeypatch, with_validator=True, with_controller=True,
        )
        controller.structured_runner = runner
        publication = hypothesis_binding(task)["artifact_ref"]
        acceptance = AcceptanceResult(
            "future-tests", "satisfied", "observation", "Published the assigned subject disposition",
            (publication,),
        )
        controller.state.acceptance_results[task.task_uid] = [acceptance]
        decision = controller._evaluate_task(
            plan, phase, task, worker_context="No safe probe in current scope",
            acceptance_results=[acceptance],
        )
        retained = next(t for t in controller.state.tasks if t.task_uid == task.task_uid)
        review = retained.recovery_context.get("hypothesis_review", {})
        return {
            "status": decision.status, "reason": decision.reason, "repair_kind": decision.repair_kind,
            "review": review,
            "expected": "partial_failure" if case[1] else "done",
            "limitation": "Real controller evaluator/routing with injected native runner; not full operation execution.",
        }


def main():
    load_dotenv(".env")
    root = Path("outputs/technique_review_trials/evidence-id-controller-20261118")
    root.mkdir(parents=True, exist_ok=True)
    count = {"requests": 0, "limit": 30}
    original = ollama.AsyncClient.chat

    async def counted(client, *args, **kwargs):
        if count["requests"] >= count["limit"]:
            raise RuntimeError("Isolated controller request limit reached")
        count["requests"] += 1
        (root / "budget.json").write_text(json.dumps(count))
        return await original(client, *args, **kwargs)

    def runner(role, prompt, tools, system, schema):
        agent = Agent(
            model=create_strands_model("ollama", "muse-glimmer:30b-mlx", role),
            system_prompt=system, tools=tools, callback_handler=None, load_tools_from_directory=False,
        )
        agent._prompt_token_limit = require_prompt_token_limit("ollama", "muse-glimmer:30b-mlx")
        result = invoke_structured(agent, prompt, schema, investigate=bool(tools))
        return StructuredPayload(result.structured_output.model_dump(), agent.messages)

    with patch.object(ollama.AsyncClient, "chat", counted):
        for case in (probe.CASES[4], probe.CASES[3]):
            path = root / f"{case[0]}.json"
            if path.exists():
                continue
            try:
                result = evaluate(root / case[0], case, runner)
            except Exception as error:
                result = {"error": str(error)}
            path.write_text(json.dumps(result, indent=2))
            print(case[0], result.get("status", result.get("error")), flush=True)


if __name__ == "__main__":
    main()
