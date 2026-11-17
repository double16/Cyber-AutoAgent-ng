"""Controller isolation and full offline execution; no provider generation or embedding dispatch."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("full_pilot", Path(__file__).with_name("run.py"))
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


@pytest.mark.parametrize("mode", ["unknown", "shared"])
def test_controller_reaches_done_offline(tmp_path, mode):
    result = pilot.run_case(tmp_path / mode, mode, "none", offline=True)
    assert result["task_status"] == "done"
    assert result["acceptance"] and result["supported_correct"]


def test_catalog_branch_is_isolated_and_closed(tmp_path, monkeypatch):
    from modules.agents import multi_agent_workflow
    from modules.storage import qdrant
    from modules.techniques import cli

    closed = []
    service = SimpleNamespace(store=SimpleNamespace(qdrant=SimpleNamespace(close=lambda: closed.append(True))))
    calls = []

    def build(*args):
        assert str(tmp_path) in args[2]
        calls.append(args)
        return service

    monkeypatch.setattr(cli, "build_service", build)
    monkeypatch.setattr(qdrant, "build_embeddings", lambda config: object())
    def inspect_fixture(controller, *args):
        import json

        task = args[-1]
        plan = args[0]
        assert plan.phases[0].technique_contexts == ("vulnerability_verification",)
        assert plan.targets[0].type == "network"
        assert plan.targets[0].value == controller.runtime.config.target == "https://fixture.invalid"
        assert {t.tool_spec["name"]: t._cyber_technique_observation_origin
                for t in controller.runtime.core_tools_list} == {
            "inspect_context": "source", "request_account": "source",
            "publish_result": "analysis", "read_fixture": "source", "read_artifact": "source",
        }
        assert task.acceptance.basis.procedure.limits == {"max_requests": 8}
        assert "cross-account" in task.objective
        reader = next(t for t in controller.runtime.core_tools_list if t.tool_spec["name"] == "read_fixture")
        inputs = json.loads(reader())
        assert inputs["selectors"] == ["A", "B"]
        assert inputs["protocol"] == "HTTP"
        assert inputs["target"] == "https://fixture.invalid"
        assert inputs["operation"]["selector_header"] == "X-Account"
        assert inputs["operation"]["tool"] == "request_account"
        assert "permissions" not in inputs and "classification" not in inputs
        artifact_reader = next(t for t in controller.runtime.core_tools_list if t.tool_spec["name"] == "read_artifact")
        retained = artifact_reader(path="artifact:fixture.json")
        assert "selectors" in str(retained)
        outside = tmp_path / "outside.txt"
        outside.write_text("Outside operation scope")
        with pytest.raises(ValueError):
            artifact_reader(path=str(outside))


    monkeypatch.setattr(multi_agent_workflow.MultiAgentWorkflowController, "_run_task", inspect_fixture)
    result = pilot.run_case(tmp_path / "catalog", "unknown", "lookup")
    assert calls and closed
    assert result["task_status"] == "active" and not result["acceptance"]


def test_live_entrypoint_is_bounded_and_declares_real_scope(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    captured = {}

    def run(root, worker, limit, protocol_extra):
        captured.update(root=root, limit=limit, protocol=protocol_extra)
        return "prepared"

    monkeypatch.setattr(pilot, "parent", lambda: SimpleNamespace(run_experiment=run))
    assert pilot.main([]) == "prepared"
    assert captured["limit"] == 100
    assert "evaluator" in captured["protocol"]["scope"]
    assert captured["protocol"]["excluded"] == [
        "whole-operation planning",
        "real endpoint I/O",
        "global operation stores",
    ]


@pytest.mark.parametrize("profile", ["http", "network", "filesystem"])
def test_controller_preserves_protocol_payload_without_inference(tmp_path, monkeypatch, profile):
    parent = pilot.parent()
    fixture = parent.load_fixture()

    def response(mode, principal, selector):
        original = fixture.response(mode, principal, selector)
        content = original["body"].get("object")
        return {
            "http": original,
            "network": {"reply_code": 550, "record": content},
            "filesystem": {"read_error": "EACCES", "file_content": content},
        }[profile]

    adapter = SimpleNamespace(context=fixture.context, response=response, grade=fixture.grade,
                              Decision=fixture.Decision)
    monkeypatch.setattr(parent, "load_fixture", lambda: adapter)
    monkeypatch.setattr(pilot, "parent", lambda: parent)
    result = pilot.run_case(tmp_path / profile, "shared", "none", offline=True)
    assert result["task_status"] == "done"
    assert result["acceptance"]
    probe = next(r for r in result["observations"] if r.get("selector") == "B")
    assert probe["response"] == response("shared", "session-red", "B")
    # Scripted evaluator owns the conclusion; controller bookkeeping preserves payloads.
    assert result["answer"]["classification"] == "no_violation_observed"


def test_entrypoint_accepts_fresh_output_and_request_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    calls = []
    monkeypatch.setattr(pilot, "parent", lambda: SimpleNamespace(
        run_experiment=lambda *args, **kwargs: calls.append((args, kwargs))))
    pilot.main(["--output", str(tmp_path / "fresh"), "--limit", "25"])
    assert calls[0][0] == (str(tmp_path / "fresh"),)
    assert calls[0][1]["limit"] == 25
    with pytest.raises(SystemExit):
        pilot.main(["--unknown-option"])
