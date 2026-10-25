"""Controller capture, semantic handoff, and durable continuation behavior."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.agents import multi_agent_workflow as workflow
from modules.operation_plugins.web.comparison import comparison_contract_matches
from modules.tools import memory
from modules.tools.memory import AcceptanceBasis, AcceptanceContract, AcceptanceCriterion, EvidenceRequirement
from tests.helpers.comparison import operation_support
from tests.test_authentication_comparisons import matrix_case as matrix_case
from tests.test_worker_flows import _use_sqlite_workflow_state
from tests.test_worker_flows import worker_case as worker_case


@pytest.fixture
def capture_case(matrix_case, monkeypatch, tmp_path):
    controller, task, store, plan, phase, target, _observed = matrix_case
    manifest = {"schema_version": 1, "unassessed_gaps": [], "items": [{
        "id": "endpoint", "kind": "endpoint", "target_id": "target", "value": target + "/private",
        "attributes": {"interaction": {"interface": "http", "operations": ["GET"]}},
    }]}
    (tmp_path / "inventory.json").write_text(json.dumps(manifest))
    manifest, snapshot_hash = workflow._load_inventory_manifest("artifact:inventory.json")
    task = replace(task, task_uid="controller-capture", acceptance=AcceptanceContract(
        mode="coverage", basis=AcceptanceBasis(kind="snapshot", description="Frozen endpoint",
                                              source_refs=["artifact:inventory.json"], snapshot_hash=snapshot_hash,
                                              item_ids=["endpoint"]),
        criteria=[AcceptanceCriterion(id="compare", description="Compare access to a suitable resource",
                                      evidence_requirements=[EvidenceRequirement(kind="artifact")])],
    ), recovery_context={
        "phase_task_contract": {"module": "web", "phase_id": 3, "workstream": "authenticated_credential_coverage",
                                "task_role": "mapping", "execution_owner": "controller"},
        "authentication_requirement": {"comparison": "none", "roles": ["user"]},
        "authentication_matrix": {"version": 1},
    })
    store.store_task("OP_AUTH", task)
    _use_sqlite_workflow_state(controller, store, monkeypatch)
    monkeypatch.setattr(memory, "_ensure_memory_client", lambda: SimpleNamespace(
        patch_task=lambda user_id=None, **kwargs: store.patch_task("OP_AUTH", **kwargs),
    ))
    monkeypatch.setattr(memory, "_store_memory_entry", lambda *_args: SimpleNamespace(created=True))
    monkeypatch.setattr(controller, "_prepare_task_authentication", lambda _plan, current: controller.state.patch_task(
        current.task_uid, auth_context={"credential_ids": ["credential"]},
    ))
    calls = []

    def capture(*_args, **_kwargs):
        calls.append("request")
        return json.dumps({"status_code": 400, "headers": {"Content-Type": "text/plain"}, "body": "invalid input"})

    monkeypatch.setattr(workflow, "authenticated_http_request", capture)
    monkeypatch.setattr(controller, "_current_unauthenticated_response", lambda *_args: {
        "outcome": "response", "status": 400, "body": "invalid input", "method": "GET", "url": target + "/private",
    })
    stored = next(item for item in controller.state.list_tasks() if item.task_uid == "controller-capture")
    return controller, stored, store, plan, phase, calls, manifest


def test_controller_capture_is_not_acceptance_and_continuation_reuses_it(capture_case):
    controller, task, store, plan, phase, calls, _manifest = capture_case
    result = controller._run_controller_web_authenticated_coverage(plan, phase, task)
    assert result.status == "needs_semantic_review" and len(result.evidence_refs) == 1
    assert store.get_acceptance_results("OP_AUTH", task.task_uid) == []
    retained = next(item for item in controller.state.list_tasks() if item.task_uid == "controller-capture")
    assert controller._run_controller_web_authenticated_coverage(plan, phase, retained) == result
    assert calls == ["request"]
    path = workflow.resolve_operation_artifact_path(result.evidence_refs[0])
    with open(path, "a") as artifact:
        artifact.write(" ")
    controller._run_controller_web_authenticated_coverage(plan, phase, retained)
    assert calls == ["request", "request"]


@pytest.mark.parametrize("outcome", ["supported", "inconclusive", "invalid", "blocked"])
def test_controller_semantic_review_precedes_acceptance_or_repair(capture_case, monkeypatch, outcome):
    controller, task, store, plan, phase, calls, _manifest = capture_case
    # Initial capture retained across the controller entry point, so review cannot repeat target requests.
    result = controller._run_controller_web_authenticated_coverage(plan, phase, task)
    task = next(item for item in controller.state.list_tasks() if item.task_uid == "controller-capture")
    reference = result.evidence_refs[0]

    def model(role, _prompt, _tools, _system):
        assert role == "task_evaluator"
        if outcome == "invalid":
            return '{"status":"done"}'
        return json.dumps({
            "status": "blocked" if outcome == "blocked" else "done",
            "comparison_assessment": {
                "operation_support": operation_support("supported" if outcome == "supported" else "inconclusive", [reference]),
                "outcome": "supported" if outcome == "supported" else "inconclusive",
                "operation_evidence_refs": [reference], "comparison_evidence_refs": [reference],
                "reason": "External prerequisite unavailable" if outcome == "blocked" else "Reviewed comparison",
            },
        })

    controller.text_runner = model
    if outcome == "inconclusive":
        def stop_at_worker(*_args, **_kwargs):
            raise RuntimeError("evidence repair entered")
        monkeypatch.setattr(controller, "_build_task_prompt", stop_at_worker)
        monkeypatch.setattr(controller, "_task_inventory_route_feedback", lambda *_args: [])
        with pytest.raises(RuntimeError, match="evidence repair entered"):
            controller._run_task_in_trace(plan, phase, task)
        updated = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == "controller-capture")
        assert updated.recovery_context["controller_comparison_repair"]["attempted"]
        assert store.get_acceptance_results("OP_AUTH", task.task_uid) == []
        # A continuation must not grant a second corrective execution.
        controller._run_task_in_trace(plan, phase, updated)
        updated = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == "controller-capture")
        assert updated.status == "partial_failure"
    else:
        monkeypatch.setattr(controller, "_build_task_prompt", lambda *_args: pytest.fail("unexpected execution worker"))
        controller._run_task_in_trace(plan, phase, task)
        updated = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == "controller-capture")
        expected = {"supported": "done", "invalid": "partial_failure", "blocked": "blocked"}[outcome]
        assert updated.status == expected, updated.status_reason
        assert bool(store.get_acceptance_results("OP_AUTH", task.task_uid)) is (outcome == "supported")
    assert calls == ["request"]


def explicit_case():
    contract = {"version": 1, "url": "https://target.test/resource?id=valid", "method": "GET",
                "prerequisite_evidence_refs": ["artifact:resource-proof"], "assertions": [
                    {"identity": "anonymous", "status": 403, "body_contains": "policy denied",
                     "content_type": "text/plain"},
                    {"identity": "credential", "status": 200, "body_contains": "resource id valid",
                     "content_type": "text/plain"},
                ]}
    captures = {"responses": [{"credential_id": "credential", "url": contract["url"], "method": "GET", "status": 200,
                              "body": "resource id valid", "headers": {"Content-Type": "text/plain; charset=utf-8"}}],
                "unauthenticated_responses": [{"url": contract["url"], "method": "GET", "status": 403,
                                               "body": "policy denied", "headers": {"content-type": "text/plain"}}]}
    return contract, captures


@pytest.mark.parametrize("defect", [None, "no_contract", "missing_prerequisite", "missing_identity",
                                   "duplicate_identity",
                                   "wrong_url", "wrong_method", "wrong_status", "wrong_body", "wrong_type", "gap",
                                   "extra_response", "missing_response"])
def test_explicit_http_contract_requires_matching_operations_and_identity_assertions(defect):
    contract, captures = explicit_case()
    prerequisites = {"artifact:resource-proof"}
    if defect == "no_contract":
        contract = None
    elif defect == "missing_prerequisite":
        prerequisites.clear()
    elif defect == "missing_identity":
        contract["assertions"][1]["identity"] = "another"
    elif defect == "duplicate_identity":
        contract["assertions"].append(contract["assertions"][0])
    elif defect in {"wrong_url", "wrong_method", "wrong_status", "wrong_body"}:
        field = defect.removeprefix("wrong_")
        captures["responses"][0][field] = 400 if field == "status" else "wrong"
    elif defect == "wrong_type":
        captures["responses"][0]["headers"] = {}
    elif defect == "gap":
        captures["coverage_gaps"] = ["failed"]
    elif defect == "extra_response":
        captures["responses"].append(captures["responses"][0])
    elif defect == "missing_response":
        captures["responses"] = []
    assert comparison_contract_matches(contract, captures, prerequisite_refs=prerequisites,
                                       identities={"anonymous", "credential"}) is (defect is None)


def test_explicit_frozen_comparison_completes_without_agent(capture_case, monkeypatch, tmp_path):
    controller, task, store, plan, phase, _calls, manifest = capture_case
    contract, captures = explicit_case()
    url = manifest["items"][0]["value"]
    contract["url"] = url
    proof = tmp_path / "resource-proof"
    proof.write_text("Current operation verified the concrete resource and required inputs")
    contract["comparison"] = "none"
    contract["assertions"][1]["identity"] = "account:1"
    manifest["items"][0]["attributes"]["interaction"].update({
        "operation_provenance": "observed", "extensions": {"http_comparison": contract},
    })
    (tmp_path / "inventory.json").write_text(json.dumps(manifest))
    _manifest, digest = workflow._load_inventory_manifest("artifact:inventory.json")
    task = replace(task, task_uid="explicit-comparison", evidence=["artifact:resource-proof"], acceptance=replace(
        task.acceptance, manifest_hash="", basis=replace(task.acceptance.basis, snapshot_hash=digest),
    ))
    store.store_task("OP_AUTH", task)
    monkeypatch.setattr(workflow, "authenticated_http_request", lambda *_args, **_kwargs: json.dumps({
        "status_code": 200, "body": "resource id valid", "headers": {"Content-Type": "text/plain"},
    }))
    monkeypatch.setattr(controller, "_current_unauthenticated_response", lambda *_args: {
        **captures["unauthenticated_responses"][0], "url": url, "outcome": "response",
    })
    monkeypatch.setattr(controller, "_evaluate_task", lambda *_args, **_kwargs: pytest.fail("no semantic agent needed"))
    monkeypatch.setattr(controller, "_build_task_prompt", lambda *_args: pytest.fail("no execution agent needed"))
    controller._run_task_in_trace(plan, phase, task)
    updated = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == task.task_uid)
    assert updated.status == "done", updated.status_reason
    assert updated.recovery_context["comparison_assessment"]["outcome"] == "supported"
    assert store.get_acceptance_results("OP_AUTH", task.task_uid)[0].status == "satisfied"


@pytest.mark.parametrize("repair_resolves", [True, False])
def test_controller_handoff_uses_one_execution_repair(capture_case, monkeypatch, tmp_path, repair_resolves):
    from modules.handlers.tool_recovery import ToolOutcome

    controller, task, store, plan, phase, requests, _manifest = capture_case
    actors = []
    evaluations = []
    repaired = tmp_path / "corrected-comparison.txt"
    repaired_ref = "artifact:corrected-comparison.txt"
    monkeypatch.setattr(controller, "_build_task_prompt", lambda *_args: {
        "prompt": "Repair assigned comparison", "tools": [],
    })
    monkeypatch.setattr(controller, "_task_inventory_route_feedback", lambda *_args: [])

    def evaluator(role, _prompt, _tools, _system):
        assert role == "task_evaluator"
        evaluations.append(True)
        supported = repair_resolves and bool(actors)
        reference = repaired_ref if actors else next(
            item for item in store.get_tasks("OP_AUTH") if item.task_uid == task.task_uid
        ).evidence[0]
        return json.dumps({"status": "done", "comparison_assessment": {
            "operation_support": operation_support("supported" if supported else "inconclusive", [reference] if supported else []),
            "outcome": "supported" if supported else "inconclusive", "reason": "Verify suitable resource inputs",
            "operation_evidence_refs": [reference] if supported else [], "comparison_evidence_refs": [reference],
        }})

    def actor(_role, prompt, tools, _system, _policy):
        actors.append(prompt)
        assert "Repair the retained comparison evidence" in prompt
        repaired.write_text("Valid resource comparison" if repair_resolves else "Still unsuitable inputs")
        acceptance = next(tool for tool in tools if workflow.get_tool_name(tool) == "record_task_acceptance")
        acceptance(status="satisfied", disposition="observation", summary="Corrective comparison captured",
                   evidence_refs=[repaired_ref])
        return workflow.TaskExecutorCycleResult("Corrective response evidence", [ToolOutcome(
            sequence=1, tool_use_id="corrected", tool_name="shell", success=True, correctable=False,
            input_summary="Compare suitable resource inputs",
            artifact_refs=(repaired_ref,), output_summary=repaired_ref,
        )])

    controller.text_runner = evaluator
    controller.work_runner = actor
    controller._run_task_in_trace(plan, phase, task)
    updated = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == task.task_uid)
    assert updated.status == ("done" if repair_resolves else "partial_failure"), updated.status_reason
    assert len(actors) == 1 and len(evaluations) == 2
    assert requests == ["request"]


@pytest.mark.parametrize("comparison,roles", [("account", ["member"]), ("role", ["member", "owner"])])
def test_controller_compares_two_accounts_without_anonymous_or_initial_executor(
    capture_case, monkeypatch, comparison, roles,
):
    controller, task, store, plan, phase, _calls, _manifest = capture_case
    task = replace(task, task_uid=f"controller-{comparison}", recovery_context={
        **task.recovery_context,
        "authentication_requirement": {"comparison": comparison, "roles": roles},
        "phase_task_contract": {
            **task.recovery_context["phase_task_contract"], "workstream": "authorization_comparison",
        },
    })
    store.store_task("OP_AUTH", task)
    monkeypatch.setattr(controller, "_prepare_task_authentication", lambda _plan, current: controller.state.patch_task(
        current.task_uid, auth_context={"credential_ids": ["first", "second"]},
    ))
    requested = []

    def request(_method, _url, credential_id, **_kwargs):
        requested.append(credential_id)
        return json.dumps({"status_code": 200, "body": "known application result", "headers": {}})

    monkeypatch.setattr(workflow, "authenticated_http_request", request)
    monkeypatch.setattr(controller, "_current_unauthenticated_response", lambda *_args: pytest.fail(
        "not an anonymous comparison",
    ))
    monkeypatch.setattr(controller, "_build_task_prompt", lambda *_args: pytest.fail("no initial execution worker"))

    def evaluate(_role, _prompt, _tools, _system):
        current = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == task.task_uid)
        refs = current.evidence
        return json.dumps({"status": "done", "comparison_assessment": {
            "outcome": "supported", "operation_support": operation_support("supported", refs),
            "operation_evidence_refs": refs, "comparison_evidence_refs": refs, "reason": "Intended operation compared",
        }})

    controller.text_runner = evaluate
    controller._run_task_in_trace(plan, phase, task)
    current = next(item for item in store.get_tasks("OP_AUTH") if item.task_uid == task.task_uid)
    assert current.status == "done", current.status_reason
    assert requested == ["first", "second"]
    capture = json.loads((Path(workflow._operation_output_root()) / "artifacts" / "controller_authenticated_coverage"
                          / f"{task.task_uid}.json").read_text())
    assert capture["unauthenticated_responses"] == []
    assert capture["comparisons"][0]["left_credential_id"] == "first"
    assert capture["comparisons"][0]["right_credential_id"] == "second"
