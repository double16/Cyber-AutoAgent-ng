"""Offline tests of isolated production-state and real acceptance/evaluator bindings."""

import importlib.util
import json
from pathlib import Path

import pytest
from strands.models.model import Model

from modules.agents.run_policy import AgentRunPolicy

BASE = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location("production_integration", BASE / "run.py")
integration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(integration)


def test_real_persistence_acceptance_and_reload(tmp_path):
    with integration.environment(tmp_path):
        ids = integration.seed(tmp_path)
        controller = integration.controller_for(tmp_path)
        for uid in ids:
            task = controller.state.patch_task(uid, status="active")
            prompt = controller._task_evaluator_prompt(
                controller.state.get_plan(), controller.state.get_plan().phases[0], task,
            )
            assert "Required hypothesis review output" in prompt
            response = controller.state.record_task_acceptance(task, {
                "status": "satisfied", "disposition": "observation",
                "summary": "Artifact availability before semantic review", "evidence_refs": [],
            })
            assert json.loads(response)["complete"]
            assert len(controller.state.list_task_acceptance_results(uid)) == 1
    with integration.environment(tmp_path):
        controller = integration.controller_for(tmp_path)
        assert len(controller.state.list_tasks()) == 3
        for uid in ids:
            assert controller.state.list_task_acceptance_results(uid)
            snapshot = integration.snapshot(controller, uid)
            assert snapshot["publication"]["operation_id"] == integration.OPERATION
            assert snapshot["binding"]["sha256"]


def test_fixture_scope_has_no_target_execution_tools(tmp_path):
    with integration.environment(tmp_path):
        integration.seed(tmp_path)
        controller = integration.controller_for(tmp_path)
        runtime = controller.runtime
        assert {integration.workflow.get_tool_name(item) for item in runtime.core_tools_list} == {
            "read_artifact", "search_artifact",
        }
        assert runtime.optional_tools_list == []
        assert integration.memory._get_database_store().db_path.startswith(str(tmp_path))
        assert runtime.operation_id == integration.OPERATION
        assert (tmp_path / "qdrant").exists()


def test_freeze_is_immutable(tmp_path):
    root = tmp_path / "frozen"
    integration.freeze(root)
    assert json.loads((root / "frozen.json").read_text())["live_target_tools"] == []
    with pytest.raises(FileExistsError):
        integration.freeze(root)


class LocalModel(Model):
    """Scripted provider exercising the real Strands agent and CLI run loop."""

    def __init__(self, payload, terminal="record_task_acceptance"):
        self.payload = payload
        self.terminal = terminal
        self.config = {"model_id": "fixture/local", "context_window_limit": 40000}
        self._cyber_native_structured_output = True
        self._output_tokens = 8192

    def update_config(self, **values):
        self.config.update(values)

    def get_config(self):
        return self.config

    async def structured_output(self, output_model, *_args, **_kwargs):
        yield {"output": output_model.model_validate(self.payload)}

    async def stream(self, messages, tool_specs=None, system_prompt=None, **_kwargs):
        yield {"messageStart": {"role": "assistant"}}
        if self.terminal not in {tool["name"] for tool in tool_specs or []}:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "Scripted evidence notes"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "fixture", "name": self.terminal}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(self.payload)}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


def test_real_cli_executor_records_acceptance_and_terminal_outcomes(tmp_path, monkeypatch):
    payload = {"status": "satisfied", "disposition": "observation",
               "summary": "Available fixture publication", "evidence_refs": []}
    monkeypatch.setattr(integration.agents, "create_strands_model", lambda *_: LocalModel(payload))
    monkeypatch.setattr(integration.agents, "get_capabilities", lambda *_: None)
    with integration.environment(tmp_path):
        integration.seed(tmp_path)
        controller = integration.controller_for(tmp_path)
        task = controller.state.patch_task("selector", status="active")
        acceptance = integration.memory.build_record_task_acceptance_tool(task.task_uid, task)
        policy = AgentRunPolicy(max_agent_calls=1, max_model_turns=2,
            required_tool_names={"record_task_acceptance"}, terminal_after_required_tools=True,
            require_successful_required_tools=True)
        result = controller._run_worker_agent("task_executor", "Record fixture availability", [acceptance],
                                              controller.runtime.system_prompt, policy)
        assert any(item.tool_name == "record_task_acceptance" and item.success for item in result.outcomes)
        assert controller.state.list_task_acceptance_results(task.task_uid)
        assert list(tmp_path.glob("executor-*.json"))


def test_real_structured_evaluator_binds_and_persists_review(tmp_path, monkeypatch):
    with integration.environment(tmp_path):
        integration.seed(tmp_path)
        controller = integration.controller_for(tmp_path)
        task = controller.state.patch_task("selector", status="active")
        current = integration.snapshot(controller, task.task_uid)
        ids = [item["hypothesis_id"] for item in current["publication"]["subjects"][0]["hypotheses"]]
        checks = {key: {"verdict": "adequate", "reason": "Scripted transport test, not semantic judgment"}
                  for key in ("grounding", "test_discrimination", "identity_requirements")}
        payload = {"status": "done", "reason": "Scripted review", "repair": {"kind": "none"},
                   "subject_reviews": [{"item_id": "selector", "decision": "retain", "reason": "Scripted review",
                       "hypotheses": [{"hypothesis_id": identifier, "decision": "retain", "reason": "Scripted",
                                       "semantic_checks": checks} for identifier in ids]}],
                   "prerequisite_gaps": [], "unresolved_criterion_ids": []}
        monkeypatch.setattr(integration.agents, "create_strands_model", lambda *_: LocalModel(payload))
        monkeypatch.setattr(integration.agents, "get_capabilities", lambda *_: None)
        # No inspection is needed for this transport test; native finalization remains production code.
        monkeypatch.setattr(controller, "_task_evaluator_tools", lambda *_: [])
        plan = controller.state.get_plan()
        decision = controller._evaluate_task(plan, plan.phases[0], task)
        assert decision.status == "done"
        updated = next(item for item in controller.state.list_tasks() if item.task_uid == task.task_uid)
        assert updated.recovery_context["hypothesis_review"]["publication_sha256"] == current["binding"]["sha256"]


@pytest.mark.parametrize("encoded", [False, True])
def test_real_review_repair_and_acceptance_cycle(tmp_path, monkeypatch, encoded):
    with integration.environment(tmp_path):
        integration.seed(tmp_path)
        controller = integration.controller_for(tmp_path)
        monkeypatch.setattr(integration, "controller_for", lambda _: controller)

        class WorkerModel(LocalModel):
            async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
                names = {item["name"] for item in tool_specs or []}
                if "revise_hypothesis" in names:
                    self.terminal = "revise_hypothesis"
                    value = integration.snapshot(controller, "selector")["publication"]["subjects"][0]["hypotheses"][0]
                    value.pop("hypothesis_id")
                    value["safe_test_steps"] = ["Use the declared GET /records?record_id=R with both future accounts"]
                    self.payload = {"replacement": json.dumps(value) if encoded else value, "reason": "Repair selector syntax"}
                else:
                    self.terminal = "record_task_acceptance"
                    self.payload = {"status": "satisfied", "disposition": "observation",
                                    "summary": "Current fixture publication", "evidence_refs": []}
                async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
                    yield event

        def model_factory(_provider, _model, role):
            if role == "task_prompt_builder":
                return LocalModel({"prompt": "Repair only the assigned fixture proposal using retained evidence.",
                                   "tools": [], "shell_commands": []})
            if role == "task_prompt_critic":
                return LocalModel({key: {"status": "pass", "issue": None} for key in (
                    "task_coverage", "scope", "execution_policy", "evidence", "capability_fit",
                )})
            if role != "task_evaluator":
                return WorkerModel({})
            current = next(item for item in controller.state.list_tasks() if item.task_uid == "selector")
            needs_repair = not current.recovery_context.get("hypothesis_review")
            publication = integration.snapshot(controller, "selector")["publication"]
            checks = {key: {"verdict": "revise" if needs_repair and key == "grounding" else "adequate",
                            "reason": "Scripted transport judgment"}
                      for key in ("grounding", "test_discrimination", "identity_requirements")}
            return LocalModel({
                "status": "partial_failure" if needs_repair else "done", "reason": "Scripted transport judgment",
                "repair": {"kind": "acceptance" if needs_repair else "none"},
                "subject_reviews": [{"item_id": "selector", "decision": "revise" if needs_repair else "retain",
                    "reason": "Scripted transport judgment", "hypotheses": [{"hypothesis_id": h["hypothesis_id"],
                        "decision": "revise" if needs_repair else "retain", "reason": "Scripted judgment",
                        "semantic_checks": checks} for h in publication["subjects"][0]["hypotheses"]]}],
                "prerequisite_gaps": [], "unresolved_criterion_ids": [],
            })

        monkeypatch.setattr(integration.agents, "create_strands_model", model_factory)
        monkeypatch.setattr(integration.agents, "get_capabilities", lambda *_: None)
        result = integration.perform(tmp_path, "selector")
        assert result["final"]["task"]["status"] == "done"
        assert [step["stage"] for step in result["history"]] == [
            "semantic_review", "production_continuation",
        ]
        before = result["history"][0]["state"]["binding"]["sha256"]
        after = result["final"]["binding"]["sha256"]
        assert before != after
        assert result["final"]["task"]["recovery_context"]["hypothesis_review"]["publication_sha256"] == after
        assert result["final"]["acceptance"]


@pytest.mark.parametrize("cycles,repair", [(0, "acceptance"), (2, "none")])
def test_unresolved_or_terminal_review_does_not_run_an_executor(tmp_path, monkeypatch, cycles, repair):
    with integration.environment(tmp_path):
        integration.seed(tmp_path)
        controller = integration.controller_for(tmp_path)
        monkeypatch.setattr(integration, "controller_for", lambda _: controller)
        monkeypatch.setattr(controller, "_evaluate_task", lambda *_args, **_kwargs: integration.workflow.WorkflowDecision(
            status="partial_failure", reason="Scripted unresolved review", repair_kind=repair,
        ))
        result = integration.perform(tmp_path, "selector", cycles=cycles)
        assert result["final"]["task"]["status"] == "partial_failure"
        assert [step["stage"] for step in result["history"]] == ["semantic_review"]
        assert not list(tmp_path.glob("executor-*.json"))


def test_main_preserves_failed_task_and_continues_isolated_tasks(tmp_path, monkeypatch):
    root = tmp_path / "operation"
    monkeypatch.setattr(integration, "ROOT", root)
    monkeypatch.setattr(integration.recording, "preflight", lambda: None)
    called = []

    def perform(directory, uid):
        assert directory == root
        called.append(uid)
        if uid == "mechanisms":
            raise OSError("Scripted executor failure")
        return {"task_uid": uid, "final": {"task": {"status": "partial_failure"}}}

    monkeypatch.setattr(integration, "perform", perform)
    integration.main()
    saved = json.loads((root / "results.json").read_text())
    assert called == ["selector", "mechanisms", "refutation"]
    assert "Scripted executor failure" in saved[1]["error"]
    assert len(saved) == 3 and (root / "initial.json").exists()
